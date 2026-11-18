"""Payload layouts of the legacy telemetry structs (profile `provisional-1`).

Sources: flight-software/docs/libs/ExVectrPackets.md and project/hardware-and-drivers.md section 9.
All little-endian, packed. Fixed point: `value = raw * limit / 32767` (saturating on the vehicle,
INT16_MIN unpacks to -1.00003 * limit; kept as is). Known quirks of the legacy vehicle are
reproduced, not corrected (see docs/wire-profiles.md):

- GPS lat/lon are radians x 1e7 on the wire (the struct documents degrees) and are decoded as radians.
- Position h_acc / v_acc carry covariance / 1000 (no physical unit). GPS accuracies carry variances (m^2).
- Attitude accuracies are always 0 on the vehicle.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from ...packets.defs.link import RcOrigin
from ...packets.defs.vehicle import MissionMode, MissionPhase, SensorState, VehicleMode

I16 = 32767

# Channels (transport ports of the legacy vehicle)
PORT_ATTITUDE = 10
PORT_POSITION = 11
PORT_GPS = 12
PORT_VEHICLE_STATE = 50
PORT_MISSION_STATE = 51
PORT_RC_STATE = 0x10  # RC_STATE port from docs/network-design.md. Provisional payload layout below.

RC_CHANNELS = 16


def fx(raw: int, limit: float) -> float:
    return raw * limit / I16


def _enum(cls: type[IntEnum], v: int) -> int:
    """Known value -> enum member, unknown value kept as a plain int (shown as #n, never an error)."""
    try:
        return cls(v)
    except ValueError:
        return v


@dataclass(frozen=True)
class Layout:
    channel: int
    type_name: str
    size: int
    decode: Callable[[bytes], dict[str, Any]]


_ATT = struct.Struct("<3h4h2h")
_POS = struct.Struct("<3h3h2h")
_GPS = struct.Struct("<iihhhhBhhh")
_VS = struct.Struct("<15B4hhh")
_MS = struct.Struct("<6fBqBB")

_OMEGA_LIM = math.radians(1000.0)
_ANGLE_LIM = math.radians(360.0)


def _attitude(b: bytes) -> dict[str, Any]:
    v = _ATT.unpack(b)
    return {
        "omega": [fx(x, _OMEGA_LIM) for x in v[0:3]],
        "q": [fx(x, 1.0) for x in v[3:7]],
        "tilt_acc": fx(v[7], _ANGLE_LIM),
        "north_acc": fx(v[8], _ANGLE_LIM),
    }


def _position(b: bytes) -> dict[str, Any]:
    v = _POS.unpack(b)
    return {
        "pos": [fx(x, 1000.0) for x in v[0:3]],
        "vel": [fx(x, 100.0) for x in v[3:6]],
        "h_acc": fx(v[6], 10.0),
        "v_acc": fx(v[7], 10.0),
    }


def _gps(b: bytes) -> dict[str, Any]:
    lat, lon, alt, vx, vy, vz, sats, pa, aa, va = _GPS.unpack(b)
    return {
        "lat": lat / 1e7,  # radians (legacy quirk)
        "lon": lon / 1e7,
        "alt": fx(alt, 1000.0),
        "vel": [fx(vx, 100.0), fx(vy, 100.0), fx(vz, 100.0)],
        "num_sats": sats,
        "pos_acc": fx(pa, 10.0),
        "alt_acc": fx(aa, 10.0),
        "vel_acc": fx(va, 10.0),
    }


def _vehicle_state(b: bytes) -> dict[str, Any]:
    v = _VS.unpack(b)
    return {
        "mode": _enum(VehicleMode, v[0]),
        "sensor_gnss": _enum(SensorState, v[1]),
        "sensor_imu": _enum(SensorState, v[2]),
        "sensor_baro": _enum(SensorState, v[3]),
        "sensor_mag": _enum(SensorState, v[4]),
        "sensor_att_kf": _enum(SensorState, v[5]),
        "sensor_pos_kf": _enum(SensorState, v[6]),
        "fail_sensor": bool(v[7]),
        "fail_attitude": bool(v[8]),
        "fail_position": bool(v[9]),
        "fail_pos_oob": bool(v[10]),
        "fail_att_oob": bool(v[11]),
        "fail_radio_loss": bool(v[12]),
        "sim": bool(v[13]),
        "armed": bool(v[14]),
        "tvc_thrust": [fx(x, 50.0) for x in v[15:18]],
        "tvc_torque": fx(v[18], 50.0),
        "ground_dist": fx(v[19], 2.0),
        "ground_dist_cov": fx(v[20], 1.0),
    }


def _mission_state(b: bytes) -> dict[str, Any]:
    v = _MS.unpack(b)
    return {
        "sp_vel": [float(x) for x in v[0:3]],
        "sp_pos": [float(x) for x in v[3:6]],
        "mission_mode": _enum(MissionMode, v[6]),
        "t_mission": v[7],
        "mission_index": v[8],
        "mission_phase": _enum(MissionPhase, v[9]),
    }


# RC_STATE (provisional): seq u8 | 16 x u16 channel pulse width in microseconds.
_RC = struct.Struct("<B16H")


def _rc_state(b: bytes) -> dict[str, Any]:
    v = _RC.unpack(b)
    # Received RC_STATE is the handset state mirrored by a TX module, never the GS's own.
    return {"origin": RcOrigin.handset, "seq": v[0], "ch_us": list(v[1:])}


def encode_rc_state(seq: int, ch_us: list[int]) -> bytes:
    """Raises ValueError for a malformed vector. Channels are clamped to the wire range 0..65535."""
    if len(ch_us) != RC_CHANNELS:
        raise ValueError(f"RcState needs {RC_CHANNELS} channels, got {len(ch_us)}")
    return _RC.pack(seq & 0xFF, *(min(max(int(c), 0), 0xFFFF) for c in ch_us))


LAYOUTS: dict[int, Layout] = {
    lay.channel: lay
    for lay in (
        Layout(PORT_ATTITUDE, "Attitude", _ATT.size, _attitude),
        Layout(PORT_POSITION, "Position", _POS.size, _position),
        Layout(PORT_GPS, "Gps", _GPS.size, _gps),
        Layout(PORT_VEHICLE_STATE, "VehicleState", _VS.size, _vehicle_state),
        Layout(PORT_MISSION_STATE, "MissionState", _MS.size, _mission_state),
        Layout(PORT_RC_STATE, "RcState", _RC.size, _rc_state),
    )
}
