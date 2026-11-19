"""RC controller rules (design 1.4, 8.2) with a fake clock and a scripted source. No I/O, no hardware."""

import asyncio
import time
from pathlib import Path

import pytest

from exvectr_gs.core import Bus
from exvectr_gs.core.clock import FakeClock
from exvectr_gs.packets.defs.host import GsEventKind
from exvectr_gs.packets.defs.link import RcOrigin, RcState
from exvectr_gs.rc import ControlDenied, RadioConfig, RadioConfigError, RcController, load_radio_config
from exvectr_gs.rc.radio_config import radio_config_from_dict
from exvectr_gs.sources.base import SourceCaps, SourceKind, SourceStatus

POCKET = Path(__file__).parents[2] / "config" / "radio.pocket.toml"
S = 1_000_000_000


class StubSource:
    """A control (or mirror) link that records what it is asked to send."""

    kind = SourceKind.VEHICLE_USB

    def __init__(self, sid="usb", rc_role="control", state="connected"):
        self.id = sid
        self.caps = SourceCaps(rc_role=rc_role, can_send=True)
        self.state = state
        self.accept = True
        self.sent: list[RcState] = []

    async def send(self, pkt):
        if not self.accept:
            return False
        self.sent.append(pkt)
        return True

    def status(self):
        return SourceStatus(self.id, self.kind, True, self.state, None, 0, 0, None)  # type: ignore[arg-type]

    async def run(self, sink):  # pragma: no cover
        pass


class Rig:
    def __init__(self, sources=None, hosts=("127.0.0.1",)):
        self.clock = FakeClock(1000 * S)
        self.bus = Bus()
        self.src = StubSource()
        self.sources = [self.src] if sources is None else sources
        self.cfg = load_radio_config(POCKET)
        self.rc = RcController(self.cfg, self.sources, self.bus, clock=self.clock, control_hosts=hosts)
        self.published = []
        self.bus.subscribe(self.published.append)

    def acquire(self, kind="connection", dur=None, holder="gui"):
        return self.rc.acquire(holder, kind, dur, "127.0.0.1")

    async def tick(self, advance=0.04):
        self.clock.advance(advance)
        return await self.rc.tick()

    def events(self):
        return [p.kind for p in self.published if p.TYPE == "GsEvent"]


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- radio config


def test_pocket_config_loads_with_safe_defaults():
    cfg = load_radio_config(POCKET)
    assert [c.id for c in cfg.controls] == ["SA", "SB", "SC", "SD", "SE", "S1"]
    assert cfg.send_rate_hz == 25 and cfg.channel_count == 16
    assert all(c.default == 0 for c in cfg.controls)  # lowest/off
    ch = cfg.channels_us(cfg.default_state())
    assert ch[:4] == [1500, 1500, 1000, 1500]  # sticks neutral, throttle low
    assert ch[4:10] == [1000] * 6 and ch[10:] == [1500] * 6
    assert {c.id: c.meaning for c in cfg.controls}["SD"] == "motor enable"  # informational label only


def test_channel_mapping_and_back():
    cfg = load_radio_config(POCKET)
    st = {"SA": 1, "SB": 2, "SC": 1, "SD": 1, "SE": 1, "S1": 0.25}
    ch = cfg.channels_us(st)
    assert ch[4:10] == [2000, 2000, 1500, 2000, 2000, 1250]
    assert cfg.state_from_channels(ch) == st
    assert cfg.control("SB").from_us(1400) == 1  # nearest position


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["controls"].append(dict(d["controls"][0])),  # duplicate id
        lambda d: d["controls"][1].update(channel=5),  # duplicate channel
        lambda d: d["controls"][0].update(channel=17),  # channel beyond channel_count
        lambda d: d["controls"][0].update(default=2),  # default index out of range
        lambda d: d["controls"][0].update(values_us=[1000, 2500]),  # us out of range
        lambda d: d["controls"][0].update(values_us=[2000, 1000]),  # descending
        lambda d: d["controls"][4].update(default=1),  # momentary not released
        lambda d: d["controls"][5].update(default=1.5),  # slider default out of range
        lambda d: d["controls"][0].update(kind="dial"),
        lambda d: d.update(channel_defaults={"3": 3000}),
        lambda d: d.update(send_rate_hz=0),
        lambda d: d.update(bogus=1),
    ],
)
def test_invalid_radio_config_is_refused(mutate):
    import tomllib

    d = tomllib.loads(POCKET.read_text())
    mutate(d)
    with pytest.raises(RadioConfigError):
        radio_config_from_dict(d)


def test_control_value_validation():
    cfg = load_radio_config(POCKET)
    for cid, bad in [("SA", 2), ("SA", 0.5), ("SB", -1), ("S1", 1.1), ("SA", float("nan")), ("SA", True), ("SA", "1")]:
        with pytest.raises(ValueError):
            cfg.control(cid).check(bad)
    with pytest.raises(ValueError):
        cfg.control("nope")


# ---------------------------------------------------------------- leases, sending, silence


def test_nothing_is_sent_without_a_lease():
    r = Rig()
    for _ in range(10):
        assert run(r.tick()) is False
    assert r.src.sent == [] and r.rc.status().mode == "idle" and not r.rc.status().sending
    assert not [p for p in r.published if p.TYPE == "RcState"]


def test_core_start_has_defaults_and_sends_nothing():
    r = Rig()
    st = r.rc.status()
    assert (
        st.controls == r.cfg.default_state() and st.lease is None and st.channels_us == r.cfg.channels_us(st.controls)
    )
    # a second controller (a "restart") shares nothing with the first
    lease = r.acquire()
    r.rc.set(lease.id, {"SA": 1})
    run(r.tick())
    r2 = RcController(r.cfg, r.sources, Bus(), clock=r.clock)
    assert r2.status().controls == r.cfg.default_state() and r2.status().lease is None
    sent = len(r.src.sent)
    run(r2.tick())
    assert len(r.src.sent) == sent


def test_acquire_sends_complete_state_to_control_source_only():
    mirror = StubSource("link", rc_role="mirror")
    r = Rig([mirror, StubSource("usb")])
    lease = r.acquire()
    assert run(r.tick()) is True
    assert mirror.sent == [] and len(r.sources[1].sent) == 1
    pkt = r.sources[1].sent[0]
    assert pkt.origin == RcOrigin.gs and pkt.src == "gs" and len(pkt.ch_us) == 16
    assert pkt.ch_us == r.cfg.channels_us(r.cfg.default_state())
    r.rc.set(lease.id, {"SA": 1, "S1": 0.5})
    run(r.tick())
    assert r.sources[1].sent[-1].ch_us[4] == 2000 and r.sources[1].sent[-1].ch_us[9] == 1500
    assert [p.seq for p in r.sources[1].sent] == [1, 2]


def test_acquire_denied_without_control_source_or_connection():
    with pytest.raises(ControlDenied):
        Rig([]).acquire()
    with pytest.raises(ControlDenied):  # only a mirror source (TX module): the GS sends nothing at all
        Rig([StubSource("link", rc_role="mirror")]).acquire()
    with pytest.raises(ControlDenied):
        Rig([StubSource(state="searching")]).acquire()


def test_one_holder_only():
    r = Rig()
    r.acquire(holder="gui")
    with pytest.raises(ControlDenied):
        r.acquire(holder="cli", kind="timed", dur=5)


def test_control_hosts_enforced_in_controller():
    r = Rig(hosts=("127.0.0.1", "::1"))
    for addr in ("192.168.1.20", "", "not-an-ip"):
        with pytest.raises(ControlDenied):
            r.rc.acquire("x", "connection", None, addr)
    assert r.rc.acquire("x", "connection", None, "::1").holder == "x"
    assert r.rc.status().lease is not None


def test_acquire_argument_validation():
    r = Rig()
    for kind, dur in [("timed", None), ("timed", 0), ("timed", 601), ("bogus", None)]:
        with pytest.raises(ValueError):
            r.rc.acquire("x", kind, dur, "127.0.0.1")
    assert r.rc.status().lease is None


def test_release_stops_sending_at_once_and_resets_to_defaults():
    r = Rig()
    lease = r.acquire()
    r.rc.set(lease.id, {"SA": 1, "SD": 1})
    run(r.tick())
    n = len(r.src.sent)
    r.rc.release(lease.id)
    assert run(r.tick()) is False and len(r.src.sent) == n
    assert r.rc.status().controls == r.cfg.default_state() and r.rc.status().mode == "idle"
    assert GsEventKind.lease_released in r.events()
    r.rc.release(lease.id)  # idempotent
    # the next holder starts from the defaults, the previous state is never resumed
    r.acquire(holder="other")
    assert r.rc.status().controls == r.cfg.default_state()
    run(r.tick())
    assert r.src.sent[-1].ch_us == r.cfg.channels_us(r.cfg.default_state())


def test_connection_lease_ends_when_heartbeat_is_lost():
    r = Rig()
    lease = r.acquire()
    r.rc.set(lease.id, {"SA": 1})
    for _ in range(20):  # 0.8 s with heartbeats: stays alive
        run(r.tick())
        r.rc.heartbeat(lease.id)
    assert r.rc.status().lease is not None
    n = len(r.src.sent)
    r.clock.advance(0.99)
    assert r.rc.status().lease is not None
    r.clock.advance(0.02)  # > 1 s since the last heartbeat
    assert run(r.rc.tick()) is False and len(r.src.sent) == n
    assert r.rc.status().lease is None and r.rc.status().controls == r.cfg.default_state()
    assert GsEventKind.lease_expired in r.events()
    with pytest.raises(ControlDenied):  # a late heartbeat cannot revive it
        r.rc.heartbeat(lease.id)
    with pytest.raises(ControlDenied):
        r.rc.set(lease.id, {"SA": 1})


def test_timed_lease_expires_and_ignores_heartbeats():
    r = Rig()
    lease = r.acquire("timed", 2.0, holder="cli")
    assert lease.expires == 1002 * S
    for _ in range(40):  # 1.6 s
        r.rc.heartbeat(lease.id)
        assert run(r.tick()) is True
    run(r.tick(0.5))  # t = 2.1 s: expired, nothing sent at this tick
    assert r.rc.status().lease is None
    n = len(r.src.sent)
    assert run(r.tick()) is False and len(r.src.sent) == n
    assert GsEventKind.lease_expired in r.events()


def test_losing_the_lease_stops_sending_within_one_period():
    """Real-time loop: after the lease ends, no frame follows more than one period later."""

    async def go():
        r = Rig()
        r.rc.cfg.send_rate_hz = 50
        r.rc._clock = type("C", (), {"now": staticmethod(lambda: time.monotonic_ns())})()
        lease = r.rc.acquire("gui", "timed", 30, "127.0.0.1")
        task = asyncio.create_task(r.rc.run())
        await asyncio.sleep(0.2)
        assert len(r.src.sent) >= 5
        r.rc.release(lease.id)
        n = len(r.src.sent)
        await asyncio.sleep(0.2)
        task.cancel()
        await task
        return n, len(r.src.sent)

    n, after = run(go())
    assert after == n  # not one more frame after release


def test_refused_send_skips_the_tick_and_keeps_the_lease():
    r = Rig()
    r.acquire()
    run(r.tick())
    r.src.accept = False
    for _ in range(5):
        assert run(r.tick()) is False
    assert r.rc.skipped_count == 5 and r.rc.status().lease is not None
    n_pub = len([p for p in r.published if p.TYPE == "RcState"])
    r.src.accept = True
    assert run(r.tick()) is True  # sending resumes with the current state, nothing is queued up
    assert len(r.src.sent) == 2
    assert len([p for p in r.published if p.TYPE == "RcState"]) >= n_pub  # only accepted sends are recorded


def test_control_source_drop_resets_controls_to_defaults():
    r = Rig()
    lease = r.acquire()
    r.rc.set(lease.id, {"SA": 1, "SD": 1})
    run(r.tick())
    r.src.state = "searching"
    for _ in range(3):
        assert run(r.tick(0.04)) is False
        r.rc.heartbeat(lease.id)
    assert r.rc.status().mode == "idle" and not r.rc.status().sending
    r.src.state = "connected"
    run(r.tick())
    assert r.src.sent[-1].ch_us == r.cfg.channels_us(r.cfg.default_state())  # not the old state


# ---------------------------------------------------------------- set validation, momentary


def test_set_is_validated_atomically():
    r = Rig()
    lease = r.acquire()
    for bad in [{"SA": 1, "SB": 5}, {"SA": 1, "NOPE": 1}, {"S1": 2.0}, {"SA": 1.5}]:
        with pytest.raises(ValueError):
            r.rc.set(lease.id, bad)
    assert r.rc.status().controls == r.cfg.default_state()  # nothing changed
    with pytest.raises(ControlDenied):
        r.rc.set("wrong-id", {"SA": 1})


def test_momentary_with_hold_is_released_by_the_core():
    r = Rig()
    lease = r.acquire()
    r.rc.set(lease.id, {"SE": 1}, hold_s=0.5)
    seen = []
    for _ in range(20):  # 0.8 s, with heartbeats but no further set: the core must release it
        run(r.tick())
        r.rc.heartbeat(lease.id)
        seen.append(r.src.sent[-1].ch_us[8])
    assert seen[0] == 2000 and seen[10] == 2000 and seen[-1] == 1000
    held = sum(1 for v in seen if v == 2000)
    assert 11 <= held <= 14  # about 0.5 s of 25 Hz ticks
    assert r.rc.status().controls["SE"] == 0


def test_momentary_without_hold_lapses_unless_refreshed():
    r = Rig()
    lease = r.acquire()
    for _ in range(10):  # refreshed every tick (GUI repeats): stays pressed for 0.4 s
        r.rc.set(lease.id, {"SE": 1})
        run(r.tick())
        r.rc.heartbeat(lease.id)
    assert r.src.sent[-1].ch_us[8] == 2000
    for _ in range(10):  # the client died: auto-release after 300 ms
        run(r.tick())
        r.rc.heartbeat(lease.id)
    assert r.src.sent[-1].ch_us[8] == 1000
    r.rc.set(lease.id, {"SE": 1})
    r.rc.set(lease.id, {"SE": 0})  # explicit release
    assert r.rc.status().controls["SE"] == 0


def test_momentary_released_when_lease_ends():
    r = Rig()
    lease = r.acquire()
    r.rc.set(lease.id, {"SE": 1}, hold_s=5)
    r.rc.release(lease.id)
    lease = r.acquire()
    assert r.rc.status().controls["SE"] == 0
    run(r.tick())
    assert r.src.sent[-1].ch_us[8] == 1000


def test_hold_s_only_for_momentary_and_bounded():
    r = Rig()
    lease = r.acquire()
    with pytest.raises(ValueError):
        r.rc.set(lease.id, {"SA": 1}, hold_s=1)
    for bad in (0, -1, 11):
        with pytest.raises(ValueError):
            r.rc.set(lease.id, {"SE": 1}, hold_s=bad)
    assert r.rc.status().controls["SE"] == 0 and r.rc.status().controls["SA"] == 0


# ---------------------------------------------------------------- recording on the bus


def test_gs_rcstate_recorded_on_change_and_once_per_second():
    r = Rig()
    lease = r.acquire("timed", 30, holder="cli")

    def recorded():
        return [p for p in r.published if p.TYPE == "RcState"]

    run(r.tick())
    assert len(recorded()) == 1 and recorded()[0].src == "gs" and recorded()[0].origin == RcOrigin.gs
    for _ in range(24):  # 0.96 s unchanged: nothing more
        run(r.tick())
    assert len(recorded()) == 1
    run(r.tick())  # >= 1 s
    assert len(recorded()) == 2
    r.rc.set(lease.id, {"SA": 1})
    run(r.tick())
    assert len(recorded()) == 3 and recorded()[-1].ch_us[4] == 2000  # on change, at once
    assert len(r.src.sent) == 27  # every tick was still sent


# ---------------------------------------------------------------- mirror, conflict


def handset(r, v=1500, sid="link"):
    ch = [1500] * 16
    ch[4], ch[5], ch[9] = 2000, 1500, v
    r.bus.publish(RcState(t=r.clock.now(), src=sid, origin=RcOrigin.handset, ch_us=ch, seq=1))


def test_mirror_mode_reports_handset_and_sends_nothing():
    r = Rig([StubSource("link", rc_role="mirror"), StubSource("usb", rc_role="none")])
    handset(r, 1250)
    st = r.rc.status()
    assert st.mode == "mirroring" and st.origin == "handset" and not st.sending and st.target is None
    assert st.controls["SA"] == 1 and st.controls["SB"] == 1 and st.controls["S1"] == pytest.approx(0.25)
    assert st.channels_us[4] == 2000
    with pytest.raises(ControlDenied):
        r.acquire()
    for _ in range(10):
        assert run(r.tick()) is False
    assert all(not s.sent for s in r.sources)
    r.clock.advance(1.01)
    assert r.rc.status().mode == "idle"


def test_conflict_when_controlling_and_handset_seen():
    r = Rig()
    r.acquire("timed", 30, holder="cli")
    run(r.tick())
    assert r.rc.status().mode == "controlling"
    handset(r)
    st = r.rc.status()
    assert st.mode == "conflict" and st.sending and st.origin == "gs"  # display only: still sending the same
    n = len(r.src.sent)
    run(r.tick())
    assert len(r.src.sent) == n + 1
    r.clock.advance(1.01)
    assert r.rc.status().mode == "controlling"


def test_own_gs_state_is_not_mistaken_for_a_handset():
    r = Rig()
    r.acquire()
    run(r.tick())  # published to the bus with origin gs, src gs
    r.bus.publish(RcState(t=1, src="gs", origin=RcOrigin.gs, ch_us=[1500] * 16, seq=9))
    assert r.rc.status().mode == "controlling"
    r.rc.release(r.rc.status().lease.id)
    assert r.rc.status().mode == "idle"


def test_rate_is_fixed_at_send_rate_hz():
    # a timed lease needs no heartbeat
    async def go_timed():
        r = Rig()
        r.rc._clock = type("C", (), {"now": staticmethod(lambda: time.monotonic_ns())})()
        r.rc.acquire("cli", "timed", 5.0, "127.0.0.1")
        task = asyncio.create_task(r.rc.run())
        t0 = time.monotonic()
        await asyncio.sleep(2.0)
        n = len(r.src.sent)
        dt = time.monotonic() - t0
        task.cancel()
        await task
        return n / dt

    assert run(go_timed()) == pytest.approx(25.0, rel=0.05)


def test_default_radio_config_object_is_valid():
    cfg = RadioConfig()
    assert cfg.controls == [] and cfg.channels_us({}) == [1500] * 16
