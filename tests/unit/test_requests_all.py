"""The requests that replaced the telecommands (docs/requests.md): action ids, integer arguments, the shipped
whitelist, and every group's answers against the fake source (DONE and the refusals)."""

import asyncio
import sys
from pathlib import Path

import pytest

from exvectr_gs.core import Bus
from exvectr_gs.core.clock import FakeClock
from exvectr_gs.core.config import ConfigError, RequestEntry, RequestsSection, load_config, parse_config
from exvectr_gs.packets.defs.link import RequestAction
from exvectr_gs.requests import RequestController, RequestError
from exvectr_gs.sources.base import SourceConfig
from exvectr_gs.sources.fake_source import FakeSource

CORE = {"core": {"source_priority": []}}
ROOT = Path(__file__).resolve().parents[2]
S = 1_000_000_000

NEW_IDS = {
    "CALIB_CONTROL": 14,
    "SIM_WIND_SPEED": 15,
    "SIM_WIND_DIRECTION": 16,
    "SIM_GUST_STRENGTH": 17,
    "SIM_GUST_PERIOD": 18,
    "SIM_GUST_SEED": 19,
    "SIM_FLAP_STUCK": 20,
    "SIM_FLAP_SLOW": 21,
    "SIM_FAULT_CLEAR": 22,
    "GET_HOME_POSITION": 23,
}


def test_mirror_has_the_new_action_ids_and_matches_the_generated_enum():
    from exvectr_gs.wire.exvectrnet_1 import _deps  # noqa: F401

    xp = sys.modules["exvectr_packets"]
    for name, value in NEW_IDS.items():
        assert RequestAction[name] == value == xp.RequestAction[name]
    assert RequestAction.CALIB_APPLY == 7 and RequestAction.SAVE_CONFIG == 8
    assert {m.name: m.value for m in RequestAction} == {m.name: m.value for m in xp.RequestAction}
    assert xp.SCHEMA_HASH == 0x036F9C45


# ---------------------------------------------------------------- config


@pytest.mark.parametrize("name", ["gs.pocket.toml", "gs.example.toml"])
def test_shipped_configs_have_the_new_rows_and_no_telecommands(name):
    cfg = load_config(ROOT / "config" / name)
    assert not hasattr(cfg, "telecommands")
    rows = {(e.action, e.arg_i) for e in cfg.requests.actions}
    for sub in (0, 1, 2):
        assert ("CALIB_APPLY", sub) in rows
        for code in (0, 1, 2, 9):
            assert ("CALIB_CONTROL", sub * 10 + code) in rows
    by = {e.action: e for e in cfg.requests.actions if e.group == "Simulation"}
    assert set(by) == {a for a in NEW_IDS if a.startswith("SIM_")}
    assert by["SIM_GUST_SEED"].arg_i_min == 0 and by["SIM_GUST_SEED"].arg_i_max == 16777215
    assert (by["SIM_FLAP_STUCK"].arg_i_min, by["SIM_FLAP_STUCK"].arg_i_max) == (0, 3)
    assert (by["SIM_FLAP_STUCK"].arg_f_min, by["SIM_FLAP_SLOW"].arg_f_min) == (-1.0, 0.05)
    assert any(e.action == "GET_HOME_POSITION" for e in cfg.requests.actions)
    assert not any(e.action in ("SYSTEM_RESET", "CALIB_GYRO_START") for e in cfg.requests.actions)


def test_telecommands_section_is_rejected_as_retired():
    with pytest.raises(ConfigError, match="retired"):
        parse_config({**CORE, "telecommands": {}})


def test_arg_i_config_validation():
    def rows(**kw):
        return parse_config({**CORE, "requests": {"action": [{"action": "SIM_FLAP_STUCK", "target": 1, **kw}]}})

    assert rows(arg_i_min=0, arg_i_max=3, arg_i=1).requests.actions[0].arg_i_ranged
    for bad in (
        {"arg_i_min": 0},
        {"arg_i_min": 2, "arg_i_max": 1},
        {"arg_i": 1.5},
        {"arg_i_min": 0, "arg_i_max": 3, "arg_i": 4},
    ):
        with pytest.raises(ConfigError):
            rows(**bad)
    dup = {"action": "CALIB_CONTROL", "target": 1, "arg_i": 1}
    parse_config(
        {**CORE, "requests": {"action": [dup, {**dup, "arg_i": 2}]}}
    )  # same action, different fixed arg_i: fine
    with pytest.raises(ConfigError, match="twice"):
        parse_config({**CORE, "requests": {"action": [dup, dup]}})


# ---------------------------------------------------------------- the controller and the fake source


class Rig:
    def __init__(self, **opts):
        self.clock = FakeClock(1000 * S)
        self.bus = Bus()
        self.src = FakeSource(
            SourceConfig(
                id="fake",
                kind="fake",
                options={
                    "profile": "usb",
                    "answer_requests": True,
                    "request_rules": True,
                    "request_latched_session": 99,
                    **opts,
                },
            )
        )
        self.src._sink = self.bus.publish
        self.src._state = "connected"
        cfg = load_config(ROOT / "config" / "gs.example.toml")
        self.rc = RequestController(
            RequestsSection(actions=list(cfg.requests.actions)),
            [self.src],
            self.bus,
            clock=self.clock,
            control_hosts=["127.0.0.1"],
            first_id=10,
        )
        self.rc.session_fn = lambda: 99

    async def send(self, action, **kw):
        self.clock.advance(1)  # the 500 ms per-client rate limit
        st = await self.rc.send(action, 1, client="c", remote_addr="127.0.0.1", confirm=action, **kw)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        done = next(r for r in self.rc.status()["recent"] if r["req_id"] == st.req_id)
        return done["status_name"], done["detail"], self.src.received_requests[-1]


async def test_calibration_requests_carry_subsystem_and_code_and_are_done():
    r = Rig()
    for sub, code in ((0, 1), (1, 0), (2, 9), (2, 2)):
        status, detail, pkt = await r.send("CALIB_CONTROL", arg_i=sub * 10 + code)
        assert (status, detail) == ("DONE", 0)
        assert (int(pkt.action), pkt.arg_i) == (14, sub * 10 + code)
    status, _, pkt = await r.send("CALIB_APPLY", arg_i=2)
    assert status == "DONE" and (int(pkt.action), pkt.arg_i) == (7, 2)
    assert (await r.send("SAVE_CONFIG"))[0] == "DONE"
    with pytest.raises(RequestError) as e:  # the row is picked by its fixed arg_i; none given or a wrong one is refused
        await r.rc.send("CALIB_CONTROL", 1, client="c", remote_addr="127.0.0.1", confirm="CALIB_CONTROL")
    assert e.value.code == "not_whitelisted"
    with pytest.raises(RequestError) as e:
        await r.rc.send("CALIB_CONTROL", 1, client="c", remote_addr="127.0.0.1", confirm="CALIB_CONTROL", arg_i=5)
    assert e.value.code == "not_whitelisted"


async def test_calibration_refusal_is_detail_6():
    r = Rig(request_calib_refuse=True)
    assert (await r.send("CALIB_CONTROL", arg_i=1))[:2] == ("REJECTED_STATE", 6)
    assert (await r.send("CALIB_APPLY", arg_i=0))[:2] == ("REJECTED_STATE", 6)
    assert (await r.send("SAVE_CONFIG"))[:2] == ("DONE", 0)


async def test_simulation_requests_not_in_simulation_are_detail_5():
    r = Rig()
    for action, kw in (
        ("SIM_WIND_SPEED", {"arg_f": 5.0}),
        ("SIM_FLAP_STUCK", {"arg_i": 1, "arg_f": 0.5}),
        ("SIM_FAULT_CLEAR", {}),
    ):
        assert (await r.send(action, **kw))[:2] == ("REJECTED_STATE", 5), action


async def test_simulation_requests_in_simulation_are_done_with_their_arguments():
    r = Rig(request_simulation=True)
    sent = [
        ("SIM_WIND_SPEED", {"arg_f": 5.0}),
        ("SIM_WIND_DIRECTION", {"arg_f": -3.0}),
        ("SIM_GUST_STRENGTH", {"arg_f": 3.0}),
        ("SIM_GUST_PERIOD", {"arg_f": 4.0}),
        ("SIM_GUST_SEED", {"arg_i": 16777215}),
        ("SIM_FLAP_STUCK", {"arg_i": 3, "arg_f": -1.0}),
        ("SIM_FLAP_SLOW", {"arg_i": 2, "arg_f": 0.3}),
        ("SIM_FAULT_CLEAR", {}),
    ]
    for action, kw in sent:
        status, detail, pkt = await r.send(action, **kw)
        assert (status, detail) == ("DONE", 0), action
        assert int(pkt.action) == NEW_IDS[action] and pkt.arg_i == kw.get("arg_i", 0)
        assert pkt.arg_f == kw.get("arg_f", 0.0) and pkt.session == 99


async def test_out_of_range_arguments_are_refused_by_the_core_before_anything_is_sent():
    r = Rig(request_simulation=True)
    n = len(r.src.received_requests)
    for action, kw in (
        ("SIM_WIND_SPEED", {"arg_f": 21.0}),
        ("SIM_GUST_PERIOD", {"arg_f": 0.1}),
        ("SIM_FLAP_SLOW", {"arg_i": 0, "arg_f": 0.01}),
        ("SIM_FLAP_STUCK", {"arg_i": 4, "arg_f": 0.5}),
        ("SIM_GUST_SEED", {"arg_i": 16777216}),
        ("SIM_FAULT_CLEAR", {"arg_f": 1.0}),
    ):
        with pytest.raises(RequestError) as e:
            await r.send(action, **kw)
        assert e.value.code == "invalid_value", action
    assert len(r.src.received_requests) == n


async def test_home_position_request_done_and_no_home_is_detail_4():
    assert (await Rig().send("GET_HOME_POSITION"))[:2] == ("DONE", 0)
    assert (await Rig(request_no_home=True).send("GET_HOME_POSITION"))[:2] == ("REJECTED_STATE", 4)


async def test_state_and_json_carry_arg_i_and_rows_with_one_action_are_in_flight_independently():
    r = Rig(request_drop_first=99)  # nothing is answered
    r.clock.advance(1)
    a = await r.rc.send("CALIB_CONTROL", 1, client="c", remote_addr="127.0.0.1", confirm="CALIB_CONTROL", arg_i=1)
    r.clock.advance(1)
    b = await r.rc.send("CALIB_CONTROL", 1, client="c", remote_addr="127.0.0.1", confirm="CALIB_CONTROL", arg_i=11)
    assert a.req_id != b.req_id and {s["arg_i"] for s in r.rc.status()["inflight"]} == {1, 11}
    r.clock.advance(1)
    with pytest.raises(RequestError) as e:  # the same row twice
        await r.rc.send("CALIB_CONTROL", 1, client="c", remote_addr="127.0.0.1", confirm="CALIB_CONTROL", arg_i=1)
    assert e.value.code == "request_in_flight"
    wl = {(w["action"], w["arg_i"]): w for w in r.rc.whitelist()}
    assert wl[("CALIB_CONTROL", 1)]["arg_i_range"] is None
    assert wl[("SIM_FLAP_STUCK", 0)]["arg_i_range"] == {"min": 0, "max": 3, "name": "flap 0..3 = TL TR BL BR"}


def test_request_entry_arg_i_default_is_fixed_zero():
    e = RequestEntry("SAVE_CONFIG", 1, "x", "y")
    assert e.arg_i == 0 and not e.arg_i_ranged
