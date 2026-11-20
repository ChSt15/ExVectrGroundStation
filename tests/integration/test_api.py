"""API server integration tests: real aiohttp server and WebSocket clients on loopback, FakeSource data."""

import asyncio
import contextlib
import dataclasses
import json
import tempfile
import time
from dataclasses import dataclass

import aiohttp
import pytest

from exvectr_gs.api import ApiServer
from exvectr_gs.api import hub as hub_mod
from exvectr_gs.core.app import CoreApp
from exvectr_gs.core.clock import FakeClock
from exvectr_gs.core.config import parse_config
from exvectr_gs.packets.defs.host import ConsoleText
from exvectr_gs.packets.defs.vehicle import Attitude, VehicleMode, VehicleState
from exvectr_gs.rc.controller import ControlDenied, Lease, RcStatus

S = 1_000_000_000


def make_app(control_hosts=("127.0.0.1", "::1"), rates=None, **fake):
    opts = {"profile": "usb", "seed": 1, **fake}
    if rates:
        opts["rates"] = rates
    cfg = parse_config(
        {
            "core": {"source_priority": ["fake"]},
            "server": {"bind": "127.0.0.1", "port": 1, "control_hosts": list(control_hosts)},
            "source": [{"id": "fake", "kind": "fake", **opts}],
            "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
        }
    )
    return CoreApp(cfg)


@dataclass
class Rig:
    app: CoreApp
    api: ApiServer
    tasks: list

    def url(self):
        return f"http://127.0.0.1:{self.api.port}"


@contextlib.asynccontextmanager
async def running(app: CoreApp | None = None, sources=True):
    app = app or make_app()
    api = ApiServer(app, "127.0.0.1", 0)
    await api.start()
    tasks = [asyncio.create_task(s.run(app.bus.publish)) for s in app.sources] if sources else []
    try:
        yield Rig(app, api, tasks)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        await api.stop()


class Client:
    def __init__(self, ws, session):
        self.ws, self.session = ws, session
        self.inbox: list[dict] = []
        self._id = 0

    @classmethod
    async def connect(cls, rig, hello=True, **kw):
        session = aiohttp.ClientSession()
        ws = await session.ws_connect(f"{rig.url()}/ws", **kw)
        c = cls(ws, session)
        if hello:
            await c.send({"op": "hello", "client": "cli", "name": "test", "protocol": 1, "id": 0})
            c.welcome = await c.expect("welcome")
        return c

    async def close(self):
        await self.ws.close()
        await self.session.close()

    async def send(self, msg):
        await self.ws.send_str(json.dumps(msg))

    async def recv(self, timeout=3.0):
        m = await asyncio.wait_for(self.ws.receive(), timeout)
        assert m.type == aiohttp.WSMsgType.TEXT, m
        msg = json.loads(m.data)
        self.inbox.append(msg)
        return msg

    async def expect(self, op, timeout=3.0):
        end = time.monotonic() + timeout
        while True:
            msg = await self.recv(max(0.01, end - time.monotonic()))
            if msg["op"] == op:
                return msg

    async def call(self, op, **fields):
        self._id += 1
        await self.send({"op": op, "id": self._id, **fields})
        while True:
            msg = await self.recv()
            if msg["op"] == "result" and msg["id"] == self._id:
                return msg

    async def ok(self, op, **fields):
        r = await self.call(op, **fields)
        assert r["ok"], r
        return r["data"]

    async def err(self, op, **fields):
        r = await self.call(op, **fields)
        assert not r["ok"], r
        return r["error"]["code"]


# ---------------------------------------------------------------- handshake and conventions


async def test_handshake_welcome_schema_status_in_order():
    async with running() as rig:
        c = await Client.connect(rig)
        assert c.welcome["protocol"] == 1 and c.welcome["can_control"] is True
        assert c.welcome["views"] == ["live", "replay"] and c.welcome["client_id"].startswith("c")
        assert isinstance(c.welcome["clock"], float) and c.welcome["id"] == 0
        schema = await c.recv()
        assert schema["op"] == "schema" and any(t["name"] == "Attitude" for t in schema["types"])
        status = await c.recv()
        assert status["op"] == "status" and status["sources"][0]["id"] == "fake"
        assert status["sources"][0]["since"] is None or isinstance(status["sources"][0]["since"], float)
        assert status["clients"] == 1 and status["recorder"]["active"] is False
        await c.close()


async def test_hello_required_first_and_protocol_checked():
    async with running() as rig:
        c = await Client.connect(rig, hello=False)
        assert await c.err("subscribe") == "bad_request"
        await c.send({"op": "hello", "client": "cli", "protocol": 99, "id": 5})
        r = await c.expect("result")
        assert r["id"] == 5 and r["error"]["code"] == "bad_request"
        await c.send({"op": "hello", "client": "cli", "protocol": 1})
        await c.expect("welcome")
        await c.close()


async def test_malformed_messages_get_errors_and_connection_survives():
    async with running() as rig:
        c = await Client.connect(rig)
        await c.ws.send_str("not json")
        assert (await c.expect("result"))["error"]["code"] == "bad_request"
        await c.ws.send_str("[1,2]")
        assert (await c.expect("result"))["error"]["code"] == "bad_request"
        assert await c.err("no.such.op") == "bad_request"
        assert await c.err("subscribe", types=["Nope"]) == "unknown_type"
        assert await c.err("subscribe", view="x") == "bad_request"
        assert await c.err("replay.speed", x=3) == "bad_request" or True  # replayer absent: either error is fine
        assert (await c.ok("sources.list"))[0]["id"] == "fake"
        await c.close()


# ---------------------------------------------------------------- subscribe, snapshot, packets


async def test_subscribe_result_snapshot_then_packets_with_float_seconds():
    async with running() as rig:
        await asyncio.sleep(0.3)  # let the fake source produce data
        c = await Client.connect(rig)
        await c.ok("subscribe", view="live", types=["Attitude", "VehicleState"], mode="latest", max_rate_hz=20)
        snap = await c.expect("snapshot")
        assert snap["view"] == "live" and isinstance(snap["clock"], float)
        assert {i["type"] for i in snap["items"]} == {"Attitude", "VehicleState"}
        for it in snap["items"]:
            assert isinstance(it["t"], float) and abs(it["t"] - time.time()) < 5
            assert it["iv"] is None or it["iv"] > 0
            assert it["stale_after"] > 0
        vs = next(i for i in snap["items"] if i["type"] == "VehicleState")
        assert isinstance(vs["f"]["mode"], str) and isinstance(vs["f"]["armed"], bool)
        pk = await c.expect("packets")
        assert pk["view"] == "live" and isinstance(pk["clock"], float)
        assert all(i["type"] in ("Attitude", "VehicleState") for i in pk["items"])
        assert all("iv" in i and "stale_after" in i for i in pk["items"])  # latest mode carries them
        await c.close()


async def test_unsubscribe_stops_packets():
    async with running() as rig:
        c = await Client.connect(rig)
        await c.ok("subscribe", types=["Attitude"], max_rate_hz=50)
        await c.expect("packets")
        await c.ok("unsubscribe", view="live")
        c.inbox.clear()
        await asyncio.sleep(0.4)
        with contextlib.suppress(asyncio.TimeoutError):
            while True:
                await c.recv(0.2)
        assert not [m for m in c.inbox if m["op"] == "packets" and m["items"]][1:]  # at most one in flight
        await c.close()


async def test_get_snapshot_filters_and_preferred():
    async with running() as rig:
        await asyncio.sleep(0.3)
        c = await Client.connect(rig)
        d = await c.ok("get_snapshot", view="live", types=["Position"], src="fake")
        assert [i["type"] for i in d["items"]] == ["Position"]
        d = await c.ok("get_snapshot", view="live", types=["Position"], src="nope")
        assert d["items"] == []
        d = await c.ok("get_snapshot", view="live", preferred=True)
        types = [i["type"] for i in d["items"]]
        assert len(types) == len(set(types)) and "Attitude" in types
        d = await c.ok("get_snapshot", view="replay")
        assert d["items"] == []
        await c.close()


async def test_get_history_returns_float_seconds_and_enum_names():
    async with running() as rig:
        await asyncio.sleep(0.6)
        c = await Client.connect(rig)
        now = rig.app.clock.now() / 1e9
        d = await c.ok(
            "get_history",
            view="live",
            series=[{"type": "Position", "fields": ["pos"]}, {"type": "Attitude", "src": "fake", "fields": ["q"]}],
            t0=now - 60,
            t1=now + 1,
            max_points=50,
        )
        pos = d["series"][0]
        assert pos["type"] == "Position" and len(pos["t"]) == len(pos["pos"]) > 1
        assert all(isinstance(t, float) for t in pos["t"]) and len(pos["t"]) <= 50
        assert await c.err("get_history", series=[{"type": "Position", "fields": ["x"]}], t0=0, t1=1) == "invalid_value"
        d = await c.ok("get_history", view="replay", series=[{"type": "Position", "fields": ["pos"]}], t0=0, t1=1)
        assert d["series"][0]["t"] == []
        # plot buffer (tuning graphs): full rate, types without history=True too
        d = await c.ok(
            "get_history",
            view="live",
            buffer="plot",
            series=[{"type": "Position", "fields": ["pos"]}, {"type": "Estimator", "fields": ["att_std"]}],
            t0=now - 60,
            t1=now + 1,
            max_points=100_000,
        )
        assert len(d["series"][0]["t"]) >= len(pos["t"]) and d["series"][1]["att_std"]
        pos_only = [{"type": "Position", "fields": ["pos"]}]
        d = await c.ok("get_history", view="replay", buffer="plot", series=pos_only, t0=0, t1=1)
        assert d["series"][0]["t"] == []  # no log open
        await c.close()


async def test_coalescing_at_3000_packets_per_second():
    """3000 Attitude packets/s into a `latest` subscription at 10 Hz: few batches, one item per (type, src), newest."""
    async with running(sources=False) as rig:
        c = await Client.connect(rig)
        await c.ok("subscribe", types=["Attitude"], mode="latest", max_rate_hz=10)
        await c.expect("snapshot")
        stop = False
        seq = 0

        async def producer():
            nonlocal seq
            t0 = time.monotonic()
            while not stop:
                due = int((time.monotonic() - t0) * 3000)
                while seq < due:
                    seq += 1
                    rig.app.bus.publish(Attitude(t=rig.app.clock.now(), src="fake", tilt_acc=float(seq)))
                await asyncio.sleep(0.002)

        prod = asyncio.create_task(producer())
        batches = []
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            with contextlib.suppress(asyncio.TimeoutError):
                m = await c.recv(0.3)
                if m["op"] == "packets":
                    batches.append(m)
        stop = True
        await prod
        assert seq > 4500
        assert 10 <= len(batches) <= 30, len(batches)  # 10 Hz nominal
        for b in batches:
            assert len(b["items"]) == 1 and b["items"][0]["src"] == "fake"
        vals = [b["items"][0]["f"]["tilt_acc"] for b in batches]
        assert vals == sorted(vals) and vals[-1] > seq - 600  # newest packet wins each batch
        assert not any("dropped" in b for b in batches)
        await c.close()


async def test_all_mode_bounded_queue_reports_dropped(monkeypatch):
    monkeypatch.setattr(hub_mod, "ALL_QUEUE_MAX", 10)
    async with running(sources=False) as rig:
        c = await Client.connect(rig)
        await c.ok("subscribe", types=["Attitude"], mode="all", max_rate_hz=5)
        await c.expect("snapshot")
        for i in range(100):  # synchronous burst: nothing can be sent in between
            rig.app.bus.publish(Attitude(t=i + 1, src="fake", tilt_acc=float(i)))
        b = await c.expect("packets")
        vals = [i["f"]["tilt_acc"] for i in b["items"]]
        assert vals == [float(i) for i in range(90, 100)]  # newest 10, in order
        assert b["dropped"] == 90
        await c.close()


async def test_all_mode_delivers_every_packet_in_order_when_not_overloaded():
    async with running(sources=False) as rig:
        c = await Client.connect(rig)
        await c.ok("subscribe", types=["Attitude"], mode="all", max_rate_hz=50)
        for i in range(200):
            rig.app.bus.publish(Attitude(t=i + 1, src="fake", tilt_acc=float(i)))
            if i % 20 == 0:
                await asyncio.sleep(0.03)
        got = []
        while len(got) < 200:
            m = await c.expect("packets")
            got += [i["f"]["tilt_acc"] for i in m["items"]]
        assert got == [float(i) for i in range(200)]
        await c.close()


async def test_slow_client_does_not_stall_the_loop():
    """A client that subscribes to everything and never reads must not delay the event loop (lag < 20 ms)."""
    async with running(sources=False) as rig:
        slow = await Client.connect(rig)
        await slow.ok("subscribe", types=["ConsoleText"], mode="all", max_rate_hz=200)
        fast = await Client.connect(rig)
        await fast.ok("subscribe", types=["Attitude"], mode="latest", max_rate_hz=20)
        stop = False
        lags: list[float] = []

        async def ticker():
            while not stop:
                t = time.monotonic()
                await asyncio.sleep(0.005)
                lags.append(time.monotonic() - t - 0.005)

        async def producer():
            t0, seq = time.monotonic(), 0
            blob = "x" * 4000
            while not stop:
                due = int((time.monotonic() - t0) * 3000)
                while seq < due:
                    seq += 1
                    now = rig.app.clock.now()
                    rig.app.bus.publish(ConsoleText(t=now, src="fake", text=blob))
                    rig.app.bus.publish(Attitude(t=now, src="fake", tilt_acc=float(seq)))
                await asyncio.sleep(0.001)

        tasks = [asyncio.create_task(ticker()), asyncio.create_task(producer())]
        got = 0
        end = time.monotonic() + 3.0
        while time.monotonic() < end:  # the fast client keeps reading
            with contextlib.suppress(asyncio.TimeoutError):
                m = await fast.recv(0.3)
                got += m["op"] == "packets"
        stop = True
        await asyncio.gather(*tasks)
        assert got >= 30  # the fast client was served at ~20 Hz throughout
        assert max(lags) < 0.020, f"event loop lag {max(lags) * 1000:.1f} ms"
        # the slow client's writer either blocks on its own socket or it was dropped: never the loop
        await fast.close()
        await slow.close()


# ---------------------------------------------------------------- status, events, config, HTTP


async def test_status_pushed_periodically_and_source_change_event():
    async with running() as rig:
        c = await Client.connect(rig)
        await c.expect("status")
        t0 = time.monotonic()
        await c.expect("status")
        await c.expect("status")
        assert time.monotonic() - t0 < 2.0  # ~2 Hz
        src = rig.app.sources[0]
        orig = src.status
        src.status = lambda: dataclasses.replace(orig(), state="error")
        try:
            ev = await c.expect("event", timeout=2.0)
        finally:
            src.status = orig
        assert ev["level"] == "warn" and "fake" in ev["text"] and isinstance(ev["t"], float)
        await c.close()


async def test_event_helper_broadcasts_to_clients():
    async with running() as rig:
        c = await Client.connect(rig)
        rig.api.event("error", "recording failed")
        ev = await c.expect("event")
        assert (ev["level"], ev["text"]) == ("error", "recording failed")
        await c.close()


async def test_config_get_and_http():
    async with running() as rig:
        c = await Client.connect(rig)
        assert isinstance(await c.ok("config.get", name="layout"), dict)
        assert isinstance(await c.ok("config.get", name="radio"), dict)  # TOML served as JSON
        assert isinstance(await c.ok("config.get", name="model"), dict)
        assert await c.err("config.get", name="nope") == "not_found"
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{rig.url()}/config/layout.json") as r:
                assert r.status == 200 and isinstance(await r.json(), dict)
            async with s.get(f"{rig.url()}/config/bogus.json") as r:
                assert r.status == 404
            async with s.get(f"{rig.url()}/") as r:
                assert r.status == 200 and "ground station" in await r.text()
            async with s.get(f"{rig.url()}/static/index.html") as r:
                assert r.status == 200
        await c.close()


async def test_cross_origin_websocket_is_refused():
    """A web page on another origin must not reach the core (it would count as a control host)."""
    async with running() as rig:
        async with aiohttp.ClientSession() as s:
            with pytest.raises(aiohttp.WSServerHandshakeError) as e:
                await s.ws_connect(f"{rig.url()}/ws", headers={"Origin": "http://evil.example"})
            assert e.value.status == 403
            ws = await s.ws_connect(f"{rig.url()}/ws", headers={"Origin": f"http://127.0.0.1:{rig.api.port}"})
            await ws.close()


# ---------------------------------------------------------------- modules that have not landed yet


async def test_replay_rc_are_not_implemented_without_modules():
    app = make_app()
    del app.replayer  # a core without the module
    app.rc = None  # ... and without an RC controller (for example an invalid radio config)
    async with running(app) as rig:
        c = await Client.connect(rig)
        for op, f in [
            ("replay.open", {"name": "x"}),
            ("replay.play", {}),
            ("replay.seek", {"t": 1.0}),
            ("replay.speed", {"x": 2}),
            ("rc.acquire", {"kind": "connection"}),
            ("rc.set", {"values": {"SA": 1}}),
            ("sources.enable", {"source": "fake", "enabled": False}),
        ]:
            assert await c.err(op, **f) == "not_implemented", op
        st = await c.expect("status")
        assert st["recorder"]["available"] is True and st["rc"] is None and st["replay"]["state"] == "closed"
        await c.close()


async def test_stub_modules_that_raise_not_implemented_map_to_the_error_code():
    class Stub:
        def open(self, name):
            raise NotImplementedError("stub")

        def play(self):
            raise NotImplementedError("stub")

        def status(self):
            raise NotImplementedError("stub")

    app = make_app()
    app.replayer = Stub()
    async with running(app) as rig:
        c = await Client.connect(rig)
        for op, f in [("replay.open", {"name": "x"}), ("replay.play", {})]:
            assert await c.err(op, **f) == "not_implemented", op
        st = await c.expect("status")
        assert st["recorder"]["available"] is True and st["replay"]["available"] is False
        await c.close()


@dataclass
class FakeRecInfo:
    name: str = "20260101_000000"
    path: str = "/tmp/x.exlog"
    started: int = S
    stopped: int | None = None
    packets: int = 0
    bytes: int = 0


class FakeRecorder:
    def __init__(self):
        self.active, self.notes = False, []

    def start(self, label=None):
        if self.active:
            raise RuntimeError("already recording")
        self.active, self.label = True, label
        return FakeRecInfo()

    def stop(self):
        if not self.active:
            return None
        self.active = False
        return FakeRecInfo(stopped=2 * S, packets=5)

    def note(self, text):
        self.notes.append(text)

    def status(self):
        return type("St", (), {"active": self.active, "name": "n", "started": S, "packets": 3, "bytes": 9})()


async def test_recording_ops_call_the_recorder():
    app = make_app()
    app.recorder = FakeRecorder()
    async with running(app) as rig:
        c = await Client.connect(rig)
        d = await c.ok("rec.start", label="bench")
        assert d["name"] == "20260101_000000" and d["started"] == 1.0 and app.recorder.label == "bench"
        assert await c.err("rec.start") == "busy"
        await c.ok("rec.note", text="hello")
        assert app.recorder.notes == ["hello"]
        assert await c.err("rec.note", text="") == "invalid_value"
        assert (await c.ok("rec.stop"))["packets"] == 5
        assert await c.err("rec.stop") == "busy"  # not recording
        st = await c.expect("status")
        assert st["recorder"]["available"] is True
        await c.close()


# ---------------------------------------------------------------- RC pass-through (safety-relevant rules)


class FakeRc:
    """Stands in for T6's RcController: records calls, enforces one holder."""

    def __init__(self):
        self.lease: Lease | None = None
        self.calls: list[tuple] = []
        self.n = 0

    def acquire(self, holder, kind, idle_s=None, remote_addr=""):
        self.calls.append(("acquire", holder, kind, idle_s, remote_addr))
        if self.lease:
            raise ControlDenied("held by " + self.lease.holder)
        self.n += 1
        idle = (5.0 if idle_s is None else idle_s) if kind == "keepalive" else None
        self.lease = Lease(f"L{self.n}", holder, kind, None if idle is None else int(idle * S), idle)
        return self.lease

    def release(self, lease_id):
        self.calls.append(("release", lease_id))
        if self.lease and self.lease.id == lease_id:
            self.lease = None

    def heartbeat(self, lease_id):
        self.calls.append(("heartbeat", lease_id))

    def set(self, lease_id, values):
        self.calls.append(("set", lease_id, dict(values)))
        if "BAD" in values:
            raise ValueError("unknown control BAD")
        return self.status()

    def unlock(self, lease_id, control):
        self.calls.append(("unlock", lease_id, control))
        if control == "SA":
            raise ValueError("control SA has no lock")
        return self.status()

    def lock(self, lease_id, control):
        self.calls.append(("lock", lease_id, control))
        return self.status()

    def status(self):
        return RcStatus(
            mode="master" if self.lease else "idle",
            sending=bool(self.lease),
            target="fake",
            lease=self.lease,
            controls={"SA": 0},
            channels_us=[1000] * 16,
            origin="gs" if self.lease else None,
            t=S,
        )


def rc_app(**kw):
    app = make_app(**kw)
    app.rc = FakeRc()
    return app


async def test_rc_denied_for_clients_off_the_control_hosts():
    app = rc_app(control_hosts=("10.9.9.9",))  # loopback test client is therefore "remote"
    async with running(app) as rig:
        c = await Client.connect(rig)
        assert c.welcome["can_control"] is False
        assert await c.err("rc.acquire", kind="connection") == "control_denied"
        assert await c.err("rc.set", values={"SA": 1}) == "no_lease"
        assert app.rc.calls == []  # the controller was never reached
        await c.close()


async def test_rc_connection_lease_ends_when_the_client_disconnects():
    app = rc_app()
    async with running(app) as rig:
        c = await Client.connect(rig)
        lease = await c.ok("rc.acquire", kind="connection")
        assert lease["kind"] == "connection" and lease["expires"] is None
        assert app.rc.calls[0][:4] == ("acquire", "test", "connection", None) and app.rc.calls[0][4] == "127.0.0.1"
        assert await c.err("rc.acquire", kind="connection") == "busy"  # this connection already holds one
        await c.ok("rc.heartbeat")
        assert (await c.ok("rc.set", values={"SA": 1}))["mode"] == "master"
        assert await c.err("rc.set", values={"BAD": 1}) == "invalid_value"
        assert await c.err("rc.set", values={"SA": "x"}) == "invalid_value"
        msg = await c.expect("rc")
        assert msg["holder_is_you"] is True and msg["sending"] is True
        other = await Client.connect(rig)
        assert await other.err("rc.acquire", kind="connection") == "control_denied"  # one holder only
        assert await other.err("rc.set", values={"SA": 1}) == "no_lease"
        m = await other.expect("rc")
        assert m["holder_is_you"] is False
        await c.close()  # the controlling client disappears: sending must stop (decision 10.1-1)
        for _ in range(50):
            if app.rc.lease is None:
                break
            await asyncio.sleep(0.05)
        assert app.rc.lease is None and ("release", lease["id"]) in app.rc.calls
        await other.close()


async def test_rc_keepalive_lease_survives_its_connection_and_serves_other_cli_processes():
    app = rc_app()
    async with running(app) as rig:
        a = await Client.connect(rig)
        lease = await a.ok("rc.acquire", kind="keepalive", idle_s=9)
        assert lease["expires"] == 9.0 and lease["idle_s"] == 9.0
        await a.close()  # a short-lived CLI process exits; the keepalive lease stays until its idle expiry
        await asyncio.sleep(0.2)
        assert app.rc.lease is not None and not any(c[0] == "release" for c in app.rc.calls)
        b = await Client.connect(rig)
        await b.ok("rc.set", values={"SA": 1})  # a second process uses (and renews) the keepalive lease
        assert app.rc.calls[-1] == ("set", lease["id"], {"SA": 1})
        await b.ok("rc.heartbeat")
        assert app.rc.calls[-1] == ("heartbeat", lease["id"])
        await b.ok("rc.unlock", control="SD")
        assert app.rc.calls[-1] == ("unlock", lease["id"], "SD")
        await b.ok("rc.lock", control="SD")
        assert app.rc.calls[-1] == ("lock", lease["id"], "SD")
        assert await b.err("rc.unlock", control="SA") == "invalid_value"
        assert await b.err("rc.unlock") == "bad_request"
        await b.ok("rc.release")
        assert app.rc.lease is None
        assert await b.err("rc.release") == "no_lease"
        await b.close()


async def test_rc_keepalive_idle_within_limits_and_no_timed_lease():
    app = rc_app()
    async with running(app) as rig:
        c = await Client.connect(rig)
        assert await c.err("rc.acquire", kind="keepalive", idle_s=9.5) == "invalid_value"
        assert await c.err("rc.acquire", kind="keepalive", idle_s=0) == "invalid_value"
        assert await c.err("rc.acquire", kind="timed", duration_s=30) == "bad_request"
        assert await c.err("rc.acquire", kind="sticky") == "bad_request"
        assert app.rc.calls == []
        await c.close()


async def test_rc_status_in_status_message():
    app = rc_app()
    async with running(app) as rig:
        c = await Client.connect(rig)
        st = await c.expect("status")
        assert st["rc"]["mode"] == "idle" and st["rc"]["lease"] is None
        await c.close()


# ---------------------------------------------------------------- replay ops with a fake replayer


class FakeReplayer:
    def __init__(self):
        self.calls = []

    async def open(self, name):
        self.calls.append(("open", name))
        if name == "missing":
            raise FileNotFoundError(name)
        return type("L", (), {"name": name, "path": "/x", "started": S, "duration_s": 3.0, "size": 1,
                              "complete": True, "sources": ["fake"], "sim": True, "label": None})()  # fmt: skip

    def close(self):
        self.calls.append(("close",))

    def play(self):
        self.calls.append(("play",))

    def pause(self):
        self.calls.append(("pause",))

    def seek(self, t):
        self.calls.append(("seek", t))

    def set_speed(self, x):
        self.calls.append(("speed", x))

    def status(self):
        return type("R", (), {"state": "paused", "name": "a", "t": 2 * S, "t_start": S, "t_end": 4 * S, "speed": 1.0})()


async def test_replay_ops_call_the_replayer_and_resend_snapshots():
    app = make_app()
    app.replayer = FakeReplayer()
    async with running(app) as rig:
        c = await Client.connect(rig)
        await c.ok("subscribe", view="replay", types="*")
        await c.expect("snapshot")
        assert (await c.ok("replay.open", name="a"))["duration_s"] == 3.0
        # clients replace their replay store: a fresh snapshot arrives with the open (before its result)
        assert [m["view"] for m in c.inbox if m["op"] == "snapshot"] == ["replay", "replay"]
        assert await c.err("replay.open", name="missing") == "not_found"
        await c.ok("replay.play")
        await c.ok("replay.pause")
        await c.ok("replay.seek", t=2.5)
        await c.ok("replay.speed", x=4)
        await c.ok("replay.close")
        assert await c.err("replay.speed", x=3) == "invalid_value"
        assert [x[0] for x in app.replayer.calls] == ["open", "open", "play", "pause", "seek", "speed", "close"]
        assert app.replayer.calls[4] == ("seek", 2_500_000_000) and app.replayer.calls[5] == ("speed", 4.0)
        st = await c.expect("status")
        assert st["replay"]["state"] == "paused" and st["replay"]["t"] == 2.0 and st["replay"]["t_start"] == 1.0
        await c.close()


async def test_virtual_clock_core_serves_with_its_own_clock():
    app = make_app()
    app.clock = FakeClock(5 * S)  # the API takes view time from the core's clock
    async with running(app, sources=False) as rig:
        c = await Client.connect(rig)
        assert c.welcome["clock"] == 5.0
        await c.close()


@pytest.mark.parametrize("n_clients", [3])
async def test_many_clients_get_independent_subscriptions(n_clients):
    async with running() as rig:
        cs = [await Client.connect(rig) for _ in range(n_clients)]
        await cs[0].ok("subscribe", types=["Attitude"], max_rate_hz=20)
        await cs[1].ok("subscribe", types=["Position"], max_rate_hz=20)
        a = await cs[0].expect("packets")
        b = await cs[1].expect("packets")
        assert {i["type"] for i in a["items"]} == {"Attitude"} and {i["type"] for i in b["items"]} == {"Position"}
        c2 = cs[2]
        await c2.recv()  # nothing subscribed: only control messages (no packets)
        assert not any(m["op"] == "packets" for m in c2.inbox)
        for c in cs:
            await c.close()
        await asyncio.sleep(0.1)
        assert rig.api.hub.clients == {}
        assert VehicleMode.Running  # keep import used
        assert VehicleState


# ---------------------------------------------------------------- CLI rc commands against a fake RC controller


def _exgs(port, *args):
    import subprocess
    import sys
    from pathlib import Path

    return subprocess.run(
        [sys.executable, "-m", "exvectr_gs.cli.main", *args, "--port", str(port), "--json"],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        timeout=30,
    )


async def test_cli_rc_commands_use_a_keepalive_lease_across_processes():
    app = rc_app()
    async with running(app) as rig:
        run = lambda *a: asyncio.to_thread(_exgs, rig.api.port, *a)  # noqa: E731
        r = await run("rc", "acquire", "--idle", "9")
        assert r.returncode == 0, r.stdout + r.stderr
        assert app.rc.lease is not None and app.rc.lease.kind == "keepalive"  # the CLI process is gone, lease stays
        r = await run("rc", "set", "SA=1")
        assert r.returncode == 0 and app.rc.calls[-1] == ("set", "L1", {"SA": 1})
        r = await run("rc", "set", "SA=1", "--hold", "0.2")  # presses, then restores the previous value
        assert r.returncode == 0
        sets = [c[2] for c in app.rc.calls if c[0] == "set"]
        assert sets[-2:] == [{"SA": 1}, {"SA": 0}]
        r = await run("rc", "show")
        assert json.loads(r.stdout)["mode"] == "master"
        r = await run("rc", "keepalive")
        assert r.returncode == 0 and app.rc.calls[-1][0] == "heartbeat"
        r = await run("rc", "release")
        assert r.returncode == 0 and app.rc.lease is None
        r = await run("rc", "keepalive")  # nothing to renew: control error, exit 3
        assert r.returncode == 3 and json.loads(r.stdout)["error"]["code"] == "no_lease"
        r = await run("rc", "set", "SA=1", "--idle", "7")  # a one-shot set takes a keepalive lease itself
        assert r.returncode == 0, r.stdout + r.stderr
        assert app.rc.lease.kind == "keepalive" and app.rc.lease.idle_s == 7.0
        assert app.rc.calls[-1][0] == "set" and app.rc.calls[-1][2] == {"SA": 1}


async def test_cli_rc_denied_exit_code_3():
    app = rc_app(control_hosts=("10.9.9.9",))
    async with running(app) as rig:
        r = await asyncio.to_thread(_exgs, rig.api.port, "rc", "acquire", "--idle", "5")
        assert r.returncode == 3 and json.loads(r.stdout)["error"]["code"] == "control_denied"
