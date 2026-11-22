import pytest

from exvectr_gs.core import FakeClock, StateStore, field_status, freshness
from exvectr_gs.packets import REGISTRY
from exvectr_gs.packets.defs.vehicle import Attitude, Position

S = 1_000_000_000


def att(t_s, src="usb", **kw):
    return Attitude(t=int(t_s * S), src=src, omega=[0, 0, 0], q=[1, 0, 0, 0], **kw)


def store(**kw):
    return StateStore(REGISTRY, ["usb", "link"], **kw)


def test_first_packet_uses_nominal_rate():
    s = store()
    s.update(att(0))
    e = s.get("Attitude", "usb")
    assert e.count == 1 and e.interval_s is None
    assert e.stale_after_s == pytest.approx(3 / 5)  # Attitude nominal 5 Hz


def test_ewma_interval_and_stale_threshold():
    s = store(stale_min_s=0.05)
    for i in range(20):
        s.update(att(i * 0.01))
    e = s.get("Attitude", "usb")
    assert e.count == 20
    assert e.interval_s == pytest.approx(0.01)
    assert e.stale_after_s == pytest.approx(0.05)  # max(3*0.01, 0.05)
    s.update(att(0.3))  # one slow interval: EWMA alpha 0.2
    assert s.get("Attitude", "usb").interval_s == pytest.approx(0.2 * 0.11 + 0.8 * 0.01, rel=1e-6)


def test_stale_min_floor():
    s = store(stale_min_s=0.5)
    for i in range(5):
        s.update(att(i * 0.01))
    assert s.get("Attitude", "usb").stale_after_s == 0.5


def test_freshness_transitions_with_fake_clock():
    s = store(stale_min_s=0.5)
    clock = FakeClock()
    for i in range(10):
        s.update(att(i * 0.1))
    e = s.get("Attitude", "usb")  # last packet at 0.9 s, interval 0.1 -> stale after 0.5
    clock.set(int(1.2 * S))
    assert freshness(e, clock.now()) == "fresh"
    clock.set(int(1.4 * S))
    assert freshness(e, clock.now()) == "fresh"
    clock.set(int(1.5 * S))
    assert freshness(e, clock.now()) == "stale"
    assert freshness(None, 0) == "missing"


def test_field_status_four_states():
    s = store()
    s.update(att(0, tilt_acc=0.1))
    e = s.get("Attitude", "usb")
    assert field_status(None, "q", 0) == "missing"
    assert field_status(e, "tilt_acc", 0) == "fresh"
    assert field_status(e, "north_acc", 0) == "not supplied"
    assert field_status(e, "tilt_acc", 100 * S) == "stale"


def test_preferred_source_fresh_priority_and_fallback():
    s = store(stale_min_s=0.5)
    s.update(att(0.0, "link"))
    s.update(att(0.1, "usb"))
    assert s.get("Attitude", now=int(0.2 * S)).packet.src == "usb"  # both fresh: priority wins
    s.update(att(0.9, "link"))
    # usb last seen at 0.1: stale at 1.0 (threshold 0.6), link (seen 0.9) is fresh
    assert s.get("Attitude", now=int(1.0 * S)).packet.src == "link"
    # everything stale: newest entry wins
    assert s.get("Attitude", now=int(100 * S)).packet.src == "link"
    assert s.get("Attitude", "usb").packet.t == int(0.1 * S)
    assert s.get("Position") is None


def test_default_now_uses_newest_packet_time_or_clock():
    s = store()
    s.update(att(0.0, "link"))
    s.update(att(10.0, "usb"))
    assert s.get("Attitude").packet.src == "usb"  # link is stale relative to newest packet
    s2 = StateStore(REGISTRY, ["usb"], clock=lambda: int(50 * S))
    s2.update(att(0.0, "usb"))
    assert freshness(s2.get("Attitude"), int(50 * S)) == "stale"


def test_snapshot_and_reset():
    s = store()
    s.update(att(0, "usb"))
    s.update(att(0, "link"))
    s.update(Position(t=0, src="usb", pos=[0, 0, 0]))
    assert len(s.snapshot()) == 3
    s.reset([att(1, "usb")])
    assert len(s.snapshot()) == 1 and s.get("Attitude", "usb").count == 1
    s.reset()
    assert s.snapshot() == []


# ---- field-level merge over sources (get_merged)

from exvectr_gs.api.protocol import item_to_json  # noqa: E402
from exvectr_gs.packets.defs.vehicle import Actuators  # noqa: E402


def act(t_s, src, **kw):
    return Actuators(t=int(t_s * S), src=src, flaps=[0, 0, 0, 0], tvc=[0, 0], thrust=0.0, **kw)


def mstore():
    s = store(stale_min_s=0.1)
    return s


def test_merge_fills_a_field_only_one_source_carries():
    s = mstore()
    for i in range(10):  # usb 50 Hz with ground_distance, link 25 Hz with roll_torque, alternating
        s.update(act(i * 0.02, "usb", ground_distance=0.5))
        if i % 2:
            s.update(act(i * 0.02 + 0.005, "link", roll_torque=0.1))
    m = s.get_merged("Actuators", now=int(0.2 * S))
    assert m.entry.packet.src == "usb"  # priority
    assert m.entry.packet.ground_distance == 0.5 and m.entry.packet.roll_torque == 0.1
    assert m.fields["roll_torque"].src == "link" and m.fields["ground_distance"].src == "usb"
    assert m.fields["roll_torque"].t == int(0.185 * S)  # the field's own packet time, not usb's
    assert s.get("Actuators").packet.roll_torque is None  # the plain get is unchanged
    assert s.get("Actuators", "usb").packet.roll_torque is None  # per-source access unchanged


def test_merge_stable_across_alternating_arrivals():
    s = mstore()
    seen = set()
    for i in range(40):
        t = i * 0.01
        s.update(act(t, "usb", ground_distance=0.5) if i % 2 == 0 else act(t, "link", roll_torque=0.1))
        m = s.get_merged("Actuators", now=int(t * S))
        if i >= 3:
            seen.add((m.entry.packet.ground_distance, m.entry.packet.roll_torque))
    assert seen == {(0.5, 0.1)}


def test_merge_priority_when_both_carry_a_field():
    s = mstore()
    s.update(act(0.0, "link", roll_torque=0.1))
    s.update(Actuators(t=0, src="usb", flaps=[1, 1, 1, 1], tvc=[0, 0], thrust=0.7))
    m = s.get_merged("Actuators", now=0)
    assert m.entry.packet.flaps == [1, 1, 1, 1] and m.fields["flaps"].src == "usb"


def test_merge_freshness_expiry_falls_back_per_field():
    s = mstore()
    s.update(act(0.0, "usb", ground_distance=0.5))
    s.update(Actuators(t=int(5 * S), src="link", flaps=[2, 2, 2, 2], tvc=[0, 0], thrust=0.1, roll_torque=0.3))
    m = s.get_merged("Actuators", now=int(5.01 * S))
    assert m.entry.packet.src == "link"  # usb is stale
    assert m.entry.packet.flaps == [2, 2, 2, 2]  # fresh link wins over stale usb
    assert m.entry.packet.ground_distance == 0.5  # only the stale usb has it: kept, with its own old time
    assert m.fields["ground_distance"].t == 0
    assert freshness(Entry_of(s, "usb"), int(5.01 * S)) == "stale"


def Entry_of(s, src):  # noqa: N802
    return s.get("Actuators", src)


def test_merge_single_source_equals_get():
    s = mstore()
    s.update(act(0.0, "usb", ground_distance=0.5))
    m = s.get_merged("Actuators", now=0)
    assert m.entry is s.get("Actuators")
    assert set(m.fields) == {"flaps", "tvc", "thrust", "ground_distance"}
    assert s.get_merged("Position") is None


def test_item_json_carries_per_field_source_only_for_filled_fields():
    s = mstore()
    s.update(act(0.0, "usb", ground_distance=0.5))
    s.update(act(0.0, "link", roll_torque=0.1))
    m = s.get_merged("Actuators", now=0)
    d = item_to_json(m.entry.packet, m.entry.interval_s, m.entry.stale_after_s, m.fields)
    assert d["src"] == "usb" and d["f"]["roll_torque"] == 0.1
    assert list(d["fs"]) == ["roll_torque"] and d["fs"]["roll_torque"]["src"] == "link"
