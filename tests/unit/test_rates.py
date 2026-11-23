"""Data-rate statistics: sliding windows, replay time, path labels, wire size of a frame."""

import pytest

from exvectr_gs.core import rates as R
from exvectr_gs.wire import load_profile
from exvectr_gs.wire.exvectrnet_1.framing import NetFramer, build_data_frame, build_stream_frame
from exvectr_gs.wire.profile import Frame

S = 1_000_000_000


def feed(tr, hz, t0, t1, payload=10, wire=14, **kw):
    n = int((t1 - t0) * hz)
    for i in range(n):
        tr.record("usb", kw.get("ptype", "POSE"), 1, R.PATH_USB, t0 * S + int(i * S / hz), payload, wire, 100.0)


def one(tr, now):
    (r,) = tr.snapshot(now)
    return r


def test_steady_rate_and_bytes_in_both_windows():
    tr = R.RateTracker()
    tr.begin(0)
    feed(tr, 50, 0, 12)
    r = one(tr, 12 * S)
    assert r.rate_hz == pytest.approx((50.0, 50.0), rel=0.03)
    assert r.bytes_s[0] == pytest.approx(700, rel=0.03) and r.payload_s[0] == pytest.approx(500, rel=0.03)
    assert r.nominal_hz == 100.0 and r.bytes_known and r.age_s == pytest.approx(0.02, abs=0.01)


def test_windows_follow_a_rate_change_and_age_grows():
    tr = R.RateTracker()
    tr.begin(0)
    feed(tr, 10, 0, 8)
    feed(tr, 40, 8, 10)
    r = one(tr, 10 * S)
    assert r.rate_hz[0] == pytest.approx(40, rel=0.05)  # the last 2 s
    assert r.rate_hz[1] == pytest.approx((8 * 10 + 2 * 40) / 10, rel=0.05)
    r = one(tr, 14 * S)  # nothing for 4 s: the 2 s window is empty, the 10 s window still holds 120 events
    assert r.rate_hz[0] == 0 and r.rate_hz[1] == pytest.approx(12, rel=0.05) and r.age_s == pytest.approx(4, abs=0.05)
    r = one(tr, 30 * S)
    assert r.rate_hz == (0.0, 0.0) and r.total > 0  # dropped from the windows, the stream stays listed


def test_young_observation_averages_over_observed_time_not_the_window():
    tr = R.RateTracker()
    tr.begin(0)
    feed(tr, 20, 0, 1)
    assert one(tr, 1 * S).rate_hz[0] == pytest.approx(20, rel=0.06)  # 20 events in 1 s, not 10/s over 2 s


def test_replay_time_is_the_callers_time_not_the_wall_clock():
    tr = R.RateTracker()
    base = 1_700_000_000 * S  # a recording from long ago
    tr.begin(base)
    feed(tr, 5, 0, 1)  # (unrelated stream shape check: times are plain numbers)
    tr = R.RateTracker()
    tr.begin(base)
    for i in range(30):
        tr.record("usb", "Attitude", None, R.PATH_USB, base + i * S // 10, nominal_hz=10.0)
    r = one(tr, base + 3 * S)
    assert r.rate_hz[0] == pytest.approx(10, rel=0.06) and not r.bytes_known
    assert one(tr, base + 3 * S + S // 2).age_s == pytest.approx(0.6, abs=0.01)


def test_wire_size_falls_back_to_payload_and_unknown_sizes_are_null():
    tr = R.RateTracker()
    tr.begin(0)
    for i in range(10):
        tr.record("usb", "X", 1, R.PATH_USB, i * S // 10, 20, 0)
    assert one(tr, S).bytes_s[0] == one(tr, S).payload_s[0] > 0
    out = R.rates_to_json(tr.snapshot(S), tr.windows, 1.0, "live", 6100.0, "frames")
    assert out["streams"][0]["bytes_s"] is not None
    tr2 = R.RateTracker()
    tr2.record("fake", "Attitude", None, R.PATH_SIM, 0)
    out = R.rates_to_json(tr2.snapshot(S), tr2.windows, 1.0, "live", 6100.0, "frames")
    assert (
        out["streams"][0]["bytes_s"] is None
        and out["paths"][0]["bytes_s"] is None
        and out["air"]["utilisation"] is None
    )


def test_json_sorting_path_sums_and_air_utilisation():
    tr = R.RateTracker()
    tr.begin(0)
    for i in range(100):
        t = i * S // 50
        tr.record("link", "VEHICLE_STATUS", 1, R.PATH_AIR, t, 30, 40, 5.0)
        tr.record("link", "AIR_POSE", 1, R.PATH_AIR, t, 100, 110, 10.0)
        tr.record("link", "RC_STATE", 2, R.PATH_LOCAL, t, 20, 30)
        tr.record("usb", "POSE", 1, R.PATH_USB, t, 50, 60, 100.0)
    out = R.rates_to_json(tr.snapshot(2 * S), tr.windows, 2.0, "live", 6100.0, "frames")
    order = [(r["path"], r["type"]) for r in out["streams"]]
    assert order == [
        ("air", "AIR_POSE"),
        ("air", "VEHICLE_STATUS"),
        ("usb direct", "POSE"),
        ("router local", "RC_STATE"),
    ]
    air = next(p for p in out["paths"] if p["path"] == "air")
    assert air["streams"] == 2 and air["payload_s"][0] == pytest.approx(50 * 130, rel=0.03)
    assert out["air"]["utilisation"][0] == pytest.approx(50 * 130 / 6100, rel=0.03)


def test_path_rules():
    assert R.frame_path("vehicle_usb", False) == R.PATH_USB
    assert R.frame_path("link_module", True) == R.PATH_AIR
    assert R.frame_path("link_module", False) == R.PATH_LOCAL
    assert R.frame_path("link_module", None) == R.PATH_UNKNOWN
    assert R.packet_path("link_module", "RcState") == R.PATH_LOCAL
    assert R.packet_path("link_module", "Attitude") == R.PATH_AIR
    assert R.packet_path("fake", "Attitude") == R.PATH_SIM
    info = R.FrameInfo("X", 1, True, 5.0, 20.0)
    assert R.nominal_for(R.PATH_AIR, info) == 5.0 and R.nominal_for(R.PATH_USB, info) == 20.0


def frame(port, node):
    return Frame(channel=port, payload=b"\0" * 8, t=0, meta={"src": node})


def test_describe_frame_labels_nodes():
    p = load_profile("exvectrnet-1")
    d = p.describe_frame(frame(0x20, 1))
    assert (d.type, d.node, d.remote, d.nominal_air_hz, d.nominal_usb_hz) == ("VEHICLE_STATUS", 1, True, 5.0, 20.0)
    assert p.describe_frame(frame(0x21, 1)).type == "AIR_POSE"
    assert p.describe_frame(frame(0x03, 3)).remote is True  # the ship radio node's HANDSET_TELEM crossed the air
    assert p.describe_frame(frame(0x01, 2)).remote is False  # the Router's own LINK_STATS
    assert p.describe_frame(frame(0x10, 2)).remote is False  # the Pocket's RC_STATE copy
    assert p.describe_frame(frame(0x10, 1)).remote is False  # RC_STATE is never air data
    assert p.describe_frame(frame(0x7F, 1)).type == "port 0x7F"
    assert p.describe_frame(frame(0x20, 0)).remote is None


def test_framer_reports_the_wire_size_of_a_frame():
    net = build_data_frame(src=1, dst=0, port=0x20, seq=3, payload=bytes(range(20)), qos=1, latest=True, ttl=3)
    wire = build_stream_frame(net)
    (f,) = NetFramer().feed(wire, 5)
    assert f.wire_len == len(wire) > len(f.payload) + 6


def _uplink_tracker():
    tr = R.RateTracker()
    tr.uplink_items = load_profile("exvectrnet-1").uplink_air_items()
    tr.begin(0)
    return tr


def test_uplink_items_come_from_the_profile_with_the_air_rate_of_cmd_state():
    items = {i.type: i for i in _uplink_tracker().uplink_items}
    assert items["CMD_STATE"].hz == 25.0 and items["CMD_STATE"].payload_bytes == 14 and items["CMD_STATE"].needs_rc
    assert "RC_STATE" not in items  # the Pocket's RC_STATE no longer goes to the air
    assert items["LINK_CONFIG"].hz == 2.0


def test_uplink_estimate_counts_cmd_state_only_while_it_is_sent_and_never_twice():
    tr = _uplink_tracker()
    est = tr.uplink_estimate(2 * S)
    assert est["payload_s"][0] == 2.0 * tr.uplink_items[1].payload_bytes  # only LINK_CONFIG without CMD_STATE
    for i in range(4):
        tr.record_tx("link", "Request", 2 * S - i * S // 4, 12)
    for i in range(50):  # the engine's frames: the 25 Hz item, never additive to it
        tr.record_tx("link", "CmdState", 2 * S - i * S // 25, 14)
    est = tr.uplink_estimate(2 * S)
    cmd = 25 * 14
    cfg = 2 * tr.uplink_items[1].payload_bytes
    assert est["payload_s"][0] == pytest.approx(cmd + cfg + 4 * 12 / 2.0)
    assert [i["type"] for i in est["items"]] == ["CMD_STATE", "LINK_CONFIG", "Request"]
    assert est["items"][2]["source"] == "sent by the ground station"


def test_air_packet_counter_rate_handles_wrap_and_staleness():
    tr = R.RateTracker()
    for i, v in enumerate([65530, 6, 19, 31]):  # +12 or +13 per 0.5 s across the wrap
        tr.record_air_counter("down", i * S // 2, v)
    assert tr.air_packets_s("down", 2 * S) == pytest.approx(25.0, rel=0.1)
    assert tr.air_packets_s("down", 30 * S) is None and tr.air_packets_s("up", 2 * S) is None


def test_json_has_per_direction_capacity_and_marks_the_uplink_estimated():
    tr = _uplink_tracker()
    tr.record("link", "AIR_POSE", 1, R.PATH_AIR, S, 100, 110, 10.0)
    out = R.rates_to_json(tr.snapshot(2 * S), tr.windows, 2.0, "live", (6100.0, 3000.0), "frames", tr, 2 * S)["air"]
    assert (
        out["capacity_bytes_s"] == out["down"]["capacity_bytes_s"] == 6100.0 and out["up"]["capacity_bytes_s"] == 3000.0
    )
    assert out["down"]["estimated"] is False and out["up"]["estimated"] is True and out["packets_max_s"] == 25.0
    assert out["up"]["payload_s"][0] > 0 and out["up"]["utilisation"][0] == out["up"]["payload_s"][0] / 3000.0
    assert out["down"]["packets_s"] is None
