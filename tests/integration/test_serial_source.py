"""SerialSource end to end over a pty fake vehicle. Never touches real hardware."""

import asyncio
import contextlib
import math
import struct
from collections import Counter

import pytest

from exvectr_gs.core.clock import MonotonicClock
from exvectr_gs.core.config import ConfigError, SourceConfig
from exvectr_gs.core.state import StateStore
from exvectr_gs.packets import REGISTRY
from exvectr_gs.packets.defs.host import GsEventKind
from exvectr_gs.packets.defs.link import RcOrigin, RcState
from exvectr_gs.sources import SerialSource, SourceKind, build_source
from exvectr_gs.sources import serial_source as ss
from exvectr_gs.wire import load_profile

from ..support_pty import FakeVehiclePty, wait_for

PROFILE = load_profile("provisional-1")


def cfg(link, sid="usb", kind="vehicle_usb", rc_role="control", **opts):
    return SourceConfig(
        id=sid,
        kind=kind,
        match={"device": str(link)},
        rc_role=rc_role,
        options={"poll_s": 0.05, "stats_period_s": 0.2, **opts},
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


@pytest.fixture
def vehicle(tmp_path):
    v = FakeVehiclePty(tmp_path / "veh", speed=2.0)
    v.start()
    yield v
    v.stop()


def rc(origin=RcOrigin.gs, seq=1, v=1500):
    return RcState(t=1, src="gs", origin=origin, ch_us=[v] * 16, seq=seq)


async def test_end_to_end_decode_console_and_unknown(vehicle, tmp_path):
    src = SerialSource(cfg(vehicle.link), PROFILE)
    assert src.status().state == "disabled"
    async with running(src) as pkts:
        await wait_for(lambda: src.status().state == "connected", what="connected")
        await wait_for(
            lambda: {"Attitude", "Position", "VehicleState", "MissionState", "ConsoleText"} <= {p.TYPE for p in pkts}
        )
        vehicle.send_frame(999, b"\xde\xad")
        vehicle.send_console("hello from teensy")
        vehicle.send_frame(10, b"\x01")  # right channel, wrong length
        await wait_for(lambda: Counter(p.TYPE for p in pkts)["RawFrame"] >= 2, what="rawframes")
        await wait_for(lambda: any(p.TYPE == "ConsoleText" and p.text == "hello from teensy" for p in pkts))
        await wait_for(lambda: any(p.TYPE == "LinkStats" and p.connected and p.rx_frames_s > 0 for p in pkts))
        await wait_for(lambda: any(p.TYPE == "LinkStats" and p.decode_errors >= 2 for p in pkts), what="decode_errors")
        st = src.status()
        assert st.device == str(vehicle.link) and st.kind is SourceKind.VEHICLE_USB and st.reconnects == 0
    raws = [p for p in pkts if p.TYPE == "RawFrame"]
    assert (raws[0].channel, raws[0].hex) == (999, "dead") and raws[1].channel == 10
    assert all(p.src == "usb" for p in pkts if p.TYPE not in ("GsEvent",))
    assert [p.kind for p in pkts if p.TYPE == "GsEvent"][0] == GsEventKind.source_up
    # values come through the independent fake-vehicle packer and the profile decoder
    pos = [p for p in pkts if p.TYPE == "Position"]
    vs = [p for p in pkts if p.TYPE == "VehicleState"]
    assert all(len(p.pos) == 3 and all(math.isfinite(x) for x in p.pos) for p in pos)
    assert vs[0].mode is not None and vs[0].tvc_thrust is not None
    stats = [p for p in pkts if p.TYPE == "LinkStats"]
    assert stats[-1].crc_errors == 0 and stats[-1].decode_errors == 2
    assert src.status().state == "disabled"


async def test_values_match_scenario(vehicle):
    vehicle.speed = 1.0
    src = SerialSource(cfg(vehicle.link), PROFILE)
    async with running(src) as pkts:
        await wait_for(lambda: any(p.TYPE == "MissionState" for p in pkts))
        att = next(p for p in pkts if p.TYPE == "Attitude")
        assert math.isclose(sum(x * x for x in att.q), 1.0, abs_tol=1e-3)
        assert att.tilt_acc == pytest.approx(0.0035, abs=0.001)


async def test_rc_uplink_reaches_vehicle_and_gates(vehicle, tmp_path):
    ctrl = SerialSource(cfg(vehicle.link, rc_role="control"), PROFILE)
    assert await ctrl.send(rc()) is False  # not connected
    async with running(ctrl):
        await wait_for(lambda: ctrl.status().state == "connected")
        assert await ctrl.send(rc(seq=7, v=1234)) is True
        await wait_for(lambda: any(f.channel == 0x10 for f in vehicle.received), what="rc frame")
        f = next(f for f in vehicle.received if f.channel == 0x10)
        assert f.payload == bytes([7]) + struct.pack("<16H", *[1234] * 16)
        # refusals: handset-origin, non-UP types, wrong length
        n = len(vehicle.received)
        assert await ctrl.send(rc(origin=RcOrigin.handset)) is False
        assert await ctrl.send(REGISTRY.get("Note").cls(text="x")) is False
        assert await ctrl.send(REGISTRY.get("Attitude").cls()) is False
        assert await ctrl.send(RcState(origin=RcOrigin.gs, ch_us=[1500] * 3, seq=1)) is False
        await asyncio.sleep(0.2)
        assert len(vehicle.received) == n


@pytest.mark.parametrize("role", ["mirror", "none"])
async def test_rc_state_never_goes_to_non_control_source(vehicle, role):
    src = SerialSource(cfg(vehicle.link, sid="link", kind="link_module", rc_role=role), PROFILE)
    async with running(src):
        await wait_for(lambda: src.status().state == "connected")
        assert await src.send(rc()) is False
        await asyncio.sleep(0.2)
        assert not vehicle.received


async def test_unplug_and_replug(vehicle):
    src = SerialSource(cfg(vehicle.link), PROFILE)
    async with running(src) as pkts:
        await wait_for(lambda: src.status().state == "connected")
        await wait_for(lambda: any(p.TYPE == "Attitude" for p in pkts))
        vehicle.stop()  # unplug
        await wait_for(lambda: src.status().state == "searching", what="searching")
        await wait_for(lambda: any(p.TYPE == "GsEvent" and p.kind == GsEventKind.source_down for p in pkts))
        assert any(p.TYPE == "LinkStats" and not p.connected for p in pkts)
        assert src.status().last_error and src.status().reconnects == 0
        assert await src.send(rc()) is False
        n = len(pkts)
        await asyncio.sleep(0.2)
        assert not any(p.TYPE == "Attitude" for p in pkts[n:])
        vehicle.start()  # replug (new pty behind the same link path)
        await wait_for(lambda: src.status().state == "connected", what="reconnected")
        assert src.status().reconnects == 1 and src.status().last_error is None
        n = len(pkts)
        await wait_for(lambda: any(p.TYPE == "Attitude" for p in pkts[n:]), what="data after reconnect")
        await wait_for(lambda: any(p.TYPE == "LinkStats" and p.connected and p.reconnects == 1 for p in pkts))
        assert await src.send(rc()) is True
        # a second cycle
        vehicle.stop()
        await wait_for(lambda: src.status().state == "searching")
        vehicle.start()
        await wait_for(lambda: src.status().state == "connected" and src.status().reconnects == 2)


async def test_starts_before_device_exists(tmp_path):
    v = FakeVehiclePty(tmp_path / "late")
    src = SerialSource(cfg(v.link), PROFILE)
    async with running(src) as pkts:
        await asyncio.sleep(0.2)
        assert src.status().state == "searching" and src.status().device is None
        v.start()
        try:
            await wait_for(lambda: src.status().state == "connected")
            assert src.status().reconnects == 0  # first connection is not a reconnect
            await wait_for(lambda: any(p.TYPE == "Attitude" for p in pkts))
        finally:
            v.stop()


async def test_unopenable_device_reports_error(tmp_path):
    notty = tmp_path / "not-a-tty"
    notty.write_text("x")
    src = SerialSource(cfg(notty), PROFILE)
    async with running(src):
        await wait_for(lambda: src.status().state == "error", what="error state")
        assert src.status().last_error and src.status().device == str(notty)


async def test_two_sources_and_priority(tmp_path):
    clock = MonotonicClock()
    v1 = FakeVehiclePty(tmp_path / "usb", speed=2.0)
    v2 = FakeVehiclePty(tmp_path / "radio", speed=2.0, rates={"Attitude": 10.0, "Position": 10.0, "VehicleState": 5.0})
    v1.start()
    v2.start()
    store = StateStore(REGISTRY, ["usb", "link"], 0.3, clock.now)
    usb = SerialSource(cfg(v1.link, "usb"), PROFILE, clock)
    link = SerialSource(cfg(v2.link, "link", "link_module", "mirror"), PROFILE, clock)
    got = []

    def sink(p):
        got.append(p)
        store.update(p)

    tasks = [asyncio.create_task(s.run(sink)) for s in (usb, link)]
    try:
        await wait_for(lambda: {p.src for p in got if p.TYPE == "Position"} == {"usb", "link"}, what="both sources")
        assert store.get("Position").packet.src == "usb"  # preferred while both are fresh
        assert store.get("Position", "link").packet.src == "link"
        v1.stop()
        await wait_for(lambda: store.get("Position").packet.src == "link", what="fallback to link")
        assert usb.status().state == "searching" and link.status().state == "connected"
        v1.start()
        await wait_for(lambda: store.get("Position").packet.src == "usb", what="back to usb")
        # one stream's loss does not disturb the other
        assert link.status().reconnects == 0 and usb.status().reconnects == 1
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        v1.stop()
        v2.stop()


async def test_cancel_closes_port_and_threads(vehicle):
    src = SerialSource(cfg(vehicle.link), PROFILE)
    async with running(src):
        await wait_for(lambda: src.status().state == "connected")
        conn = src._conn
    assert src._conn is None and not conn.ser.is_open
    assert not any(t.is_alive() for t in conn.threads)


# ---------------------------------------------------------------- configuration and matching


def test_build_source_returns_serial_source(tmp_path):
    s = build_source(cfg(tmp_path / "x", kind="link_module", rc_role="mirror"), PROFILE)
    assert isinstance(s, SerialSource) and s.kind is SourceKind.LINK_MODULE
    assert s.caps.rc_role == "mirror" and s._deassert is True  # ESP32 bridges: DTR/RTS low
    assert build_source(cfg(tmp_path / "x"), PROFILE)._deassert is False  # Teensy: leave DTR alone


def test_config_errors(tmp_path):
    with pytest.raises(ConfigError):
        SerialSource(SourceConfig(id="a", kind="vehicle_usb"), PROFILE)  # no match
    with pytest.raises(ConfigError):
        SerialSource(SourceConfig(id="a", kind="vehicle_usb", match={"colour": "red"}), PROFILE)
    with pytest.raises(ConfigError):
        SerialSource(SourceConfig(id="a", kind="fake", match={"device": "x"}), PROFILE)
    with pytest.raises(ConfigError):
        SerialSource(SourceConfig(id="a", kind="vehicle_usb", match={"device": "x"}, options={"bogus": 1}), PROFILE)


def test_dtr_rts_deasserted_before_open(monkeypatch, tmp_path):
    seen = {}

    class FakeSerial:
        def __init__(self):
            self.dtr = True
            self.rts = True

        def open(self):
            seen["dtr"], seen["rts"], seen["excl"] = self.dtr, self.rts, self.exclusive

    monkeypatch.setattr(ss.serial, "Serial", FakeSerial)
    SerialSource(cfg(tmp_path / "x", kind="link_module", rc_role="mirror"), PROFILE)._open("/dev/null")
    assert seen == {"dtr": False, "rts": False, "excl": True}
    SerialSource(cfg(tmp_path / "x"), PROFILE)._open("/dev/null")
    assert seen["dtr"] is True and seen["rts"] is True


def test_match_by_vid_pid_serial(monkeypatch):
    class P:
        def __init__(self, device, vid, pid, sn):
            self.device, self.vid, self.pid, self.serial_number = device, vid, pid, sn

    ports = [
        P("/dev/ttyUSB0", 0x10C4, 0xEA60, "A1"),
        P("/dev/ttyACM0", 0x16C0, 0x0483, "T1"),
        P("/dev/ttyACM1", 0x16C0, 0x0483, "T2"),
    ]
    monkeypatch.setattr(ss.serial.tools.list_ports, "comports", lambda: ports)
    assert ss.match_device({"vid": "16c0", "pid": "0483"}) == "/dev/ttyACM0"
    assert ss.match_device({"vid": "16C0", "pid": "0x0483", "serial": "T2"}) == "/dev/ttyACM1"
    assert ss.match_device({"vid": "10c4"}) == "/dev/ttyUSB0"
    assert ss.match_device({"vid": "dead"}) is None
    assert ss.match_device({"device": "/nonexistent/by-id/x"}) is None
