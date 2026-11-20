import exvectr_packets as xp
import pytest


@pytest.fixture
def zeroing_mask(monkeypatch):
    """The pinned schema has no ZEROING flag yet: give the mapping a fake mask (bit 7), as the later pin will."""
    monkeypatch.setattr(xp.VehicleStatus, "FLAGS_ZEROING", 128, raising=False)
