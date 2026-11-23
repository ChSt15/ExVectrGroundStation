"""CommandEngine shell (rc/engine.py): links, identical frames, session, recording, warnings, virtual panel, with a fake
clock and stub sources. The pure logic is tested in test_command_core.py."""

import asyncio
from pathlib import Path

import pytest

from exvectr_gs.core import Bus
from exvectr_gs.core.clock import FakeClock
from exvectr_gs.packets.defs.host import GsEventKind
from exvectr_gs.packets.defs.link import CmdState, RcOrigin, RcState
from exvectr_gs.packets.defs.vehicle import VehicleStatus
from exvectr_gs.rc import CommandEngine, RcController, load_radio_config, new_session
from exvectr_gs.sources.base import SourceCaps, SourceKind, SourceStatus

POCKET_CFG = Path(__file__).parents[2] / "config" / "radio.pocket.toml"
S = 10**9


class Link:
    kind = SourceKind.VEHICLE_USB

    def __init__(self, sid, rc_role="control", state="connected", can_send=True):
        self.id = sid
        self.caps = SourceCaps(rc_role=rc_role, can_send=can_send)
        self.state = state
        self.sent: list[CmdState] = []

    async def send(self, pkt):
        self.sent.append(pkt)
        return True

    def status(self):
        return SourceStatus(self.id, self.kind, True, self.state, None, 0, 0, None)  # type: ignore[arg-type]


class Rig:
    def __init__(self, links=None, virtual=True, session=None):
        self.clock = FakeClock(1000 * S)
        self.bus = Bus()
        self.usb, self.router = Link("usb"), Link("router")
        self.links = [self.usb, self.router] if links is None else links
        self.rc = RcController(load_radio_config(POCKET_CFG), self.bus, clock=self.clock) if virtual else None
        self.engine = CommandEngine(self.links, self.bus, clock=self.clock, virtual=self.rc, session=session)
        self.published = []
        self.bus.subscribe(self.published.append)
        self.engine.start()
        self.bus.publish(VehicleStatus(t=self.clock.now(), src="usb", simulation=False))  # ready at once

    async def run_for(self, seconds, step_ms=5):
        for _ in range(int(seconds * 1000 / step_ms)):
            self.clock.advance(step_ms / 1000)
            await self.engine.step()

    def pocket(self, sa=1000, sd=1000, se=1000, ok=True, node=2, origin=RcOrigin.handset, src="router"):
        ch = [1500] * 16
        ch[4], ch[5], ch[6], ch[7], ch[8] = sa, 2000, 1000, sd, se
        self.bus.publish(RcState(t=self.clock.now(), src=src, origin=origin, ch_us=ch, seq=1, source_ok=ok, node=node))

    async def pocket_stream(self, seconds, **kw):
        for _ in range(int(seconds * 200)):
            self.clock.advance(0.005)
            self.pocket(**kw)
            await self.engine.step()

    def events(self):
        return [p.detail for p in self.published if p.TYPE == "GsEvent" and p.kind == GsEventKind.link_warning]


def run(coro):
    return asyncio.run(coro)


def test_session_is_random_and_never_zero(monkeypatch):
    assert Rig().engine.session != 0
    assert len({new_session() for _ in range(50)}) > 40
    monkeypatch.setattr("secrets.randbelow", lambda n: 0)
    assert new_session() == 1
    assert Rig(session=0x1234ABCD).engine.session == 0x1234ABCD


def test_identical_frame_same_sequence_on_every_command_link_at_25_hz():
    async def go():
        r = Rig()
        await r.run_for(2.0)
        return r

    r = run(go())
    assert 48 <= len(r.usb.sent) <= 52
    assert [(p.seq, p.session, p.go, p.motors) for p in r.usb.sent] == [
        (p.seq, p.session, p.go, p.motors) for p in r.router.sent
    ]
    assert all(p.session == r.engine.session and p.src == "gs" for p in r.usb.sent)
    seqs = [p.seq for p in r.usb.sent]
    assert seqs == [(seqs[0] + i) & 0xFF for i in range(len(seqs))]


def test_only_connected_control_links_receive_it():
    async def go():
        mirror, none = Link("m", rc_role="mirror"), Link("n", rc_role="none")
        down = Link("d", state="searching")
        r = Rig(links=[mirror, none, down, Link("ok")])
        await r.run_for(0.5)
        return r, mirror, none, down

    r, mirror, none, down = run(go())
    assert not mirror.sent and not none.sent and not down.sent and r.links[3].sent
    assert r.engine.status()["links"] == ["ok"]


def test_nothing_is_sent_before_the_vehicle_mode_is_known_or_2_s():
    async def go():
        r = Rig()
        r.engine.core._ready = False  # as if no VehicleStatus had arrived
        r.engine.core.begin(r.clock.now())
        await r.run_for(1.9)
        n1 = len(r.usb.sent)
        await r.run_for(0.2)
        return n1, len(r.usb.sent), r

    n1, n2, r = run(go())
    assert n1 == 0 and n2 > 0 and r.usb.sent[0].vehicle_mode == 0


def test_the_sent_state_is_recorded_on_change_and_once_per_second():
    async def go():
        r = Rig()
        await r.run_for(3.5)
        return r

    r = run(go())
    rec = [p for p in r.published if p.TYPE == "CmdState"]
    assert 3 <= len(rec) <= 5  # first frame, then about once per second
    assert all(p.session == r.engine.session for p in rec)


def test_pocket_switches_drive_the_output_and_the_unsafe_ones_need_a_cycle():
    async def go():
        r = Rig()
        await r.pocket_stream(0.1, sa=2000)  # SA is up when the Pocket shows: ignored
        a = r.usb.sent[-1].go
        await r.pocket_stream(0.1, sa=1000)
        await r.pocket_stream(0.1, sa=2000)  # cycled
        return a, r.usb.sent[-1].go, r

    a, b, r = run(go())
    assert a == 0 and b == 1
    assert r.usb.sent[-1].pocket_live is True and r.usb.sent[-1].input_virtual is False


def test_only_the_routers_handset_copy_counts():
    async def go():
        r = Rig()
        await r.pocket_stream(0.1, node=1)  # not the Router
        await r.pocket_stream(0.1, origin=RcOrigin.gs, src="gs")  # our own
        await r.pocket_stream(0.1, ok=False)  # SOURCE_OK clear
        return r

    r = run(go())
    assert r.engine.core.pocket_state == "absent"
    assert [p.pocket_live for p in r.usb.sent if p.pocket_live] == []


def test_pocket_loss_is_a_warning_event_only_and_the_stream_continues_with_the_last_state():
    async def go():
        r = Rig()
        await r.pocket_stream(0.1, sa=1000)
        await r.pocket_stream(0.1, sa=2000, sd=1000)
        n = len(r.usb.sent)
        await r.run_for(1.0)  # the Pocket stops
        return r, n

    r, n = run(go())
    assert r.usb.sent[-1].go == 1 and r.usb.sent[-1].failsafe == 0 and r.usb.sent[-1].motors == 0
    assert len(r.usb.sent) - n >= 22  # still 25 Hz
    warn = [e for e in r.events() if "Pocket stream lost" in e]
    assert len(warn) == 1 and "WARNING" in warn[0]
    assert r.engine.status()["pocket"] == "lost"


def test_pocket_returning_with_sa_released_aborts_at_once():
    async def go():
        r = Rig()
        await r.pocket_stream(0.1, sa=1000)
        await r.pocket_stream(0.1, sa=2000)
        assert r.usb.sent[-1].go == 1
        await r.run_for(0.6)
        await r.pocket_stream(0.05, sa=1000)
        return r

    r = run(go())
    assert r.usb.sent[-1].go == 0
    assert any("Pocket stream back" in e for e in r.events())


def test_virtual_panel_feeds_the_engine_when_the_pocket_is_absent_and_sets_the_flag():
    async def go():
        r = Rig()
        assert r.rc is not None
        lease = r.rc.acquire("gui", "connection", None, "127.0.0.1")
        await r.run_for(0.1)
        assert r.engine.status()["input"] == "virtual"
        r.rc.set(lease.id, {"SA": 1})
        await r.run_for(0.1)
        return r

    r = run(go())
    assert r.usb.sent[-1].go == 1 and r.usb.sent[-1].input_virtual is True and r.usb.sent[-1].pocket_live is False


def test_the_virtual_panel_is_preset_to_the_output_when_it_becomes_the_input():
    async def go():
        r = Rig()
        r.engine.core.out = r.engine.core.out.__class__(go=0, profile=2, vehicle_mode=1, motors=0)
        lease = r.rc.acquire("gui", "connection", None, "127.0.0.1")
        await r.run_for(0.05)
        return r, lease

    r, lease = run(go())
    c = r.rc.status().controls
    assert c["SB"] == 2 and c["SC"] == 1  # the panel shows what is being sent
    assert r.usb.sent[-1].profile == 2 and r.usb.sent[-1].vehicle_mode == 1


def test_a_lease_ending_or_a_client_leaving_never_stops_the_stream():
    async def go():
        r = Rig()
        lease = r.rc.acquire("gui", "connection", None, "127.0.0.1")
        await r.run_for(0.2)
        n = len(r.usb.sent)
        r.rc.release(lease.id)
        await r.run_for(1.0)
        return len(r.usb.sent) - n, r

    n, r = run(go())
    assert n >= 22 and r.engine.status()["input"] == "none"


def test_failsafe_level_goes_into_the_next_frame():
    async def go():
        r = Rig()
        await r.run_for(0.1)
        r.engine.set_failsafe(True)
        await r.run_for(0.02)
        return r

    r = run(go())
    p = r.usb.sent[-1]
    assert (p.failsafe, p.motors, p.go) == (1, 0, 0)
    assert r.engine.status()["failsafe_latched"] is True


def test_abort_button_goes_out_at_once_notifies_and_failsafe_notifies_both_ways():
    notes = []

    async def go():
        r = Rig()
        r.engine.notify = lambda lvl, text, key: notes.append(key)
        r.pocket(sa=2000)
        await r.run_for(0.1)
        r.engine.abort()
        await r.run_for(0.02)
        sent = [p.go for p in r.usb.sent]
        r.pocket(sa=2000)  # SA still up
        await r.run_for(0.1)
        r.engine.set_failsafe(True)
        r.engine.set_failsafe(False)
        return r, sent

    r, sent = run(go())
    assert sent[-1] == 0 and r.usb.sent[-1].go == 0
    assert r.engine.status()["abort_count"] == 1
    assert notes == ["gs_abort", "gs_failsafe", "gs_failsafe_clear"]


def test_vehicle_session_comparison_and_ctrl_flags_are_display_only():
    async def go():
        r = Rig(session=0xABCD1234)
        r.bus.publish(
            VehicleStatus(t=r.clock.now(), src="usb", simulation=False, cmd_session=0x1234, ctrl_flags=1 << 7)
        )
        await r.run_for(0.05)
        a = r.engine.status()
        r.bus.publish(
            VehicleStatus(t=r.clock.now(), src="usb", simulation=False, cmd_session=0x9999, ctrl_flags=1 << 11)
        )
        await r.run_for(0.05)
        return a, r.engine.status(), r

    a, b, r = run(go())
    assert a["master"] == "ok" and a["usb_fresh"] and not a["multi_master"]
    assert b["master"] == "not_master" and b["multi_master"]
    assert r.usb.sent  # NOT MASTER is shown, never acted on here


def test_a_source_that_raises_does_not_stop_the_others():
    class Bad(Link):
        async def send(self, pkt):
            raise OSError("boom")

    async def go():
        good = Link("good")
        r = Rig(links=[Bad("bad"), good])
        await r.run_for(0.3)
        return good

    assert run(go()).sent


def test_no_link_connected_counts_unsent_and_keeps_running():
    async def go():
        r = Rig(links=[Link("d", state="searching")])
        await r.run_for(0.5)
        return r

    r = run(go())
    assert r.engine.sent_count == 0 and r.engine.unsent_count > 5 and r.engine.status()["sending"] is False


@pytest.mark.parametrize("n", [0])
def test_engine_without_a_virtual_panel_still_runs(n):
    async def go():
        r = Rig(virtual=False)
        await r.run_for(0.3)
        return r

    assert run(go()).usb.sent


def test_pocket_loss_and_return_are_notified_to_clients_with_a_toast_key():
    async def go():
        r = Rig()
        got = []
        r.engine.notify = lambda level, text, key: got.append((level, key, text))
        await r.pocket_stream(0.1, sa=1000)
        await r.run_for(0.6)
        await r.pocket_stream(0.05, sa=1000)
        return got

    got = run(go())
    assert [(lv, k) for lv, k, _ in got] == [("warn", "pocket_lost"), ("info", "pocket_back")]
    assert got[0][2].startswith("WARNING: Pocket stream lost")


def test_a_failing_notify_never_touches_the_engine():
    async def go():
        r = Rig()

        def boom(*a):
            raise RuntimeError("client")

        r.engine.notify = boom
        await r.pocket_stream(0.1, sa=1000)
        await r.run_for(0.6)
        return r

    assert run(go()).usb.sent
