"""`get_rates`: fake source (packet level), pty fake vehicles (frame level, real paths) and replay (replay time)."""

import asyncio
import tempfile

from exvectr_gs.core.app import CoreApp
from exvectr_gs.core.config import parse_config

from ..support_logs import T0_NS, make_log
from ..support_pty import FakeVehiclePty
from .test_api import Client, make_app, running

S = 1_000_000_000


async def test_live_fake_source_is_packet_level_and_simulated():
    async with running() as rig:
        c = await Client.connect(rig)
        await asyncio.sleep(1.0)
        d = await c.ok("get_rates")
        assert d["view"] == "live" and d["measure"] == "frames" and d["windows_s"] == [2.0, 10.0]
        rows = {r["type"]: r for r in d["streams"]}
        assert {"Attitude", "Position", "VehicleState"} <= set(rows)
        att = rows["Attitude"]
        assert att["source"] == "fake" and att["path"] == "simulated" and att["node"] is None
        assert att["rate_hz"][0] > 5 and att["bytes_s"] is None and att["nominal_hz"]
        assert "LinkStats" not in rows and "GsEvent" not in rows
        assert d["paths"][0]["path"] == "simulated" and d["air"]["utilisation"] is None
        assert d["air"]["capacity_bytes_s"] == 6100.0
        assert await c.err("get_rates", view="nope") == "bad_request"
        await c.close()


async def test_live_pty_sources_label_paths_and_count_wire_bytes(tmp_path):
    usb = FakeVehiclePty(tmp_path / "usb", speed=2.0, profile="exvectrnet-1", hb_hz=10.0)
    radio = FakeVehiclePty(
        tmp_path / "radio", speed=2.0, profile="exvectrnet-1", role="txmodule", hb_hz=10.0,
        rates={"VEHICLE_STATUS": 10.0, "LINK_STATS_TX": 4.0, "LINK_STATS_RX": 4.0},
    )  # fmt: skip
    usb.start()
    radio.start()
    app = CoreApp(
        parse_config(
            {
                "core": {"wire_profile": "exvectrnet-1", "source_priority": ["usb", "link"]},
                "server": {"bind": "127.0.0.1", "port": 1},
                "source": [
                    {"id": "usb", "kind": "vehicle_usb", "rc_role": "none", "poll_s": 0.05,
                     "match": {"device": str(usb.link)}},
                    {"id": "link", "kind": "link_module", "rc_role": "mirror", "poll_s": 0.05,
                     "match": {"device": str(radio.link)}},
                ],
                "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
            }
        )
    )  # fmt: skip
    try:
        async with running(app) as rig:
            c = await Client.connect(rig)

            async def rows():
                return await c.ok("get_rates")

            async def have():
                d = await rows()
                got = {(r["source"], r["type"]): r for r in d["streams"]}
                return ("usb", "POSE") in got and any(k[0] == "link" for k in got)

            end = asyncio.get_running_loop().time() + 8
            while not await have():
                assert asyncio.get_running_loop().time() < end, "no streams"
                await asyncio.sleep(0.2)
            await asyncio.sleep(2.5)
            d = await rows()
            got = {(r["source"], r["type"]): r for r in d["streams"]}
            pose = got[("usb", "POSE")]
            assert pose["path"] == "usb direct" and pose["node"] == 1 and pose["nominal_hz"] == 100.0
            assert pose["rate_hz"][0] > 20 and pose["bytes_s"][0] > pose["payload_s"][0] > 0
            link = [r for r in d["streams"] if r["source"] == "link"]
            by_node = {(r["node"], r["path"]) for r in link}
            assert any(p == "air" and n in (1, 3) for n, p in by_node), by_node
            assert all(p == "router local" for n, p in by_node if n == 2), by_node
            assert all(p != "air" for n, p in by_node if n == 2)
            assert [p["path"] for p in d["paths"]] == ["air", "usb direct", "router local"]
            air = d["air"]
            assert air["payload_s"][0] > 0 and 0 < air["utilisation"][0] < 1
            await c.close()
    finally:
        usb.stop()
        radio.stop()


async def test_replay_rates_are_in_replay_time_and_follow_a_seek():
    app = make_app()
    log = make_log(app.library.directory, "full", 12.0)
    async with running(app, sources=False) as rig:
        c = await Client.connect(rig)
        empty = await c.ok("get_rates", view="replay")
        assert empty["view"] == "replay" and empty["measure"] == "packets" and empty["streams"] == []
        await c.ok("replay.open", name=log.name)
        await c.ok("replay.seek", t=T0_NS / 1e9 + 8)
        d = await c.ok("get_rates", view="replay")
        assert abs(d["now"] - (T0_NS / 1e9 + 8)) < 1e-6  # the replay clock, not the wall clock
        rows = {r["type"]: r for r in d["streams"] if r["source"] == "usb"}
        assert {"Attitude", "Position"} <= set(rows)
        assert rows["Attitude"]["rate_hz"][0] > 1 and rows["Attitude"]["bytes_s"] is None
        assert (
            rows["Attitude"]["path"] == "simulated" and rows["Attitude"]["age_s"] < 1.0
        )  # a fake source in the log header
        await c.ok("replay.seek", t=T0_NS / 1e9 + 1)
        d1 = await c.ok("get_rates", view="replay")
        assert abs(d1["now"] - (T0_NS / 1e9 + 1)) < 1e-6
        assert {r["type"] for r in d1["streams"]}
        live = await c.ok("get_rates")
        assert all(r["source"] != "usb" for r in live["streams"])  # replay never reaches the live statistics
        await c.close()
