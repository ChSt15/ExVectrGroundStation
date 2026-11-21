"""UsbCdcTransport: a CDC-ACM serial port driven in userspace with pyusb (no kernel cdc_acm needed).

For hosts such as the Steam Frame whose kernel has no `CONFIG_USB_ACM`: the Router (ESP32-S3 USB Serial/JTAG,
303a:1001) and the Teensy (16c0:0483) are visible on the USB bus but have no /dev/ttyACM*. Both are CDC-ACM:
a control interface (class 0x02) and a data interface (class 0x0A) with one bulk IN and one bulk OUT endpoint.
Interface and endpoint numbers are discovered from the active configuration, never hard-coded.

Threading: `read()` and `write()` are blocking calls that `SerialSource` runs in its reader and writer
threads (libusb is thread safe across endpoints). Each is guarded by its own lock so `close()` never disposes
the device while a transfer is in flight; `read()` uses a short timeout, so close waits at most that long.

Reads request exactly one max-packet-size (64 B full speed) per call. pyusb discards the data of a timed out
transfer, and a larger request that is only partly filled (the device sent full packets and no short one)
would lose it on timeout; a one-packet request always completes with the packet. A full-speed bulk pipe
carries at most about 1 kB/ms, so this costs nothing in throughput; the tests show it sustains the
Router's ~11 kB/s with ample margin.

pyusb is an optional dependency, imported lazily; a missing pyusb or libusb gives a clear error message.
"""

from __future__ import annotations

import errno
import logging
import threading
from typing import Any

from .transport import TransportError

log = logging.getLogger(__name__)

CDC_CONTROL_CLASS = 0x02
CDC_DATA_CLASS = 0x0A
SET_CONTROL_LINE_STATE = (0x21, 0x22)  # bmRequestType (host to device, class, interface), bRequest
DTR_RTS = 0x03
READ_TIMEOUT_MS = 50
WRITE_TIMEOUT_MS = 1000
CTRL_TIMEOUT_MS = 1000
_GONE = {errno.ENODEV, errno.EIO, errno.EPIPE, errno.ESHUTDOWN, errno.ENOENT, errno.EPROTO}
PERMISSION_HINT = (
    "permission denied opening the USB device: add the user to the group that owns /dev/bus/usb "
    "(on the Frame: group 'input'), or install a udev rule for this vendor/product"
)


def import_usb() -> tuple[Any, Any]:
    """Import pyusb lazily. Returns (usb.core, usb.util). Raises TransportError with the fix."""
    try:
        import usb.core
        import usb.util
    except ImportError as e:
        raise TransportError(
            "transport 'usb' needs pyusb: install it with  pip install 'pyusb>=1.2'  "
            "(or  pip install -e '.[usb]'), and libusb-1.0 on the host"
        ) from e
    return usb.core, usb.util


def usb_available() -> bool:
    try:
        import_usb()
    except TransportError:
        return False
    return True


def _hex(value: str, what: str) -> int:
    try:
        return int(str(value).lower().removeprefix("0x"), 16)
    except ValueError:
        raise ValueError(f'match {what} must be a hexadecimal string such as "303a" (got {value!r})') from None


class UsbCdcTransport:
    """One CDC-ACM device found by `match = { vid, pid, serial? }`."""

    def __init__(self, match: dict[str, str]):
        if "vid" not in match or "pid" not in match:
            raise ValueError("transport 'usb' needs match = { vid = ..., pid = ... } (device paths do not apply)")
        self.vid = _hex(match["vid"], "vid")
        self.pid = _hex(match["pid"], "pid")
        self.serial = match.get("serial")
        self._dev: Any = None
        self._core: Any = None
        self._util: Any = None
        self._ctrl_if: int | None = None
        self._data_if: int | None = None
        self._claimed: list[int] = []
        self._reattach: list[int] = []
        self._ep_in: Any = None
        self._ep_out: Any = None
        self._addr_in = self._addr_out = 0
        self._mps_in = 64
        self._mps_out = 64
        self._closed = False
        self._rd = threading.Lock()
        self._wr = threading.Lock()
        self.name = f"usb {self.vid:04x}:{self.pid:04x}"

    # ---- finding the device

    def probe(self) -> str | None:
        """The device is on the bus now: returns its display name, else None. Raises TransportError (no pyusb
        or libusb, no permission)."""
        core, util = import_usb()
        try:
            for dev in core.find(find_all=True, idVendor=self.vid, idProduct=self.pid):
                if self.serial is not None:
                    try:
                        if dev.serial_number != self.serial:
                            continue
                    except (ValueError, core.USBError, NotImplementedError):
                        continue
                self._core, self._util, self._dev = core, util, dev
                self.name = f"usb {self.vid:04x}:{self.pid:04x} bus {dev.bus} addr {dev.address}"
                return self.name
        except core.NoBackendError as e:
            raise TransportError("transport 'usb' needs libusb-1.0 on the host (pyusb found no backend)") from e
        except core.USBError as e:
            raise self._translate(e, "searching for the device") from e
        return None

    def _translate(self, e: Exception, doing: str) -> TransportError:
        code = getattr(e, "errno", None)
        if code in (errno.EACCES, errno.EPERM):
            return TransportError(f"{self.name}: {PERMISSION_HINT}")
        if code in _GONE:
            return TransportError(f"{self.name}: device gone while {doing}")
        return TransportError(f"{self.name}: USB error while {doing}: {e}")

    def _is_timeout(self, e: Exception) -> bool:
        return isinstance(e, getattr(self._core, "USBTimeoutError", ())) or getattr(e, "errno", None) == errno.ETIMEDOUT

    # ---- open / close

    def open(self) -> None:
        if self._dev is None:
            raise TransportError(f"{self.name}: open() before a successful probe()")
        try:
            self._open()
        except BaseException:
            self.close()
            raise

    def _open(self) -> None:
        dev, util, core = self._dev, self._util, self._core
        try:
            try:
                cfg = dev.get_active_configuration()
            except core.USBError as e:
                if getattr(e, "errno", None) in (errno.EACCES, errno.EPERM):
                    raise
                dev.set_configuration()
                cfg = dev.get_active_configuration()
            data_if = ctrl_if = None
            for intf in cfg:
                if getattr(intf, "bAlternateSetting", 0) != 0:
                    continue
                cls = intf.bInterfaceClass
                if cls == CDC_DATA_CLASS and data_if is None:
                    eps = [(ep.bEndpointAddress, ep.bmAttributes & 3, ep.wMaxPacketSize) for ep in intf]
                    ep_in = next((e for e in eps if e[0] & 0x80 and e[1] == 2), None)
                    ep_out = next((e for e in eps if not e[0] & 0x80 and e[1] == 2), None)
                    if ep_in and ep_out:
                        data_if = intf
                        self._mps_in, self._mps_out = max(ep_in[2], 8), max(ep_out[2], 8)
                        self._addr_in, self._addr_out = ep_in[0], ep_out[0]
                elif cls == CDC_CONTROL_CLASS and ctrl_if is None:
                    ctrl_if = intf
            if data_if is None:
                raise TransportError(f"{self.name}: no CDC data interface (class 0x0A with bulk IN and OUT) found")
            self._data_if = data_if.bInterfaceNumber
            self._ctrl_if = ctrl_if.bInterfaceNumber if ctrl_if is not None else None
            for n in (self._ctrl_if, self._data_if):
                if n is None:
                    continue
                if dev.is_kernel_driver_active(n):
                    log.warning("%s: detaching the kernel driver from interface %d", self.name, n)
                    dev.detach_kernel_driver(n)
                    self._reattach.append(n)
                try:
                    util.claim_interface(dev, n)
                    self._claimed.append(n)
                except core.USBError as e:
                    if n == self._data_if or getattr(e, "errno", None) in (errno.EACCES, errno.EPERM):
                        raise
                    log.warning("%s: control interface %d not claimed (%s); continuing", self.name, n, e)
            self._ep_in = self._find_endpoint(data_if, self._addr_in)
            self._ep_out = self._find_endpoint(data_if, self._addr_out)
            # The devices only transmit while the host is attached, which they detect by DTR.
            dev.ctrl_transfer(*SET_CONTROL_LINE_STATE, DTR_RTS, self._ctrl_if or 0, None, CTRL_TIMEOUT_MS)
        except core.USBError as e:
            raise self._translate(e, "opening the device") from e
        log.info(
            "%s: CDC data interface %s (IN 0x%02x, OUT 0x%02x), control interface %s",
            self.name, self._data_if, self._addr_in, self._addr_out, self._ctrl_if,
        )  # fmt: skip

    @staticmethod
    def _find_endpoint(intf: Any, address: int) -> Any:
        return next(ep for ep in intf if ep.bEndpointAddress == address)

    def close(self) -> None:
        """Deassert DTR, release the interfaces, dispose the device. Never raises."""
        with self._rd, self._wr:  # waits for an in-flight transfer (at most the read timeout)
            if self._closed:
                return
            self._closed = True
            dev, util = self._dev, self._util
            if dev is None:
                return
            if self._data_if is not None:
                try:
                    dev.ctrl_transfer(*SET_CONTROL_LINE_STATE, 0x00, self._ctrl_if or 0, None, CTRL_TIMEOUT_MS)
                except Exception as e:
                    log.debug("%s: DTR deassert failed: %s", self.name, e)
            for n in self._claimed:
                try:
                    util.release_interface(dev, n)
                except Exception as e:
                    log.debug("%s: release interface %d failed: %s", self.name, n, e)
            for n in self._reattach:
                try:
                    dev.attach_kernel_driver(n)
                except Exception as e:
                    log.debug("%s: re-attaching kernel driver on %d failed: %s", self.name, n, e)
            try:
                util.dispose_resources(dev)
            except Exception as e:
                log.debug("%s: dispose failed: %s", self.name, e)
            self._claimed, self._reattach = [], []
            self._ep_in = self._ep_out = None

    # ---- data

    def read(self) -> bytes:
        with self._rd:
            ep = self._ep_in
            if self._closed or ep is None:
                raise TransportError(f"{self.name}: closed")
            try:
                return bytes(ep.read(self._mps_in, READ_TIMEOUT_MS))
            except self._core.USBError as e:
                if self._is_timeout(e):
                    return b""
                raise self._translate(e, "reading") from e

    def write(self, data: bytes) -> None:
        """Bulk OUT in max-packet chunks, plus a zero-length packet when the data ends on a packet boundary."""
        with self._wr:
            ep = self._ep_out
            if self._closed or ep is None:
                raise TransportError(f"{self.name}: closed")
            n = self._mps_out
            try:
                for i in range(0, len(data), n):
                    ep.write(data[i : i + n], WRITE_TIMEOUT_MS)
                if data and len(data) % n == 0:
                    ep.write(b"", WRITE_TIMEOUT_MS)
            except self._core.USBError as e:
                raise self._translate(e, "writing") from e
