import asyncio
import math
from collections import Counter

import pytest

from exvectr_gs.core.clock import FakeClock
from exvectr_gs.core.config import ConfigError, SourceConfig
from exvectr_gs.packets import REGISTRY, decode_line, encode_line
from exvectr_gs.packets.defs.vehicle import MissionPhase, VehicleMode
from exvectr_gs.sources import SourceKind, build_source
from exvectr_gs.sources.fake_source import PROFILES, FakeSource
from exvectr_gs.wire import load_profile

S = 1_000_000_000


def make(profile="usb", **opts):
    return FakeSource(SourceConfig(id="fake", kind="fake", options={"profile": profile, **opts}))


def test_build_source_and_caps():
    src = build_source(SourceConfig(id="f", kind="fake", rc_role="mirror"), load_profile("fake"))
    assert src.kind is SourceKind.FAKE and src.caps.rc_role == "mirror" and not src.caps.can_send
    assert asyncio.run(src.send(None)) is False  # type: ignore[arg-type]
    with pytest.raises(
        ConfigError
    ):  # serial kinds need a `match` (serial source: tests/integration/test_serial_source.py)
        build_source(SourceConfig(id="u", kind="vehicle_usb"), load_profile("fake"))


def test_usb_profile_rates():
    src = make("usb")
    src.reset(0)
    pkts = src.step(10.0)
    counts = Counter(p.TYPE for p in pkts if p.src == "fake")
    for name, rate in PROFILES["usb"].items():
        assert counts[name] == pytest.approx(rate * 10, abs=2), name
    assert counts["LinkStats"] in (10, 11)
    assert [p.t for p in pkts] == sorted(p.t for p in pkts)  # time ordered


def test_radio_profile_is_reduced():
    src = make("radio")
    src.reset(0)
    pkts = src.step(5.0)
    types = {p.TYPE for p in pkts}
    assert {"Attitude", "Position", "VehicleState", "MissionState", "RadioLinkStats", "RcState", "LinkStats"} <= types
    assert {"Actuators", "FlapState"} <= types  # AIR_ACTUATORS: two samples per frame, 50 samples/s
    assert not types & {"TvcState", "ConsoleText"}
    act = next(p for p in pkts if p.TYPE == "Actuators")
    assert act.ground_distance is None and act.roll_torque is not None
    assert next(p for p in pkts if p.TYPE == "FlapState").cmd is None
    assert Counter(p.TYPE for p in pkts)["MissionState"] == pytest.approx(25, abs=2)
    att = next(p for p in pkts if p.TYPE == "Attitude")
    assert att.tilt_acc is None and att.omega is not None  # not supplied on the radio set
    vs = next(p for p in pkts if p.TYPE == "VehicleState")
    assert vs.tvc_thrust is None and vs.mode is not None
    assert Counter(p.TYPE for p in pkts)["Attitude"] == pytest.approx(50, abs=2)
    rc = [p for p in pkts if p.TYPE == "RcState"]
    assert rc[0].origin.name == "handset" and [p.seq for p in rc] == list(range(1, len(rc) + 1))


def test_deterministic_and_seed_dependent():
    def lines(seed):
        s = make(seed=seed)
        s.reset(1000)
        return [encode_line(p) for p in s.step(8.0)]

    assert lines(1) == lines(1)
    assert lines(1) != lines(2)


def test_all_packets_roundtrip_through_text_codec():
    s = make()
    s.reset(5 * S)
    for p in s.step(3.0):
        assert decode_line(encode_line(p)) == p


def test_scripted_flight_modes_and_phases():
    s = make()
    s.reset(0)
    pkts = s.step(34.0)
    modes = [p.mode for p in pkts if p.TYPE == "VehicleState"]
    seen = list(dict.fromkeys(modes))
    assert seen == [VehicleMode.Startup, VehicleMode.SensorInit, VehicleMode.Ready, VehicleMode.Running]
    phases = list(dict.fromkeys(p.mission_phase for p in pkts if p.TYPE == "MissionState"))
    assert phases == [MissionPhase.Idle, MissionPhase.Hover, MissionPhase.Ascent, MissionPhase.Descent,
                      MissionPhase.Landing, MissionPhase.Completed]  # fmt: skip
    z = [p.pos[2] for p in pkts if p.TYPE == "Position"]
    assert max(z) == pytest.approx(5.0, abs=0.05) and z[0] == 0 and abs(z[-1]) < 1e-9
    for p in pkts:
        if p.TYPE == "Attitude":
            assert math.isclose(sum(x * x for x in p.q), 1.0, rel_tol=1e-9)
        if p.TYPE == "FlapState":
            assert all(math.radians(14.9) <= a <= math.radians(90.1) for a in p.angle)
    ms = [p for p in pkts if p.TYPE == "MissionState"]
    assert ms[0].t_mission < 0 < ms[-1].t_mission


def test_faults():
    s = make(
        faults=[
            {"kind": "dropout", "at": 2, "duration": 3, "types": ["Attitude"]},
            {"kind": "rate", "at": 6, "duration": 2, "types": ["Position"], "scale": 0.1},
            {"kind": "nan", "at": 8},
            {"kind": "unknown_enum", "at": 9, "duration": 1},
            {"kind": "failure_flag", "at": 12},
            {"kind": "disconnect", "at": 14, "duration": 2},
        ]
    )
    s.reset(0)
    pkts = s.step(20.0)

    def times(name, lo, hi):
        return [p.t / S for p in pkts if p.TYPE == name and lo <= p.t / S < hi]

    assert times("Attitude", 2, 5) == [] and len(times("Attitude", 5, 6)) > 90
    assert len(times("Position", 6, 8)) <= 12  # 100 expected without the fault
    nan = [p for p in pkts if p.TYPE == "Position" and math.isnan(p.pos[0])]
    assert len(nan) == 1 and nan[0].t / S >= 8
    vs = [p for p in pkts if p.TYPE == "VehicleState"]
    assert {p.mode for p in vs if 9 <= p.t / S < 10} == {9}
    assert all(p.fail_sensor for p in vs if p.t / S >= 12) and not any(p.fail_sensor for p in vs if p.t / S < 12)
    assert times("Attitude", 14, 16) == [] and times("VehicleState", 14, 16) == []
    ls = {round(p.t / S): p.connected for p in pkts if p.TYPE == "LinkStats"}
    assert ls[13] is True and ls[14] is False and ls[15] is False and ls[16] is True
    ev = [(p.kind.name, round(p.t / S)) for p in pkts if p.TYPE == "GsEvent"]
    assert ev == [("source_up", 0), ("source_down", 14), ("source_up", 16)]
    assert s.status().reconnects == 1


def test_bad_options():
    with pytest.raises(ConfigError):
        make("nope")
    with pytest.raises(ConfigError):
        make(faults=[{"kind": "meteor", "at": 1}])
    with pytest.raises(ConfigError):
        make(rates={"Bogus": 5})


async def test_run_with_clock_and_cancel():
    clock = FakeClock(1000 * S)
    src = FakeSource(SourceConfig(id="fake", kind="fake", options={"profile": "radio"}), clock=clock)
    got = []
    task = asyncio.create_task(src.run(got.append))
    await asyncio.sleep(0.05)
    assert src.status().state == "connected" and got  # t=0 batch
    clock.advance(2.0)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert src.status().state == "disabled"
    assert Counter(p.TYPE for p in got)["Attitude"] >= 20


def test_registry_has_every_generated_type():
    s = make()
    s.reset(0)
    assert {p.TYPE for p in s.step(3)} <= {p.name for p in REGISTRY.all()}


# ---- REQUEST answering test double (no vehicle logic)


def _request_packet(rid=5, target=1):
    from exvectr_gs.packets.defs.link import Request, RequestAction

    return Request(t=1, src="gs", req_id=rid, action=RequestAction.CALIB_APPLY, target=target, arg_i=0, arg_f=0.0)


async def _answer(src, *pkts):
    got = []
    src._sink = got.append
    src._state = "connected"
    oks = [await src.send(p) for p in pkts]
    await asyncio.sleep(0)
    return oks, got


async def test_fake_source_answers_requests_only_when_asked_to():
    assert await make().send(_request_packet()) is False
    src = make(answer_requests=True, request_status=2, request_detail=7)
    assert src.caps.can_send and src.caps.request_targets == (1, 2, 3) and src.caps.rc_role == "none"
    oks, got = await _answer(src, _request_packet())
    assert oks == [True] and [(p.TYPE, p.req_id, p.status, p.detail) for p in got] == [("RequestAck", 5, 2, 7)]


async def test_fake_source_request_is_idempotent_drops_first_n_and_respects_reach():
    src = make(answer_requests=True, request_drop_first=2, request_targets=[1])
    oks, got = await _answer(src, *[_request_packet() for _ in range(3)], _request_packet(6, target=2))
    assert oks == [True, True, True, False]  # node 2 is out of reach
    assert [p.req_id for p in got] == [5]  # only the third attempt of id 5 is answered
    assert len(src.executed_requests) == 1 and len(src.received_requests) == 3
    src._state = "searching"
    assert await src.send(_request_packet(9)) is False  # not connected


# ---- request_rules: the documented executor answers for actions 12 and 13 (test double)


def _act(action, rid=5, target=1, arg_f=25.0, session=99):
    from exvectr_gs.packets.defs.link import Request

    return Request(t=1, src="gs", req_id=rid, action=action, target=target, arg_i=0, arg_f=arg_f, session=session)


async def _rules_answer(pkt, **opts):
    src = make(answer_requests=True, request_rules=True, request_latched_session=99, **opts)
    _, got = await _answer(src, pkt)
    return src, [(p.status, p.detail) for p in got]


async def test_request_rules_done_changes_the_echoed_stop_altitude_once():
    src, got = await _rules_answer(_act(13))
    assert got == [(0, 0)] and src.stop_altitude == 25.0
    _, got = await _rules_answer(_act(12, arg_f=10.0))
    assert got == [(0, 0)]


async def test_request_rules_each_refusal_pair():
    assert (await _rules_answer(_act(13, target=2)))[1] == [(3, 2)]  # wrong target (node 2 is reachable by the fake)
    assert (await _rules_answer(_act(13), request_on_ground=False))[1] == [(2, 1)]
    assert (await _rules_answer(_act(13, session=5)))[1] == [(2, 3)]  # session differs
    assert (await _rules_answer(_act(13, session=0)))[1] == [(2, 3)]  # session 0
    assert (await _rules_answer(_act(13, arg_f=0.5)))[1] == [(3, 1)]  # out of range
    assert (await _rules_answer(_act(13, arg_f=float("nan"))))[1] == [(3, 1)]
    # the session is checked before the ground state
    assert (await _rules_answer(_act(13, session=5), request_on_ground=False))[1] == [(2, 3)]
    # a refused value never changes the echo
    src, _ = await _rules_answer(_act(13, arg_f=0.5))
    assert src.stop_altitude == 20.0
    # other actions keep the plain configured answer
    src = make(answer_requests=True, request_rules=True, request_status=0)
    _, got = await _answer(src, _request_packet())
    assert [(p.status, p.detail) for p in got] == [(0, 0)]
