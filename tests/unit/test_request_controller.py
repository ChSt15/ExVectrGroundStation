"""Request controller rules (docs/requests.md) with a fake clock and scripted sources. No I/O, no hardware."""

import pytest

from exvectr_gs.core import Bus
from exvectr_gs.core.clock import FakeClock
from exvectr_gs.core.config import ConfigError, RequestEntry, RequestsSection, parse_config
from exvectr_gs.packets.defs.host import GsEventKind
from exvectr_gs.packets.defs.link import RequestAck, RequestAction, RequestStatus
from exvectr_gs.requests import RequestController, RequestError
from exvectr_gs.requests.controller import MAX_ATTEMPTS
from exvectr_gs.sources.base import SourceCaps, SourceKind, SourceStatus

S = 1_000_000_000


class StubSource:
    def __init__(self, sid, kind, targets, state="connected"):
        self.id, self.kind, self.state, self.accept = sid, kind, state, True
        self.caps = SourceCaps(rc_role="none", can_send=True, request_targets=targets)
        self.sent = []

    async def send(self, pkt):
        if not self.accept:
            return False
        self.sent.append(pkt)
        return True

    def status(self):
        return SourceStatus(self.id, self.kind, True, self.state, None, 0, 0, None)  # type: ignore[arg-type]


WHITELIST = [
    RequestEntry("CALIB_GYRO_START", 1, "Gyro start", "Calibration"),
    RequestEntry("CALIB_APPLY", 1, "Apply", "Calibration"),
    RequestEntry("LINK_UPLOAD_MODE", 2, "TX upload", "Link", "danger"),
    RequestEntry("LINK_UPLOAD_MODE", 3, "RX upload", "Link", "danger"),
]


class Rig:
    def __init__(self, sources=None, first_id=7, hosts=("127.0.0.1",), whitelist=WHITELIST, pref=None):
        self.clock = FakeClock(1000 * S)
        self.bus = Bus()
        self.events = []
        self.acks = []
        self.bus.subscribe(self.events.append, {"GsEvent"})
        self.usb = StubSource("usb", SourceKind.VEHICLE_USB, (1,))
        self.link = StubSource("link", SourceKind.LINK_MODULE, (1, 2, 3))
        self.sources = sources if sources is not None else [self.link, self.usb]  # config order must not matter
        self.rc = RequestController(
            RequestsSection(source_preference=pref, actions=list(whitelist)),
            self.sources, self.bus, clock=self.clock, control_hosts=list(hosts), first_id=first_id,
        )  # fmt: skip

    async def send(self, action="CALIB_APPLY", target=None, addr="127.0.0.1", client="c", **kw):
        kw.setdefault("confirm", action)
        return await self.rc.send(action, target, client=client, remote_addr=addr, **kw)

    def ack(self, req_id, status=0, detail=0, src="usb"):
        self.bus.publish(RequestAck(t=self.clock.now(), src=src, req_id=req_id, status=status, detail=detail))

    def kinds(self):
        return [e.kind for e in self.events]


async def test_send_builds_request_and_logs_event():
    r = Rig()
    st = await r.send("CALIB_APPLY")
    assert (st.req_id, st.action, st.target, st.state, st.attempts, st.source) == (
        7,
        "CALIB_APPLY",
        1,
        "sending",
        1,
        "usb",
    )
    pkt = r.usb.sent[0]
    assert (pkt.TYPE, pkt.req_id, pkt.action, pkt.target) == ("Request", 7, RequestAction.CALIB_APPLY, 1)
    assert r.kinds() == [GsEventKind.request_sent]
    assert "CALIB_APPLY" in r.events[0].detail and "id 7" in r.events[0].detail


async def test_ids_monotonic_wrap_skip_zero_and_new_per_action():
    r = Rig(first_id=0xFFFE)
    a = await r.send("CALIB_APPLY")
    r.ack(a.req_id)
    r.clock.advance(1)
    b = await r.send("CALIB_APPLY")
    r.ack(b.req_id)
    r.clock.advance(1)
    c = await r.send("CALIB_APPLY")
    assert (a.req_id, b.req_id, c.req_id) == (0xFFFE, 0xFFFF, 1)  # wraps over 0


async def test_start_id_is_random_and_never_zero():
    ids = set()
    for _ in range(20):
        rc = RequestController(RequestsSection(actions=WHITELIST), [], Bus())
        ids.add(rc._new_id())
    assert 0 not in ids and len(ids) > 1


async def test_retry_every_second_same_id_up_to_five_attempts_then_timeout():
    r = Rig()
    st = await r.send("CALIB_APPLY")
    await r.rc.tick()
    assert len(r.usb.sent) == 1  # nothing before 1 s
    for n in range(2, MAX_ATTEMPTS + 1):
        r.clock.advance(0.99)
        await r.rc.tick()
        assert len(r.usb.sent) == n - 1
        r.clock.advance(0.01)
        await r.rc.tick()
        assert len(r.usb.sent) == n and st.attempts == n
    assert {p.req_id for p in r.usb.sent} == {7}  # reused on every retry
    assert st.state == "sending"
    r.clock.advance(1.0)
    await r.rc.tick()
    assert st.state == "timeout" and len(r.usb.sent) == 5  # no sixth attempt
    assert r.rc.status()["inflight"] == [] and r.rc.status()["recent"][0]["state"] == "timeout"
    assert r.kinds()[-1] == GsEventKind.request_timeout
    r.clock.advance(10)
    await r.rc.tick()
    assert len(r.usb.sent) == 5


async def test_ack_stops_retries_at_once_and_shows_status_as_reported():
    r = Rig()
    st = await r.send("CALIB_APPLY")
    r.clock.advance(1.0)
    await r.rc.tick()
    assert len(r.usb.sent) == 2
    r.ack(7, status=RequestStatus.REJECTED_STATE, detail=9)
    assert st.state == "acked" and (st.status, st.status_name, st.detail) == (2, "REJECTED_STATE", 9)
    r.clock.advance(5)
    await r.rc.tick()
    assert len(r.usb.sent) == 2 and st.state == "acked"
    assert r.kinds()[-1] == GsEventKind.request_ack
    assert r.rc.status()["recent"][0]["status_name"] == "REJECTED_STATE"


async def test_unknown_status_value_is_shown_not_assumed():
    r = Rig()
    st = await r.send("CALIB_APPLY")
    r.ack(7, status=200)
    assert st.status_name == "#200" and st.state == "acked"


async def test_ack_with_other_id_is_ignored_and_later_final_ack_updates_history():
    r = Rig()
    st = await r.send("CALIB_APPLY")
    r.ack(99)
    assert st.state == "sending"
    r.ack(7, status=RequestStatus.IN_PROGRESS)
    assert st.status_name == "IN_PROGRESS" and st.state == "acked"
    r.ack(7, status=RequestStatus.DONE)
    assert r.rc.status()["recent"][0]["status_name"] == "DONE"


async def test_one_in_flight_per_action_and_target():
    r = Rig()
    await r.send("CALIB_APPLY")
    r.clock.advance(1)  # past the rate limit
    with pytest.raises(RequestError) as e:
        await r.send("CALIB_APPLY")
    assert e.value.code == "request_in_flight"
    r.clock.advance(0.5)
    await r.send("CALIB_GYRO_START")  # a different action is fine
    r.clock.advance(0.5)
    await r.send("LINK_UPLOAD_MODE", 2, understand_link_stops=True, client="d")
    r.clock.advance(1)
    await r.send("LINK_UPLOAD_MODE", 3, understand_link_stops=True)  # same action, other target
    r.ack(7)
    r.clock.advance(1)
    await r.send("CALIB_APPLY")  # acked: free again, with a new id
    assert len(r.rc.status()["inflight"]) == 4


async def test_whitelist_enforced_unknown_action_wrong_target_and_ambiguous_target():
    r = Rig()
    for action, target in (("SYSTEM_RESET", None), ("NOPE", None), ("CALIB_APPLY", 2), ("CALIB_APPLY", 99)):
        with pytest.raises(RequestError) as e:
            await r.send(action, target, confirm=action)
        assert e.value.code == "not_whitelisted", (action, target)
    with pytest.raises(RequestError) as e:  # LINK_UPLOAD_MODE exists for 2 and 3: target must be named
        await r.send("LINK_UPLOAD_MODE", None, understand_link_stops=True)
    assert e.value.code == "not_whitelisted" and "target" in e.value.message
    with pytest.raises(RequestError) as e:
        await r.send("LINK_UPLOAD_MODE", 1, understand_link_stops=True)  # node 1 is not whitelisted for it
    assert e.value.code == "not_whitelisted"
    with pytest.raises(RequestError):
        await r.send(5, None)  # type: ignore[arg-type]
    assert r.usb.sent == [] and r.link.sent == []


async def test_empty_whitelist_sends_nothing():
    r = Rig(whitelist=[])
    with pytest.raises(RequestError) as e:
        await r.send("CALIB_APPLY")
    assert e.value.code == "not_whitelisted"


async def test_confirmation_required_and_danger_acknowledgement():
    r = Rig()
    for conf in (None, "", "calib_apply", "CALIB_GYRO_START"):
        with pytest.raises(RequestError) as e:
            await r.send("CALIB_APPLY", confirm=conf)
        assert e.value.code == "confirm_required"
    with pytest.raises(RequestError) as e:
        await r.send("LINK_UPLOAD_MODE", 2)  # danger without the acknowledgement
    assert e.value.code == "confirm_required"
    assert r.usb.sent == [] and r.link.sent == [] and r.events == []
    st = await r.send("LINK_UPLOAD_MODE", 2, understand_link_stops=True)
    assert st.confirm == "danger"


async def test_control_host_only_and_rate_limit_per_client_host():
    r = Rig(hosts=["127.0.0.1"])
    for addr in ("10.0.0.5", "", "garbage"):
        with pytest.raises(RequestError) as e:
            await r.send("CALIB_APPLY", addr=addr)
        assert e.value.code == "control_denied"
    await r.send("CALIB_APPLY")
    r.clock.advance(0.49)
    with pytest.raises(RequestError) as e:
        await r.send("CALIB_GYRO_START", client="another-connection")  # same host: still limited
    assert e.value.code == "rate_limited"
    r.clock.advance(0.01)
    await r.send("CALIB_GYRO_START")
    assert len(r.rc.status()["inflight"]) == 2


async def test_refusals_do_not_consume_the_rate_limit():
    r = Rig()
    with pytest.raises(RequestError):
        await r.send("CALIB_APPLY", confirm=None)
    await r.send("CALIB_APPLY")


async def test_routing_prefers_vehicle_usb_for_target_1_then_air():
    r = Rig()
    st = await r.send("CALIB_APPLY")
    assert st.source == "usb" and r.link.sent == []
    r.usb.state = "searching"  # USB unplugged: the next request goes via the TX module
    r.clock.advance(1)
    st2 = await r.send("CALIB_GYRO_START")
    assert st2.source == "link" and r.link.sent[0].target == 1
    r.usb.state = "connected"
    r.clock.advance(1)  # retry of an in-flight request re-routes too (CALIB_APPLY was sent 2 s ago)
    await r.rc.tick()
    assert {p.req_id for p in r.usb.sent[1:]} == {st.req_id, st2.req_id}  # both re-sent, now via the USB


async def test_link_targets_go_through_the_tx_module_only():
    r = Rig()
    st = await r.send("LINK_UPLOAD_MODE", 2, understand_link_stops=True)
    assert st.source == "link" and r.usb.sent == []
    r.clock.advance(1)
    st3 = await r.send("LINK_UPLOAD_MODE", 3, understand_link_stops=True)
    assert st3.source == "link" and [p.target for p in r.link.sent] == [2, 3]
    assert r.rc.route(2) == "link" and r.rc.route(1) == "usb"


async def test_refused_without_a_suitable_connected_source_and_never_queued():
    r = Rig()
    r.link.state = "searching"  # only the vehicle USB is connected
    with pytest.raises(RequestError) as e:
        await r.send("LINK_UPLOAD_MODE", 2, understand_link_stops=True)
    assert e.value.code == "no_route" and "nothing was sent or queued" in e.value.message
    r.usb.state = "searching"
    with pytest.raises(RequestError) as e:
        await r.send("CALIB_APPLY")
    assert e.value.code == "no_route"
    assert r.rc.status() == {"inflight": [], "recent": []} and r.events == []
    r.usb.state, r.link.state = "connected", "connected"  # reconnecting sends nothing by itself
    await r.rc.tick()
    assert r.usb.sent == [] and r.link.sent == []


async def test_source_that_refuses_the_frame_is_skipped_then_no_route():
    r = Rig()
    r.usb.accept = False
    st = await r.send("CALIB_APPLY")
    assert st.source == "link"
    r.ack(st.req_id)
    r.clock.advance(1)
    r.link.accept = False
    with pytest.raises(RequestError) as e:
        await r.send("CALIB_APPLY")
    assert e.value.code == "no_route" and r.rc.status()["inflight"] == []


async def test_configured_preference_overrides_default_order():
    r = Rig(pref=["link", "usb"])
    st = await r.send("CALIB_APPLY")
    assert st.source == "link"
    r2 = Rig(pref=["usb"])
    with pytest.raises(RequestError):
        await r2.send("LINK_UPLOAD_MODE", 2, understand_link_stops=True)  # link not in the list: never used


async def test_source_that_cannot_reach_the_target_is_never_used():
    fake = StubSource("f", SourceKind.FAKE, ())
    r = Rig(sources=[fake])
    with pytest.raises(RequestError) as e:
        await r.send("CALIB_APPLY")
    assert e.value.code == "no_route" and fake.sent == []


async def test_change_callback_fires_on_send_ack_and_timeout():
    r = Rig()
    n = []
    r.rc.on_change = lambda: n.append(1)
    st = await r.send("CALIB_APPLY")
    r.ack(st.req_id)
    assert len(n) == 2


async def test_retry_with_no_source_consumes_attempt_without_queueing():
    r = Rig()
    st = await r.send("CALIB_APPLY")
    r.usb.state = r.link.state = "searching"
    r.clock.advance(1)
    await r.rc.tick()
    assert st.attempts == 2 and len(r.usb.sent) == 1
    assert r.kinds()[-1] == GsEventKind.request_retry and "no connected source" in r.events[-1].detail


def test_status_and_whitelist_json():
    r = Rig()
    wl = r.rc.whitelist()
    assert [(e["action"], e["target"], e["confirm"]) for e in wl][-2:] == [
        ("LINK_UPLOAD_MODE", 2, "danger"),
        ("LINK_UPLOAD_MODE", 3, "danger"),
    ]
    assert wl[0]["route"] == "usb" and wl[2]["route"] == "link" and wl[2]["target_name"] == "TX module"


# ------------------------------------------------------------------ config and enum mirrors


def _cfg(actions, **kw):
    return parse_config({"core": {"source_priority": []}, "requests": {"action": actions, **kw}})


def test_config_validation():
    ok = {"action": "CALIB_APPLY", "target": 1, "label": "x", "group": "g"}
    assert _cfg([ok]).requests.actions[0] == RequestEntry("CALIB_APPLY", 1, "x", "g", "hold")
    for bad in (
        {**ok, "action": "NOPE"},
        {**ok, "action": "NONE"},
        {**ok, "target": 4},
        {**ok, "target": True},
        {**ok, "confirm": "yes"},
        {**ok, "extra": 1},
        {**ok, "label": ""},
    ):
        with pytest.raises(ConfigError):
            _cfg([bad])
    with pytest.raises(ConfigError):
        _cfg([ok, ok])  # duplicate (action, target)
    with pytest.raises(ConfigError):
        parse_config({"core": {"source_priority": []}, "requests": {"source_preference": ["nope"]}})
    with pytest.raises(ConfigError):
        parse_config({"core": {"source_priority": []}, "requests": {"bogus": 1}})
    assert parse_config({"core": {"source_priority": []}}).requests.actions == []


def test_enums_mirror_the_generated_schema():
    import sys

    from exvectr_gs.wire.exvectrnet_1 import _deps  # noqa: F401

    xp = sys.modules["exvectr_packets"]
    assert {m.name: m.value for m in RequestAction} == {m.name: m.value for m in xp.RequestAction}
    assert {m.name: m.value for m in RequestStatus} == {m.name: m.value for m in xp.RequestStatus}
