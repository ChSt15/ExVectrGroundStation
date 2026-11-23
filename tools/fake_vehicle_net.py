"""Independent `exvectrnet-1` encoder and decoder for the fake vehicle (docs/network-design.md section 4).

Written from the wire spec and the packet reference tables (`ExVectrPackets/docs/packet-reference.md`),
with `struct`, and deliberately *not* using `exvectr_gs.wire.exvectrnet_1`, `provisional_1` or the
generated `exvectr_packets` encoders, so that the profile is checked against a second implementation.
Only the schema hash value comes from `exvectr_packets`.
"""

from __future__ import annotations

import math
import struct

PORT_LINK_STATS, PORT_LOG_TEXT, PORT_HANDSET, PORT_RC_STATE = 0x01, 0x02, 0x03, 0x10
PORT_LINK_CONFIG, PORT_TELECOMMAND = 0x04, 0x7F  # the latter is the TEMPORARY legacy telecommand port
PORT_REQUEST, PORT_REQUEST_ACK, PORT_CMD_STATE = 0x11, 0x12, 0x13
PORT_VEHICLE_STATUS, PORT_AIR_POSE, PORT_AIR_ACTUATORS, PORT_AIR_MISSION = 0x20, 0x21, 0x22, 0x23
PORT_POSE, PORT_ACTUATORS, PORT_MISSION, PORT_GNSS = 0x80, 0x81, 0x82, 0x83
PORT_AIR_GNSS, PORT_AIR_GNSS_INFO, PORT_HOME_POSITION = 0x24, 0x25, 0x50
PORT_CMD_APPLIED = 0x84
PORT_IMU, PORT_ESTIMATOR, PORT_TIMING, PORT_CALIBRATION = 0x90, 0x91, 0x92, 0x93
QOS = {
    PORT_RC_STATE: 0, PORT_CMD_STATE: 0, PORT_VEHICLE_STATUS: 1, PORT_HANDSET: 1, PORT_LINK_STATS: 1, PORT_LOG_TEXT: 2,
}  # fmt: skip
# everything else is telemetry state
LATEST = {PORT_RC_STATE, PORT_CMD_STATE, PORT_VEHICLE_STATUS, PORT_HANDSET, PORT_LINK_STATS}


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while True:
        j = i
        while j < len(data) and j - i < 254 and data[j] != 0:
            j += 1
        out.append(j - i + 1)
        out += data[i:j]
        if j - i == 254:  # full block, no zero consumed
            i = j
            if i == len(data):  # python `cobs`: a trailing empty block follows a full last block
                out.append(1)
                return bytes(out)
            continue
        if j == len(data):
            return bytes(out)
        i = j + 1
        if i == len(data):
            out.append(1)
            return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        code = data[i]
        if code == 0 or i + code > len(data):
            raise ValueError("bad COBS")
        out += data[i + 1 : i + code]
        i += code
        if code < 255 and i < len(data):
            out.append(0)
    return bytes(out)


def stream_frame(net_frame: bytes) -> bytes:
    dec = b"\x01" + net_frame
    dec += struct.pack("<H", crc16(dec))
    return cobs_encode(dec) + b"\x00"


def net_data(src: int, dst: int, port: int, seq: int, payload: bytes, ttl: int = 3) -> bytes:
    ctrl = (1 << 6) | (0 << 4) | (QOS.get(port, 3) << 2) | ((port in LATEST) << 1)
    return bytes([ctrl, ttl, dst, src, port, seq & 0xFF]) + payload


def net_heartbeat(src: int, role: int, schema_hash: int, uptime_ms: int, net_version: int = 1) -> bytes:
    hdr = bytes([0x56, 3, 0xFF, src])
    return hdr + struct.pack("<BBIIB", role, net_version, schema_hash, uptime_ms & 0xFFFFFFFF, 0)


def parse_stream(decoded: bytes) -> dict | None:
    """Decoded stream frame (CRC and SKIND checked) to a dict, or None if bad. DATA and HEARTBEAT."""
    if len(decoded) < 7 or decoded[0] != 1:
        return None
    if struct.unpack_from("<H", decoded, len(decoded) - 2)[0] != crc16(decoded[:-2]):
        return None
    f = decoded[1:-2]
    ctrl, ttl, dst, src = f[0], f[1], f[2], f[3]
    kind = (ctrl >> 4) & 3
    if kind == 0 and len(f) >= 6:
        pl = bytes(f[6:])
        return {
            "type": "data",
            "src": src,
            "dst": dst,
            "ttl": ttl,
            "ctrl": ctrl,
            "port": f[4],
            "seq": f[5],
            "payload": pl,
        }
    if kind == 1:
        return {"type": "heartbeat", "src": src, "dst": dst, "ttl": ttl, "ctrl": ctrl, "payload": bytes(f[4:])}
    return None


# ---- payload packers (offsets and scalings from packet-reference.md). `f` dicts are Scenario fields.


def _fx(v: float, scale: float, lo: int, hi: int, invalid: int) -> int:
    if not math.isfinite(v):
        return invalid
    x = v / scale
    r = math.floor(abs(x) + 0.5) * (1 if x >= 0 else -1)
    return max(lo, min(hi, r))


def i16(v: float, scale: float) -> int:
    return _fx(v, scale, -32767, 32767, -32768)


def pack_pose(t_us: int, a: dict, p: dict) -> bytes:
    return struct.pack("<I3f4f3f3f", t_us, *a["omega"], *a["q"], *p["vel"], *p["pos"])


def pack_air_pose(t_us: int, a: dict, p: dict) -> bytes:
    q = [i16(x, 1 / 32767) for x in a["q"]]
    pos = [round(x * 1000) for x in p["pos"]]  # i32, 1 mm
    vel = [i16(x, 0.01) for x in p["vel"]]
    return struct.pack("<I4h3i3h", t_us, *q, *pos, *vel)


def pack_actuators(
    t_us: int, flaps: list[float], tvc: list[float], thrust: float, ground: float, roll_torque: float | None = None
) -> bytes:
    """ACTUATORS (40 B). `roll_torque` None = older 36 B sender (field absent)."""
    pl = struct.pack("<I4f2fff", t_us, *flaps, *tvc, thrust, ground)
    return pl if roll_torque is None else pl + struct.pack("<f", roll_torque)


def u16(v: float, scale: float) -> int:
    return _fx(v, scale, 0, 65534, 65535)


def pack_air_actuators(t_us: int, prev_age: float, new: dict, old: dict | None) -> bytes:
    """AIR_ACTUATORS (38 B): two samples, newest then previous. Each sample dict: tvc[2], roll_torque, thrust,
    flaps[4]. `old` None = no previous sample (prev_age and all its values are the invalid sentinels)."""
    nan = float("nan")
    age = prev_age if old is not None else nan
    old = old or {"tvc": [nan, nan], "roll_torque": nan, "thrust": nan, "flaps": [nan] * 4}
    return struct.pack(
        "<IH4h2h2H8h",
        t_us,
        u16(age, 1e-6),
        *(i16(x, 1e-4) for x in (*new["tvc"], *old["tvc"])),
        i16(new["roll_torque"], 1e-5),
        i16(old["roll_torque"], 1e-5),
        u16(new["thrust"], 1e-3),
        u16(old["thrust"], 1e-3),
        *(i16(x, 1e-4) for x in (*new["flaps"], *old["flaps"])),
    )


def _sp_q(m: dict) -> list[float]:
    return list(m.get("sp_q") or [0.0, 0.0, 0.0, 0.0])  # all zero = no attitude setpoint


def pack_air_mission(t_us: int, m: dict, waypoint: int = 255) -> bytes:
    ms = _fx(m["t_mission"] / 1e9, 0.001, -(2**31) + 1, 2**31 - 1, -(2**31))
    pos = [_fx(x, 0.001, -(2**31) + 1, 2**31 - 1, -(2**31)) for x in m["sp_pos"]]
    vel = [i16(x, 0.01) for x in m["sp_vel"]]
    return struct.pack(
        "<IiBBBB3i3h4h2h",
        t_us,
        ms,
        int(m["mission_mode"]),
        int(m["mission_phase"]),
        m["mission_index"],
        waypoint,
        *pos,
        *vel,
        *(i16(x, 1 / 32768) for x in _sp_q(m)),
        *(_fx(m.get(k, math.nan), 0.1, -32767, 32767, -32768) for k in ("flip_start_altitude", "stop_altitude")),
    )


def pack_mission(t_us: int, m: dict, with_mode: bool = True) -> bytes:
    """MISSION (60 B): mission_mode and waypoint (255 = not the waypoint mission) are appended; `with_mode` False =
    older 50 B sender."""
    ms = _fx(m["t_mission"] / 1e9, 0.001, -(2**31) + 1, 2**31 - 1, -(2**31))
    pl = struct.pack(
        "<I3f3fiBB4f", t_us, *m["sp_pos"], *m["sp_vel"], ms, m["mission_index"], int(m["mission_phase"]), *_sp_q(m)
    )
    wp = m.get("waypoint")
    if not with_mode:
        return pl
    pl += bytes([int(m["mission_mode"]), 255 if wp is None else wp])
    return pl + struct.pack("<2f", m.get("flip_start_altitude", math.nan), m.get("stop_altitude", math.nan))


def pack_gnss(t_us: int, g: dict, fix_type: int = 3) -> bytes:
    return struct.pack(
        "<Iddf3fBBfff",
        t_us,
        g["lat"],
        g["lon"],
        g["alt"],
        *g["vel"],
        g["num_sats"],
        fix_type,
        g["pos_acc"],
        g["alt_acc"],
        g["vel_acc"],
    )


def pack_air_gnss(t_us: int, g: dict) -> bytes:
    """AIR_GNSS (22 B): lat/lon i32 at 1e-7 deg, altitude i32 mm, NWU velocity i16 at 0.01 m/s; NaN = sentinel."""
    ll = [_fx(math.degrees(g[k]) * 1e7 if math.isfinite(g[k]) else math.nan, 1.0, -(2**31) + 1, 2**31 - 1, -(2**31))
          for k in ("lat", "lon")]  # fmt: skip
    alt = _fx(g["alt"], 0.001, -(2**31) + 1, 2**31 - 1, -(2**31))
    return struct.pack("<I3i3h", t_us, *ll, alt, *(i16(x, 0.01) for x in g["vel"]))


def _u16_cm(v: float) -> int:
    return _fx(v, 0.01, 0, 65534, 65535)


def _u16_ms(v: float) -> int:
    return _fx(v, 0.001, 0, 65534, 65535)


def pack_air_gnss_info(t_us: int, g: dict) -> bytes:
    """AIR_GNSS_INFO (10 B): fix type, satellites, h_acc and v_acc u16 at 1 cm (65535 = unknown)."""
    return struct.pack("<IBBHH", t_us, g["fix_type"], g["num_sats"], _u16_cm(g["pos_acc"]), _u16_cm(g["alt_acc"]))


def pack_home_position(h: dict) -> bytes:
    """HOME_POSITION (25 B), from the packet table of the ExVectrPackets reference, independent of the generator."""
    flags = (
        (1 if h["latlon_valid"] else 0)
        | (2 if h["alt_valid"] else 0)
        | (4 if h["simulation"] else 0)
        | (8 if h["zeroing"] else 0)
    )
    lat = _fx(math.degrees(h["lat"]) * 1e7 if h["latlon_valid"] else math.nan, 1.0, -(2**31) + 1, 2**31 - 1, -(2**31))
    lon = _fx(math.degrees(h["lon"]) * 1e7 if h["latlon_valid"] else math.nan, 1.0, -(2**31) + 1, 2**31 - 1, -(2**31))
    alt = _fx(h["alt"] if h["alt_valid"] else math.nan, 0.001, -(2**31) + 1, 2**31 - 1, -(2**31))
    return struct.pack("<BiiiHBqB", flags, lat, lon, alt, _u16_cm(h["h_acc"]), h["epoch"], h["set_time"], 0)


def pack_vehicle_status(
    t_ns: int,
    vs: dict,
    mission_mode: int,
    mission_phase: int,
    flags_extra: int = 0,
    home_epoch: int | None = None,
    calib_state: int | None = None,
    ctrl: tuple[int, int, int, int, int] | None = None,
    cmd_session: int | None = None,
) -> bytes:
    flags = (
        (1 if vs["armed"] else 0)
        | (2 if vs["sim"] else 0)
        | (4 if vs["armed"] else 0)
        | 8
        | (64 if vs.get("uart_peer_seen") else 0)
        | flags_extra
    )
    t_us = (t_ns // 1000) & 0xFFFFFFFF
    pl = struct.pack(
        "<IqBBBBBIHHBH",
        t_us,
        t_ns,
        int(vs["mode"]),
        int(mission_mode),
        int(mission_phase),
        2,
        flags,
        0,
        0xAAA,
        7,
        0,
        40,
    )
    # home_epoch then calib_state are appended (u8 each); absent = older sender (calib_state needs home_epoch)
    if home_epoch is None:
        return pl
    pl += bytes([home_epoch]) + (b"" if calib_state is None else bytes([calib_state]))
    if ctrl is None or calib_state is None:
        return pl
    # T9b tail: ctrl_flags u16, go_refusal u8, degraded_bits u8, mission_profile u8 (255 none), rc_rejected u8
    pl += struct.pack("<HBBBB", *ctrl)
    # GS-control tail: cmd_session u16 (low 16 bits of the latched CMD_STATE session, 0 none); absent = older sender
    return pl if cmd_session is None else pl + struct.pack("<H", cmd_session & 0xFFFF)


def pack_cmd_applied(
    t_us: int, go: int, profile: int, vehicle_mode: int, motors: int, reset_button: int, failsafe: int, flags: int,
    safe_seen: int, session: int, age_s: float, usb_age_s: float, uart_age_s: float, rejects: tuple[int, int, int, int],
) -> bytes:  # fmt: skip
    """CMD_APPLIED (26 B, port 0x84, USB only), from the packet table of the ExVectrPackets reference."""
    return struct.pack(
        "<IBBBBBBBBIHHH4B", t_us, go, profile, vehicle_mode, motors, reset_button, failsafe, flags, safe_seen, session,
        _u16_ms(age_s), _u16_ms(usb_age_s), _u16_ms(uart_age_s), *rejects,
    )  # fmt: skip


def pack_cmd_state(
    session: int, go: int = 0, profile: int = 2, vehicle_mode: int = 0, motors: int = 0, reset_button: int = 0,
    failsafe: int = 0, flags: int = 1, version: int = 1, reserved: int = 0,
) -> bytes:  # fmt: skip
    """CMD_STATE (14 B, port 0x13). No range checks: tests build bad frames with it."""
    return struct.pack("<BBIBBBBBBH", flags, version, session, go, profile, vehicle_mode, motors, reset_button,
                       failsafe, reserved)  # fmt: skip


def parse_cmd_state(payload: bytes) -> dict | None:
    """The vehicle's strict view of CMD_STATE: None (frame rejected) unless exactly 14 bytes and every field in range
    (flags bit 0 set and only bits 0..2, version 1, session != 0, enums, reserved 0)."""
    if len(payload) != 14:
        return None
    flags, version, session, go, profile, mode, motors, reset, failsafe, reserved = struct.unpack(
        "<BBIBBBBBBH", payload
    )
    if (flags & 1) == 0 or flags > 7 or version != 1 or session == 0 or reserved != 0:
        return None
    if go > 1 or profile > 2 or mode > 1 or motors > 1 or reset > 1 or failsafe > 1:
        return None
    return {
        "pocket_live": bool(flags & 2), "input_virtual": bool(flags & 4), "session": session, "go": go,
        "profile": profile, "vehicle_mode": mode, "motors": motors, "reset_button": reset, "failsafe": failsafe,
    }  # fmt: skip


def pack_handset(vs: dict, g: dict, a: dict, mode_text: str) -> bytes:
    flags = (1 if vs["armed"] else 0) | (4 if vs["sim"] else 0) | 8
    roll, pitch, yaw = _euler(a["q"])
    return struct.pack(
        "<B8siiHHhBhhhhH",
        flags,
        mode_text.encode("ascii")[:8].ljust(8, b"\0"),
        round(math.degrees(g["lat"]) * 1e7),
        round(math.degrees(g["lon"]) * 1e7),
        round(math.hypot(g["vel"][0], g["vel"][1]) * 100),
        round(math.degrees(yaw) % 360 * 100),
        round(g["alt"]),
        g["num_sats"],
        round(g["vel"][2] * 100),
        round(pitch * 1e4),
        round(roll * 1e4),
        round(yaw * 1e4),
        0,
    )


def _euler(q: list[float]) -> tuple[float, float, float]:
    w, x, y, z = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


def pack_link_stats(
    uptime_ms: int, air_state: int, lq: int, rssi: int, snr: int, ants: int, ok: int, tx_power: int = 10
) -> bytes:
    return struct.pack("<IBBbbbBHHHHB", uptime_ms, air_state, lq, rssi, snr, tx_power, ants, ok & 0xFFFF, 2, 0, 1, 15)


def pack_link_config(tx_power_dbm: int, flags: int = 0) -> bytes:
    return struct.pack("<bB", tx_power_dbm, flags)


def parse_telecommand(payload: bytes) -> dict:
    """The legacy 8-byte Telecommand, little-endian (independent of the ground station's encoder)."""
    if len(payload) != 8:
        raise ValueError(f"telecommand length {len(payload)}")
    typ, sub, param_int, param_float = struct.unpack("<BBhf", payload)
    return {"type": typ, "subsystem": sub, "param_int": param_int, "param_float": param_float}


def pack_rc_state(flags: int, ch: list[int]) -> bytes:
    return struct.pack("<BB16h", flags, 0, *ch)


def pack_request_ack(req_id: int, status: int, detail: int = 0) -> bytes:
    return struct.pack("<HBB", req_id, status, detail)


def parse_request(payload: bytes) -> dict:
    req_id, action, target, arg_i, arg_f = struct.unpack("<HBBif", payload[:12])
    session = struct.unpack("<I", payload[12:16])[0] if len(payload) >= 16 else 0  # 0 = field absent (older sender)
    return {"req_id": req_id, "action": action, "target": target, "arg_i": arg_i, "arg_f": arg_f, "session": session}


def pack_calibration(t_us: int, gyro_source=7, accel_source=3, mag_source=3) -> bytes:
    """CALIBRATION (164 B), bench-like values: gyro and accel diag(-1,1,-1) (accel scaled), mag a permutation."""
    d = lambda a, b, c: [a, 0.0, 0.0, 0.0, b, 0.0, 0.0, 0.0, c]  # noqa: E731
    vals = [
        0.01606, 0.03491, 0.01047, *d(-1.0, 1.0, -1.0),
        0.42849, 0.55833, 2.14256, *d(-0.9984, 1.003, -0.9673),
        1.0491e-6, -1.467e-7, 8.154e-7, *(0.1622 * x for x in (0, 0, -1, 1, 0, 0, 0, -1, 0)),
        0.001, 0.0, -0.002,
    ]  # fmt: skip
    return struct.pack("<IBBBB39f", t_us, 0, gyro_source, accel_source, mag_source, *vals)


def pack_imu(t_us: int, t: float) -> bytes:
    return struct.pack(
        "<I3f3f3ff f", t_us, 0.01 * math.sin(t), 0.02, -0.01, 0.0, 0.1, 9.81, 2e-5, 0.0, 4e-5, 101325.0 - 10 * t, 293.15
    )  # fmt: skip


def pack_estimator(t_us: int) -> bytes:
    return struct.pack("<I9f", t_us, 0.01, 0.01, 0.02, 0.1, 0.1, 0.1, 0.5, 0.5, 0.8)


def pack_timing(t_us: int) -> bytes:
    avg = [100 + 10 * i for i in range(3)] + [65535] * 5  # microseconds, 65535 = invalid
    mx = [200 + 10 * i for i in range(3)] + [65535] * 5
    return struct.pack("<IB8H8H", t_us, 3, *avg, *mx)
