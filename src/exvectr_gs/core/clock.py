"""Host time: epoch-anchored but monotonic (design section 2.3)."""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> int: ...  # ns since the Unix epoch


class MonotonicClock:
    """E + (monotonic_ns() - M): absolute, monotonic, immune to NTP steps during a session."""

    def __init__(self) -> None:
        self._epoch = time.time_ns()
        self._mono = time.monotonic_ns()

    def now(self) -> int:
        return self._epoch + (time.monotonic_ns() - self._mono)


class FakeClock:
    """Manually advanced clock for tests."""

    def __init__(self, start_ns: int = 0) -> None:
        self._t = start_ns

    def now(self) -> int:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += int(seconds * 1e9)

    def set(self, t_ns: int) -> None:
        self._t = t_ns
