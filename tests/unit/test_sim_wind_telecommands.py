"""Simulation wind telecommands (types 11..15, docs/telecommands.md): whitelist, exact bytes, group rate limit."""

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
WIND = ["SimWindSpeed", "SimWindDirection", "SimGustStrength", "SimGustPeriod", "SimGustSeed"]


def entries(name):
    c = parse_config(tomllib.loads((CFG / name).read_text()))
    return {e.name: e for e in c.telecommands.commands}


@pytest.mark.parametrize("cfg", ["gs.example.toml", "gs.pocket.toml"])
def test_shipped_wind_entries(cfg):
    names = entries(cfg)
    for i, n in enumerate(WIND):
        e = names[n]
        assert (e.type, e.subsystem, e.param_int, e.group, e.rate_group) == (n, 0, 0x57, "Simulation wind", "simwind")
        assert int(TelecommandType[n]) == 11 + i
    assert (names["SimWindSpeed"].param_float_min, names["SimWindSpeed"].param_float_max) == (0.0, 20.0)
    assert names["SimGustPeriod"].param_float_min == 0.5 and names["SimGustSeed"].param_float_max == 16777215.0
    assert names["SimWindDirection"].param_float_max >= math.pi
    assert all(e.rate_group == "" for n, e in names.items() if n not in WIND)  # nothing else widened


@pytest.mark.parametrize(
    ("name", "value", "hexbytes"),
    [
        ("SimWindSpeed", 5.0, "0B 00 57 00 00 00 A0 40"),
        ("SimWindDirection", math.pi / 2, "0C 00 57 00 DB 0F C9 3F"),
        ("SimGustStrength", 3.0, "0D 00 57 00 00 00 40 40"),
        ("SimGustPeriod", 4.0, "0E 00 57 00 00 00 80 40"),
        ("SimGustSeed", 42.0, "0F 00 57 00 00 00 28 42"),
    ],
)
def test_exact_wire_bytes(name, value, hexbytes):
    pkt = Telecommand(t=1, src="gs", tc_type=TelecommandType[name], subsystem=0, param_int=0x57, param_float=value)
    f = ExVectrNet1Profile().encode(pkt)
    assert f.channel == 0x7F and f.payload == bytes.fromhex(hexbytes.replace(" ", ""))
    assert f.payload == struct.pack("<BBhf", int(TelecommandType[name]), 0, 0x57, value)


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


async def test_group_spacing_100ms_and_everything_else_stays_500ms():
    r = await rig()
    await r.send("SimWindSpeed", param_float=5.0)
    with pytest.raises(TelecommandError) as e:  # too fast even for the group
        await r.send("SimWindDirection", param_float=1.0)
    assert e.value.code == "rate_limited"
    for n in WIND[1:]:
        r.clock.advance(0.15)
        await r.send(n)
    assert len(r.usb.sent) == 5
    r.clock.advance(0.15)  # a non-group command after a group send still needs 500 ms
    with pytest.raises(TelecommandError):
        await r.send("Arm")
    r.clock.advance(0.5)
    await r.send("Arm")
    with pytest.raises(TelecommandError):  # and the group after a non-group command too
        r.clock.advance(0.15)
        await r.send("SimWindSpeed")


async def test_wind_values_are_range_checked_and_non_finite_refused():
    r = await rig()
    for v in (21.0, -0.1, math.nan, math.inf):
        r.clock.advance(1)
        with pytest.raises(TelecommandError) as e:
            await r.send("SimWindSpeed", param_float=v)
        assert e.value.code == "invalid_value"
    r.clock.advance(1)
    with pytest.raises(TelecommandError):
        await r.send("SimWindSpeed", param=5)  # the int16 magic is fixed
