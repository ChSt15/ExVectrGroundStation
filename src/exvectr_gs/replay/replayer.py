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
from ..core.history import History
from ..core.state import StateStore
from ..packets import Packet
from ..packets.textcodec import decode_line
from ..recording import LogInfo, LogLibrary, LogReader
from ..recording.logfile import LogIndex, Marker, _open

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
