"""Decoded `exvectr_packets` objects to ground-station semantic packets (docs/wire-profiles.md).

Decoded values are already SI. Each mapper returns a list: some wire packets feed two GS types so
that the existing panels keep working (POSE gives Attitude and Position, ACTUATORS gives Actuators
and FlapState). Nothing here derives or infers a value: fields the wire does not carry stay None.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from enum import IntEnum
from typing import Any

from . import _deps  # noqa: F401  (puts exvectr_packets on sys.path)

import exvectr_packets as xp  # noqa: E402  isort: skip

from ...packets import Packet
from ...packets.defs import link as L
from ...packets.defs import vehicle as V
from ...packets.defs.host import ConsoleText

Mapper = Callable[[Any, int, str, dict[str, Any]], list[Packet]]


def _enum(cls: type[IntEnum], v: int) -> Any:
    """Known value to enum member, unknown value kept as a plain int (shown as #n, never an error)."""
    try:
        return cls(int(v))
    except ValueError:
        return int(v)


def _flag(flags: int, pkt_cls: type, name: str) -> bool | None:
    """Flag by the generated constant's name. None if the schema no longer has that flag."""
    mask = getattr(pkt_cls, name, None)
    return None if mask is None else bool(flags & mask)


def _fl(xs: Any) -> list[float]:
    return [float(x) for x in xs]


def _vehicle_status(p: xp.VehicleStatus, t: int, src: str, meta: dict) -> list[Packet]:
    armed = _flag(p.flags, xp.VehicleStatus, "FLAGS_ARMED")
    sim = _flag(p.flags, xp.VehicleStatus, "FLAGS_SIMULATION")
    mode = _enum(V.VehicleMode, p.mode)
    status = V.VehicleStatus(
        t=t,
        src=src,
        vehicle_time=p.t_ns,
        mode=mode,
        mission_mode=_enum(V.MissionMode, p.mission_mode),
        mission_phase=_enum(V.MissionPhase, p.mission_phase),
        ctrl_source=_enum(V.ControlSource, p.ctrl_source),
        armed=armed,
        simulation=sim,
        motors_enabled=_flag(p.flags, xp.VehicleStatus, "FLAGS_MOTORS_ENABLED"),
        gs_present=_flag(p.flags, xp.VehicleStatus, "FLAGS_GS_PRESENT"),
        uart_peer_seen=_flag(p.flags, xp.VehicleStatus, "FLAGS_UART_PEER_SEEN"),
        zeroing=_flag(p.flags, xp.VehicleStatus, "FLAGS_ZEROING"),
        incompatible=_flag(p.flags, xp.VehicleStatus, "FLAGS_INCOMPATIBLE"),
        failsafe=_flag(p.flags, xp.VehicleStatus, "FLAGS_FAILSAFE"),
        failure_bits=p.failure_bits,
        sensor_state=p.sensor_state,
        last_req_id=p.last_req_id,
        last_req_status=p.last_req_status,
        rc_age=p.rc_age,
    )
    # The existing mode panels read VehicleState: fill what the wire states directly.
    state = V.VehicleState(t=t, src=src, mode=mode, sim=sim, armed=armed)
    return [status, state]


def _pose(p: xp.Pose, t: int, src: str, meta: dict) -> list[Packet]:
    return [
        V.Attitude(t=t, src=src, omega=_fl(p.omega), q=_fl(p.q)),
        V.Position(t=t, src=src, pos=_fl(p.pos), vel=_fl(p.vel)),
    ]


def _air_pose(p: xp.AirPose, t: int, src: str, meta: dict) -> list[Packet]:
    return [V.Attitude(t=t, src=src, q=_fl(p.q)), V.Position(t=t, src=src, pos=_fl(p.pos), vel=_fl(p.vel))]


def _actuators(p: xp.Actuators, t: int, src: str, meta: dict) -> list[Packet]:
    return [
        V.Actuators(
            t=t, src=src, flaps=_fl(p.flaps), tvc=_fl(p.tvc), thrust=p.thrust, ground_distance=p.ground_distance
        ),
        V.FlapState(t=t, src=src, angle=_fl(p.flaps)),
    ]


def _air_actuators(p: xp.AirActuators, t: int, src: str, meta: dict) -> list[Packet]:
    """One AIR_ACTUATORS frame carries two samples, the previous one `prev_age` older. Both become an Actuators and a
    FlapState packet (the older pair first), so panels, plots and recordings see 50 samples/s. The frame's host
    receive time stamps the newest sample. NaN `prev_age` = no previous sample: only the newest is emitted."""
    out: list[Packet] = []
    prev_ok = math.isfinite(p.prev_age)
    for k in (1, 0) if prev_ok else (0,):
        ts = t - round(p.prev_age * 1e9) if k else t
        flaps = _fl(p.flaps[4 * k : 4 * k + 4])
        out.append(
            V.Actuators(
                t=ts,
                src=src,
                flaps=flaps,
                tvc=_fl(p.tvc[2 * k : 2 * k + 2]),
                thrust=float(p.thrust[k]),
                roll_torque=float(p.roll_torque[k]),
            )
        )
        out.append(V.FlapState(t=ts, src=src, angle=flaps))
    return out


def _air_mission(p: xp.AirMission, t: int, src: str, meta: dict) -> list[Packet]:
    t_mission = None if not math.isfinite(p.mission_time) else round(p.mission_time * 1e9)
    return [
        V.MissionState(
            t=t,
            src=src,
            sp_vel=_fl(p.setpoint_vel),
            sp_pos=_fl(p.setpoint_pos),
            mission_mode=_enum(V.MissionMode, p.mission_mode),
            mission_phase=_enum(V.MissionPhase, p.phase),
            t_mission=t_mission,
            mission_index=p.index,
            waypoint=None if p.waypoint == 255 else p.waypoint,
        )
    ]


def _mission(p: xp.Mission, t: int, src: str, meta: dict) -> list[Packet]:
    t_mission = None if not math.isfinite(p.mission_time) else round(p.mission_time * 1e9)
    return [
        V.MissionState(
            t=t,
            src=src,
            sp_vel=_fl(p.setpoint_vel),
            sp_pos=_fl(p.setpoint_pos),
            mission_phase=_enum(V.MissionPhase, p.phase),
            t_mission=t_mission,
            mission_index=p.index,
        )
    ]


def _gnss(p: xp.Gnss, t: int, src: str, meta: dict) -> list[Packet]:
    return [
        V.Gps(
            t=t,
            src=src,
            lat=p.latitude,
            lon=p.longitude,
            alt=p.altitude,
            vel=_fl(p.vel),
            num_sats=p.satellites,
            fix_type=p.fix_type,
            pos_acc=p.h_acc,
            alt_acc=p.v_acc,
            vel_acc=p.s_acc,
        )
    ]


def _handset_telem(p: xp.HandsetTelem, t: int, src: str, meta: dict) -> list[Packet]:
    H = xp.HandsetTelem
    return [
        V.HandsetTelem(
            t=t,
            src=src,
            armed=_flag(p.flags, H, "FLAGS_ARMED"),
            failure=_flag(p.flags, H, "FLAGS_FAILURE"),
            simulation=_flag(p.flags, H, "FLAGS_SIMULATION"),
            gnss_fix=_flag(p.flags, H, "FLAGS_GNSS_FIX"),
            mode_text=p.mode_text,
            latitude=p.latitude,
            longitude=p.longitude,
            ground_speed=p.ground_speed,
            heading=p.heading,
            altitude=p.altitude,
            satellites=p.satellites,
            vertical_speed=p.vertical_speed,
            pitch=p.pitch,
            roll=p.roll,
            yaw=p.yaw,
            battery=p.battery,
        )
    ]


def _link_stats(p: xp.LinkStats, t: int, src: str, meta: dict) -> list[Packet] | str:
    node = meta["src"]
    if node not in (2, 3):
        return f"LINK_STATS from unexpected node {node}"
    fields: dict[str, Any] = {
        "module_uptime": p.uptime,
        "air_state": int(p.air_state),
        "rx_lq": p.rx_lq,
        "rssi": p.rssi,
        "snr": p.snr,
        "tx_power": p.tx_power,
        "rx_antenna": p.antenna & 0x0F,
        "tx_antenna": p.antenna >> 4,
        "air_rx_ok": p.air_rx_ok,
        "air_rx_bad": p.air_rx_bad,
        "frames_dropped": p.frames_dropped,
        "frames_expired": p.frames_expired,
        "queue_peak": p.queue_peak,
    }
    if node == 3:
        return [L.RxModuleStats(t=t, src=src, **fields)]
    return [
        L.TxModuleStats(t=t, src=src, **fields),
        L.RadioLinkStats(
            t=t, src=src, rssi_dbm=[float(p.rssi)], lq_pct=float(p.rx_lq), snr_db=float(p.snr), antenna=p.antenna & 0x0F
        ),
    ]


def _link_config(p: xp.LinkConfig, t: int, src: str, meta: dict) -> list[Packet]:
    return [L.LinkConfig(t=t, src=src, tx_power_dbm=p.tx_power_dbm, flags=p.flags, from_node=meta.get("src"))]


def _log_text(p: xp.LogText, t: int, src: str, meta: dict) -> list[Packet]:
    sev = p.severity.name if isinstance(p.severity, xp.Severity) else f"sev{p.severity}"
    return [ConsoleText(t=t, src=src, text=f"[node {meta['src']} {sev}] {p.text}")]


def _rc_state(p: xp.RcState, t: int, src: str, meta: dict) -> list[Packet]:
    origin = L.RcOrigin.gs if meta["src"] == 0 else L.RcOrigin.handset
    ch_us = [0 if c == xp.RcState.CH_INVALID else 1500 + math.floor(c / 2 + 0.5) for c in p.ch]
    return [
        L.RcState(
            t=t,
            src=src,
            origin=origin,
            ch_us=ch_us,
            seq=meta.get("seq"),
            flags=p.flags,
            source_ok=_flag(p.flags, xp.RcState, "FLAGS_SOURCE_OK"),
        )
    ]


def _request_ack(p: xp.RequestAck, t: int, src: str, meta: dict) -> list[Packet]:
    return [L.RequestAck(t=t, src=src, req_id=p.req_id, status=int(p.status), detail=p.detail)]


def _imu_raw(p: xp.ImuRaw, t: int, src: str, meta: dict) -> list[Packet]:
    return [
        V.ImuRaw(
            t=t,
            src=src,
            gyro=_fl(p.gyro),
            accel=_fl(p.accel),
            mag=_fl(p.mag),
            pressure=p.pressure,
            temperature=p.temperature,
        )
    ]


def _estimator(p: xp.Estimator, t: int, src: str, meta: dict) -> list[Packet]:
    return [V.Estimator(t=t, src=src, att_std=_fl(p.att_std), vel_std=_fl(p.vel_std), pos_std=_fl(p.pos_std))]


def _task_timing(p: xp.TaskTiming, t: int, src: str, meta: dict) -> list[Packet]:
    return [V.TaskTiming(t=t, src=src, count=p.count, avg=_fl(p.avg), max=_fl(p.max))]


def _request(p: xp.Request, t: int, src: str, meta: dict) -> list[Packet] | str:
    return "REQUEST is an uplink packet, not expected from a peer"


def _calibration(p: xp.Calibration, t: int, src: str, meta: dict) -> list[Packet]:
    return [
        V.Calibration(
            t=t,
            src=src,
            raw_frame=int(p.raw_frame),
            gyro_source=p.gyro_source,
            accel_source=p.accel_source,
            mag_source=p.mag_source,
            gyro_offset=_fl(p.gyro_offset),
            gyro_matrix=_fl(p.gyro_matrix),
            accel_offset=_fl(p.accel_offset),
            accel_matrix=_fl(p.accel_matrix),
            mag_offset=_fl(p.mag_offset),
            mag_matrix=_fl(p.mag_matrix),
            gyro_bias_est=_fl(p.gyro_bias_est),
        )
    ]


#: wire packet class -> mapper. A mapper returns the GS packets, or a string: why it is a RawFrame.
MAPPERS: dict[type, Callable[..., list[Packet] | str]] = {
    xp.VehicleStatus: _vehicle_status,
    xp.AirPose: _air_pose,
    xp.Pose: _pose,
    xp.Actuators: _actuators,
    xp.AirActuators: _air_actuators,
    xp.AirMission: _air_mission,
    xp.Mission: _mission,
    xp.Gnss: _gnss,
    xp.HandsetTelem: _handset_telem,
    xp.LinkStats: _link_stats,
    xp.LinkConfig: _link_config,
    xp.LogText: _log_text,
    xp.RcState: _rc_state,
    xp.RequestAck: _request_ack,
    xp.Request: _request,
    xp.ImuRaw: _imu_raw,
    xp.Calibration: _calibration,
    xp.Estimator: _estimator,
    xp.TaskTiming: _task_timing,
}

#: GS packet types the profile can produce (plus RawFrame, GsEvent, NetworkStatus, ConsoleText)
PRODUCED_TYPES = {
    "VehicleStatus",
    "VehicleState",
    "Attitude",
    "Position",
    "Actuators",
    "FlapState",
    "MissionState",
    "Gps",
    "HandsetTelem",
    "TxModuleStats",
    "RxModuleStats",
    "RadioLinkStats",
    "LinkConfig",
    "ConsoleText",
    "RcState",
    "RequestAck",
    "ImuRaw",
    "Calibration",
    "Estimator",
    "TaskTiming",
    "NetworkStatus",
    "RawFrame",
    "GsEvent",
}
