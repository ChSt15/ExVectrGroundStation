"""FakeSource: generates packets directly from the scripted scenario (design section 6.5).

It is a source, not a sink: it cannot be connected to a vehicle and implements no vehicle logic.
Rate scaling (`speed`) only makes simulated time pass faster than wall time.
"""

from __future__ import annotations

import asyncio
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..core.clock import Clock, MonotonicClock
from ..core.config import ConfigError, SourceConfig
from ..packets import REGISTRY, Packet
from ..packets.defs.host import ConsoleText, GsEvent, GsEventKind, LinkStats
from ..packets.defs.link import RcOrigin, RcState, RequestAck
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
    # vehicle status and the USB debug set (tuning graphs, raw sensor panel)
    "VehicleStatus": 10,
    "Actuators": 50,
    "ImuRaw": 100,
    "Estimator": 20,
    "TaskTiming": 5,
    "Calibration": 0.5,
}
RADIO_RATES: dict[str, float] = {
    "Attitude": 10,
    "Position": 10,
    "VehicleState": 5,
    "MissionState": 5,  # AIR_MISSION
    # AIR_ACTUATORS: 25 frames/s with two samples each = 50 samples/s
    "Actuators": 50,
    "FlapState": 50,
    "RadioLinkStats": 2,
    "RcState": 10,
}
PROFILES = {"usb": USB_RATES, "radio": RADIO_RATES}

# Fields the radio telemetry set does not carry: sent as None ("not supplied").
RADIO_UNSUPPLIED: dict[str, set[str]] = {
    "Attitude": {"tilt_acc", "north_acc"},
    "Position": {"h_acc", "v_acc"},
    "VehicleState": {"tvc_thrust", "tvc_torque", "ground_dist", "ground_dist_cov"},
    "Actuators": {"ground_distance"},
    "FlapState": {"cmd", "enabled"},
}
# Fields only the radio set carries (AIR_ACTUATORS roll torque): None on the usb profile.
USB_UNSUPPLIED: dict[str, set[str]] = {"Actuators": {"roll_torque"}}

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
        # Reported `VehicleStatus.simulation` / `VehicleState.sim` (test double: false looks like a real vehicle)
        self.simulation = bool(opts.get("simulation", True))
        self.scenario = Scenario(int(opts.get("seed", 1)))
        self.faults = parse_faults(opts.get("faults", []))
        # Test double of a vehicle answering REQUESTs (no vehicle logic). `answer_requests = true` makes this
        # source accept GS Request packets and answer each with a RequestAck of `request_status` (0 DONE,
        # 2 REJECTED_STATE, ...), idempotent per req_id like the vehicle. `request_drop_first = N` swallows the
        # first N attempts of every req_id (to test retries; a large N never answers: timeout).
        self.answer_requests = bool(opts.get("answer_requests", False))
        self.request_status = int(opts.get("request_status", 0))
        self.request_detail = int(opts.get("request_detail", 0))
        self.request_drop_first = int(opts.get("request_drop_first", 0))
        targets = tuple(int(x) for x in opts.get("request_targets", (1, 2, 3)))
        self.received_requests: deque[Packet] = deque(maxlen=1000)  # every attempt that reached this source
        self.executed_requests: list[tuple[int, int, int]] = []  # (req_id, action, target), once per req_id
        self._attempts: dict[int, int] = {}
        # TEMPORARY legacy telecommand test double: `answer_telecommands = true` accepts GS Telecommand packets
        # (recorded in `received_telecommands`, nothing else happens, no vehicle logic).
        self.answer_telecommands = bool(opts.get("answer_telecommands", False))
        self.received_telecommands: list[Packet] = []
        self._sink: PacketSink | None = None
        self.caps = SourceCaps(
            rc_role=cfg.rc_role,  # type: ignore[arg-type]
            can_send=cfg.rc_role == "control" or self.answer_requests or self.answer_telecommands,
            request_targets=targets if (self.answer_requests or self.answer_telecommands) else (),
        )
        # Test-only RC round trip (design 6.5). With `echo_rc = true`, the last received GS RcState value of
        # channel `echo_channel` (1-based, default 5) is echoed as `MissionState.mission_index`
        # (round((us - 1000) / 500): 0, 1, 2). It implements no vehicle logic.
        self.echo_rc = bool(opts.get("echo_rc", False))
        self.echo_channel = int(opts.get("echo_channel", 5))
        if not 1 <= self.echo_channel <= 16:
            raise ConfigError(f"fake source {cfg.id}: echo_channel must be within 1..16")
        self.received_rc: deque[RcState] = deque(maxlen=1000)  # test observation of what reached this source
        self._echo_idx: int | None = None
        self.enabled = cfg.enabled
        self.disabled_by: str | None = None if cfg.enabled else "config"
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
        if self.profile_name == "usb":
            for name in USB_UNSUPPLIED.get(type_name, ()):
                fields[name] = None
        if type_name == "Position" and fields.get("pos") is not None:
            for f in self.faults:  # fires once, at the first Position at or after `at`
                if f.kind == "nan" and not f.fired and td >= f.at:
                    f.fired = True
                    fields["pos"] = [math.nan, *fields["pos"][1:]]
        if type_name == "MissionState" and self.echo_rc and self._echo_idx is not None:
            fields["mission_index"] = self._echo_idx
        if type_name == "VehicleStatus":
            fields["simulation"] = self.simulation
        if type_name == "VehicleState":
            fields["sim"] = self.simulation
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
        self._sink = sink
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
        """Accepts GS RcState while connected and rc_role is control (like a real control link), and (with
        `answer_requests`) Request packets for a reachable target; everything else is refused. Used by the RC
        loop and request tests."""
        if getattr(pkt, "TYPE", None) == "Request":
            return self._on_request(pkt)
        if getattr(pkt, "TYPE", None) == "Telecommand":
            if not self.answer_telecommands or self._state != "connected":
                return False
            self.received_telecommands.append(pkt)
            return True
        if getattr(pkt, "TYPE", None) != "RcState" or self.caps.rc_role != "control" or self._state != "connected":
            return False
        ch = getattr(pkt, "ch_us", None)
        if ch is None or len(ch) < self.echo_channel:
            return False
        self.received_rc.append(pkt)  # type: ignore[arg-type]
        self._echo_idx = round((ch[self.echo_channel - 1] - 1000) / 500)
        return True

    def _on_request(self, pkt: Packet) -> bool:
        if not self.answer_requests or self._state != "connected" or pkt.target not in self.caps.request_targets:  # type: ignore[attr-defined]
            return False
        rid = int(pkt.req_id)  # type: ignore[attr-defined]
        self.received_requests.append(pkt)
        n = self._attempts[rid] = self._attempts.get(rid, 0) + 1
        if rid not in {r[0] for r in self.executed_requests}:
            self.executed_requests.append((rid, int(pkt.action), int(pkt.target)))  # type: ignore[attr-defined]
        if n <= self.request_drop_first or self._sink is None:
            return True  # accepted onto the link, the "vehicle" does not answer
        ack = RequestAck(
            t=self._clock.now(), src=self.id, req_id=rid, status=self.request_status, detail=self.request_detail
        )
        asyncio.get_running_loop().call_soon(self._sink, ack)
        return True

    def status(self) -> SourceStatus:
        return SourceStatus(
            id=self.id,
            kind=self.kind,
            enabled=self.enabled,
            disabled_by=None if self.enabled else self.disabled_by,
            state=self._state,  # type: ignore[arg-type]
            device=f"fake:{self.profile_name}",
            since=self._since,
            reconnects=self._reconnects,
            last_error=None,
        )
