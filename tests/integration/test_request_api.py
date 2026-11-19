"""REQUEST sender through the API with the FakeSource as the answering vehicle (docs/requests.md)."""

import asyncio
import contextlib
import tempfile

import pytest

from exvectr_gs.core.app import CoreApp
from exvectr_gs.core.config import parse_config
from exvectr_gs.packets.defs.host import GsEventKind

from .test_api import Client, running

ACTIONS = [
    {"action": "CALIB_GYRO_START", "target": 1, "label": "Gyro start", "group": "Calibration"},
    {"action": "CALIB_APPLY", "target": 1, "label": "Apply", "group": "Calibration"},
    {"action": "SAVE_CONFIG", "target": 1, "label": "Save", "group": "Vehicle"},
    {"action": "LINK_UPLOAD_MODE", "target": 2, "label": "TX upload", "group": "Link", "confirm": "danger"},
    {"action": "LINK_UPLOAD_MODE", "target": 3, "label": "RX upload", "group": "Link", "confirm": "danger"},
]


def make_app(control_hosts=("127.0.0.1", "::1"), actions=ACTIONS, **fake):
    cfg = parse_config(
        {
            "core": {"source_priority": ["fake"]},
            "server": {"bind": "127.0.0.1", "port": 1, "control_hosts": list(control_hosts)},
            "source": [{"id": "fake", "kind": "fake", "answer_requests": True, "seed": 1, **fake}],
            "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
            "requests": {"action": actions},
        }
    )
    return CoreApp(cfg)


@contextlib.asynccontextmanager
async def req_running(app=None):
    app = app or make_app()
    async with running(app) as rig:
        task = asyncio.create_task(app.requests.run())
        events = []
        app.bus.subscribe(lambda e: e.kind.name.startswith("request_") and events.append(e), {"GsEvent"})
        rig.events = events
        try:
            yield rig
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def final(c, req_id, timeout=8.0):
    """The pushed `requests` message in which the request has left `inflight`."""
    end = asyncio.get_running_loop().time() + timeout
    seen = 0
    while True:
        while seen < len(c.inbox):  # pushes that arrived while another call was waiting are in the inbox
            m = c.inbox[seen]
            seen += 1
            if m["op"] == "requests" and not any(r["req_id"] == req_id for r in m["inflight"]):
                found = [r for r in m["recent"] if r["req_id"] == req_id]
                if found:
                    return found[0]
        await c.recv(max(0.01, end - asyncio.get_running_loop().time()))


async def test_list_status_and_hello_push():
    async with req_running() as rig:
        c = await Client.connect(rig)
        wl = (await c.ok("request.list"))["actions"]
        assert [(e["action"], e["target"], e["confirm"]) for e in wl][-2:] == [
            ("LINK_UPLOAD_MODE", 2, "danger"),
            ("LINK_UPLOAD_MODE", 3, "danger"),
        ]
        assert wl[0]["group"] == "Calibration" and wl[0]["label"] == "Gyro start" and wl[0]["route"] == "fake"
        assert await c.ok("request.status") == {"inflight": [], "recent": []}
        assert any(m["op"] == "requests" for m in c.inbox)  # pushed after hello
        await c.close()


async def test_send_acked_status_shown_and_event_logged():
    async with req_running() as rig:
        c = await Client.connect(rig)
        r = await c.ok("request.send", action="CALIB_APPLY", confirm="CALIB_APPLY")
        assert r["state"] == "sending" and r["req_id"] != 0 and r["attempts"] == 1 and r["target"] == 1
        done = await final(c, r["req_id"])
        assert (done["state"], done["status_name"], done["attempts"]) == ("acked", "DONE", 1)
        assert (await c.ok("request.status"))["recent"][0]["req_id"] == r["req_id"]
        fake = rig.app.sources[0]
        assert [(x[1], x[2]) for x in fake.executed_requests] == [(7, 1)]
        kinds = [e.kind for e in rig.events]
        assert kinds == [GsEventKind.request_sent, GsEventKind.request_ack]
        await c.close()


async def test_vehicle_reported_status_is_shown_exactly_never_assumed_success():
    async with req_running(make_app(request_status=2, request_detail=5)) as rig:
        c = await Client.connect(rig)
        r = await c.ok("request.send", action="CALIB_APPLY", confirm="CALIB_APPLY")
        done = await final(c, r["req_id"])
        assert (done["state"], done["status"], done["status_name"], done["detail"]) == ("acked", 2, "REJECTED_STATE", 5)
        await c.close()


async def test_retry_until_ack_reuses_the_id_and_vehicle_executes_once():
    async with req_running(make_app(request_drop_first=2)) as rig:
        c = await Client.connect(rig)
        r = await c.ok("request.send", action="SAVE_CONFIG", confirm="SAVE_CONFIG")
        done = await final(c, r["req_id"])
        assert (done["state"], done["attempts"], done["status_name"]) == ("acked", 3, "DONE")
        fake = rig.app.sources[0]
        assert [p.req_id for p in fake.received_requests] == [r["req_id"]] * 3
        assert len(fake.executed_requests) == 1
        assert GsEventKind.request_retry in [e.kind for e in rig.events]
        await c.close()


async def test_timeout_after_five_attempts_is_shown_as_timeout():
    async with req_running(make_app(request_drop_first=99)) as rig:
        c = await Client.connect(rig)
        r = await c.ok("request.send", action="SAVE_CONFIG", confirm="SAVE_CONFIG")
        await asyncio.sleep(0.55)  # past the rate limit
        assert await c.err("request.send", action="SAVE_CONFIG", confirm="SAVE_CONFIG") == "request_in_flight"
        done = await final(c, r["req_id"], timeout=9)
        assert (done["state"], done["attempts"], done["status"]) == ("timeout", 5, None)
        assert len(rig.app.sources[0].received_requests) == 5
        assert rig.events[-1].kind == GsEventKind.request_timeout
        await c.close()


async def test_confirmation_whitelist_and_danger_enforced_nothing_reaches_the_source():
    async with req_running() as rig:
        c = await Client.connect(rig)
        assert await c.err("request.send", action="CALIB_APPLY") == "confirm_required"
        assert await c.err("request.send", action="CALIB_APPLY", confirm="SAVE_CONFIG") == "confirm_required"
        assert (
            await c.err("request.send", action="LINK_UPLOAD_MODE", target=2, confirm="LINK_UPLOAD_MODE")
            == "confirm_required"
        )
        assert await c.err("request.send", action="SYSTEM_RESET", confirm="SYSTEM_RESET") == "not_whitelisted"
        assert await c.err("request.send", action="CALIB_APPLY", target=2, confirm="CALIB_APPLY") == "not_whitelisted"
        assert (
            await c.err(
                "request.send", action="LINK_UPLOAD_MODE", confirm="LINK_UPLOAD_MODE", understand_link_stops=True
            )
            == "not_whitelisted"
        )
        assert await c.err("request.send", action=5) == "bad_request"
        assert await c.err("request.send", confirm="x") == "bad_request"
        assert await c.err("request.send", action="CALIB_APPLY", target="1") == "bad_request"
        assert await c.err("request.send", action="CALIB_APPLY", understand_link_stops="yes") == "bad_request"
        assert len(rig.app.sources[0].received_requests) == 0 and rig.events == []
        ok = await c.ok(
            "request.send", action="LINK_UPLOAD_MODE", target=2, confirm="LINK_UPLOAD_MODE", understand_link_stops=True
        )
        assert ok["confirm"] == "danger" and ok["target"] == 2
        await c.close()


async def test_rate_limit_500ms_per_client():
    async with req_running() as rig:
        c = await Client.connect(rig)
        await c.ok("request.send", action="CALIB_APPLY", confirm="CALIB_APPLY")
        assert await c.err("request.send", action="SAVE_CONFIG", confirm="SAVE_CONFIG") == "rate_limited"
        await asyncio.sleep(0.55)
        await c.ok("request.send", action="SAVE_CONFIG", confirm="SAVE_CONFIG")
        await c.close()


async def test_only_control_hosts_may_send_but_everybody_may_look():
    async with req_running(make_app(control_hosts=("10.99.99.99",))) as rig:
        c = await Client.connect(rig)
        assert c.welcome["can_control"] is False
        assert await c.err("request.send", action="CALIB_APPLY", confirm="CALIB_APPLY") == "control_denied"
        assert (await c.ok("request.list"))["actions"] and await c.ok("request.status")
        assert len(rig.app.sources[0].received_requests) == 0
        await c.close()


async def test_controller_checks_the_address_even_if_the_api_did_not():
    from exvectr_gs.requests import RequestError

    async with req_running(make_app(control_hosts=("127.0.0.1",))) as rig:
        with pytest.raises(RequestError) as e:
            await rig.app.requests.send("CALIB_APPLY", None, client="x", remote_addr="", confirm="CALIB_APPLY")
        assert e.value.code == "control_denied"


async def test_no_connected_source_refuses_clearly_and_queues_nothing():
    app = make_app(profile="usb", faults=[{"kind": "disconnect", "at": 0.0, "duration": 600}])
    async with req_running(app) as rig:
        await asyncio.sleep(0.1)
        c = await Client.connect(rig)
        r = await c.call("request.send", action="CALIB_APPLY", confirm="CALIB_APPLY")
        assert (
            not r["ok"] and r["error"]["code"] == "no_route" and "nothing was sent or queued" in r["error"]["message"]
        )
        assert await c.ok("request.status") == {"inflight": [], "recent": []}
        await c.close()


async def test_not_available_without_a_whitelist_entry_for_the_target_node():
    app = make_app(request_targets=[3])  # the fake source cannot reach node 1
    async with req_running(app) as rig:
        c = await Client.connect(rig)
        assert await c.err("request.send", action="CALIB_APPLY", confirm="CALIB_APPLY") == "no_route"
        await c.close()
