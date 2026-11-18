"""The `fake` wire profile: no bytes. FakeSource emits packets directly."""

from __future__ import annotations

from collections.abc import Iterable

from ..packets import Packet
from ..packets.defs.host import RawFrame
from .profile import ConsoleText, Frame, FramerStats


class NullFramer:
    def __init__(self) -> None:
        self.stats = FramerStats()

    def feed(self, data: bytes, t: int) -> Iterable[Frame | ConsoleText]:
        return ()

    def encode(self, frame: Frame) -> bytes:
        return b""

    def reset(self) -> None:
        pass


class FakeProfile:
    name = "fake"

    def make_framer(self) -> NullFramer:
        return NullFramer()

    def decode(self, frame: Frame, src: str) -> Packet:
        return RawFrame(
            t=frame.t,
            src=src,
            channel=frame.channel,
            len=len(frame.payload),
            hex=frame.payload.hex(),
            err="fake profile decodes nothing",
        )

    def encode(self, pkt: Packet) -> Frame | None:
        return None

    def supported_types(self) -> set[str]:
        return set()


def make_profile() -> FakeProfile:
    return FakeProfile()
