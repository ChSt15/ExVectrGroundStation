"""RC controller rules (design 1.4, 8.2) with a fake clock and a scripted source. No I/O, no hardware."""

import asyncio
from pathlib import Path

import pytest

from exvectr_gs.core import Bus
from exvectr_gs.core.clock import FakeClock
from exvectr_gs.packets.defs.host import GsEventKind
from exvectr_gs.packets.defs.link import RcOrigin, RcState
from exvectr_gs.rc import ControlDenied, ControlLocked, RadioConfig, RadioConfigError, RcController, load_radio_config
from exvectr_gs.rc.radio_config import radio_config_from_dict

POCKET = Path(__file__).parents[2] / "config" / "radio.pocket.toml"
S = 1_000_000_000


class Rig:
    def __init__(self, hosts=("127.0.0.1",)):
        self.clock = FakeClock(1000 * S)
        self.bus = Bus()
        self.cfg = load_radio_config(POCKET)
        self.rc = RcController(self.cfg, self.bus, clock=self.clock, control_hosts=hosts)
        self.published = []
        self.bus.subscribe(self.published.append)

    def acquire(self, kind="connection", dur=None, holder="gui"):
        return self.rc.acquire(holder, kind, dur, "127.0.0.1")

    def poll(self, advance=0.04):
        """What the engine does every 5 ms: advance, expire, read the virtual switches."""
        self.clock.advance(advance)
        return self.rc.poll()

    def ch(self):
        """The panel's channel vector (the state the engine would read)."""
        return self.cfg.channels_us(self.rc.status().controls)

    def events(self):
        return [p.kind for p in self.published if p.TYPE == "GsEvent"]


def run(coro):
    return asyncio.run(coro)


def beat(r, lease, seconds, step=0.5):
    """Advance the fake clock keeping the lease alive with heartbeats / renewals."""
    left = seconds
    while left > 1e-9:
        d = min(step, left)
        r.clock.advance(d)
        r.rc.heartbeat(lease.id)
        left -= d


# ---------------------------------------------------------------- radio config


def test_pocket_config_loads_with_safe_defaults():
    cfg = load_radio_config(POCKET)
    assert [c.id for c in cfg.controls] == ["SA", "SB", "SC", "SD", "SE", "S1"]
    assert cfg.send_rate_hz == 25 and cfg.channel_count == 16
    assert all(c.default == 0 for c in cfg.controls)  # lowest/off
    ch = cfg.channels_us(cfg.default_state())
    assert ch[:4] == [1500, 1500, 1000, 1500]  # sticks neutral, throttle low
    assert ch[4:10] == [1000] * 6 and ch[10:] == [1500] * 6
    assert {c.id: c.meaning for c in cfg.controls}["SD"].startswith("motors armed")  # informational label only


def test_stick_display_mapping_defaults():
    import tomllib

    assert load_radio_config(POCKET).sticks == {"mode": 2, "roll": 1, "pitch": 2, "throttle": 3, "yaw": 4}
    d = tomllib.loads(POCKET.read_text())
    del d["sticks"]
    assert radio_config_from_dict(d).sticks["throttle"] == 3  # all keys optional
    d["sticks"] = {"mode": 1}
    assert radio_config_from_dict(d).sticks == {"mode": 1, "roll": 1, "pitch": 2, "throttle": 3, "yaw": 4}


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
        lambda d: d["sticks"].update(mode=3),  # stick display mapping
        lambda d: d["sticks"].update(roll=2),  # duplicate stick channel
        lambda d: d["sticks"].update(yaw=5),  # used by a control (SA)
        lambda d: d["sticks"].update(throttle=17),
        lambda d: d["sticks"].update(aux=6),
        lambda d: d.update(sticks=3),
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


# ---------------------------------------------------------------- leases (they guard the virtual panel only)


def test_one_holder_only():
    r = Rig()
    r.acquire(holder="gui")
    with pytest.raises(ControlDenied):
        r.acquire(holder="cli", kind="keepalive", dur=5)


def test_control_hosts_enforced_in_controller():
    r = Rig(hosts=("127.0.0.1", "::1"))
    for addr in ("192.168.1.20", "", "not-an-ip"):
        with pytest.raises(ControlDenied):
            r.rc.acquire("x", "connection", None, addr)
    assert r.rc.acquire("x", "connection", None, "::1").holder == "x"
    assert r.rc.status().lease is not None


def test_acquire_argument_validation():
    r = Rig()
    for kind, dur in [("keepalive", 0), ("keepalive", 9.5), ("keepalive", -1), ("timed", 5), ("bogus", None)]:
        with pytest.raises(ValueError):
            r.rc.acquire("x", kind, dur, "127.0.0.1")
    assert r.rc.status().lease is None


def test_release_ends_the_virtual_input_at_once_and_resets_to_defaults():
    r = Rig()
    lease = r.acquire()
    r.rc.set(lease.id, {"SA": 1, "SB": 2})
    assert r.poll().sa == 1 and r.poll().sb == 2
    r.rc.release(lease.id)
    assert r.poll() is None  # no lease: no virtual input (the engine keeps sending its last state)
    assert r.rc.status().controls == r.cfg.default_state() and r.rc.status().mode == "idle"
    assert GsEventKind.lease_released in r.events()
    r.rc.release(lease.id)  # idempotent
    # the next holder starts from the defaults, the previous state is never resumed
    r.acquire(holder="other")
    assert r.rc.status().controls == r.cfg.default_state()
    r.poll()
    assert r.ch() == r.cfg.channels_us(r.cfg.default_state())


def test_connection_lease_ends_when_heartbeat_is_lost():
    r = Rig()
    lease = r.acquire()
    r.rc.set(lease.id, {"SA": 1})
    for _ in range(20):  # 0.8 s with heartbeats: stays alive
        r.poll()
        r.rc.heartbeat(lease.id)
    assert r.rc.status().lease is not None
    r.clock.advance(0.99)
    assert r.rc.status().lease is not None
    r.clock.advance(0.02)  # > 1 s since the last heartbeat
    assert r.rc.poll() is None
    assert r.rc.status().lease is None and r.rc.status().controls == r.cfg.default_state()
    assert GsEventKind.lease_expired in r.events()
    with pytest.raises(ControlDenied):  # a late heartbeat cannot revive it
        r.rc.heartbeat(lease.id)
    with pytest.raises(ControlDenied):
        r.rc.set(lease.id, {"SA": 1})


def test_keepalive_lease_defaults_to_5_s_and_is_renewed_by_every_operation():
    r = Rig()
    lease = r.acquire("keepalive", None, holder="cli")
    assert lease.idle_s == 5.0 and lease.expires == 1005 * S
    r.clock.advance(4.0)
    r.rc.set(lease.id, {"SA": 1})  # a set renews: deadline = now + 5 s
    assert lease.expires == 1009 * S
    r.clock.advance(4.0)
    r.rc.heartbeat(lease.id)  # so does keepalive/heartbeat
    r.clock.advance(4.0)
    r.rc.unlock(lease.id, "SD")  # and unlock
    r.clock.advance(4.0)
    r.rc.lock(lease.id, "SD")  # and lock
    r.clock.advance(4.9)
    assert r.rc.status().lease is not None and r.rc.status().controls["SA"] == 1  # status itself does not renew
    r.clock.advance(0.2)  # 5.1 s idle
    assert r.rc.status().lease is None and r.rc.status().controls == r.cfg.default_state()


def test_keepalive_lease_expires_input_ends_and_a_late_renewal_cannot_revive_it():
    r = Rig()
    lease = r.acquire("keepalive", 2.0, holder="cli")
    assert lease.expires == 1002 * S
    r.rc.set(lease.id, {"SA": 1})
    for _ in range(40):  # 1.6 s, no renewal
        assert r.poll() is not None
    assert r.poll(0.5) is None  # t = 2.1 s: expired
    assert r.rc.status().lease is None
    assert GsEventKind.lease_expired in r.events()
    for op in (
        lambda: r.rc.heartbeat(lease.id),
        lambda: r.rc.set(lease.id, {"SA": 0}),
        lambda: r.rc.unlock(lease.id, "SD"),
        lambda: r.rc.lock(lease.id, "SD"),
    ):
        with pytest.raises(ControlDenied):  # the renewal is checked after the expiry
            op()
    assert r.rc.status().lease is None and r.rc.status().controls == r.cfg.default_state()


def test_keepalive_idle_is_bounded_to_single_digit_seconds_and_the_connection_lease_ignores_idle():
    r = Rig()
    for bad in (0, 9.01, 600):
        with pytest.raises(ValueError):
            r.rc.acquire("x", "keepalive", bad, "127.0.0.1")
    assert r.rc.acquire("x", "keepalive", 9, "127.0.0.1").idle_s == 9
    r.rc.release(r.rc.status().lease.id)
    conn = r.rc.acquire("x", "connection", 3, "127.0.0.1")
    assert conn.idle_s is None and conn.expires is None
    r.clock.advance(0.8)
    r.rc.set(conn.id, {"SA": 1})  # a set does NOT renew a connection lease: only the heartbeat does
    r.clock.advance(0.3)
    assert r.rc.status().lease is None


# ---------------------------------------------------------------- locked controls (user decision 2026-11-19)


def test_pocket_config_locks_sd_only():
    cfg = load_radio_config(POCKET)
    assert [c.id for c in cfg.controls if c.lock] == ["SD"] and cfg.control("SD").lock_timeout_s == 60


def test_locked_control_rejects_non_default_and_applies_nothing():
    r = Rig()
    lease = r.acquire()
    st = r.rc.status()
    assert st.locks["SD"]["locked"] is True and st.locks["SD"]["remaining_s"] is None
    assert set(st.locks) == {"SD"}
    with pytest.raises(ControlLocked):
        r.rc.set(lease.id, {"SA": 1, "SD": 1})  # one bad value: the whole request is rejected
    assert r.rc.status().controls == r.cfg.default_state()
    r.rc.set(lease.id, {"SD": 0, "SA": 1})  # the default is always allowed
    assert r.rc.status().controls["SA"] == 1
    r.poll()
    assert r.ch()[7] == 1000  # SD stays off on the wire


def test_unlock_needs_a_lease_and_a_lockable_control():
    r = Rig()
    with pytest.raises(ControlDenied):
        r.rc.unlock("nope", "SD")
    lease = r.acquire()
    with pytest.raises(ValueError):
        r.rc.unlock(lease.id, "SA")  # no lock configured
    with pytest.raises(ValueError):
        r.rc.unlock(lease.id, "XX")
    with pytest.raises(ValueError):
        r.rc.lock(lease.id, "SA")


def test_unlocked_control_can_be_set_and_relocks_after_the_timeout_to_off():
    r = Rig()
    lease = r.acquire()
    st = r.rc.unlock(lease.id, "SD")
    assert st.locks["SD"] == {"locked": False, "remaining_s": 60.0, "timeout_s": 60.0}
    r.rc.set(lease.id, {"SD": 1})
    r.poll()
    assert r.ch()[7] == 2000
    beat(r, lease, 59.0)
    assert r.rc.status().controls["SD"] == 1
    assert r.rc.status().locks["SD"]["remaining_s"] == pytest.approx(0.96, abs=0.01)
    beat(r, lease, 1.01)
    assert r.rc.status().controls["SD"] == 0 and r.rc.status().locks["SD"]["locked"] is True  # reset to off
    r.poll()
    assert r.ch()[7] == 1000  # the expiry is applied on the next tick, no operation needed
    with pytest.raises(ControlLocked):
        r.rc.set(lease.id, {"SD": 1})


def test_unlock_expiry_is_applied_by_the_tick_alone():
    r = Rig()
    lease = r.acquire("keepalive", 9, holder="cli")
    r.rc.unlock(lease.id, "SD")
    r.rc.set(lease.id, {"SD": 1})
    beat(r, lease, 56.0)  # lease kept alive by renewals, unlock not renewed
    r.clock.advance(5.0)  # t = 61 s since the unlock, 5 s since the last renewal: lease alive, unlock lapsed
    assert r.rc.poll() is not None  # the poll itself relocks, no operation in between
    assert r.ch()[7] == 1000 and r.rc._unlock_until == {} and r.rc.status().lease is not None


def test_explicit_lock_resets_to_default_and_is_idempotent():
    r = Rig()
    lease = r.acquire()
    r.rc.unlock(lease.id, "SD")
    r.rc.set(lease.id, {"SD": 1})
    st = r.rc.lock(lease.id, "SD")
    assert st.controls["SD"] == 0 and st.locks["SD"]["locked"] is True
    assert r.rc.lock(lease.id, "SD").controls["SD"] == 0
    with pytest.raises(ControlLocked):
        r.rc.set(lease.id, {"SD": 1})


def _unlock_sd_on(r, lease):
    r.rc.unlock(lease.id, "SD")
    r.rc.set(lease.id, {"SD": 1, "SA": 1})
    assert r.rc.status().controls["SD"] == 1


def test_every_end_of_control_returns_defaults_and_relocks():
    # release
    r = Rig()
    lease = r.acquire()
    _unlock_sd_on(r, lease)
    r.rc.release(lease.id)
    assert r.rc._unlock_until == {} and r.rc.status().controls == r.cfg.default_state()
    # heartbeat loss
    r = Rig()
    lease = r.acquire()
    _unlock_sd_on(r, lease)
    r.clock.advance(1.01)
    assert r.rc.status().lease is None and r.rc._unlock_until == {} and r.rc.status().controls["SD"] == 0
    # keepalive expiry
    r = Rig()
    lease = r.acquire("keepalive", 1.0, holder="cli")
    _unlock_sd_on(r, lease)
    r.clock.advance(1.01)
    assert r.rc.status().lease is None and r.rc._unlock_until == {} and r.rc.status().controls["SD"] == 0
    # acquire after an unlock that was never closed (cannot normally happen: defence in depth)
    r = Rig()
    r.rc._unlock_until["SD"] = 10**18
    r.rc._state["SD"] = 1
    lease = r.acquire()
    assert r.rc.status().controls["SD"] == 0 and r.rc.status().locks["SD"]["locked"] is True


def test_fresh_controller_starts_locked_at_defaults():
    r = Rig()
    st = r.rc.status()
    assert st.controls == r.cfg.default_state() and st.locks["SD"]["locked"] is True


def test_mirror_status_reports_locks_as_locked():
    r = Rig()
    handset(r)
    st = r.rc.status()
    assert st.mode == "pocket" and st.locks["SD"]["locked"] is True


def test_radio_config_lock_validation():
    base = {"controls": [{"id": "A", "kind": "toggle2", "channel": 1, "values_us": [1000, 2000], "default": 0}]}

    def with_(**kw):
        d = {"controls": [{**base["controls"][0], **kw}]}
        return radio_config_from_dict(d)

    c = with_(lock=True).control("A")
    assert c.lock is True and c.lock_timeout_s == 60.0
    assert with_(lock=True, lock_timeout_s=5).control("A").lock_timeout_s == 5
    for bad in ({"lock": "yes"}, {"lock": True, "lock_timeout_s": 0}, {"lock": True, "lock_timeout_s": 4000},
                {"lock": True, "lock_timeout_s": "x"}):  # fmt: skip
        with pytest.raises(RadioConfigError):
            with_(**bad)


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
        r.poll()
        r.rc.heartbeat(lease.id)
        seen.append(r.ch()[8])
    assert seen[0] == 2000 and seen[10] == 2000 and seen[-1] == 1000
    held = sum(1 for v in seen if v == 2000)
    assert 11 <= held <= 14  # about 0.5 s of 25 Hz ticks
    assert r.rc.status().controls["SE"] == 0


def test_momentary_without_hold_lapses_unless_refreshed():
    r = Rig()
    lease = r.acquire()
    for _ in range(10):  # refreshed every tick (GUI repeats): stays pressed for 0.4 s
        r.rc.set(lease.id, {"SE": 1})
        r.poll()
        r.rc.heartbeat(lease.id)
    assert r.ch()[8] == 2000
    for _ in range(10):  # the client died: auto-release after 300 ms
        r.poll()
        r.rc.heartbeat(lease.id)
    assert r.ch()[8] == 1000
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
    r.poll()
    assert r.ch()[8] == 1000


def test_hold_s_only_for_momentary_and_bounded():
    r = Rig()
    lease = r.acquire()
    with pytest.raises(ValueError):
        r.rc.set(lease.id, {"SA": 1}, hold_s=1)
    for bad in (0, -1, 11):
        with pytest.raises(ValueError):
            r.rc.set(lease.id, {"SE": 1}, hold_s=bad)
    assert r.rc.status().controls["SE"] == 0 and r.rc.status().controls["SA"] == 0


# ---------------------------------------------------------------- Pocket display and the engine's view


def handset(r, v=1500, sid="link"):
    ch = [1500] * 16
    ch[4], ch[5], ch[9] = 2000, 1500, v
    r.bus.publish(RcState(t=r.clock.now(), src=sid, origin=RcOrigin.handset, ch_us=ch, seq=1))


def test_pocket_state_is_displayed_and_the_panel_is_still_available():
    r = Rig()
    handset(r, 1250)
    st = r.rc.status()
    assert st.mode == "pocket" and st.origin == "handset" and not st.sending and st.target is None
    assert st.controls["SA"] == 1 and st.controls["SB"] == 1 and st.controls["S1"] == pytest.approx(0.25)
    assert st.channels_us[4] == 2000
    assert r.poll() is None  # no lease: no virtual input, the Pocket shows only
    r.acquire()  # the Pocket wins in the engine, but the panel can be taken (the lease only guards the panel)
    assert r.rc.status().mode == "virtual"
    r.rc.release(r.rc.status().lease.id)
    r.clock.advance(1.01)
    assert r.rc.status().mode == "idle"


def test_default_radio_config_object_is_valid():
    cfg = RadioConfig()
    assert cfg.controls == [] and cfg.channels_us({}) == [1500] * 16


def test_idle_without_handset_and_pocket_follows_freshness():
    r = Rig()
    assert r.rc.status().mode == "idle" and r.rc.status().pocket_active is False
    handset(r)
    assert r.rc.status().mode == "pocket" and r.rc.status().pocket_active is True
    r.clock.advance(0.99)
    assert r.rc.status().mode == "pocket"
    r.clock.advance(0.02)
    assert r.rc.status().mode == "idle"


def test_poll_gives_the_engine_the_panel_positions_and_none_without_a_lease():
    r = Rig()
    assert r.rc.poll() is None
    lease = r.acquire()
    sw = r.rc.poll()
    assert (sw.sa, sw.sb, sw.sc, sw.sd, sw.se) == (0, 0, 0, 0, 0)
    r.rc.set(lease.id, {"SA": 1, "SB": 2, "SC": 1})
    sw = r.rc.poll()
    assert (sw.sa, sw.sb, sw.sc) == (1, 2, 1)


def test_preset_sets_the_panel_to_the_engine_output_and_opens_the_sd_guard_for_armed_motors():
    from exvectr_gs.rc import Switches

    r = Rig()
    r.rc.preset(Switches(sa=1, sb=2, sc=1, sd=1, se=0))  # no lease: ignored
    assert r.rc.status().controls == r.cfg.default_state()
    lease = r.acquire()
    r.rc.preset(Switches(sa=0, sb=2, sc=1, sd=1, se=0))
    st = r.rc.status()
    assert (st.controls["SA"], st.controls["SB"], st.controls["SC"], st.controls["SD"]) == (0, 2, 1, 1)
    assert st.locks["SD"]["locked"] is False  # shown as armed, relocks (disarms) after the usual timeout
    beat(r, lease, 61)
    assert r.rc.poll().sd == 0 and r.rc.status().locks["SD"]["locked"] is True
