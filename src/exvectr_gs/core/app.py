"""Core wiring: sources -> LiveBus -> state store and history. Recorder, replayer, RC controller and API hang off it."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import platform
from collections import Counter
from pathlib import Path

from .. import __version__
from ..packets import REGISTRY
from ..packets.defs.host import GsEvent, GsEventKind
from ..packets.defs.vehicle import MissionState, VehicleState
from ..rc import RadioConfigError, RcController, load_radio_config
from ..recording import LogLibrary, Recorder
from ..replay import Replayer
from ..requests import RequestController
from ..sources import Source, build_source
from ..telecommand import TelecommandController
from ..wire import ProfileNotFound, WireProfile, load_profile
from .bus import Bus
from .clock import Clock, MonotonicClock
from .config import ConfigError, GsConfig
from .history import PLOT_MAX_HZ, PLOT_TYPES, PLOT_WINDOW_S, History
from .rates import SYNTHETIC_TYPES, RateTracker, packet_path
from .state import StateStore

log = logging.getLogger(__name__)

HEARTBEAT_PERIOD_S = 1.0


class CoreApp:
    def __init__(self, cfg: GsConfig, clock: Clock | None = None):
        self.cfg = cfg
        self.clock = clock or MonotonicClock()
        self.bus = Bus()
        self.store = StateStore(REGISTRY, cfg.core.source_priority, cfg.core.stale_min_s, self.clock.now)
        self.history = History(REGISTRY)
        # full-rate buffer of the tuning graphs (get_history buffer "plot"); the replay view reads the log instead
        self.plot_history = History(REGISTRY, PLOT_WINDOW_S, PLOT_MAX_HZ, PLOT_TYPES)
        # replay view (filled by the Replayer; the API serves them as view "replay")
        self.replay_bus = Bus()
        self.replay_store = StateStore(REGISTRY, [], cfg.core.stale_min_s)
        self.replay_history = History(REGISTRY)
        self.packet_counts: Counter[str] = Counter()  # per source id
        self._last_counts: Counter[str] = Counter()
        self.bus.subscribe(self._on_packet)
        enabled = [s for s in cfg.sources if s.enabled]
        profile = self._profile(any(s.kind != "fake" for s in enabled))
        # Sources with `enabled = false` are built too and start disabled (the GUI / `exgs sources enable` can
        # start them). Serial sources need the real wire profile: without it only the enabled ones exist.
        usable = [s for s in cfg.sources if s.enabled or s.kind == "fake" or profile.name != "fake"]
        self.sources: list[Source] = [build_source(s, profile) for s in usable]
        # Data-rate statistics (docs/api.md get_rates): serial sources report every wire frame themselves; sources
        # without frames (the fake source) are counted per packet in _on_packet.
        self.rates = RateTracker()
        self._packet_level = {s.id: s.kind.value for s in self.sources if not hasattr(s, "rate_tracker")}
        for s in self.sources:
            if hasattr(s, "rate_tracker"):
                s.rate_tracker = self.rates  # type: ignore[attr-defined]
        self._source_tasks: dict[str, asyncio.Task] = {}
        self._running = False
        # Recording starts only by explicit user action (decision 10.1-3): nothing is opened here.
        self.recorder = Recorder(
            Path(cfg.recorder.directory).expanduser(),
            REGISTRY,
            self.bus,
            {
                "gs": __version__,
                "host": platform.node() or "unknown",
                "profile": cfg.core.wire_profile,
                "config": hashlib.sha1(repr(cfg).encode()).hexdigest()[:12],
            },
            sources=[
                {"id": s.id, "kind": s.kind, "device": s.match.get("device", ""), "rc_role": s.rc_role} for s in enabled
            ],
            clock=self.clock.now,
            flush_interval_ms=cfg.recorder.flush_interval_ms,
            fsync_interval_ms=cfg.recorder.fsync_interval_ms,
        )
        # RC controller, the only uplink. An invalid or missing radio config means no RC controller at all
        # (`rc` is None, the API answers not_implemented); the rest of the core still runs. Nothing is sent
        # until a client takes a control lease, and every control starts at its configured safe default.
        self.rc: RcController | None = None
        self.rc_error: str | None = None
        try:
            radio_path = Path(cfg.radio.config).expanduser()
            if not radio_path.is_absolute() and not radio_path.exists():
                radio_path = Path(__file__).resolve().parents[3] / radio_path
            self.rc = RcController(
                load_radio_config(radio_path), self.sources, self.bus,
                clock=self.clock, control_hosts=cfg.server.control_hosts,
            )  # fmt: skip
        except RadioConfigError as e:
            self.rc_error = str(e)
            log.error("RC controller disabled, invalid radio config: %s", e)
        # Whitelisted REQUEST sender, the second uplink (user decision 2026-11-19, docs/requests.md). It sends
        # nothing by itself: a control-host client must call `request.send` with the confirmation arguments.
        self.requests = RequestController(
            cfg.requests, self.sources, self.bus, clock=self.clock, control_hosts=cfg.server.control_hosts
        )
        # TEMPORARY legacy telecommand sender (user decision 2026-11-20, docs/telecommands.md): third uplink, one
        # frame per confirmation, deleted with the flight software's T9b.
        self.telecommands = TelecommandController(
            cfg.telecommands, self.sources, self.bus, clock=self.clock, control_hosts=cfg.server.control_hosts
        )
        self.library = LogLibrary(Path(cfg.recorder.directory).expanduser())
        # Replay is a separate view (design 6.4): it publishes on replay_bus only, never on the live bus,
        # so replayed data is not recorded and live recording continues during replay.
        self.replayer = Replayer(self.library, self.replay_bus, self.replay_store, self.replay_history)

    def _profile(self, needed: bool) -> WireProfile:
        try:
            return load_profile(self.cfg.core.wire_profile)
        except (ProfileNotFound, ModuleNotFoundError) as e:  # the latter: a submodule is not checked out
            if needed:
                raise ConfigError(f"wire profile {self.cfg.core.wire_profile!r} not available: {e}") from None
            log.info(
                "wire profile %r not available yet, using 'fake' (no serial source enabled)",
                self.cfg.core.wire_profile,
            )
            return load_profile("fake")

    def _on_packet(self, pkt) -> None:
        self.store.update(pkt)
        self.history.update(pkt)
        self.plot_history.update(pkt)
        self.packet_counts[pkt.src] += 1
        kind = self._packet_level.get(pkt.src)
        if kind is not None and pkt.TYPE not in SYNTHETIC_TYPES:
            path = packet_path(kind, pkt.TYPE)
            try:
                nominal = REGISTRY.get(pkt.TYPE).nominal_rate_hz or 0.0
            except KeyError:
                nominal = 0.0
            self.rates.record(pkt.src, pkt.TYPE, None, path, pkt.t, nominal_hz=nominal)

    def summary_line(self, period_s: float = 1.0) -> str:
        """One line: per-source rate and connection state plus the headline vehicle values."""
        parts = []
        off = [s.id for s in self.sources if not getattr(s, "enabled", True)]
        for s in self.sources:
            if s.id in off:
                continue
            st = s.status()
            n = self.packet_counts[s.id]
            parts.append(f"{s.id}:{st.state} {(n - self._last_counts[s.id]) / period_s:.0f}pkt/s")
        self._last_counts = Counter(self.packet_counts)
        if off:
            parts.append(f"off:{','.join(off)}")
        vs = self.store.get(VehicleState.TYPE)
        ms = self.store.get(MissionState.TYPE)
        if vs is not None:
            p = vs.packet
            mode = getattr(p.mode, "name", f"#{p.mode}") if p.mode is not None else "?"
            parts.append(f"mode={mode} armed={p.armed}")
        if ms is not None and ms.packet.mission_phase is not None:
            parts.append(f"phase={getattr(ms.packet.mission_phase, 'name', ms.packet.mission_phase)}")
        pos = self.store.get("Position")
        if pos is not None and pos.packet.pos is not None:
            x, y, z = pos.packet.pos
            parts.append(f"pos=[{x:.2f},{y:.2f},{z:.2f}]")
        return " | ".join(parts)

    async def _heartbeat_loop(self) -> None:
        """Ground-station heartbeat at 1 Hz to every connected source that supports it (any rc_role).

        Independent of the RcController and of leases: nothing here reads RC state, and nothing in
        the RC path reads this task. Presence information only (network-design Revision 1)."""
        t0 = self.clock.now()
        while True:
            uptime = (self.clock.now() - t0) / 1e9
            for s in self.sources:
                try:
                    await s.send_heartbeat(uptime)
                except Exception:  # never let a source problem stop the others
                    log.exception("heartbeat to %s failed", s.id)
            await asyncio.sleep(HEARTBEAT_PERIOD_S)

    # ------------------------------------------------------------ run-time enable / disable (docs/api.md)

    def _start_source(self, s: Source) -> None:
        self._source_tasks[s.id] = asyncio.create_task(s.run(self.bus.publish), name=f"source-{s.id}")

    async def set_source_enabled(self, sid: str, enabled: bool) -> None:
        """Disable: a true disconnect. The source task is cancelled, which closes the port (nothing is received or
        sent through it: no RC state, requests, telecommands or heartbeats), in-flight REQUESTs are aborted so no
        retry goes out after a re-enable, and a GsEvent records it. Enable: the source starts again from scratch
        (a fresh connection with an empty send queue; the RC controller sends safe defaults first to a source
        that reappears). Not persisted: a core restart uses the config's `enabled`."""
        src = next((s for s in self.sources if s.id == sid), None)
        if src is None:
            raise KeyError(sid)
        running = sid in self._source_tasks and not self._source_tasks[sid].done()
        if not enabled:
            src.enabled = False  # type: ignore[attr-defined]
            src.disabled_by = "user"  # type: ignore[attr-defined]
            task = self._source_tasks.pop(sid, None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            aborted = self.requests.abort_inflight(f"source {sid} disabled by user")
            if running:
                self.bus.publish(
                    GsEvent(
                        t=self.clock.now(), src="gs", kind=GsEventKind.source_down,
                        detail=f"{sid}: DISABLED by user (port closed, nothing received or sent)"
                        + (f"; {aborted} request(s) aborted" if aborted else ""),
                    )
                )  # fmt: skip
            return
        src.enabled = True  # type: ignore[attr-defined]
        src.disabled_by = None  # type: ignore[attr-defined]
        if not running:
            self.bus.publish(
                GsEvent(t=self.clock.now(), src="gs", kind=GsEventKind.source_up, detail=f"{sid}: enabled by user")
            )
            if self._running:
                self._start_source(src)

    async def run(self, duration_s: float | None = None, summary=print, serve: bool = False) -> None:
        """Run all enabled sources until cancelled (or `duration_s`), printing a 1 Hz summary. With `serve`,
        also run the API server on cfg.server.bind:port."""
        self._running = True
        for s in self.sources:
            if getattr(s, "enabled", True):
                self._start_source(s)
        tasks = []
        tasks.append(asyncio.create_task(self._heartbeat_loop(), name="gs-heartbeat"))
        tasks.append(asyncio.create_task(self.requests.run(), name="requests"))
        api = None
        if self.rc is not None:
            tasks.append(asyncio.create_task(self.rc.run(), name="rc"))
        try:
            if serve:
                from ..api import ApiServer

                api = ApiServer(self, self.cfg.server.bind, self.cfg.server.port)
                await api.start()
                summary(f"api: listening on {self.cfg.server.bind}:{api.port}")
            end = None if duration_s is None else self.clock.now() + int(duration_s * 1e9)
            while end is None or self.clock.now() < end:
                await asyncio.sleep(1.0)
                summary(self.summary_line())
        finally:
            self._running = False
            if api is not None:
                await api.stop()
            tasks.extend(self._source_tasks.values())
            self._source_tasks.clear()
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await t
