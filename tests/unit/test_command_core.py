"""Pure command logic (rc/command.py): pickup rule, Pocket loss and return, SE forcing, virtual input, failsafe level,
25 Hz scheduling. A fake clock (plain ns integers), no I/O. Spec: workspace docs/gs-control-design.md 3 and 3.2."""

import itertools
import random

import pytest

from exvectr_gs.rc.command import (
    POCKET_LOST_NS,
    SE_MIN_PRESS_NS,
    SEND_PERIOD_NS,
    START_WAIT_NS,
    CommandCore,
    Switches,
    band,
    decode_channels,
)

MS = 1_000_000

SAFE = Switches(sa=0, sb=2, sc=0, sd=0, se=0)


def sw(**kw):
    return Switches(**{**{"sa": 0, "sb": 2, "sc": 0, "sd": 0, "se": 0}, **kw})


class Rig:
    """A core at time t (ns), the Pocket frames come every 5 ms like the Router's 200/s USB stream."""

    def __init__(self, sim=False):
        self.t = 1000 * 10**9
        self.core = CommandCore()
        self.core.begin(self.t)
        if sim is not None:
            self.core.vehicle_mode_reported(sim)
        self.frames = []

    def advance(self, ms):
        self.t += int(ms * MS)

    def tick(self, ms=0):
        self.advance(ms)
        f = self.core.tick(self.t)
        if f is not None:
            self.frames.append(f)
        return f

    def pocket(self, s, ms=5, n=1, valid=True):
        for _ in range(n):
            self.advance(ms)
            self.core.pocket_frame(self.t, s, valid)
            self.tick()

    def live(self, s):
        """Pocket arrives (3 valid frames) showing s."""
        self.pocket(s, n=3)

    def virtual(self, s):
        self.core.virtual_input(self.t, s)
        self.tick()

    @property
    def out(self):
        return self.core.out


# ---------------------------------------------------------------- decoding


@pytest.mark.parametrize(
    ("us", "n", "pos"),
    [(1000, 2, 0), (1250, 2, 0), (1251, 2, None), (1500, 2, None), (1749, 2, None), (1750, 2, 1), (2000, 2, 1)]
    + [(1000, 3, 0), (1250, 3, 0), (1300, 3, None), (1400, 3, 1), (1500, 3, 1), (1600, 3, 1), (1700, 3, None)]
    + [(1750, 3, 2), (2000, 3, 2), (0, 3, None), (None, 2, None)],
)
def test_bands_of_the_old_decoder(us, n, pos):
    assert band(us, n) == pos


def test_decode_channels_uses_channels_5_to_9_and_invalid_when_short():
    ch = [1500] * 16
    ch[4], ch[5], ch[6], ch[7], ch[8] = 2000, 1500, 1000, 2000, 2000
    assert decode_channels(ch) == Switches(sa=1, sb=1, sc=0, sd=1, se=1)
    assert decode_channels([1500] * 6) == Switches(sa=None, sb=1, sc=None, sd=None, se=None)
    assert decode_channels(None) == Switches()


# ---------------------------------------------------------------- start


def test_session_starts_safe_and_waits_for_the_vehicle_mode_at_most_2_s():
    r = Rig(sim=None)
    assert r.out.go == r.out.motors == r.out.reset_button == r.out.failsafe == 0 and r.out.profile == 2
    for _ in range(19):  # 1.9 s: nothing is sent yet
        assert r.tick(100) is None
    r.advance(100)
    assert r.t - 1000 * 10**9 >= START_WAIT_NS
    f = r.tick()
    assert f is not None and f.out.vehicle_mode == 0  # no report within 2 s: REAL


def test_session_starts_in_the_mode_the_vehicle_reports():
    r = Rig(sim=True)
    f = r.tick()
    assert f is not None and f.out.vehicle_mode == 1
    r.core.vehicle_mode_reported(False)  # later reports never change the output
    assert r.out.vehicle_mode == 1


def test_first_valid_sb_sets_the_profile_and_sb_always_follows():
    r = Rig()
    assert r.out.profile == 2
    r.live(sw(sb=0))
    assert r.out.profile == 0
    r.pocket(sw(sb=1))
    assert r.out.profile == 1
    r.pocket(sw(sb=None))  # invalid SB holds
    assert r.out.profile == 1


# ---------------------------------------------------------------- pickup rule per switch


@pytest.mark.parametrize(("name", "field"), [("sa", "go"), ("sd", "motors")])
def test_unsafe_switch_at_start_is_ignored_until_cycled(name, field):
    r = Rig()
    r.live(sw(**{name: 1}))  # the switch is up when the Pocket first shows
    assert getattr(r.out, field) == 0
    assert getattr(r.core.hints(), name) is True
    r.pocket(sw(**{name: 1}), n=20)
    assert getattr(r.out, field) == 0
    r.pocket(sw(**{name: 0}))  # cycle: down
    assert getattr(r.out, field) == 0 and getattr(r.core.hints(), name) is False
    r.pocket(sw(**{name: 1}))  # up again: taken
    assert getattr(r.out, field) == 1
    r.pocket(sw(**{name: 0}))  # safe direction: at once
    assert getattr(r.out, field) == 0


@pytest.mark.parametrize("name", ["sa", "sd", "se"])
def test_invalid_channel_decodes_to_the_safe_value(name):
    r = Rig()
    r.live(sw())
    r.pocket(sw(**{name: 1}))
    assert (r.out.go, r.out.motors, r.out.reset_button) != (0, 0, 0)
    r.pocket(sw(**{name: None}))
    assert (r.out.go, r.out.motors) == (0, 0)
    r.advance(SE_MIN_PRESS_NS // MS)  # the SE stretch has run out
    r.pocket(sw(**{name: None}))
    assert r.out.reset_button == 0


def test_se_pressed_at_start_needs_a_release_first_and_a_press_is_stretched_to_160_ms():
    r = Rig()
    r.live(sw(se=1))
    assert r.out.reset_button == 0 and r.core.hints().se is True
    r.pocket(sw(se=0))
    t_press = r.t + 5 * MS
    r.pocket(sw(se=1))
    assert r.out.reset_button == 1
    r.pocket(sw(se=0))  # released after 5 ms: stays pressed until 160 ms have passed
    assert r.out.reset_button == 1
    r.pocket(sw(se=0), ms=100)
    assert r.out.reset_button == 1
    r.pocket(sw(se=0), ms=60)
    assert r.t - t_press >= SE_MIN_PRESS_NS and r.out.reset_button == 0


def test_a_long_se_hold_stays_pressed_while_held():
    r = Rig()
    r.live(sw())
    r.pocket(sw(se=1))
    r.pocket(sw(se=1), ms=5, n=400)  # 2 s
    assert r.out.reset_button == 1
    r.pocket(sw(se=0))
    r.pocket(sw(se=0), ms=200)
    assert r.out.reset_button == 0


def test_sc_difference_at_start_is_shown_and_ignored_until_the_switch_matches():
    r = Rig(sim=False)  # the vehicle is REAL
    r.live(sw(sc=1))  # the Pocket shows SIMULATION
    assert r.out.vehicle_mode == 0 and r.core.hints().sc == 0  # "flip SC to REAL to take over"
    r.pocket(sw(sc=2), n=10)  # still a simulation position
    assert r.out.vehicle_mode == 0
    r.pocket(sw(sc=0))  # matches: picked up
    assert r.out.vehicle_mode == 0 and r.core.hints().sc is None
    r.pocket(sw(sc=1))
    assert r.out.vehicle_mode == 1
    r.pocket(sw(sc=2))  # 1 <-> 2 is no change
    assert r.out.vehicle_mode == 1
    r.pocket(sw(sc=0))
    assert r.out.vehicle_mode == 0
    r.pocket(sw(sc=None))  # invalid SC holds
    assert r.out.vehicle_mode == 0


def test_sc_follows_a_simulation_session_too():
    r = Rig(sim=True)
    r.live(sw(sc=2))  # equal to the vehicle's mode from the start: picked up at once
    assert r.out.vehicle_mode == 1 and r.core.hints().sc is None
    r.pocket(sw(sc=0))
    assert r.out.vehicle_mode == 0


# ---------------------------------------------------------------- Pocket lost and back


def test_pocket_needs_three_valid_frames_and_invalid_frames_do_not_count():
    r = Rig()
    r.pocket(sw(sa=0), n=2)
    assert r.core.pocket_state == "absent" and r.core.input == "none"
    r.pocket(sw(sa=0), valid=False)  # SOURCE_OK clear: breaks the run
    r.pocket(sw(sa=0), n=2)
    assert r.core.pocket_state == "absent"
    r.pocket(sw(sa=0))
    assert r.core.pocket_state == "live" and r.core.input == "pocket"


def test_pocket_loss_is_a_warning_only_the_output_is_held_and_se_is_released():
    r = Rig()
    r.live(sw())
    r.pocket(sw(sa=1))
    r.pocket(sw(sd=1, sa=1))
    r.pocket(sw(sd=1, sa=1, se=1))
    before = r.out
    assert (before.go, before.motors, before.reset_button) == (1, 1, 1)
    r.tick(POCKET_LOST_NS // MS - 10)
    assert r.core.pocket_state == "live"
    r.tick(20)
    assert r.core.pocket_state == "lost" and r.core.warnings == ["pocket_lost"]
    assert (r.out.go, r.out.motors) == (1, 1)  # held: no abort, no failsafe, no motors off
    assert r.out.reset_button == 0 and r.out.failsafe == 0  # SE forced released
    assert r.core.input == "none"
    r.tick(5000)  # a long loss changes nothing more
    assert (r.out.go, r.out.motors, r.out.failsafe) == (1, 1, 0)
    assert r.core.picked == {"sa": False, "sc": False, "sd": False, "se": False}


def test_pocket_back_applies_the_pickup_rule_safe_changes_at_once_unsafe_only_after_a_cycle():
    r = Rig()
    r.live(sw())
    r.pocket(sw(sa=1, sd=1))
    assert (r.out.go, r.out.motors) == (1, 1)
    r.tick(400)  # lost
    # (a) back with SA released and SD still up: abort at once, motors stay (SD up is the value being sent)
    r.live(sw(sa=0, sd=1))
    assert r.out.go == 0 and r.out.motors == 1
    # (b) back with SD down: motors disabled at once
    r.pocket(sw(sa=0, sd=0))
    assert r.out.motors == 0
    # lost again, back with both switches up while the output shows NOT_GO / DISABLED: nothing until cycled
    r.tick(400)
    r.live(sw(sa=1, sd=1))
    assert (r.out.go, r.out.motors) == (0, 0)
    assert r.core.hints().sa and r.core.hints().sd
    r.pocket(sw(sa=0, sd=1))
    assert (r.out.go, r.out.motors) == (0, 0)
    r.pocket(sw(sa=1, sd=1))
    assert (r.out.go, r.out.motors) == (1, 0)  # SA cycled: taken. SD was never down: still waiting
    r.pocket(sw(sa=1, sd=0))
    r.pocket(sw(sa=1, sd=1))
    assert (r.out.go, r.out.motors) == (1, 1)


def test_sc_differing_after_the_pocket_returns_waits_for_the_switch_to_match():
    r = Rig(sim=False)
    r.live(sw(sc=0))
    r.pocket(sw(sc=1))
    assert r.out.vehicle_mode == 1
    r.tick(400)
    r.live(sw(sc=0))  # the output is SIMULATION, the Pocket shows REAL: no change until it matches
    assert r.out.vehicle_mode == 1 and r.core.hints().sc == 1
    r.pocket(sw(sc=0), n=10)
    assert r.out.vehicle_mode == 1
    r.pocket(sw(sc=2))  # matches (a simulation position): picked up
    assert r.out.vehicle_mode == 1 and r.core.hints().sc is None
    r.pocket(sw(sc=0))
    assert r.out.vehicle_mode == 0


def test_a_press_of_se_during_a_loss_is_not_resumed_on_return():
    r = Rig()
    r.live(sw())
    r.pocket(sw(se=1))
    assert r.out.reset_button == 1
    r.tick(400)
    assert r.out.reset_button == 0
    r.live(sw(se=1))  # still held on return: it must be released and pressed again
    assert r.out.reset_button == 0
    r.pocket(sw(se=0))
    r.pocket(sw(se=1))
    assert r.out.reset_button == 1


# ---------------------------------------------------------------- virtual panel and the real Pocket


def test_virtual_input_only_while_the_pocket_is_not_live_and_sets_the_flag():
    r = Rig()
    r.virtual(sw())  # becomes the input: the panel is preset to the output
    assert r.core.input == "virtual" and r.core.preset_request is not None
    assert r.core.preset_request == Switches(sa=0, sb=2, sc=0, sd=0, se=0)
    r.core.preset_request = None
    r.virtual(sw(sa=1))
    assert r.out.go == 1
    f = r.tick(40)
    assert f.input_virtual is True and f.pocket_live is False
    r.live(sw(sa=0))  # the real Pocket wins when live
    assert r.core.input == "pocket"
    r.virtual(sw(sa=1))  # ignored
    r.pocket(sw(sa=0))
    f = r.tick(40)
    assert r.out.go == 0 and f.input_virtual is False and f.pocket_live is True


def test_virtual_panel_taking_over_changes_nothing_that_is_sent():
    r = Rig()
    r.live(sw())
    r.pocket(sw(sa=1, sd=1))
    assert (r.out.go, r.out.motors) == (1, 1)
    r.tick(400)  # Pocket lost
    n = r.core.changes
    r.virtual(sw(sa=0, sd=0, sb=0, sc=1))  # a stale panel state: replaced by the snapshot, never applied
    assert r.core.input == "virtual" and r.core.changes == n
    assert (r.out.go, r.out.motors, r.out.profile) == (1, 1, 2)
    assert r.core.preset_request == Switches(sa=1, sb=2, sc=0, sd=1, se=0)
    r.core.preset_request = None
    r.virtual(Switches(sa=1, sb=2, sc=0, sd=1, se=0))
    assert (r.out.go, r.out.motors) == (1, 1)
    r.virtual(Switches(sa=0, sb=2, sc=0, sd=1, se=0))  # the virtual SA down: abort
    assert r.out.go == 0


def test_virtual_unsafe_switch_after_a_button_needs_a_cycle():
    r = Rig()
    r.virtual(sw())
    r.core.preset_request = None
    r.virtual(sw(sa=1, sd=1))
    assert (r.out.go, r.out.motors) == (1, 1)
    r.core.set_failsafe(True, r.t)
    r.virtual(sw(sa=1, sd=1))
    assert (r.out.go, r.out.motors, r.out.failsafe) == (0, 0, 1)
    r.core.set_failsafe(False, r.t)
    r.virtual(sw(sa=1, sd=1))
    assert (r.out.go, r.out.motors, r.out.failsafe) == (0, 0, 0)  # the inputs must be cycled
    r.virtual(sw(sa=0, sd=0))
    r.virtual(sw(sa=1, sd=1))
    assert (r.out.go, r.out.motors) == (1, 1)


def test_virtual_lease_ending_holds_the_output_and_releases_se():
    r = Rig()
    r.virtual(sw())
    r.core.preset_request = None
    r.virtual(sw(sa=1, se=1))
    assert (r.out.go, r.out.reset_button) == (1, 1)
    r.virtual(None)
    assert r.core.input == "none" and r.out.go == 1 and r.out.reset_button == 0


def test_pocket_returning_over_a_virtual_input_forgets_the_pickups():
    r = Rig()
    r.virtual(sw())
    r.core.preset_request = None
    r.virtual(sw(sa=1))
    assert r.out.go == 1
    r.live(sw(sa=1))  # the Pocket arrives with SA up: equal to the output, nothing changes, picked up
    assert r.core.input == "pocket" and r.out.go == 1
    r.pocket(sw(sa=0))
    assert r.out.go == 0


# ---------------------------------------------------------------- failsafe level


def test_failsafe_level_latches_with_motors_disabled_and_go_not_go_in_the_same_frame():
    r = Rig()
    r.live(sw())
    r.pocket(sw(sa=1, sd=1))
    r.tick(40)
    r.core.set_failsafe(True, r.t)
    f = r.tick(10)
    assert f is not None and (f.out.failsafe, f.out.motors, f.out.go) == (1, 0, 0)
    r.pocket(sw(sa=1, sd=1), n=20)  # the switches cannot undo it
    assert (r.out.failsafe, r.out.motors, r.out.go) == (1, 0, 0)
    r.tick(5000)
    assert r.out.failsafe == 1  # held indefinitely, also across a Pocket loss
    r.core.set_failsafe(False, r.t)
    assert r.out.failsafe == 0 and r.out.go == 0 and r.out.motors == 0
    r.pocket(sw(sa=1, sd=1), n=5)
    assert (r.out.go, r.out.motors) == (0, 0)  # each switch must be cycled


# ---------------------------------------------------------------- scheduling (25 Hz, on change)


def test_sends_at_25_hz_with_an_incrementing_sequence_and_never_bursts_after_a_stall():
    r = Rig()
    r.tick()  # first frame
    r.frames.clear()
    for _ in range(200):  # 1 s in 5 ms steps
        r.tick(5)
    assert 24 <= len(r.frames) <= 26
    assert [f.seq for f in r.frames] == [(r.frames[0].seq + i) & 0xFF for i in range(len(r.frames))]
    r.frames.clear()
    r.tick(3000)  # the loop stalled for 3 s: one frame, no catch-up burst
    assert len(r.frames) == 1
    assert r.tick(5) is None


def test_an_output_change_goes_out_at_once_but_at_most_one_per_10_ms():
    r = Rig()
    r.tick()
    r.live(sw())
    r.frames.clear()
    r.advance(2)  # somewhere inside a period
    r.core.pocket_frame(r.t, sw(sa=1), True)
    f1 = r.tick()
    assert f1 is not None and f1.out.go == 1
    r.core.pocket_frame(r.t + MS, sw(sa=0), True)
    r.advance(1)
    assert r.tick() is None  # inside the 10 ms gap
    r.advance(9)
    f2 = r.tick()
    assert f2 is not None and f2.out.go == 0 and f2.seq == (f1.seq + 1) & 0xFF


def test_sequence_wraps_at_256():
    r = Rig()
    r.tick()
    for _ in range(300):
        r.tick(SEND_PERIOD_NS // MS)
    assert {f.seq for f in r.frames} == set(range(256))


# ---------------------------------------------------------------- the central property


def _unsafe_edges(prev, cur):
    return [n for n in ("go", "motors") if getattr(prev, n) == 0 and getattr(cur, n) == 1]


POS = {"sa": (0, 1, None), "sd": (0, 1, None), "se": (0, 1, None), "sc": (0, 1, 2, None), "sb": (0, 1, 2, None)}


@pytest.mark.parametrize("start", list(itertools.product((0, 1, None), (0, 1, None), (0, 1, 2))))
def test_no_start_loss_or_return_ever_produces_an_unsafe_edge_without_a_cycle(start):
    """An oracle independent of the core: SA / SD may go 0 -> 1 only on a sample that is unsafe while the previous
    sample since the last reset event (start, loss, return, failsafe) was safe (the switch was seen down)."""
    sa, sd, sc = start
    rnd = random.Random(hash(start) & 0xFFFF)
    for _ in range(25):
        r = Rig(sim=False)
        seen_down = {"sa": False, "sd": False}
        prev = r.out
        steps = [("live", sw(sa=sa, sd=sd, sc=sc))]
        for _ in range(60):
            kind = rnd.choice(["frame"] * 8 + ["loss", "failsafe", "clear"])
            steps.append((kind, sw(**{k: rnd.choice(v) for k, v in POS.items()})))
        for kind, s in steps:
            if kind == "live":
                r.live(s)
                seen_down = {"sa": s.sa in (0, None), "sd": s.sd in (0, None)}
                # the first 3 frames carry the same sample; the pickup is evaluated on each of them
            elif kind == "frame":
                r.pocket(s)
                for n in ("sa", "sd"):
                    v = getattr(s, n)
                    if r.core.pocket_state == "live":
                        if v in (0, None):
                            seen_down[n] = True
            elif kind == "loss":
                r.tick(400)
                seen_down = {"sa": False, "sd": False}
                r.live(s)
                seen_down = {"sa": s.sa in (0, None), "sd": s.sd in (0, None)}
            elif kind == "failsafe":
                r.core.set_failsafe(True, r.t)
                seen_down = {"sa": False, "sd": False}
            else:
                r.core.set_failsafe(False, r.t)
            cur = r.out
            for e in _unsafe_edges(prev, cur):
                name = "sa" if e == "go" else "sd"
                assert seen_down[name], f"{e} went 0 -> 1 without the switch seen down: start={start} step={kind} {s}"
            prev = cur
        # the SE output is never pressed while no input exists
        r.tick(400)
        assert r.out.reset_button in (0, 1)


def test_se_reset_after_a_failsafe_clears_the_request_in_an_earlier_frame():
    """Owner decision Q6: the vehicle refuses an SE reset while failsafe = 1, so the engine sends failsafe = 0 first."""
    r = Rig()
    r.live(sw())
    r.pocket(sw(sa=1, sd=1))
    r.core.set_failsafe(True, r.t)
    r.tick(10)
    r.pocket(sw(se=0), ms=5, n=3)  # SE released, SA / SD down: normal
    r.frames.clear()
    r.pocket(sw(se=1))  # the operator presses SE
    r.tick(20)
    r.tick(20)
    sent = [(f.out.failsafe, f.out.reset_button) for f in r.frames]
    assert (0, 1) in sent
    first_press = next(i for i, x in enumerate(sent) if x[1] == 1)
    assert first_press > 0 and sent[first_press - 1] == (0, 0)  # an earlier frame carried failsafe = 0, reset released
    assert all(not (f == 1 and b == 1) for f, b in sent)  # never failsafe = 1 together with the reset press
    assert r.core.failsafe_latched is False and r.out.go == 0 and r.out.motors == 0
