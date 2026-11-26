"""A small synthetic log with one mission (countdown, Hover, Ascent, Completed), vehicle status changes, console
lines and GS events, written through the Recorder. Used by the mission event tests."""

from __future__ import annotations

from pathlib import Path

from exvectr_gs.core.bus import Bus
from exvectr_gs.packets import REGISTRY
from exvectr_gs.packets.defs.host import ConsoleText, GsEvent, GsEventKind
from exvectr_gs.packets.defs.vehicle import MissionMode, MissionPhase, MissionState, VehicleMode, VehicleStatus
from exvectr_gs.recording import Recorder

from .support_logs import T0_NS

S = 1_000_000_000
T0_MISSION_S = 8  # mission T0 is at log second 8 (the countdown starts at second 3)


def make_mission_log(directory: Path, name: str = "mission") -> Path:
    bus = Bus()
    now = [T0_NS]
    rec = Recorder(
        directory, REGISTRY, bus, {"gs": "t", "host": "t", "profile": "fake"},
        sources=[{"id": "usb", "kind": "fake", "device": "", "rc_role": "none"}], clock=lambda: now[0],
    )  # fmt: skip
    info = rec.start(name)

    def pub(sec: float, pkt):
        now[0] = T0_NS + int(sec * S)
        bus.publish(pkt)

    for sec in range(0, 22):
        t = T0_NS + sec * S
        tm = sec - T0_MISSION_S
        if sec < 3:
            phase, mm, tmv = MissionPhase.Idle, MissionMode.Idle, None
        elif sec < T0_MISSION_S:
            phase, mm, tmv = MissionPhase.Idle, MissionMode.Startup, tm * S
        elif sec < 14:
            phase, mm, tmv = MissionPhase.Hover, MissionMode.Running, tm * S
        elif sec < 20:
            phase, mm, tmv = MissionPhase.Ascent, MissionMode.Running, (sec - 14) * S
        else:
            phase, mm, tmv = MissionPhase.Completed, MissionMode.Finished, (sec - 14) * S
        pub(sec, MissionState(t=t, src="usb", mission_phase=phase, mission_mode=mm, t_mission=tmv, mission_index=0))
        pub(
            sec + 0.01,
            VehicleStatus(
                t=t + S // 100, src="usb",
                mode=VehicleMode.Running if 4 <= sec < 20 else VehicleMode.Ready,
                armed=4 <= sec < 20, motors_enabled=T0_MISSION_S <= sec < 19, simulation=False, failsafe=sec >= 17,
                failure_bits=0,
            ),
        )  # fmt: skip
    pub(5.5, ConsoleText(t=T0_NS + int(5.5 * S), src="usb", text="[node 1 WARNING] Calibration warning demo"))
    pub(
        12.5,
        ConsoleText(t=T0_NS + int(12.5 * S), src="usb", text="[node 1 INFO] Started bellyflop kick, target q 1 0 0 0"),
    )
    pub(2.5, GsEvent(t=T0_NS + int(2.5 * S), src="gs", kind=GsEventKind.source_up, detail="usb: connected"))
    now[0] = T0_NS + 22 * S
    rec.stop()
    return info.path
