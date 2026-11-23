"""Request controller: the ground station's second uplink (user decision 2026-11-19, design 1.4, 10.1-9).

SAFETY-RELEVANT. It widens the uplink beyond RC state. Rules this module enforces:

- Only REQUESTs named in the configured whitelist (action AND target) are ever sent. No raw send, no free
  action number, nothing automatic: a request exists only because a control-host client called `send` with
  the deliberate-confirmation arguments. Replay never reaches this module and a source that is not
  connected is never used.
- The ground station does not interpret an action and makes no decision for the vehicle: whether a vehicle
  accepts it (disarmed and on the ground) is the vehicle's call. State shown to clients comes from
  REQUEST_ACKs only: `sending`, then `acked` with the status exactly as reported, or `timeout`. Nothing is
  assumed successful.
- Idempotent ids and retry (network-design 3.5): a non-zero u16 `req_id`, new per action, reused for every
  retry; one attempt per second, at most 5 attempts; retries stop at once on a matching ACK. One request in
  flight per (action, target). No queueing: with no suitable connected source the send is refused.
- Control hosts only (checked here and in the API), at most one new request per 500 ms per client host
  (keyed by the peer address, so reconnecting does not reset it).
- Nothing is persisted. Request and ACK events are recorded as `GsEvent`s (info for the log, never state).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from ..core.bus import Bus
from ..core.clock import Clock, MonotonicClock
from ..core.config import RequestEntry, RequestsSection
from ..core.hosts import is_control_host
from ..packets import Packet
from ..packets.defs.host import GsEvent, GsEventKind
from ..packets.defs.link import Request, RequestAction, RequestStatus
from ..sources.base import Source, SourceKind

log = logging.getLogger(__name__)

RETRY_PERIOD_S = 1.0
MAX_ATTEMPTS = 5
MIN_INTERVAL_S = 0.5  # per client
RECENT_KEPT = 20
TICK_S = 0.05
NODE_NAMES = {1: "vehicle", 2: "Router", 3: "ship radio node"}
CODES = ("control_denied", "not_whitelisted", "confirm_required", "rate_limited", "request_in_flight", "no_route")


class RequestError(Exception):
    """A refused send. `code` is the API error code (docs/api.md)."""

    def __init__(self, code: str, message: str):
        assert code in CODES, code
        super().__init__(message)
        self.code = code
        self.message = message


def status_name(status: int) -> str:
    try:
        return RequestStatus(int(status)).name
    except ValueError:
        return f"#{int(status)}"


@dataclass
class RequestState:
    req_id: int
    action: str
    target: int
    label: str
    group: str
    confirm: str
    client: str
    state: Literal["sending", "acked", "timeout", "aborted"]
    attempts: int
    t_first: int  # ns
    next_at: int  # ns of the next retry (sending only)
    source: str | None = None  # source of the latest attempt
    t_done: int | None = None
    status: int | None = None  # as reported in the ACK
    status_name: str | None = None
    detail: int | None = None
    abort_reason: str | None = None


class RequestController:
    def __init__(
        self,
        cfg: RequestsSection,
        sources: Sequence[Source],
        bus: Bus,
        clock: Clock | None = None,
        control_hosts: Sequence[str] | None = None,
        first_id: int | None = None,
    ):
        self.cfg = cfg
        self._sources = list(sources)
        self._bus = bus
        self._clock = clock or MonotonicClock()
        self._control_hosts = None if control_hosts is None else list(control_hosts)
        self._by_key = {(e.action, e.target): e for e in cfg.actions}
        # A random start: the vehicle remembers the last ids it executed, so a restarted ground station
        # counting from 1 again could have a fresh action mistaken for a repeat and never executed.
        self._next_id = (secrets.randbelow(0xFFFF) + 1) if first_id is None else first_id
        self._inflight: dict[tuple[str, int], RequestState] = {}
        self._recent: deque[RequestState] = deque(maxlen=RECENT_KEPT)
        self._last_by_client: dict[str, int]  # peer address -> ns of its last accepted request
        self._last_by_client = {}
        self.on_change: Callable[[], None] | None = None
        # The command engine's session id (workspace gs-control-design Q5): a REQUEST to the vehicle (target 1)
        # carries it, the vehicle accepts the request only from its latched session. None = no engine.
        self.session_fn: Callable[[], int] | None = None
        self.sent_count = 0  # attempts handed to a source
        bus.subscribe(self._on_packet, {"RequestAck"})

    # ------------------------------------------------------------ helpers

    def _event(self, kind: GsEventKind, detail: str) -> None:
        self._bus.publish(GsEvent(t=self._clock.now(), src="gs", kind=kind, detail=detail))

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                log.exception("request on_change")

    def _new_id(self) -> int:
        """Monotonic, wrapping, never 0, and never an id still in flight."""
        busy = {s.req_id for s in self._inflight.values()}
        while True:
            rid = self._next_id
            self._next_id = rid + 1 if rid < 0xFFFF else 1
            if rid != 0 and rid not in busy:
                return rid

    def _order(self) -> list[Source]:
        pref = self.cfg.source_preference
        if pref is not None:
            by_id = {s.id: s for s in self._sources}
            return [by_id[i] for i in pref if i in by_id]
        rank = {SourceKind.VEHICLE_USB: 0, SourceKind.LINK_MODULE: 1, SourceKind.FAKE: 2}
        return sorted(self._sources, key=lambda s: rank.get(s.kind, 9))  # stable: config order inside a kind

    def candidates(self, target: int) -> list[Source]:
        """Connected sources that can reach `target`, in the configured preference order."""
        return [
            s
            for s in self._order()
            if s.caps.can_send and target in s.caps.request_targets and s.status().state == "connected"
        ]

    def route(self, target: int) -> str | None:
        """Id of the source a send to `target` would use now (display only), or None."""
        c = self.candidates(target)
        return c[0].id if c else None

    def _entry(self, action: str, target: int | None) -> RequestEntry:
        if not isinstance(action, str):
            raise RequestError("not_whitelisted", "action must be a name")
        entries = [e for e in self.cfg.actions if e.action == action]
        if not entries:
            raise RequestError("not_whitelisted", f"{action} is not in the request whitelist")
        if target is None:
            if len(entries) > 1:
                raise RequestError(
                    "not_whitelisted", f"{action} needs a target, one of {sorted(e.target for e in entries)}"
                )
            return entries[0]
        e = self._by_key.get((action, target))
        if e is None:
            raise RequestError(
                "not_whitelisted",
                f"{action} is not whitelisted for target {target} (allowed: {sorted(x.target for x in entries)})",
            )
        return e

    def _packet(self, st: RequestState) -> Request:
        return Request(
            t=self._clock.now(),
            src="gs",
            req_id=st.req_id,
            action=RequestAction[st.action],
            target=st.target,
            arg_i=0,
            arg_f=0.0,
            session=(self.session_fn() if self.session_fn is not None else 0) if st.target == 1 else 0,
        )

    async def _transmit(self, st: RequestState) -> str | None:
        """Hand one attempt to the first candidate source that takes it. Returns its id, or None."""
        pkt = self._packet(st)
        for s in self.candidates(st.target):
            try:
                ok = await s.send(pkt)
            except Exception:
                log.exception("request send via %s failed", s.id)
                ok = False
            if ok:
                self.sent_count += 1
                return s.id
        return None

    # ------------------------------------------------------------ API

    def whitelist(self) -> list[dict]:
        return [
            {
                "action": e.action,
                "target": e.target,
                "target_name": NODE_NAMES.get(e.target, f"node {e.target}"),
                "label": e.label,
                "group": e.group,
                "confirm": e.confirm,
                "warning": e.warning,
                "route": self.route(e.target),
            }
            for e in self.cfg.actions
        ]

    def status(self) -> dict:
        return {
            "inflight": [self._json(s) for s in self._inflight.values()],
            "recent": [self._json(s) for s in reversed(self._recent)],
        }

    @staticmethod
    def _json(s: RequestState) -> dict:
        return {
            "req_id": s.req_id,
            "action": s.action,
            "target": s.target,
            "label": s.label,
            "group": s.group,
            "confirm": s.confirm,
            "client": s.client,
            "state": s.state,
            "attempts": s.attempts,
            "max_attempts": MAX_ATTEMPTS,
            "source": s.source,
            "t": s.t_first / 1e9,
            "t_done": None if s.t_done is None else s.t_done / 1e9,
            "status": s.status,
            "status_name": s.status_name,
            "detail": s.detail,
            "abort_reason": s.abort_reason,
        }

    async def send(
        self,
        action: str,
        target: int | None,
        *,
        client: str,
        remote_addr: str,
        confirm: str | None,
        understand_link_stops: bool = False,
    ) -> RequestState:
        """Send one whitelisted REQUEST (first attempt now, retries by `tick`). Raises RequestError."""
        if self._control_hosts is not None and not is_control_host(remote_addr, self._control_hosts):
            raise RequestError("control_denied", "this client is not on a control host (view only)")
        entry = self._entry(action, target)
        if confirm != entry.action:
            raise RequestError("confirm_required", f"confirmation missing: repeat the action name ({entry.action})")
        if entry.confirm == "danger" and not understand_link_stops:
            raise RequestError(
                "confirm_required",
                f"{entry.warning or entry.action + ' stops the radio link of the module'}: "
                "the explicit acknowledgement is required",
            )
        now = self._clock.now()
        last = self._last_by_client.get(remote_addr)
        if last is not None and now - last < int(MIN_INTERVAL_S * 1e9):
            raise RequestError("rate_limited", f"at most one new request per {MIN_INTERVAL_S * 1000:.0f} ms")
        key = (entry.action, entry.target)
        if key in self._inflight:
            cur = self._inflight[key]
            raise RequestError(
                "request_in_flight", f"{entry.action} for target {entry.target} is already in flight (id {cur.req_id})"
            )
        if not self.candidates(entry.target):
            raise RequestError(
                "no_route",
                f"no connected source can reach the {NODE_NAMES.get(entry.target, 'node')} (node {entry.target}); "
                "nothing was sent or queued",
            )
        st = RequestState(
            req_id=self._new_id(),
            action=entry.action,
            target=entry.target,
            label=entry.label,
            group=entry.group,
            confirm=entry.confirm,
            client=client,
            state="sending",
            attempts=1,
            t_first=now,
            next_at=now + int(RETRY_PERIOD_S * 1e9),
        )
        self._inflight[key] = st  # registered before sending: an ACK can never overtake the state
        src = await self._transmit(st)
        if src is None:
            self._inflight.pop(key, None)
            raise RequestError("no_route", "no connected source accepted the request; nothing was sent or queued")
        st.source = src
        self._last_by_client[remote_addr] = now
        self._event(
            GsEventKind.request_sent,
            f"REQUEST {st.action} target {st.target} id {st.req_id} via {src} (client {client})",
        )
        self._changed()
        return st

    # ------------------------------------------------------------ retry and ACK

    async def tick(self) -> None:
        """Retries every RETRY_PERIOD_S, a timeout one period after the last attempt. Run by `run`."""
        now = self._clock.now()
        for key, st in list(self._inflight.items()):
            if st.state != "sending" or now < st.next_at:
                continue
            if st.attempts >= MAX_ATTEMPTS:
                self._finish(key, st, "timeout")
                continue
            st.attempts += 1
            st.next_at = now + int(RETRY_PERIOD_S * 1e9)
            src = await self._transmit(st)
            if key not in self._inflight:  # an ACK arrived while sending
                continue
            if src is not None:
                st.source = src
            self._event(
                GsEventKind.request_retry,
                f"REQUEST {st.action} id {st.req_id} attempt {st.attempts}/{MAX_ATTEMPTS}"
                + (f" via {src}" if src else ": no connected source accepted it"),
            )
            self._changed()

    def abort_inflight(self, why: str) -> int:
        """Stop every request still waiting for its ACK (a source was disabled by the user): no further attempt is
        sent, through that source or another one, even if a source comes back. Returns how many were aborted."""
        n = 0
        for key, st in list(self._inflight.items()):
            if st.state == "sending":
                st.abort_reason = why
                self._finish(key, st, "aborted")
                n += 1
        return n

    def _finish(self, key: tuple[str, int], st: RequestState, state: Literal["acked", "timeout", "aborted"]) -> None:
        self._inflight.pop(key, None)
        st.state = state
        st.t_done = self._clock.now()
        self._recent.append(st)
        if state == "timeout":
            self._event(
                GsEventKind.request_timeout,
                f"REQUEST {st.action} id {st.req_id}: no REQUEST_ACK after {st.attempts} attempts",
            )
        elif state == "aborted":
            self._event(
                GsEventKind.request_timeout,
                f"REQUEST {st.action} id {st.req_id}: ABORTED after {st.attempts} attempt(s): {st.abort_reason}",
            )
        self._changed()

    def _on_packet(self, pkt: Packet) -> None:
        rid, status = getattr(pkt, "req_id", None), getattr(pkt, "status", None)
        if rid is None or status is None:
            return
        detail = getattr(pkt, "detail", None)
        for key, st in list(self._inflight.items()):
            if st.req_id == rid:  # stop at once: no more retries
                st.status, st.detail, st.status_name = int(status), detail, status_name(status)
                self._event(
                    GsEventKind.request_ack,
                    f"REQUEST_ACK {st.action} id {rid}: {st.status_name} detail {detail} "
                    f"after {st.attempts} attempt(s) (from {pkt.src})",
                )
                self._finish(key, st, "acked")
                return
        for st in self._recent:  # a later ACK of an already finished request (for example DONE after IN_PROGRESS)
            if st.req_id == rid and st.state == "acked" and st.status != int(status):
                st.status, st.detail, st.status_name = int(status), detail, status_name(status)
                self._event(GsEventKind.request_ack, f"REQUEST_ACK {st.action} id {rid}: {st.status_name} (update)")
                self._changed()
                return

    async def run(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                await self.tick()
            except Exception:
                log.exception("request tick")
