import math
import random
import struct

import pytest

from exvectr_gs.packets import REGISTRY
from exvectr_gs.packets.defs.link import RcOrigin, RcState
from exvectr_gs.packets.defs.vehicle import MissionPhase, VehicleMode
from exvectr_gs.wire import ConsoleText, Frame, load_profile
from exvectr_gs.wire.provisional_1.framing import (
    MAX_ENCODED,
    ProvisionalFramer,
    cobs_decode,
    cobs_encode,
    crc16,
    encode_frame,
)

P = load_profile("provisional-1")


def frame(ch, payload=b""):
    return Frame(channel=ch, payload=payload, t=7, meta={})


# ---------------------------------------------------------------- COBS and CRC


def test_cobs_known_vectors():
    assert cobs_encode(b"") == b"\x01"
    assert cobs_encode(b"\x00") == b"\x01\x01"
    assert cobs_encode(b"\x00\x00") == b"\x01\x01\x01"
    assert cobs_encode(b"\x11\x22\x00\x33") == b"\x03\x11\x22\x02\x33"
    assert cobs_encode(b"\x11\x22\x33\x44") == b"\x05\x11\x22\x33\x44"
    assert cobs_encode(bytes(range(1, 255))) == b"\xff" + bytes(range(1, 255)) + b"\x01"
    assert cobs_encode(bytes(range(1, 256))) == b"\xff" + bytes(range(1, 255)) + b"\x02\xff"


def test_cobs_roundtrip_random():
    rng = random.Random(1)
    for n in (0, 1, 2, 253, 254, 255, 256, 508, 509, 1000):
        for _ in range(20):
            data = bytes(rng.choice([0, 0, rng.randrange(256)]) for _ in range(n))
            enc = cobs_encode(data)
            assert 0 not in enc
            assert cobs_decode(enc) == data


def test_cobs_decode_rejects_malformed():
    for bad in (b"\x05\x01", b"\x02\x00\x01", b"\x00"):
        with pytest.raises(ValueError):
            cobs_decode(bad)


def test_crc16_check_value():
    assert crc16(b"123456789") == 0x29B1  # CRC-16/CCITT-FALSE


def test_frame_bytes_exact():
    # channel 10, payload 01 02: body 0a 00 02 00 01 02 + crc
    body = bytes.fromhex("0a0002000102")
    crc = struct.pack("<H", crc16(body))
    assert encode_frame(10, b"\x01\x02") == b"\x00" + cobs_encode(body + crc) + b"\x00"


# ---------------------------------------------------------------- framer


def test_framer_frames_and_console():
    fr = ProvisionalFramer()
    stream = (
        b"hello\r\nworld\n"
        + encode_frame(10, b"\x00\x01")
        + b"after\n"
        + encode_frame(300, b"")
        + encode_frame(11, b"x")
    )
    items = list(fr.feed(stream, 5))
    assert [type(i).__name__ for i in items] == ["ConsoleText", "ConsoleText", "Frame", "ConsoleText", "Frame", "Frame"]
    assert [i.text for i in items if isinstance(i, ConsoleText)] == ["hello", "world", "after"]
    frames = [i for i in items if isinstance(i, Frame)]
    assert [(f.channel, f.payload) for f in frames] == [(10, b"\x00\x01"), (300, b""), (11, b"x")]
    assert fr.stats.frames == 3 and fr.stats.crc_errors == 0 and fr.stats.bytes == len(stream)


def test_framer_any_chunking():
    stream = b"a\n" + encode_frame(10, bytes(range(256)) * 3) + b"b\n" + encode_frame(50, b"\x00" * 27)
    ref = [(type(i).__name__, getattr(i, "channel", None)) for i in ProvisionalFramer().feed(stream, 0)]
    rng = random.Random(3)
    for _ in range(30):
        fr = ProvisionalFramer()
        out = []
        i = 0
        while i < len(stream):
            n = rng.randrange(1, 40)
            out += fr.feed(stream[i : i + n], 0)
            i += n
        assert [(type(x).__name__, getattr(x, "channel", None)) for x in out] == ref


def test_framer_crc_error_and_resync():
    good = encode_frame(10, b"abcdef")
    body = struct.pack("<HH", 10, 6) + b"abcdef"
    bad = b"\x00" + cobs_encode(body + struct.pack("<H", crc16(body) ^ 0x0101)) + b"\x00"  # wrong CRC
    fr = ProvisionalFramer()
    items = list(fr.feed(bytes(bad) + good, 0))
    frames = [i for i in items if isinstance(i, Frame)]
    assert len(frames) == 1 and frames[0].payload == b"abcdef"
    assert fr.stats.crc_errors == 1


def test_framer_garbage_never_raises_and_recovers():
    rng = random.Random(9)
    fr = ProvisionalFramer()
    for _ in range(200):
        fr.feed(bytes(rng.randrange(256) for _ in range(rng.randrange(1, 300))), 0)
    good = encode_frame(11, b"ok")
    got = []
    for _ in range(3):  # a stray delimiter state may eat the first frame, never more than that
        got += [i for i in fr.feed(good + b"\n", 0) if isinstance(i, Frame)]
    assert got and got[-1].payload == b"ok"


def test_framer_truncated_and_overlong():
    fr = ProvisionalFramer()
    good = encode_frame(10, b"1234")
    assert [i for i in fr.feed(good[:5], 0)] == []
    items = list(fr.feed(good, 0))  # the cut frame is dropped when the next delimiter arrives
    assert [i.channel for i in items if isinstance(i, Frame)] == [10]
    fr2 = ProvisionalFramer()
    fr2.feed(b"\x00" + b"\x01" * (MAX_ENCODED + 10), 0)
    assert fr2.stats.resyncs >= 1
    assert [i for i in fr2.feed(good, 0) if isinstance(i, Frame)]


def test_framer_text_edge_cases():
    fr = ProvisionalFramer()
    assert list(fr.feed(b"partial", 1)) == []
    items = list(fr.feed(b" line\n\n\xff\xfe bad utf8\n", 2))
    assert [i.text for i in items][0] == "partial line"
    assert "�" in items[1].text
    assert len(list(ProvisionalFramer().feed(b"x" * 3000, 0))) == 2  # over-long lines are split


def test_framer_reset_keeps_stats_drops_partial():
    fr = ProvisionalFramer()
    good = encode_frame(10, b"1")
    list(fr.feed(good, 0))
    fr.feed(b"text", 0)
    fr.feed(good[:4], 0)
    fr.reset()
    assert fr.stats.frames == 1
    assert [i.channel for i in fr.feed(good, 0) if isinstance(i, Frame)] == [10]


# ---------------------------------------------------------------- layouts (byte vectors)


def test_attitude_vector():
    raw = struct.pack("<9h", 32767, 0, -32767, 32767, 0, 0, 0, 16384, 0)
    p = P.decode(frame(10, raw), "usb")
    assert p.TYPE == "Attitude" and p.src == "usb" and p.t == 7
    assert p.omega == pytest.approx([math.radians(1000), 0.0, -math.radians(1000)])
    assert p.q == pytest.approx([1.0, 0.0, 0.0, 0.0])
    assert p.tilt_acc == pytest.approx(math.radians(360) * 16384 / 32767) and p.north_acc == 0.0


def test_position_vector():
    raw = struct.pack("<8h", 16384, -16384, 3277, 32767, 0, -32768, 3277, 0)
    p = P.decode(frame(11, raw), "usb")
    assert p.pos == pytest.approx([500.0, -500.0, 100.0], rel=1e-3)
    assert p.vel[0] == pytest.approx(100.0) and p.vel[2] == pytest.approx(-100.00305, rel=1e-5)  # INT16_MIN quirk
    assert p.h_acc == pytest.approx(1.0, rel=1e-3) and p.v_acc == 0.0


def test_gps_vector_is_radians():
    lat_rad = 0.8996  # about 51.5 deg
    raw = struct.pack("<iihhhhBhhh", int(lat_rad * 1e7), -1_234_567, 3277, 0, 327, -327, 12, 100, 200, 300)
    assert len(raw) == 23
    p = P.decode(frame(12, raw), "usb")
    assert p.TYPE == "Gps"
    assert p.lat == pytest.approx(lat_rad, abs=1e-7) and p.lon == pytest.approx(-0.1234567)
    assert p.alt == pytest.approx(100.0, rel=1e-3) and p.num_sats == 12
    assert p.vel == pytest.approx([0.0, 1.0, -1.0], abs=2e-2)
    assert p.pos_acc == pytest.approx(100 * 10 / 32767)


def test_vehicle_state_vector():
    head = bytes([4, 2, 2, 3, 2, 0, 2, 0, 0, 0, 1, 0, 1, 1, 1])
    raw = head + struct.pack("<6h", 32767, -16384, 0, 1000, 16384, 3277)
    assert len(raw) == 27
    p = P.decode(frame(50, raw), "usb")
    assert p.mode == VehicleMode.Running and p.sensor_baro == 3
    assert p.fail_pos_oob is True and p.fail_sensor is False and p.fail_radio_loss is True
    assert p.sim is True and p.armed is True
    assert p.tvc_thrust == pytest.approx([50.0, -25.0, 0.0], abs=1e-2)
    assert p.tvc_torque == pytest.approx(1000 * 50 / 32767)
    assert p.ground_dist == pytest.approx(1.0, abs=1e-3) and p.ground_dist_cov == pytest.approx(3277 / 32767)


def test_vehicle_state_unknown_enum_kept():
    raw = bytes([9] + [0] * 14) + bytes(12)
    p = P.decode(frame(50, raw), "usb")
    assert p.mode == 9 and not isinstance(p.mode, VehicleMode)


def test_mission_state_vector():
    raw = struct.pack("<6fBqBB", 1, 2, 3, 4, 5, 6, 3, -10_000_000_000, 0, 2)
    assert len(raw) == 35
    p = P.decode(frame(51, raw), "usb")
    assert p.sp_vel == [1.0, 2.0, 3.0] and p.sp_pos == [4.0, 5.0, 6.0]
    assert p.mission_phase == MissionPhase.Ascent and p.t_mission == -10_000_000_000 and p.mission_mode == 3


def test_decoded_packets_survive_text_codec():
    from exvectr_gs.packets import decode_line, encode_line

    raw = struct.pack("<6fBqBB", 1, 2, 3, 4, 5, 6, 3, -5, 0, 2)
    p = P.decode(frame(51, raw), "usb")
    assert decode_line(encode_line(p)) == p


# ---------------------------------------------------------------- undecodable input


def test_unknown_channel_and_bad_length_become_rawframe():
    p = P.decode(frame(999, b"\x01\x02"), "usb")
    assert p.TYPE == "RawFrame" and p.channel == 999 and p.hex == "0102" and p.len == 2 and "unknown" in p.err
    p = P.decode(frame(10, b"\x01"), "usb")
    assert p.TYPE == "RawFrame" and "expected 18" in p.err


def test_decode_never_raises():
    rng = random.Random(5)
    for _ in range(300):
        ch = rng.choice([10, 11, 12, 50, 51, 16, 0, 65535])
        data = bytes(rng.randrange(256) for _ in range(rng.choice([0, 1, 16, 18, 23, 27, 33, 35])))
        assert P.decode(frame(ch, data), "usb").TYPE


# ---------------------------------------------------------------- RcState uplink


def test_rc_state_encode_vector():
    ch = [1500] * 16
    ch[2] = 1000
    pkt = RcState(t=1, src="gs", origin=RcOrigin.gs, ch_us=ch, seq=300)
    fr = P.encode(pkt)
    assert fr is not None and fr.channel == 0x10
    assert fr.payload == bytes([300 & 0xFF]) + struct.pack("<16H", *ch)
    back = P.decode(fr, "link")
    assert back.TYPE == "RcState" and back.ch_us == ch and back.seq == 44 and back.origin == RcOrigin.handset


def test_rc_state_encode_refusals():
    ok = [1500] * 16
    assert P.encode(RcState(origin=RcOrigin.handset, ch_us=ok, seq=1)) is None  # never re-send mirrored state
    assert P.encode(RcState(origin=RcOrigin.gs, ch_us=ok[:15], seq=1)) is None
    assert P.encode(RcState(origin=RcOrigin.gs, ch_us=None, seq=1)) is None
    assert P.encode(REGISTRY.get("Note").cls(text="x")) is None  # not an UP type
    assert P.encode(REGISTRY.get("Attitude").cls()) is None
    clamped = P.encode(RcState(origin=RcOrigin.gs, ch_us=[-5] + [70000] + [1500] * 14, seq=1))
    assert struct.unpack_from("<2H", clamped.payload, 1) == (0, 65535)


def test_supported_types_and_profile_identity():
    assert P.name == "provisional-1"
    assert P.supported_types() == {"Attitude", "Position", "Gps", "VehicleState", "MissionState", "RcState"}
    fr = P.make_framer()
    assert fr.encode(frame(10, b"ab")) == encode_frame(10, b"ab")
