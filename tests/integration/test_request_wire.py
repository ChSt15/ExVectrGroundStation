"""REQUEST over the exvectrnet-1 wire against the pty fake vehicle and fake TX module (two implementations of the
frame format). The fake vehicle's ACK logic is a test double only."""

import asyncio
import contextlib
import tempfile

import exvectr_packets as xp

from exvectr_gs.core.app import CoreApp
from exvectr_gs.core.config import parse_config

from ..support_pty import FakeVehiclePty, wait_for
from .test_api import Client
from .test_api import running as api_running

ACTIONS = [
    {"action": "CALIB_APPLY", "target": 1, "label": "Apply", "group": "Calibration"},
    {"action": "LINK_UPLOAD_MODE", "target": 2, "label": "TX upload", "group": "Link", "confirm": "danger"},
    {"action": "LINK_UPLOAD_MODE", "target": 3, "label": "RX upload", "group": "Link", "confirm": "danger"},
]


def make_vehicle(tmp_path, name, role="vehicle", **kw):
    v = FakeVehiclePty(tmp_path / name, speed=2.0, profile="exvectrnet-1", role=role, hb_hz=10.0, **kw)
    v.start()
    return v


def make_app(v1, v2, preference=None, usb=True):
    sources = [
        {"id": "link", "kind": "link_module", "match": {"device": str(v2.link)}, "rc_role": "mirror", "poll_s": 0.05},
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
    req = {"action": ACTIONS}
    if preference:
        req["source_preference"] = preference
    return CoreApp(
        parse_config(
            {
                "core": {"wire_profile": "exvectrnet-1", "source_priority": [x["id"] for x in sources]},
                "server": {"bind": "127.0.0.1", "port": 1},
                "source": sources,
                "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
                "requests": req,
            }
        )
    )


@contextlib.asynccontextmanager
async def started(app):
    async with api_running(app) as rig:
        task = asyncio.create_task(app.requests.run())
        try:
            await wait_for(lambda: all(s.status().state == "connected" for s in app.sources), what="sources")
            yield rig
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def done(c, req_id, timeout=8.0):
    for _ in range(int(timeout * 20)):
        st = (await c.ok("request.status"))["recent"]
        hit = [r for r in st if r["req_id"] == req_id]
        if hit:
            return hit[0]
        await asyncio.sleep(0.05)
    raise AssertionError("request never finished")


async def test_requests_are_routed_by_target_and_acked_over_the_wire(tmp_path):
    v1 = make_vehicle(tmp_path, "usb")
    v2 = make_vehicle(tmp_path, "radio", role="txmodule")
    try:
        async with started(make_app(v1, v2)) as rig:
            c = await Client.connect(rig)
            r = await c.ok("request.send", action="CALIB_APPLY", confirm="CALIB_APPLY")
            assert r["source"] == "usb"  # target 1: the vehicle's USB first
            d = await done(c, r["req_id"])
            assert (d["state"], d["status_name"]) == ("acked", "DONE")
            [req] = v1.received_requests
            assert (req["req_id"], req["action"], req["target"], req["dst"], req["src"]) == (
                r["req_id"], xp.RequestAction.CALIB_APPLY, 1, 1, 0,
            )  # fmt: skip
            assert v2.received_requests == []
            await asyncio.sleep(0.55)
            r2 = await c.ok(
                "request.send",
                action="LINK_UPLOAD_MODE",
                target=2,
                confirm="LINK_UPLOAD_MODE",
                understand_link_stops=True,
            )
            assert r2["source"] == "link"  # target 2: only the TX module reaches it
            d2 = await done(c, r2["req_id"])
            assert d2["status_name"] == "DONE" and v1.received_requests == [req]
            assert (v2.received_requests[0]["action"], v2.received_requests[0]["dst"]) == (
                xp.RequestAction.LINK_UPLOAD_MODE,
                2,
            )
            assert r2["req_id"] != r["req_id"]
            await c.close()
    finally:
        v1.stop()
        v2.stop()


async def test_target_1_goes_over_the_air_when_the_vehicle_usb_is_absent(tmp_path):
    v1 = make_vehicle(tmp_path, "usb")
    v2 = make_vehicle(tmp_path, "radio", role="txmodule")
    try:
        async with started(make_app(v1, v2, usb=False)) as rig:
            c = await Client.connect(rig)
            r = await c.ok("request.send", action="CALIB_APPLY", confirm="CALIB_APPLY")
            assert r["source"] == "link"
            await wait_for(lambda: v2.received_requests, what="request at the TX module")
            req = v2.received_requests[0]
            assert (req["dst"], req["target"]) == (1, 1)  # addressed to the vehicle, the TX module forwards it
            assert req["dst"] == 1 and any(f["port"] == 0x11 for f in v2.forwarded if f["type"] == "data")
            await c.close()
    finally:
        v1.stop()
        v2.stop()


async def test_retries_cross_the_wire_with_the_same_id(tmp_path):
    v1 = make_vehicle(tmp_path, "usb", request_drop_first=2)
    v2 = make_vehicle(tmp_path, "radio", role="txmodule")
    try:
        async with started(make_app(v1, v2)) as rig:
            c = await Client.connect(rig)
            r = await c.ok("request.send", action="CALIB_APPLY", confirm="CALIB_APPLY")
            d = await done(c, r["req_id"])
            assert (d["state"], d["attempts"]) == ("acked", 3)
            assert [q["req_id"] for q in v1.received_requests] == [r["req_id"]] * 3
            await c.close()
    finally:
        v1.stop()
        v2.stop()


async def test_vehicle_rejection_is_shown_as_reported(tmp_path):
    v1 = make_vehicle(tmp_path, "usb", request_status=int(xp.RequestStatus.REJECTED_STATE))
    v2 = make_vehicle(tmp_path, "radio", role="txmodule")
    try:
        async with started(make_app(v1, v2)) as rig:
            c = await Client.connect(rig)
            r = await c.ok("request.send", action="CALIB_APPLY", confirm="CALIB_APPLY")
            d = await done(c, r["req_id"])
            assert (d["state"], d["status_name"]) == ("acked", "REJECTED_STATE")
            await c.close()
    finally:
        v1.stop()
        v2.stop()


async def test_request_to_an_unplugged_source_is_refused_not_queued(tmp_path):
    v1 = make_vehicle(tmp_path, "usb")
    v2 = make_vehicle(tmp_path, "radio", role="txmodule")
    try:
        async with started(make_app(v1, v2)) as rig:
            c = await Client.connect(rig)
            v2.stop()  # TX module unplugged
            await wait_for(lambda: rig.app.sources[0].status().state != "connected", what="unplug")
            assert (
                await c.err(
                    "request.send",
                    action="LINK_UPLOAD_MODE",
                    target=3,
                    confirm="LINK_UPLOAD_MODE",
                    understand_link_stops=True,
                )
                == "no_route"
            )
            v2.start()  # replug: nothing is sent by itself
            await wait_for(lambda: rig.app.sources[0].status().state == "connected", what="replug")
            await asyncio.sleep(0.3)
            assert v2.received_requests == [] and v1.received_requests == []
            await c.close()
    finally:
        v1.stop()
        v2.stop()


async def test_serial_source_refuses_a_target_it_cannot_reach(tmp_path):
    from exvectr_gs.core.config import SourceConfig
    from exvectr_gs.packets.defs.link import Request, RequestAction
    from exvectr_gs.sources import SerialSource
    from exvectr_gs.wire import load_profile

    v = make_vehicle(tmp_path, "usb")
    src = SerialSource(
        SourceConfig(id="usb", kind="vehicle_usb", match={"device": str(v.link)}, options={"poll_s": 0.05}),
        load_profile("exvectrnet-1"),
    )
    assert src.caps.request_targets == (1,)
    task = asyncio.create_task(src.run(lambda p: None))
    try:
        await wait_for(lambda: src.status().state == "connected")

        def req(target):
            return Request(t=1, req_id=3, action=RequestAction.CALIB_APPLY, target=target, arg_i=0, arg_f=0.0)

        assert await src.send(req(2)) is False and await src.send(req(3)) is False
        assert await src.send(req(1)) is True
        await wait_for(lambda: len(v.received_requests) == 1, what="the request")
        await asyncio.sleep(0.2)
        assert [r["dst"] for r in v.received_requests] == [1]
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        v.stop()
