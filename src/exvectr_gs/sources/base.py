"""Abstract Source and its status types (design section 2.4)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from ..core.config import SourceConfig
from ..packets import Packet
from ..wire import WireProfile


class SourceKind(Enum):
    VEHICLE_USB = "vehicle_usb"
    LINK_MODULE = "link_module"
    FAKE = "fake"


@dataclass(frozen=True)
class SourceCaps:
    rc_role: Literal["control", "mirror", "none"]  # control: GS RC state goes here. mirror: handset state arrives.
    can_send: bool


@dataclass
class SourceStatus:
    id: str
    kind: SourceKind
    enabled: bool
    state: Literal["disabled", "searching", "connected", "error"]
    device: str | None
    since: int
    reconnects: int
    last_error: str | None


PacketSink = Callable[[Packet], None]


class Source(ABC):
    id: str
    kind: SourceKind
    caps: SourceCaps

    @abstractmethod
    async def run(self, sink: PacketSink) -> None:
        """Runs until cancelled, handles reconnects itself, never raises."""

    @abstractmethod
    async def send(self, pkt: Packet) -> bool:
        """False if not connected or not encodable."""

    @abstractmethod
    def status(self) -> SourceStatus: ...


def build_source(cfg: SourceConfig, profile: WireProfile) -> Source:
    kind = SourceKind(cfg.kind)
    if kind is SourceKind.FAKE:
        from .fake_source import FakeSource

        return FakeSource(cfg)
    raise NotImplementedError(f"source kind {cfg.kind!r} (serial source) is implemented in task T5")
