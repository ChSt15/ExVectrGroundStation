"""SerialSource: USB serial from the vehicle (Teensy) or from the Router (design section 6.1).

Plain pyserial by default; `transport = "usb"` drives a CDC-ACM device in userspace with pyusb instead (for hosts
without the kernel cdc_acm driver, `usb_cdc.py`), `"auto"` picks per connection attempt. The wire format is not
known here: bytes go through the profile's Framer and `WireProfile.decode` only.
Threads: one reader and one writer per connection; everything that
touches the sink, the framer and the statistics runs on the event loop (the reader hands bytes over with
`call_soon_threadsafe`).

State machine: `searching` (poll for the device) -> `connected` (reader and writer threads running) ->
on read/write error or device gone: close, framer.reset(), `LinkStats connected=false`, `GsEvent
source_down`, back to `searching`. No backoff, so a Teensy reflash reconnects within one poll period.
`error` is shown while the device exists but cannot be opened (for example missing permissions).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import queue
import threading
from dataclasses import dataclass, field
from typing import Any

import serial
import serial.tools.list_ports

from ..core.clock import Clock, MonotonicClock
from ..core.config import ConfigError, SourceConfig
from ..core.rates import RateTracker, frame_path, nominal_for
from ..packets import REGISTRY, Direction, Packet
from ..packets.defs.host import ConsoleText as ConsoleTextPacket
from ..packets.defs.host import GsEvent, GsEventKind, LinkStats
from ..wire import ConsoleText, Frame, WireProfile, decode_frame
from .base import PacketSink, Source, SourceCaps, SourceKind, SourceStatus
from .transport import Transport, TransportError
from .usb_cdc import UsbCdcTransport, usb_available

log = logging.getLogger(__name__)

DEFAULT_BAUD = 115200
READ_TIMEOUT_S = 0.05
WRITE_TIMEOUT_S = 1.0
TX_QUEUE_LEN = 8
MATCH_KEYS = {"device", "vid", "pid", "serial", "serial_number"}  # serial_number is an alias of serial
TRANSPORTS = ("serial", "usb", "auto")


@dataclass
class _Conn:
    tp: Transport
    device: str
    stop: threading.Event = field(default_factory=threading.Event)
    lost: asyncio.Event = field(default_factory=asyncio.Event)
    tx: queue.Queue[bytes] = field(default_factory=lambda: queue.Queue(TX_QUEUE_LEN))
    threads: list[threading.Thread] = field(default_factory=list)

    @property
    def ser(self) -> serial.Serial:  # pyserial transport only
        return self.tp.ser  # type: ignore[attr-defined]


class SerialTransport:
    """pyserial port (the default transport). Reads whatever is waiting, blocks at most READ_TIMEOUT_S."""

    def __init__(self, device: str, baud: int, deassert: bool):
        self.ser = serial.Serial()
        self.ser.port = device
        self.ser.baudrate = baud
        self.ser.timeout = READ_TIMEOUT_S
        self.ser.write_timeout = WRITE_TIMEOUT_S
        self.ser.exclusive = True
        if deassert:  # set before open() so the port is never opened with them asserted by us
            self.ser.dtr = False
            self.ser.rts = False

    def open(self) -> None:
        self.ser.open()

    def read(self) -> bytes:
        return self.ser.read(max(1, self.ser.in_waiting))

    def write(self, data: bytes) -> None:
        self.ser.write(data)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.ser.close()


def match_device(match: dict[str, str]) -> str | None:
    """Resolve a `match` table to a device path now, or None if the device is not present.

    `device` is a path (a stable /dev/serial/by-id/... name is preferred). Otherwise USB `vid`, `pid`
    (hex, case-insensitive) and optionally `serial` are compared with `serial.tools.list_ports`.
    """
    dev = match.get("device")
    if dev:
        return dev if os.path.exists(dev) else None
    for p in serial.tools.list_ports.comports():
        if "vid" in match and (p.vid is None or f"{p.vid:04x}" != match["vid"].lower().removeprefix("0x")):
            continue
        if "pid" in match and (p.pid is None or f"{p.pid:04x}" != match["pid"].lower().removeprefix("0x")):
            continue
        if "serial" in match and p.serial_number != match["serial"]:
            continue
        return p.device
    return None


class SerialSource(Source):
    def __init__(self, cfg: SourceConfig, profile: WireProfile, clock: Clock | None = None):
        if cfg.kind not in (SourceKind.VEHICLE_USB.value, SourceKind.LINK_MODULE.value):
            raise ConfigError(f"source {cfg.id}: SerialSource cannot serve kind {cfg.kind!r}")
        bad = set(cfg.match) - MATCH_KEYS
        if bad or not cfg.match:
            raise ConfigError(
                f"source {cfg.id}: match needs one or more of {sorted(MATCH_KEYS)} (got {sorted(cfg.match)})"
            )
        opts = dict(cfg.options)
        self._transport = str(opts.pop("transport", "serial"))
        if self._transport not in TRANSPORTS:
            raise ConfigError(f"source {cfg.id}: transport must be one of {TRANSPORTS} (got {self._transport!r})")
        if self._transport == "usb" and ("device" in cfg.match or not {"vid", "pid"} <= set(cfg.match)):
            raise ConfigError(
                f"source {cfg.id}: transport 'usb' needs match = {{ vid = ..., pid = ... }} (device paths do not apply)"
            )
        self.id = cfg.id
        self.kind = SourceKind(cfg.kind)
        reach = (1,) if self.kind is SourceKind.VEHICLE_USB else (1, 2, 3)
        self.caps = SourceCaps(rc_role=cfg.rc_role, can_send=True, request_targets=reach)  # type: ignore[arg-type]
        self.enabled = cfg.enabled
        self.disabled_by: str | None = None if cfg.enabled else "config"
        self._match = {k: str(v) for k, v in cfg.match.items()}
        if "serial_number" in self._match:
            self._match["serial"] = self._match.pop("serial_number")
        try:
            if self._transport != "serial" and {"vid", "pid"} <= set(self._match):
                UsbCdcTransport(self._match)  # validates the hex ids at construction
        except ValueError as e:
            raise ConfigError(f"source {cfg.id}: {e}") from None
        self._baud = int(cfg.baud or DEFAULT_BAUD)
        # ESP32 bridges (CP210x/CH34x) reset the module when DTR/RTS toggle, so keep both low.
        # The Teensy needs DTR asserted (many sketches wait for it), so the vehicle keeps pyserial's default.
        # Not set: decided per connection (a native USB CDC device over the usb transport always gets DTR).
        self._deassert_opt = opts.get("deassert_dtr_rts")
        self._deassert = bool(
            self._deassert_opt if self._deassert_opt is not None else self.kind is SourceKind.LINK_MODULE
        )
        self._poll_s = float(opts.get("poll_s", 1.0))
        self._stats_period_s = float(opts.get("stats_period_s", 1.0))
        unknown = set(opts) - {"deassert_dtr_rts", "poll_s", "stats_period_s"}
        if unknown:
            raise ConfigError(f"source {cfg.id}: unknown option(s) {sorted(unknown)}")
        self._profile = profile
        self._framer = profile.make_framer()
        self._clock = clock or MonotonicClock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sink: PacketSink | None = None
        self._conn: _Conn | None = None
        self._state: str = "disabled"
        self._device: str | None = None
        self._since = self._clock.now()
        self._reconnects = 0
        self._ever_connected = False
        self._last_error: str | None = None
        self._tx_frames = 0
        self._decode_errors = 0
        self.rate_tracker: RateTracker | None = None  # set by CoreApp: per-frame data-rate statistics
        self._last_stats = (0, 0, 0)  # framer bytes, frames and tx frames at the previous LinkStats

    # ---- Source interface

    def status(self) -> SourceStatus:
        return SourceStatus(
            id=self.id,
            kind=self.kind,
            enabled=self.enabled,
            disabled_by=None if self.enabled else self.disabled_by,
            state=self._state,  # type: ignore[arg-type]
            device=self._device,
            since=self._since,
            reconnects=self._reconnects,
            last_error=self._last_error,
        )

    async def send(self, pkt: Packet) -> bool:
        """Queue one uplink packet. False if not connected, not allowed or not encodable.

        Only UP packet types go out (RcState, Request and the TEMPORARY Telecommand), RcState only through the
        source with
        rc_role = "control" (design 6.2): a mirror or none source never carries GS RC state, whatever the
        caller asks. A Request goes out whatever the rc_role, but only to a target this source can reach.
        """
        conn = self._conn
        if conn is None or conn.stop.is_set() or not self.caps.can_send:
            return False
        try:
            if REGISTRY.get(pkt.TYPE).direction is not Direction.UP:
                return False
        except KeyError:
            return False
        if pkt.TYPE == "RcState" and self.caps.rc_role != "control":
            return False
        if pkt.TYPE == "Telecommand" and 1 not in self.caps.request_targets:
            return False  # TEMPORARY legacy telecommand: only to the vehicle (node 1), which this source must reach
        if pkt.TYPE == "Request" and getattr(pkt, "target", None) not in self.caps.request_targets:
            return False  # the request controller routes by reach; a source never carries what it cannot reach
        frame = self._profile.encode(pkt)
        if frame is None:
            return False
        ok = self._enqueue(conn, self._framer.encode(frame), drop_oldest=True)
        if ok and self.rate_tracker is not None and self.kind.value == "link_module":
            self.rate_tracker.record_tx(self.id, pkt.TYPE, self._clock.now(), len(frame.payload))  # uplink estimate
        return ok

    async def send_heartbeat(self, uptime_s: float) -> bool:
        """Queue the ground station's heartbeat (any rc_role: it is presence information, not control).

        False if the source is down, the profile has no heartbeat, or the writer queue is full: a
        heartbeat is dropped silently and never displaces or delays a queued frame, never blocks.
        """
        conn = self._conn
        make = getattr(self._profile, "encode_heartbeat", None)
        if conn is None or conn.stop.is_set() or not self.caps.can_send or make is None:
            return False
        return self._enqueue(conn, self._framer.encode(make(uptime_s)), drop_oldest=False)

    def _enqueue(self, conn: _Conn, data: bytes, drop_oldest: bool) -> bool:
        while True:  # latest wins: when the port cannot keep up, the oldest queued frame is dropped
            try:
                conn.tx.put_nowait(data)
                break
            except queue.Full:
                if not drop_oldest:
                    return False
                with contextlib.suppress(queue.Empty):
                    conn.tx.get_nowait()
        self._tx_frames += 1
        return True

    async def run(self, sink: PacketSink) -> None:
        self._loop = asyncio.get_running_loop()
        self._sink = sink
        self._set_state("searching")
        stats_task = asyncio.create_task(self._stats_loop(), name=f"linkstats-{self.id}")
        try:
            while True:
                try:
                    await self._session()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # never raises: report and keep searching
                    log.exception("serial source %s: unexpected error", self.id)
                    self._last_error = f"{type(e).__name__}: {e}"
                    self._set_state("error")
                    await asyncio.sleep(self._poll_s)
        finally:
            stats_task.cancel()
            conn, self._conn = self._conn, None
            if conn is not None:
                await asyncio.shield(asyncio.to_thread(self._close, conn))
            self._set_state("disabled")

    # ---- connection handling

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self._since = self._clock.now()

    def _emit(self, pkt: Packet) -> None:
        try:
            assert self._sink is not None
            self._sink(pkt)
        except Exception:
            log.exception("serial source %s: sink raised", self.id)

    def _event(self, kind: GsEventKind, detail: str) -> None:
        self._emit(GsEvent(t=self._clock.now(), src="gs", kind=kind, detail=f"{self.id}: {detail}"))

    def _deassert_for(self, transport: str) -> bool:
        return False if transport == "usb" and self._deassert_opt is None else self._deassert

    def _pick_transport(self) -> str:
        """`serial` or `usb` for this connection attempt. auto: serial when the matching tty exists, else usb
        when the match has vid and pid and pyusb imports, else serial (which then keeps searching)."""
        if self._transport != "auto":
            return self._transport
        if match_device(self._match) is None and {"vid", "pid"} <= set(self._match) and usb_available():
            return "usb"
        return "serial"

    def _probe(self, transport: str) -> tuple[str | None, UsbCdcTransport | None]:  # thread
        if transport == "usb":
            tp = UsbCdcTransport(self._match)
            return tp.probe(), tp
        return match_device(self._match), None

    async def _session(self) -> None:
        try:
            transport = await asyncio.to_thread(self._pick_transport)
            device, usb_tp = await asyncio.to_thread(self._probe, transport)
        except (TransportError, OSError, ValueError) as e:
            self._device = None
            self._last_error = str(e)
            self._set_state("error")
            await asyncio.sleep(self._poll_s)
            return
        if device is None:
            self._device = None
            if self._state != "searching":
                self._set_state("searching")
            await asyncio.sleep(self._poll_s)
            return
        self._device = device
        fut = asyncio.ensure_future(asyncio.to_thread(self._open, device, usb_tp, transport))
        try:
            conn = await asyncio.shield(fut)
        except asyncio.CancelledError:
            # disabled (task cancelled) while the port was opening: close it as soon as the open returns,
            # so a disabled source never keeps its port
            fut.add_done_callback(lambda f: None if f.cancelled() or f.exception() else self._close(f.result()))
            raise
        except (serial.SerialException, OSError, ValueError) as e:  # TransportError is an OSError
            self._last_error = str(e)
            self._set_state("error")
            await asyncio.sleep(self._poll_s)
            return
        self._framer.reset()
        if self._ever_connected:
            self._reconnects += 1
        self._ever_connected = True
        self._conn = conn
        self._last_error = None
        self._set_state("connected")
        self._event(GsEventKind.source_up, f"connected {device}")
        self._start_threads(conn)
        await conn.lost.wait()
        self._conn = None
        await asyncio.to_thread(self._close, conn)
        self._framer.reset()
        self._set_state("searching")
        self._emit_stats(connected=False, period_s=None)
        self._event(GsEventKind.source_down, f"disconnected: {self._last_error or 'closed'}")

    def _open(self, device: str, tp: Transport | None = None, transport: str = "serial") -> _Conn:
        if tp is None:
            tp = SerialTransport(device, self._baud, self._deassert_for(transport))
        tp.open()
        return _Conn(tp=tp, device=device)

    def _start_threads(self, conn: _Conn) -> None:
        conn.threads = [
            threading.Thread(target=self._reader, args=(conn,), name=f"serial-rx-{self.id}", daemon=True),
            threading.Thread(target=self._writer, args=(conn,), name=f"serial-tx-{self.id}", daemon=True),
        ]
        for th in conn.threads:
            th.start()

    @staticmethod
    def _close(conn: _Conn) -> None:
        conn.stop.set()
        conn.tp.close()
        for th in conn.threads:
            if th is not threading.current_thread():
                th.join(timeout=2.0)

    def _call(self, fn: Any, *args: Any) -> None:
        loop = self._loop
        if loop is not None:
            with contextlib.suppress(RuntimeError):  # loop already closed
                loop.call_soon_threadsafe(fn, *args)

    def _lost(self, conn: _Conn, why: str) -> None:  # event loop thread
        if conn is self._conn and not conn.lost.is_set():
            self._last_error = why
            conn.lost.set()

    def _reader(self, conn: _Conn) -> None:  # thread
        tp = conn.tp
        try:
            while not conn.stop.is_set():
                data = tp.read()
                if data:
                    self._call(self._on_data, conn, data, self._clock.now())
        except (serial.SerialException, OSError, TypeError, ValueError) as e:
            if not conn.stop.is_set():
                self._call(self._lost, conn, f"read failed: {e}")

    def _writer(self, conn: _Conn) -> None:  # thread
        try:
            while not conn.stop.is_set():
                try:
                    data = conn.tx.get(timeout=READ_TIMEOUT_S)
                except queue.Empty:
                    continue
                conn.tp.write(data)
        except (serial.SerialException, OSError, TypeError, ValueError) as e:
            if not conn.stop.is_set():
                self._call(self._lost, conn, f"write failed: {e}")

    def _on_data(self, conn: _Conn, data: bytes, t: int) -> None:  # event loop thread
        if conn is not self._conn:
            return
        for item in self._framer.feed(data, t):
            if isinstance(item, Frame):
                self._observe(item)
                for pkt in decode_frame(self._profile, item, self.id):
                    if pkt.TYPE == "RawFrame":
                        self._decode_errors += 1
                    self._emit(pkt)
            elif isinstance(item, ConsoleText):
                self._emit(ConsoleTextPacket(t=item.t, src=self.id, text=item.text))

    def _observe(self, frame: Frame) -> None:
        """Count one received wire frame for the data-rate view (never affects decoding)."""
        describe = getattr(self._profile, "describe_frame", None)
        if self.rate_tracker is None or describe is None:
            return
        try:
            info = describe(frame)
            path = frame_path(self.kind.value, info.remote)
            self.rate_tracker.record(
                self.id, info.type, info.node, path, frame.t,
                len(frame.payload), frame.wire_len, nominal_for(path, info),
            )  # fmt: skip
        except Exception:
            log.exception("serial source %s: rate statistics failed", self.id)

    # ---- link statistics

    def _emit_stats(self, connected: bool, period_s: float | None) -> None:
        st = self._framer.stats
        last = self._last_stats
        if period_s:
            rx_b = (st.bytes - last[0]) / period_s
            rx_f = (st.frames - last[1]) / period_s
            tx_f = (self._tx_frames - last[2]) / period_s
        else:
            rx_b = rx_f = tx_f = 0.0
        self._last_stats = (st.bytes, st.frames, self._tx_frames)
        self._emit(
            LinkStats(
                t=self._clock.now(),
                src=self.id,
                connected=connected,
                rx_bytes_s=rx_b,
                rx_frames_s=rx_f,
                tx_frames_s=tx_f,
                crc_errors=st.crc_errors,
                decode_errors=self._decode_errors,
                reconnects=self._reconnects,
            )
        )

    async def _stats_loop(self) -> None:
        last_t = self._clock.now()
        while True:
            await asyncio.sleep(self._stats_period_s)
            now = self._clock.now()
            period = max((now - last_t) / 1e9, 1e-3)
            last_t = now
            self._emit_stats(connected=self._conn is not None, period_s=period)
