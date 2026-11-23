"""Run-time source enable/disable (docs/api.md `sources.enable`): a disabled source is a true disconnect.

Against the pty fake vehicles (exvectrnet-1): while disabled not one frame goes either way (no telemetry in, no RC
state, requests or heartbeats out), the request routes drop it, a GsEvent records it, in-flight
requests are aborted, and after a re-enable the RC controller sends the safe defaults first (no stale state).
"""

import asyncio
import contextlib
import sys
import tempfile
from pathlib import Path

from exvectr_gs.core.app import CoreApp
from exvectr_gs.core.config import parse_config

from ..support_pty import FakeVehiclePty, wait_for
from .test_api import Client, make_app, running

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import fake_vehicle_net as net  # noqa: E402

REQUESTS = {"action": [{"action": "CALIB_APPLY", "label": "Apply", "group": "Calibration", "target": 1}]}


def cmd_frames(frames):
    """CMD_STATE frames among the received ones, parsed with the independent strict parser."""
    return [net.parse_cmd_state(f.payload) for f in frames if f.channel == net.PORT_CMD_STATE]


@contextlib.asynccontextmanager
async def rig(tmp_path, usb_enabled=True):
    v = FakeVehiclePty(tmp_path / "usb", speed=2.0, profile="exvectrnet-1", hb_hz=10.0, request_drop_first=99)
    v.start()
    app = CoreApp(
        parse_config(
            {
                "core": {"wire_profile": "exvectrnet-1", "source_priority": ["usb", "fake"]},
                "server": {"bind": "127.0.0.1", "port": 1},
                "source": [
                    {"id": "usb", "kind": "vehicle_usb", "match": {"device": str(v.link)}, "rc_role": "control",
                     "poll_s": 0.05, "enabled": usb_enabled},
                    {"id": "fake", "kind": "fake", "profile": "usb", "rc_role": "none"},
                ],
                "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
                "requests": REQUESTS,
            }
        )
    )  # fmt: skip
    extra = [asyncio.create_task(app.engine.run()), asyncio.create_task(app.requests.run())]
    hb = asyncio.create_task(app._heartbeat_loop())
    try:
        async with running(app) as r:
            r.v = v
            yield r
    finally:
        for t in [*extra, hb]:
            t.cancel()
        for t in [*extra, hb]:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        v.stop()


def usb(app):
    return next(s for s in app.sources if s.id == "usb")


async def test_disable_is_a_true_disconnect_and_reenable_starts_from_safe_defaults(tmp_path):
    async with rig(tmp_path) as r:
        app, v = r.app, r.v
        await wait_for(lambda: usb(app).status().state == "connected", what="connected")
        c = await Client.connect(r)
        await c.ok("rec.start", label="disable-test")
        await c.ok("rc.acquire", kind="connection")

        async def beat():  # the connection lease needs the holder's heartbeat
            while True:
                await c.send({"op": "rc.heartbeat"})
                await asyncio.sleep(0.3)

        beater = asyncio.create_task(beat())
        await c.ok("rc.set", values={"SA": 1})
        await wait_for(lambda: any(f and f["go"] == 1 for f in cmd_frames(v.received)), what="go on the wire")
        await wait_for(lambda: len(v.received_heartbeats) > 0, what="GS heartbeat")
        st = r.api._status_msg()
        assert st["request_routes"]["1"] == "usb"
        # a request in flight (the fake swallows every attempt) is aborted by the disable
        req = await c.ok("request.send", action="CALIB_APPLY", confirm="CALIB_APPLY")
        assert req["state"] == "sending"

        d = await c.ok("sources.enable", source="usb", enabled=False)
        assert d["enabled"] is False and d["state"] == "disabled" and d["disabled_by"] == "user"
        assert usb(app)._conn is None  # the port is closed
        n_rx, n_hb, n_pkts = len(v.received), len(v.received_heartbeats), app.packet_counts["usb"]
        await asyncio.sleep(1.5)
        assert len(v.received) == n_rx, "frames reached the vehicle while the source was disabled"
        assert len(v.received_heartbeats) == n_hb, "heartbeats reached the vehicle while disabled"
        assert app.packet_counts["usb"] == n_pkts, "packets were received from a disabled source"
        st = r.api._status_msg()
        assert st["request_routes"]["1"] is None
        assert await c.err("request.send", action="CALIB_APPLY", confirm="CALIB_APPLY") in ("no_route", "rate_limited")
        rs = await c.ok("request.status")
        assert rs["inflight"] == [] and rs["recent"][0]["state"] == "aborted"
        assert "disabled by user" in rs["recent"][0]["abort_reason"]
        lst = {s["id"]: s for s in await c.ok("sources.list")}
        assert lst["usb"]["state"] == "disabled" and lst["usb"]["disabled_by"] == "user"
        assert lst["fake"]["state"] == "connected"

        # re-enable: a fresh connection; the engine goes on with its own state and session (nothing is replayed from
        # the link), and no stale request is sent
        n_rx = len(v.received)
        await c.ok("sources.enable", source="usb", enabled=True)
        await wait_for(lambda: usb(app).status().state == "connected", what="reconnected")
        await wait_for(lambda: cmd_frames(v.received[n_rx:]), what="CMD_STATE after re-enable")
        after = cmd_frames(v.received[n_rx:])
        assert all(f and f["session"] == app.engine.session for f in after)
        assert not [f for f in v.received[n_rx:] if f.channel == net.PORT_REQUEST], "a stale request was sent"
        assert usb(app).status().disabled_by is None
        beater.cancel()
        info = await c.ok("rec.stop")
        text = Path(info["path"]).read_text()
        assert "usb: DISABLED by user" in text and "usb: enabled by user" in text
        await c.close()


async def test_config_disabled_source_starts_disabled_and_can_be_enabled(tmp_path):
    async with rig(tmp_path, usb_enabled=False) as r:
        app, v = r.app, r.v
        await asyncio.sleep(0.5)
        st = usb(app).status()
        assert st.state == "disabled" and st.disabled_by == "config"
        assert not v.received and app.packet_counts["usb"] == 0
        c = await Client.connect(r)
        await c.ok("sources.enable", source="usb", enabled=True)
        await wait_for(lambda: usb(app).status().state == "connected", what="connected")
        await wait_for(lambda: app.packet_counts["usb"] > 0, what="telemetry")
        await c.close()


async def test_view_only_host_cannot_disable_and_unknown_source_is_not_found():
    async with running(make_app(control_hosts=("10.99.99.99",))) as r:
        c = await Client.connect(r)
        assert await c.err("sources.enable", source="fake", enabled=False) == "control_denied"
        assert await c.err("sources.enable", source="nope", enabled=False) == "not_found"
        assert r.app.sources[0].status().state == "connected"
        await c.close()


async def test_fake_source_disable_stops_its_packets():
    async with running(make_app()) as r:
        c = await Client.connect(r)
        await wait_for(lambda: r.app.packet_counts["fake"] > 0, what="packets")
        await c.ok("sources.enable", source="fake", enabled=False)
        n = r.app.packet_counts["fake"]
        await asyncio.sleep(0.6)
        assert r.app.packet_counts["fake"] == n
        await c.ok("sources.enable", source="fake", enabled=True)
        await wait_for(lambda: r.app.packet_counts["fake"] > n, what="packets again")
        await c.close()
