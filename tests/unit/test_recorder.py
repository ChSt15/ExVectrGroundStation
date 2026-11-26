import errno
import os
import signal
import subprocess
import sys
import textwrap
import time
from datetime import datetime

import pytest

from exvectr_gs.core.bus import Bus
from exvectr_gs.packets import REGISTRY
from exvectr_gs.packets.defs.host import GsEventKind, Note
from exvectr_gs.packets.defs.vehicle import Attitude
from exvectr_gs.recording import LogReader, Recorder

T0 = 1_763_471_000_000_000_000


def make(tmp_path, **kw):
    bus = Bus()
    now = kw.pop("now", [T0])
    rec = Recorder(tmp_path, REGISTRY, bus, {"gs": "t", "host": "h", "profile": "fake"}, clock=lambda: now[0], **kw)
    return rec, bus, now


def att(t, src="usb"):
    return Attitude(t=t, src=src, omega=[0.0, 0.1, 0.2], q=[1.0, 0.0, 0.0, 0.0])


def local_stem(ns):
    return datetime.fromtimestamp(ns / 1e9).strftime("%Y-%m-%d_%H-%M-%S")


def test_file_layout_and_roundtrip(tmp_path):
    rec, bus, now = make(tmp_path, sources=[{"id": "usb", "kind": "fake", "device": "/dev/x y", "rc_role": "none"}])
    info = rec.start("hover test")
    assert info.name == f"{local_stem(T0)}_hover-test.exlog"
    pkts = [att(T0 + i * 1000) for i in range(5)]
    for p in pkts:
        bus.publish(p)
    now[0] = T0 + 10_000_000
    stopped = rec.stop()
    assert stopped.packets == 7  # rec_start, 5 attitudes, rec_stop
    assert stopped.stopped == now[0] and stopped.bytes == info.path.stat().st_size

    lines = info.path.read_text().splitlines()
    assert lines[0] == "#exlog v=1"
    assert lines[1].startswith("#meta gs=t host=h profile=fake started=") and 'label="hover test"' in lines[1]
    assert lines[2] == '#source id=usb kind=fake device="/dev/x y" rc_role=none'
    assert lines[-1].startswith("#end stopped=") and lines[-1].endswith("packets=7")
    types = [ln.split()[1] for ln in lines if ln.startswith("#type")]
    assert types == ["GsEvent", "Attitude"]  # once per type, before its first packet
    assert lines.index(
        "#type Attitude v=1 omega:float[3]:rad/s q:float[4] tilt_acc:float:rad north_acc:float:rad"
    ) < next(i for i, ln in enumerate(lines) if " Attitude " in ln and not ln.startswith("#"))

    r = LogReader(info.path)
    assert r.header()["label"] == "hover test" and r.header()["gs"] == "t"
    got = [p for _, p in r.iter_packets()]
    assert [p for p in got if isinstance(p, Attitude)] == pkts
    assert got[0].kind == GsEventKind.rec_start and got[-1].kind == GsEventKind.rec_stop


def test_both_link_direction_packets_are_recorded_and_replayed(tmp_path):
    from exvectr_gs.packets.defs.link import RxModuleStats, TxModuleStats

    rec, bus, _now = make(tmp_path)
    info = rec.start("link")
    tx = TxModuleStats(t=T0 + 1, src="link", rx_lq=40, rssi=-62, snr=7, tx_power=10, frames_expired=0, frames_dropped=0)
    rx = RxModuleStats(t=T0 + 2, src="link", rx_lq=96, rssi=-77, snr=5, tx_power=10, frames_expired=1, frames_dropped=2)
    bus.publish(tx)
    bus.publish(rx)
    rec.stop()
    got = [p for _, p in LogReader(info.path).iter_packets()]
    assert [p for p in got if isinstance(p, TxModuleStats)] == [tx]
    assert [p for p in got if isinstance(p, RxModuleStats)] == [rx]


def test_never_overwrites(tmp_path):
    rec, bus, now = make(tmp_path)
    stem = local_stem(T0)
    pre = tmp_path / f"{stem}.exlog"
    pre.write_text("precious")
    a = rec.start()
    rec.stop()
    b = rec.start()  # same second again
    rec.stop()
    c = rec.start()
    rec.stop()
    assert pre.read_text() == "precious"
    assert [a.name, b.name, c.name] == [f"{stem}-2.exlog", f"{stem}-3.exlog", f"{stem}-4.exlog"]
    assert len({a.path, b.path, c.path}) == 3


def test_start_stop_rules(tmp_path):
    rec, bus, now = make(tmp_path)
    assert rec.stop() is None
    assert not rec.status().active
    rec.start()
    assert rec.status().active
    with pytest.raises(RuntimeError):
        rec.start()
    rec.stop()
    assert rec.stop() is None


def test_nothing_recorded_when_idle_and_no_auto_start(tmp_path):
    d = tmp_path / "rec"
    rec, bus, now = make(d)
    bus.publish(att(T0))
    rec.note("ignored: not recording")
    assert not d.exists()  # constructor and packets create nothing: recording starts only by user action
    rec.start()
    rec.stop()
    bus.publish(att(T0 + 1))  # after stop: unsubscribed
    assert len(list(d.iterdir())) == 1


def test_old_file_untouched_and_new_file_after_restart(tmp_path):
    """A crashed recording stays as it is. A new Recorder never resumes or appends: the user starts a new one."""
    rec1, bus1, now = make(tmp_path)
    old = rec1.start("a").path
    bus1.publish(att(T0))
    time.sleep(0.3)  # let the writer flush; then "crash": abandon without stop()
    before = old.read_bytes()
    rec2, bus2, _ = make(tmp_path, now=[T0 + 5_000_000_000])
    assert not rec2.status().active and rec2.status().info is None
    new = rec2.start("b").path
    rec2.stop()
    assert new != old and old.read_bytes() == before
    rec1.stop()


def test_notes_recorded_only_while_active(tmp_path):
    rec, bus, now = make(tmp_path)
    seen = []
    bus.subscribe(seen.append, {"Note"})
    rec.note("before")
    path = rec.start().path
    rec.note('gusts "west", 3 m/s')
    rec.stop()
    assert [n.text for n in seen] == ["before", 'gusts "west", 3 m/s']
    notes = [p for _, p in LogReader(path).iter_packets() if isinstance(p, Note)]
    assert [n.text for n in notes] == ['gusts "west", 3 m/s']


def test_flush_within_interval_without_stop(tmp_path):
    rec, bus, now = make(tmp_path, flush_interval_ms=50)
    path = rec.start().path
    bus.publish(att(T0 + 1))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and b" Attitude " not in path.read_bytes():
        time.sleep(0.02)
    assert b" Attitude " in path.read_bytes()
    rec.stop()


def test_source_connected_line(tmp_path):
    rec, bus, now = make(tmp_path)
    path = rec.start().path
    rec.source_connected({"id": "usb", "kind": "vehicle_usb", "device": "/dev/ttyACM0", "rc_role": "control"})
    rec.stop()
    assert "#source id=usb kind=vehicle_usb device=/dev/ttyACM0 rc_role=control" in path.read_text()


def test_label_is_safe_in_file_name(tmp_path):
    rec, bus, now = make(tmp_path)
    info = rec.start("../x/y: z")
    rec.stop()
    assert info.path.parent == tmp_path and "/" not in info.name[1:]
    assert LogReader(info.path).header()["label"] == "../x/y: z"


def test_unknown_packet_roundtrip(tmp_path):
    from exvectr_gs.packets.base import UnknownPacket

    rec, bus, now = make(tmp_path)
    path = rec.start().path
    bus.publish(UnknownPacket(t=T0, src="usb", type="Future", raw_fields={"a": "1", "b": '"x y"'}))
    rec.stop()
    got = [p for _, p in LogReader(path).iter_packets()]
    u = [p for p in got if isinstance(p, UnknownPacket)][0]
    assert u.type == "Future" and u.raw_fields == {"a": "1", "b": '"x y"'}
    assert "#type Future" not in path.read_text()


# ---------------------------------------------------------------- disk full


class FullAfter:
    """File wrapper that raises ENOSPC on flush once more than `limit` bytes were written."""

    def __init__(self, fh, limit):
        self._fh, self._limit, self._n = fh, limit, 0

    def write(self, data):
        self._n += len(data)
        if self._n > self._limit:
            raise OSError(errno.ENOSPC, "No space left on device")
        return self._fh.write(data)

    def __getattr__(self, name):
        return getattr(self._fh, name)


def test_disk_full_stops_recorder_and_reports(tmp_path):
    errors = []
    rec, bus, now = make(
        tmp_path,
        opener=lambda p: FullAfter(open(p, "xb"), 1500),
        on_error=errors.append,
        flush_interval_ms=20,
    )
    info = rec.start()
    for i in range(200):
        bus.publish(att(T0 + i))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not rec.status().last_error:
        time.sleep(0.02)
    st = rec.status()
    assert not st.active and "No space left" in st.last_error
    assert len(errors) == 1 and "No space left" in errors[0]
    bus.publish(att(T0 + 999))  # core keeps running, the bus keeps working
    # the partial file is still a valid, readable log
    assert len(list(LogReader(info.path).iter_packets())) >= 1
    stopped = rec.stop()  # cleans up; does not raise or hang
    assert stopped is not None and not rec.status().active
    assert rec.status().last_error is not None
    rec2, _, _ = make(tmp_path)  # a new recording can be started by the user afterwards
    rec2.start()
    rec2.stop()


def test_queue_overflow_counts_dropped(tmp_path):
    rec, bus, now = make(tmp_path, max_queue=3, flush_interval_ms=1000)
    import threading

    gate = threading.Event()
    rec.start()
    orig = rec._session._write_packet
    rec._session._write_packet = lambda p: (gate.wait(2), orig(p))[1]
    for i in range(50):
        bus.publish(att(T0 + i))
    assert rec.status().dropped > 0
    gate.set()
    rec.stop()


# ---------------------------------------------------------------- crash


CHILD = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from exvectr_gs.core.bus import Bus
    from exvectr_gs.packets import REGISTRY
    from exvectr_gs.packets.defs.vehicle import Attitude
    from exvectr_gs.recording import Recorder

    bus = Bus()
    rec = Recorder(Path(sys.argv[1]), REGISTRY, bus, {}, flush_interval_ms=100, fsync_interval_ms=1000)
    info = rec.start("crash")
    print(info.path, flush=True)
    while True:
        bus.publish(Attitude(t=time.time_ns(), src="usb", omega=[0.0, 0.0, 0.0], q=[1.0, 0.0, 0.0, 0.0]))
        time.sleep(0.001)
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="needs SIGKILL")
def test_crash_loses_at_most_flush_interval(tmp_path):
    proc = subprocess.Popen([sys.executable, "-I", "-c", CHILD, str(tmp_path)], stdout=subprocess.PIPE, text=True)
    try:
        path = proc.stdout.readline().strip()
        time.sleep(1.5)
        kill_t = time.time_ns()
        os.kill(proc.pid, signal.SIGKILL)
    finally:
        proc.wait(timeout=10)
        proc.stdout.close()
    reader = LogReader(__import__("pathlib").Path(path))
    pkts = [p for _, p in reader.iter_packets()]
    assert len(pkts) > 500
    last = max(p.t for p in pkts)
    lost_ms = (kill_t - last) / 1e6
    assert lost_ms <= 100 + 150, f"lost {lost_ms:.0f} ms"  # 100 ms flush interval plus scheduling margin
    trailer = open(path, "rb").read().rstrip(b"\n").split(b"\n")[-1]
    assert not trailer.startswith(b"#end")  # incomplete, but fully readable
    assert reader.bad_lines == []
