import math
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from exvectr_gs.packets import REGISTRY, CodecError, UnknownPacket, decode_line, encode_line
from exvectr_gs.packets.defs.host import GsEventKind, Note
from exvectr_gs.packets.defs.vehicle import Attitude, MissionPhase, VehicleMode, VehicleState
from exvectr_gs.packets.fields import FieldSpec

GOLDEN = Path(__file__).parents[1] / "data" / "golden.exlog"
floats = st.floats(allow_nan=True, allow_infinity=True)


def value_strategy(spec: FieldSpec):
    k = spec.kind
    if k == "float":
        return floats
    if k in ("int", "time_ns"):
        return st.integers(-(2**63), 2**63 - 1)
    if k == "bool":
        return st.booleans()
    if k == "enum":
        return st.one_of(st.sampled_from(list(spec.enum)), st.integers(0, 255))
    if k == "str":
        return st.text()
    n = spec.length
    elem = floats if k == "float_array" else st.integers(-(2**31), 2**31)
    return st.lists(elem, min_size=n or 0, max_size=n or 5)


def packet_strategy(ptype):
    fields = {s.name: st.none() | value_strategy(s) for s in ptype.fields}
    return st.builds(
        ptype.cls,
        t=st.integers(0, 2**63 - 1),
        src=st.from_regex(r"[A-Za-z0-9_\-]{1,8}", fullmatch=True),
        **fields,
    )


@pytest.mark.parametrize("ptype", REGISTRY.all(), ids=lambda p: p.name)
def test_roundtrip_property(ptype):
    @given(packet_strategy(ptype))
    def check(pkt):
        line = encode_line(pkt)
        assert "\n" not in line and "\r" not in line
        assert decode_line(line) == pkt

    check()


def test_nan_inf_none_and_unknown_enum_roundtrip():
    pkt = Attitude(t=1, src="usb", omega=[math.nan, math.inf, -math.inf], q=[1.0, 0.0, 0.0, -0.0])
    assert decode_line(encode_line(pkt)) == pkt
    vs = VehicleState(t=2, src="usb", mode=9, armed=False)
    line = encode_line(vs)
    assert "mode=#9" in line and "armed=false" in line and "sensor_imu" not in line
    assert decode_line(line).mode == 9


def test_string_with_spaces_quotes_and_unicode():
    n = Note(t=3, src="gs", text='gust "from" west \\ ü space  =x')
    line = encode_line(n)
    assert line.isascii()
    assert decode_line(line) == n


def test_nan_aware_equality():
    a = Attitude(t=1, src="a", omega=[math.nan])
    assert a == Attitude(t=1, src="a", omega=[math.nan])
    assert a != Attitude(t=1, src="a", omega=[0.0])


def test_extra_fields_survive():
    line = '5 usb Note text="hi" newfield=12 other=[1,2] s="a b"'
    pkt = decode_line(line)
    assert pkt.text == "hi"
    assert pkt.extra == {"newfield": "12", "other": "[1,2]", "s": '"a b"'}
    assert encode_line(pkt) == line


def test_unknown_type():
    line = '7 link Mystery a=1 b="x y" c=[1,2]'
    pkt = decode_line(line)
    assert isinstance(pkt, UnknownPacket)
    assert pkt.type == "Mystery" and pkt.raw_fields == {"a": "1", "b": '"x y"', "c": "[1,2]"}
    assert encode_line(pkt) == line


@pytest.mark.parametrize("line", ["", "   ", "#exlog v=1", "#meta started=2026"])
def test_meta_and_blank_lines_give_none(line):
    assert decode_line(line) is None


@pytest.mark.parametrize(
    "line",
    [
        'abc usb Note text="x"',
        "5 usb",
        "5 usb Note text",
        'x 5 usb Note text="unterminated',
        '5 usb Note text="a"b',
        '5 usb Note text="a" text="b"',
        "5 usb VehicleState mode=Bogus",
        "5 usb VehicleState armed=maybe",
        "5 usb Attitude q=1,2,3",
        "5 usb Attitude q=[a]",
        "5 usb Note text=5",
    ],
)
def test_malformed_lines_raise(line):
    with pytest.raises(CodecError):
        decode_line(line)


def test_golden_file_decodes_to_known_values_and_reencodes_identically():
    lines = [ln for ln in GOLDEN.read_text().splitlines()]
    pkts = [(ln, decode_line(ln)) for ln in lines]
    decoded = [p for _, p in pkts if p is not None]
    assert {type(p).TYPE for p in decoded} == {p.name for p in REGISTRY.all()}  # every registered type
    for ln, p in pkts:
        if p is not None:
            assert encode_line(p) == ln
    by_type = {type(p).TYPE: p for p in decoded}
    att = by_type["Attitude"]
    assert (att.t, att.src) == (1763471000123456789, "link")
    assert att.omega == [0.0123, -0.0041, 0.0007] and att.q == [0.99981, 0.0102, -0.0163, 0.0021]
    vs = by_type["VehicleState"]
    assert vs.mode is VehicleMode.Running and vs.armed is True and vs.tvc_thrust == [0.12, -0.05, 11.8]
    ms = by_type["MissionState"]
    assert ms.mission_phase is MissionPhase.Ascent and ms.t_mission == 4250000000
    assert ms.waypoint == 3
    vst = by_type["VehicleStatus"]
    assert vst.calib_state == 23 and vst.calib_gyro_ok is True and vst.calib_session is False
    assert vst.ready_to_arm is True and by_type["Actuators"].roll_torque == 0.012
    assert by_type["Position"].ground_distance == 1.234
    # an old recording's Actuators.ground_distance is no schema field any more: it is carried as an unknown extra
    # field (no error, re-encoded unchanged) and shows no ground-distance reading
    assert by_type["Actuators"].extra == {"ground_distance": "nan"}
    assert by_type["GsEvent"].kind is GsEventKind.rec_start
    assert by_type["RcState"].ch_us[2] == 1000 and by_type["RcState"].seq == 8812
    assert by_type["Note"].text == "gusts from the west, ~3 m/s"
