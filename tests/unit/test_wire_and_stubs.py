import pytest

from exvectr_gs.api import ApiServer
from exvectr_gs.core import Bus, History, StateStore
from exvectr_gs.packets import REGISTRY
from exvectr_gs.rc import RadioConfig, RcController
from exvectr_gs.recording import LogLibrary
from exvectr_gs.replay import Replayer
from exvectr_gs.wire import Frame, ProfileNotFound, load_profile


def test_fake_profile_contract():
    p = load_profile("fake")
    assert p.name == "fake" and p.supported_types() == set()
    framer = p.make_framer()
    assert list(framer.feed(b"abc", 0)) == [] and framer.stats.frames == 0
    framer.reset()
    pkt = p.decode(Frame(channel=7, payload=b"\x01\x02", t=5, meta={}), "fake")
    assert pkt.TYPE == "RawFrame" and pkt.hex == "0102" and pkt.channel == 7
    assert p.encode(pkt) is None


def test_unknown_profile():
    with pytest.raises(ProfileNotFound):
        load_profile("does-not-exist")
    with pytest.raises(ProfileNotFound):
        load_profile("provisional-1")  # arrives with T5


def test_stubs_raise_not_implemented(tmp_path):
    bus = Bus()
    lib = LogLibrary(tmp_path)
    rp = Replayer(lib, bus, StateStore(REGISTRY, []), History(REGISTRY))
    rc = RcController(RadioConfig(), [], bus)
    calls = [
        lambda: rp.close(), lambda: rp.play(), lambda: rp.pause(), lambda: rp.seek(0), lambda: rp.set_speed(1),
        lambda: rp.status(),
        lambda: rc.acquire("me", "timed", 1.0), lambda: rc.release("x"), lambda: rc.heartbeat("x"),
        lambda: rc.set("x", {}), lambda: rc.status(),
    ]  # fmt: skip
    for call in calls:
        with pytest.raises(NotImplementedError):
            call()


async def test_async_stubs_raise_not_implemented(tmp_path):
    bus = Bus()
    rp = Replayer(LogLibrary(tmp_path), bus, StateStore(REGISTRY, []), History(REGISTRY))
    rc = RcController(RadioConfig(), [], bus)
    api = ApiServer(None, "127.0.0.1", 0)
    for coro in (rp.open("x"), rc.run(), api.start(), api.stop()):
        with pytest.raises(NotImplementedError):
            await coro
