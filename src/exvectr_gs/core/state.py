"""State store: latest packet per (type, source) with measured rate and staleness (section 3.7)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from ..packets import PacketRegistry
from ..packets.base import Packet

EWMA_ALPHA = 0.2

Freshness = Literal["fresh", "stale", "missing", "not supplied"]


@dataclass
class Entry:
    packet: Packet
    count: int
    interval_s: float | None  # EWMA of inter-arrival time for this (type, src)
    stale_after_s: float  # max(3 * interval_s, stale_min_s), or 3/nominal_rate if no interval yet


def freshness(entry: Entry | None, now: int) -> Literal["fresh", "stale", "missing"]:
    """Freshness against the view clock `now` (ns)."""
    if entry is None:
        return "missing"
    return "fresh" if (now - entry.packet.t) / 1e9 <= entry.stale_after_s else "stale"


def field_status(entry: Entry | None, field: str, now: int) -> Freshness:
    """fresh / stale / missing (never received) / not supplied (received, but the field is None)."""
    if entry is None:
        return "missing"
    if getattr(entry.packet, field, None) is None:
        return "not supplied"
    return freshness(entry, now)


class StateStore:
    def __init__(
        self,
        registry: PacketRegistry,
        source_priority: list[str],
        stale_min_s: float = 0.5,
        clock: Callable[[], int] | None = None,
    ):
        """`clock` is the view clock used when `get(now=None)`. Default: the newest packet time seen
        (right for replay). The live core passes its host clock."""
        self._registry = registry
        self._priority = list(source_priority)
        self._stale_min_s = stale_min_s
        self._clock = clock
        self._entries: dict[tuple[str, str], Entry] = {}
        self._latest_t = 0

    def _nominal(self, type_name: str) -> float | None:
        try:
            return self._registry.get(type_name).nominal_rate_hz
        except KeyError:
            return None

    def update(self, pkt: Packet) -> None:
        key = (pkt.TYPE if pkt.TYPE != "Unknown" else getattr(pkt, "type", "Unknown"), pkt.src)
        self._latest_t = max(self._latest_t, pkt.t)
        old = self._entries.get(key)
        interval = old.interval_s if old else None
        if old is not None:
            dt = (pkt.t - old.packet.t) / 1e9
            if dt > 0:
                interval = dt if interval is None else EWMA_ALPHA * dt + (1 - EWMA_ALPHA) * interval
        if interval is not None:
            stale_after = max(3 * interval, self._stale_min_s)
        else:
            nominal = self._nominal(key[0])
            stale_after = 3 / nominal if nominal else self._stale_min_s
        self._entries[key] = Entry(pkt, (old.count if old else 0) + 1, interval, stale_after)

    def _now(self, now: int | None) -> int:
        if now is not None:
            return now
        return self._clock() if self._clock else self._latest_t

    def get(self, type_name: str, src: str | None = None, now: int | None = None) -> Entry | None:
        """src=None: highest-priority source whose entry is fresh at `now`, else the newest entry."""
        if src is not None:
            return self._entries.get((type_name, src))
        cands = [e for (t, _), e in self._entries.items() if t == type_name]
        if not cands:
            return None
        now = self._now(now)

        def rank(e: Entry) -> tuple[int, str]:
            s = e.packet.src
            return (self._priority.index(s) if s in self._priority else len(self._priority), s)

        fresh = [e for e in cands if freshness(e, now) == "fresh"]
        if fresh:
            return min(fresh, key=rank)
        return max(cands, key=lambda e: e.packet.t)

    def snapshot(self) -> list[Entry]:
        """Latest entry per (type, src)."""
        return list(self._entries.values())

    def reset(self, entries: Iterable[Packet] = ()) -> None:
        """Clear, then load the given packets (used by replay seek)."""
        self._entries.clear()
        self._latest_t = 0
        for pkt in entries:
            self.update(pkt)
