"""Wire layer protocols. Only wire profile plug-ins know bytes; everything above sees packets."""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..packets import Packet


@dataclass(frozen=True)
class Frame:
    channel: int  # transport port or packet id, whatever the profile uses to select a layout
    payload: bytes
    t: int  # host time of the last byte
    meta: Mapping[str, Any]  # profile-specific (source/destination node, sequence, ...)


@dataclass(frozen=True)
class ConsoleText:
    """Non-frame bytes on the stream (e.g. Teensy Serial.print), split into lines."""

    text: str
    t: int


@dataclass
class FramerStats:
    bytes: int = 0
    frames: int = 0
    crc_errors: int = 0
    resyncs: int = 0


class Framer(Protocol):
    stats: FramerStats

    def feed(self, data: bytes, t: int) -> Iterable[Frame | ConsoleText]: ...
    def encode(self, frame: Frame) -> bytes: ...
    def reset(self) -> None: ...  # called on (re)connect


class WireProfile(Protocol):
    name: str  # e.g. "provisional-1", later "exvectrnet-2"

    def make_framer(self) -> Framer: ...
    def decode(self, frame: Frame, src: str) -> Packet: ...  # unknown/invalid -> RawFrame packet, never raises
    def encode(self, pkt: Packet) -> Frame | None: ...  # None if this profile cannot send the type
    def supported_types(self) -> set[str]: ...


def decode_frame(profile: WireProfile, frame: Frame, src: str) -> list[Packet]:
    """All semantic packets of one frame. A profile may offer `decode_many(frame, src)` (one wire
    packet feeding several semantic types, or none for a suppressed duplicate); otherwise it is
    `[profile.decode(frame, src)]`."""
    many = getattr(profile, "decode_many", None)
    return list(many(frame, src)) if many is not None else [profile.decode(frame, src)]


class ProfileNotFound(LookupError):
    pass


def load_profile(name: str) -> WireProfile:
    """Look up `exvectr_gs.wire.<name>` (dashes become underscores). The module provides
    `make_profile() -> WireProfile`."""
    modname = f"{__package__}.{name.replace('-', '_')}"
    try:
        mod = importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name == modname:
            raise ProfileNotFound(f"unknown wire profile {name!r}") from None
        raise
    return mod.make_profile()
