"""UsbCdcTransport and the `transport` option of SerialSource, against a fake `usb.core`. No real USB device."""

import asyncio
import contextlib
import errno
import sys
import threading
import time
import types

import pytest

from exvectr_gs.core.config import ConfigError, SourceConfig
from exvectr_gs.sources import SerialSource, build_source, usb_cdc
from exvectr_gs.sources import serial_source as ss
from exvectr_gs.sources.transport import TransportError
from exvectr_gs.sources.usb_cdc import UsbCdcTransport
from exvectr_gs.wire import load_profile

from ..support_pty import wait_for
from ..tests_tools import net

PROFILE = load_profile("exvectrnet-1")


class USBError(Exception):
    def __init__(self, strerror="usb error", errno=None):
        super().__init__(strerror)
        self.errno = errno


class USBTimeoutError(USBError):
    pass


class NoBackendError(ValueError):
    pass


class FakeEp:
    def __init__(self, dev, address, mps=64, attrs=2):
        self.dev, self.bEndpointAddress, self.wMaxPacketSize, self.bmAttributes = dev, address, mps, attrs

    def read(self, size, timeout):
        return self.dev.bulk_in(size, timeout)

    def write(self, data, timeout):
        return self.dev.bulk_out(bytes(data), timeout)


class FakeIntf(list):
    def __init__(self, num, cls, eps=(), alt=0):
        super().__init__(eps)
        self.bInterfaceNumber, self.bInterfaceClass, self.bAlternateSetting = num, cls, alt


class FakeDevice:
    """Router-like layout by default (control 0 / data 1 / vendor 2), or Teensy-like with other numbers."""

    def __init__(self, vid=0x303A, pid=0x1001, serial="SN1", layout="router", mps=64):
        self.idVendor, self.idProduct, self.serial_number, self.bus, self.address = vid, pid, serial, 1, 5
        if layout == "router":
            self.cfg = [
                FakeIntf(0, 0x02, [FakeEp(self, 0x82, 64, 3)]),
                FakeIntf(1, 0x0A, [FakeEp(self, 0x81, mps), FakeEp(self, 0x01, mps)]),
                FakeIntf(2, 0xFF, [FakeEp(self, 0x83), FakeEp(self, 0x03)]),
            ]
        else:  # odd numbers and endpoint addresses, an IAD-style order with data first
            self.cfg = [
                FakeIntf(3, 0x0A, [FakeEp(self, 0x04, mps), FakeEp(self, 0x85, mps)]),
                FakeIntf(2, 0x02, [FakeEp(self, 0x81, 16, 3)]),
            ]
        self.inq = []  # scripted chunks (bytes) or exceptions
        self.out = []
        self.ctrl = []
        self.claimed = []
        self.released = []
        self.disposed = False
        self.kernel_active = set()
        self.detached = []
        self.attached = []
        self.fail_claim = None
        self.configured = True
        self.read_sizes = []
        self.lock = threading.Lock()

    def get_active_configuration(self):
        if not self.configured:
            raise USBError("unconfigured", errno.ENODEV)
        return self.cfg

    def set_configuration(self):
        self.configured = True

    def is_kernel_driver_active(self, n):
        return n in self.kernel_active

    def detach_kernel_driver(self, n):
        self.detached.append(n)
        self.kernel_active.discard(n)

    def attach_kernel_driver(self, n):
        self.attached.append(n)

    def ctrl_transfer(self, rt, req, value, index, data, timeout=None):
        self.ctrl.append((rt, req, value, index, data))
        return 0

    def bulk_in(self, size, timeout):
        self.read_sizes.append(size)
        with self.lock:
            item = self.inq.pop(0) if self.inq else None
        if item is None:
            time.sleep(min(timeout, 5) / 1000)
            raise USBTimeoutError("timed out", errno.ETIMEDOUT)
        if isinstance(item, Exception):
            raise item
        return item[:size]

    def bulk_out(self, data, timeout):
        if isinstance(getattr(self, "out_error", None), Exception):
            raise self.out_error
        self.out.append(data)
        return len(data)

    def feed(self, data: bytes, mps=64):
        with self.lock:
            self.inq.extend(data[i : i + mps] for i in range(0, len(data), mps))


class FakeUsb:
    """Stands in for the `usb.core` / `usb.util` pair that import_usb() returns."""

    def __init__(self, *devices):
        self.devices = list(devices)
        self.find_error = None
        self.core = types.SimpleNamespace(
            USBError=USBError, USBTimeoutError=USBTimeoutError, NoBackendError=NoBackendError, find=self._find
        )
        self.util = types.SimpleNamespace(
            claim_interface=self._claim, release_interface=self._release, dispose_resources=self._dispose
        )

    def _find(self, find_all=False, idVendor=None, idProduct=None):
        if self.find_error:
            raise self.find_error
        return [d for d in self.devices if d.idVendor == idVendor and d.idProduct == idProduct]

    def _claim(self, dev, n):
        if dev.fail_claim:
            raise dev.fail_claim
        dev.claimed.append(n)

    def _release(self, dev, n):
        dev.released.append(n)

    def _dispose(self, dev):
        dev.disposed = True


@pytest.fixture
def fake_usb(monkeypatch):
    f = FakeUsb(FakeDevice())
    monkeypatch.setattr(usb_cdc, "import_usb", lambda: (f.core, f.util))
    return f


def opened(match=None, **kw):
    tp = UsbCdcTransport(match or {"vid": "303a", "pid": "1001"})
    assert tp.probe()
    tp.open()
    return tp


def test_match_vid_pid_serial_and_miss(fake_usb):
    other = FakeDevice(serial="SN2")
    fake_usb.devices.append(other)
    tp = UsbCdcTransport({"vid": "0x303A", "pid": "1001"})
    assert tp.probe() == "usb 303a:1001 bus 1 addr 5"
    tp = UsbCdcTransport({"vid": "303a", "pid": "1001", "serial": "SN2"})
    tp.probe()
    assert tp._dev is other
    assert UsbCdcTransport({"vid": "303a", "pid": "1001", "serial": "none"}).probe() is None
    assert UsbCdcTransport({"vid": "16c0", "pid": "0483"}).probe() is None
    with pytest.raises(ValueError, match="vid and pid|vid = "):
        UsbCdcTransport({"device": "/dev/ttyACM0"})
    with pytest.raises(ValueError, match="hexadecimal"):
        UsbCdcTransport({"vid": "zz", "pid": "1"})


def test_interface_discovery_router_and_teensy_layouts(fake_usb):
    dev = fake_usb.devices[0]
    tp = opened()
    assert (tp._ctrl_if, tp._data_if, tp._addr_in, tp._addr_out) == (0, 1, 0x81, 0x01)
    assert dev.claimed == [0, 1]  # vendor interface 2 (JTAG) never touched
    assert dev.ctrl == [(0x21, 0x22, 0x03, 0, None)]  # SET_CONTROL_LINE_STATE, DTR and RTS, control interface
    tp.close()

    teensy = FakeDevice(vid=0x16C0, pid=0x0483, layout="teensy")
    fake_usb.devices.append(teensy)
    tp = opened({"vid": "16c0", "pid": "0483"})
    assert (tp._ctrl_if, tp._data_if, tp._addr_in, tp._addr_out) == (2, 3, 0x85, 0x04)
    assert teensy.ctrl == [(0x21, 0x22, 0x03, 2, None)]
    assert teensy.claimed == [2, 3]
    tp.close()


def test_no_cdc_data_interface(fake_usb):
    fake_usb.devices[0].cfg = [FakeIntf(0, 0xFF, [])]
    tp = UsbCdcTransport({"vid": "303a", "pid": "1001"})
    tp.probe()
    with pytest.raises(TransportError, match="no CDC data interface"):
        tp.open()
    assert fake_usb.devices[0].disposed


def test_unconfigured_device_gets_configuration(fake_usb):
    fake_usb.devices[0].configured = False
    opened().close()


def test_kernel_driver_detached_only_when_bound(fake_usb, caplog):
    dev = fake_usb.devices[0]
    opened().close()
    assert dev.detached == []
    dev.kernel_active = {1}
    caplog.set_level("WARNING")
    tp = opened()
    assert dev.detached == [1] and "detaching the kernel driver from interface 1" in caplog.text
    tp.close()
    assert dev.attached == [1]


def test_close_deasserts_dtr_releases_and_disposes(fake_usb):
    dev = fake_usb.devices[0]
    tp = opened()
    tp.close()
    tp.close()  # idempotent
    assert dev.ctrl[-1] == (0x21, 0x22, 0x00, 0, None) and len(dev.ctrl) == 2
    assert dev.released == [0, 1] and dev.disposed
    with pytest.raises(TransportError, match="closed"):
        tp.read()
    with pytest.raises(TransportError, match="closed"):
        tp.write(b"x")


def test_read_one_packet_per_call_timeout_is_empty(fake_usb):
    dev = fake_usb.devices[0]
    tp = opened()
    dev.feed(b"abc")
    assert tp.read() == b"abc"
    assert tp.read() == b""  # timeout, not an error
    assert set(dev.read_sizes) == {64}
    tp.close()


def test_read_errors_become_transport_errors(fake_usb):
    dev = fake_usb.devices[0]
    tp = opened()
    for code in (errno.ENODEV, errno.EIO, errno.EPIPE):
        dev.inq.append(USBError("x", code))
        with pytest.raises(TransportError, match="gone while reading"):
            tp.read()
    dev.inq.append(USBError("overflow", errno.EOVERFLOW))
    with pytest.raises(TransportError, match="USB error while reading"):
        tp.read()
    tp.close()


def test_write_chunks_and_zero_length_packet(fake_usb):
    dev = fake_usb.devices[0]
    tp = opened()
    tp.write(b"a" * 10)
    assert dev.out == [b"a" * 10]
    dev.out.clear()
    tp.write(b"b" * 130)
    assert [len(c) for c in dev.out] == [64, 64, 2]
    dev.out.clear()
    tp.write(b"c" * 128)  # ends on a packet boundary: ZLP terminates the transfer
    assert [len(c) for c in dev.out] == [64, 64, 0]
    dev.out_error = USBTimeoutError("timed out", errno.ETIMEDOUT)
    with pytest.raises(TransportError, match="writing"):
        tp.write(b"x")
    tp.close()


def test_permission_denied_message(fake_usb):
    dev = fake_usb.devices[0]
    dev.fail_claim = USBError("Access denied (insufficient permissions)", errno.EACCES)
    tp = UsbCdcTransport({"vid": "303a", "pid": "1001"})
    tp.probe()
    with pytest.raises(TransportError) as ei:
        tp.open()
    msg = str(ei.value)
    assert "permission denied" in msg and "group that owns /dev/bus/usb" in msg and "udev rule" in msg
    fake_usb.find_error = USBError("denied", errno.EACCES)
    with pytest.raises(TransportError, match="permission denied"):
        UsbCdcTransport({"vid": "303a", "pid": "1001"}).probe()


def test_missing_libusb_backend(fake_usb):
    fake_usb.find_error = NoBackendError("No backend available")
    with pytest.raises(TransportError, match="libusb-1.0"):
        UsbCdcTransport({"vid": "303a", "pid": "1001"}).probe()


def test_missing_pyusb(monkeypatch):
    monkeypatch.setitem(sys.modules, "usb", None)  # `import usb.core` raises ImportError
    monkeypatch.setitem(sys.modules, "usb.core", None)
    monkeypatch.setitem(sys.modules, "usb.util", None)
    with pytest.raises(TransportError, match=r"needs pyusb.*pip install"):
        usb_cdc.import_usb()
    assert usb_cdc.usb_available() is False
    with pytest.raises(TransportError, match="pyusb"):
        UsbCdcTransport({"vid": "303a", "pid": "1001"}).probe()


# ---- through SerialSource


def usb_cfg(match=None, kind="link_module", rc_role="mirror", **opts):
    return SourceConfig(
        id="link",
        kind=kind,
        match=match or {"vid": "303a", "pid": "1001"},
        rc_role=rc_role,
        options={"transport": "usb", "poll_s": 0.05, "stats_period_s": 0.2, **opts},
    )


@contextlib.asynccontextmanager
async def running(source):
    pkts = []
    task = asyncio.create_task(source.run(pkts.append))
    try:
        yield pkts
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def heartbeat_stream(n=3):
    return b"".join(net.stream_frame(net.net_heartbeat(2, 1, 0x761D5378, 1000 * i)) for i in range(n))


async def test_stream_into_framer_and_profile_and_send(fake_usb):
    dev = fake_usb.devices[0]
    dev.feed(heartbeat_stream(3))
    src = build_source(usb_cfg(), PROFILE)
    assert isinstance(src, SerialSource)
    async with running(src):
        await wait_for(lambda: src.status().state == "connected", what="connected")
        await wait_for(lambda: src._framer.stats.frames >= 3)
        assert src._framer.stats.frames >= 3 and src._framer.stats.crc_errors == 0
        assert src.status().device.startswith("usb 303a:1001")
        assert await src.send_heartbeat(1.0)  # the node 0 heartbeat goes out through bulk OUT
        await wait_for(lambda: dev.out, what="heartbeat written")
        assert dev.ctrl[0] == (0x21, 0x22, 0x03, 0, None)
        assert dev.ctrl[0][2] == 0x03
    assert dev.ctrl[-1][2] == 0x00 and dev.disposed  # cancelled: DTR dropped, resources disposed


async def test_dtr_asserted_for_link_module_over_usb(fake_usb):
    src = SerialSource(usb_cfg(), PROFILE)
    assert src._deassert_for("usb") is False and src._deassert_for("serial") is True
    src = SerialSource(usb_cfg(deassert_dtr_rts=True), PROFILE)
    assert src._deassert_for("usb") is True


async def test_disconnect_and_reconnect(fake_usb):
    dev = fake_usb.devices[0]
    src = SerialSource(usb_cfg(), PROFILE)
    async with running(src) as pkts:
        await wait_for(lambda: src.status().state == "connected", what="connected")
        dev.feed(heartbeat_stream(1))
        dev.inq.append(USBError("no device", errno.ENODEV))
        fake_usb.devices.clear()  # unplugged
        await wait_for(lambda: src.status().state == "searching", what="searching")
        assert dev.disposed and "gone while reading" in (src.status().last_error or "")
        assert any(p.TYPE == "GsEvent" and "disconnected" in p.detail for p in pkts)
        new = FakeDevice()
        new.feed(heartbeat_stream(1))
        fake_usb.devices.append(new)
        await wait_for(lambda: src.status().state == "connected" and src.status().reconnects == 1, what="reconnect")


async def test_permission_error_shows_state_error_and_retries(fake_usb):
    dev = fake_usb.devices[0]
    dev.fail_claim = USBError("Access denied", errno.EACCES)
    src = SerialSource(usb_cfg(), PROFILE)
    async with running(src):
        await wait_for(lambda: src.status().state == "error", what="error")
        assert "permission denied" in src.status().last_error
        dev.fail_claim = None
        await wait_for(lambda: src.status().state == "connected", what="connected after fix")


async def test_missing_pyusb_is_an_error_state(monkeypatch):
    monkeypatch.setitem(sys.modules, "usb", None)
    monkeypatch.setitem(sys.modules, "usb.core", None)
    src = SerialSource(usb_cfg(), PROFILE)
    async with running(src):
        await wait_for(lambda: src.status().state == "error", what="error")
        assert "pyusb" in src.status().last_error


# ---- config validation and auto selection


def test_transport_validation():
    with pytest.raises(ConfigError, match="transport must be one of"):
        SerialSource(usb_cfg(transport="bluetooth"), PROFILE)
    with pytest.raises(ConfigError, match="vid = .*pid"):
        SerialSource(usb_cfg(match={"vid": "303a"}), PROFILE)
    with pytest.raises(ConfigError, match="device paths do not apply"):
        SerialSource(usb_cfg(match={"device": "/dev/ttyACM0", "vid": "303a", "pid": "1001"}), PROFILE)
    with pytest.raises(ConfigError, match="hexadecimal"):
        SerialSource(usb_cfg(match={"vid": "zz", "pid": "1001"}), PROFILE)
    src = SerialSource(usb_cfg(match={"vid": "303a", "pid": "1001", "serial_number": "SN1"}), PROFILE)
    assert src._match["serial"] == "SN1"
    # default stays pyserial
    c = usb_cfg()
    del c.options["transport"]
    assert SerialSource(c, PROFILE)._transport == "serial"


def test_auto_selection(monkeypatch, tmp_path):
    present = {"v": None}
    monkeypatch.setattr(ss, "match_device", lambda m: present["v"])
    has_usb = {"v": True}
    monkeypatch.setattr(ss, "usb_available", lambda: has_usb["v"])
    src = SerialSource(usb_cfg(transport="auto"), PROFILE)
    assert src._pick_transport() == "usb"  # no tty, pyusb importable
    present["v"] = "/dev/ttyACM0"
    assert src._pick_transport() == "serial"  # tty exists
    present["v"], has_usb["v"] = None, False
    assert src._pick_transport() == "serial"  # no tty, no pyusb: keep searching with pyserial
    path_only = SerialSource(usb_cfg(match={"device": "/dev/x"}, transport="auto"), PROFILE)
    has_usb["v"] = True
    assert path_only._pick_transport() == "serial"  # a device path cannot be used over usb
    assert SerialSource(usb_cfg(transport="serial"), PROFILE)._pick_transport() == "serial"
    assert SerialSource(usb_cfg(transport="usb"), PROFILE)._pick_transport() == "usb"
