import gzip
import os
import time

from exvectr_gs.packets.defs.vehicle import Attitude, VehicleState
from exvectr_gs.recording import LogLibrary, LogReader
from tests.support_logs import T0_NS, make_log, write_long_log


def full_log(tmp_path, name="full", duration=10.0):
    return make_log(tmp_path, name, duration)


def test_truncated_last_line_ignored(tmp_path):
    p = full_log(tmp_path)
    data = p.read_bytes()
    complete = len(list(LogReader(p).iter_packets()))
    # cut inside the final packet line (drop the #end line, then half of the line before it)
    body = data[: data.index(b"#end")]
    cut = body[:-30]
    p.write_bytes(cut)
    r = LogReader(p)
    got = list(r.iter_packets())
    assert len(got) == complete - 1  # the half-written line is not returned, even if the cut text would parse
    assert r.bad_lines == []
    assert r.header()["label"] == "sample-full"


def test_bad_middle_line_skipped_and_reported(tmp_path):
    p = full_log(tmp_path, duration=2.0)
    lines = p.read_bytes().split(b"\n")
    i = next(i for i, ln in enumerate(lines) if b" Position " in ln)
    lines.insert(i, b"123 usb Attitude omega=[nonsense")
    lines.insert(i, b"garbage")
    p.write_bytes(b"\n".join(lines))
    r = LogReader(p)
    n = len(list(r.iter_packets()))
    assert len(r.bad_lines) == 2 and n > 100


def test_start_offset_and_offsets(tmp_path):
    p = full_log(tmp_path, duration=3.0)
    r = LogReader(p)
    allp = list(r.iter_packets())
    off, pkt = allp[100]
    again = list(LogReader(p).iter_packets(start_offset=off))
    assert again[0] == (off, pkt) and len(again) == len(allp) - 100


def test_gzip_readable(tmp_path):
    p = full_log(tmp_path, duration=2.0)
    gz = tmp_path / (p.name + ".gz")
    gz.write_bytes(gzip.compress(p.read_bytes()))
    assert [x for _, x in LogReader(gz).iter_packets()] == [x for _, x in LogReader(p).iter_packets()]
    p.unlink()
    (info,) = LogLibrary(tmp_path).list()
    assert info.complete and info.name.endswith(".exlog.gz")


def test_index_keyframes_seek_and_markers(tmp_path):
    p = make_log(tmp_path, "full", 30.0)
    r = LogReader(p)
    idx = r.build_index()
    assert idx.t_start == T0_NS and idx.t_end == T0_NS + 30_000_000_000
    assert idx.n_packets == len(list(r.iter_packets()))
    assert len(idx.keyframes) == 31 and idx.keyframes[1].t == T0_NS + 1_000_000_000
    assert idx.size == p.stat().st_size

    # state reconstructed from a keyframe equals the state from a full sequential read
    t = T0_NS + 12_345_000_000
    kf = idx.keyframe_for(t)
    assert kf.t <= t < kf.t + 1_000_000_000
    latest = {}
    for _, pkt in r.iter_packets():
        if pkt.t >= kf.t and _ >= kf.offset:
            break
        latest[(pkt.TYPE, pkt.src)] = pkt
    from_kf = {k: next(iter(LogReader(p).iter_packets(o)))[1] for k, o in kf.latest.items()}
    assert from_kf == latest
    assert next(iter(LogReader(p).iter_packets(kf.offset)))[1].t >= kf.t
    # offset_for gives the first line at or after t
    o = idx.offset_for(t)
    first = next(iter(LogReader(p).iter_packets(o)))[1]
    assert first.t >= t
    assert idx.keyframe_for(T0_NS - 1) is None
    assert idx.offset_for(idx.t_end + 1) == idx.size

    # history: <= 20 Hz, only history types, packets are decoded
    att = idx.history[("Attitude", "usb")]
    assert 500 <= len(att) <= 30 * 20 + 1 and all(isinstance(x, Attitude) for x in att)
    assert all(b.t - a.t >= 50_000_000 for a, b in zip(att, att[1:], strict=False))
    assert ("VehicleState", "usb") not in idx.history or all(
        isinstance(x, VehicleState) for x in idx.history[("VehicleState", "usb")]
    )
    kinds = {m.kind for m in idx.markers}
    assert "event" in kinds and "note" in kinds
    assert any(m.kind == "mode" for m in idx.markers)  # vehicle mode changes in the scenario
    assert idx.bad_lines == 0


def test_index_gap_makes_single_keyframe(tmp_path):
    p = tmp_path / "gap.exlog"
    p.write_text(
        "#exlog v=1\n"
        "#meta started=2025-11-18T14:03:20.000+01:00\n"
        '1000000000 gs Note text="a"\n'
        '1500000000 gs Note text="b"\n'
        '9000000000 gs Note text="c"\n'
    )
    idx = LogReader(p).build_index()
    assert [k.t for k in idx.keyframes] == [1_000_000_000, 9_000_000_000]
    assert idx.keyframe_for(5_000_000_000).t == 1_000_000_000
    assert idx.keyframe_for(5_000_000_000).offset == idx.offsets[0]


def test_index_of_one_hour_log_under_10_s(tmp_path):
    base = make_log(tmp_path / "base", "full", 20.0)
    big = tmp_path / "big" / "long.exlog"
    big.parent.mkdir()
    n = write_long_log(big, 1.0, base)
    assert n > 900_000
    t = time.monotonic()
    idx = LogReader(big).build_index()
    dt = time.monotonic() - t
    assert idx.n_packets == n and len(idx.keyframes) == 3601
    assert dt < 10, f"index took {dt:.1f} s"


def test_library_lists_complete_and_incomplete(tmp_path):
    a = make_log(tmp_path, "full", 10.0)  # complete
    b = make_log(tmp_path, "dropouts", 8.0)
    data = b.read_bytes()
    b.write_bytes(data[: data.index(b"#end")] + b"1763471008000000000 usb Posi")  # crash: no #end, truncated line
    c = make_log(tmp_path, "radio", 5.0)
    c_data = c.read_bytes()
    (tmp_path / "notes.txt").write_text("not a log")
    (tmp_path / "empty.exlog").write_text("")
    (tmp_path / "foreign.exlog").write_text("hello\nworld\n")
    os.utime(a, (1, 1))
    infos = LogLibrary(tmp_path).list()
    by = {i.label: i for i in infos}
    assert set(by) == {"sample-full", "sample-dropouts", "sample-radio"}
    full, drop, radio = by["sample-full"], by["sample-dropouts"], by["sample-radio"]
    assert full.complete and abs(full.duration_s - 10.0) < 0.01 and full.sources == ["usb"]
    assert (
        full.started == T0_NS and full.size == a.stat().st_size and full.sim is True
    )  # the fake vehicle reports sim=true
    assert not drop.complete and abs(drop.duration_s - 8.0) < 0.01  # from the last complete line
    assert radio.complete and radio.sources == ["usb"] or radio.sources == ["link"]
    assert [i.name for i in infos] == sorted((i.name for i in infos), reverse=True)  # newest first
    assert c.read_bytes() == c_data
    assert LogLibrary(tmp_path / "missing").list() == []
    assert LogLibrary(tmp_path).get("../x") is None and LogLibrary(tmp_path).get(a.name).label == "sample-full"


def test_library_newest_first(tmp_path):
    a = make_log(tmp_path / "x", "full", 2.0)
    text = a.read_text().replace("started=2025-11-18T14:03:20.000+01:00", "started=2026-01-01T10:00:00.000+01:00")
    (tmp_path / "newer.exlog").write_text(text)
    (tmp_path / "older.exlog").write_text(a.read_text())
    assert [i.name for i in LogLibrary(tmp_path).list()] == ["newer.exlog", "older.exlog"]
