"""FakeSource: generates packets directly from the scripted scenario (design section 6.5).

It is a source, not a sink: it cannot be connected to a vehicle and implements no vehicle logic.
Rate scaling (`speed`) only makes simulated time pass faster than wall time.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any

from ..core.clock import Clock, MonotonicClock
from ..core.config import ConfigError, SourceConfig
from ..packets import REGISTRY, Packet
from ..packets.defs.host import ConsoleText, GsEvent, GsEventKind, LinkStats
from ..packets.defs.link import RcOrigin, RcState
from .base import PacketSink, Source, SourceCaps, SourceKind, SourceStatus
from .scenario import Scenario

USB_RATES: dict[str, float] = {
    "Attitude": 100,
    "Position": 50,
    "VehicleState": 20,
    "FlapState": 50,
    "TvcState": 50,
    "MissionState": 10,
    "Gps": 1,
    "ConsoleText": 0.5,
}
RADIO_RATES: dict[str, float] = {
    "Attitude": 10,
    "Position": 10,
    "VehicleState": 5,
    "MissionState": 2,
    "RadioLinkStats": 2,
    "RcState": 10,
}
PROFILES = {"usb": USB_RATES, "radio": RADIO_RATES}

# Fields the radio telemetry set does not carry: sent as None ("not supplied").
RADIO_UNSUPPLIED: dict[str, set[str]] = {
    "Attitude": {"tilt_acc", "north_acc"},
    "Position": {"h_acc", "v_acc"},
    "VehicleState": {"tvc_thrust", "tvc_torque", "ground_dist", "ground_dist_cov"},
}

FAULT_KINDS = ("dropout", "rate", "disconnect", "nan", "unknown_enum", "failure_flag")


@dataclass
class Fault:
    kind: str
    at: float  # simulation time, s
    duration: float = 0.0
    types: tuple[str, ...] = ()
    scale: float = 1.0  # for "rate"
    fired: bool = field(default=False, compare=False)

    def active(self, t: float) -> bool:
        return self.at <= t < self.at + self.duration

    def covers(self, type_name: str) -> bool:
        return not self.types or type_name in self.types


def parse_faults(raw: list[dict[str, Any]]) -> list[Fault]:
    faults = []
    for d in raw:
        kind = d.get("kind")
        if kind not in FAULT_KINDS:
            raise ConfigError(f"fake source: unknown fault kind {kind!r}, expected one of {FAULT_KINDS}")
        faults.append(
            Fault(
                kind,
                float(d["at"]),
                float(d.get("duration", 0.0)),
                tuple(d.get("types", ())),
                float(d.get("scale", 1.0)),
            )
        )
    return faults


class FakeSource(Source):
    kind = SourceKind.FAKE

    def __init__(self, cfg: SourceConfig, clock: Clock | None = None):
        opts = dict(cfg.options)
        self.id = cfg.id
        self.profile_name: str = opts.get("profile", "usb")
        if self.profile_name not in PROFILES:
            raise ConfigError(f"fake source {cfg.id}: profile must be one of {sorted(PROFILES)}")
        self.rates = {
            **PROFILES[self.profile_name],
            **{k: float(v) for k, v in opts.get("rates", {}).items()},
        }
        for name in self.rates:
            if name not in REGISTRY._types and name != "ConsoleText":
                raise ConfigError(f"fake source {cfg.id}: unknown packet type {name!r} in rates")
        self.speed = float(opts.get("speed", 1.0))
        self.scenario = Scenario(int(opts.get("seed", 1)))
        self.faults = parse_faults(opts.get("faults", []))
        self.caps = SourceCaps(rc_role=cfg.rc_role, can_send=False)  # type: ignore[arg-type]
        self.enabled = cfg.enabled
        self._clock = clock or MonotonicClock()
        self._tick_s = 0.005
        self.reset(0)

    # ---- deterministic generation (no clock, no I/O: directly testable)

    def reset(self, t0_ns: int) -> None:
        """Restart the scenario; packet host times are t0_ns + sim time / speed."""
        self._t0_ns = t0_ns
        self._next_due = {name: 0.0 for name in self.rates}
        self._next_stats = 1.0
        self._stats_frames = 0
        self._seq = 0
        self._last_t = -1.0
        self._announced = False
        self._state: str = "disabled"
        self._since = t0_ns
        self._reconnects = 0
        for f in self.faults:
            f.fired = False

    def _host_t(self, sim_t: float) -> int:
        return self._t0_ns + int(sim_t * 1e9 / self.speed)

    def _fault(self, kind: str, t: float, type_name: str | None = None) -> Fault | None:
        for f in self.faults:
            if f.kind == kind and f.active(t) and (type_name is None or f.covers(type_name)):
                return f
        return None

    def _make(self, type_name: str, td: float) -> Packet:
        t = self._host_t(td)
        if type_name == "ConsoleText":
            mode, _, phase = self.scenario.phase_at(td)
            return ConsoleText(t=t, src=self.id, text=f"[fake] t={td:.1f}s mode={mode.name} phase={phase.name}")
        if type_name == "RcState":
            self._seq += 1
            return RcState(
                t=t,
                src=self.id,
                origin=RcOrigin.handset,
                ch_us=self.scenario.handset_channels(td),
                seq=self._seq,
            )
        fields = self.scenario.fields(type_name, td)
        if self.profile_name == "radio":
            for name in RADIO_UNSUPPLIED.get(type_name, ()):
                fields[name] = None
        if type_name == "Position" and fields.get("pos") is not None:
            for f in self.faults:  # fires once, at the first Position at or after `at`
                if f.kind == "nan" and not f.fired and td >= f.at:
                    f.fired = True
                    fields["pos"] = [math.nan, *fields["pos"][1:]]
        if type_name == "VehicleState":
            if self._fault("unknown_enum", td):
                fields["mode"] = 9  # not a VehicleMode member
            if any(f.kind == "failure_flag" and td >= f.at for f in self.faults):
                fields["fail_sensor"] = True
        return REGISTRY.get(type_name).cls(t=t, src=self.id, **fields)

    def _event(self, kind: GsEventKind, td: float, detail: str) -> GsEvent:
        return GsEvent(t=self._host_t(td), src="gs", kind=kind, detail=f"{self.id}: {detail}")

    def _scale(self, type_name: str, t: float) -> float:
        f = self._fault("rate", t, type_name)
        return f.scale if f else 1.0

    def step(self, sim_t: float) -> list[Packet]:
        """All packets due up to simulation time `sim_t`, in time order."""
        out: list[tuple[float, Packet]] = []
        if not self._announced:
            self._announced = True
            self._state = "connected"
            out.append((0.0, self._event(GsEventKind.source_up, 0.0, f"fake source ({self.profile_name})")))
        for f in self.faults:  # connection transitions inside (last step, sim_t], however coarse the step
            if f.kind != "disconnect":
                continue
            if self._last_t < f.at <= sim_t:
                out.append((f.at, self._event(GsEventKind.source_down, f.at, "injected disconnect")))
            if self._last_t < f.at + f.duration <= sim_t:
                self._reconnects += 1
                out.append((f.at + f.duration, self._event(GsEventKind.source_up, f.at + f.duration, "reconnected")))
        self._last_t = sim_t
        self._state = "searching" if self._fault("disconnect", sim_t) else "connected"
        for name, rate in self.rates.items():
            if rate <= 0:
                continue
            while self._next_due[name] <= sim_t:
                td = self._next_due[name]
                self._next_due[name] = td + 1.0 / (rate * self._scale(name, td))
                if self._fault("disconnect", td) or self._fault("dropout", td, name):
                    continue
                out.append((td, self._make(name, td)))
                self._stats_frames += 1
        while self._next_stats <= sim_t:
            td = self._next_stats
            self._next_stats += 1.0
            out.append(
                (
                    td,
                    LinkStats(
                        t=self._host_t(td),
                        src=self.id,
                        connected=self._fault("disconnect", td) is None,
                        rx_frames_s=float(self._stats_frames),
                        tx_frames_s=0.0,
                        crc_errors=0,
                        decode_errors=0,
                        reconnects=self._reconnects,
                    ),
                )
            )
            self._stats_frames = 0
        out.sort(key=lambda x: x[0])
        return [p for _, p in out]

    # ---- Source interface

    async def run(self, sink: PacketSink) -> None:
        self.reset(self._clock.now())
        try:
            while True:
                sim_t = (self._clock.now() - self._t0_ns) / 1e9 * self.speed
                for pkt in self.step(sim_t):
                    sink(pkt)
                await asyncio.sleep(self._tick_s)
        finally:
            self._state = "disabled"

    async def send(self, pkt: Packet) -> bool:
        return False  # the optional test-only echo (echo_rc) arrives with the RC controller (T6)

    def status(self) -> SourceStatus:
        return SourceStatus(
            id=self.id,
            kind=self.kind,
            enabled=self.enabled,
            state=self._state,  # type: ignore[arg-type]
            device=f"fake:{self.profile_name}",
            since=self._since,
            reconnects=self._reconnects,
            last_error=None,
        )
