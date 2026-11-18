"""Log files: reading, indexing and listing `.exlog` files (design sections 3.3, 5.7).

Everything here works on the semantic text format only. A final line without its terminating
newline is a truncated write (crash) and is ignored everywhere.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from array import array
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

from ..packets import REGISTRY, Packet
from ..packets.textcodec import decode_line

log = logging.getLogger(__name__)

LOG_SUFFIXES = (".exlog", ".exlog.gz")
FORMAT_VERSION = "1"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_KV = re.compile(r'([A-Za-z_][\w.-]*)=("(?:[^"\\]|\\.)*"|\S*)')
_HEADER_MAX_LINES = 10_000
_TAIL_BYTES = 65536
_SIM_SCAN_BYTES = 2_000_000
_BARE = re.compile(r'[^\s"=]+')


# ---------------------------------------------------------------- helpers


def ns_to_iso(ns: int) -> str:
    """ISO 8601 with local UTC offset and millisecond resolution."""
    return datetime.fromtimestamp(ns / 1e9).astimezone().isoformat(timespec="milliseconds")


def iso_to_ns(text: str) -> int:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return (dt - _EPOCH) // timedelta(microseconds=1) * 1000


def format_kv(items: dict[str, str]) -> str:
    """`key=value key=value`; values with spaces, quotes or `=` are JSON-quoted."""
    return " ".join(f"{k}={v if _BARE.fullmatch(v) else json.dumps(v)}" for k, v in items.items())


def parse_kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _KV.finditer(text):
        v = m.group(2)
        if v.startswith('"'):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[m.group(1)] = v
    return out


def _open(path: Path) -> IO[bytes]:
    return gzip.open(path, "rb") if path.name.endswith(".gz") else open(path, "rb")  # noqa: SIM115


def scan_lines(path: Path, start_offset: int = 0) -> Iterator[tuple[int, bytes]]:
    """Yield `(byte offset of the line start, raw line incl. newline)` for complete lines only."""
    with _open(path) as fh:
        if start_offset:
            fh.seek(start_offset)
        off = start_offset
        for raw in fh:
            if not raw.endswith(b"\n"):
                return  # truncated last line
            yield off, raw
            off += len(raw)


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace").rstrip("\r\n")


def read_header_block(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    """`(header, sources)` from the leading `#` lines (up to the first packet line)."""
    header: dict[str, str] = {}
    sources: list[dict[str, str]] = []
    n = 0
    for _, raw in scan_lines(path):
        if not raw.startswith(b"#"):
            break
        n += 1
        if n > _HEADER_MAX_LINES:
            break
        word, _, rest = _text(raw)[1:].partition(" ")
        if word == "exlog":
            header["v"] = parse_kv(rest).get("v", "")
        elif word == "meta":
            header.update(parse_kv(rest))
        elif word == "source":
            sources.append(parse_kv(rest))
    return header, sources


def read_tail(path: Path) -> tuple[dict[str, str] | None, int | None]:
    """`(trailer, last packet time)` from the end of the file. Trailer is None if `#end` is missing."""
    if path.name.endswith(".gz"):
        trailer, last_t = None, None
        for _, raw in scan_lines(path):
            line = _text(raw)
            if line.startswith("#end"):
                trailer = parse_kv(line[4:])
            elif line[:1].isdigit() or line[:1] == "-":
                try:
                    last_t = int(line.split(" ", 1)[0])
                except ValueError:
                    pass
        return trailer, last_t
    with open(path, "rb") as fh:
        size = fh.seek(0, 2)
        fh.seek(max(0, size - _TAIL_BYTES))
        data = fh.read()
    pieces = data.split(b"\n")
    pieces.pop()  # text after the last newline: truncated (or empty). Always dropped.
    if size > _TAIL_BYTES and pieces:
        pieces.pop(0)  # may start mid-line
    trailer = None
    last_t = None
    for raw in reversed(pieces):
        line = raw.decode("utf-8", "replace").rstrip("\r")
        if not line:
            continue
        if line.startswith("#end"):
            if trailer is None and last_t is None:
                trailer = parse_kv(line[4:])
            continue
        if line.startswith("#"):
            continue
        try:
            last_t = int(line.split(" ", 1)[0])
            break
        except ValueError:
            continue
    return trailer, last_t


# ---------------------------------------------------------------- index


@dataclass
class Keyframe:
    t: int  # keyframe boundary (ns)
    offset: int  # offset of the first line at or after the boundary: resume point
    latest: dict[tuple[str, str], int]  # (type, src) -> offset of the latest line before that point


@dataclass
class Marker:
    t: int
    kind: str  # "event" | "note" | "mode"
    src: str
    label: str


@dataclass
class LogIndex:
    """Seek index of a log. `times` is a running maximum, so it is sorted even if lines are slightly out of order."""

    path: Path
    size: int  # bytes covered (end of the last complete line)
    t_start: int | None
    t_end: int | None
    keyframe_ns: int
    times: array = field(default_factory=lambda: array("q"))  # per packet line
    offsets: array = field(default_factory=lambda: array("q"))  # per packet line
    keyframes: list[Keyframe] = field(default_factory=list)
    history: dict[tuple[str, str], list[Packet]] = field(default_factory=dict)  # history=True types, <= 20 Hz
    markers: list[Marker] = field(default_factory=list)
    bad_lines: int = 0

    @property
    def n_packets(self) -> int:
        return len(self.times)

    def keyframe_for(self, t: int) -> Keyframe | None:
        """The last keyframe with `keyframe.t <= t`, or None if t is before the first."""
        i = self._kf_bisect(t)
        return self.keyframes[i] if i >= 0 else None

    def _kf_bisect(self, t: int) -> int:
        lo, hi = 0, len(self.keyframes)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.keyframes[mid].t <= t:
                lo = mid + 1
            else:
                hi = mid
        return lo - 1

    def offset_for(self, t: int) -> int:
        """Byte offset of the first packet line with time >= t (`size` if there is none)."""
        i = bisect_right(self.times, t - 1)
        return self.offsets[i] if i < len(self.offsets) else self.size


# ---------------------------------------------------------------- reader


class LogReader:
    """Shared by the Replayer, LogLibrary and the `exgs log` tools."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._header: dict[str, str] | None = None
        self._sources: list[dict[str, str]] = []
        self.bad_lines: list[tuple[int, str]] = []  # (offset, error) of undecodable lines seen by iter_packets

    def header(self) -> dict[str, str]:
        """`v` (format version) plus the `#meta` keys (started, gs, host, profile, label, ...)."""
        if self._header is None:
            self._header, self._sources = read_header_block(self.path)
        return dict(self._header)

    def sources(self) -> list[dict[str, str]]:
        """`#source` lines of the header block."""
        self.header()
        return [dict(s) for s in self._sources]

    def iter_packets(self, start_offset: int = 0) -> Iterator[tuple[int, Packet]]:
        """`(byte offset, packet)`. `start_offset` must be a line start (from an index). Skips `#` lines,
        undecodable lines (recorded in `bad_lines`) and a truncated last line."""
        for off, raw in scan_lines(self.path, start_offset):
            if raw[:1] == b"#":
                continue
            line = _text(raw)
            if not line.strip():
                continue
            try:
                pkt = decode_line(line)
            except Exception as e:  # CodecError, or a malformed numeric token
                if len(self.bad_lines) < 1000:
                    self.bad_lines.append((off, str(e)))
                continue
            if pkt is not None:
                yield off, pkt

    def build_index(self, keyframe_s: float = 1.0, history_hz: float = 20.0) -> LogIndex:
        """One pass over the file: `(t, offset)` per line, a keyframe every `keyframe_s` of log time
        (offset of the latest line per (type, src)), history series and event/note/mode markers."""
        kf_ns = max(1, int(keyframe_s * 1e9))
        hist_dt = int(1e9 / history_hz)
        history_types = {p.name for p in REGISTRY.all() if p.history}
        idx = LogIndex(self.path, 0, None, None, kf_ns)
        times, offsets = idx.times, idx.offsets
        latest: dict[tuple[str, str], int] = {}
        last_hist: dict[tuple[str, str], int] = {}
        last_mode: dict[str, str] = {}
        next_kf = 0
        last_t = 0
        end = 0
        for off, raw in scan_lines(self.path):
            end = off + len(raw)
            if raw[:1] == b"#" or not raw.strip():
                continue
            text = _text(raw)
            parts = text.split(" ", 3)
            try:
                t = int(parts[0])
                src, typ = parts[1], parts[2]
            except (ValueError, IndexError):
                idx.bad_lines += 1
                continue
            if idx.t_start is None:
                idx.t_start = last_t = t
                next_kf = t
            if t < last_t:
                t = last_t
            last_t = t
            if t >= next_kf:
                boundary = next_kf + (t - next_kf) // kf_ns * kf_ns  # a gap gets one keyframe, not one per second
                idx.keyframes.append(Keyframe(boundary, off, dict(latest)))
                next_kf = boundary + kf_ns
            times.append(t)
            offsets.append(off)
            key = (typ, src)
            latest[key] = off
            if typ in history_types:
                if t - last_hist.get(key, -(1 << 62)) >= hist_dt:
                    try:
                        pkt = decode_line(text)
                    except Exception:
                        pkt = None
                    if pkt is not None:
                        idx.history.setdefault(key, []).append(pkt)
                        last_hist[key] = t
            elif typ == "GsEvent" or typ == "Note":
                self._marker(idx, text, t)
            elif typ == "VehicleState" and len(parts) > 3:
                mode = _token(parts[3], "mode")
                if mode is not None:
                    prev = last_mode.get(src)
                    if prev is not None and prev != mode:
                        idx.markers.append(Marker(t, "mode", src, f"{prev} -> {mode}"))
                    last_mode[src] = mode
        idx.t_end = last_t if idx.t_start is not None else None
        idx.size = end
        return idx

    @staticmethod
    def _marker(idx: LogIndex, text: str, t: int) -> None:
        try:
            pkt = decode_line(text)
        except Exception:
            return
        if pkt is None:
            return
        if pkt.TYPE == "Note":
            idx.markers.append(Marker(t, "note", pkt.src, str(getattr(pkt, "text", ""))))
        else:
            kind = getattr(pkt, "kind", None)
            name = getattr(kind, "name", str(kind))
            idx.markers.append(Marker(t, "event", pkt.src, f"{name}: {getattr(pkt, 'detail', '') or ''}".rstrip(": ")))


def _token(rest: str, name: str) -> str | None:
    """Raw value of `name=` in the field text, without decoding the line."""
    prefix = name + "="
    if rest.startswith(prefix):
        i = 0
    else:
        i = rest.find(" " + prefix)
        if i < 0:
            return None
        i += 1
    j = i + len(prefix)
    k = rest.find(" ", j)
    return rest[j : k if k >= 0 else len(rest)]


# ---------------------------------------------------------------- library


@dataclass
class LogInfo:
    name: str
    path: Path
    started: int
    duration_s: float | None
    size: int
    complete: bool
    sources: list[str]
    sim: bool | None
    label: str | None


class LogLibrary:
    def __init__(self, directory: Path):
        self.directory = Path(directory).expanduser()

    def list(self) -> list[LogInfo]:
        """Logs in the directory, newest first. Reads the header and the trailer (the tail for incomplete files)."""
        if not self.directory.is_dir():
            return []
        out = []
        for p in self.directory.iterdir():
            if p.is_file() and p.name.endswith(LOG_SUFFIXES):
                try:
                    info = self.info(p)
                except OSError as e:
                    log.warning("cannot read log %s: %s", p, e)
                    continue
                if info is not None:
                    out.append(info)
        out.sort(key=lambda i: (i.started, i.name), reverse=True)
        return out

    def get(self, name: str) -> LogInfo | None:
        """Info for one log by file name (no path separators allowed)."""
        if Path(name).name != name:
            return None
        p = self.directory / name
        return self.info(p) if p.is_file() else None

    def info(self, path: Path) -> LogInfo | None:
        reader = LogReader(path)
        header = reader.header()
        if "v" not in header:
            log.warning("%s is not an exlog file (no #exlog line), skipped", path)
            return None
        size = path.stat().st_size
        trailer, last_t = read_tail(path)
        started = None
        if "started" in header:
            try:
                started = iso_to_ns(header["started"])
            except ValueError:
                pass
        if started is None:
            started = int(path.stat().st_mtime * 1e9)
        end = last_t
        if trailer is not None and "stopped" in trailer:
            try:
                end = iso_to_ns(trailer["stopped"])
            except ValueError:
                pass
        duration = max(0.0, (end - started) / 1e9) if end is not None else None
        return LogInfo(
            name=path.name,
            path=path,
            started=started,
            duration_s=duration,
            size=size,
            complete=trailer is not None,
            sources=[s["id"] for s in reader.sources() if "id" in s],
            sim=_find_sim(reader),
            label=header.get("label"),
        )


def _find_sim(reader: LogReader) -> bool | None:
    """`VehicleState.sim` of the first VehicleState that supplies it, within the first ~2 MB."""
    seen = 0
    for _, raw in scan_lines(reader.path):
        seen += len(raw)
        if seen > _SIM_SCAN_BYTES:
            return None
        if raw[:1] == b"#":
            continue
        parts = raw.split(b" ", 3)
        if len(parts) > 3 and parts[2] == b"VehicleState":
            v = _token(parts[3].decode("utf-8", "replace"), "sim")
            if v in ("true", "false"):
                return v == "true"
    return None
