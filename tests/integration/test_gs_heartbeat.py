"""Ground-station heartbeat (node 0, 1 Hz): sent on every connected serial source, independent of RC leases."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile

import exvectr_packets as xp
import pytest

from exvectr_gs.core.app import CoreApp
from exvectr_gs.core.config import parse_config
from exvectr_gs.packets.defs.link import RcOrigin, RcState
from exvectr_gs.sources import SerialSource
from exvectr_gs.wire import load_profile

from ..support_pty import FakeVehiclePty, wait_for
from .test_exvectrnet_e2e import cfg, running

pytestmark = pytest.mark.asyncio


def make(tmp_path, name, role="vehicle"):
    v = FakeVehiclePty(tmp_path / name, speed=2.0, profile="exvectrnet-1", role=role, hb_hz=10.0)
    v.start()
    return v


def make_app(vehicles):
    sources = [
        {"id": sid, "kind": kind, "match": {"device": str(v.link)}, "rc_role": role, "poll_s": 0.05}
        for sid, kind, role, v in vehicles
    ]
    conf = parse_config(
        {
            "core": {"wire_profile": "exvectrnet-1", "source_priority": [s["id"] for s in sources]},
            "server": {"bind": "127.0.0.1", "port": 1},
            "source": sources,
            "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
        }
    )
    return CoreApp(conf)


@contextlib.asynccontextmanager
async def app_running(app):
    task = asyncio.create_task(app.run(summary=lambda _s: None))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_heartbeat_to_control_mirror_and_none_sources_at_1hz(tmp_path):
    vs = {r: make(tmp_path, r, "vehicle" if r == "control" else "txmodule") for r in ("control", "mirror", "none")}
    try:
        app = make_app(
            [
                ("c", "vehicle_usb", "control", vs["control"]),
                ("m", "link_module", "mirror", vs["mirror"]),
                ("n", "link_module", "none", vs["none"]),
            ]
        )
        async with app_running(app):
            await wait_for(lambda: all(len(v.heartbeats_from(0)) >= 4 for v in vs.values()), timeout=8, what="4 hb")
            for v in vs.values():
                hbs = v.heartbeats_from(0)
                ts = [h["t"] for h in hbs]
                gaps = [(b - a) / 1e9 for a, b in zip(ts, ts[1:], strict=False)]
                assert all(0.85 < g < 1.15 for g in gaps), gaps  # 1 Hz
                h0 = hbs[0]
                assert h0["ctrl"] == 0x56 and h0["ttl"] == 3 and h0["dst"] == 0xFF and h0["src"] == 0
                assert len(h0["payload"]) == 11
                up = [xp.Heartbeat.decode(h["payload"]).uptime for h in hbs]
                assert up == sorted(up) and up[-1] - up[0] == pytest.approx(len(up) - 1, abs=0.3)
                assert not [f for f in v.received if f.channel == 0x10]  # nothing but heartbeats: no RC
    finally:
        for v in vs.values():
            v.stop()


async def test_txmodule_does_not_forward_node0_heartbeats_to_its_vehicle_side(tmp_path):
    v = make(tmp_path, "tx", "txmodule")
    try:
        src = SerialSource(cfg(v.link, "link", "link_module", "mirror"), load_profile("exvectrnet-1"))
        async with running(src):
            await wait_for(lambda: src.status().state == "connected")
            assert await src.send_heartbeat(1.0) is True
            await wait_for(lambda: v.heartbeats_from(0), what="hb")
            await asyncio.sleep(0.1)
            assert not [f for f in v.forwarded if f["type"] == "heartbeat" and f["src"] == 0]
    finally:
        v.stop()


async def test_no_heartbeat_when_down_and_resumes_after_replug(tmp_path):
    v = make(tmp_path, "veh")
    src = SerialSource(cfg(v.link), load_profile("exvectrnet-1"))
    try:
        assert await src.send_heartbeat(1.0) is False  # not connected yet
        async with running(src):
            await wait_for(lambda: src.status().state == "connected")
            assert await src.send_heartbeat(1.0) is True
            await wait_for(lambda: len(v.heartbeats_from(0)) == 1, what="first hb")
            v.stop()  # unplug
            await wait_for(lambda: src.status().state != "connected", what="down")
            assert await src.send_heartbeat(2.0) is False
            v.start()  # replug
            await wait_for(lambda: src.status().state == "connected", what="up")
            assert await src.send_heartbeat(3.0) is True
            await wait_for(lambda: len(v.heartbeats_from(0)) == 2, what="hb after replug")
    finally:
        v.stop()


async def test_full_queue_drops_heartbeat_silently_without_blocking(tmp_path):
    v = make(tmp_path, "veh")
    src = SerialSource(cfg(v.link), load_profile("exvectrnet-1"))
    try:
        async with running(src):
            await wait_for(lambda: src.status().state == "connected")
            conn = src._conn
            conn.stop.set()  # freeze the writer thread so the queue cannot drain
            await asyncio.sleep(0.2)
            conn.stop.clear()
            for _ in range(conn.tx.maxsize):
                conn.tx.put_nowait(b"x")
            before = list(conn.tx.queue)
            assert await asyncio.wait_for(src.send_heartbeat(1.0), 0.5) is False
            assert list(conn.tx.queue) == before  # nothing displaced
    finally:
        v.stop()


async def test_provisional_profile_and_fake_source_send_nothing(tmp_path):
    v = FakeVehiclePty(tmp_path / "p", speed=2.0)
    v.start()
    try:
        src = SerialSource(cfg(v.link), load_profile("provisional-1"))
        async with running(src):
            await wait_for(lambda: src.status().state == "connected")
            assert await src.send_heartbeat(1.0) is False
    finally:
        v.stop()


async def test_rc_stays_silent_while_heartbeats_continue_and_lease_logic_is_unaffected(tmp_path):
    v = make(tmp_path, "veh")
    try:
        app = make_app([("usb", "vehicle_usb", "control", v)])
        async with app_running(app):
            await wait_for(lambda: len(v.heartbeats_from(0)) >= 3, timeout=8, what="3 hb")
            assert not [f for f in v.received if f.channel == 0x10]  # no lease, no RC, heartbeats flowing
            # an explicit RC send through the control source still works and is the only RC path
            chs = [1500] * 16
            assert await app.sources[0].send(RcState(origin=RcOrigin.gs, ch_us=chs, seq=1)) is True
            await wait_for(lambda: [f for f in v.received if f.channel == 0x10], what="rc")
            n = len(v.heartbeats_from(0))
            await wait_for(lambda: len(v.heartbeats_from(0)) > n, timeout=3, what="hb continues")
    finally:
        v.stop()
