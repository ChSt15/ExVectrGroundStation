"""Per-stream data-rate statistics (docs/api.md `get_rates`, docs/gui.md "Data rates").

A stream is one (source, packet type, origin node) with a PATH: where the data came from before it reached the
ground station. The path matters because `LinkStats.rx_frames_s` counts only the serial hop between the ground
station and its USB peer: for the Router it is dominated by the Pocket's own RC_STATE copies, which never crossed
the air.

| path | meaning |
|---|---|
| `air` | frame from node 1 or 3 received through the Router (crossed the radio link) |
| `usb direct` | frame from the vehicle on its own USB |
| `router local` | frame made by the Router itself (node 2), or the Pocket's RC_STATE copy: never on the air |
| `simulated` | test double (fake source) or a log without frame information |
| `unknown` | a node this code has no rule for |

Two producers feed the same `RateTracker`:

* live serial sources record every received wire frame (`SerialSource`, through the profile's `describe_frame`):
  frames, payload bytes and wire bytes (COBS + delimiter + SKIND + CRC + network header, as on the serial line);
* packet-level producers (the fake source live, and the replay) record semantic packets: frames only, bytes are
  not known (`bytes_known = False`, reported as `null`). One wire frame can give several packets (`Pose` gives
  `Attitude` and `Position`), so a packet-level rate is per packet type.

Time is whatever the caller passes in: host receive time live, the packet timestamps and the replay clock in replay,
never the wall clock inside this module. All of it runs on the event loop (no locks).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field

WINDOWS_S = (2.0, 10.0)  # sliding windows: the table shows the first, the second smooths bursts
MIN_SPAN_S = 0.5  # shortest averaging span: right after start the rate is count / 0.5 s, not a spike

PATH_AIR = "air"
PATH_USB = "usb direct"
PATH_LOCAL = "router local"
PATH_SIM = "simulated"
PATH_UNKNOWN = "unknown"
PATH_ORDER = (PATH_AIR, PATH_USB, PATH_LOCAL, PATH_SIM, PATH_UNKNOWN)

# Types of the Router's own traffic that arrive through a `link_module` source (packet-level: replay, which has no
# node ids). The rest of a link log crossed the air. RxModuleStats is the ship radio node's LINK_STATS: it did.
ROUTER_LOCAL_TYPES = frozenset({"TxModuleStats", "RadioLinkStats", "RcState", "NetworkStatus"})

# Packets the ground station makes itself (not received data): never counted.
SYNTHETIC_TYPES = frozenset({"LinkStats", "ConsoleText", "GsEvent", "RawFrame", "Note"})


@dataclass(frozen=True)
class FrameInfo:
    """What a wire profile says about a received frame (`WireProfile.describe_frame`, optional)."""

    type: str  # wire packet name, e.g. "VEHICLE_STATUS", "HEARTBEAT", "port 0x7F"
    node: int | None  # origin node id
    remote: bool | None  # True: crossed the air (when the source is a radio Router), False: Router-local, None: unknown
    nominal_air_hz: float = 0.0
    nominal_usb_hz: float = 0.0


def frame_path(source_kind: str, remote: bool | None) -> str:
    if source_kind == "vehicle_usb":
        return PATH_USB
    if source_kind == "link_module":
        return PATH_AIR if remote else PATH_LOCAL if remote is False else PATH_UNKNOWN
    return PATH_SIM


def packet_path(source_kind: str, ptype: str) -> str:
    """Path of a packet-level stream (no node ids): by source kind, and for a Router by the type."""
    if source_kind == "link_module":
        return PATH_LOCAL if ptype in ROUTER_LOCAL_TYPES else PATH_AIR
    return frame_path(source_kind, None)


def nominal_for(path: str, info: FrameInfo) -> float:
    """Schema rate that applies on this path: `rate_air` on the air, `rate_usb` on every serial hop (0: none)."""
    if path == PATH_AIR:
        return info.nominal_air_hz
    return info.nominal_usb_hz


StreamKey = tuple[str, str, int | None, str]  # source id, type, origin node, path


@dataclass
class _Stream:
    nominal_hz: float = 0.0
    bytes_known: bool = False
    last_t: int = 0
    total: int = 0
    events: deque = field(default_factory=deque)  # (t_ns, payload bytes, wire bytes)


@dataclass(frozen=True)
class StreamRates:
    source: str
    type: str
    node: int | None
    path: str
    nominal_hz: float
    bytes_known: bool
    age_s: float
    total: int
    rate_hz: tuple[float, ...]  # per window, in WINDOWS_S order
    bytes_s: tuple[float, ...]  # wire bytes per second (payload bytes when the profile has no wire size)
    payload_s: tuple[float, ...]  # payload bytes per second


class RateTracker:
    """Sliding-window rates per stream. `record` takes event times; `snapshot(now)` evaluates at `now`."""

    def __init__(self, windows: tuple[float, ...] = WINDOWS_S):
        self.windows = windows
        self._streams: dict[StreamKey, _Stream] = {}
        self._t0: int | None = None

    def reset(self) -> None:
        self._streams.clear()
        self._t0 = None

    def begin(self, t: int) -> None:
        """Declare that observation started at `t` (ns), before the first event: a window then averages over the
        time actually observed, not over the span between the first event and now."""
        self._t0 = t

    def record(
        self,
        source: str,
        ptype: str,
        node: int | None,
        path: str,
        t: int,
        payload: int | None = None,
        wire: int | None = None,
        nominal_hz: float = 0.0,
    ) -> None:
        """One received frame (or packet). `payload`/`wire` None: size unknown (packet level). A wire size of 0
        (the profile does not know it) falls back to the payload size."""
        key = (source, ptype, node, path)
        s = self._streams.get(key)
        if s is None:
            s = self._streams[key] = _Stream()
        if self._t0 is None:
            self._t0 = t
        s.nominal_hz = nominal_hz
        s.last_t = max(s.last_t, t)
        s.total += 1
        if payload is not None:
            s.bytes_known = True
        p = payload or 0
        s.events.append((t, p, wire or p))

    def snapshot(self, now: int) -> list[StreamRates]:
        """Every stream known, evaluated at `now` (ns). Events older than the longest window are dropped."""
        longest = max(self.windows)
        cutoff = now - int(longest * 1e9)
        out = []
        observed = 0.0 if self._t0 is None else max((now - self._t0) / 1e9, 0.0)
        for (source, ptype, node, path), s in self._streams.items():
            while s.events and s.events[0][0] < cutoff:
                s.events.popleft()
            rate, wire, payload = [], [], []
            for w in self.windows:
                span = min(w, max(observed, MIN_SPAN_S))
                lo = now - int(w * 1e9)
                n = bs = ps = 0
                for t, p, b in reversed(s.events):
                    if t < lo:
                        break
                    if t <= now:
                        n += 1
                        ps += p
                        bs += b
                rate.append(n / span)
                wire.append(bs / span)
                payload.append(ps / span)
            out.append(
                StreamRates(
                    source, ptype, node, path, s.nominal_hz, s.bytes_known,
                    max((now - s.last_t) / 1e9, 0.0), s.total, tuple(rate), tuple(wire), tuple(payload),
                )  # fmt: skip
            )
        return out


def rates_to_json(
    streams: Iterable[StreamRates], windows: tuple[float, ...], now_s: float, view: str, air_capacity: float, measure: str
) -> dict:
    """The `get_rates` reply (docs/api.md). Sorted by path, then wire bytes/s (2 s window) descending."""
    rows = sorted(streams, key=lambda r: (PATH_ORDER.index(r.path), -r.bytes_s[0], -r.rate_hz[0], r.source, r.type))
    out_rows = []
    sums: dict[str, dict] = {}
    for r in rows:
        out_rows.append(
            {
                "source": r.source,
                "type": r.type,
                "node": r.node,
                "path": r.path,
                "nominal_hz": r.nominal_hz or None,
                "age_s": r.age_s,
                "count": r.total,
                "rate_hz": list(r.rate_hz),
                "bytes_s": list(r.bytes_s) if r.bytes_known else None,
                "payload_s": list(r.payload_s) if r.bytes_known else None,
            }
        )
        p = sums.setdefault(
            r.path,
            {"path": r.path, "streams": 0, "rate_hz": [0.0] * len(windows), "bytes_s": [0.0] * len(windows),
             "payload_s": [0.0] * len(windows), "bytes_known": False},
        )  # fmt: skip
        p["streams"] += 1
        p["bytes_known"] = p["bytes_known"] or r.bytes_known
        for i in range(len(windows)):
            p["rate_hz"][i] += r.rate_hz[i]
            p["bytes_s"][i] += r.bytes_s[i]
            p["payload_s"][i] += r.payload_s[i]
    paths = []
    for name in PATH_ORDER:
        if name in sums:
            p = sums[name]
            if not p["bytes_known"]:
                p["bytes_s"] = p["payload_s"] = None
            del p["bytes_known"]
            paths.append(p)
    air = next((p for p in paths if p["path"] == PATH_AIR), None)
    pay = air["payload_s"] if air else None
    util = None if pay is None or air_capacity <= 0 else [x / air_capacity for x in pay]
    return {
        "view": view,
        "now": now_s,
        "windows_s": list(windows),
        "measure": measure,
        "streams": out_rows,
        "paths": paths,
        "air": {"capacity_bytes_s": air_capacity, "payload_s": pay, "utilisation": util},
    }
