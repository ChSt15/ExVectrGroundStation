import pytest

from exvectr_gs.core import Bus, FakeClock, History, MonotonicClock
from exvectr_gs.core.config import ConfigError, load_config, parse_config
from exvectr_gs.packets import REGISTRY
from exvectr_gs.packets.defs.host import Note
from exvectr_gs.packets.defs.vehicle import Attitude, Position

S = 1_000_000_000


def test_bus_dispatch_filter_unsubscribe_and_error_isolation():
    bus, got_all, got_att = Bus(), [], []
    unsub = bus.subscribe(got_all.append)
    bus.subscribe(got_att.append, {"Attitude"})

    def bad(_):
        raise RuntimeError("boom")

    bus.subscribe(bad)
    a, n = Attitude(t=1, src="x"), Note(t=2, src="x", text="hi")
    bus.publish(a)
    bus.publish(n)
    assert got_all == [a, n] and got_att == [a] and bus.handler_errors == 2
    unsub()
    bus.publish(a)
    assert len(got_all) == 2


def test_clocks():
    c = MonotonicClock()
    a, b = c.now(), c.now()
    assert b >= a and abs(a - __import__("time").time_ns()) < 5 * S
    fc = FakeClock(10)
    fc.advance(1.5)
    assert fc.now() == 10 + int(1.5 * S)


def test_history_only_history_types_downsample_window_query():
    h = History(REGISTRY, window_s=10, max_hz=10)
    for i in range(300):  # 100 Hz for 3 s
        h.update(Position(t=i * S // 100, src="usb", pos=[float(i), 0, 0]))
    h.update(Note(t=0, src="usb", text="x"))
    r = h.query("Position", "usb", ["pos"], 0, 10 * S, 1000)
    assert 29 <= len(r["t"]) <= 31 and len(r["pos"]) == len(r["t"])  # downsampled to 10 Hz
    assert h.query("Note", None, ["text"], 0, 10 * S, 10)["t"] == []  # history=False
    r = h.query("Position", None, ["pos"], 1 * S, 2 * S, 1000)
    assert all(1 * S <= t <= 2 * S for t in r["t"])
    assert len(h.query("Position", "usb", [], 0, 10 * S, 5)["t"]) == 5
    h.update(Position(t=60 * S, src="usb", pos=[0, 0, 0]))  # old samples fall out of the window
    assert h.query("Position", "usb", [], 0, 100 * S, 100)["t"] == [60 * S]


def test_history_keeps_sources_separate():
    h = History(REGISTRY)
    h.update(Attitude(t=0, src="usb", q=[1, 0, 0, 0]))
    h.update(Attitude(t=1, src="link", q=[0, 1, 0, 0]))
    assert h.query("Attitude", "usb", ["q"], 0, S, 10)["q"] == [[1, 0, 0, 0]]
    assert h.query("Attitude", None, ["q"], 0, S, 10)["q"] == [[0, 1, 0, 0]]  # newest source


BASE = {"source": [{"id": "a", "kind": "fake"}], "core": {"source_priority": ["a"]}}


def test_example_config_loads():
    cfg = load_config("config/gs.example.toml")
    assert [s.id for s in cfg.sources] == ["usb", "link", "fake"]
    assert cfg.sources[2].options["profile"] == "usb" and cfg.sources[0].match["vid"] == "16c0"
    assert sum(s.rc_role == "control" for s in cfg.sources) == 1


def _bad(**over):
    data = {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in BASE.items()}
    data.update(over)
    with pytest.raises(ConfigError):
        parse_config(data)


def test_config_validation():
    parse_config(BASE)
    _bad(source=[{"id": "a", "kind": "fake"}, {"id": "a", "kind": "fake"}])
    _bad(source=[{"id": "gs", "kind": "fake"}], core={"source_priority": []})
    _bad(source=[{"id": "a", "kind": "usb2"}])
    _bad(source=[{"id": "a", "kind": "fake", "rc_role": "boss"}])
    _bad(source=[{"id": "a", "kind": "fake", "rc_role": "control"}, {"id": "b", "kind": "fake", "rc_role": "control"}],
         core={"source_priority": []})  # fmt: skip
    _bad(core={"source_priority": ["ghost"]})
    _bad(recorder={"raw_frames": "all"})
    _bad(recorder={"auto_start": True})
    _bad(server={"port": 0})
    _bad(bogus={})
    _bad(source=[{"kind": "fake"}])


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")
