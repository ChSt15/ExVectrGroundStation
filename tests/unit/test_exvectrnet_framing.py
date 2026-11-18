"""exvectrnet-1 stream framing against the shared vectors (ExVectrNetwork wire_v1.json) and robustness."""

import random
import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st

from exvectr_gs.wire.exvectrnet_1 import framing as fr
from exvectr_gs.wire.profile import Frame

from ..tests_tools import net  # independent encoder of the fake vehicle
from ..vectors import wire_vectors

V = wire_vectors()
T = 123


def hx(s: str) -> bytes:
    return bytes.fromhex(s)


@pytest.mark.parametrize("case", V["crc16_ccitt_false"], ids=lambda c: c["name"])
def test_crc16_vectors(case):
    data = hx(case["input_hex"])
    assert fr.crc16(data) == int(case["crc_hex"], 16)
    assert net.crc16(data) == int(case["crc_hex"], 16)


@pytest.mark.parametrize("case", V["cobs"], ids=lambda c: c["name"])
def test_cobs_vectors_both_implementations(case):
    dec, enc = hx(case["decoded_hex"]), hx(case["encoded_hex"])
    assert fr.cobs_encode(dec) == enc and fr.cobs_decode(enc) == dec
    assert net.cobs_encode(dec) == enc and net.cobs_decode(enc) == dec


@pytest.mark.parametrize("h", V["cobs_invalid_hex"])
def test_cobs_invalid_vectors_rejected(h):
    with pytest.raises(ValueError):
        fr.cobs_decode(hx(h))


@given(st.binary(max_size=600))
def test_cobs_roundtrip_and_matches_independent_encoder(data):
    enc = fr.cobs_encode(data)
    assert 0 not in enc and fr.cobs_decode(enc) == data and net.cobs_encode(data) == enc


@pytest.mark.parametrize("case", V["net_frames"], ids=lambda c: c["name"])
def test_net_frame_vectors_parse(case):
    f = fr.parse_net_frame(hx(case["frame_hex"]), T)
    m = case["fields"]
    assert (f.meta["src"], f.meta["dst"], f.meta["ttl"], f.meta["qos"], f.meta["latest"]) == (
        m["src"], m["dst"], m["ttl"], m["qos"], bool(m["latest"]),
    )  # fmt: skip
    if "data" in case:
        d = case["data"]
        assert (f.channel, f.meta["seq"], f.payload) == (d["port"], d["seq"], hx(d["payload_hex"]))
        built = fr.build_data_frame(
            m["src"], m["dst"], d["port"], d["seq"], hx(d["payload_hex"]), m["qos"], bool(m["latest"]), m["ttl"]
        )
        assert built == hx(case["frame_hex"])
    else:
        assert f.channel == fr.HEARTBEAT_CHANNEL and f.payload == hx(case["frame_hex"])[4:]


@pytest.mark.parametrize("case", V["stream_frames"], ids=lambda c: c["name"])
def test_stream_frame_vectors(case):
    wire = hx(case["wire_hex"])
    assert fr.build_stream_frame(hx(case["frame_hex"])) == wire
    assert net.stream_frame(hx(case["frame_hex"])) == wire
    framer = fr.NetFramer()
    (frame,) = framer.feed(wire, T)
    assert isinstance(frame, Frame) and framer.stats.frames == 1 and framer.stats.crc_errors == 0
    assert hx(case["decoded_hex"])[:-2] == b"\x01" + hx(case["frame_hex"])


def test_framer_encode_matches_vector():
    case = next(c for c in V["stream_frames"] if c["name"] == "request_ack")
    nf = next(c for c in V["net_frames"] if c["name"] == "request_ack")
    d = nf["data"]
    frame = Frame(
        channel=d["port"], payload=hx(d["payload_hex"]), t=0, meta={"src": 1, "dst": 0, "seq": d["seq"], "qos": 2}
    )
    assert fr.NetFramer(node_id=1).encode(frame) == hx(case["wire_hex"])


def good(payload=b"\x01\x02\x03", port=0x80, seq=0, src=1):
    return fr.build_stream_frame(fr.build_data_frame(src, 0, port, seq, payload, 3, True, 3))


def test_maximum_frame_sizes_roundtrip():
    for n in (0, 1, 233, 234):  # payload; 234 B is the largest (240 B network frame, 243 B decoded)
        for fill in (1, 0, 255):
            payload = bytes([fill]) * n
            wire = good(payload)
            assert len(wire) <= 246  # at most 245 encoded plus the delimiter (spec 4.2)
            (f,) = fr.NetFramer().feed(wire, T)
            assert f.payload == payload
    with pytest.raises(ValueError):
        good(bytes(235))


def test_byte_by_byte_and_split_feeds():
    wire = good(b"abc", seq=1) + good(b"def", seq=2)
    framer = fr.NetFramer()
    got = []
    for b in wire:
        got += framer.feed(bytes([b]), T)
    assert [f.payload for f in got] == [b"abc", b"def"]


def test_bad_frame_costs_only_that_frame():
    a, b, c = good(b"a", seq=1), good(b"b", seq=2), good(b"c", seq=3)
    flipped = bytearray(b)
    flipped[4] ^= 0x40  # CRC error (still valid COBS: no zero created)
    framer = fr.NetFramer()
    got = framer.feed(a + bytes(flipped) + c, T)
    assert [f.payload for f in got] == [b"a", b"c"]
    assert framer.stats.crc_errors + framer.stats.resyncs == 1 and framer.stats.frames == 2


def test_truncated_frame_and_garbage_resync():
    a, b = good(b"a", seq=1), good(b"b", seq=2)
    framer = fr.NetFramer()
    got = framer.feed(b"\x05\x11\x22" + b"\x00" + a[: len(a) // 2] + b"\x00" + b, T)
    assert [f.payload for f in got] == [b"b"]
    assert framer.stats.resyncs + framer.stats.crc_errors >= 2


def test_overlong_run_without_delimiter_is_discarded_until_next_delimiter():
    framer = fr.NetFramer()
    got = framer.feed(b"\x11" * 300 + b"\x00" + good(b"ok"), T)
    assert [f.payload for f in got] == [b"ok"] and framer.stats.resyncs == 1


def test_length_skind_version_and_type_rules():
    def stream(net_frame, skind=1, fix_crc=True):
        dec = bytes([skind]) + net_frame
        return fr.cobs_encode(dec + struct.pack("<H", fr.crc16(dec))) + b"\x00"

    framer = fr.NetFramer()
    ok = fr.build_data_frame(1, 0, 0x80, 0, b"x", 3, True, 3)
    assert len(framer.feed(stream(ok), T)) == 1
    assert framer.feed(stream(ok, skind=2), T) == []  # unknown SKIND
    assert framer.feed(stream(b"\x56\x03\xff"), T) == []  # network frame shorter than 4
    assert framer.feed(stream(bytes([0x86]) + ok[1:]), T) == []  # version 2
    assert framer.feed(stream(bytes([0x20]) + ok[1:]), T) == []  # reserved type 2
    assert framer.feed(stream(ok + bytes(250)), T) == []  # longer than 240 B
    assert framer.feed(stream(ok[:5]), T) == []  # DATA without a full transport header
    assert framer.stats.frames == 1


def test_reset_drops_partial_frame_but_keeps_counters():
    framer = fr.NetFramer()
    w = good(b"zzz")
    framer.feed(w[:5], T)
    framer.reset()
    got = framer.feed(w, T)
    assert len(got) == 1 and framer.stats.frames == 1


@given(st.binary(max_size=2000), st.integers(0, 2**31))
def test_framer_never_raises_on_garbage_and_recovers(garbage, seed):
    framer = fr.NetFramer()
    framer.feed(garbage, T)
    framer.feed(b"\x00", T)  # terminates whatever was pending
    got = framer.feed(good(b"after"), T)
    assert got and got[-1].payload == b"after"


def test_random_bit_errors_never_deliver_a_wrong_payload():
    rng = random.Random(5)
    payload = bytes(range(40))
    base = bytearray(good(payload))
    wrong = 0
    for _ in range(500):
        w = bytearray(base)
        w[rng.randrange(len(w) - 1)] ^= 1 << rng.randrange(8)
        for f in fr.NetFramer().feed(bytes(w), T):
            wrong += f.payload != payload
    assert wrong == 0  # CRC-16 catches every single-bit error
