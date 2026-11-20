"""exvectrnet-1 end to end over the pty fake vehicle: SerialSource, profile, state store, API.

The fake vehicle encodes with its own implementation (tools/fake_vehicle_net.py), so these tests
compare the profile with a second implementation of the wire spec. No hardware is touched.
"""

import asyncio
import contextlib
import math
import tempfile

import exvectr_packets as xp
import pytest

from exvectr_gs.core.app import CoreApp
from exvectr_gs.core.clock import MonotonicClock
from exvectr_gs.core.config import SourceConfig, parse_config
from exvectr_gs.core.state import StateStore
from exvectr_gs.packets import REGISTRY
from exvectr_gs.packets.defs.host import GsEventKind
from exvectr_gs.packets.defs.link import RcOrigin, RcState
from exvectr_gs.sources import SerialSource
from exvectr_gs.wire import load_profile

from ..support_pty import FakeVehiclePty, wait_for
from ..tests_tools import net
from .test_api import Client
from .test_api import running as api_running


def profile():
    return load_profile("exvectrnet-1")


def cfg(link, sid="usb", kind="vehicle_usb", rc_role="control"):
    return SourceConfig(
        id=sid,
        kind=kind,
        match={"device": str(link)},
        rc_role=rc_role,
        options={"poll_s": 0.05, "stats_period_s": 0.2},
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


def make_vehicle(tmp_path, name="veh", role="vehicle", **kw):
    v = FakeVehiclePty(tmp_path / name, speed=2.0, profile="exvectrnet-1", role=role, hb_hz=10.0, **kw)
    v.start()
    return v


@pytest.fixture
def vehicle(tmp_path):
    v = make_vehicle(tmp_path)
    yield v
    v.stop()


def types(pkts):
    return {p.TYPE for p in pkts}


async def test_vehicle_usb_full_set_decodes_without_errors(vehicle):
    src = SerialSource(cfg(vehicle.link), profile())
    async with running(src) as pkts:
        want = {"VehicleStatus", "VehicleState", "Attitude", "Position", "Actuators", "FlapState", "MissionState"}
        want |= {"Gps", "HandsetTelem", "ImuRaw", "Estimator", "TaskTiming", "NetworkStatus"}
        await wait_for(lambda: want <= types(pkts), what="all USB packet types")
        await wait_for(lambda: any(p.TYPE == "LinkStats" and p.rx_frames_s > 0 for p in pkts), what="link stats")
    assert "RawFrame" not in types(pkts)
    stats = [p for p in pkts if p.TYPE == "LinkStats"]
    assert stats[-1].crc_errors == 0 and stats[-1].decode_errors == 0
    ns = [p for p in pkts if p.TYPE == "NetworkStatus"]
    assert ns[-1].present == [0, 1, 0, 0] and not ns[-1].incompatible and ns[-1].node == 1
    att = next(p for p in pkts if p.TYPE == "Attitude")
    assert math.isclose(sum(x * x for x in att.q), 1.0, abs_tol=1e-4) and len(att.omega) == 3
    vs = next(p for p in pkts if p.TYPE == "VehicleStatus")
    assert vs.simulation is True and vs.vehicle_time >= 0 and vs.gs_present is True
    gps = next(p for p in pkts if p.TYPE == "Gps")
    assert gps.num_sats == 12 and gps.fix_type == 3 and gps.lat == pytest.approx(0.8996)
    assert all(p.src == "usb" for p in pkts if p.TYPE not in ("GsEvent",))


async def test_values_agree_with_the_scenario(vehicle):
    src = SerialSource(cfg(vehicle.link), profile())
    async with running(src) as pkts:
        await wait_for(lambda: any(p.TYPE == "MissionState" for p in pkts) and any(p.TYPE == "Position" for p in pkts))
    ms = next(p for p in pkts if p.TYPE == "MissionState")
    # mission_time is ms on the wire; the scenario value at the first sample (sim time ~0) is -T_START s
    assert ms.t_mission is not None and ms.t_mission % 1_000_000 == 0
    pos = next(p for p in pkts if p.TYPE == "Position")
    ref = vehicle.scenario.pos(0.0)
    assert pos.pos == pytest.approx(ref, abs=1.0)  # first samples are at sim time ~0


async def test_bad_frames_cost_only_themselves(vehicle):
    src = SerialSource(cfg(vehicle.link), profile())
    async with running(src) as pkts:
        await wait_for(lambda: any(p.TYPE == "Attitude" for p in pkts))
        good = net.stream_frame(net.net_data(1, 0, 0x91, 1, net.pack_estimator(0)))
        bad_crc = bytearray(good)
        bad_crc[-3] ^= 0x01
        vehicle.write_raw(b"\x13\x37\x00" + bytes(bad_crc) + b"\xff" * 300 + b"\x00")
        vehicle.send_net(1, 0, 0x55, b"\xde\xad")  # unknown port
        vehicle.send_net(1, 0, 0x20, b"\x00\x01")  # known port, short payload
        await wait_for(lambda: sum(p.TYPE == "RawFrame" for p in pkts) >= 2, what="rawframes")
        n = len(pkts)
        await wait_for(lambda: any(p.TYPE == "Attitude" for p in pkts[n:]), what="data keeps flowing")
        await wait_for(lambda: any(p.TYPE == "LinkStats" and p.crc_errors >= 1 and p.decode_errors == 2 for p in pkts))
    raws = [p for p in pkts if p.TYPE == "RawFrame"]
    assert (raws[0].channel, raws[0].hex, raws[0].err) == (0x55, "dead", "unknown port")
    assert raws[1].channel == 0x20 and "VEHICLE_STATUS" in raws[1].err


async def test_schema_mismatch_is_reported_but_data_still_flows(tmp_path):
    v = make_vehicle(tmp_path, schema_hash=0x12345678)
    try:
        src = SerialSource(cfg(v.link), profile())
        async with running(src) as pkts:
            await wait_for(lambda: any(p.TYPE == "NetworkStatus" and p.incompatible for p in pkts))
            await wait_for(lambda: any(p.TYPE == "Attitude" for p in pkts))
        ev = [p for p in pkts if p.TYPE == "GsEvent" and p.kind == GsEventKind.link_warning]
        assert len(ev) == 1 and "0x12345678" in ev[0].detail  # once, not at every heartbeat
        assert f"0x{xp.SCHEMA_HASH:08X}" in next(p for p in pkts if p.TYPE == "NetworkStatus").detail
    finally:
        v.stop()


async def test_tx_module_forwards_vehicle_pocket_and_link_stats(tmp_path):
    v = make_vehicle(tmp_path, "tx", role="txmodule")
    try:
        src = SerialSource(cfg(v.link, "link", "link_module", "mirror"), profile())
        async with running(src) as pkts:
            want = {"VehicleStatus", "Attitude", "Position", "HandsetTelem", "RcState", "RadioLinkStats"}
            await wait_for(lambda: want | {"TxModuleStats", "RxModuleStats"} <= types(pkts), what="forwarded types")
            await wait_for(lambda: any(p.TYPE == "NetworkStatus" and p.present == [0, 1, 1, 1] for p in pkts))
        rcs = [p for p in pkts if p.TYPE == "RcState"]
        assert all(p.origin == RcOrigin.handset and p.source_ok is True for p in rcs)
        assert rcs[0].ch_us[2] == 1000  # throttle channel of the scenario handset
        # a window of consecutive sequence numbers: nothing lost or reordered by the framing
        assert [(b.seq - a.seq) % 256 for a, b in zip(rcs, rcs[1:], strict=False)] == [1] * (len(rcs) - 1)
        assert "RawFrame" not in types(pkts) and "ImuRaw" not in types(pkts)  # debug set is USB only
    finally:
        v.stop()


async def test_radio_path_air_actuators_and_air_mission_give_two_samples_per_frame(tmp_path):
    v = make_vehicle(tmp_path, "air", role="txmodule")
    try:
        src = SerialSource(cfg(v.link, "link", "link_module", "mirror"), profile())
        async with running(src) as pkts:
            await wait_for(
                lambda: sum(p.TYPE == "Actuators" for p in pkts) >= 10 and "MissionState" in types(pkts),
                what="air actuators and mission",
            )
        acts = [p for p in pkts if p.TYPE == "Actuators"]
        flaps = [p for p in pkts if p.TYPE == "FlapState"]
        assert len(acts) == len(flaps) and all(a.roll_torque is not None and a.ground_distance is None for a in acts)
        assert all(len(a.tvc) == 2 and len(a.flaps) == 4 and math.isfinite(a.thrust) for a in acts)
        # each frame gives (previous, newest) 20 ms apart; the previous sample of one frame is older than the next
        # frame's previous sample: the stream is time ordered with 50 samples per second
        pairs = list(zip(acts[0::2], acts[1::2], strict=False))
        assert all(round((new.t - old.t) / 1e6) == 20 for old, new in pairs)
        assert [a.t for a in acts] == sorted(a.t for a in acts)
        m = next(p for p in pkts if p.TYPE == "MissionState")
        assert m.sp_pos is not None and m.t_mission is not None and m.mission_index == 0 and m.waypoint is None
        assert "RawFrame" not in types(pkts)
    finally:
        v.stop()


async def test_rc_uplink_bytes_reach_the_vehicle_and_gating_holds(vehicle):
    ctrl = SerialSource(cfg(vehicle.link, rc_role="control"), profile())
    async with running(ctrl):
        await wait_for(lambda: ctrl.status().state == "connected")
        chs = [1000 + 50 * i for i in range(16)]
        for _ in range(3):
            assert await ctrl.send(RcState(t=1, src="gs", origin=RcOrigin.gs, ch_us=chs, seq=1)) is True
        await wait_for(lambda: len([f for f in vehicle.received if f.channel == 0x10]) == 3, what="3 rc frames")
        frames = [f for f in vehicle.received if f.channel == 0x10]
        wire_ch = [(c - 1500) * 2 for c in chs]
        assert all(f.payload == net.pack_rc_state(xp.RcState.FLAGS_SOURCE_OK, wire_ch) for f in frames)
        assert [(f.meta["src"], f.meta["dst"], f.meta["seq"]) for f in frames] == [(0, 1, 0), (0, 1, 1), (0, 1, 2)]
        n = len(vehicle.received)
        assert await ctrl.send(RcState(origin=RcOrigin.handset, ch_us=chs, seq=1)) is False
        assert await ctrl.send(REGISTRY.get("Attitude").cls()) is False
        await asyncio.sleep(0.2)
        assert len(vehicle.received) == n  # nothing but RC_STATE ever goes out (no heartbeat, no request)
    mirror = SerialSource(cfg(vehicle.link, "link", "link_module", "mirror"), profile())
    async with running(mirror):
        await wait_for(lambda: mirror.status().state == "connected")
        n = len(vehicle.received)
        assert await mirror.send(RcState(origin=RcOrigin.gs, ch_us=chs, seq=1)) is False
        await asyncio.sleep(0.2)
        assert len(vehicle.received) == n


async def test_vehicle_usb_preferred_over_tx_module_for_telemetry(tmp_path):
    clock = MonotonicClock()
    v1 = make_vehicle(tmp_path, "usb")
    v2 = make_vehicle(tmp_path, "radio", role="txmodule")
    store = StateStore(REGISTRY, ["usb", "link"], 0.3, clock.now)
    usb = SerialSource(cfg(v1.link, "usb"), profile(), clock)  # one profile per source id is not required
    link = SerialSource(cfg(v2.link, "link", "link_module", "mirror"), profile(), clock)

    def sink(p):
        store.update(p)

    tasks = [asyncio.create_task(s.run(sink)) for s in (usb, link)]
    try:
        await wait_for(
            lambda: {e.packet.src for e in store.snapshot() if e.packet.TYPE == "Position"} == {"usb", "link"}
        )
        assert store.get("Position").packet.src == "usb"  # both fresh: vehicle USB wins
        await wait_for(lambda: store.get("RadioLinkStats") and store.get("RcState"), what="TX module packets")
        assert store.get("RadioLinkStats").packet.src == "link"  # only the TX module has it
        assert store.get("RcState").packet.origin == RcOrigin.handset
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        v1.stop()
        v2.stop()


async def test_core_pipeline_to_store_and_api(tmp_path):
    v1 = make_vehicle(tmp_path, "usb")
    v2 = make_vehicle(tmp_path, "radio", role="txmodule")
    try:
        conf = parse_config(
            {
                "core": {"wire_profile": "exvectrnet-1", "source_priority": ["usb", "link"]},
                "server": {"bind": "127.0.0.1", "port": 1},
                "source": [
                    {
                        "id": "usb",
                        "kind": "vehicle_usb",
                        "match": {"device": str(v1.link)},
                        "rc_role": "control",
                        "poll_s": 0.05,
                    },  # fmt: skip
                    {
                        "id": "link",
                        "kind": "link_module",
                        "match": {"device": str(v2.link)},
                        "rc_role": "mirror",
                        "poll_s": 0.05,
                    },  # fmt: skip
                ],
                "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
            }
        )
        app = CoreApp(conf)
        assert app.sources[0]._profile.name == "exvectrnet-1"
        async with api_running(app) as rig:
            await wait_for(lambda: app.store.get("VehicleStatus") and app.store.get("RadioLinkStats"), what="store")
            assert app.store.get("VehicleStatus").packet.src == "usb"
            c = await Client.connect(rig)
            d = await c.ok("get_snapshot", view="live", preferred=True)
            items = {i["type"]: i for i in d["items"]}
            assert {"VehicleStatus", "Attitude", "Actuators", "NetworkStatus", "RadioLinkStats", "RcState"} <= set(
                items
            )
            assert items["VehicleStatus"]["src"] == "usb" and items["RadioLinkStats"]["src"] == "link"
            assert "RawFrame" not in items
            src_list = await c.ok("sources.list")
            assert {s["id"]: s["state"] for s in src_list} == {"usb": "connected", "link": "connected"}
            await c.close()
    finally:
        v1.stop()
        v2.stop()
