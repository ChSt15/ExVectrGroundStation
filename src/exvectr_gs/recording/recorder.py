"""Recorder: writes everything on the live bus to an `.exlog` file (design sections 2.4, 3.3, 3.5).

Recording starts and stops only by explicit user action (decision 10.1-3). After a crash the old file
stays as it is (valid, a truncated last line is ignored) and the user starts a new recording manually.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import shutil
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO

from ..core.bus import Bus
from ..packets import PacketRegistry
from ..packets.base import Packet, UnknownPacket
from ..packets.defs.host import GsEvent, GsEventKind, Note
from ..packets.fields import FieldSpec
from ..packets.textcodec import encode_line
from .logfile import FORMAT_VERSION, format_kv, ns_to_iso

log = logging.getLogger(__name__)

_STOP = object()
_LABEL_BAD = {ord(c): "-" for c in '/\\:*?"<>|'}


@dataclass
class RecordingInfo:
    path: Path
    name: str
    started: int
    stopped: int | None
    packets: int
    bytes: int


@dataclass
class RecorderStatus:
    active: bool
    info: RecordingInfo | None  # the current recording (live counters), or the last failed one
    last_error: str | None
    free_bytes: int | None  # free space in the recording directory (None if unknown)
    dropped: int  # packets dropped because the writer could not keep up


def _type_line(spec_name: str, version: int, fields: Sequence[FieldSpec]) -> str:
    cols = []
    for f in fields:
        kind = f.kind
        if kind in ("float_array", "int_array"):
            kind = f"{kind.split('_')[0]}[{f.length if f.length is not None else ''}]"
        cols.append(f"{f.name}:{kind}" + (f":{f.unit}" if f.unit else ""))
    return f"#type {spec_name} v={version} " + " ".join(cols)


def safe_label(label: str) -> str:
    """Label as used in a file name: no path separators or characters that are invalid on common file systems."""
    s = label.strip().translate(_LABEL_BAD)
    return "-".join(s.split())[:60]


class _Session:
    """One recording: the open file, the queue and the writer thread."""

    def __init__(
        self,
        path: Path,
        fh: IO[bytes],
        registry: PacketRegistry,
        flush_s: float,
        fsync_s: float,
        started: int,
        max_queue: int,
        on_error: Callable[[str], None] | None,
    ):
        self.path, self.fh, self.registry = path, fh, registry
        self.flush_s, self.fsync_s = flush_s, fsync_s
        self.started = started
        self.stopped: int | None = None
        self.packets = 0
        self.bytes = 0
        self.dropped = 0
        self.error: str | None = None
        self.on_error = on_error
        self.q: queue.Queue = queue.Queue(maxsize=max_queue)
        self.seen_types: set[str] = set()
        self.thread = threading.Thread(target=self._run, name="recorder-writer", daemon=True)

    # ---- called from the bus thread

    def put(self, item) -> None:
        if self.error is not None:
            return
        try:
            self.q.put_nowait(item)
        except queue.Full:
            self.dropped += 1

    def info(self) -> RecordingInfo:
        return RecordingInfo(self.path, self.path.name, self.started, self.stopped, self.packets, self.bytes)

    # ---- writer thread

    def _write(self, text: str) -> None:
        data = text.encode("utf-8") + b"\n"
        self.fh.write(data)
        self.bytes += len(data)

    def _write_packet(self, pkt: Packet) -> None:
        if not isinstance(pkt, UnknownPacket) and pkt.TYPE not in self.seen_types:
            try:
                ptype = self.registry.get(pkt.TYPE)
            except KeyError:
                ptype = None
            if ptype is not None:
                self._write(_type_line(ptype.name, ptype.version, ptype.fields))
            self.seen_types.add(pkt.TYPE)
        try:
            line = encode_line(pkt)
        except Exception:
            log.exception("cannot encode %s packet, skipped", pkt.TYPE)
            return
        self._write(line)
        self.packets += 1

    def _run(self) -> None:
        mono = time.monotonic
        next_flush = mono() + self.flush_s
        next_fsync = mono() + self.fsync_s
        dirty = False
        stop_t = None
        try:
            while True:
                try:
                    item = self.q.get(timeout=max(0.0, next_flush - mono()))
                except queue.Empty:
                    item = None
                if isinstance(item, tuple) and item and item[0] is _STOP:
                    stop_t = item[1]
                    break
                if item is not None:
                    dirty = True
                    if isinstance(item, str):
                        self._write(item)
                    else:
                        self._write_packet(item)
                now = mono()
                if now >= next_flush:
                    if dirty:
                        self.fh.flush()
                    next_flush = now + self.flush_s
                if now >= next_fsync:
                    if dirty:
                        os.fsync(self.fh.fileno())
                        dirty = False
                    next_fsync = now + self.fsync_s
            self._write(f"#end stopped={ns_to_iso(stop_t)} packets={self.packets}")
            self.fh.flush()
            os.fsync(self.fh.fileno())
        except OSError as e:
            self._fail(e)
        finally:
            with contextlib.suppress(OSError):
                self.fh.close()

    def _fail(self, e: OSError) -> None:
        msg = f"recording stopped: write error on {self.path}: {e}"
        self.error = msg
        log.error(msg)
        if self.on_error is not None:
            try:
                self.on_error(msg)
            except Exception:
                log.exception("recorder on_error callback failed")


class Recorder:
    """Records every packet published on `bus` while a recording is active.

    `on_error(message)` is called from the writer thread on a write error (disk full, ...). The recorder
    stops, the core keeps running. Make the callback thread-safe (for example `loop.call_soon_threadsafe`).
    Not thread-safe itself: call start/stop/note/status from the thread that runs the bus.
    """

    def __init__(
        self,
        directory: Path,
        registry: PacketRegistry,
        bus: Bus,
        meta: Mapping[str, str],
        *,
        sources: Sequence[Mapping[str, str]] = (),
        clock: Callable[[], int] = time.time_ns,
        flush_interval_ms: int = 100,
        fsync_interval_ms: int = 1000,
        on_error: Callable[[str], None] | None = None,
        opener: Callable[[Path], IO[bytes]] | None = None,
        max_queue: int = 500_000,
    ):
        self._dir = Path(directory).expanduser()
        self._registry = registry
        self._bus = bus
        self._meta = dict(meta)
        self._sources = [dict(s) for s in sources]
        self._clock = clock
        self._flush_s = flush_interval_ms / 1000
        self._fsync_s = fsync_interval_ms / 1000
        self._on_error = on_error
        self._opener = opener or (lambda p: open(p, "xb"))  # noqa: SIM115  (exclusive create: never overwrite)
        self._max_queue = max_queue
        self._session: _Session | None = None
        self._unsub: Callable[[], None] | None = None
        self._last_error: str | None = None
        self._last_info: RecordingInfo | None = None
        self._dropped = 0

    # ---- control

    def start(self, label: str | None = None) -> RecordingInfo:
        """Start a new file. Never overwrites. Raises RuntimeError if already recording."""
        if self._session is not None and self._session.error is None:
            raise RuntimeError("already recording")
        if self._session is not None:
            self.stop()  # clean up the failed session
        self._last_error = None
        now = self._clock()
        self._dir.mkdir(parents=True, exist_ok=True)
        label = label.strip() if label else None
        path, fh = self._create(now, safe_label(label) if label else "")
        try:
            meta = {**self._meta, "started": ns_to_iso(now)}
            if label:
                meta["label"] = label
            meta.pop("v", None)
            head = [f"#exlog v={FORMAT_VERSION}", f"#meta {format_kv(meta)}"]
            head += [f"#source {format_kv(s)}" for s in self._sources]
            head_bytes = ("\n".join(head) + "\n").encode("utf-8")
            fh.write(head_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        except OSError:
            with contextlib.suppress(OSError):
                fh.close()
            raise
        s = _Session(path, fh, self._registry, self._flush_s, self._fsync_s, now, self._max_queue, self._on_error)
        s.bytes = len(head_bytes)
        self._session = s
        s.thread.start()
        self._unsub = self._bus.subscribe(self._on_packet)
        self._bus.publish(GsEvent(t=now, src="gs", kind=GsEventKind.rec_start, detail="manual"))
        return s.info()

    def _create(self, now: int, label: str) -> tuple[Path, IO[bytes]]:
        stem = datetime.fromtimestamp(now / 1e9).strftime("%Y-%m-%d_%H-%M-%S") + (f"_{label}" if label else "")
        n = 1
        while True:
            name = stem + ("" if n == 1 else f"-{n}") + ".exlog"
            path = self._dir / name
            try:
                return path, self._opener(path)
            except FileExistsError:
                n += 1

    def stop(self) -> RecordingInfo | None:
        """Flush, fsync, write the trailer. None if not recording."""
        s = self._session
        if s is None:
            return None
        now = self._clock()
        if s.error is None:
            self._bus.publish(GsEvent(t=now, src="gs", kind=GsEventKind.rec_stop, detail="manual"))
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if s.thread.is_alive():
            with contextlib.suppress(queue.Full):
                s.q.put((_STOP, now), timeout=10)
        s.thread.join(timeout=30)
        s.stopped = now
        self._session = None
        self._dropped += s.dropped
        if s.error is not None:
            self._last_error = s.error
        self._last_info = s.info()
        return self._last_info

    def note(self, text: str) -> None:
        """Publish a `Note` packet (recorded if a recording is active)."""
        self._bus.publish(Note(t=self._clock(), src="gs", text=text))

    def source_connected(self, info: Mapping[str, str]) -> None:
        """Write a `#source` line (call on each source connect while recording)."""
        s = self._session
        if s is not None:
            s.put(f"#source {format_kv(dict(info))}")

    def status(self) -> RecorderStatus:
        s = self._session
        err = s.error if s is not None and s.error is not None else self._last_error
        try:
            free = shutil.disk_usage(self._dir if self._dir.exists() else self._dir.parent).free
        except OSError:
            free = None
        info = s.info() if s is not None else self._last_info
        return RecorderStatus(
            active=s is not None and s.error is None,
            info=info,
            last_error=err,
            free_bytes=free,
            dropped=self._dropped + (s.dropped if s is not None else 0),
        )

    def _on_packet(self, pkt: Packet) -> None:
        s = self._session
        if s is not None:
            s.put(pkt)
