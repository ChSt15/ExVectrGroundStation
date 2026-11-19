"""RC controller: the only uplink of the ground station (design 1.4, 5.6, decision 10.1-1/-4/-8).

SAFETY-RELEVANT. Rules this module enforces:

- Master / slave (user decision 2026-11-19, 10.1-8). Slave (a fresh handset `RcState` is arriving, nobody
  holds a lease) and idle (nothing arriving) send nothing. Master is the explicit user choice: the control
  lease (`acquire`) is the master switch, allowed even while handset data arrives. The controller never
  switches to master by itself, and it does not reject or filter anything: the Pocket's TX module (and later
  the vehicle) reject ground-station RC state, the ground station adds no refusal of its own.
- Nothing is sent without an active control lease. In master the RC state goes to every connected source
  with `rc_role = "control"` (several are allowed) and never to a `mirror` or `none` source.
- A *connection* lease (GUI, `exgs rc session`) ends when its heartbeat is missing for 1 s. A *keepalive*
  lease (one-shot CLI commands) ends when it was not renewed for its idle timeout (default 5 s, max 9 s).
  A release is immediate. There is no long timed lease (user decision 2026-11-19). When a lease ends,
  sending stops entirely (silence is the vehicle's failsafe) and the state returns to the configured
  defaults. The last state is never kept sending.
- Defaults are the configured safe positions. They are applied on every start, on every acquire, on every
  lease end and whenever the control source drops. Nothing is persisted and no previous state is resumed.
- A control configured with `lock = true` (a deliberate-action guard, user decision 2026-11-19) is held at
  its default until it is explicitly unlocked with a held lease. It relocks (back to its default) after its
  `lock_timeout_s`, and every reset above relocks it. The controller does not know what the control means.
- The complete channel state is sent on a fixed timer (`send_rate_hz`) independent of clicks.
- A momentary control is held by the core: it returns to its default automatically (after `hold_s`, or
  300 ms after its last refresh), so a killed client cannot leave it pressed.
- The controller never computes a command, never reads vehicle state and never synthesises transitions.
  It only maps control positions to channel values from the radio config.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from ..core.bus import Bus
from ..core.clock import Clock, MonotonicClock
from ..core.hosts import is_control_host
from ..packets import Packet
from ..packets.defs.host import GsEvent, GsEventKind
from ..packets.defs.link import RcOrigin, RcState
from ..sources.base import Source
from .radio_config import RadioConfig

log = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_S = 1.0  # a connection lease needs a heartbeat at least this often
MOMENTARY_REFRESH_S = 0.3  # a momentary press without hold_s lapses this long after its last refresh
MAX_HOLD_S = 10.0
KEEPALIVE_DEFAULT_S = 5.0  # a keepalive lease ends this long after its last renewal ...
KEEPALIVE_MAX_S = 9.0  # ... which may be configured down, but never above this (single-digit seconds)
HANDSET_FRESH_S = 1.0  # a handset RcState younger than this means "mirroring"
RECORD_PERIOD_S = 1.0  # GS RcState is recorded on change and at least this often while sending
SEQ_MASK = 0xFF  # provisional wire layout carries seq as uint8; the sequence is only a liveness counter


class ControlDenied(Exception):
    """Another holder, no control source, or remote address not allowed."""


class ControlLocked(Exception):
    """A locked control was set to a non-default value (it must be unlocked first)."""


@dataclass
class Lease:
    id: str
    holder: str
    kind: Literal["connection", "keepalive"]
    expires: int | None  # ns (host clock): the keepalive deadline, moved by every renewal; None for connection
    idle_s: float | None = None  # keepalive idle timeout


@dataclass
class RcStatus:
    mode: Literal["master", "slave", "idle"]
    sending: bool
    target: str | None  # ids of the sources being sent to, comma separated
    lease: Lease | None
    controls: dict[str, float | int]  # sent state (controlling) or mirrored handset state (mirroring)
    channels_us: list[int]  # full channel vector of that state
    origin: Literal["gs", "handset"] | None
    t: int | None
    locks: dict[str, dict] = field(default_factory=dict)  # lockable control id -> {locked, remaining_s, timeout_s}
    targets: list[str] = field(default_factory=list)  # ids of the sources being sent to (master)
    pocket_active: bool = False  # a fresh handset RcState is arriving (display only; master shows a warning)


class RcController:
    def __init__(
        self,
        cfg: RadioConfig,
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
        self._lease: Lease | None = None
        self._hb_deadline = 0
        self._state: dict[str, float | int] = cfg.default_state()  # always the defaults at start
        self._mom_until: dict[str, int] = {}
        self._unlock_until: dict[str, int] = {}  # lockable control id -> ns deadline; absent = locked
        self._seq = 0
        self._last_sent_t: int | None = None
        self._last_sent_ch: list[int] | None = None
        self._last_recorded = 0
        self._handset_ch: list[int] | None = None
        self._handset_t: int | None = None
        self._ready_prev: set[str] = set()
        self.sent_count = 0
        self.skipped_count = 0
        bus.subscribe(self._on_packet, {"RcState"})

    # ------------------------------------------------------------ helpers

    def _ready_sources(self) -> list[Source]:
        """Every connected source that may be sent to (`rc_role = "control"`)."""
        return [s for s in self._sources if s.caps.rc_role == "control" and self._source_ready(s)]

    def _source_ready(self, src: Source | None) -> bool:
        return src is not None and src.caps.can_send and src.status().state == "connected"

    def _on_packet(self, pkt: Packet) -> None:
        """Handset state mirrored by a TX module (never our own GS state)."""
        if getattr(pkt, "origin", None) == RcOrigin.handset and pkt.src != "gs" and pkt.ch_us is not None:  # type: ignore[attr-defined]
            self._handset_ch = list(pkt.ch_us)  # type: ignore[attr-defined]
            self._handset_t = self._clock.now()

    def _event(self, kind: GsEventKind, detail: str) -> None:
        self._bus.publish(GsEvent(t=self._clock.now(), src="gs", kind=kind, detail=detail))

    def _reset_state(self) -> None:
        """Every control back to its default and every lock closed."""
        self._state = self.cfg.default_state()
        self._mom_until.clear()
        self._unlock_until.clear()

    def _end_lease(self, kind: GsEventKind, why: str) -> None:
        lease, self._lease = self._lease, None
        self._reset_state()
        self._last_sent_ch = None
        self._last_sent_t = None
        if lease is not None:
            self._event(kind, f"{lease.kind} lease {lease.id} held by {lease.holder}: {why}")

    def _expire(self, now: int) -> None:
        """Lease, unlock and momentary expiry. Run before every operation and every tick."""
        lease = self._lease
        if lease is not None:
            if lease.kind == "keepalive" and lease.expires is not None and now >= lease.expires:
                self._end_lease(GsEventKind.lease_expired, "not renewed within its idle timeout")
            elif lease.kind == "connection" and now >= self._hb_deadline:
                self._end_lease(GsEventKind.lease_expired, "heartbeat lost")
        for cid, until in list(self._unlock_until.items()):
            if now >= until:
                self._relock(cid)
        for cid, until in list(self._mom_until.items()):
            if now >= until:
                self._state[cid] = self.cfg.control(cid).default
                del self._mom_until[cid]

    def _relock(self, cid: str) -> None:
        """Lock a control and return it to its default (off)."""
        self._unlock_until.pop(cid, None)
        self._mom_until.pop(cid, None)
        self._state[cid] = self.cfg.control(cid).default

    def _check_lease(self, lease_id: str) -> Lease:
        """Validate the lease; any operation on a keepalive lease renews it (never an expired one)."""
        now = self._clock.now()
        self._expire(now)
        lease = self._lease
        if lease is None or lease.id != lease_id:
            raise ControlDenied("no such control lease (it may have expired)")
        if lease.kind == "keepalive" and lease.idle_s is not None:
            lease.expires = now + int(lease.idle_s * 1e9)
        return lease

    # ------------------------------------------------------------ lease API

    def acquire(self, holder: str, kind: str, idle_s: float | None = None, remote_addr: str = "") -> Lease:
        """`connection`: needs a heartbeat at least every 1 s. `keepalive`: renewed by every operation and
        by `heartbeat`, ends `idle_s` after the last one (default 5 s, max 9 s)."""
        now = self._clock.now()
        self._expire(now)
        if self._control_hosts is not None and not is_control_host(remote_addr, self._control_hosts):
            raise ControlDenied("this client is not on a control host (view only)")
        if kind not in ("connection", "keepalive"):
            raise ValueError("kind must be 'connection' or 'keepalive'")
        idle = None
        if kind == "keepalive":
            idle = KEEPALIVE_DEFAULT_S if idle_s is None else idle_s
            if not 0 < idle <= KEEPALIVE_MAX_S:
                raise ValueError(f"keepalive idle_s must be within (0, {KEEPALIVE_MAX_S:g}]")
        if self._lease is not None:
            raise ControlDenied(f"control is held by {self._lease.holder}")
        ready = self._ready_sources()
        if not ready:
            raise ControlDenied("no RC-capable source is connected")
        self._ready_prev = {s.id for s in ready}
        self._reset_state()  # always start from the safe defaults, every control locked
        self._last_sent_ch = None
        expires = now + int(idle * 1e9) if idle is not None else None
        self._lease = Lease(id=secrets.token_hex(4), holder=holder, kind=kind, expires=expires, idle_s=idle)  # type: ignore[arg-type]
        self._hb_deadline = now + int(HEARTBEAT_TIMEOUT_S * 1e9)
        self._event(GsEventKind.lease_acquired, f"{kind} lease {self._lease.id} held by {holder}")
        return self._lease

    def release(self, lease_id: str) -> None:
        """Stops sending at once and returns every control to its default."""
        self._expire(self._clock.now())
        if self._lease is None or self._lease.id != lease_id:
            return  # already gone: the intended end state
        self._end_lease(GsEventKind.lease_released, "released")

    def heartbeat(self, lease_id: str) -> None:
        """Connection lease: the 1 s heartbeat. Keepalive lease: a renewal (done by the lease check)."""
        lease = self._check_lease(lease_id)
        if lease.kind == "connection":
            self._hb_deadline = self._clock.now() + int(HEARTBEAT_TIMEOUT_S * 1e9)

    def set(self, lease_id: str, values: Mapping[str, float | int], hold_s: float | None = None) -> RcStatus:
        """Set controls by id. Validates everything first, so a bad request changes nothing.

        `hold_s` (momentary controls only): the core holds the press for that long and releases it.
        """
        self._check_lease(lease_id)
        checked: dict[str, float | int] = {}
        for cid, v in values.items():
            spec = self.cfg.control(cid)  # ValueError for an unknown control
            checked[cid] = spec.check(v)
            if hold_s is not None and spec.kind != "momentary":
                raise ValueError(f"hold_s applies to momentary controls only, not {cid}")
        if hold_s is not None and not 0 < hold_s <= MAX_HOLD_S:
            raise ValueError(f"hold_s must be within (0, {MAX_HOLD_S:g}]")
        for cid, v in checked.items():
            spec = self.cfg.control(cid)
            if spec.lock and cid not in self._unlock_until and v != spec.default:
                raise ControlLocked(f"control {cid} is locked: unlock it first")  # nothing of the request applied
        now = self._clock.now()
        for cid, v in checked.items():
            self._state[cid] = v
            if self.cfg.control(cid).kind == "momentary":
                if v == self.cfg.control(cid).default:
                    self._mom_until.pop(cid, None)
                else:
                    self._mom_until[cid] = now + int((hold_s if hold_s is not None else MOMENTARY_REFRESH_S) * 1e9)
        return self.status()

    def unlock(self, lease_id: str, control: str) -> RcStatus:
        """Deliberate-action guard: the control may be set to non-default values for `lock_timeout_s`."""
        self._check_lease(lease_id)
        spec = self.cfg.control(control)
        if not spec.lock:
            raise ValueError(f"control {control} has no lock")
        self._unlock_until[control] = self._clock.now() + int(spec.lock_timeout_s * 1e9)
        return self.status()

    def lock(self, lease_id: str, control: str) -> RcStatus:
        """Relock at once; the control returns to its default (off)."""
        self._check_lease(lease_id)
        if not self.cfg.control(control).lock:
            raise ValueError(f"control {control} has no lock")
        self._relock(control)
        return self.status()

    def _locks(self, now: int) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for c in self.cfg.controls:
            if c.lock:
                until = self._unlock_until.get(c.id)
                out[c.id] = {
                    "locked": until is None,
                    "remaining_s": None if until is None else max(0.0, (until - now) / 1e9),
                    "timeout_s": c.lock_timeout_s,
                }
        return out

    # ------------------------------------------------------------ status

    def _pocket_active(self, now: int) -> bool:
        if self._handset_ch is None or self._handset_t is None:
            return False
        return now - self._handset_t < HANDSET_FRESH_S * 1e9

    def status(self) -> RcStatus:
        now = self._clock.now()
        self._expire(now)
        ready = self._ready_sources()
        master = self._lease is not None and bool(ready)
        pocket = self._pocket_active(now)
        if not master and pocket:
            assert self._handset_ch is not None
            return RcStatus(
                "slave", False, None, self._lease, self.cfg.state_from_channels(self._handset_ch),
                list(self._handset_ch), "handset", self._handset_t, self._locks(now), [], True,
            )  # fmt: skip
        ids = [s.id for s in ready] if master else []
        return RcStatus(
            "master" if master else "idle",
            master,
            ", ".join(ids) if ids else None,
            self._lease,
            dict(self._state),
            self.cfg.channels_us(self._state),
            "gs" if self._lease is not None else None,
            self._last_sent_t,
            self._locks(now),
            ids,
            pocket,
        )

    # ------------------------------------------------------------ sender

    async def tick(self) -> bool:
        """One sender period. Returns True if a packet was accepted by the control source."""
        now = self._clock.now()
        self._expire(now)
        if self._lease is None:
            return False
        ready = self._ready_sources()
        ids = {x.id for x in ready}
        if not ready:
            self._reset_state()  # never resume an old state after the control source returns
            self._last_sent_ch = None
            self._ready_prev = set()
            self.skipped_count += 1
            return False
        if ids - self._ready_prev:
            # a source (re)appeared: it must see the safe state first, not whatever was set meanwhile
            self._reset_state()
            self._last_sent_ch = None
        self._ready_prev = ids
        ch = self.cfg.channels_us(self._state)
        self._seq = (self._seq + 1) & SEQ_MASK
        pkt = RcState(t=now, src="gs", origin=RcOrigin.gs, ch_us=ch, seq=self._seq)
        lease_id = self._lease.id
        accepted = 0
        for src in ready:
            try:
                ok = await src.send(pkt)
            except Exception:
                log.exception("RC send to %s failed", src.id)
                ok = False
            if ok:
                accepted += 1
            else:
                self.skipped_count += 1  # refused or not connected: skip this tick for this source
        if not accepted:
            return False
        self.sent_count += 1
        if self._lease is not None and self._lease.id == lease_id:
            self._last_sent_t = now
            if ch != self._last_sent_ch or now - self._last_recorded >= RECORD_PERIOD_S * 1e9:
                self._last_sent_ch = ch
                self._last_recorded = now
                self._bus.publish(pkt)  # record what was commanded (never replayed back out)
        return True

    async def run(self) -> None:
        """Sender timer at a fixed rate; lease and momentary expiry run on the same timer."""
        period = 1.0 / self.cfg.send_rate_hz
        nxt = time.monotonic()
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                await self.tick()
                nxt += period
                delay = nxt - time.monotonic()
                if delay < 0:
                    nxt = time.monotonic()  # fell behind: never burst to catch up
                    delay = 0
                await asyncio.sleep(delay)
