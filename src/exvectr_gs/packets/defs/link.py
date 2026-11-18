"""Packet types related to the radio link and the virtual radio."""

from dataclasses import dataclass
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
    desc="RC channel state. Sent by the GS (origin gs) or mirrored from the handset via the TX module. "
    "ch_us 0 = channel not provided (exvectrnet-1).",
)
class RcState(Packet):
    origin: RcOrigin = f()
    ch_us: list[int] = f(
        unit="us", length=16, desc="channel pulse widths, 1000-2000 (wire -1000..1000 maps to 1000..2000)"
    )
    seq: int = f(desc="wire sequence number (exvectrnet-1) or the sender's counter (provisional-1)")
    flags: int | None = f(desc="generic flag byte of the wire packet (exvectrnet-1), as sent or received")
    source_ok: bool | None = f(desc="SOURCE_OK flag: the input is fresh (exvectrnet-1)")


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


@dataclass(kw_only=True, eq=False)
class _ModuleStats(Packet):
    """Fields of LINK_STATS as sent by a link module (nodes 2 and 3)."""

    module_uptime: float = f(unit="s")
    air_state: int = f(desc="0 off, 1 searching, 2 syncing, 3 synced")
    rx_lq: int = f(unit="%", desc="valid air packets in the last 1 s, percent of expected")
    rssi: int = f(unit="dBm", desc="of the last valid packet")
    snr: int = f(unit="dB", desc="of the last valid packet")
    tx_power: int = f(unit="dBm")
    rx_antenna: int = f(desc="RX winner")
    tx_antenna: int = f()
    air_rx_ok: int = f(desc="cumulative, wraps at 65536")
    air_rx_bad: int = f(desc="cumulative, wraps at 65536")
    frames_dropped: int = f(desc="queue full, cumulative")
    frames_expired: int = f(desc="cumulative")
    queue_peak: int = f(unit="%", desc="peak queue fill in the last 1 s")


@packet(
    "TxModuleStats",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=2,
    history=False,
    desc="LINK_STATS from the TX module (node 2). Also fills RadioLinkStats.",
)
class TxModuleStats(_ModuleStats):
    pass


@packet(
    "RxModuleStats",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=2,
    history=False,
    desc="LINK_STATS from the vehicle RX module (node 3), forwarded by the TX module.",
)
class RxModuleStats(_ModuleStats):
    pass


@packet(
    "RequestAck",
    version=1,
    direction=Direction.DOWN,
    history=False,
    desc="Vehicle acknowledgement of a REQUEST (status 0 done, 1 in progress, 2 rejected state, 3 invalid, 4 failed).",
)
class RequestAck(Packet):
    req_id: int = f()
    status: int = f()
    detail: int = f()


@packet(
    "NetworkStatus",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=3,
    history=False,
    desc="Node presence and version check from ExVectrNetwork heartbeats (exvectrnet-1), one per heartbeat.",
)
class NetworkStatus(Packet):
    node: int = f(desc="sender of this heartbeat")
    role: int = f(desc="0 ground station, 1 vehicle, 2 link TX, 3 link RX")
    net_version: int = f()
    schema_hash: int = f(desc="sender's schema hash")
    uptime: float = f(unit="s")
    present: list[int] = f(length=4, labels=("gs", "vehicle", "tx", "rx"), desc="1 = a valid frame within 3 s")
    age: list[float] = f(unit="s", length=4, labels=("gs", "vehicle", "tx", "rx"), desc="NaN = never seen")
    incompatible: bool = f(desc="any present node has another network version or schema hash than this GS")
    local_schema_hash: int = f(desc="schema hash of the exvectr_packets this GS was built with")
    detail: str = f(desc="what differs, empty if compatible")
