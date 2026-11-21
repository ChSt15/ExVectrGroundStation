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
    "HANDSET_TELEM": {"HandsetTelem"},
    "LINK_STATS": {"TxModuleStats", "RadioLinkStats"},
    "LINK_CONFIG": {"LinkConfig"},
    "LOG_TEXT": {"ConsoleText"},
    "RC_STATE": {"RcState"},
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
    mapped = {xp.PACKETS_BY_NAME[n].PORT for n in EXPECTED} | {0x11}  # REQUEST is deliberately a RawFrame
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
    assert net.pack_air_mission(77, m) == pl
    assert net.pack_air_mission(77, m | {"sp_q": [0.5, -0.5, 0.5, -0.5]})[-8:] == struct.pack(
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
    m2 = PROFILE.decode_many(frame(0x23, pl[:-8] + struct.pack("<4h", *(round(x * 32768) for x in q))), "radio")[0]
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
    assert m.sp_pos == pytest.approx(vals["setpoint_pos"]) and m.mission_mode is None
    assert m.sp_q == pytest.approx(vals["setpoint_q"])
    # all four zero = no attitude setpoint; NaN (older sender) = none as well
    for q in ([0.0] * 4, [math.nan] * 4):
        pl2 = pl[:-16] + struct.pack("<4f", *q)
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


def test_encode_rc_state_bytes_match_the_independent_encoder():
    p = load_profile("exvectrnet-1")
    framer = p.make_framer()
    chs = [1000, 1250, 1500, 1750, 2000, 0] + [1500 + 10 * i for i in range(10)]
    wire_ch = [-1000, -500, 0, 500, 1000, -32768] + [20 * i for i in range(10)]
    for seq in range(3):
        f = p.encode(rc(chs))
        assert f.channel == 0x10 and f.meta["dst"] == 1 and f.meta["seq"] == seq
        expect = net.stream_frame(net.net_data(0, 1, 0x10, seq, net.pack_rc_state(xp.RcState.FLAGS_SOURCE_OK, wire_ch)))
        assert framer.encode(f) == expect
    # what the vehicle's parser sees
    (back,) = fr.NetFramer().feed(framer.encode(p.encode(rc())), S)
    assert back.meta["src"] == 0 and back.meta["dst"] == 1 and back.channel == 0x10
    rcs = xp.RcState.decode(back.payload)
    assert rcs.ch == [0] * 16 and rcs.flags == xp.RcState.FLAGS_SOURCE_OK


def test_encode_sets_only_generic_flags_and_clamps():
    p = load_profile("exvectrnet-1")
    f = p.encode(rc([900, 2100] + [1500] * 14))
    r = xp.RcState.decode(f.payload)
    assert r.ch[:2] == [-1000, 1000]
    assert r.flags == xp.RcState.FLAGS_SOURCE_OK  # no motor-enable or other bit is ever set by the profile
    assert xp.RcState.decode(p.encode(rc(source_ok=False)).payload).flags == 0


def test_encode_refuses_everything_but_gs_rc_state():
    p = load_profile("exvectrnet-1")
    assert p.encode(rc(origin=L.RcOrigin.handset)) is None
    assert p.encode(rc([1500] * 3)) is None
    assert p.encode(L.RcState(origin=L.RcOrigin.gs, seq=1)) is None
    for t in REGISTRY.all():
        if t.name != "RcState":
            assert p.encode(t.cls()) is None, t.name
    assert p.encode(V.Attitude()) is None and p.encode(L.RequestAck()) is None


def _request(**kw):
    d = {"req_id": 0x0102, "action": L.RequestAction.LINK_UPLOAD_MODE, "target": 2, "arg_i": 0, "arg_f": 0.0}
    return L.Request(t=5, src="gs", **{**d, **kw})


def test_encode_request_addressed_to_its_target_matches_the_independent_decoder():
    p = load_profile("exvectrnet-1")
    fr_ = p.encode(_request())
    assert (fr_.channel, fr_.meta["dst"], fr_.meta["qos"], fr_.meta["latest"]) == (0x11, 2, xp.Request.QOS, False)
    assert net.parse_request(fr_.payload) == {"req_id": 0x0102, "action": 10, "target": 2, "arg_i": 0, "arg_f": 0.0}
    wire = p.make_framer().encode(fr_)
    f = net.parse_stream(net.cobs_decode(wire[:-1]))
    assert (f["type"], f["src"], f["dst"], f["port"]) == ("data", 0, 2, 0x11)
    assert p.encode(_request(target=1)).meta["dst"] == 1
    seqs = [p.encode(_request()).meta["seq"] for _ in range(3)]
    assert seqs[1] == seqs[0] + 1 and seqs[2] == seqs[0] + 2  # its own sequence counter, independent of RC_STATE


def test_encode_request_refuses_bad_ids_and_incomplete_packets():
    p = load_profile("exvectrnet-1")
    for bad in ({"req_id": 0}, {"req_id": 0x10000}, {"req_id": None}, {"action": None}, {"target": None}):
        assert p.encode(_request(**bad)) is None, bad
    assert p.encode(_request(target=300)) is None  # does not fit u8


def test_sequence_counter_wraps_at_256():
    p = load_profile("exvectrnet-1")
    seqs = [p.encode(rc()).meta["seq"] for _ in range(258)]
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
    assert p.encode(rc()).meta["seq"] == 0  # heartbeats do not touch the RC sequence counter
    assert p.encode_heartbeat(3.0).channel not in range(256)  # no DATA port: it can never be a control packet


def test_other_profiles_have_no_heartbeat():
    assert not hasattr(load_profile("provisional-1"), "encode_heartbeat")
    assert not hasattr(load_profile("fake"), "encode_heartbeat")
