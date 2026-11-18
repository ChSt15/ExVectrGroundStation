"""RC controller interface. Implemented in task T6 (stub: every method raises NotImplementedError).

Safety-relevant: this is the only uplink of the ground station (section 1.4 of the design)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from ..core.bus import Bus
from ..sources.base import Source
from .radio_config import RadioConfig

_T6 = "implemented in task T6"


class ControlDenied(Exception):
    """Another holder, no control source, or remote address not allowed."""


@dataclass
class Lease:
    id: str
    holder: str
    kind: Literal["connection", "timed"]
    expires: int | None


@dataclass
class RcStatus:
    mode: Literal["controlling", "mirroring", "idle", "conflict"]
    sending: bool
    target: str | None
    lease: Lease | None
    controls: dict[str, float | int]  # sent state (controlling) or mirrored handset state (mirroring)
    channels_us: list[int]  # full channel vector of that state
    origin: Literal["gs", "handset"] | None
    t: int | None


class RcController:
    def __init__(self, cfg: RadioConfig, sources: Sequence[Source], bus: Bus):
        pass

    def acquire(self, holder: str, kind: str, duration_s: float | None = None, remote_addr: str = "") -> Lease:
        raise NotImplementedError(_T6)

    def release(self, lease_id: str) -> None:
        raise NotImplementedError(_T6)

    def heartbeat(self, lease_id: str) -> None:
        raise NotImplementedError(_T6)

    def set(self, lease_id: str, values: Mapping[str, float | int]) -> RcStatus:
        raise NotImplementedError(_T6)

    def status(self) -> RcStatus:
        raise NotImplementedError(_T6)

    async def run(self) -> None:
        raise NotImplementedError(_T6)
