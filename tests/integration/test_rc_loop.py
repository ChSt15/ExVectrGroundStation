"""Command engine and virtual panel end to end: API with the FakeSource echo, control_hosts, disconnects, Pocket loss,
and the pty fake vehicle. The engine is the only sender; leases only guard the virtual panel.

Never touches real hardware. Recordings go to temp dirs."""

import asyncio
import contextlib
import tempfile
import time

import pytest

from exvectr_gs.core.app import CoreApp
from exvectr_gs.core.config import parse_config
from exvectr_gs.packets.defs.link import RcOrigin, RcState

from ..support_pty import FakeVehiclePty, wait_for
from .test_api import Client, running


def make_rc_app(control_hosts=("127.0.0.1", "::1"), rc_role="control", **fake):
    cfg = parse_config(
        {
            "core": {"source_priority": ["fake"]},
            "server": {"bind": "127.0.0.1", "port": 1, "control_hosts": list(control_hosts)},
            "source": [
                {
                    "id": "fake",
                    "kind": "fake",
                    "rc_role": rc_role,
                    "echo_rc": True,
                    "echo_field": "go",
                    "seed": 1,
                    **fake,
                }
            ],
            "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
        }
    )
    return CoreApp(cfg)


@contextlib.asynccontextmanager
async def rc_running(app=None):
    app = app or make_rc_app()
    async with running(app) as rig:
        task = asyncio.create_task(app.engine.run())
        try:
            yield rig
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def make_two_source_app(roles=("control", "control", "mirror"), **kw):
    """A radio-profile fake (provides the Pocket's handset RcState) plus two more fakes."""
    ids = ["pocket", "usb", "link"]
    cfg = parse_config(
        {
            "core": {"source_priority": ids},
            "server": {"bind": "127.0.0.1", "port": 1, "control_hosts": ["127.0.0.1", "::1"]},
            "source": [
                {
                    "id": i,
                    "kind": "fake",
                    "rc_role": r,
                    "echo_rc": True,
                    "echo_field": "go",
                    "seed": n,
                    "profile": "radio" if n == 1 else "usb",
                }
                for n, (i, r) in enumerate(zip(ids, roles, strict=True), 1)
            ],
            "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
        }
    )
    return CoreApp(cfg)


def received(rig):
    return len(rig.app.sources[0].received_cmd)


async def applied(c, expect, timeout=3.0):
    """Wait until the echoed MissionState.mission_index (the fake vehicle's 'applied' go value) equals `expect`."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snap = await c.ok("get_snapshot", view="live", types=["MissionState"], src="fake")
        items = snap["items"] if isinstance(snap, dict) else snap
        if items and items[0]["f"].get("mission_index") == expect:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"applied value never became {expect}")


async def test_the_stream_runs_without_any_client_or_lease_at_25_hz():
    async with rc_running() as rig:
        await wait_for(lambda: received(rig) > 5, what="frames")
        n0, t0 = received(rig), time.monotonic()
        await asyncio.sleep(2.0)
        rate = (received(rig) - n0) / (time.monotonic() - t0)
        assert rate == pytest.approx(25.0, rel=0.1), rate
        last = rig.app.sources[0].last_cmd
        assert last.session == rig.app.engine.session and last.go == 0 and last.motors == 0
        assert rig.app.rc.status().lease is None


async def test_round_trip_through_api_and_fake_echo():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        lease = await c.ok("rc.acquire", kind="connection")
        assert lease["kind"] == "connection" and lease["expires"] is None
        st = await c.ok("rc.set", values={"SA": 1, "S1": 0.5})
        assert st["mode"] == "virtual" and st["controls"]["SA"] == 1 and st["channels_us"][9] == 1500
        for _ in range(5):  # a GUI heartbeats every 300 ms
            await c.ok("rc.heartbeat")
            await asyncio.sleep(0.1)
        await applied(c, 1)  # SA up -> go GO -> echoed
        assert rig.app.sources[0].last_cmd.input_virtual is True
        await c.ok("rc.set", values={"SA": 0})
        await applied(c, 0)
        pushed = [m for m in c.inbox if m["op"] == "rc"]
        assert pushed and pushed[-1]["holder_is_you"] is True
        cmd = pushed[-1]["command"]
        assert cmd["session"] == rig.app.engine.session and cmd["input"] == "virtual" and cmd["sending"] is True
        assert pushed[-1]["target"] == "fake" and pushed[-1]["sending"] is True
        await c.ok("rc.release")
        await asyncio.sleep(0.15)
        n = received(rig)
        await asyncio.sleep(0.3)
        assert received(rig) > n + 5  # releasing the panel never stops the command stream
        await c.close()


async def test_the_sent_command_state_is_recorded_on_the_bus():
    async with rc_running() as rig:
        await wait_for(lambda: rig.app.store.get("CmdState", "gs") is not None, what="recorded CmdState")
        st = rig.app.store.get("CmdState", "gs")
        assert st.packet.session == rig.app.engine.session and st.packet.motors == 0


async def test_client_disconnect_ends_the_lease_but_never_the_stream():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="connection")
        await c.ok("rc.set", values={"SA": 1})
        await applied(c, 1)
        await c.close()  # GUI tab closed or crashed
        await asyncio.sleep(0.3)
        n = received(rig)
        await asyncio.sleep(0.4)
        assert received(rig) > n + 8  # still 25 Hz
        assert rig.app.rc.status().lease is None and rig.app.rc.status().controls["SA"] == 0
        last = rig.app.sources[0].last_cmd
        assert last.go == 1 and last.input_virtual is False  # the last command state is held (D-GS5)


async def test_heartbeat_loss_ends_the_lease_the_stream_continues():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="connection")
        await wait_for(lambda: received(rig) > 3, what="frames")
        await asyncio.sleep(1.3)  # no heartbeat: lease dies after 1 s
        n = received(rig)
        await asyncio.sleep(0.3)
        assert received(rig) > n
        assert await c.err("rc.set", values={"SA": 1}) == "control_denied"  # the lease is gone
        again = await c.ok("rc.acquire", kind="connection")  # and the same connection can take the panel again
        assert again["id"] != ""
        await c.close()


async def test_keepalive_lease_survives_clients_expires_when_not_renewed():
    async with rc_running() as rig:
        c1 = await Client.connect(rig)
        await c1.ok("rc.acquire", kind="keepalive", idle_s=0.8)
        await c1.close()  # short-lived CLI process
        c2 = await Client.connect(rig)
        await c2.ok("rc.set", values={"SB": 1})  # a second process may use the current keepalive lease
        await asyncio.sleep(0.3)
        assert rig.app.sources[0].last_cmd.profile == 1
        await asyncio.sleep(1.0)
        assert rig.app.rc.status().lease is None
        await c2.close()


async def test_control_hosts_enforced_through_the_api():
    async with rc_running(make_rc_app(control_hosts=("10.99.99.99",))) as rig:
        c = await Client.connect(rig)
        assert c.welcome["can_control"] is False
        assert await c.err("rc.acquire", kind="connection") == "control_denied"
        assert await c.err("rc.acquire", kind="keepalive", idle_s=9) == "control_denied"
        assert await c.err("rc.set", values={"SA": 1}) == "no_lease"
        await asyncio.sleep(0.2)
        assert rig.app.rc.status().lease is None
        await wait_for(lambda: received(rig) > 2, what="the engine still sends: it needs no client")
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
        await c.ok("rc.acquire", kind="keepalive", idle_s=9)
        for values in ({"SA": 7}, {"NOPE": 1}, {"S1": 3.0}, {"SA": 1, "SB": 9}):
            assert await c.err("rc.set", values=values) == "invalid_value"
        assert await c.err("rc.set", values={"SA": 1}, hold_s=1) == "invalid_value"  # hold: momentary only
        assert await c.err("rc.set", values={"SE": 1}, hold_s=99) == "invalid_value"
        assert rig.app.rc.status().controls["SA"] == 0 and rig.app.rc.status().controls["SE"] == 0
        await c.ok("rc.release")
        await c.close()


async def test_momentary_hold_is_released_by_the_core_even_if_the_client_dies():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="keepalive", idle_s=9)
        await c.ok("rc.set", values={"SE": 1}, hold_s=0.4)
        await wait_for(lambda: any(p.reset_button == 1 for p in rig.app.sources[0].received_cmd), what="pressed")
        await c.close()  # killed before it could release
        await wait_for(lambda: rig.app.sources[0].received_cmd[-1].reset_button == 0, 2.0, "released by core")
        sent = [p.reset_button for p in rig.app.sources[0].received_cmd]
        assert 8 <= sent.count(1) <= 14  # about 0.4 s at 25 Hz


async def test_a_link_that_refuses_is_skipped_and_the_engine_keeps_going():
    async with rc_running() as rig:
        await wait_for(lambda: received(rig) > 2, what="frames")
        src = rig.app.sources[0]

        async def refuse(pkt):
            return False

        orig, src.send = src.send, refuse
        await asyncio.sleep(0.3)
        n = received(rig)
        await asyncio.sleep(0.2)
        assert received(rig) == n and rig.app.engine.unsent_count > 3
        src.send = orig
        await wait_for(lambda: received(rig) > n, what="sending resumes")


async def test_a_mirror_source_is_never_a_command_link_and_the_pocket_shows_in_the_status():
    app = make_rc_app(rc_role="mirror", profile="radio")
    async with rc_running(app) as rig:
        c = await Client.connect(rig)
        await wait_for(lambda: app.rc.status().mode == "pocket", what="pocket")
        st = app.rc.status()
        assert st.origin == "handset" and not st.sending
        assert received(rig) == 0
        push = await c.ok("get_status") if False else None  # noqa: F841
        await wait_for(lambda: app.engine.status()["pocket"] == "live", what="Pocket live in the engine")
        assert app.engine.status()["links"] == [] and app.engine.status()["sending"] is False
        await c.close()


async def test_core_restart_has_a_new_session_and_starts_safe():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="keepalive", idle_s=9)
        await c.ok("rc.set", values={"SA": 1, "SB": 1})
        s1 = rig.app.engine.session
        await c.close()
    async with rc_running() as rig2:  # a "restart": a new core from the same config
        await wait_for(lambda: received(rig2) > 2, what="frames")
        st = rig2.app.rc.status()
        assert st.lease is None and st.controls == rig2.app.rc.cfg.default_state()
        assert rig2.app.engine.session != s1 and rig2.app.engine.session != 0
        first = rig2.app.sources[0].received_cmd[0]
        assert (first.go, first.motors, first.reset_button, first.failsafe) == (0, 0, 0, 0)


async def test_pocket_loss_is_a_warning_event_and_the_stream_keeps_its_last_state():
    app = make_rc_app()
    events = []
    app.bus.subscribe(lambda p: events.append(p.detail), {"GsEvent"})
    async with rc_running(app) as rig:
        for _ in range(60):  # the Pocket: SA down, then SA up (cycled)
            ch = [1500] * 16
            ch[4] = 1000 if _ < 30 else 2000
            app.bus.publish(RcState(t=app.clock.now(), src="fake", origin=RcOrigin.handset, ch_us=ch, seq=_, node=2))
            await asyncio.sleep(0.005)
        await wait_for(lambda: rig.app.sources[0].last_cmd.go == 1, what="go from the Pocket")
        await asyncio.sleep(0.8)  # the Pocket stops
        assert app.engine.status()["pocket"] == "lost"
        assert any("Pocket stream lost" in e for e in events)
        n = received(rig)
        await asyncio.sleep(0.4)
        assert received(rig) > n + 8 and rig.app.sources[0].last_cmd.go == 1  # no abort, no failsafe
        assert rig.app.sources[0].last_cmd.failsafe == 0


# ---------------------------------------------------------------- pty fake vehicle (exvectrnet-1): real bytes


def pty_app(link, **opts):
    cfg = parse_config(
        {
            "core": {"wire_profile": "exvectrnet-1", "source_priority": ["usb"]},
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


def cmd_frames(vehicle):
    return [c for c in list(vehicle.received_cmd)]


@contextlib.asynccontextmanager
async def pty_core(tmp_path):
    v = FakeVehiclePty(tmp_path / "veh", speed=1.0, profile="exvectrnet-1")
    v.start()
    app = pty_app(tmp_path / "veh")
    tasks = [asyncio.create_task(app.sources[0].run(app.bus.publish)), asyncio.create_task(app.engine.run())]
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


async def test_pty_cmd_state_bytes_rate_session_and_no_unsafe_edge_at_start(tmp_path):
    async with pty_core(tmp_path) as (app, v):
        await wait_for(lambda: len(cmd_frames(v)) > 10, what="frames")
        n0, t0 = len(cmd_frames(v)), time.monotonic()
        await asyncio.sleep(3.0)
        rate = (len(cmd_frames(v)) - n0) / (time.monotonic() - t0)
        assert rate == pytest.approx(25.0, rel=0.08), rate
        fr = cmd_frames(v)
        assert all(not f["rejected"] for f in fr), "every frame passes the vehicle's strict parser"
        assert all(f["src"] == 0 and f["dst"] == 1 for f in fr)
        assert {f["session"] for f in fr} == {app.engine.session} and v.cmd_session == app.engine.session
        assert all((f["go"], f["motors"], f["reset_button"], f["failsafe"]) == (0, 0, 0, 0) for f in fr)
        seqs = [f["seq"] for f in fr]
        assert all((b - a) & 0xFF == 1 for a, b in zip(seqs, seqs[1:], strict=False))  # one stream, one counter
        assert v.cmd_rejects == 0


async def test_pty_virtual_switch_change_goes_out_at_once_and_the_vehicle_reports_it_back(tmp_path):
    async with pty_core(tmp_path) as (app, v):
        await wait_for(lambda: len(cmd_frames(v)) > 3, what="frames")
        lease = app.rc.acquire("t", "keepalive", 9, "127.0.0.1")
        await asyncio.sleep(0.1)
        t_set = time.monotonic_ns()
        app.rc.set(lease.id, {"SA": 1})
        await wait_for(lambda: cmd_frames(v)[-1]["go"] == 1, 1.0, "go on the wire")
        assert (cmd_frames(v)[-1]["t"] - t_set) / 1e6 < 60  # well inside one 40 ms period plus the loop
        assert cmd_frames(v)[-1]["input_virtual"] is True
        # the vehicle's own report (CMD_APPLIED over the same wire), never the sent state
        await wait_for(lambda: (app.store.get("CmdApplied") or None) is not None, 3.0, "CMD_APPLIED")
        await wait_for(lambda: app.store.get("CmdApplied").packet.go == 1, 3.0, "applied go")
        assert app.store.get("CmdApplied").packet.session == app.engine.session
        app.rc.release(lease.id)


async def test_pty_requests_to_the_vehicle_carry_the_session(tmp_path):
    from exvectr_gs.packets.defs.link import Request, RequestAction

    async with pty_core(tmp_path) as (app, v):
        await wait_for(lambda: len(cmd_frames(v)) > 3, what="frames")
        pkt = Request(t=1, src="gs", req_id=7, action=RequestAction.SAVE_CONFIG, target=1, session=app.engine.session)
        assert await app.sources[0].send(pkt)
        await wait_for(lambda: v.received_requests, what="request")
        assert v.received_requests[-1]["session"] == app.engine.session


async def test_pty_unplug_stops_frames_and_replug_continues_the_same_session(tmp_path):
    async with pty_core(tmp_path) as (app, v):
        await wait_for(lambda: len(cmd_frames(v)) > 5, what="frames")
        session = app.engine.session
        v.stop()  # unplug
        await wait_for(lambda: app.sources[0].status().state != "connected", what="down")
        assert app.engine.status()["sending"] is False
        v.received_cmd.clear()
        v.cmd_session = None
        v.start()  # replug
        await wait_for(lambda: app.sources[0].status().state == "connected", 5, "reconnect")
        await wait_for(lambda: len(cmd_frames(v)) > 2, what="frames after replug")
        assert cmd_frames(v)[0]["session"] == session  # the session lives with the core, not the link
        assert (cmd_frames(v)[0]["go"], cmd_frames(v)[0]["motors"]) == (0, 0)


# ---------------------------------------------------------------- locked control (SD) through the API


async def test_locked_control_api_flow_and_push_state():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="connection")
        assert await c.err("rc.set", values={"SD": 1}) == "control_locked"
        assert await c.err("rc.set", values={"SA": 1, "SD": 1}) == "control_locked"
        st = await c.ok("rc.set", values={"SD": 0})
        assert st["controls"]["SA"] == 0 and st["locks"]["SD"] == {
            "locked": True,
            "remaining_s": None,
            "timeout_s": 60.0,
        }
        assert await c.err("rc.unlock", control="SA") == "invalid_value"  # SA has no lock
        assert await c.err("rc.unlock", control="NOPE") == "invalid_value"
        st = await c.ok("rc.unlock", control="SD")
        assert st["locks"]["SD"]["locked"] is False and 59 < st["locks"]["SD"]["remaining_s"] <= 60
        await c.ok("rc.set", values={"SD": 1})
        for _ in range(5):
            await c.ok("rc.heartbeat")
            await asyncio.sleep(0.05)
        msg = [m for m in c.inbox if m["op"] == "rc"][-1]  # the push carries the lock state
        assert msg["controls"]["SD"] == 1 and msg["locks"]["SD"]["locked"] is False and msg["channels_us"][7] == 2000
        await wait_for(lambda: rig.app.sources[0].last_cmd.motors == 1, 2.0, "motors armed on the wire")
        st = await c.ok("rc.lock", control="SD")  # relock: back to off
        assert st["controls"]["SD"] == 0 and st["locks"]["SD"]["locked"] is True and st["channels_us"][7] == 1000
        assert await c.err("rc.set", values={"SD": 1}) == "control_locked"
        await c.close()


async def test_lease_end_by_connection_drop_holds_the_output_relocks_the_panel_and_the_next_holder_starts_from_it():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="connection")
        await c.ok("rc.unlock", control="SD")
        await c.ok("rc.set", values={"SD": 1})
        await wait_for(lambda: rig.app.sources[0].last_cmd.motors == 1, 2.0, "motors armed on the wire")
        await c.close()
        await asyncio.sleep(0.3)
        st = rig.app.rc.status()
        assert st.lease is None and st.controls["SD"] == 0 and st.locks["SD"]["locked"] is True  # the panel is reset
        # the engine holds its last command state: a closed tab never disarms (nor does it abort or failsafe)
        assert rig.app.sources[0].last_cmd.motors == 1 and rig.app.engine.status()["input"] == "none"
        d = await Client.connect(rig)
        await d.ok("rc.acquire", kind="connection")  # the next holder starts at what is being sent
        st = rig.app.rc.status()
        assert st.controls["SD"] == 1
        await d.ok("rc.set", values={"SD": 0})  # the safe direction is always allowed
        await wait_for(lambda: rig.app.sources[0].last_cmd.motors == 0, 2.0, "motors disarmed on the wire")
        await d.close()


async def test_keepalive_lease_renewal_and_idle_expiry_through_the_api():
    async with rc_running() as rig:
        a = await Client.connect(rig)
        await a.ok("rc.acquire", kind="keepalive", idle_s=0.6)
        await a.close()
        for _ in range(4):  # separate short-lived clients keep it alive, each op renews
            await asyncio.sleep(0.3)
            b = await Client.connect(rig)
            await b.ok("rc.heartbeat")
            await b.close()
        assert rig.app.rc.status().lease is not None
        await asyncio.sleep(0.9)
        assert rig.app.rc.status().lease is None
        c = await Client.connect(rig)
        assert await c.err("rc.heartbeat") == "no_lease"  # a late renewal does not revive it
        await c.close()


async def test_abort_and_failsafe_ops_need_no_lease_and_survive_the_client():
    async with rc_running() as rig:
        c = await Client.connect(rig)
        await c.ok("rc.acquire", kind="connection")
        await c.ok("rc.set", values={"SA": 1})
        await applied(c, 1)
        await c.ok("rc.release")
        r = await c.ok("engine.abort")  # no lease held: still acts
        assert r["command"]["out"]["go"] == 0 and r["command"]["abort_count"] == 1
        r = await c.ok("engine.failsafe", on=True)
        assert r["command"]["out"]["failsafe"] == 1 and r["command"]["failsafe_latched"] is True
        await c.close()
        await asyncio.sleep(0.2)
        assert rig.app.engine.status()["out"]["failsafe"] == 1  # latched after the GUI is gone
        c = await Client.connect(rig)
        r = await c.ok("engine.failsafe", on=False)
        assert r["command"]["out"]["failsafe"] == 0
        assert await c.err("engine.failsafe", on="x") == "bad_request"
        await c.close()


async def test_abort_and_failsafe_ops_are_refused_for_view_only_clients():
    async with rc_running(make_rc_app(control_hosts=("10.99.99.99",))) as rig:
        c = await Client.connect(rig)
        assert await c.err("engine.abort") == "control_denied"
        assert await c.err("engine.failsafe", on=True) == "control_denied"
        await c.close()
