"""Command engine: the ground station is the vehicle's control source (workspace `docs/gs-control-design.md`).

SAFETY-RELEVANT. One engine per core, independent of GUI clients and leases (D-GS5): it turns the Pocket's switch
state (handset `RcState` from the Router) or the virtual panel into the command state (`CMD_STATE`) and sends it at
25 Hz continuously, plus at once on every change, as the identical frame (same sequence number) on every connected
command link (a source with `rc_role = "control"`: the Router, and the vehicle USB on the bench). The vehicle
dedupes the one stream arriving on two links. The engine is the **only** sender of `CmdState` in a core.

The logic (pickup rule, Pocket loss, SE forcing, failsafe level, scheduling) is the pure `CommandCore`
(`command.py`); this class adds the bus, the clock, the sources, recording and warnings. It makes no control,
mission or safety decision: it copies switch positions, the vehicle decides.

One session id per core start (random, never 0). Requests to the vehicle carry it (`session`).
Replay and observer setups never send: the engine only uses the live bus and the connected live sources.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Callable, Sequence

from ..core.bus import Bus
from ..core.clock import Clock, MonotonicClock
from ..packets import Packet
from ..packets.defs.host import GsEvent, GsEventKind
from ..packets.defs.link import CmdState, RcOrigin
from ..sources.base import Source
from .command import CommandCore, Frame, decode_channels
from .controller import RcController

log = logging.getLogger(__name__)

RECORD_PERIOD_NS = 1_000_000_000  # a sent CmdState is recorded on change and at least this often
LOOP_PERIOD_S = 0.005
STALL_WARN_NS = 100_000_000  # log a warning when the engine itself did not run for this long (R1 watchdog)
ROUTER_NODE = 2


def new_session() -> int:
    """Random u32, never 0."""
    return secrets.randbelow(0xFFFFFFFF) + 1


class CommandEngine:
    def __init__(
        self,
        sources: Sequence[Source],
        bus: Bus,
        clock: Clock | None = None,
        virtual: RcController | None = None,
        session: int | None = None,
        notify: Callable[[str, str, str], None] | None = None,
    ):
        self._sources = list(sources)
        self._bus = bus
        self._clock = clock or MonotonicClock()
        self._virtual = virtual
        self.session = session if session else new_session()
        self.core = CommandCore()
        # (level, text, key): the Pocket-loss warning for clients (the API turns it into an event with a toast); the
        # GsEvent is always published as well
        self.notify = notify
        self.sent_count = 0  # frames handed to at least one link
        self.unsent_count = 0  # frames due while no command link was connected
        self.last_sent_t: int | None = None
        self.last_targets: list[str] = []
        self.max_interval_ns = 0
        self._last_tick: int | None = None
        self._last_recorded = 0
        self._last_rec_out = None
        self._vehicle_session16 = 0
        self._vehicle_ctrl_flags: int | None = None
        self._tasks: set[asyncio.Task] = set()
        self._started = False
        self._pocket_last_t: int | None = None
        if virtual is not None:
            virtual.on_acquire = self._panel_acquired
        bus.subscribe(self._on_packet, {"RcState", "VehicleStatus"})

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Begin the session: safe output, nothing picked up (`CommandCore.begin`)."""
        self.core.begin(self._clock.now())
        self._started = True

    def set_failsafe(self, on: bool) -> None:
        """GS FAILSAFE level in the command state (the GUI button comes in a later step). True latches
        failsafe = 1 with motors DISABLED and go NOT_GO until False clears it."""
        now = self._clock.now()
        self.core.set_failsafe(on, now)
        self._pump(now)

    def _panel_acquired(self) -> None:
        """A client took the virtual panel: it starts at what is being sent (no output change)."""
        if self._virtual is not None:
            self._virtual.preset(self.core.virtual_snapshot())
            self.core.virtual_acquired()

    # ------------------------------------------------------------ inputs

    def _on_packet(self, pkt: Packet) -> None:
        now = self._clock.now()
        if pkt.TYPE == "VehicleStatus":
            sim = getattr(pkt, "simulation", None)
            self.core.vehicle_mode_reported(None if sim is None else bool(sim))
            cs = getattr(pkt, "cmd_session", None)
            if cs is not None:
                self._vehicle_session16 = int(cs)
            self._vehicle_ctrl_flags = getattr(pkt, "ctrl_flags", None)
            return
        if getattr(pkt, "origin", None) != RcOrigin.handset or pkt.src == "gs":
            return
        node = getattr(pkt, "node", None)
        if node is not None and node != ROUTER_NODE:
            return  # only the Router's copy of the Pocket counts
        ch = getattr(pkt, "ch_us", None)
        valid = getattr(pkt, "source_ok", None) is not False and ch is not None
        self._pocket_last_t = now if valid else self._pocket_last_t
        self.core.pocket_frame(now, decode_channels(ch) if ch is not None else None, valid)
        self._after(now)

    # ------------------------------------------------------------ sending

    def _links(self) -> list[Source]:
        out = []
        for s in self._sources:
            try:
                ok = s.caps.rc_role == "control" and s.caps.can_send and s.status().state == "connected"
            except Exception:  # a source must never break the engine
                ok = False
            if ok:
                out.append(s)
        return out

    def _after(self, now: int) -> None:
        """Send what is due, then the warnings and the virtual panel preset it produced (the preset must reach the
        panel before the next poll)."""
        self._pump(now)
        self._drain(now)

    def _drain(self, now: int) -> None:
        core = self.core
        while core.warnings:
            w = core.warnings.pop(0)
            if w == "pocket_lost":
                self._event(
                    "WARNING: Pocket stream lost: the ground station keeps sending its last command state "
                    "(SE released); switches must be cycled after the Pocket returns",
                    "warn",
                    "pocket_lost",
                )
            elif w == "pocket_back":
                self._event(
                    "Pocket stream back: switches are re-evaluated, an unsafe position must be cycled first",
                    "info",
                    "pocket_back",
                )
        if core.preset_request is not None and self._virtual is not None:
            self._virtual.preset(core.preset_request)
        core.preset_request = None

    def _event(self, detail: str, level: str = "warn", key: str = "") -> None:
        self._bus.publish(GsEvent(t=self._clock.now(), src="gs", kind=GsEventKind.link_warning, detail=detail))
        log.warning("command engine: %s", detail)
        if self.notify is not None:
            try:
                self.notify(level, detail, key)
            except Exception:  # a client problem never touches the engine
                log.exception("command engine notify")

    def _pump(self, now: int) -> None:
        """Ask the core whether a frame is due; hand it to every command link."""
        frame = self.core.tick(now)
        if frame is not None:
            task = asyncio.ensure_future(self._send(frame, now))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def _packet(self, frame: Frame, now: int) -> CmdState:
        o = frame.out
        return CmdState(
            t=now, src="gs", session=self.session, go=o.go, profile=o.profile, vehicle_mode=o.vehicle_mode,
            motors=o.motors, reset_button=o.reset_button, failsafe=o.failsafe,
            pocket_live=frame.pocket_live, input_virtual=frame.input_virtual, seq=frame.seq,
        )  # fmt: skip

    async def _send(self, frame: Frame, now: int) -> None:
        pkt = self._packet(frame, now)
        accepted: list[str] = []
        for src in self._links():
            try:
                ok = await src.send(pkt)
            except Exception:
                log.exception("CMD_STATE send to %s failed", src.id)
                ok = False
            if ok:
                accepted.append(src.id)
        if not accepted:
            self.unsent_count += 1
            return
        self.sent_count += 1
        self.last_sent_t = now
        self.last_targets = accepted
        if frame.out != self._last_rec_out or now - self._last_recorded >= RECORD_PERIOD_NS:
            self._last_rec_out = frame.out
            self._last_recorded = now
            self._bus.publish(pkt)  # recorded as sent (never replayed out: replay has its own bus)

    async def step(self) -> None:
        """One iteration of the loop: virtual panel poll, Pocket-loss detection, the 25 Hz schedule. Yields once so
        the send tasks it created run (tests drive this with a fake clock)."""
        now = self._clock.now()
        if self._last_tick is not None:
            gap = now - self._last_tick
            self.max_interval_ns = max(self.max_interval_ns, gap)
            if gap > STALL_WARN_NS:
                log.warning("command engine ran %.0f ms late", gap / 1e6)
        self._last_tick = now
        if self._virtual is not None:
            self.core.virtual_input(now, self._virtual.poll())
        self._after(now)
        await asyncio.sleep(0)

    async def run(self) -> None:
        """The 5 ms loop."""
        if not self._started:
            self.start()
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                await self.step()
                await asyncio.sleep(LOOP_PERIOD_S)

    # ------------------------------------------------------------ status (display and API only)

    def status(self) -> dict:
        now = self._clock.now()
        core = self.core
        o = core.out
        h = core.hints()
        sess16 = self.session & 0xFFFF
        veh = self._vehicle_session16
        links = [s.id for s in self._links()]
        flags = self._vehicle_ctrl_flags or 0
        return {
            "session": self.session,
            "session16": sess16,
            "vehicle_session16": veh,
            "master": "unknown" if not veh else ("ok" if veh == sess16 else "not_master"),
            "multi_master": bool(flags & (1 << 11)),
            "gs_failsafe_req": bool(flags & (1 << 12)),
            "usb_fresh": bool(flags & (1 << 7)),
            "uart_fresh": bool(flags & (1 << 8)),
            "input": core.input,
            "pocket": core.pocket_state,
            "pocket_age_s": None if self._pocket_last_t is None else max(0.0, (now - self._pocket_last_t) / 1e9),
            "ready": core.ready(now),
            "links": links,
            "sending": bool(links) and core.ready(now),
            "sent": self.sent_count,
            "unsent": self.unsent_count,
            "last_sent_t": None if self.last_sent_t is None else self.last_sent_t / 1e9,
            "max_interval_ms": self.max_interval_ns / 1e6,
            "failsafe_latched": core.failsafe_latched,
            "out": {
                "go": o.go, "profile": o.profile, "vehicle_mode": o.vehicle_mode, "motors": o.motors,
                "reset_button": o.reset_button, "failsafe": o.failsafe,
            },
            "hints": {"sa": h.sa, "sd": h.sd, "se": h.se, "sc": h.sc},
            "picked": dict(core.picked),
        }  # fmt: skip
