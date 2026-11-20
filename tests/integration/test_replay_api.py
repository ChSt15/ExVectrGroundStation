"""Replay through the real core: API ops, the replay view, and live recording continuing during replay."""

import asyncio
import time
from pathlib import Path

from exvectr_gs.recording import LogReader
from tests.integration.test_api import Client, make_app, running
from tests.support_logs import T0_NS, make_log

S = 1_000_000_000


async def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < end, "timeout"
        await asyncio.sleep(0.02)


async def take(c: Client, op: str, **match) -> dict:
    """Newest message `op` already received (the snapshot arrives before the result), else wait for one."""
    for m in reversed(c.inbox):
        if m["op"] == op and all(m.get(k) == v for k, v in match.items()):
            c.inbox.remove(m)
            return m
    return await c.expect(op)


async def drain(c: Client, secs: float) -> list[dict]:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        try:
            await c.recv(max(0.01, end - time.monotonic()))
        except TimeoutError:
            break
    return c.inbox


async def test_replay_ops_snapshots_history_markers_and_status():
    app = make_app()
    log = make_log(Path(app.library.directory), "dropouts", 45.0)
    async with running(app, sources=False) as rig:
        c = await Client.connect(rig)
        listing = await c.ok("logs.list")
        assert [i["name"] for i in listing] == [log.name]
        assert await c.err("replay.open", name="missing.exlog") == "not_found"
        assert await c.err("replay.play") == "busy"  # nothing open
        await c.ok("subscribe", view="replay", types="*", mode="latest", max_rate_hz=30)
        await take(c, "snapshot")
        c.inbox.clear()
        c.inbox.clear()
        info = await c.ok("replay.open", name=log.name)
        assert info["name"] == log.name
        snap = await take(c, "snapshot")
        assert snap["view"] == "replay" and snap["items"] and snap["clock"] == T0_NS / 1e9
        st = (await take(c, "status"))["replay"]
        assert st["available"] and st["state"] == "paused" and st["t"] == st["t_start"] == T0_NS / 1e9
        # seek into the Position dropout (15 s .. 19 s): Position is stale there, others are fresh
        c.inbox.clear()
        await c.ok("replay.seek", t=T0_NS / 1e9 + 18)
        snap = await take(c, "snapshot")
        items = {i["type"]: i for i in snap["items"]}
        assert snap["clock"] == T0_NS / 1e9 + 18
        assert snap["clock"] - items["Position"]["t"] > items["Position"]["stale_after"]
        assert snap["clock"] - items["Attitude"]["t"] < items["Attitude"]["stale_after"]
        h = await c.ok(
            "get_history",
            view="replay",
            series=[{"type": "Position", "fields": ["pos"]}],
            t0=T0_NS / 1e9,
            t1=T0_NS / 1e9 + 10,
            max_points=50,
        )
        (s0,) = h["series"]
        assert 0 < len(s0["t"]) <= 50 and all(T0_NS / 1e9 <= t <= T0_NS / 1e9 + 10 for t in s0["t"])
        # plot buffer: full rate read from the log (types without history=True too)
        hp = await c.ok(
            "get_history",
            view="replay",
            buffer="plot",
            series=[{"type": "Position", "fields": ["pos"]}, {"type": "VehicleState", "fields": ["mode"]}],
            t0=T0_NS / 1e9,
            t1=T0_NS / 1e9 + 10,
            max_points=100_000,
        )
        p0, v0 = hp["series"]
        assert len(p0["t"]) > len(s0["t"]) and all(T0_NS / 1e9 <= t <= T0_NS / 1e9 + 10 for t in p0["t"])
        assert p0["t"] == sorted(p0["t"]) and len(p0["pos"]) == len(p0["t"])
        assert v0["t"] and isinstance(v0["mode"][0], str)
        assert await c.err("get_history", buffer="x", series=[], t0=0, t1=1) == "invalid_value"
        live_h = await c.ok("get_history", view="live", series=[{"type": "Position", "fields": ["pos"]}], t0=0, t1=1e12)
        assert live_h["series"][0]["t"] == []  # replay history never reaches the live view
        markers = await c.ok("replay.markers")
        assert markers == [
            {"t": m.t / 1e9, "kind": m.kind, "src": m.src, "label": m.label}
            for m in LogReader(log).build_index().markers
        ]
        await c.ok("replay.speed", x=8)
        c.inbox.clear()
        await c.ok("replay.close")
        snap = await take(c, "snapshot")
        assert snap["items"] == []
        assert (await take(c, "status"))["replay"]["state"] == "closed"
        await c.close()


async def test_play_over_api_streams_to_replay_view_only_and_ends():
    app = make_app()
    log = make_log(Path(app.library.directory), "full", 6.0)
    live_seen = []
    app.bus.subscribe(live_seen.append)
    async with running(app, sources=False) as rig:
        c = await Client.connect(rig)
        await c.ok("subscribe", view="replay", types=["Attitude"], mode="all", max_rate_hz=30)
        await c.ok("subscribe", view="live", types="*", mode="all", max_rate_hz=30)
        await c.ok("replay.open", name=log.name)
        await c.ok("replay.speed", x=8)
        await c.ok("replay.play")
        await wait_for(lambda: app.replayer.status().state == "ended")
        msgs = await drain(c, 0.3)
        replayed = [i for m in msgs if m["op"] == "packets" and m["view"] == "replay" for i in m["items"]]
        assert replayed and all(i["type"] == "Attitude" for i in replayed)
        assert not any(m["op"] == "packets" and m["view"] == "live" for m in msgs)
        assert live_seen == []  # replay never touches the live bus
        await c.ok("replay.play")  # from ended: restarts at the beginning
        assert app.replayer.status().state == "playing" and app.replayer.status().t == app.replayer.status().t_start
        await c.ok("replay.pause")
        await c.close()


async def test_recording_continues_during_replay_and_records_no_replayed_data():
    app = make_app()
    lib_dir = Path(app.library.directory)
    log = make_log(lib_dir, "full", 8.0)
    async with running(app) as rig:  # FakeSource (id "fake") feeds the live bus
        await asyncio.sleep(0.3)
        rec = app.recorder.start("during-replay")
        await app.replayer.open(log.name)
        app.replayer.set_speed(8)
        app.replayer.play()
        before = app.recorder.status().info.packets
        await wait_for(lambda: app.replayer.status().state == "ended")
        await asyncio.sleep(0.3)
        assert app.recorder.status().active and app.recorder.status().info.packets > before  # still recording live data
        app.replayer.seek(T0_NS + 2 * S)
        app.replayer.close()
        app.recorder.stop()
        srcs = {p.src for _, p in LogReader(rec.path).iter_packets()}
        assert srcs and "usb" not in srcs  # "usb" is the replayed log's source; the recording has only "fake"
        assert rig.app.history.query("Position", "usb", ["pos"], 0, 1 << 62, 0)["t"] == []
