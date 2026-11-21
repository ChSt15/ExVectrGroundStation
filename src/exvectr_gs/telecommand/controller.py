"""Telecommand controller: TEMPORARY third uplink (user decision 2026-11-20). SAFETY-RELEVANT.

TODO(T9b): delete this package, `[telecommands]`, the API ops `telecommand.*`, `exgs tc`, the GUI panel
'Commands (temporary)', `Telecommand` in packets/defs/link.py and `_encode_telecommand` in the wire profile as soon
as the flight software's T9b replaces the legacy telecommand by RC_STATE mapping and REQUESTs.

The vehicle accepts the legacy 8-byte `Telecommand` on port 0x7F from node 0 (flight-software
docs/project/hardware-and-drivers.md section 10). Rules this module enforces:

- Only commands named in the configured whitelist are ever sent (name -> type, subsystem, parameter). No raw
  send, no free command code. A client may choose only the int16 parameter of an entry that has a
  `param_min`/`param_max` range, and only within that range.
- A telecommand is sent only because a control-host client called `send` with the deliberate-confirmation
  argument (`confirm` equal to the entry name): never automatically, never in replay, never to a source that is
  not connected, never queued. At most one new telecommand per 500 ms per client host (peer address).
- **Exactly one frame per confirmation. No retry, no repeat, no ACK.** The vehicle de-duplicates nothing, so a
  repeat would run a command twice: `SimulationMode` TOGGLES on the vehicle. The result is whatever the
  vehicle reports afterwards (VehicleStatus); this module never says a command worked and never assumes a state.
- Route: the first connected source in preference order that reaches the vehicle (vehicle USB first, else the
  Router link).
- Nothing is persisted. Each send is recorded as a `GsEvent` (info for the log, never state).
"""

from __future__ import annotations

import logging
import math
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..core.bus import Bus
from ..core.clock import Clock, MonotonicClock
from ..core.config import TelecommandEntry, TelecommandsSection
from ..core.hosts import is_control_host
from ..packets.defs.host import GsEvent, GsEventKind
from ..packets.defs.link import Telecommand, TelecommandType
from ..sources.base import Source, SourceKind

log = logging.getLogger(__name__)

MIN_INTERVAL_S = 0.5  # per client
# Documented exception (owner approval 2026-11-21): two consecutive commands of the SAME non-empty `rate_group` from the
# same client (the simulation wind group, applied as 5 frames by one hold) need only the vehicle's 100 ms tick spacing.
# A command of any other group, or the first of a group, still needs 500 ms after the previous send of that client.
GROUP_MIN_INTERVAL_S = 0.1
RECENT_KEPT = 20
VEHICLE_NODE = 1
CODES = ("control_denied", "not_whitelisted", "confirm_required", "rate_limited", "no_route", "invalid_value")


class TelecommandError(Exception):
    """A refused send. `code` is the API error code (docs/api.md)."""

    def __init__(self, code: str, message: str):
        assert code in CODES, code
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class TelecommandSent:
    """What was handed to a source, once. There is deliberately no 'state' or 'result' field."""

    n: int  # ground-station counter, display only
    name: str
    label: str
    type: str
    subsystem: int
    param_int: int
    param_float: float
    client: str
    source: str
    t: int  # ns, host clock, when it was handed to the source


class TelecommandController:
    def __init__(
        self,
        cfg: TelecommandsSection,
        sources: Sequence[Source],
        bus: Bus,
        clock: Clock | None = None,
        control_hosts: Sequence[str] | None = None,
    ):
        self.cfg = cfg
        self._sources = list(sources)
        self._bus = bus
        self._clock = clock or MonotonicClock()
        self._control_hosts = None if control_hosts is None else list(control_hosts)
        self._by_name = {e.name: e for e in cfg.commands}
        self._recent: deque[TelecommandSent] = deque(maxlen=RECENT_KEPT)
        self._last_by_client: dict[str, tuple[int, str]] = {}
        self._n = 0
        self.on_change: Callable[[], None] | None = None
        self.sent_count = 0

    # ------------------------------------------------------------ routing

    def _order(self) -> list[Source]:
        pref = self.cfg.source_preference
        if pref is not None:
            by_id = {s.id: s for s in self._sources}
            return [by_id[i] for i in pref if i in by_id]
        rank = {SourceKind.VEHICLE_USB: 0, SourceKind.LINK_MODULE: 1, SourceKind.FAKE: 2}
        return sorted(self._sources, key=lambda s: rank.get(s.kind, 9))

    def candidates(self) -> list[Source]:
        return [
            s
            for s in self._order()
            if s.caps.can_send and VEHICLE_NODE in s.caps.request_targets and s.status().state == "connected"
        ]

    def route(self) -> str | None:
        """Id of the source a send would use now (display only)."""
        c = self.candidates()
        return c[0].id if c else None

    # ------------------------------------------------------------ API

    def whitelist(self) -> list[dict]:
        route = self.route()
        return [
            {
                "name": e.name,
                "type": e.type,
                "label": e.label,
                "group": e.group,
                "subsystem": e.subsystem,
                "param_int": e.param_int,
                "param_min": e.param_min,
                "param_max": e.param_max,
                "param_float": e.param_float,
                "param_float_min": e.param_float_min,
                "param_float_max": e.param_float_max,
                "param_float_name": e.param_float_name,
                "rate_group": e.rate_group,
                "confirm": e.confirm,
                "note": e.note,
                "route": route,
            }
            for e in self.cfg.commands
        ]

    def status(self) -> dict:
        return {"recent": [self._json(s) for s in reversed(self._recent)]}

    @staticmethod
    def _json(s: TelecommandSent) -> dict:
        return {
            "n": s.n,
            "name": s.name,
            "label": s.label,
            "type": s.type,
            "subsystem": s.subsystem,
            "param_int": s.param_int,
            "param_float": s.param_float,
            "client": s.client,
            "source": s.source,
            "t": s.t / 1e9,
        }

    def _entry(self, name: object) -> TelecommandEntry:
        if not isinstance(name, str) or name not in self._by_name:
            raise TelecommandError("not_whitelisted", f"{name!r} is not in the telecommand whitelist")
        return self._by_name[name]

    async def send(
        self,
        name: str,
        *,
        client: str,
        remote_addr: str,
        confirm: str | None,
        param: int | None = None,
        param_float: float | None = None,
    ) -> TelecommandSent:
        """Send ONE whitelisted telecommand frame now. Raises TelecommandError. Never retries."""
        if self._control_hosts is not None and not is_control_host(remote_addr, self._control_hosts):
            raise TelecommandError("control_denied", "this client is not on a control host (view only)")
        entry = self._entry(name)
        if confirm != entry.name:
            raise TelecommandError("confirm_required", f"confirmation missing: repeat the command name ({entry.name})")
        value = entry.param_int
        if param is not None:
            if entry.param_min is None or entry.param_max is None:
                raise TelecommandError("invalid_value", f"{entry.name} has a fixed parameter, none may be given")
            if isinstance(param, bool) or not isinstance(param, int) or not entry.param_min <= param <= entry.param_max:
                raise TelecommandError(
                    "invalid_value",
                    f"{entry.name} parameter must be an integer within {entry.param_min}..{entry.param_max}",
                )
            value = param
        fvalue = entry.param_float
        if param_float is not None:
            lo, hi = entry.param_float_min, entry.param_float_max
            if lo is None or hi is None:
                raise TelecommandError("invalid_value", f"{entry.name} has a fixed float parameter, none may be given")
            if isinstance(param_float, bool) or not isinstance(param_float, (int, float)):
                raise TelecommandError("invalid_value", f"{entry.name} float parameter must be a number")
            if not math.isfinite(param_float) or not lo <= param_float <= hi:
                raise TelecommandError("invalid_value", f"{entry.name} float parameter must be within {lo:g}..{hi:g}")
            fvalue = float(param_float)
        now = self._clock.now()
        last = self._last_by_client.get(remote_addr)
        if last is not None:
            t_last, g_last = last
            same_group = bool(entry.rate_group) and entry.rate_group == g_last
            limit = GROUP_MIN_INTERVAL_S if same_group else MIN_INTERVAL_S
            if now - t_last < int(limit * 1e9):
                raise TelecommandError("rate_limited", f"at most one new telecommand per {limit * 1000:.0f} ms")
        cands = self.candidates()
        if not cands:
            raise TelecommandError("no_route", "no connected source reaches the vehicle; nothing was sent or queued")
        pkt = Telecommand(
            t=now,
            src="gs",
            tc_type=TelecommandType[entry.type],
            subsystem=entry.subsystem,
            param_int=value,
            param_float=fvalue,
        )
        used: str | None = None
        for s in cands:  # the first source that takes it; ONE frame, never to two sources
            try:
                ok = await s.send(pkt)
            except Exception:
                log.exception("telecommand via %s failed", s.id)
                ok = False
            if ok:
                used = s.id
                break
        if used is None:
            raise TelecommandError("no_route", "no connected source accepted the telecommand; nothing was sent")
        self.sent_count += 1
        self._n += 1
        self._last_by_client[remote_addr] = (now, entry.rate_group)
        rec = TelecommandSent(
            self._n, entry.name, entry.label, entry.type, entry.subsystem, value, fvalue, client, used, now
        )
        self._recent.append(rec)
        self._bus.publish(
            GsEvent(
                t=now,
                src="gs",
                kind=GsEventKind.telecommand_sent,
                detail=f"TELECOMMAND {entry.name} ({entry.type} sub {entry.subsystem} param {value} float {fvalue:g}) "
                f"via {used} "
                f"(client {client}), sent once",
            )
        )
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                log.exception("telecommand on_change")
        return rec
