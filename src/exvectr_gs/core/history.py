"""Time series of packets: types declared `history=True` (3D trail, 20 Hz) and the plot buffer of the
tuning graphs (`PLOT_TYPES`, full rate up to 200 Hz, about two minutes)."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping

from ..packets import PacketRegistry
from ..packets.base import Packet

# Types kept by the plot history (docs/gui.md "Tuning view"): everything the graph catalogue plots.
PLOT_TYPES = (
    "Attitude", "Position", "ImuRaw", "Actuators", "FlapState", "TvcState", "MissionState", "Estimator",
    "TaskTiming", "VehicleState", "Gps",
)  # fmt: skip
PLOT_WINDOW_S = 130.0  # the longest graph window is 2 min
PLOT_MAX_HZ = 200.0  # keeps 100 Hz telemetry with jitter complete


class History:
    def __init__(
        self,
        registry: PacketRegistry,
        window_s: float = 600,
        max_hz: float = 20,
        types: Iterable[str] | None = None,
    ):
        """`types`: keep exactly these packet types instead of the ones declared `history=True`."""
        self._registry = registry
        self._types = frozenset(types) if types is not None else None
        self._window_ns = int(window_s * 1e9)
        self._min_dt_ns = int(1e9 / max_hz)
        self._series: dict[tuple[str, str], deque[Packet]] = defaultdict(deque)

    def update(self, pkt: Packet) -> None:
        """Only types with history=True (or the configured `types`), downsampled to max_hz."""
        if self._types is not None:
            if pkt.TYPE not in self._types:
                return
        else:
            try:
                if not self._registry.get(pkt.TYPE).history:
                    return
            except KeyError:
                return
        dq = self._series[(pkt.TYPE, pkt.src)]
        if dq and pkt.t - dq[-1].t < self._min_dt_ns:
            return
        dq.append(pkt)
        while dq and pkt.t - dq[0].t > self._window_ns:
            dq.popleft()

    def load(self, series: Mapping[tuple[str, str], Iterable[Packet]]) -> None:
        """Replace all series with the given `(type, src) -> packets` (replay preload). The retention
        window does not apply: a replay keeps the whole log and clients query a time range."""
        self._series = defaultdict(deque, {k: deque(v) for k, v in series.items()})

    def clear(self) -> None:
        self._series = defaultdict(deque)

    def query(
        self, type_name: str, src: str | None, fields: list[str], t0: int, t1: int, max_points: int
    ) -> dict[str, list]:
        """`{"t": [...], "<field>": [...]}` for t0 <= t <= t1, at most max_points (even decimation).
        src=None picks the source with the newest data. `t` is integer ns."""
        out: dict[str, list] = {"t": [], **{name: [] for name in fields}}
        if src is None:
            cands = [(k[1], dq) for k, dq in self._series.items() if k[0] == type_name and dq]
            if not cands:
                return out
            dq = max(cands, key=lambda c: c[1][-1].t)[1]
        else:
            dq = self._series.get((type_name, src))
            if not dq:
                return out
        pkts = list(dq)
        ts = [p.t for p in pkts]
        sel = pkts[bisect_left(ts, t0) : bisect_right(ts, t1)]
        if max_points > 0 and len(sel) > max_points:
            step = len(sel) / max_points
            sel = [sel[int(i * step)] for i in range(max_points)]
        for p in sel:
            out["t"].append(p.t)
            for name in fields:
                out[name].append(getattr(p, name, None))
        return out
