import math

import pytest

from exvectr_gs.api import protocol as P
from exvectr_gs.api.server import is_control_host
from exvectr_gs.cli.expr import ExprError, parse_condition
from exvectr_gs.packets import REGISTRY
from exvectr_gs.packets.defs.host import Note
from exvectr_gs.packets.defs.vehicle import Attitude, MissionPhase, MissionState, VehicleMode, VehicleState

T = 1_795_000_000_123_456_789


def test_packet_json_times_are_float_seconds_and_enums_are_names():
    ms = MissionState(t=T, src="s", mission_phase=MissionPhase.Hover, t_mission=5_000_000_000, mission_index=2)
    d = P.packet_to_json(ms)
    assert isinstance(d["t"], float) and d["t"] == pytest.approx(T / 1e9, abs=1e-6)
    assert d["f"]["mission_phase"] == "Hover"
    assert d["f"]["t_mission"] == 5.0  # time_ns fields are seconds too
    assert d["type"] == "MissionState" and d["src"] == "s"
    assert "sp_vel" not in d["f"]  # None fields are omitted


def test_nan_inf_become_null_with_nf_marker_and_roundtrip():
    a = Attitude(t=T, src="s", omega=[1.0, math.nan, 3.0], tilt_acc=math.inf, north_acc=-math.inf)
    d = P.packet_to_json(a)
    assert d["f"]["omega"] == [1.0, None, 3.0] and d["f"]["tilt_acc"] is None
    assert d["nf"] == {"omega": [None, "nan", None], "tilt_acc": "inf", "north_acc": "-inf"}
    b = P.packet_from_json(d)
    assert b.omega[1] != b.omega[1] and b.tilt_acc == math.inf and b.north_acc == -math.inf


def test_unknown_enum_value_roundtrip():
    vs = VehicleState(t=T, src="s", mode=99)
    d = P.packet_to_json(vs)
    assert d["f"]["mode"] == "#99"
    assert int(P.packet_from_json(d).mode) == 99


def test_roundtrip_every_type_within_time_precision():
    for ptype in REGISTRY.all():
        pkt = ptype.cls(t=T, src="s")
        back = P.packet_from_json(P.packet_to_json(pkt))
        assert type(back) is type(pkt) and abs(back.t - T) < 1000  # float seconds keep ~0.2 us


def test_note_text_roundtrip():
    n = Note(t=T, src="gs", text="hello world")
    assert P.packet_from_json(P.packet_to_json(n)).text == "hello world"


def test_control_host_matching():
    hosts = ["127.0.0.1", "::1"]
    assert is_control_host("127.0.0.1", hosts) and is_control_host("::1", hosts)
    assert is_control_host("::ffff:127.0.0.1", hosts)
    assert not is_control_host("192.168.1.5", hosts) and not is_control_host("", hosts)
    assert not is_control_host("not-an-ip", hosts)
    assert is_control_host("127.0.0.7", ["localhost"]) and not is_control_host("10.0.0.1", ["localhost"])
    assert is_control_host("10.0.0.9", ["10.0.0.0/24"]) and not is_control_host("10.0.1.9", ["10.0.0.0/24"])
    assert not is_control_host("127.0.0.1", [])


def test_validation_errors():
    with pytest.raises(P.ProtocolError) as e:
        P.parse_subscribe({"view": "nope"})
    assert e.value.code == "bad_request"
    with pytest.raises(P.ProtocolError) as e:
        P.parse_subscribe({"types": ["Nope"]})
    assert e.value.code == "unknown_type"
    with pytest.raises(P.ProtocolError):
        P.parse_subscribe({"mode": "bogus"})
    with pytest.raises(P.ProtocolError) as e:
        P.parse_subscribe({"max_rate_hz": 0})
    assert e.value.code == "invalid_value"
    assert P.parse_subscribe({}).types is None and P.parse_subscribe({"types": ["Attitude"]}).types == {"Attitude"}
    with pytest.raises(P.ProtocolError):
        P.parse_rc_acquire({"kind": "timed"})  # needs a duration
    with pytest.raises(P.ProtocolError):
        P.parse_rc_acquire({"kind": "timed", "duration_s": 601})
    assert P.parse_rc_acquire({"kind": "timed", "duration_s": 30}) == ("timed", 30.0)
    assert P.parse_rc_acquire({"kind": "connection", "duration_s": 30}) == ("connection", None)
    for bad in ({"values": {"SA": True}}, {"values": {"SA": "1"}}, {"values": {"SA": math.nan}}, {"values": {}}, {}):
        with pytest.raises(P.ProtocolError):
            P.parse_rc_set(bad)
    with pytest.raises(P.ProtocolError):
        P.parse_replay_speed({"x": 3})
    assert P.parse_replay_speed({"x": 0.25}) == 0.25
    with pytest.raises(P.ProtocolError):
        P.parse_history({"series": [{"type": "Attitude", "fields": ["nope"]}], "t0": 0, "t1": 1})


def test_expr_parse_and_evaluate():
    c = parse_condition("VehicleState.armed == true")
    assert c.holds(VehicleState(armed=True)) and not c.holds(VehicleState(armed=False))
    assert not c.holds(VehicleState())  # not supplied never satisfies
    c = parse_condition("VehicleState.mode == Running")
    assert c.holds(VehicleState(mode=VehicleMode.Running)) and not c.holds(VehicleState(mode=VehicleMode.Ready))
    assert parse_condition("VehicleState.mode >= Ready").holds(VehicleState(mode=VehicleMode.Running))
    c = parse_condition("Position.pos[2] > 1.5")
    from exvectr_gs.packets.defs.vehicle import Position

    assert c.holds(Position(pos=[0.0, 0.0, 2.0])) and not c.holds(Position(pos=[0.0, 0.0, 1.0]))
    assert parse_condition("Position.h_acc <= 0.5").holds(Position(h_acc=0.4))
    assert parse_condition("MissionState.mission_index != 3").holds(MissionState(mission_index=2))


@pytest.mark.parametrize(
    "text",
    [
        "garbage",
        "Nope.x == 1",
        "VehicleState.nope == 1",
        "VehicleState.armed == maybe",
        "VehicleState.armed > true",
        "VehicleState.mode == Bogus",
        "Position.pos == 1",
        "Position.pos[9] > 1",
        "Position.h_acc[0] > 1",
        "Position.h_acc > abc",
    ],
)
def test_expr_rejects(text):
    with pytest.raises(ExprError):
        parse_condition(text)
