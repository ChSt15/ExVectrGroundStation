"""Packets the GUI's mission event list is derived from (docs/gui.md "Mission events", docs/api.md `get_events`).

Display only: this is a data reduction, not an interpretation. Per (type, source) a packet is kept when one of the
listed fields differs from the last kept packet of that stream (or when `every_s` has passed since it, so the
mission clock still sees a regular sample), so a whole recording or the last hour live is a small list. Types
without a field list (console lines, GS events, notes) are kept unchanged.
"""

from __future__ import annotations

from collections import deque

from ..packets.base import Packet

# type -> (fields whose change keeps a packet or None = keep every packet, every_s or None)
EVENT_TYPES: dict[str, tuple[tuple[str, ...] | None, float | None]] = {
    # the mission clock needs a regular sample (it ends a mission after a 5 s gap): one per second at least
    "MissionState": (("mission_mode", "mission_phase", "mission_index", "waypoint"), 1.0),
    "VehicleStatus": (
        (
            "mode",
            "mission_mode",
            "mission_phase",
            "armed",
            "simulation",
            "motors_enabled",
            "failsafe",
            "failure_bits",
            "ctrl_flags",
            "go_refusal",
            "degraded_bits",
            "mission_profile",
            "ctrl_source",
            "gs_present",
            "ready_to_arm",
            "incompatible",
        ),
        None,
    ),  # fmt: skip
    "CmdState": (
        (
            "session",
            "go",
            "profile",
            "vehicle_mode",
            "motors",
            "reset_button",
            "failsafe",
            "pocket_live",
            "input_virtual",
        ),
        None,
    ),
    "CmdApplied": (("go", "profile", "vehicle_mode", "motors", "reset_button", "failsafe"), None),
    "ConsoleText": (None, None),
    "GsEvent": (None, None),
    "Note": (None, None),
}
EVENT_CAPACITY = 40000  # live buffer: the newest packets of the kept ones


class EventFilter:
    """Stateful `accept(pkt)` for the rule table above."""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], tuple[tuple, int]] = {}

    def accept(self, pkt: Packet) -> bool:
        rule = EVENT_TYPES.get(pkt.TYPE)
        if rule is None:
            return False
        fields, every_s = rule
        if fields is None:
            return True
        key = (pkt.TYPE, pkt.src)
        vals = tuple(getattr(pkt, name, None) for name in fields)
        prev = self._last.get(key)
        if prev is not None and prev[0] == vals and (every_s is None or pkt.t - prev[1] < every_s * 1e9):
            return False
        self._last[key] = (vals, pkt.t)
        return True


class EventHistory:
    """Live buffer of the kept packets (`CoreApp.event_history`)."""

    def __init__(self, capacity: int = EVENT_CAPACITY):
        self._filter = EventFilter()
        self._items: deque[Packet] = deque(maxlen=capacity)

    def update(self, pkt: Packet) -> None:
        if self._filter.accept(pkt):
            self._items.append(pkt)

    def packets(self) -> list[Packet]:
        return list(self._items)

    def clear(self) -> None:
        self._filter = EventFilter()
        self._items.clear()
