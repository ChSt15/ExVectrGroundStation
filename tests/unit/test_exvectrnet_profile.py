"""exvectrnet-1 profile: mapping of every wire packet, heartbeats, duplicate suppression, uplink bytes."""

import itertools
import math
import struct

import exvectr_packets as xp
import pytest

from exvectr_gs.packets import REGISTRY
from exvectr_gs.packets.defs import link as L
from exvectr_gs.packets.defs import vehicle as V
from exvectr_gs.packets.defs.host import GsEventKind
from exvectr_gs.packets.textcodec import decode_line, encode_line
from exvectr_gs.wire import load_profile
from exvectr_gs.wire.exvectrnet_1 import framing as fr
from exvectr_gs.wire.profile import Frame, decode_frame

from ..tests_tools import net
from ..vectors import packet_vectors, wire_vectors

S = 1_000_000_000
PROFILE = load_profile("exvectrnet-1")


@pytest.fixture(autouse=True)
def _fresh_profile():
    """Profiles are stateful (presence, sequence numbers): every test gets its own."""
    global PROFILE
    PROFILE = load_profile("exvectrnet-1")


_seq = itertools.count()


def frame(port, payload, src=1, seq=None, t=S):
    """A DATA frame. Without `seq` each call takes the next number, so frames are never duplicates."""
    seq = next(_seq) % 256 if seq is None else seq
    return Frame(channel=port, payload=payload, t=t, meta={"src": src, "dst": 0, "seq": seq, "type": 0})


def hb_frame(node=1, role=None, version=1, schema=None, t=S):
    schema = xp.SCHEMA_HASH if schema is None else schema
    pl = xp.Heartbeat(role=node if role is None else role, net_version=version, schema_hash=schema, uptime=5.0).encode()
    return Frame(channel=fr.HEARTBEAT_CHANNEL, payload=pl, t=t, meta={"src": node, "dst": 255, "type": 1})


def by_type(pkts):
    return {p.TYPE: p for p in pkts}


# ---- every wire packet with a port, using the shared packet vectors

EXPECTED = {
    "VEHICLE_STATUS": {"VehicleStatus", "VehicleState"},
    "AIR_POSE": {"Attitude", "Position"},
    "AIR_ACTUATORS": {"Actuators", "FlapState"},
    "AIR_MISSION": {"MissionState"},
    "POSE": {"Attitude", "Position"},
    "ACTUATORS": {"Actuators", "FlapState"},
    "MISSION": {"MissionState"},
    "GNSS": {"Gps"},
    "AIR_GNSS": {"Gps"},
    "HOME_POSITION": {"HomePosition"},
    "MISSION_PARAMS": {"MissionParams"},
    "HANDSET_TELEM": {"HandsetTelem"},
    "LINK_STATS": {"TxModuleStats", "RadioLinkStats"},
    "LINK_CONFIG": {"LinkConfig"},
    "LOG_TEXT": {"ConsoleText"},
    "RC_STATE": {"RcState"},
    "CMD_APPLIED": {"CmdApplied"},
    "REQUEST_ACK": {"RequestAck"},
    "IMU_RAW": {"ImuRaw"},
    "CALIBRATION": {"Calibration"},
    "ESTIMATOR": {"Estimator"},
    "TASK_TIMING": {"TaskTiming"},
}
PORT_CASES = [c for c in packet_vectors()["cases"] if c["packet"] in EXPECTED]


@pytest.mark.parametrize("case", PORT_CASES, ids=lambda c: c["name"])
def test_every_vector_maps_to_gs_types_and_round_trips_the_text_codec(case):
    port = xp.PACKETS_BY_NAME[case["packet"]].PORT
    pkts = PROFILE.decode_many(frame(port, bytes.fromhex(case["hex"]), src=2), "usb")
    assert {p.TYPE for p in pkts} == EXPECTED[case["packet"]]
    for p in pkts:
        assert REGISTRY.get(p.TYPE) and p.src == "usb" and p.t <= S  # AIR_ACTUATORS: older sample
        assert p.t == S or case["packet"] == "AIR_ACTUATORS"
        assert decode_line(encode_line(p)) == p  # logs stay readable


def test_all_ports_of_the_generated_module_are_mapped():
    # REQUEST and CMD_STATE are uplink packets, deliberately RawFrames; AIR_GNSS_INFO is merged into the next
    # AIR_GNSS (see its test)
    mapped = {xp.PACKETS_BY_NAME[n].PORT for n in EXPECTED} | {0x11, 0x13, 0x25}  # CMD_STATE: uplink only
    assert set(xp.PACKETS_BY_PORT) == mapped


def test_supported_types_are_registered_and_cover_the_mapping():
    assert PROFILE.supported_types() <= {t.name for t in REGISTRY.all()}
    produced = {t for ts in EXPECTED.values() for t in ts}
    assert produced <= PROFILE.supported_types()


def case_payload(name):
    c = next(c for c in packet_vectors()["cases"] if c["name"] == name)
    return c["values"], bytes.fromhex(c["hex"])


def test_vehicle_status_values_and_flags():
    vals, pl = case_payload("vehicle_status")
    d = by_type(PROFILE.decode_many(frame(0x20, pl), "usb"))
    vs, st = d["VehicleStatus"], d["VehicleState"]
    X = xp.VehicleStatus
    assert vs.vehicle_time == vals["t_ns"] and vs.failure_bits == vals["failure_bits"]
    assert vs.armed == bool(vals["flags"] & X.FLAGS_ARMED) and vs.simulation == bool(vals["flags"] & X.FLAGS_SIMULATION)
    assert vs.motors_enabled == bool(vals["flags"] & X.FLAGS_MOTORS_ENABLED)
    assert vs.failsafe == bool(vals["flags"] & X.FLAGS_FAILSAFE) and vs.incompatible == bool(
        vals["flags"] & X.FLAGS_INCOMPATIBLE
    )
    assert vs.gs_present == bool(vals["flags"] & X.FLAGS_GS_PRESENT)
    assert vs.uart_peer_seen == bool(vals["flags"] & X.FLAGS_UART_PEER_SEEN)
    assert int(vs.ctrl_source) == int(vals["ctrl_source"]) and vs.rc_age == pytest.approx(vals["rc_age"])
    assert (st.mode, st.armed, st.sim) == (vs.mode, vs.armed, vs.simulation) and st.sensor_gnss is None
    _, pl = case_payload("vehicle_status_no_rc")
    assert math.isnan(by_type(PROFILE.decode_many(frame(0x20, pl), "usb"))["VehicleStatus"].rc_age)


def test_uart_peer_seen_flag_is_bit_6_and_information_only():
    assert xp.VehicleStatus.FLAGS_UART_PEER_SEEN == 1 << 6
    for on in (True, False):
        pl = xp.VehicleStatus(flags=xp.VehicleStatus.FLAGS_UART_PEER_SEEN if on else 0).encode()
        d = by_type(PROFILE.decode_many(frame(0x20, pl), "usb"))
        assert d["VehicleStatus"].uart_peer_seen is on
        assert d["VehicleStatus"].armed is False and d["VehicleStatus"].motors_enabled is False  # nothing else follows


def test_zeroing_flag_is_bit_7_of_the_generated_constant():
    X = xp.VehicleStatus
    assert X.FLAGS_ZEROING == 1 << 7
    for on in (True, False):
        pl = X(flags=X.FLAGS_ZEROING if on else 0).encode()
        vs = by_type(PROFILE.decode_many(frame(0x20, pl), "usb"))["VehicleStatus"]
        assert vs.zeroing is on and vs.armed is False and vs.simulation is False  # nothing else follows


def _air_actuator_samples(name):
    vals, pl = case_payload(name)
    pkts = PROFILE.decode_many(frame(0x22, pl, t=S), "radio")
    return vals, [p for p in pkts if p.TYPE == "Actuators"], [p for p in pkts if p.TYPE == "FlapState"]


def test_air_actuators_maps_to_two_samples_with_their_own_timestamps():
    vals, acts, flaps = _air_actuator_samples("air_actuators")
    dec = next(c for c in packet_vectors()["cases"] if c["name"] == "air_actuators")["decoded"]
    assert [a.t for a in acts] == [S - 20_000_000, S] and [f.t for f in flaps] == [S - 20_000_000, S]  # older first
    old, new = acts
    assert new.tvc == pytest.approx(dec["tvc"][:2]) and old.tvc == pytest.approx(dec["tvc"][2:])
    assert new.flaps == pytest.approx(dec["flaps"][:4]) and old.flaps == pytest.approx(dec["flaps"][4:])
    assert (new.thrust, old.thrust) == pytest.approx((12.5, 0.0))
    assert (new.roll_torque, old.roll_torque) == pytest.approx(tuple(dec["roll_torque"]))
    assert new.ground_distance is None and flaps[1].angle == new.flaps and flaps[0].cmd is None
    assert new.src == "radio" and new.t - old.t == round(vals["prev_age"] * 1e9)
    for p in acts + flaps:
        assert decode_line(encode_line(p)).t == p.t  # logs stay readable


def test_independent_fake_vehicle_encoder_matches_the_air_vectors():
    v, pl = case_payload("air_actuators")

    def s(i):
        return {
            "tvc": v["tvc"][2 * i : 2 * i + 2],
            "roll_torque": v["roll_torque"][i],
            "thrust": v["thrust"][i],
            "flaps": v["flaps"][4 * i : 4 * i + 4],
        }

    assert net.pack_air_actuators(v["t_us"], v["prev_age"], s(0), s(1)) == pl
    v, pl = case_payload("air_actuators_no_previous")
    new = {"tvc": [0.0, 0.0], "roll_torque": 0.0, "thrust": 1.0, "flaps": [0.5] * 4}
    assert net.pack_air_actuators(1, 0.02, new, None) == pl
    v, pl = case_payload("air_mission")
    m = {"t_mission": -9.5e9, "mission_mode": 1, "mission_phase": 1, "mission_index": 1}
    m |= {"sp_pos": v["setpoint_pos"], "sp_vel": v["setpoint_vel"]}
    m |= {"flip_start_altitude": v["flip_start_altitude"], "stop_altitude": v["stop_altitude"]}
    assert net.pack_air_mission(77, m) == pl
    assert net.pack_air_mission(77, m | {"sp_q": [0.5, -0.5, 0.5, -0.5]})[-12:-4] == struct.pack(
        "<4h", 16384, -16384, 16384, -16384
    )


def test_air_actuators_without_previous_sample_gives_only_the_newest():
    _, acts, flaps = _air_actuator_samples("air_actuators_no_previous")
    assert len(acts) == len(flaps) == 1 and acts[0].t == S
    assert acts[0].thrust == 1.0 and acts[0].roll_torque == 0.0


def test_air_mission_maps_to_mission_state_and_waypoint_255_is_none():
    vals, pl = case_payload("air_mission")
    m = PROFILE.decode_many(frame(0x23, pl), "radio")[0]
    assert m.TYPE == "MissionState" and m.t == S
    assert m.t_mission == -9_500_000_000 and m.mission_index == 1 and m.mission_phase == 1 and m.mission_mode == 1
    assert m.sp_pos == pytest.approx(vals["setpoint_pos"]) and m.sp_vel == pytest.approx(vals["setpoint_vel"])
    assert m.waypoint is None and m.sp_q is None  # vector has all four zero
    q = [0.5, -0.5, 0.5, -0.5]
    pl2 = pl[:-12] + struct.pack("<4h", *(round(x * 32768) for x in q)) + pl[-4:]
    m2 = PROFILE.decode_many(frame(0x23, pl2), "radio")[0]
    assert m2.sp_q == pytest.approx(q, abs=1e-4)
    pl = bytearray(pl)
    pl[11] = 3
    assert PROFILE.decode_many(frame(0x23, bytes(pl)), "radio")[0].waypoint == 3


def test_describe_frame_labels_air_packets_with_the_schema_rates():
    for port, hz in ((0x22, 25.0), (0x23, 5.0)):
        info = PROFILE.describe_frame(frame(port, b"", src=1))
        assert info.remote is True and (info.nominal_air_hz, info.nominal_usb_hz) == (hz, 0.0)


def test_unknown_enum_values_stay_plain_integers():
    pl = bytearray(xp.VehicleStatus().encode())
    pl[12], pl[15] = 99, 77
    vs = by_type(PROFILE.decode_many(frame(0x20, bytes(pl)), "usb"))["VehicleStatus"]
    assert vs.mode == 99 and vs.ctrl_source == 77


def test_pose_goes_to_attitude_and_position():
    vals, pl = case_payload("pose")
    d = by_type(PROFILE.decode_many(frame(0x80, pl), "usb"))
    assert d["Attitude"].omega == pytest.approx(vals["omega"]) and d["Attitude"].q == pytest.approx(vals["q"])
    assert d["Position"].pos == pytest.approx(vals["pos"]) and d["Position"].vel == pytest.approx(vals["vel"])
    assert d["Attitude"].tilt_acc is None and d["Position"].h_acc is None  # not on the wire: not supplied


def test_air_pose_has_no_omega():
    vals, pl = case_payload("air_pose")
    d = by_type(PROFILE.decode_many(frame(0x21, pl), "link"))
    assert d["Attitude"].omega is None and d["Attitude"].q == pytest.approx(vals["q"], abs=1e-4)
    assert vals["pos"][2] == 1234.567 and d["Position"].pos[2] == pytest.approx(1234.567, abs=1e-9)  # i32, 1 mm
    assert d["Position"].pos == pytest.approx(vals["pos"], abs=1e-3)


def test_actuators_mission_gnss():
    vals, pl = case_payload("actuators_no_ground")
    d = by_type(PROFILE.decode_many(frame(0x81, pl), "usb"))
    assert d["Actuators"].flaps == pytest.approx(vals["flaps"]) and d["Actuators"].tvc == pytest.approx(vals["tvc"])
    assert d["FlapState"].angle == d["Actuators"].flaps and d["FlapState"].cmd is None
    assert math.isnan(d["Actuators"].ground_distance)
    vals, pl = case_payload("mission")
    m = by_type(PROFILE.decode_many(frame(0x82, pl), "usb"))["MissionState"]
    assert m.t_mission == round(vals["mission_time"] * 1e9) and m.mission_index == vals["index"]
    assert m.sp_pos == pytest.approx(vals["setpoint_pos"]) and int(m.mission_mode) == vals["mission_mode"]
    assert m.sp_q == pytest.approx(vals["setpoint_q"])
    # all four zero = no attitude setpoint; NaN (older sender) = none as well
    for q in ([0.0] * 4, [math.nan] * 4):
        pl2 = pl[:-26] + struct.pack("<4f", *q) + pl[-10:]
        assert by_type(PROFILE.decode_many(frame(0x82, pl2), "usb"))["MissionState"].sp_q is None
    vals, pl = case_payload("gnss")
    g = by_type(PROFILE.decode_many(frame(0x83, pl), "usb"))["Gps"]
    assert (g.lat, g.lon, g.num_sats, g.fix_type) == (vals["latitude"], vals["longitude"], 14, 3)
    assert (g.pos_acc, g.alt_acc, g.vel_acc) == (vals["h_acc"], vals["v_acc"], vals["s_acc"])


def test_handset_link_stats_log_and_acks():
    vals, pl = case_payload("handset_telem")
    h = by_type(PROFILE.decode_many(frame(0x03, pl), "link"))["HandsetTelem"]
    assert h.mode_text == vals["mode_text"] and h.latitude == pytest.approx(vals["latitude"], abs=1e-7)
    assert h.armed == bool(vals["flags"] & xp.HandsetTelem.FLAGS_ARMED)
    vals, pl = case_payload("link_stats")
    tx = by_type(PROFILE.decode_many(frame(0x01, pl, src=2), "link"))
    assert tx["TxModuleStats"].rx_lq == vals["rx_lq"] and tx["RadioLinkStats"].lq_pct == vals["rx_lq"]
    assert tx["RadioLinkStats"].rssi_dbm == [float(vals["rssi"])]
    assert (
        tx["TxModuleStats"].rx_antenna == vals["antenna"] & 15
        and tx["TxModuleStats"].tx_antenna == vals["antenna"] >> 4
    )
    rx = by_type(PROFILE.decode_many(frame(0x01, pl, src=3), "link"))
    assert set(rx) == {"RxModuleStats"}  # node 3 does not feed RadioLinkStats (that is the TX module's)
    (raw,) = PROFILE.decode_many(frame(0x01, pl, src=1), "link")
    assert raw.TYPE == "RawFrame" and "unexpected node" in raw.err
    vals, pl = case_payload("log_text")
    (c,) = PROFILE.decode_many(frame(0x02, pl, src=3), "link")
    assert c.TYPE == "ConsoleText" and c.text.startswith("[node 3 ") and c.text.endswith(vals["text"])
    vals, pl = case_payload("request_ack_rejected")
    a = by_type(PROFILE.decode_many(frame(0x12, pl), "usb"))["RequestAck"]
    assert (a.req_id, a.status, a.detail) == (7, 2, 9)


def test_debug_packets():
    vals, pl = case_payload("imu_raw")
    i = by_type(PROFILE.decode_many(frame(0x90, pl), "usb"))["ImuRaw"]
    assert i.gyro == pytest.approx(vals["gyro"]) and i.pressure == pytest.approx(vals["pressure"])
    vals, pl = case_payload("estimator")
    assert by_type(PROFILE.decode_many(frame(0x91, pl), "usb"))["Estimator"].pos_std == pytest.approx(vals["pos_std"])
    vals, pl = case_payload("task_timing")
    t = by_type(PROFILE.decode_many(frame(0x92, pl), "usb"))["TaskTiming"]
    assert t.count == vals["count"] and t.avg[0] == pytest.approx(vals["avg"][0])


def test_rc_state_from_pocket_and_origin():
    vals, pl = case_payload("rc_state")
    r = by_type(PROFILE.decode_many(frame(0x10, pl, src=2, seq=9), "link"))["RcState"]
    assert r.origin == L.RcOrigin.handset and r.seq == 9 and r.flags == vals["flags"] and r.source_ok is True
    assert r.ch_us[0] == 1000 and r.ch_us[2] == 1500 and r.ch_us[4] == 2000
    assert r.ch_us[5] == 0  # CH_INVALID: channel not provided
    assert by_type(PROFILE.decode_many(frame(0x10, pl, src=0), "usb"))["RcState"].origin == L.RcOrigin.gs
    _, pl = case_payload("rc_state_not_ok")
    assert by_type(PROFILE.decode_many(frame(0x10, pl, src=2), "link"))["RcState"].source_ok is False


def test_unknown_port_short_payload_and_request_become_rawframes():
    (r,) = PROFILE.decode_many(frame(0x55, b"\xde\xad"), "usb")
    assert (r.TYPE, r.channel, r.hex, r.err) == ("RawFrame", 0x55, "dead", "unknown port")
    (r,) = PROFILE.decode_many(frame(0x20, b"\x00" * 5), "usb")
    assert r.TYPE == "RawFrame" and "VEHICLE_STATUS" in r.err and r.len == 5
    (r,) = PROFILE.decode_many(frame(0x11, xp.Request(req_id=1, action=7).encode()), "usb")
    assert r.TYPE == "RawFrame" and "REQUEST" in r.err
    assert PROFILE.decode(frame(0x55, b"x"), "usb").TYPE == "RawFrame"  # contract: decode returns one packet
    assert decode_frame(PROFILE, frame(0x55, b"x"), "usb")[0].TYPE == "RawFrame"


def test_decode_never_raises_on_hostile_frames():
    for ch in (0, 0x10, 0x20, 0x80, 0xFF, 0x100, 5000):
        for pl in (b"", b"\xff" * 300, bytes(range(60))):
            for meta in ({}, {"src": 99}, {"src": "x"}):
                assert PROFILE.decode_many(Frame(channel=ch, payload=pl, t=S, meta=meta), "usb")


def test_text_payload_with_non_ascii_never_fails():
    pl = bytearray(xp.HandsetTelem().encode())
    pl[1:9] = b"\xff\xfeAB\x00\x00\x00\x00"
    (h,) = PROFILE.decode_many(frame(0x03, bytes(pl)), "usb")
    assert h.TYPE == "HandsetTelem"


# ---- heartbeats: presence and version check


def test_heartbeat_vector_has_a_foreign_schema_hash():
    case = next(c for c in wire_vectors()["net_frames"] if c["name"] == "heartbeat")
    f = fr.parse_net_frame(bytes.fromhex(case["frame_hex"]), S)
    pkts = PROFILE.decode_many(f, "usb")
    ns, ev = pkts
    assert ns.TYPE == "NetworkStatus" and ev.TYPE == "GsEvent" and ev.kind == GsEventKind.link_warning
    assert (ns.node, ns.role, ns.net_version, ns.schema_hash, ns.uptime) == (1, 1, 1, 0x12345678, 1.0)
    assert ns.incompatible and "0x12345678" in ns.detail and f"0x{xp.SCHEMA_HASH:08X}" in ns.detail
    assert ns.local_schema_hash == xp.SCHEMA_HASH and ns.present == [0, 1, 0, 0]


def test_heartbeat_compatible_presence_and_expiry():
    p = load_profile("exvectrnet-1")
    (ns,) = p.decode_many(hb_frame(1, t=S), "usb")
    assert not ns.incompatible and ns.detail == "" and ns.present == [0, 1, 0, 0]
    p.decode_many(frame(0x20, xp.VehicleStatus().encode(), src=3, t=2 * S), "usb")  # any valid frame is presence
    (ns,) = p.decode_many(hb_frame(2, t=3 * S), "usb")
    assert ns.present == [0, 1, 1, 1] and ns.age[1] == pytest.approx(2.0) and math.isnan(ns.age[0])
    (ns,) = p.decode_many(hb_frame(2, t=int(4.5 * S)), "usb")  # node 1 last seen 3.5 s ago, node 3 2.5 s ago
    assert ns.present == [0, 0, 1, 1]
    (ns,) = p.decode_many(hb_frame(2, t=3 * S), "other")  # presence is per source
    assert ns.present == [0, 0, 1, 0]


def test_heartbeat_version_and_hash_mismatch_events_only_on_change():
    p = load_profile("exvectrnet-1")
    ev = lambda pk: [x for x in pk if x.TYPE == "GsEvent"]  # noqa: E731
    pk = p.decode_many(hb_frame(1, version=2), "usb")
    assert pk[0].incompatible and "network version 2" in pk[0].detail and len(ev(pk)) == 1
    assert ev(p.decode_many(hb_frame(1, version=2), "usb")) == []  # same state, no new event
    pk = p.decode_many(hb_frame(1), "usb")
    assert not pk[0].incompatible and len(ev(pk)) == 1 and "compatible again" in ev(pk)[0].detail
    pk = p.decode_many(hb_frame(3, schema=0xDEADBEEF), "usb")
    assert pk[0].incompatible and "0xDEADBEEF" in pk[0].detail
    pk = p.decode_many(hb_frame(2), "usb")  # a healthy node does not clear another node's problem
    assert pk[0].incompatible


def test_bad_heartbeats_become_rawframes():
    p = load_profile("exvectrnet-1")
    short = Frame(channel=fr.HEARTBEAT_CHANNEL, payload=b"\x01\x01", t=S, meta={"src": 1, "type": 1})
    assert p.decode_many(short, "usb")[0].TYPE == "RawFrame"
    assert p.decode_many(hb_frame(7), "usb")[0].TYPE == "RawFrame"  # node outside 0..3


# ---- duplicate suppression (network-design 3.4)


def accepted(p, seq, t, src="usb", node=1, port=0x20):
    return bool(p.decode_many(frame(port, xp.VehicleStatus().encode(), src=node, seq=seq, t=t), src))


def test_sequence_rule():
    p = load_profile("exvectrnet-1")
    t = S
    assert accepted(p, 10, t)  # first frame is accepted whatever its number
    assert not accepted(p, 10, t + 1)  # same number: duplicate
    assert accepted(p, 11, t + 2)
    assert not accepted(p, 10, t + 3)  # older copy
    assert accepted(p, 11 + 127, t + 4)  # +127 is still "newer"
    assert not accepted(p, 11 + 127 + 128, t + 5)  # +128 is "older"
    assert accepted(p, 11 + 127 + 1, t + 6)  # +1 ok
    assert p.duplicates_dropped == 3


def test_sequence_wraps_and_gap_resets():
    p = load_profile("exvectrnet-1")
    assert accepted(p, 254, S) and accepted(p, 255, S + 1) and accepted(p, 0, S + 2) and accepted(p, 1, S + 3)
    assert not accepted(p, 255, S + 4)
    assert accepted(p, 255, S + 3 + int(1.1 * S))  # nothing for more than 1 s: next frame accepted


def test_duplicates_are_tracked_per_source_node_and_port_and_heartbeats_are_exempt():
    p = load_profile("exvectrnet-1")
    assert accepted(p, 5, S) and accepted(p, 5, S, src="link")  # two links delivering the same frame: both shown
    assert accepted(p, 5, S, node=2) and accepted(p, 5, S, port=0x80)
    assert not accepted(p, 5, S + 1)
    for i in range(3):
        assert p.decode_many(hb_frame(1, t=S + i), "usb")


def test_suppressed_duplicate_through_serial_decode_helper_is_empty_and_decode_says_why():
    p = load_profile("exvectrnet-1")
    f = frame(0x20, xp.VehicleStatus().encode(), seq=3)
    assert len(decode_frame(p, f, "usb")) == 2
    assert decode_frame(p, f, "usb") == []
    r = p.decode(f, "usb")
    assert r.TYPE == "RawFrame" and "duplicate" in r.err


# ---- uplink


def rc(ch_us=None, origin=L.RcOrigin.gs, **kw):
    return L.RcState(t=5, src="gs", origin=origin, ch_us=ch_us if ch_us is not None else [1500] * 16, seq=1, **kw)


def cmd(**kw):
    d = dict(session=0xCAFE0001, go=0, profile=2, vehicle_mode=0, motors=0, reset_button=0, failsafe=0)
    return L.CmdState(t=5, src="gs", **{**d, "pocket_live": False, "input_virtual": False, **kw})


def test_encode_cmd_state_bytes_match_the_independent_encoder():
    p = load_profile("exvectrnet-1")
    framer = p.make_framer()
    for seq in range(3):  # the profile's own counter when the engine sets no seq
        f = p.encode(cmd(go=1, profile=1, vehicle_mode=1, motors=1, reset_button=1, pocket_live=True))
        assert f.channel == 0x13 and f.meta["dst"] == 1 and f.meta["seq"] == seq and f.meta["latest"] is True
        assert f.meta["qos"] == xp.CmdState.QOS == 0  # control class, latest wins (the Router's single slot)
        pl = net.pack_cmd_state(0xCAFE0001, go=1, profile=1, vehicle_mode=1, motors=1, reset_button=1, flags=3)
        assert f.payload == pl and len(pl) == 14
        assert framer.encode(f) == net.stream_frame(net.net_data(0, 1, 0x13, seq, pl))
    # what the vehicle's strict parser and the generated decoder both see
    f = p.encode(cmd(failsafe=1, input_virtual=True))
    assert net.parse_cmd_state(f.payload) == {
        "pocket_live": False, "input_virtual": True, "session": 0xCAFE0001, "go": 0, "profile": 2,
        "vehicle_mode": 0, "motors": 0, "reset_button": 0, "failsafe": 1,
    }  # fmt: skip
    d = xp.CmdState.decode(f.payload)
    assert (d.flags, d.version, d.session, d.failsafe, d.reserved) == (5, 1, 0xCAFE0001, 1, 0)


def test_encode_cmd_state_uses_the_engines_sequence_number_so_two_links_carry_the_same_frame():
    a, b = load_profile("exvectrnet-1"), load_profile("exvectrnet-1")  # profile instances are shared per core
    a.encode(cmd())  # a source encoding something else first must not matter
    fa, fb = a.encode(cmd(seq=77)), b.encode(cmd(seq=77))
    assert fa.meta["seq"] == fb.meta["seq"] == 77 and fa.payload == fb.payload
    assert a.encode(cmd(seq=300)).meta["seq"] == 300 & 0xFF


@pytest.mark.parametrize(
    "bad",
    [
        {"go": 2},
        {"profile": 3},
        {"vehicle_mode": 2},
        {"motors": 2},
        {"reset_button": 2},
        {"failsafe": 2},
        {"session": 0},
    ],
)
def test_encode_cmd_state_never_sends_an_out_of_range_field(bad):
    assert load_profile("exvectrnet-1").encode(cmd(**bad)) is None


def test_the_vehicle_side_parser_rejects_every_out_of_range_frame():
    good = net.pack_cmd_state(5)
    assert net.parse_cmd_state(good) is not None
    for bad in (
        net.pack_cmd_state(5, flags=0), net.pack_cmd_state(5, flags=8), net.pack_cmd_state(5, version=2),
        net.pack_cmd_state(0), net.pack_cmd_state(5, go=2), net.pack_cmd_state(5, profile=3),
        net.pack_cmd_state(5, vehicle_mode=2), net.pack_cmd_state(5, motors=2), net.pack_cmd_state(5, reset_button=2),
        net.pack_cmd_state(5, failsafe=2), net.pack_cmd_state(5, reserved=1), good[:13], good + b"\0",
    ):  # fmt: skip
        assert net.parse_cmd_state(bad) is None


def test_the_gs_never_encodes_rc_state_any_more():
    p = load_profile("exvectrnet-1")
    assert p.encode(rc()) is None and p.encode(rc(origin=L.RcOrigin.handset)) is None


def test_encode_refuses_everything_but_cmd_state_and_request():
    p = load_profile("exvectrnet-1")
    assert p.encode(cmd(session=None)) is None
    for t in REGISTRY.all():
        if t.name not in ("CmdState", "Request"):
            assert p.encode(t.cls()) is None, t.name
    assert p.encode(V.Attitude()) is None and p.encode(L.RequestAck()) is None


def _request(**kw):
    d = {"req_id": 0x0102, "action": L.RequestAction.LINK_UPLOAD_MODE, "target": 2, "arg_i": 0, "arg_f": 0.0}
    return L.Request(t=5, src="gs", **{**d, **kw})


def test_encode_request_addressed_to_its_target_matches_the_independent_decoder():
    p = load_profile("exvectrnet-1")
    fr_ = p.encode(_request())
    assert (fr_.channel, fr_.meta["dst"], fr_.meta["qos"], fr_.meta["latest"]) == (0x11, 2, xp.Request.QOS, False)
    assert net.parse_request(fr_.payload) == {
        "req_id": 0x0102, "action": 10, "target": 2, "arg_i": 0, "arg_f": 0.0, "session": 0,
    }  # fmt: skip
    wire = p.make_framer().encode(fr_)
    f = net.parse_stream(net.cobs_decode(wire[:-1]))
    assert (f["type"], f["src"], f["dst"], f["port"]) == ("data", 0, 2, 0x11)
    assert p.encode(_request(target=1)).meta["dst"] == 1
    # a REQUEST to the vehicle carries the command session (gs-control Q5); to a radio node it is 0
    assert net.parse_request(p.encode(_request(target=1, session=0xCAFE0001)).payload)["session"] == 0xCAFE0001
    assert net.parse_request(p.encode(_request(target=2, session=0xCAFE0001)).payload)["session"] == 0
    assert len(p.encode(_request(target=1, session=7)).payload) == 16 == xp.Request.MAX_SIZE
    seqs = [p.encode(_request()).meta["seq"] for _ in range(3)]
    assert seqs[1] == seqs[0] + 1 and seqs[2] == seqs[0] + 2  # its own sequence counter, independent of RC_STATE


def test_encode_request_refuses_bad_ids_and_incomplete_packets():
    p = load_profile("exvectrnet-1")
    for bad in ({"req_id": 0}, {"req_id": 0x10000}, {"req_id": None}, {"action": None}, {"target": None}):
        assert p.encode(_request(**bad)) is None, bad
    assert p.encode(_request(target=300)) is None  # does not fit u8


def test_sequence_counter_wraps_at_256():
    p = load_profile("exvectrnet-1")
    seqs = [p.encode(cmd()).meta["seq"] for _ in range(258)]
    assert seqs[:3] == [0, 1, 2] and seqs[255:258] == [255, 0, 1]


# ---- the fake vehicle's independent packers against the generated module


def test_independent_packers_decode_with_the_generated_module():
    from exvectr_gs.sources.scenario import Scenario

    sc = Scenario(1)
    for t in (0.0, 7.3, 30.0):
        att, pos, gps = sc.fields("Attitude", t), sc.fields("Position", t), sc.fields("Gps", t)
        vs, ms = sc.fields("VehicleState", t), sc.fields("MissionState", t)
        p = xp.Pose.decode(net.pack_pose(5, att, pos))
        assert list(p.omega) == pytest.approx(att["omega"], rel=1e-6) and list(p.pos) == pytest.approx(
            pos["pos"], rel=1e-6
        )
        a = xp.AirPose.decode(net.pack_air_pose(5, att, pos))
        assert list(a.q) == pytest.approx(att["q"], abs=1e-4) and list(a.pos) == pytest.approx(pos["pos"], abs=0.001)
        g = xp.Gnss.decode(net.pack_gnss(5, gps))
        assert (g.latitude, g.satellites, g.fix_type) == (gps["lat"], gps["num_sats"], 3)
        m = xp.Mission.decode(net.pack_mission(5, ms))
        assert m.mission_time == pytest.approx(ms["t_mission"] / 1e9, abs=1e-3) and m.phase == int(ms["mission_phase"])
        s = xp.VehicleStatus.decode(net.pack_vehicle_status(int(t * 1e9), vs, ms["mission_mode"], ms["mission_phase"]))
        assert s.t_ns == int(t * 1e9) and bool(s.flags & xp.VehicleStatus.FLAGS_ARMED) == vs["armed"]
        h = xp.HandsetTelem.decode(net.pack_handset(vs, gps, att, "RUN"))
        assert (
            h.mode_text == "RUN"
            and h.satellites == gps["num_sats"]
            and h.latitude == pytest.approx(gps["lat"], abs=1e-6)
        )
        k = xp.Actuators.decode(net.pack_actuators(5, [0.1] * 4, [0.2, 0.3], 4.0, float("nan")))
        assert list(k.tvc) == pytest.approx([0.2, 0.3]) and math.isnan(k.ground_distance)
    ls = xp.LinkStats.decode(net.pack_link_stats(2500, 3, 97, -70, 9, 0x11, 70000))
    assert (ls.uptime, ls.rx_lq, ls.rssi, ls.antenna, ls.air_rx_ok) == (2.5, 97, -70, 0x11, 70000 & 0xFFFF)
    assert xp.ImuRaw.decode(net.pack_imu(1, 0.5)).pressure == pytest.approx(101325.0 - 5)
    assert list(xp.Estimator.decode(net.pack_estimator(1)).pos_std) == pytest.approx([0.5, 0.5, 0.8])
    tt = xp.TaskTiming.decode(net.pack_timing(1))
    assert tt.count == 3 and tt.avg[0] == pytest.approx(1e-4) and math.isnan(tt.avg[7])
    rcs = xp.RcState.decode(net.pack_rc_state(1, list(range(-8, 8))))
    assert rcs.ch == list(range(-8, 8)) and rcs.flags == 1
    hb = xp.Heartbeat.decode(net.net_heartbeat(1, 1, 0xABCD, 4000)[4:])
    assert (hb.schema_hash, hb.uptime, hb.role, hb.net_version) == (0xABCD, 4.0, 1, 1)


def test_heartbeat_bytes_match_the_independent_encoder():
    p = load_profile("exvectrnet-1")
    f = p.encode_heartbeat(12.3456)
    expect = net.stream_frame(net.net_heartbeat(0, 0, xp.SCHEMA_HASH, 12345, 1))
    assert p.make_framer().encode(f) == expect
    # the spec bytes: CTRL 0x56, TTL 3, DST broadcast, SRC 0, then the 11-byte payload
    (back,) = fr.NetFramer().feed(expect, S)
    assert back.channel == fr.HEARTBEAT_CHANNEL and len(back.payload) == 11
    assert back.meta["ttl"] == 3 and back.meta["dst"] == 0xFF and back.meta["src"] == 0
    assert back.meta["qos"] == 1 and back.meta["latest"] is True
    hb = xp.Heartbeat.decode(back.payload)
    assert hb.role == xp.HeartbeatRole.GROUND_STATION and hb.net_version == 1 and hb.flags == 0
    assert hb.schema_hash == xp.SCHEMA_HASH and hb.uptime == pytest.approx(12.345)


def test_heartbeat_uptime_wraps_like_u32_and_never_negative():
    p = load_profile("exvectrnet-1")
    assert xp.Heartbeat.decode(p.encode_heartbeat(-5.0).payload).uptime == 0
    assert xp.Heartbeat.decode(p.encode_heartbeat(2**32 / 1000 + 1).payload).uptime == pytest.approx(1.0)


def test_heartbeat_is_not_a_packet_and_does_not_disturb_rc_encoding():
    p = load_profile("exvectrnet-1")
    p.encode_heartbeat(1.0)
    p.encode_heartbeat(2.0)
    assert p.encode(cmd()).meta["seq"] == 0  # heartbeats do not touch the command sequence counter
    assert p.encode_heartbeat(3.0).channel not in range(256)  # no DATA port: it can never be a control packet


def test_other_profiles_have_no_heartbeat():
    assert not hasattr(load_profile("provisional-1"), "encode_heartbeat")
    assert not hasattr(load_profile("fake"), "encode_heartbeat")


# ---- GNSS (AIR_GNSS 0x24, AIR_GNSS_INFO 0x25, USB GNSS 0x83) and the home position (0x50)


def test_air_gnss_decodes_position_and_nwu_velocity():
    vals, pl = case_payload("air_gnss")
    g = PROFILE.decode_many(frame(0x24, pl), "radio")[0]
    assert g.TYPE == "Gps" and g.t == S
    assert g.lat == pytest.approx(vals["latitude"], abs=1e-9) and g.lon == pytest.approx(vals["longitude"], abs=1e-9)
    assert g.alt == pytest.approx(1234.567, abs=1e-9) and g.vel == pytest.approx(vals["vel"], abs=1e-9)
    assert (g.num_sats, g.fix_type, g.pos_acc, g.alt_acc, g.info_t) == (None,) * 5  # no info received yet


def test_air_gnss_no_fix_sentinels_are_none():
    _, pl = case_payload("air_gnss_no_fix")
    g = PROFILE.decode_many(frame(0x24, pl), "radio")[0]
    assert g.lat is None and g.lon is None and g.alt is None and g.vel is None


def test_air_gnss_info_is_merged_into_the_next_air_gnss_per_source():
    vals, info = case_payload("air_gnss_info")
    _, pos = case_payload("air_gnss")
    prof = load_profile("exvectrnet-1")
    # info alone emits nothing: re-emitting an old position under a new time stamp would make it look fresh
    assert prof.decode_many(frame(0x25, info, t=S), "radio") == []
    g = prof.decode_many(frame(0x24, pos, t=S + 100_000_000), "radio")[0]
    assert (g.num_sats, g.fix_type, g.info_t) == (vals["satellites"], vals["fix_type"], S)
    assert g.pos_acc == pytest.approx(1.23) and g.alt_acc == pytest.approx(2.5)
    assert g.lat is not None and g.t == S + 100_000_000
    # another source does not see it
    assert prof.decode_many(frame(0x24, pos, t=S + 200_000_000), "other")[0].fix_type is None
    # unknown accuracy (65535) is None; the 10 Hz packets keep carrying the last info
    _, unk = case_payload("air_gnss_info_unknown")
    prof.decode_many(frame(0x25, unk, t=S + 300_000_000), "radio")
    g = prof.decode_many(frame(0x24, pos, t=S + 400_000_000), "radio")[0]
    assert g.pos_acc is None and g.alt_acc is None and g.num_sats == 0 and g.info_t == S + 300_000_000


def test_usb_gnss_and_air_gnss_feed_the_same_packet_type():
    _, pl = case_payload("gnss")
    usb = PROFILE.decode_many(frame(0x83, pl), "usb")[0]
    _, pos = case_payload("air_gnss")
    air = PROFILE.decode_many(frame(0x24, pos), "radio")[0]
    assert type(usb) is type(air) and usb.TYPE == "Gps" and usb.num_sats == 14 and usb.info_t is None


def test_home_position_decodes_flags_and_sentinels():
    vals, pl = case_payload("home_position")
    h = PROFILE.decode_many(frame(0x50, pl), "radio")[0]
    assert h.TYPE == "HomePosition"
    assert (h.latlon_valid, h.alt_valid, h.simulation, h.zeroing) == (True, True, False, False)
    assert h.lat == pytest.approx(vals["latitude"], abs=1e-9) and h.alt == pytest.approx(421.5)
    assert h.h_acc == pytest.approx(0.85) and h.epoch == 2 and h.set_time == 123456789012
    _, pl = case_payload("home_position_invalid")
    h = PROFILE.decode_many(frame(0x50, pl), "radio")[0]
    assert (h.latlon_valid, h.alt_valid) == (False, False)
    assert h.lat is None and h.lon is None and h.alt is None and h.h_acc is None and h.set_time == 0


def test_vehicle_status_home_epoch_and_older_sender():
    _, pl = case_payload("vehicle_status")
    assert by_type(PROFILE.decode_many(frame(0x20, pl), "usb"))["VehicleStatus"].home_epoch == 2
    # an older sender's 28 byte payload decodes as epoch 0 (no home yet)
    assert by_type(PROFILE.decode_many(frame(0x20, pl[:28]), "usb"))["VehicleStatus"].home_epoch == 0


def test_independent_fake_encoder_matches_the_gnss_and_home_vectors():
    for name, fn in (("air_gnss", net.pack_air_gnss), ("air_gnss_info", net.pack_air_gnss_info)):
        c = next(c for c in packet_vectors()["cases"] if c["name"] == name)
        v = c["values"]
        g = {"lat": v.get("latitude"), "lon": v.get("longitude"), "alt": v.get("altitude"), "vel": v.get("vel"),
             "fix_type": v.get("fix_type"), "num_sats": v.get("satellites"), "pos_acc": v.get("h_acc"),
             "alt_acc": v.get("v_acc")}  # fmt: skip
        assert fn(v["t_us"], g).hex() == c["hex"], name
    c = next(c for c in packet_vectors()["cases"] if c["name"] == "home_position")
    v = c["values"]
    h = {"latlon_valid": True, "alt_valid": True, "simulation": False, "zeroing": False, "lat": v["latitude"],
         "lon": v["longitude"], "alt": v["altitude"], "h_acc": v["h_acc"], "epoch": v["epoch"],
         "set_time": v["set_time"]}  # fmt: skip
    assert net.pack_home_position(h).hex() == c["hex"]


# ---- schema 0x414DE27A: calib_state, roll_torque, mission_mode / waypoint, with the shared vectors


def decode_vector(name, port, via="usb"):
    cases = packet_vectors()
    c = next(c for c in cases["cases"] + cases["decode_only"] if c["name"] == name)
    return by_type(PROFILE.decode_many(frame(port, bytes.fromhex(c["hex"])), via))


def test_schema_hash_is_the_pinned_one():
    assert xp.SCHEMA_HASH == 0x5E23CDD1


def test_calib_state_decodes_into_booleans():
    vals, _ = case_payload("vehicle_status")
    assert vals["calib_state"] == 23  # gyro, accel, mag valid, ready to arm
    vs = decode_vector("vehicle_status", 0x20)["VehicleStatus"]
    assert vs.calib_state == 23 and vs.home_epoch == 2
    assert (vs.calib_gyro_ok, vs.calib_accel_ok, vs.calib_mag_ok) == (True, True, True)
    assert vs.calib_session is False and vs.ready_to_arm is True


@pytest.mark.parametrize(
    "bits, expect",
    [
        (0b00001, (True, False, False, False, False)),
        (0b00110, (False, True, True, False, False)),
        (0b01001, (True, False, False, True, False)),
        (0b10000, (False, False, False, False, True)),
        (0b11111, (True, True, True, True, True)),
    ],
)
def test_calib_state_bits(bits, expect):
    pl = xp.VehicleStatus(calib_state=bits).encode()
    vs = by_type(PROFILE.decode_many(frame(0x20, pl), "usb"))["VehicleStatus"]
    assert (vs.calib_gyro_ok, vs.calib_accel_ok, vs.calib_mag_ok, vs.calib_session, vs.ready_to_arm) == expect
    assert decode_line(encode_line(vs)) == vs


def test_calib_state_zero_or_absent_is_unknown():
    for name in ("vehicle_status_older_sender_no_calib_state", "vehicle_status_older_sender_no_home_epoch"):
        vs = decode_vector(name, 0x20)["VehicleStatus"]
        assert vs.calib_state in (0, None)
        assert all(
            getattr(vs, k) is None
            for k in ("calib_gyro_ok", "calib_accel_ok", "calib_mag_ok", "calib_session", "ready_to_arm")
        )


def test_usb_actuators_carries_roll_torque_and_older_sender_is_unknown():
    a = decode_vector("actuators_no_ground", 0x81)["Actuators"]
    assert a.roll_torque == pytest.approx(-0.0625) and math.isnan(a.ground_distance)
    old = decode_vector("actuators_older_sender_no_roll_torque", 0x81)["Actuators"]
    assert old.roll_torque is None


def test_usb_mission_carries_mode_and_waypoint():
    m = decode_vector("mission", 0x82)["MissionState"]
    assert (int(m.mission_mode), m.waypoint) == (3, 2)
    n = decode_vector("mission_not_waypoint", 0x82)["MissionState"]
    assert int(n.mission_mode) == 4 and n.waypoint is None  # 255 = not the waypoint mission
    assert decode_line(encode_line(m)) == m


def test_older_mission_sender_decodes_without_failing():
    m = decode_vector("mission_older_sender_no_mode_waypoint", 0x82)["MissionState"]
    assert m.sp_q is not None and m.mission_index == 4  # the older fields still decode


def test_air_pose_nominal_rate_is_25_hz_from_the_schema():
    assert xp.PACKETS_BY_NAME["AIR_POSE"].AIR_RATE_HZ == 25


def test_fake_packers_match_the_new_fields():
    from exvectr_gs.sources.scenario import Scenario

    sc = Scenario(1)
    t = 30.0
    vs, ms, ac = sc.fields("VehicleState", t), sc.fields("MissionState", t), sc.fields("Actuators", t)
    st = sc.fields("VehicleStatus", t)
    s = xp.VehicleStatus.decode(
        net.pack_vehicle_status(
            1, vs, ms["mission_mode"], ms["mission_phase"], home_epoch=1, calib_state=st["calib_state"]
        )
    )
    assert s.calib_state == st["calib_state"] == 0b10111
    k = xp.Actuators.decode(net.pack_actuators(5, [0.1] * 4, [0.2, 0.3], 4.0, 1.0, ac["roll_torque"]))
    assert k.roll_torque == pytest.approx(ac["roll_torque"], abs=1e-6)
    m = xp.Mission.decode(net.pack_mission(5, ms))
    assert m.mission_mode == int(ms["mission_mode"]) and m.waypoint == 255


def test_vehicle_status_t9b_tail_and_older_payloads():
    from exvectr_gs.sources.scenario import Scenario

    sc = Scenario(1)
    vs, ms = sc.fields("VehicleState", 30.0), sc.fields("MissionState", 30.0)
    base = (1, vs, ms["mission_mode"], ms["mission_phase"])
    full = net.pack_vehicle_status(
        *base, home_epoch=1, calib_state=0, ctrl=(0x180 | 2, 6, 0b101, 2, 9), cmd_session=0xBEEF
    )
    assert len(full) == 38 == xp.VehicleStatus.MAX_SIZE
    s = by_type(PROFILE.decode_many(frame(0x20, full), "usb"))["VehicleStatus"]
    assert (s.ctrl_flags, s.go_refusal, s.degraded_bits, s.mission_profile, s.rc_rejected) == (0x182, 6, 5, 2, 9)
    assert s.cmd_session == 0xBEEF
    # a sender with the T9b tail but without cmd_session: None, not "no session"
    t9b = by_type(PROFILE.decode_many(frame(0x20, full[:36]), "usb"))["VehicleStatus"]
    assert t9b.rc_rejected == 9 and t9b.cmd_session is None
    # an older sender: the generated module decodes the tail as 0, the GS packet says None (unknown)
    old = net.pack_vehicle_status(*base, home_epoch=1, calib_state=0)
    s = by_type(PROFILE.decode_many(frame(0x20, old), "usb"))["VehicleStatus"]
    assert xp.VehicleStatus.decode(old).mission_profile == 0
    assert (s.ctrl_flags, s.go_refusal, s.degraded_bits, s.mission_profile, s.rc_rejected, s.cmd_session) == (None,) * 6
    part = full[:34]  # ctrl_flags and go_refusal present, the rest not
    s = by_type(PROFILE.decode_many(frame(0x20, part), "usb"))["VehicleStatus"]
    assert (s.ctrl_flags, s.go_refusal, s.degraded_bits, s.mission_profile) == (0x182, 6, 5, None)
    assert decode_line(encode_line(s)) == s


def test_cmd_applied_decodes_through_the_generated_module_and_the_fake_packer():
    pl = net.pack_cmd_applied(5, 1, 2, 1, 1, 0, 1, 3, 0b0101, 0xCAFE0001, 0.04, 0.05, float("nan"), (1, 2, 3, 4))
    assert len(pl) == 26 == xp.CmdApplied.MAX_SIZE and xp.CmdApplied.decode(pl).session == 0xCAFE0001
    r = by_type(PROFILE.decode_many(frame(0x84, pl), "usb"))["CmdApplied"]
    assert (r.go, r.profile, r.vehicle_mode, r.motors, r.reset_button, r.failsafe) == (1, 2, 1, 1, 0, 1)
    assert (r.flags, r.safe_seen, r.session) == (3, 5, 0xCAFE0001)
    assert r.age == pytest.approx(0.04) and r.usb_age == pytest.approx(0.05) and r.uart_age is None
    assert r.rejects == [1, 2, 3, 4]
    assert decode_line(encode_line(r)) == r


def _mission_state(port, pl):
    return by_type(PROFILE.decode_many(frame(port, pl), "radio"))["MissionState"]


def test_mission_state_flip_start_and_stop_altitude_from_air_mission():
    vals, pl = case_payload("air_mission")
    m = _mission_state(0x23, pl)
    assert m.flip_start_altitude == pytest.approx(123.4, abs=0.05) and m.stop_altitude == pytest.approx(20.0, abs=0.05)
    # sentinel -32768 = NaN on the wire = None (flip start only sent during freefall)
    m = _mission_state(0x23, pl[:-4] + struct.pack("<2h", -32768, 200))
    assert m.flip_start_altitude is None and m.stop_altitude == pytest.approx(20.0)
    m = _mission_state(0x23, pl[:-4] + struct.pack("<2h", 1234, -32768))
    assert m.flip_start_altitude == pytest.approx(123.4) and m.stop_altitude is None
    # older sender: short payload, fields missing -> unknown
    m = _mission_state(0x23, pl[:-4])
    assert m.flip_start_altitude is None and m.stop_altitude is None


def test_mission_state_flip_start_and_stop_altitude_from_mission():
    vals, pl = case_payload("mission")
    m = _mission_state(0x82, pl)
    assert m.flip_start_altitude == pytest.approx(vals["flip_start_altitude"], abs=1e-3)
    assert m.stop_altitude == pytest.approx(vals["stop_altitude"])
    m = _mission_state(0x82, pl[:-8] + struct.pack("<2f", math.nan, 35.5))
    assert m.flip_start_altitude is None and m.stop_altitude == pytest.approx(35.5)
    m = _mission_state(0x82, pl[:-8] + struct.pack("<2f", 50.0, math.nan))
    assert m.flip_start_altitude == pytest.approx(50.0) and m.stop_altitude is None
    for cut in (8, 10):  # older sender without the two fields (with and without mode/waypoint)
        m = _mission_state(0x82, pl[:-cut])
        assert m.flip_start_altitude is None and m.stop_altitude is None
