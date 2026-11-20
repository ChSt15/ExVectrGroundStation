"""TEMPORARY legacy telecommand over the exvectrnet-1 wire against the pty fake vehicle (an independent decoder
that flips its own `simulation` / `armed` status). The fake's effects are test doubles, not vehicle logic."""

import asyncio
import contextlib
import tempfile

from exvectr_gs.core.app import CoreApp
from exvectr_gs.core.config import parse_config

from ..support_pty import FakeVehiclePty, wait_for
from .test_api import Client
from .test_api import running as api_running

COMMANDS = [
    {"name": "Arm", "type": "Arm", "param_int": 0xA5},
    {"name": "Disarm", "type": "Arm", "param_int": 0},
    {"name": "SimulationMode", "type": "SimulationMode", "param_int": 0xA9},
    {
        "name": "ZeroEstimators",
        "type": "ZeroEstimators",
        "param_int": 0x2E,
        "param_float": 10.0,
        "param_float_min": 1.0,
        "param_float_max": 60.0,
        "param_float_name": "duration_s",
    },
    {"name": "MissionBegin", "type": "MissionBegin", "param_int": -10, "param_min": -3600, "param_max": 3600},
]


def make_vehicle(tmp_path, name, role="vehicle"):
    v = FakeVehiclePty(tmp_path / name, speed=2.0, profile="exvectrnet-1", role=role, hb_hz=10.0)
    v.start()
    return v


def make_app(v1, v2, usb=True, hosts=("127.0.0.1", "::1")):
    sources = [
        {"id": "link", "kind": "link_module", "match": {"device": str(v2.link)}, "rc_role": "mirror", "poll_s": 0.05}
    ]
    if usb:
        sources.append(
            {
                "id": "usb",
                "kind": "vehicle_usb",
                "match": {"device": str(v1.link)},
                "rc_role": "control",
                "poll_s": 0.05,
            }
        )
    return CoreApp(
        parse_config(
            {
                "core": {
                    "wire_profile": "exvectrnet-1",
                    "source_priority": sorted((x["id"] for x in sources), key=lambda i: i != "usb"),
                },
                "server": {"bind": "127.0.0.1", "port": 1, "control_hosts": list(hosts)},
                "source": sources,
                "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
                "telecommands": {"command": COMMANDS},
            }
        )
    )


@contextlib.asynccontextmanager
async def started(app):
    async with api_running(app) as rig:
        await wait_for(lambda: all(s.status().state == "connected" for s in app.sources), what="sources")
        yield rig


def reported(app):
    e = app.store.get("VehicleStatus")
    return None if e is None else (e.packet.simulation, e.packet.armed)


async def test_frames_decode_on_port_7f_and_the_reported_status_flips(tmp_path):
    v1, v2 = make_vehicle(tmp_path, "usb"), make_vehicle(tmp_path, "radio", role="txmodule")
    try:
        app = make_app(v1, v2)
        async with started(app) as rig:
            c = await Client.connect(rig)
            await wait_for(lambda: reported(app) is not None, what="VehicleStatus")
            sim0 = reported(app)[0]
            r = await c.ok("telecommand.send", name="SimulationMode", confirm="SimulationMode")
            assert r["source"] == "usb" and "state" not in r
            await wait_for(lambda: v1.received_telecommands, what="telecommand at the vehicle")
            tc = v1.received_telecommands[0]
            assert (tc["type"], tc["subsystem"], tc["param_int"], tc["dst"], tc["src"]) == (6, 0, 0xA9, 1, 0)
            assert tc["ctrl"] == 0x48 and tc["ttl"] == 3
            await wait_for(lambda: reported(app)[0] == (not sim0), what="simulation flipped as reported")
            await asyncio.sleep(1.5)  # no automatic repeat: still exactly one frame, the state stays flipped
            assert len(v1.received_telecommands) == 1 and reported(app)[0] == (not sim0)
            await asyncio.sleep(0.5)
            await c.ok("telecommand.send", name="Arm", confirm="Arm")
            await wait_for(lambda: reported(app)[1] is True, what="armed as reported")
            await asyncio.sleep(0.55)
            await c.ok("telecommand.send", name="Disarm", confirm="Disarm")
            await wait_for(lambda: reported(app)[1] is False, what="disarmed as reported")
            await asyncio.sleep(0.55)
            await c.ok("telecommand.send", name="MissionBegin", confirm="MissionBegin", param=-30)
            await wait_for(lambda: len(v1.received_telecommands) == 4, what="mission begin")
            assert (v1.received_telecommands[3]["type"], v1.received_telecommands[3]["param_int"]) == (7, -30)
            assert v2.received_telecommands == []
            seqs = [t["seq"] for t in v1.received_telecommands]
            assert seqs == [0, 1, 2, 3]
            await c.close()
    finally:
        v1.stop()
        v2.stop()


async def test_goes_over_the_router_when_the_vehicle_usb_is_absent(tmp_path):
    v1, v2 = make_vehicle(tmp_path, "usb"), make_vehicle(tmp_path, "radio", role="txmodule")
    try:
        async with started(make_app(v1, v2, usb=False)) as rig:
            c = await Client.connect(rig)
            r = await c.ok("telecommand.send", name="Arm", confirm="Arm")
            assert r["source"] == "link"
            await wait_for(lambda: v2.received_telecommands, what="telecommand at the Router")
            assert v2.received_telecommands[0]["dst"] == 1  # addressed to the vehicle, the Router forwards it
            assert v1.received_telecommands == []
            await c.close()
    finally:
        v1.stop()
        v2.stop()


async def test_api_refusals_send_nothing_and_a_view_only_host_is_denied(tmp_path):
    v1, v2 = make_vehicle(tmp_path, "usb"), make_vehicle(tmp_path, "radio", role="txmodule")
    try:
        async with started(make_app(v1, v2)) as rig:
            c = await Client.connect(rig)
            assert await c.err("telecommand.send", name="Arm") == "confirm_required"
            assert await c.err("telecommand.send", name="Arm", confirm="Disarm") == "confirm_required"
            assert await c.err("telecommand.send", name="Format", confirm="Format") == "not_whitelisted"
            assert await c.err("telecommand.send", name="Arm", confirm="Arm", param=1) == "invalid_value"
            lst = await c.ok("telecommand.list")
            assert [e["name"] for e in lst["commands"]] == [
                "Arm",
                "Disarm",
                "SimulationMode",
                "ZeroEstimators",
                "MissionBegin",
            ]
            assert lst["commands"][0]["route"] == "usb"
            await c.ok("telecommand.send", name="Arm", confirm="Arm")
            assert await c.err("telecommand.send", name="Disarm", confirm="Disarm") == "rate_limited"
            await asyncio.sleep(0.5)
            assert len(v1.received_telecommands) == 1
            await c.close()
        async with started(make_app(v1, v2, hosts=("10.9.9.9",))) as rig:
            c = await Client.connect(rig)
            assert await c.err("telecommand.send", name="Arm", confirm="Arm") == "control_denied"
            await c.close()
    finally:
        v1.stop()
        v2.stop()


def zeroing(app):
    e = app.store.get("VehicleStatus")
    return None if e is None else e.packet.zeroing


async def test_zero_estimators_frame_decodes_and_the_reported_zeroing_flag_sets_then_clears(tmp_path, zeroing_mask):
    v1, v2 = make_vehicle(tmp_path, "usb"), make_vehicle(tmp_path, "radio", role="txmodule")
    try:
        app = make_app(v1, v2)
        async with started(app) as rig:
            c = await Client.connect(rig)
            await wait_for(lambda: zeroing(app) is False, what="VehicleStatus with the flag clear")
            r = await c.ok("telecommand.send", name="ZeroEstimators", confirm="ZeroEstimators", param_float=1.0)
            assert r["source"] == "usb" and r["param_float"] == 1.0 and "state" not in r
            await wait_for(lambda: v1.received_telecommands, what="telecommand at the vehicle")
            tc = v1.received_telecommands[0]  # independent decoder
            assert (tc["type"], tc["subsystem"], tc["param_int"], tc["param_float"]) == (10, 0, 0x2E, 1.0)
            assert (tc["dst"], tc["src"], tc["ctrl"]) == (1, 0, 0x48)
            await wait_for(lambda: zeroing(app) is True, what="ZEROING as reported")
            await wait_for(lambda: zeroing(app) is False, timeout=5, what="ZEROING cleared by the vehicle")
            assert len(v1.received_telecommands) == 1  # no repeat
            await asyncio.sleep(0.55)
            r = await c.ok("telecommand.send", name="ZeroEstimators", confirm="ZeroEstimators")  # default 10 s
            assert r["param_float"] == 10.0
            await wait_for(lambda: len(v1.received_telecommands) == 2, what="second frame")
            assert v1.received_telecommands[1]["param_float"] == 10.0
            await c.close()
    finally:
        v1.stop()
        v2.stop()
