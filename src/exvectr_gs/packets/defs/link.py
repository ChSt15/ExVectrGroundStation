"""Packet types related to the radio link and the virtual radio."""

from enum import IntEnum

from ..base import Direction, Packet
from ..fields import f
from ..registry import packet


class RcOrigin(IntEnum):
    gs = 0
    handset = 1


@packet(
    "RcState",
    version=1,
    direction=Direction.UP,
    nominal_rate_hz=25,
    history=False,
    desc="RC channel state. Sent by the GS (origin gs) or mirrored from the handset via the TX module.",
)
class RcState(Packet):
    origin: RcOrigin = f()
    ch_us: list[int] = f(unit="us", length=16, desc="channel pulse widths, 1000-2000")
    seq: int = f()


@packet(
    "RadioLinkStats",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=2,
    history=False,
    desc="Planned type. Link quality as reported by the TX module.",
)
class RadioLinkStats(Packet):
    rssi_dbm: list[float] = f(unit="dBm", length=2)
    lq_pct: float = f(unit="%")
    snr_db: float = f(unit="dB")
    up_hz: float = f(unit="Hz")
    down_hz: float = f(unit="Hz")
    antenna: int = f()
