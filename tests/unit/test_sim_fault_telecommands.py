"""Simulation servo-fault telecommands (types 16..18, docs/telecommands.md): whitelist, exact bytes, rate limit."""

import math
import struct
import tomllib
from pathlib import Path

import pytest

from exvectr_gs.core.config import TelecommandsSection, parse_config
from exvectr_gs.packets.defs.link import Telecommand, TelecommandType
from exvectr_gs.telecommand import TelecommandError
from exvectr_gs.wire.exvectrnet_1.profile import ExVectrNet1Profile

from .test_telecommand_controller import Rig

CFG = Path(__file__).parents[2] / "config"
FLAPS = ["TL", "TR", "BL", "BR"]


def entries(name):
    c = parse_config(tomllib.loads((CFG / name).read_text()))
    return {e.name: e for e in c.telecommands.commands}


@pytest.mark.parametrize("cfg", ["gs.example.toml", "gs.pocket.toml"])
def test_shipped_fault_entries(cfg):
    names = entries(cfg)
    assert int(TelecommandType.SimFlapStuck) == 16
    assert int(TelecommandType.SimFlapSlow) == 17
    assert int(TelecommandType.SimFaultClear) == 18
    assert int(TelecommandType.SetStopAltitude) == 19
    s = names["SetStopAltitude"]
    got = (s.type, s.group, s.param_float, s.param_float_min, s.param_float_max)
    assert got == ("SetStopAltitude", "Vehicle settings", 20.0, 1.0, 1000000.0)
    assert not [n for n in names if n.endswith("Descent")]  # the arm-for-descent bit is gone (the vehicle refuses it)
    for i, f in enumerate(FLAPS):
        e = names[f"SimFlapStuck{f}"]
        assert (e.type, e.subsystem, e.param_int, e.group, e.rate_group, e.confirm) == (
            "SimFlapStuck", i, 0x57, "Simulation tests", "", "hold",
        )  # fmt: skip
        assert e.param_float_min == -1.0 and e.param_float_max >= math.pi / 2
    for i, f in enumerate(FLAPS):
        e = names[f"SimFlapSlow{f}"]
        assert (e.type, e.subsystem, e.param_float_min, e.param_float_max) == ("SimFlapSlow", i, 0.05, 1.0)
    c = names["SimFaultClear"]
    assert (c.type, c.subsystem, c.param_int, c.param_float_min) == ("SimFaultClear", 0, 0x57, None)
    sim = [e for e in names.values() if e.group == "Simulation tests"]
    assert len(sim) == 9 and all(e.rate_group == "" for e in sim)  # the wind group exception is not widened


@pytest.mark.parametrize(
    ("typ", "sub", "value", "hexbytes"),
    [
        ("SimFlapStuck", 0, math.radians(42), "10 00 57 00 66 A8 3B 3F"),  # TL forced to 42 deg
        ("SimFlapStuck", 3, -1.0, "10 03 57 00 00 00 80 BF"),  # BR hold the current angle
        ("SimFlapStuck", 0, -1.0, "10 00 57 00 00 00 80 BF"),  # stick at the current angle
        ("SimFlapSlow", 0, 0.3, "11 00 57 00 9A 99 99 3E"),
        ("SimFaultClear", 0, 0.0, "12 00 57 00 00 00 00 00"),
        ("SetStopAltitude", 0, 25.0, "13 00 57 00 00 00 C8 41"),
    ],
)
def test_exact_wire_bytes(typ, sub, value, hexbytes):
    pkt = Telecommand(t=1, src="gs", tc_type=TelecommandType[typ], subsystem=sub, param_int=0x57, param_float=value)
    f = ExVectrNet1Profile().encode(pkt)
    assert f.channel == 0x7F and f.payload == bytes.fromhex(hexbytes.replace(" ", ""))
    assert f.payload == struct.pack("<BBhf", int(TelecommandType[typ]), sub, 0x57, value)


async def rig():
    r = Rig()
    r.tc = type(r.tc)(
        TelecommandsSection(commands=list(entries("gs.example.toml").values())),
        [r.link, r.usb],
        r.bus,
        clock=r.clock,
        control_hosts=["127.0.0.1"],
    )
    return r


async def test_sent_once_with_flap_subsystem_and_500ms_rule():
    r = await rig()
    await r.send("SimFlapStuckBR", param_float=-1.0)
    pkt = r.usb.sent[0]
    assert (int(pkt.tc_type), pkt.subsystem, pkt.param_int) == (16, 3, 0x57)
    r.clock.advance(0.15)  # no group exception: 150 ms is too fast
    with pytest.raises(TelecommandError) as e:
        await r.send("SimFaultClear")
    assert e.value.code == "rate_limited"
    r.clock.advance(0.4)
    await r.send("SimFaultClear")
    assert len(r.usb.sent) == 2
    with pytest.raises(TelecommandError):  # out of range: refused by the core
        r.clock.advance(1.0)
        await r.send("SimFlapSlowTL", param_float=1.5)
