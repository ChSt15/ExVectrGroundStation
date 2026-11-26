"""CMD_STATE, CMD_APPLIED and REQUEST.session against the shared vectors of the pinned ExVectrPackets: the generated
module, the fake vehicle's independent packer and parser, and the GS profile's encoder must all agree."""

import math

import exvectr_packets as xp
import pytest

from exvectr_gs.packets.defs import link as L
from exvectr_gs.wire import load_profile

from ..tests_tools import net
from ..vectors import packet_vectors

V = packet_vectors()
CMD_CASES = [c for c in V["cases"] if c["packet"] == "CMD_STATE"]
CMD_FAIL = [c for c in V["decode_fail"] if c["packet"] == "CMD_STATE"]


def test_the_vectors_are_the_pinned_ones():
    assert xp.SCHEMA_HASH == 0xF49ACD01 and CMD_CASES and CMD_FAIL


@pytest.mark.parametrize("case", CMD_CASES, ids=lambda c: c["name"])
def test_cmd_state_vectors_encode_and_decode_in_every_implementation(case):
    v = case["values"]
    gen = xp.CmdState(**v)
    assert gen.encode().hex() == case["hex"]
    back = xp.CmdState.decode(bytes.fromhex(case["hex"]))
    assert {k: int(getattr(back, k)) for k in v} == v
    # the independent packer and the vehicle's strict parser
    assert net.pack_cmd_state(**{k: v[k] for k in v}).hex() == case["hex"]
    parsed = net.parse_cmd_state(bytes.fromhex(case["hex"]))
    assert parsed is not None and parsed["session"] == v["session"] and parsed["go"] == v["go"]
    # the GS profile encodes the same bytes from the semantic packet
    pkt = L.CmdState(
        t=1, src="gs", session=v["session"], go=v["go"], profile=v["profile"], vehicle_mode=v["vehicle_mode"],
        motors=v["motors"], reset_button=v["reset_button"], failsafe=v["failsafe"],
        pocket_live=bool(v["flags"] & 2), input_virtual=bool(v["flags"] & 4),
    )  # fmt: skip
    assert load_profile("exvectrnet-1").encode(pkt).payload.hex() == case["hex"]


@pytest.mark.parametrize("case", CMD_FAIL, ids=lambda c: c["name"])
def test_every_malformed_cmd_state_is_rejected(case):
    raw = bytes.fromhex(case["hex"])
    with pytest.raises(ValueError):
        xp.CmdState.decode(raw)
    assert net.parse_cmd_state(raw) is None


def test_cmd_applied_vector_matches_the_independent_packer():
    case = next(c for c in V["cases"] if c["packet"] == "CMD_APPLIED")
    v = case["values"]
    nan = math.nan
    pl = net.pack_cmd_applied(
        v["t_us"], v["go"], v["profile"], v["vehicle_mode"], v["motors"], v["reset_button"], v["failsafe"],
        v["flags"], v["safe_seen"], v["session"], v["age"], nan if v["usb_age"] is None else v["usb_age"],
        v["uart_age"], tuple(v["rejects"]),
    )  # fmt: skip
    assert pl.hex() == case["hex"]


def test_request_session_vectors_and_the_older_sender_without_it():
    case = next(c for c in V["cases"] if c["name"] == "request_vehicle_session")
    d = xp.Request.decode(bytes.fromhex(case["hex"]))
    assert d.session == 1 and net.parse_request(bytes.fromhex(case["hex"]))["session"] == 1
    old = next(c for c in V["decode_only"] if c["name"] == "request_older_sender_no_session")
    assert xp.Request.decode(bytes.fromhex(old["hex"])).session == 0
    assert net.parse_request(bytes.fromhex(old["hex"]))["session"] == 0
