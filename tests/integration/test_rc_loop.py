"""RC loop end to end: API with the FakeSource echo, control_hosts, disconnects, mirror, and the pty fake vehicle.

Never touches real hardware. Recordings go to temp dirs."""

import asyncio
import contextlib
import struct
import tempfile
import time

import pytest

from exvectr_gs.core.app import CoreApp
from exvectr_gs.core.config import parse_config
from exvectr_gs.packets.defs.link import RcOrigin
from exvectr_gs.wire.provisional_1 import layouts

from ..support_pty import FakeVehiclePty, wait_for
from .test_api import Client, running


def make_rc_app(control_hosts=("127.0.0.1", "::1"), rc_role="control", **fake):
    cfg = parse_config(
        {
            "core": {"source_priority": ["fake"]},
            "server": {"bind": "127.0.0.1", "port": 1, "control_hosts": list(control_hosts)},
            "source": [{"id": "fake", "kind": "fake", "rc_role": rc_role, "echo_rc": True, "seed": 1, **fake}],
            "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
        }
    )
    return CoreApp(cfg)


@contextlib.asynccontextmanager
async def rc_running(app=None):
    app = app or make_rc_app()
    async with running(app) as rig:
        task = asyncio.create_task(app.rc.run())
        try:
            yield rig
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def received(rig):
    return len(rig.app.sources[0].received_rc)


async def applied(c, expect, timeout=3.0):
    """Wait until the echoed MissionState.mission_index (the fake vehicle's 'applied' value) equals `expect`."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snap = await c.ok("get_snapshot", view="live", types=["MissionState"], src="fake")
        items = snap["items"] if isinstance(snap, dict) else snap
        if items and items[0]["f"].get("mission_index") == expect:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"applied value never became {expect}")


async def test_round_trip_through_api_and_fake_echo():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        lease = await c.ok("rc.acquire", kind="connection")
        assert lease["kind"] == "connection" and lease["expires"] is None
        st = await c.ok("rc.set", values={"SA": 1, "S1": 0.5})
        assert st["mode"] == "controlling" and st["controls"]["SA"] == 1 and st["channels_us"][9] == 1500
        for _ in range(5):  # a GUI heartbeats every 300 ms
            await c.ok("rc.heartbeat")
            await asyncio.sleep(0.1)
        await applied(c, 2)  # SA=1 -> 2000 us -> echoed
        await c.ok("rc.set", values={"SA": 0})
        await applied(c, 0)
        assert received(rig) > 10
        pushed = [m for m in c.inbox if m["op"] == "rc"]
        assert pushed and pushed[-1]["holder_is_you"] is True
        await c.ok("rc.release")
        await asyncio.sleep(0.15)
        n = received(rig)
        await asyncio.sleep(0.3)
        assert received(rig) == n  # silence after release
        await c.close()


async def test_gs_rcstate_is_recorded_on_the_bus_not_replayed():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="timed", duration_s=5)
        await c.ok("rc.set", values={"SD": 1})
        await asyncio.sleep(0.3)
        st = rig.app.store.get("RcState", "gs")
        assert st is not None and st.packet.origin == RcOrigin.gs and st.packet.ch_us[7] == 2000
        await c.close()  # timed lease: survives the CLI process


async def test_client_disconnect_stops_sending_connection_lease():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="connection")
        await c.ok("rc.set", values={"SA": 1})
        await wait_for(lambda: received(rig) > 3, what="frames")
        await c.close()  # GUI tab closed or crashed
        await asyncio.sleep(0.2)
        n = received(rig)
        await asyncio.sleep(0.3)
        assert received(rig) == n
        assert rig.app.rc.status().lease is None and rig.app.rc.status().controls["SA"] == 0


async def test_heartbeat_loss_stops_sending_with_connection_still_open():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="connection")
        await wait_for(lambda: received(rig) > 3, what="frames")
        await asyncio.sleep(1.3)  # no heartbeat: lease dies after 1 s
        n = received(rig)
        await asyncio.sleep(0.3)
        assert received(rig) == n
        assert await c.err("rc.set", values={"SA": 1}) == "control_denied"  # the lease is gone
        again = await c.ok("rc.acquire", kind="connection")  # and the same connection can take control again
        assert again["id"] != "" and rig.app.rc.status().controls == rig.app.rc.cfg.default_state()
        await c.close()


async def test_timed_lease_survives_clients_and_expires():
    async with rc_running() as rig:
        c1 = await Client.connect(rig)
        await c1.ok("rc.acquire", kind="timed", duration_s=0.8)
        await c1.close()  # short-lived CLI process
        c2 = await Client.connect(rig)
        await c2.ok("rc.set", values={"SB": 2})  # a second process may use the current timed lease
        await asyncio.sleep(0.3)
        assert received(rig) > 3
        await asyncio.sleep(0.8)
        n = received(rig)
        await asyncio.sleep(0.3)
        assert received(rig) == n and rig.app.rc.status().lease is None
        await c2.close()


async def test_control_hosts_enforced_through_the_api():
    async with rc_running(make_rc_app(control_hosts=("10.99.99.99",))) as rig:
        c = await Client.connect(rig)
        assert c.welcome["can_control"] is False
        assert await c.err("rc.acquire", kind="connection") == "control_denied"
        assert await c.err("rc.acquire", kind="timed", duration_s=5) == "control_denied"
        assert await c.err("rc.set", values={"SA": 1}) == "no_lease"
        await asyncio.sleep(0.2)
        assert received(rig) == 0 and rig.app.rc.status().lease is None
        await c.close()


async def test_controller_checks_the_address_even_if_the_api_did_not():
    app = make_rc_app(control_hosts=("127.0.0.1",))
    from exvectr_gs.rc import ControlDenied

    async with rc_running(app):
        await asyncio.sleep(0.1)
        with pytest.raises(ControlDenied):
            app.rc.acquire("x", "connection", None, "192.168.0.5")


async def test_view_only_second_client_cannot_take_over():
    async with rc_running() as rig:
        a, b = await Client.connect(rig), await Client.connect(rig)
        await a.ok("rc.acquire", kind="connection")
        assert await b.err("rc.acquire", kind="connection") == "control_denied"
        await a.close()
        await b.close()


async def test_invalid_values_rejected_with_nothing_changed():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="timed", duration_s=5)
        for values in ({"SA": 7}, {"NOPE": 1}, {"S1": 3.0}, {"SA": 1, "SB": 9}):
            assert await c.err("rc.set", values=values) == "invalid_value"
        assert await c.err("rc.set", values={"SA": 1}, hold_s=1) == "invalid_value"  # hold: momentary only
        assert await c.err("rc.set", values={"SE": 1}, hold_s=99) == "invalid_value"
        assert rig.app.rc.status().controls == rig.app.rc.cfg.default_state()
        await c.ok("rc.release")
        await c.close()


async def test_momentary_hold_is_released_by_the_core_even_if_the_client_dies():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="timed", duration_s=5)
        await c.ok("rc.set", values={"SE": 1}, hold_s=0.4)
        await wait_for(lambda: any(p.ch_us[8] == 2000 for p in rig.app.sources[0].received_rc), what="pressed")
        await c.close()  # killed before it could release
        await wait_for(lambda: rig.app.sources[0].received_rc[-1].ch_us[8] == 1000, 2.0, "released by core")
        sent = [p.ch_us[8] for p in rig.app.sources[0].received_rc]
        assert 8 <= sent.count(2000) <= 13  # about 0.4 s at 25 Hz


async def test_refused_send_skips_ticks_and_reports_not_sending():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="timed", duration_s=5)
        await wait_for(lambda: received(rig) > 2, what="frames")
        src = rig.app.sources[0]

        async def refuse(pkt):
            return False

        orig, src.send = src.send, refuse
        await asyncio.sleep(0.2)
        n = received(rig)
        await asyncio.sleep(0.2)
        assert received(rig) == n and rig.app.rc.skipped_count > 3
        src.send = orig
        await wait_for(lambda: received(rig) > n, what="sending resumes")
        await c.close()


async def test_mirror_mode_through_the_api():
    app = make_rc_app(rc_role="mirror", profile="radio")
    async with rc_running(app) as rig:
        c = await Client.connect(rig)
        await wait_for(lambda: app.rc.status().mode == "mirroring", what="mirroring")
        st = app.rc.status()
        assert st.origin == "handset" and not st.sending
        assert await c.err("rc.acquire", kind="connection") == "control_denied"  # mirror link: GS never sends
        assert received(rig) == 0
        await c.close()


async def test_core_restart_sends_nothing_and_starts_with_defaults():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="timed", duration_s=30)
        await c.ok("rc.set", values={"SA": 1, "SD": 1})
        await c.close()
    async with rc_running() as rig2:  # a "restart": a new core from the same config
        await asyncio.sleep(0.5)
        st = rig2.app.rc.status()
        assert st.lease is None and st.controls == rig2.app.rc.cfg.default_state()
        assert received(rig2) == 0 and st.mode == "idle"


# ---------------------------------------------------------------- pty fake vehicle: real bytes


def pty_app(link, **opts):
    cfg = parse_config(
        {
            "core": {"wire_profile": "provisional-1", "source_priority": ["usb"]},
            "server": {"bind": "127.0.0.1", "port": 1, "control_hosts": ["127.0.0.1"]},
            "source": [
                {
                    "id": "usb",
                    "kind": "vehicle_usb",
                    "rc_role": "control",
                    "match": {"device": str(link)},
                    "poll_s": 0.05,
                    "stats_period_s": 0.5,
                }
            ],
            "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
        }
    )
    return CoreApp(cfg)


def decode_rc_state(payload):
    """Independent decode of the provisional RcState payload: uint8 seq, uint16 LE x 16."""
    seq, *ch = struct.unpack("<B16H", payload)
    return seq, ch


def rc_frames(vehicle):
    return [f for f in list(vehicle.received) if f.channel == layouts.PORT_RC_STATE]


@contextlib.asynccontextmanager
async def pty_core(tmp_path):
    v = FakeVehiclePty(tmp_path / "veh", speed=1.0)
    v.start()
    app = pty_app(tmp_path / "veh")
    tasks = [asyncio.create_task(app.sources[0].run(app.bus.publish)), asyncio.create_task(app.rc.run())]
    await wait_for(lambda: app.sources[0].status().state == "connected", what="connected")
    try:
        yield app, v
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        v.stop()


async def test_pty_uplink_rate_stops_on_release_and_lease_expiry(tmp_path):
    async with pty_core(tmp_path) as (app, v):
        await asyncio.sleep(0.4)
        assert rc_frames(v) == []  # connected, no lease: nothing on the wire
        lease = app.rc.acquire("t", "timed", 30, "127.0.0.1")
        app.rc.set(lease.id, {"SA": 1, "SB": 2, "S1": 0.5})
        n0, t0 = len(rc_frames(v)), time.monotonic()
        await asyncio.sleep(3.0)
        n1, t1 = len(rc_frames(v)), time.monotonic()
        rate = (n1 - n0) / (t1 - t0)
        assert rate == pytest.approx(25.0, rel=0.05), rate
        f = rc_frames(v)[-1]
        seq, chans = decode_rc_state(f.payload)
        assert chans[4] == 2000 and chans[5] == 2000 and chans[9] == 1500 and chans[2] == 1000 and chans[0] == 1500
        assert len(rc_frames(v)) >= 2 and rc_frames(v)[-1].payload[0] != rc_frames(v)[-2].payload[0]  # seq moves
        app.rc.release(lease.id)
        stop = time.monotonic()
        await asyncio.sleep(0.1)  # one period (40 ms) plus transport slack
        n = len(rc_frames(v))
        await asyncio.sleep(0.5)
        assert len(rc_frames(v)) == n, "frames after release"
        assert time.monotonic() - stop < 1.0

        lease = app.rc.acquire("cli", "timed", 0.6, "127.0.0.1")  # expiry path
        await asyncio.sleep(0.9)
        n = len(rc_frames(v))
        await asyncio.sleep(0.4)
        assert len(rc_frames(v)) == n
        # a start from defaults: the first frame of the new lease carries no residue of the old state
        lease = app.rc.acquire("cli", "timed", 5, "127.0.0.1")
        await wait_for(lambda: len(rc_frames(v)) > n, what="new lease frames")
        _, chans = decode_rc_state(rc_frames(v)[n].payload)
        assert chans == app.rc.cfg.channels_us(app.rc.cfg.default_state())
        app.rc.release(lease.id)


async def test_pty_unplug_stops_frames_and_replug_starts_from_defaults(tmp_path):
    async with pty_core(tmp_path) as (app, v):
        lease = app.rc.acquire("cli", "timed", 30, "127.0.0.1")
        app.rc.set(lease.id, {"SD": 1})
        await wait_for(lambda: len(rc_frames(v)) > 5, what="frames")
        v.stop()  # unplug
        await wait_for(lambda: app.sources[0].status().state != "connected", what="down")
        assert app.rc.status().sending is False
        v.received.clear()
        v.start()  # replug
        await wait_for(lambda: app.sources[0].status().state == "connected", 5, "reconnect")
        await wait_for(lambda: len(rc_frames(v)) > 2, what="frames after replug")
        _, chans = decode_rc_state(rc_frames(v)[0].payload)
        assert chans[7] == 1000  # SD back at its default: the old state was not resumed
        app.rc.release(lease.id)
