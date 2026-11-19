import pytest

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
    assert load_profile("provisional-1").name == "provisional-1"
