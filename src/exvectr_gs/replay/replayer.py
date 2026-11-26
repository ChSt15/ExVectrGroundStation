"""Replayer: plays a recorded log into the replay view (design sections 5.7 and 6.4).

Replay is not a Source. It publishes on its own bus (the ReplayBus) and keeps its own StateStore and
History, so recording of live data is unaffected and replayed data is never recorded. Lines go through the
same text codec as everything else, so panels see the same `Packet` objects as for live data.

The replay position `t` is an absolute host time in ns, the `t` of the log's packets. A log's index
(`LogReader.build_index`) gives seek points: a keyframe every second of log time that names the latest
line per (type, src), so a seek decodes a few dozen lines plus at most one second of log, independent
of the log length.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from bisect import bisect_right
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal

from ..core.bus import Bus
from ..core.event_history import EVENT_TYPES, EventFilter
from ..core.history import History
from ..core.rates import SYNTHETIC_TYPES, RateTracker, StreamRates, packet_path
from ..core.state import StateStore
from ..packets import REGISTRY, Packet
from ..packets.textcodec import decode_line
from ..recording import LogInfo, LogLibrary, LogReader
from ..recording.logfile import LogIndex, Marker, _open, scan_lines

log = logging.getLogger(__name__)

SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
TICK_S = 0.010  # replay clock step (design 5.7)
MAX_STEP_S = 0.5  # a stalled event loop does not make the replay jump ahead by more than this (wall time)

State = Literal["closed", "loading", "paused", "playing", "ended"]


@dataclass
class ReplayStatus:
    state: State
    name: str | None
    t: int | None
    t_start: int | None
    t_end: int | None
    speed: float


MAX_EVENT_PACKETS = 100_000  # `read_events`: kept packets of the whole log


class Replayer:
    def __init__(
        self,
        library: LogLibrary,
        bus: Bus,
        store: StateStore,
        history: History,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        tick_s: float = TICK_S,
    ):
        self._library = library
        self._bus = bus
        self._store = store
        self._history = history
        self._mono = monotonic
        self._tick_s = tick_s
        self._state: State = "closed"
        self._speed = 1.0
        self._generation = 0  # bumped by open/close so a late index build is discarded
        self._info: LogInfo | None = None
        self._reader: LogReader | None = None
        self._index: LogIndex | None = None
        self._t = 0  # replay clock (ns)
        self._iter: Iterator[tuple[int, Packet]] | None = None
        self._peek: tuple[int, Packet] | None = None  # next unpublished line, decoded
        self._peek_t = 0
        self._last_eff = 0  # effective (running max) time of the last line taken from `_iter`
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------ open / close

    async def open(self, name: str) -> LogInfo:
        """Index the log in a worker thread, then show its first state, paused at `t_start`.
        Raises FileNotFoundError if there is no such log."""
        info = self._library.get(name)
        if info is None:
            raise FileNotFoundError(f"no such log: {name}")
        self._stop_task()
        self._generation += 1
        gen = self._generation
        self._info, self._index, self._reader = info, None, None
        self._state = "loading"
        self._clear_view()
        reader = LogReader(info.path)
        index = await asyncio.to_thread(reader.build_index)
        if gen != self._generation:  # closed or another log opened meanwhile
            raise RuntimeError("replay open superseded by close or another open")
        self._reader, self._index = reader, index
        self._state = "paused"
        self._history.load(index.history)
        self._seek_to(index.t_start if index.t_start is not None else info.started)
        return info

    def close(self) -> None:
        self._generation += 1
        self._stop_task()
        self._state = "closed"
        self._info = self._reader = self._index = None
        self._t = 0
        self._clear_view()

    def _clear_view(self) -> None:
        self._store.reset()
        self._history.clear()
        self._reset_cursor()

    # ------------------------------------------------------------ transport

    def play(self) -> None:
        self._need_open()
        if self._state == "playing":
            return
        if self._state == "ended":
            self._seek_to(self._t_start())
        self._state = "playing"
        if self._t >= self._t_end() and self._peek is None and self._iter is None:
            self._state = "ended"
            return
        self._task = asyncio.get_running_loop().create_task(self._run(), name="replay-clock")

    def pause(self) -> None:
        self._need_open()
        if self._state == "playing":
            self._stop_task()
            self._state = "paused"

    def seek(self, t: int) -> None:
        """Move to absolute time `t` (clamped to the log). Keeps the play/pause state; from `ended`
        a seek before the end pauses. Replaces the replay state store without publishing."""
        self._need_open()
        self._seek_to(t)
        if self._state == "ended" and self._t < self._t_end():
            self._state = "paused"

    def set_speed(self, speed: float) -> None:
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {', '.join(str(s) for s in SPEEDS)}")
        self._speed = float(speed)

    def status(self) -> ReplayStatus:
        idx = self._index
        loaded = idx is not None and self._info is not None
        return ReplayStatus(
            state=self._state,
            name=self._info.name if self._info is not None else None,
            t=self._t if loaded else None,
            t_start=self._t_start() if loaded else None,
            t_end=self._t_end() if loaded else None,
            speed=self._speed,
        )

    def read_series(
        self, specs: list[dict], t0: int, t1: int, max_points: int, whole: bool = False
    ) -> list[dict[str, list]]:
        """Full-rate series of the open log for the tuning graphs (get_history buffer "plot", replay view).
        `specs`: `[{type, src, fields}]`; returns one `{"t": [...ns], "<field>": [...]}` per spec, like
        `History.query`. Reads the log file: blocking, call it in a worker thread. The span is limited to
        `MAX_SERIES_SPAN_S` before t1 unless `whole` (the replay graphs load the entire recording once; the points are
        thinned by a stride while reading, so memory stays bounded). Raises RuntimeError when no log is open."""
        idx = self._need_open()
        reader = self._reader
        assert reader is not None
        return read_series(reader.path, idx, specs, t0, t1, max_points, whole)

    def read_events(self, limit: int = MAX_EVENT_PACKETS) -> tuple[list[Packet], bool]:
        """The packets of the whole open log that the mission event list is derived from (`EVENT_TYPES`, reduced by
        `EventFilter`), in time order, and whether the list was cut at `limit`. One pass over the file: blocking,
        call it in a worker thread. Raises RuntimeError when no log is open."""
        idx = self._need_open()
        reader = self._reader
        assert reader is not None
        return read_events(reader.path, idx, limit)

    def read_rates(self, windows: tuple[float, ...]) -> tuple[int, list[StreamRates]]:
        """Per-type packet rates at the replay position, in replay time: the log lines of the last `max(windows)` s
        before `t` (stateless, so a seek needs no reset). Packet level only (a log holds no wire frames): no byte
        counts, paths from the `#source` kind and the type. Blocking, call it in a worker thread."""
        idx = self._need_open()
        reader = self._reader
        assert reader is not None
        t = self._t
        kinds = {s.get("id", ""): s.get("kind", "") for s in reader.sources()}
        longest = int(max(windows) * 1e9)
        n = bisect_right(idx.times, t)
        end = idx.offsets[n] if n < len(idx.offsets) else idx.size
        tr = RateTracker(windows)
        tr.begin(max(self._t_start(), t - longest))
        nominal: dict[str, float] = {}
        for off, raw in scan_lines(reader.path, idx.offset_for(t - longest)):
            if off >= end:
                break
            if raw.startswith(b"#"):
                continue
            parts = raw.split(b" ", 3)
            if len(parts) < 3:
                continue
            try:
                lt = int(parts[0])
            except ValueError:
                continue
            src, ptype = parts[1].decode("utf-8", "replace"), parts[2].decode("utf-8", "replace")
            if src == "gs" or ptype in SYNTHETIC_TYPES:
                continue
            if ptype not in nominal:
                try:
                    nominal[ptype] = REGISTRY.get(ptype).nominal_rate_hz or 0.0
                except KeyError:
                    nominal[ptype] = 0.0
            tr.record(src, ptype, None, packet_path(kinds.get(src, ""), ptype), lt, nominal_hz=nominal[ptype])
        return t, tr.snapshot(t)

    def markers(self) -> list[Marker]:
        """Event, note and mode-change markers of the open log (for the transport bar)."""
        return list(self._index.markers) if self._index is not None else []

    # ------------------------------------------------------------ internals

    def _need_open(self) -> LogIndex:
        if self._index is None or self._state in ("closed", "loading"):
            raise RuntimeError("no replay log is open")
        return self._index

    def _t_start(self) -> int:
        idx = self._index
        assert idx is not None and self._info is not None
        return idx.t_start if idx.t_start is not None else self._info.started

    def _t_end(self) -> int:
        idx = self._index
        assert idx is not None
        return idx.t_end if idx.t_end is not None else self._t_start()

    def _stop_task(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _reset_cursor(self) -> None:
        self._iter = None
        self._peek = None
        self._last_eff = 0

    def _seek_to(self, t: int) -> None:
        """State at `t` equals applying every line with (index) time <= t in file order."""
        idx, reader = self._index, self._reader
        assert idx is not None and reader is not None
        t = max(self._t_start(), min(t, self._t_end()))
        n = bisect_right(idx.times, t)  # lines 0..n-1 are at or before t
        packets: dict[tuple[str, str], Packet] = {}
        kf = idx.keyframe_for(t)
        resume = 0
        if kf is not None:
            for pkt in _read_at(reader, sorted(kf.latest.values())):
                packets[(_type_of(pkt), pkt.src)] = pkt
            resume = kf.offset
        limit = idx.offsets[n] if n < len(idx.offsets) else None
        for off, pkt in reader.iter_packets(resume):
            if limit is not None and off >= limit:
                break
            packets[(_type_of(pkt), pkt.src)] = pkt
        ordered = sorted(packets.values(), key=lambda p: p.t)
        self._store.reset(ordered)
        self._reset_cursor()
        self._t = t
        self._last_eff = idx.times[n - 1] if n else 0
        if n < len(idx.offsets):
            self._iter = reader.iter_packets(idx.offsets[n])

    def _fill_peek(self) -> None:
        if self._peek is None and self._iter is not None:
            nxt = next(self._iter, None)
            if nxt is None:
                self._iter = None
                return
            self._peek = nxt
            self._last_eff = self._peek_t = max(self._last_eff, nxt[1].t)

    def _advance(self, wall_dt: float) -> None:
        """Move the clock by `wall_dt` seconds of wall time at the current speed and publish due lines."""
        end = self._t_end()
        self._t = min(end, self._t + int(wall_dt * self._speed * 1e9))
        while True:
            self._fill_peek()
            if self._peek is None or self._peek_t > self._t:
                break
            pkt = self._peek[1]
            self._peek = None
            self._store.update(pkt)
            self._bus.publish(pkt)
        if self._state == "playing" and self._t >= end and self._peek is None and self._iter is None:
            self._state = "ended"

    async def _run(self) -> None:
        last = self._mono()
        try:
            while self._state == "playing":
                await asyncio.sleep(self._tick_s)
                now = self._mono()
                self._advance(min(now - last, MAX_STEP_S))
                last = now
        finally:
            if self._task is asyncio.current_task():
                self._task = None


MAX_SERIES_SPAN_S = 600
_OUT_OF_ORDER_NS = 1_000_000_000  # log lines may be slightly out of time order; read this far past t1


def read_series(
    path, idx: LogIndex, specs: list[dict], t0: int, t1: int, max_points: int, whole: bool = False
) -> list[dict[str, list]]:
    """One pass over the lines with t0 <= t <= t1 of a log, decoding only the wanted types (see
    `Replayer.read_series`). A spec without `src` takes the source with the most packets in the range."""
    if not whole:
        t0 = max(t0, t1 - MAX_SERIES_SPAN_S * 1_000_000_000)
    want = {s["type"] for s in specs}
    per: dict[tuple[str, str], list[Packet]] = {}
    seen: dict[tuple[str, str], int] = {}  # packets per key before thinning
    stride: dict[tuple[str, str], int] = {}
    if t1 >= t0:
        with _open(path) as fh:
            fh.seek(idx.offset_for(t0))
            for raw in fh:
                if not raw.endswith(b"\n") or raw[:1] == b"#":
                    continue
                parts = raw.split(b" ", 3)
                if len(parts) < 3:
                    continue
                try:
                    t = int(parts[0])
                except ValueError:
                    continue
                if t > t1 + _OUT_OF_ORDER_NS:
                    break
                typ = parts[2].decode("ascii", "replace")
                if typ not in want or not t0 <= t <= t1:
                    continue
                with contextlib.suppress(Exception):
                    pkt = decode_line(raw.decode("utf-8", "replace").rstrip("\r\n"))
                    if pkt is not None:
                        key = (typ, pkt.src)
                        n = seen[key] = seen.get(key, 0) + 1
                        k = stride.setdefault(key, 1)
                        if (n - 1) % k:
                            continue
                        lst = per.setdefault(key, [])
                        lst.append(pkt)
                        if max_points > 0 and len(lst) >= 2 * max_points:
                            per[key] = lst[::2]  # keep memory bounded: every other kept packet, double the stride
                            stride[key] = k * 2
    out = []
    for s in specs:
        fields = s["fields"]
        res: dict[str, list] = {"t": [], **{name: [] for name in fields}}
        if s.get("src") is not None:
            pkts = per.get((s["type"], s["src"]), [])
        else:
            cands = [k for k in per if k[0] == s["type"]]
            pkts = per[max(cands, key=lambda k: seen[k])] if cands else []
        pkts.sort(key=lambda p: p.t)
        if max_points > 0 and len(pkts) > max_points:
            step = len(pkts) / max_points
            pkts = [pkts[int(i * step)] for i in range(max_points)]
        for p in pkts:
            res["t"].append(p.t)
            for name in fields:
                res[name].append(getattr(p, name, None))
        out.append(res)
    return out


def read_events(path, idx: LogIndex, limit: int = MAX_EVENT_PACKETS) -> tuple[list[Packet], bool]:
    """Whole log, only the `EVENT_TYPES` lines decoded, change-filtered per (type, source) like the live buffer."""
    out: list[Packet] = []
    flt = EventFilter()
    wanted = {t.encode() for t in EVENT_TYPES}
    with _open(path) as fh:
        fh.seek(idx.offset_for(0))
        for raw in fh:
            if not raw.endswith(b"\n") or raw[:1] == b"#":
                continue
            parts = raw.split(b" ", 3)
            if len(parts) < 3 or parts[2] not in wanted:
                continue
            with contextlib.suppress(Exception):
                pkt = decode_line(raw.decode("utf-8", "replace").rstrip("\r\n"))
                if pkt is not None and flt.accept(pkt):
                    out.append(pkt)
                    if len(out) >= limit:
                        return sorted(out, key=lambda p: p.t), True
    return sorted(out, key=lambda p: p.t), False


def _type_of(pkt: Packet) -> str:
    return pkt.TYPE if pkt.TYPE != "Unknown" else getattr(pkt, "type", "Unknown")


def _read_at(reader: LogReader, offsets: list[int]) -> Iterator[Packet]:
    """Decode the lines starting at the given (ascending) byte offsets with one open file."""
    with _open(reader.path) as fh:
        for off in offsets:
            fh.seek(off)
            raw = fh.readline()
            if not raw.endswith(b"\n"):
                continue
            with contextlib.suppress(Exception):
                pkt = decode_line(raw.decode("utf-8", "replace").rstrip("\r\n"))
                if pkt is not None:
                    yield pkt
