"""Replayer interface. Implemented in task T4 (stub: every method raises NotImplementedError)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..core.bus import Bus
from ..core.history import History
from ..core.state import StateStore
from ..recording import LogInfo, LogLibrary

_T4 = "implemented in task T4"


@dataclass
class ReplayStatus:
    state: Literal["closed", "loading", "paused", "playing", "ended"]
    name: str | None
    t: int | None
    t_start: int | None
    t_end: int | None
    speed: float


class Replayer:
    def __init__(self, library: LogLibrary, bus: Bus, store: StateStore, history: History):
        pass

    async def open(self, name: str) -> LogInfo:
        raise NotImplementedError(_T4)

    def close(self) -> None:
        raise NotImplementedError(_T4)

    def play(self) -> None:
        raise NotImplementedError(_T4)

    def pause(self) -> None:
        raise NotImplementedError(_T4)

    def seek(self, t: int) -> None:
        raise NotImplementedError(_T4)

    def set_speed(self, speed: float) -> None:  # allowed: 0.25, 0.5, 1, 2, 4, 8
        raise NotImplementedError(_T4)

    def status(self) -> ReplayStatus:
        raise NotImplementedError(_T4)
