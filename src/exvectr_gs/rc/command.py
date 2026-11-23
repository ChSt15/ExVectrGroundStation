"""Pure command logic: Pocket / virtual switches -> `CMD_STATE` output, with the GS pickup rule.

SAFETY-RELEVANT. No I/O, no clock of its own (every method takes `now` in ns), so the whole behaviour is unit tested
with a fake clock. Spec: workspace `docs/gs-control-design.md` sections 3 and 3.2 (D-GS4, D-GS7), `docs/safety.md`
(rules marked "Owner decision 2026-11-23").

The ground station computes no command and no safety decision here. It copies the operator's switch positions into
the command state (the vehicle decides what they mean), and it never lets a start, a Pocket reconnect or an input
change produce a safe -> unsafe change of the output:

- A switch that moves in the safe direction (SA down, SD down, SE released) is applied at once.
- A switch in the unsafe direction (SA up, SD up, SE pressed) is applied only after the input has been seen at the
  value the engine is currently sending ("picked up"). SC (mode) and SB (profile) are not like that: they follow the
  switch at once (SC only after MODE_SETTLE_NS from the first frame, so the vehicle sees the start mode first), also at
  start and after a Pocket reconnect. They cannot produce a go or arm edge.
- The engine starts every session with go NOT_GO, motors DISABLED, SE released, failsafe 0, nothing picked up.
- While the Pocket is lost and no virtual panel is the input, the output is held (SE is forced released) and every
  pickup is forgotten; the Pocket coming back applies the rule above, so a switch left up must first be cycled.
- A button (failsafe) forces the output to the safe values and clears the affected pickups.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

NS = 1_000_000_000

SEND_PERIOD_NS = 40_000_000  # 25 Hz
MIN_CHANGE_GAP_NS = 10_000_000  # an on-change frame at most once per 10 ms
POCKET_LOST_NS = 300_000_000  # no valid handset RcState for this long: Pocket stream lost
POCKET_BACK_FRAMES = 3  # consecutive valid frames before a lost Pocket counts as back
SE_MIN_PRESS_NS = 160_000_000  # a press is stretched to 4 frames so the vehicle's classifier sees it
START_WAIT_NS = 2 * NS  # wait this long for the vehicle's reported mode before the first frame
# The vehicle takes a mode change only as an OBSERVED change between consecutive accepted frames: after the first frame
# the engine keeps the start mode for this long (>= 3 frames at 25 Hz), and only then follows SC.
MODE_SETTLE_NS = 300_000_000

# Pulse widths (us) of the Pocket channels, the bands of the old vehicle decoder (wire value = (us - 1500) * 2):
# <= -500 low, -200..200 middle, >= 500 high. Anything else (and an unprovided channel, 0) is invalid.
LOW_MAX_US = 1250
MID_MIN_US, MID_MAX_US = 1400, 1600
HIGH_MIN_US = 1750

#: 1-based Pocket channels of the switches (config/radio.pocket.toml, unchanged)
CHANNELS = {"sa": 5, "sb": 6, "sc": 7, "sd": 8, "se": 9}

InputName = Literal["pocket", "virtual", "none"]


def band(us: int | None, positions: int) -> int | None:
    """Switch position (0 = lowest) of a pulse width, None if invalid. A two-position switch has no middle."""
    if us is None or us <= 0:
        return None
    if us <= LOW_MAX_US:
        return 0
    if us >= HIGH_MIN_US:
        return positions - 1
    if positions == 3 and MID_MIN_US <= us <= MID_MAX_US:
        return 1
    return None


@dataclass(frozen=True)
class Switches:
    """Decoded switch positions of one input sample. None = invalid. sa/sd/se: 0 safe, 1 unsafe."""

    sa: int | None = None
    sb: int | None = None
    sc: int | None = None
    sd: int | None = None
    se: int | None = None


def decode_channels(ch_us: list[int] | None) -> Switches:
    """Pocket RcState channels (us, 1-based channel numbers in CHANNELS) -> switch positions."""

    def us(name: str) -> int | None:
        i = CHANNELS[name] - 1
        return ch_us[i] if ch_us is not None and len(ch_us) > i else None

    return Switches(
        sa=band(us("sa"), 2), sb=band(us("sb"), 3), sc=band(us("sc"), 3), sd=band(us("sd"), 2), se=band(us("se"), 2)
    )


def mode_of_sc(sc: int) -> int:
    """SC position -> CMD vehicle_mode: position 0 REAL (0), positions 1 and 2 SIMULATION (1)."""
    return 0 if sc == 0 else 1


@dataclass(frozen=True)
class Output:
    """What is being sent (the command state without session, flags and sequence)."""

    go: int = 0
    profile: int = 2
    vehicle_mode: int = 0
    motors: int = 0
    reset_button: int = 0
    failsafe: int = 0


@dataclass(frozen=True)
class Frame:
    """One command frame to send: the output plus the information flags and the transport sequence."""

    out: Output
    seq: int
    pocket_live: bool
    input_virtual: bool


def _follow(v: int | None, o: int, picked: bool) -> tuple[int, bool]:
    """The pickup rule for a switch with a safe value 0 (SA, SD, SE). v is the input (None = invalid = safe)."""
    if v is None:
        v = 0
    if v == o:
        return o, True
    if v == 0:
        return 0, True  # safe direction: at once
    return (v, True) if picked else (o, False)  # unsafe direction: only after the input was seen at O's value


@dataclass
class PickupHints:
    """What the operator must do (display only). A key is present only while the input is waiting."""

    sa: bool = False  # SA is up while the output is NOT_GO: flip it down first
    sd: bool = False
    se: bool = False
    sc: int | None = None  # unused (SC always follows); kept for the API shape


@dataclass
class CommandCore:
    """The engine's state machine. Feed it samples, call `tick(now)` to get the frame to send (or None)."""

    out: Output = field(default_factory=Output)
    picked: dict[str, bool] = field(default_factory=lambda: {"sa": False, "sd": False, "se": False})
    failsafe_latched: bool = False
    pocket_state: Literal["absent", "live", "lost"] = "absent"
    input: InputName = "none"

    def __post_init__(self) -> None:
        self._t0: int | None = None
        self._ready = False
        self._settle_until: int | None = None
        self._profile_known = False
        self._pocket_last_valid: int | None = None
        self._pocket_run = 0
        self._pocket_sw = Switches()
        self._virtual_sw: Switches | None = None
        self._se_pressed_at: int | None = None
        self._se_holdoff = False
        self._panel_preset = False
        self._seq = 0
        self._last_send: int | None = None
        self._next_periodic: int | None = None
        self._dirty = False
        self.preset_request: Switches | None = None  # the shell must set the virtual panel to this, then clear it
        self.warnings: list[str] = []  # drained by the shell: "pocket_lost", "pocket_back"
        self.changes = 0  # number of output changes (tests)

    # ------------------------------------------------------------ session start

    def begin(self, now: int) -> None:
        """New session: the output is the safe state, nothing is picked up, sending waits for the vehicle's
        reported mode (at most START_WAIT_NS)."""
        self._t0 = now

    def vehicle_mode_reported(self, simulation: bool | None) -> None:
        """The vehicle's applied mode (VEHICLE_STATUS). Used once, to start the session in the mode the vehicle is
        in; later reports never change the output."""
        if self._ready or simulation is None:
            return
        self.out = replace(self.out, vehicle_mode=1 if simulation else 0)
        self._ready = True

    def ready(self, now: int) -> bool:
        if not self._ready and self._t0 is not None and now - self._t0 >= START_WAIT_NS:
            self._ready = True  # no report within 2 s: REAL
        return self._ready

    # ------------------------------------------------------------ inputs

    def pocket_frame(self, now: int, sw: Switches | None, valid: bool) -> None:
        """One handset RcState from the Router. `valid` = SOURCE_OK (a frame that is not valid neither refreshes
        nor counts toward 'back')."""
        if not valid:
            self._pocket_run = 0
            return
        self._pocket_last_valid = now
        self._pocket_run += 1
        self._pocket_sw = sw if sw is not None else Switches()
        if self.pocket_state != "live" and self._pocket_run >= POCKET_BACK_FRAMES:
            was = self.pocket_state
            self.pocket_state = "live"
            self._forget_pickups()  # reconnect rule: every pickup starts false
            if was == "lost":
                self.warnings.append("pocket_back")
        self._reevaluate(now)

    def virtual_input(self, now: int, sw: Switches | None) -> None:
        """The virtual panel's positions, or None while no client holds the virtual panel (no virtual input)."""
        self._virtual_sw = sw
        if sw is None:
            self._panel_preset = False
        self._reevaluate(now)

    def virtual_snapshot(self) -> Switches:
        """Switch positions equal to the current output (a virtual panel becoming the input starts from here)."""
        o = self.out
        return Switches(sa=o.go, sb=o.profile, sc=0 if o.vehicle_mode == 0 else 1, sd=o.motors, se=0)

    def virtual_acquired(self) -> None:
        """A client took the virtual panel and the shell has preset it to `virtual_snapshot()`: the panel equals the
        output, so every pickup is true (the next edit is a deliberate change) and the input switch needs no
        snapshot of its own."""
        for k in self.picked:
            self.picked[k] = True
        self._panel_preset = True

    def _forget_pickups(self) -> None:
        for k in self.picked:
            self.picked[k] = False

    # ------------------------------------------------------------ evaluation

    def _effective_input(self) -> InputName:
        if self.pocket_state == "live":
            return "pocket"
        if self._virtual_sw is not None:
            return "virtual"
        return "none"

    def _reevaluate(self, now: int) -> None:
        prev = self.input
        cur = self._effective_input()
        if cur != prev:
            self.input = cur
            if cur == "pocket":
                self._panel_preset = False
            if cur == "virtual" and self._panel_preset:
                pass  # the panel was preset on acquire (`virtual_acquired`), edits since then are real edits
            elif cur == "virtual":
                # becomes the input: the panel is preset to the output (the shell applies `preset_request` to the
                # panel), so the sample equals O and every pickup starts true. No output change here.
                self._forget_pickups()
                self._virtual_sw = self.virtual_snapshot()
                self.preset_request = self._virtual_sw
        sw = self._pocket_sw if cur == "pocket" else self._virtual_sw if cur == "virtual" else None
        o = self.out
        if sw is None:
            # no input: hold, but SE is momentary and is forced released (a held SE would keep zeroing running)
            self._set(replace(o, reset_button=0), now)
            self._se_pressed_at = None
            return
        go, self.picked["sa"] = _follow(sw.sa, o.go, self.picked["sa"])
        md, self.picked["sd"] = _follow(sw.sd, o.motors, self.picked["sd"])
        se_v = sw.se
        se, self.picked["se"] = _follow(se_v, o.reset_button, self.picked["se"])
        fs = o.failsafe
        if se == 1 and o.reset_button == 0 and self.failsafe_latched:
            # The vehicle refuses an SE reset while the failsafe request is still 1 (owner decision Q6): the
            # request is cleared first and goes out in a frame of its own, the reset press follows after it.
            self.failsafe_latched = False
            fs = 0
            self._se_holdoff = True
        if self._se_holdoff:
            se = o.reset_button  # hold the press back until the failsafe = 0 frame has been sent
        if o.reset_button == 1 and se == 0 and self._se_pressed_at is not None:
            if now - self._se_pressed_at < SE_MIN_PRESS_NS:
                se = 1  # stretch a short press to 4 frames; the safe release follows on a later sample or tick
        if se == 1 and o.reset_button == 0:
            self._se_pressed_at = now
        if se == 0 and o.reset_button == 0:
            self._se_pressed_at = None
        profile = o.profile
        if sw.sb is not None:
            profile = sw.sb
            self._profile_known = True
        vm = o.vehicle_mode
        if sw.sc is not None and self._settle_until is not None and now >= self._settle_until:
            vm = mode_of_sc(sw.sc)  # follows at once (also at start / reconnect), once the start mode was observed
        if self.failsafe_latched:
            go, md = 0, 0
        self._set(replace(o, go=go, profile=profile, vehicle_mode=vm, motors=md, reset_button=se, failsafe=fs), now)

    def _set(self, new: Output, now: int) -> None:
        if new != self.out:
            self.out = new
            self._dirty = True
            self.changes += 1

    def hints(self) -> PickupHints:
        """What the current input waits for (only while an input exists)."""
        h = PickupHints()
        sw = self._pocket_sw if self.input == "pocket" else self._virtual_sw if self.input == "virtual" else None
        if sw is None:
            return h
        o = self.out
        h.sa = sw.sa == 1 and o.go == 0
        h.sd = sw.sd == 1 and o.motors == 0
        h.se = sw.se == 1 and o.reset_button == 0
        return h

    # ------------------------------------------------------------ failsafe level (GS FAILSAFE button, later step)

    def set_failsafe(self, on: bool, now: int) -> None:
        """Latch the failsafe request level. True: failsafe = 1 with motors DISABLED and go NOT_GO in the same
        frame, held until cleared. The inputs cannot undo it: the affected pickups are cleared, so each switch must
        be cycled afterwards. False clears the level only."""
        if on:
            self.failsafe_latched = True
            self.picked["sa"] = self.picked["sd"] = False
            self._set(replace(self.out, failsafe=1, go=0, motors=0), now)
        else:
            self.failsafe_latched = False
            self._set(replace(self.out, failsafe=0), now)

    # ------------------------------------------------------------ time

    def tick(self, now: int) -> Frame | None:
        """Pocket-loss detection, SE release after its minimum press, and the 25 Hz schedule. Returns the frame to
        send now, or None. The same frame object goes to every link (identical sequence number)."""
        if self.pocket_state == "live" and self._pocket_last_valid is not None:
            if now - self._pocket_last_valid > POCKET_LOST_NS:
                self.pocket_state = "lost"
                self._pocket_run = 0
                self._forget_pickups()
                self.warnings.append("pocket_lost")
        self._reevaluate(now)  # also releases a stretched SE press, and applies loss at once
        if not self.ready(now):
            return None
        send = False
        if self._next_periodic is None or now >= self._next_periodic:
            send = True
            behind = self._next_periodic is None or now - self._next_periodic >= SEND_PERIOD_NS
            self._next_periodic = now + SEND_PERIOD_NS if behind else self._next_periodic + SEND_PERIOD_NS
        elif self._dirty and (self._last_send is None or now - self._last_send >= MIN_CHANGE_GAP_NS):
            send = True  # an output change goes out at once (at most one per 10 ms); the 25 Hz anchor is unchanged
        if not send:
            return None
        self._dirty = False
        if self._settle_until is None:
            self._settle_until = now + MODE_SETTLE_NS  # the first frame carries the start mode
        self._last_send = now
        self._se_holdoff = False  # a frame (with failsafe = 0) goes out now; the held-back SE press may follow
        self._seq = (self._seq + 1) & 0xFF
        return Frame(self.out, self._seq, self.pocket_state == "live", self.input == "virtual")
