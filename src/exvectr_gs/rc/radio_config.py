"""Radio configuration: control ids, channel mapping and safe defaults (design 5.6).

The mapping is configuration, never code: the vehicle firmware owns the real channel -> function
mapping. `meaning` texts are informational labels. Nothing here knows about Starship commands.
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KINDS = ("toggle2", "switch3", "momentary", "slider")
POSITIONS = {"toggle2": 2, "switch3": 3, "momentary": 2}
US_MIN, US_MAX = 1000, 2000  # every configured pulse width must be inside this range
MAX_CHANNELS = 16  # the RcState packet carries 16 channels
DEFAULT_LOCK_TIMEOUT_S = 60.0
# Stick display (GUI only, never sent by the ground station): stick axis -> 1-based channel, and the
# gimbal mode (2: left stick yaw / throttle, right stick roll / pitch; 1: left yaw / pitch, right roll / throttle).
STICK_AXES = ("roll", "pitch", "throttle", "yaw")
DEFAULT_STICKS = {"mode": 2, "roll": 1, "pitch": 2, "throttle": 3, "yaw": 4}
MAX_LOCK_TIMEOUT_S = 3600.0


class RadioConfigError(ValueError):
    """The radio configuration is invalid: the RC controller refuses to start."""


@dataclass(frozen=True)
class ControlSpec:
    id: str
    kind: str
    channel: int  # 1-based
    default: float | int  # switch index (the safe position) or slider position 0..1
    values_us: tuple[int, ...] = ()  # switches: pulse width per position
    range_us: tuple[int, int] = (US_MIN, US_MAX)  # slider
    label: str = ""
    meaning: str = ""  # informational only
    svg: Mapping[str, float] = field(default_factory=dict)
    lock: bool = False  # deliberate-action guard: held at the default until unlocked (generic, no meaning attached)
    lock_timeout_s: float = DEFAULT_LOCK_TIMEOUT_S  # an unlock lapses (and the control returns to default) after this

    @property
    def discrete(self) -> bool:
        return self.kind != "slider"

    def check(self, value: float | int) -> float | int:
        """Validate a control position (index for switches, 0..1 for sliders). Raises ValueError."""
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"control {self.id}: value must be a finite number")
        if self.discrete:
            if int(value) != value or not 0 <= value < len(self.values_us):
                raise ValueError(f"control {self.id}: position must be an integer in 0..{len(self.values_us) - 1}")
            return int(value)
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"control {self.id}: slider value must be within 0..1")
        return float(value)

    def to_us(self, value: float | int) -> int:
        if self.discrete:
            return self.values_us[int(value)]
        lo, hi = self.range_us
        return round(lo + float(value) * (hi - lo))

    def from_us(self, us: int) -> float | int:
        """Nearest position for a pulse width (used to display a mirrored handset)."""
        if self.discrete:
            return min(range(len(self.values_us)), key=lambda i: abs(self.values_us[i] - us))
        lo, hi = self.range_us
        return min(1.0, max(0.0, (us - lo) / (hi - lo)))


@dataclass
class RadioConfig:
    name: str = "radio"
    send_rate_hz: float = 25.0
    channel_count: int = MAX_CHANNELS
    channel_default_us: int = 1500
    channel_defaults: dict[int, int] = field(default_factory=dict)
    mapping_note: str = ""
    controls: list[ControlSpec] = field(default_factory=list)
    sticks: dict[str, int] | None = None  # None: the defaults, without the overlap check

    def __post_init__(self) -> None:
        self.validate()

    # ------------------------------------------------------------ validation

    def validate(self) -> None:
        if not 1 <= self.send_rate_hz <= 100:
            raise RadioConfigError("send_rate_hz must be within 1..100")
        if not 1 <= self.channel_count <= MAX_CHANNELS:
            raise RadioConfigError(f"channel_count must be within 1..{MAX_CHANNELS}")
        if not US_MIN <= self.channel_default_us <= US_MAX:
            raise RadioConfigError(f"channel_default_us must be within {US_MIN}..{US_MAX}")
        for ch, us in self.channel_defaults.items():
            if not 1 <= ch <= self.channel_count or not US_MIN <= us <= US_MAX:
                raise RadioConfigError(f"channel_defaults: bad entry {ch} = {us}")
        ids = [c.id for c in self.controls]
        if len(set(ids)) != len(ids):
            raise RadioConfigError("control ids must be unique")
        chans = [c.channel for c in self.controls]
        if len(set(chans)) != len(chans):
            raise RadioConfigError("control channels must be unique")
        for c in self.controls:
            self._check_control(c)
        self._check_sticks(set(chans))

    def _check_sticks(self, control_channels: set[int]) -> None:
        """Display-only stick mapping: known keys, mode 1 or 2, distinct channels that no control uses."""
        if self.sticks is None:
            self.sticks = dict(DEFAULT_STICKS)
            return
        extra = set(self.sticks) - set(DEFAULT_STICKS)
        if extra:
            raise RadioConfigError(f"sticks: unknown keys {sorted(extra)}")
        self.sticks = {**DEFAULT_STICKS, **self.sticks}
        if self.sticks["mode"] not in (1, 2):
            raise RadioConfigError("sticks: mode must be 1 or 2")
        chans = [self.sticks[a] for a in STICK_AXES]
        if any(not isinstance(c, int) or isinstance(c, bool) or not 1 <= c <= self.channel_count for c in chans):
            raise RadioConfigError(f"sticks: channels must be integers within 1..{self.channel_count}")
        if len(set(chans)) != len(chans):
            raise RadioConfigError("sticks: channels must be distinct")
        if set(chans) & control_channels:
            raise RadioConfigError("sticks: a stick channel is also used by a control")

    def _check_control(self, c: ControlSpec) -> None:
        if not c.id or any(ch.isspace() or ch == "=" for ch in c.id):
            raise RadioConfigError(f"invalid control id {c.id!r}")
        if c.kind not in KINDS:
            raise RadioConfigError(f"control {c.id}: kind must be one of {KINDS}")
        if not 1 <= c.channel <= self.channel_count:
            raise RadioConfigError(f"control {c.id}: channel {c.channel} outside 1..{self.channel_count}")
        if c.discrete:
            n = POSITIONS[c.kind]
            if len(c.values_us) != n or any(not US_MIN <= v <= US_MAX for v in c.values_us):
                raise RadioConfigError(f"control {c.id}: {c.kind} needs {n} values_us within {US_MIN}..{US_MAX}")
            if list(c.values_us) != sorted(c.values_us):
                raise RadioConfigError(f"control {c.id}: values_us must be ascending (lowest position first)")
        else:
            lo, hi = c.range_us
            if not US_MIN <= lo < hi <= US_MAX:
                raise RadioConfigError(f"control {c.id}: range_us must be ascending within {US_MIN}..{US_MAX}")
        if not isinstance(c.lock, bool):
            raise RadioConfigError(f"control {c.id}: lock must be true or false")
        if not isinstance(c.lock_timeout_s, (int, float)) or not 0 < c.lock_timeout_s <= MAX_LOCK_TIMEOUT_S:
            raise RadioConfigError(f"control {c.id}: lock_timeout_s must be within (0, {MAX_LOCK_TIMEOUT_S:g}]")
        try:
            c.check(c.default)
        except ValueError as e:
            raise RadioConfigError(f"default out of range: {e}") from None
        if c.kind == "momentary" and c.default != 0:
            raise RadioConfigError(f"control {c.id}: a momentary control must default to released (0)")

    # ------------------------------------------------------------ lookups and conversion

    def control(self, cid: str) -> ControlSpec:
        for c in self.controls:
            if c.id == cid:
                return c
        raise ValueError(f"unknown control {cid!r}")

    def default_state(self) -> dict[str, float | int]:
        return {c.id: c.default for c in self.controls}

    def base_channels(self) -> list[int]:
        out = [self.channel_default_us] * MAX_CHANNELS
        for ch, us in self.channel_defaults.items():
            out[ch - 1] = us
        return out

    def channels_us(self, state: Mapping[str, float | int]) -> list[int]:
        """The full 16-channel vector for a control state (unmapped channels at their defaults)."""
        out = self.base_channels()
        for c in self.controls:
            out[c.channel - 1] = c.to_us(state.get(c.id, c.default))
        return out

    def state_from_channels(self, ch_us: list[int]) -> dict[str, float | int]:
        """Mirrored handset display: nearest configured position per control."""
        return {c.id: c.from_us(ch_us[c.channel - 1]) for c in self.controls if c.channel <= len(ch_us)}


def _control(d: Mapping[str, Any]) -> ControlSpec:
    known = {
        "id",
        "kind",
        "channel",
        "default",
        "values_us",
        "range_us",
        "label",
        "meaning",
        "svg",
        "lock",
        "lock_timeout_s",
    }
    extra = set(d) - known
    if extra:
        raise RadioConfigError(f"control {d.get('id')!r}: unknown keys {sorted(extra)}")
    try:
        kind = d["kind"]
        default = d.get("default", 0)
        return ControlSpec(
            id=str(d["id"]),
            kind=kind,
            channel=int(d["channel"]),
            default=float(default) if kind == "slider" else default,
            values_us=tuple(int(v) for v in d.get("values_us", ())),
            range_us=tuple(int(v) for v in d.get("range_us", (US_MIN, US_MAX))),  # type: ignore[arg-type]
            label=str(d.get("label", d["id"])),
            meaning=str(d.get("meaning", "")),
            svg=dict(d.get("svg", {})),
            lock=d.get("lock", False),
            lock_timeout_s=d.get("lock_timeout_s", DEFAULT_LOCK_TIMEOUT_S),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise RadioConfigError(f"control {d.get('id')!r}: bad or missing field ({e})") from None


def radio_config_from_dict(data: Mapping[str, Any]) -> RadioConfig:
    data = dict(data)
    controls = [_control(c) for c in data.pop("controls", [])]
    known = {"name", "send_rate_hz", "channel_count", "channel_default_us", "channel_defaults", "mapping_note"}
    known.add("sticks")
    extra = set(data) - known
    if extra:
        raise RadioConfigError(f"unknown radio config keys {sorted(extra)}")
    try:
        if "channel_defaults" in data:
            data["channel_defaults"] = {int(k): int(v) for k, v in data["channel_defaults"].items()}
        if "sticks" in data and not isinstance(data["sticks"], dict):
            raise RadioConfigError("sticks must be a table")
        return RadioConfig(controls=controls, **data)
    except (TypeError, ValueError, AttributeError) as e:
        if isinstance(e, RadioConfigError):
            raise
        raise RadioConfigError(str(e)) from None


def load_radio_config(path: str | Path) -> RadioConfig:
    try:
        with open(Path(path).expanduser(), "rb") as fh:
            return radio_config_from_dict(tomllib.load(fh))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise RadioConfigError(f"{path}: {e}") from None
