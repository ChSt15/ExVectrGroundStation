"""Synchronous publish/subscribe bus. Handlers must not block."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..packets import Packet

log = logging.getLogger(__name__)

PacketSink = Callable[[Packet], None]


class Bus:
    def __init__(self) -> None:
        self._subs: list[tuple[PacketSink, frozenset[str] | None]] = []
        self.handler_errors = 0

    def subscribe(self, handler: PacketSink, types: set[str] | None = None) -> Callable[[], None]:
        """Subscribe to all packets, or only to the named types. Returns the unsubscribe function."""
        entry = (handler, frozenset(types) if types is not None else None)
        self._subs.append(entry)

        def unsubscribe() -> None:
            if entry in self._subs:
                self._subs.remove(entry)

        return unsubscribe

    def publish(self, pkt: Packet) -> None:
        name = pkt.TYPE
        for handler, types in list(self._subs):  # copy: handlers may unsubscribe
            if types is not None and name not in types:
                continue
            try:
                handler(pkt)
            except Exception:  # one faulty handler must not stop the others
                self.handler_errors += 1
                log.exception("bus handler %r failed for %s", handler, name)
