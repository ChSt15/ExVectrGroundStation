"""TEMPORARY legacy telecommand sender (docs/telecommands.md): rules with a fake clock and stub sources."""

import struct

import pytest

from exvectr_gs.core import Bus
from exvectr_gs.core.clock import FakeClock
from exvectr_gs.core.config import ConfigError, TelecommandEntry, TelecommandsSection, parse_config
from exvectr_gs.packets.defs.host import GsEventKind
from exvectr_gs.packets.defs.link import TelecommandType
from exvectr_gs.sources.base import SourceCaps, SourceKind, SourceStatus
from exvectr_gs.telecommand import TelecommandController, TelecommandError
from exvectr_gs.wire.exvectrnet_1.profile import ExVectrNet1Profile

S = 1_000_000_000

WL = [
    TelecommandEntry("Arm", "Arm", "Arm", "Vehicle", 0, 0xA5),
    TelecommandEntry("Disarm", "Arm", "Disarm", "Vehicle", 0, 0),
    TelecommandEntry("SimulationMode", "SimulationMode", "Sim toggle", "Vehicle", 0, 0xA9),
    TelecommandEntry(
        "ZeroEstimators",
        "ZeroEstimators",
        "Zero",
        "Vehicle",
        0,
        0x2E,
        None,
        None,
        "hold",
        "",
        10.0,
        1.0,
        60.0,
        "duration_s",
    ),
    TelecommandEntry("MissionBegin", "MissionBegin", "Begin", "Mission", 0, -10, -3600, 3600),
]


class Stub:
    def __init__(self, sid, kind, targets, state="connected"):
        self.id, self.kind, self.state, self.accept = sid, kind, state, True
        self.caps = SourceCaps(rc_role="none", can_send=True, request_targets=targets)
        self.sent = []

    async def send(self, pkt):
        if not self.accept:
            return False
        self.sent.append(pkt)
        return True

    def status(self):
        return SourceStatus(self.id, self.kind, True, self.state, None, 0, 0, None)  # type: ignore[arg-type]


class Rig:
    def __init__(self, hosts=("127.0.0.1",)):
        self.clock = FakeClock(1000 * S)
        self.bus = Bus()
        self.events = []
        self.bus.subscribe(self.events.append, {"GsEvent"})
        self.usb = Stub("usb", SourceKind.VEHICLE_USB, (1,))
        self.link = Stub("link", SourceKind.LINK_MODULE, (1, 2, 3))
        self.tc = TelecommandController(
            TelecommandsSection(commands=list(WL)), [self.link, self.usb], self.bus,
            clock=self.clock, control_hosts=list(hosts),
        )  # fmt: skip

    async def send(self, name="Arm", addr="127.0.0.1", **kw):
        kw.setdefault("confirm", name)
        return await self.tc.send(name, client="c", remote_addr=addr, **kw)


async def test_one_frame_to_the_vehicle_usb_first_and_a_log_event():
    r = Rig()
    rec = await r.send("Arm")
    assert rec.source == "usb" and rec.param_int == 0xA5 and r.link.sent == []
    [pkt] = r.usb.sent
    assert (pkt.TYPE, pkt.tc_type, pkt.param_int) == ("Telecommand", TelecommandType.Arm, 0xA5)
    assert [e.kind for e in r.events] == [GsEventKind.telecommand_sent] and "once" in r.events[0].detail


async def test_no_retry_no_repeat_however_long_we_wait():
    r = Rig()
    await r.send("SimulationMode")
    r.clock.advance(60)
    assert len(r.usb.sent) == 1 and r.tc.sent_count == 1
    assert not hasattr(r.tc, "tick") and not hasattr(r.tc, "run")


async def test_falls_back_to_the_router_when_usb_is_down_and_refuses_without_route():
    r = Rig()
    r.usb.state = "searching"
    assert (await r.send("Arm")).source == "link"
    r.link.state = "error"
    r.clock.advance(1)
    with pytest.raises(TelecommandError) as e:
        await r.send("Arm")
    assert e.value.code == "no_route" and r.tc.sent_count == 1  # nothing queued


async def test_a_source_that_refuses_gives_no_route_and_is_not_counted():
    r = Rig()
    r.usb.accept = r.link.accept = False
    with pytest.raises(TelecommandError) as e:
        await r.send("Arm")
    assert e.value.code == "no_route" and r.tc.status() == {"recent": []}


async def test_whitelist_confirmation_and_control_host_rules():
    r = Rig()
    for kw, code in [
        ({"name": "Format"}, "not_whitelisted"),
        ({"name": "Arm", "confirm": None}, "confirm_required"),
        ({"name": "Arm", "confirm": "Disarm"}, "confirm_required"),
        ({"name": "Arm", "addr": "10.0.0.9"}, "control_denied"),
    ]:
        with pytest.raises(TelecommandError) as e:
            await r.send(**kw)
        assert e.value.code == code
    assert r.usb.sent == [] and r.link.sent == []


async def test_rate_limit_is_per_client_host_and_500ms():
    r = Rig(hosts=("127.0.0.1", "127.0.0.2"))
    await r.send("Arm")
    with pytest.raises(TelecommandError) as e:
        await r.send("Disarm")
    assert e.value.code == "rate_limited"
    await r.send("Disarm", addr="127.0.0.2")  # another host is independent
    r.clock.advance(0.51)
    await r.send("Disarm")
    assert len(r.usb.sent) == 3


async def test_parameter_only_within_the_configured_range():
    r = Rig()
    assert (await r.send("MissionBegin")).param_int == -10  # default
    for p, code in [(5000, "invalid_value"), (True, "invalid_value"), (1.5, "invalid_value")]:
        r.clock.advance(1)
        with pytest.raises(TelecommandError) as e:
            await r.send("MissionBegin", param=p)
        assert e.value.code == code
    r.clock.advance(1)
    assert (await r.send("MissionBegin", param=-30)).param_int == -30
    r.clock.advance(1)
    with pytest.raises(TelecommandError) as e:
        await r.send("Arm", param=0xA5)  # a fixed parameter cannot be chosen by a client
    assert e.value.code == "invalid_value"


async def test_float_parameter_only_within_range_and_default_is_ten_seconds():
    r = Rig()
    rec = await r.send("ZeroEstimators")
    assert (rec.param_int, rec.param_float) == (0x2E, 10.0)
    assert r.usb.sent[-1].param_float == 10.0 and r.usb.sent[-1].tc_type == TelecommandType.ZeroEstimators
    for pf in (0.5, 61, float("nan"), float("inf"), True, "5"):
        r.clock.advance(1)
        with pytest.raises(TelecommandError) as e:
            await r.send("ZeroEstimators", param_float=pf)
        assert e.value.code == "invalid_value"
    r.clock.advance(1)
    assert (await r.send("ZeroEstimators", param_float=30)).param_float == 30.0
    assert r.usb.sent[-1].param_float == 30.0 and len(r.usb.sent) == 2  # refusals sent nothing
    r.clock.advance(1)
    with pytest.raises(TelecommandError) as e:
        await r.send("Arm", param_float=5.0)  # no float range: a client cannot choose one
    assert e.value.code == "invalid_value"
    assert r.tc.status()["recent"][0]["param_float"] == 30.0
    w = {x["name"]: x for x in r.tc.whitelist()}["ZeroEstimators"]
    assert (w["param_float"], w["param_float_min"], w["param_float_max"], w["param_float_name"]) == (
        10.0, 1.0, 60.0, "duration_s")  # fmt: skip


async def test_status_records_the_send_without_any_result_field():
    r = Rig()
    await r.send("Arm")
    [d] = r.tc.status()["recent"]
    assert d["name"] == "Arm" and d["source"] == "usb" and d["t"] == 1000.0
    assert not {"state", "status", "status_name", "acked"} & set(d)


def test_profile_encodes_the_exact_legacy_frame_little_endian():
    from exvectr_gs.packets.defs.link import Telecommand

    p = ExVectrNet1Profile()
    f = p.encode(
        Telecommand(t=1, src="gs", tc_type=TelecommandType.MissionBegin, subsystem=0, param_int=-10, param_float=0.0)
    )
    assert f.channel == 0x7F and f.payload == struct.pack("<BBhf", 7, 0, -10, 0.0) and len(f.payload) == 8
    assert f.payload == bytes([7, 0, 0xF6, 0xFF, 0, 0, 0, 0])
    assert (f.meta["dst"], f.meta["qos"], f.meta["latest"]) == (1, 2, False)
    wire = p.make_framer().encode(f)  # CTRL byte 0x48, TTL 3, DST 1, SRC 0, PORT 0x7F
    from exvectr_gs.wire.provisional_1.framing import cobs_decode

    net = cobs_decode(wire[:-1])[1:-2]
    assert net[:6] == bytes([0x48, 3, 1, 0, 0x7F, 0]) and net[6:] == f.payload
    f2 = p.encode(Telecommand(t=2, src="gs", tc_type=TelecommandType.Arm, subsystem=0, param_int=0xA5, param_float=0.0))
    assert f2.meta["seq"] == 1  # the sequence counts per command


def test_zero_estimators_frame_is_the_exact_eight_bytes():
    from exvectr_gs.packets.defs.link import Telecommand

    f = ExVectrNet1Profile().encode(
        Telecommand(
            t=1, src="gs", tc_type=TelecommandType.ZeroEstimators, subsystem=0, param_int=0x2E, param_float=10.0
        )
    )
    assert f.channel == 0x7F and f.payload == bytes([10, 0, 0x2E, 0, 0x00, 0x00, 0x20, 0x41])  # 10.0f = 0x41200000
    assert f.payload == struct.pack("<BBhf", 10, 0, 0x2E, 10.0)


def cfg(**tc):
    return parse_config(
        {"core": {"source_priority": ["a"]}, "source": [{"id": "a", "kind": "fake"}], "telecommands": tc}
    )


def test_config_validation():
    ok = cfg(command=[{"name": "A", "type": "Arm", "param_int": 165}])
    assert ok.telecommands.commands[0].confirm == "hold"
    bad = [
        {"command": [{"name": "A", "type": "Nope"}]},
        {"command": [{"name": "A", "type": "Arm"}, {"name": "A", "type": "Arm"}]},
        {"command": [{"name": "A", "type": "Arm", "param_int": 70000}]},
        {"command": [{"name": "A", "type": "Arm", "param_min": 0}]},
        {"command": [{"name": "A", "type": "Arm", "param_int": 5, "param_min": 0, "param_max": 1}]},
        {"command": [{"name": "A", "type": "Arm", "subsystem": 9}]},
        {"command": [{"name": "A", "type": "Arm", "confirm": "danger"}]},
        {"command": [{"name": "A", "type": "Arm", "extra": 1}]},
        {"source_preference": ["zzz"]},
        {"bogus": 1},
        {"command": [{"name": "A", "type": "Arm", "param_float_min": 1.0}]},
        {"command": [{"name": "A", "type": "Arm", "param_float": 70.0, "param_float_min": 1, "param_float_max": 60}]},
        {"command": [{"name": "A", "type": "Arm", "param_float": float("nan")}]},
        {"command": [{"name": "A", "type": "Arm", "param_float": True}]},
    ]
    for b in bad:
        with pytest.raises(ConfigError):
            cfg(**b)


def test_shipped_configs_whitelist_the_expected_commands():
    import tomllib
    from pathlib import Path

    for name in ("gs.example.toml", "gs.pocket.toml"):
        c = parse_config(tomllib.loads((Path(__file__).parents[2] / "config" / name).read_text()))
        names = {e.name: e for e in c.telecommands.commands}
        assert {"Arm", "Disarm", "SystemReset", "SimulationMode", "MissionBegin", "MissionAbort"} <= set(names)
        assert names["Arm"].param_int == 0xA5 and names["Disarm"].param_int == 0
        assert names["SystemReset"].param_int == 0xC5 and names["SimulationMode"].param_int == 0xA9
        assert all(e.confirm == "hold" for e in names.values())
        z = names["ZeroEstimators"]
        assert (z.type, z.subsystem, z.param_int) == ("ZeroEstimators", 0, 0x2E)
        assert (z.param_float, z.param_float_min, z.param_float_max, z.param_float_name) == (
            10.0,
            1.0,
            60.0,
            "duration_s",
        )
        assert "NOT zero estimators" in names["SystemReset"].label
        reb = {(e.action, e.target): e for e in c.requests.actions if e.action == "LINK_REBOOT"}
        assert set(reb) == {("LINK_REBOOT", 2), ("LINK_REBOOT", 3)}
        assert all(e.confirm == "danger" and e.warning for e in reb.values())
