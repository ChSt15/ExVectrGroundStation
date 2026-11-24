"""Virtual panel (the GUI's virtual Pocket): leases and switch state. It sends nothing.

Since the GS-as-control-source cutover (workspace `docs/gs-control-design.md`, owner decision 2026-11-23) the only
sender of vehicle commands is the `CommandEngine` (`engine.py`), one per core. This module keeps what the engine
cannot know: who may move the virtual switches (leases, control hosts), the SD guard (lock) and the momentary SE.
Its state is the **virtual input** of the engine, used only while the Pocket is not live (the Pocket wins).

SAFETY-RELEVANT. Rules this module enforces:

- A lease only decides who may change the virtual switches. It never starts or stops the command stream (D-GS5):
  a closed browser tab or a lease end changes the virtual input (no input again: the engine holds its output and
  forces SE released), never the sending.
- A *connection* lease (GUI, `exgs rc session`) ends when its heartbeat is missing for 1 s. A *keepalive* lease
  (one-shot CLI commands) ends when it was not renewed for its idle timeout (default 5 s, max 9 s). A release is
  immediate. When a lease ends the panel returns to the configured defaults and every lock closes.
- Defaults are the configured safe positions: applied on every start, every acquire and every lease end, except
  that an acquire while the engine is sending presets the panel to the engine's output (`preset`), so taking the
  panel changes nothing that is sent.
- A control configured with `lock = true` (SD, motors armed) is held at its default until it is explicitly unlocked
  with a held lease. Only ARMING is guarded: setting it back to its default (disarm) is always immediate. An unlock
  has NO timeout: it stays open until the user locks it (`lock`: back to default, guard closed) or a reset event
  closes it (`relock_all`: failsafe request, SE system reset; also a lease end and a core start). No timer ever
  changes a control.
- A momentary control (SE) returns to its default automatically, so a killed client cannot leave it pressed.
- The panel never computes a command and never reads vehicle state.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from ..core.bus import Bus
from ..core.clock import Clock, MonotonicClock
from ..core.hosts import is_control_host
from ..packets import Packet
from ..packets.defs.host import GsEvent, GsEventKind
from ..packets.defs.link import RcOrigin
from .command import Switches
from .radio_config import RadioConfig

log = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_S = 1.0  # a connection lease needs a heartbeat at least this often
MOMENTARY_REFRESH_S = 0.3  # a momentary press without hold_s lapses this long after its last refresh
MAX_HOLD_S = 10.0
KEEPALIVE_DEFAULT_S = 5.0  # a keepalive lease ends this long after its last renewal ...
KEEPALIVE_MAX_S = 9.0  # ... which may be configured down, but never above this (single-digit seconds)
HANDSET_FRESH_S = 1.0  # a handset RcState younger than this means "the Pocket is showing" (display only)


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
    mode: Literal["virtual", "pocket", "idle"]  # virtual: a lease holds the panel. pocket: a handset is showing
    sending: bool  # filled in by the API from the command engine (this class sends nothing)
    target: str | None  # ids of the command links, comma separated (filled in by the API)
    lease: Lease | None
    controls: dict[str, float | int]  # the panel state (lease held), else the Pocket's positions, else defaults
    channels_us: list[int]  # full channel vector of that state
    origin: Literal["gs", "handset"] | None
    t: int | None
    locks: dict[str, dict] = field(default_factory=dict)  # lockable control id -> {locked, remaining_s, timeout_s}
    targets: list[str] = field(default_factory=list)  # ids of the command links (filled in by the API)
    pocket_active: bool = False  # a fresh handset RcState is arriving (display only)


class RcController:
    def __init__(
        self,
        cfg: RadioConfig,
        bus: Bus,
        clock: Clock | None = None,
        control_hosts: Sequence[str] | None = None,
    ):
        self.cfg = cfg
        self._bus = bus
        self._clock = clock or MonotonicClock()
        self._control_hosts = None if control_hosts is None else list(control_hosts)
        self._lease: Lease | None = None
        self._hb_deadline = 0
        self._state: dict[str, float | int] = cfg.default_state()  # always the defaults at start
        self._mom_until: dict[str, int] = {}
        self._unlocked: set[str] = set()  # lockable control ids whose guard is open; absent = locked
        # Set by the command engine: called right after a lease starts, so the panel is preset to the engine's output
        # before the client can set anything (an edit made before the engine took the panel would be wiped).
        self.on_acquire: Callable[[], None] | None = None
        self._handset_ch: list[int] | None = None
        self._handset_t: int | None = None
        bus.subscribe(self._on_packet, {"RcState"})

    # ------------------------------------------------------------ helpers

    def _on_packet(self, pkt: Packet) -> None:
        """Handset state from the Router (display only here; the command engine reads it separately)."""
        if getattr(pkt, "origin", None) == RcOrigin.handset and pkt.src != "gs" and pkt.ch_us is not None:  # type: ignore[attr-defined]
            self._handset_ch = list(pkt.ch_us)  # type: ignore[attr-defined]
            self._handset_t = self._clock.now()

    def _event(self, kind: GsEventKind, detail: str) -> None:
        self._bus.publish(GsEvent(t=self._clock.now(), src="gs", kind=kind, detail=detail))

    def _reset_state(self) -> None:
        """Every control back to its default and every lock closed."""
        self._state = self.cfg.default_state()
        self._mom_until.clear()
        self._unlocked.clear()

    def _end_lease(self, kind: GsEventKind, why: str) -> None:
        lease, self._lease = self._lease, None
        self._reset_state()
        if lease is not None:
            self._event(kind, f"{lease.kind} lease {lease.id} held by {lease.holder}: {why}")

    def _expire(self, now: int) -> None:
        """Lease and momentary expiry. Run before every operation and every tick."""
        lease = self._lease
        if lease is not None:
            if lease.kind == "keepalive" and lease.expires is not None and now >= lease.expires:
                self._end_lease(GsEventKind.lease_expired, "not renewed within its idle timeout")
            elif lease.kind == "connection" and now >= self._hb_deadline:
                self._end_lease(GsEventKind.lease_expired, "heartbeat lost")
        for cid, until in list(self._mom_until.items()):
            if now >= until:
                self._state[cid] = self.cfg.control(cid).default
                del self._mom_until[cid]

    def _relock(self, cid: str) -> None:
        """Lock a control and return it to its default (off)."""
        self._unlocked.discard(cid)
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
        self._reset_state()  # start from the safe defaults, every control locked (the engine presets the panel)
        expires = now + int(idle * 1e9) if idle is not None else None
        self._lease = Lease(id=secrets.token_hex(4), holder=holder, kind=kind, expires=expires, idle_s=idle)  # type: ignore[arg-type]
        self._hb_deadline = now + int(HEARTBEAT_TIMEOUT_S * 1e9)
        self._event(GsEventKind.lease_acquired, f"{kind} lease {self._lease.id} held by {holder}")
        if self.on_acquire is not None:
            self.on_acquire()
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
            if spec.lock and cid not in self._unlocked and v != spec.default:
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
        """Deliberate-action guard: arming is possible until locked again (no timeout)."""
        self._check_lease(lease_id)
        spec = self.cfg.control(control)
        if not spec.lock:
            raise ValueError(f"control {control} has no lock")
        self._unlocked.add(control)
        return self.status()

    def lock(self, lease_id: str, control: str) -> RcStatus:
        """Relock at once; the control returns to its default (off)."""
        self._check_lease(lease_id)
        if not self.cfg.control(control).lock:
            raise ValueError(f"control {control} has no lock")
        self._relock(control)
        return self.status()

    def relock_all(self) -> None:
        """Reset event (failsafe request, SE system reset): every guard closes and every locked control returns to its
        default (SD disarmed). Needs no lease; the engine follows the panel only through its pickup rules."""
        for c in self.cfg.controls:
            if c.lock and (c.id in self._unlocked or self._state.get(c.id, c.default) != c.default):
                self._relock(c.id)
                self._state[c.id] = c.default

    def _locks(self, now: int) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for c in self.cfg.controls:
            if c.lock:
                st = self._state.get(c.id, c.default)
                out[c.id] = {"locked": c.id not in self._unlocked, "armed": st != c.default}
        return out

    # ------------------------------------------------------------ status

    def _pocket_active(self, now: int) -> bool:
        if self._handset_ch is None or self._handset_t is None:
            return False
        return now - self._handset_t < HANDSET_FRESH_S * 1e9

    def status(self) -> RcStatus:
        now = self._clock.now()
        self._expire(now)
        pocket = self._pocket_active(now)
        if self._lease is None and pocket:
            assert self._handset_ch is not None
            return RcStatus(
                "pocket", False, None, None, self.cfg.state_from_channels(self._handset_ch),
                list(self._handset_ch), "handset", self._handset_t, self._locks(now), [], True,
            )  # fmt: skip
        held = self._lease is not None
        return RcStatus(
            "virtual" if held else "idle",
            False,
            None,
            self._lease,
            dict(self._state),
            self.cfg.channels_us(self._state),
            "gs" if held else None,
            None,
            self._locks(now),
            [],
            pocket,
        )

    # ------------------------------------------------------------ the engine's view (virtual input)

    def poll(self) -> Switches | None:
        """Expiry (lease, unlock, momentary) and the virtual panel's switch positions for the command engine.
        None while no lease is held: there is no virtual input."""
        self._expire(self._clock.now())
        if self._lease is None:
            return None
        st = self._state

        def pos(cid: str) -> int | None:
            try:
                return int(st[cid])
            except (KeyError, TypeError, ValueError):
                return None

        return Switches(sa=pos("SA"), sb=pos("SB"), sc=pos("SC"), sd=pos("SD"), se=pos("SE"))

    def preset(self, sw: Switches) -> None:
        """Set the panel to the engine's output when it becomes the input, so that taking it changes nothing that is
        sent. SD at 1 (motors armed) opens its guard, so the panel can show it."""
        if self._lease is None:
            return
        for cid, v in (("SA", sw.sa), ("SB", sw.sb), ("SC", sw.sc), ("SD", sw.sd), ("SE", sw.se)):
            try:
                spec = self.cfg.control(cid)
            except ValueError:
                continue
            if v is None:
                continue
            self._state[cid] = spec.check(v)
            if spec.lock and v != spec.default:
                self._unlocked.add(cid)
            self._mom_until.pop(cid, None)
