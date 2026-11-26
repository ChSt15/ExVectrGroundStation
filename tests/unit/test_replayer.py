"""Replayer: seek equals sequential replay, playback pacing, speeds, ended state, incomplete logs."""

import asyncio
import random
import time

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from exvectr_gs.core import Bus, History, StateStore
from exvectr_gs.packets import REGISTRY
from exvectr_gs.recording import LogLibrary, LogReader
from exvectr_gs.replay import Replayer
from tests.support_logs import T0_NS, make_log, write_long_log

S = 1_000_000_000


def make(tmp_path, scenario="dropouts", duration=45.0, **kw):
    path = make_log(tmp_path, scenario, duration)
    bus, store, hist = Bus(), StateStore(REGISTRY, []), History(REGISTRY)
    clock = [0.0]
    rp = Replayer(LogLibrary(tmp_path), bus, store, hist, monotonic=lambda: clock[0], **kw)
    return rp, path, bus, store, hist


def latest_packets(store):
    return {(e.packet.TYPE, e.packet.src): e.packet for e in store.snapshot()}


def sequential_state(reader, idx, t):
    """Reference: apply every line with index time <= t in file order."""
    out = {}
    n = 0
    for _, pkt in reader.iter_packets():
        if idx.times[n] > t:
            break
        n += 1
        out[(pkt.TYPE, pkt.src)] = pkt
    return out


async def test_open_is_paused_at_start_with_first_state(tmp_path):
    rp, path, _, store, hist = make(tmp_path)
    assert rp.status().state == "closed" and rp.status().t is None
    info = await rp.open(path.name)
    s = rp.status()
    assert info.name == path.name and s.state == "paused" and s.name == path.name
    assert s.t == s.t_start == T0_NS and s.t_end > s.t_start + 40 * S and s.speed == 1.0
    assert latest_packets(store)  # first packets at t_start applied
    assert hist.query("Position", None, ["pos"], 0, 1 << 62, 0)["t"]  # history preloaded for the whole log


async def test_open_unknown_log_raises_file_not_found(tmp_path):
    rp, *_ = make(tmp_path)
    with pytest.raises(FileNotFoundError):
        await rp.open("nope.exlog")
    assert rp.status().state == "closed"


async def test_transport_needs_an_open_log(tmp_path):
    rp, *_ = make(tmp_path)
    for call in (rp.play, rp.pause, lambda: rp.seek(0)):
        with pytest.raises(RuntimeError):
            call()
    with pytest.raises(ValueError):
        rp.set_speed(3)


async def test_seek_equals_sequential_replay_random_times(tmp_path):
    rp, path, _, store, _ = make(tmp_path)
    await rp.open(path.name)
    reader = LogReader(path)
    idx = reader.build_index()
    rng = random.Random(7)
    ts = [rp.status().t_start, rp.status().t_end] + [
        rng.randint(T0_NS - 3 * S, rp.status().t_end + 3 * S) for _ in range(60)
    ]
    for t in ts:
        rp.seek(t)
        tc = max(rp.status().t_start, min(t, rp.status().t_end))
        assert rp.status().t == tc
        assert latest_packets(store) == sequential_state(reader, idx, tc), t


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(frac=st.lists(st.floats(0, 1), min_size=1, max_size=4))
def test_seek_property(tmp_path_factory, frac):
    tmp = tmp_path_factory.mktemp("prop")
    rp, path, _, store, _ = make(tmp, "full", 12.0)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(rp.open(path.name))
        reader = LogReader(path)
        idx = reader.build_index()
        for f in frac:
            t = int(idx.t_start + f * (idx.t_end - idx.t_start))
            rp.seek(t)
            assert latest_packets(store) == sequential_state(reader, idx, t)
    finally:
        loop.close()


async def test_seek_then_play_continues_without_gap_or_duplicates(tmp_path):
    rp, path, bus, store, _ = make(tmp_path, "full", 10.0)
    await rp.open(path.name)
    seen = []
    bus.subscribe(seen.append)
    rp.seek(T0_NS + 4 * S)
    clock_pkts = [p for _, p in LogReader(path).iter_packets() if p.t > T0_NS + 4 * S]
    rp._advance(100.0)  # whole rest of the log in one step (speed 1: capped only by the log end)
    assert seen == clock_pkts and rp.status().state == "paused"  # _advance does not end by itself unless playing
    assert rp.status().t == rp.status().t_end


async def test_playback_paces_at_speed_and_ends(tmp_path):
    rp, path, bus, store, _ = make(tmp_path, "full", 10.0, tick_s=0.0)
    clock = [0.0]
    rp._mono = lambda: clock[0]
    await rp.open(path.name)
    seen = []
    bus.subscribe(seen.append)
    for speed in (0.25, 1, 8):
        rp.seek(T0_NS)
        seen.clear()
        rp.set_speed(speed)
        rp.play()
        assert rp.status().state == "playing"
        # one second of wall time at `speed` publishes exactly the lines with t <= t_start + speed s
        rp._advance(1.0)
        want = [p for _, p in LogReader(path).iter_packets() if T0_NS < p.t <= T0_NS + int(speed * S)]
        assert [p.t for p in seen] == [p.t for p in want] and rp.status().t == T0_NS + int(speed * S)
        rp.pause()
        assert rp.status().state == "paused"


async def test_play_to_end_sets_ended_and_play_restarts(tmp_path):
    rp, path, bus, store, _ = make(tmp_path, "full", 6.0, tick_s=0.001)
    await rp.open(path.name)
    rp.set_speed(8)
    seen = []
    bus.subscribe(seen.append)
    rp.seek(rp.status().t_end - S // 2)
    rp.play()
    deadline = time.monotonic() + 5
    rp._mono = time.monotonic  # real clock for the task
    while rp.status().state != "ended" and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    s = rp.status()
    assert s.state == "ended" and s.t == s.t_end
    assert latest_packets(store) == sequential_state(LogReader(path), LogReader(path).build_index(), s.t_end)
    rp.seek(s.t_start + S)  # seek before the end leaves `ended`
    assert rp.status().state == "paused"
    rp.seek(s.t_end)
    rp.play()
    assert rp.status().state == "ended"  # nothing left to play
    rp.close()


async def test_play_from_ended_restarts_at_the_beginning(tmp_path):
    rp, path, _, store, _ = make(tmp_path, "full", 4.0)
    await rp.open(path.name)
    rp.seek(rp.status().t_end)
    rp._state = "ended"
    rp.play()
    assert rp.status().state == "playing" and rp.status().t == rp.status().t_start
    rp.pause()


async def test_seek_keeps_play_state(tmp_path):
    rp, path, *_ = make(tmp_path, "full", 8.0)
    await rp.open(path.name)
    rp.play()
    rp.seek(T0_NS + 3 * S)
    assert rp.status().state == "playing"
    rp.pause()
    rp.seek(T0_NS + 5 * S)
    assert rp.status().state == "paused"


async def test_close_clears_everything_and_stops_publishing(tmp_path):
    rp, path, bus, store, hist = make(tmp_path, "full", 6.0, tick_s=0.001)
    await rp.open(path.name)
    seen = []
    bus.subscribe(seen.append)
    rp._mono = time.monotonic
    rp.play()
    await asyncio.sleep(0.05)
    rp.close()
    n = len(seen)
    await asyncio.sleep(0.05)
    assert len(seen) == n and rp.status().state == "closed" and rp.status().name is None
    assert store.snapshot() == [] and hist.query("Position", None, ["pos"], 0, 1 << 62, 0)["t"] == []


async def test_reopen_during_loading_supersedes_first_open(tmp_path):
    rp, path, *_ = make(tmp_path, "full", 4.0)
    a = asyncio.create_task(rp.open(path.name))
    await asyncio.sleep(0)  # a is now building its index in the worker thread
    rp.close()
    with pytest.raises(RuntimeError):
        await a
    assert rp.status().state == "closed"


async def test_incomplete_log_with_truncated_last_line(tmp_path):
    rp, path, _, store, _ = make(tmp_path, "full", 6.0)
    data = path.read_bytes()
    path.write_bytes(data[: data.index(b"#end")] + b"1763471006000000000 usb Posi")
    info = await rp.open(path.name)
    assert not info.complete
    rp.seek(rp.status().t_end)
    assert ("Position", "usb") in latest_packets(store)


async def test_empty_log_opens_paused_and_does_nothing(tmp_path):
    p = tmp_path / "e.exlog"
    p.write_text("#exlog v=1\n#meta started=2025-11-18T14:03:20.000+01:00\n")
    rp, *_ = make(tmp_path / "x")  # unrelated library: build one on tmp_path instead
    rp = Replayer(LogLibrary(tmp_path), Bus(), StateStore(REGISTRY, []), History(REGISTRY))
    await rp.open("e.exlog")
    s = rp.status()
    assert s.state == "paused" and s.t == s.t_start == s.t_end
    rp.seek(s.t + S)
    rp.play()
    assert rp.status().state == "ended"


async def test_markers_come_from_the_index(tmp_path):
    rp, path, *_ = make(tmp_path, "full", 30.0)
    assert rp.markers() == []
    await rp.open(path.name)
    assert rp.markers() == LogReader(path).build_index().markers


async def test_seek_in_one_hour_log_under_200_ms(tmp_path):
    base = make_log(tmp_path / "base", "full", 20.0)
    d = tmp_path / "lib"
    d.mkdir()
    write_long_log(d / "long.exlog", 1.0, base)
    rp = Replayer(LogLibrary(d), Bus(), StateStore(REGISTRY, []), History(REGISTRY))
    await rp.open("long.exlog")
    rng = random.Random(3)
    s = rp.status()
    worst = 0.0
    for _ in range(30):
        t0 = time.perf_counter()
        rp.seek(rng.randint(s.t_start, s.t_end))
        worst = max(worst, time.perf_counter() - t0)
    assert worst < 0.2, f"seek took {worst * 1000:.0f} ms"


async def test_read_series_whole_lifts_the_600_s_limit_and_bounds_the_points(tmp_path):
    base = make_log(tmp_path / "base", "full", 20.0)
    d = tmp_path / "lib"
    d.mkdir()
    write_long_log(d / "long.exlog", 0.2, base)  # 720 s
    rp = Replayer(LogLibrary(d), Bus(), StateStore(REGISTRY, []), History(REGISTRY))
    await rp.open("long.exlog")
    s = rp.status()
    specs = [{"type": "Attitude", "src": None, "fields": ["omega"]}]
    (cut,) = rp.read_series(specs, s.t_start, s.t_end, 100_000)
    assert cut["t"][-1] - cut["t"][0] <= 600 * S  # the plot buffer limit
    (full,) = rp.read_series(specs, s.t_start, s.t_end, 500, whole=True)
    assert 0 < len(full["t"]) <= 500 and full["t"] == sorted(full["t"])
    assert full["t"][-1] - full["t"][0] > 700 * S  # the entire log, thinned
