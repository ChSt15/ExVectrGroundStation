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
    desc="RC channel state. Sent by the GS (origin gs) or mirrored from the handset via the Router. "
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


class RequestAction(IntEnum):
    """Mirror of the `RequestAction` enum of the packet schema (a test compares the two). The ground station
    does not interpret an action: it only sends the ones its whitelist names."""

    NONE = 0
    CALIB_GYRO_START = 1
    CALIB_GYRO_STOP = 2
    CALIB_ACCEL_START = 3
    CALIB_ACCEL_STOP = 4
    CALIB_MAG_START = 5
    CALIB_MAG_STOP = 6
    CALIB_APPLY = 7
    SAVE_CONFIG = 8
    SYSTEM_RESET = 9
    LINK_UPLOAD_MODE = 10
    LINK_REBOOT = 11


class RequestStatus(IntEnum):
    """Mirror of the schema's `RequestStatus` (REQUEST_ACK status). Shown exactly as the vehicle reports it."""

    DONE = 0
    IN_PROGRESS = 1
    REJECTED_STATE = 2
    REJECTED_INVALID = 3
    FAILED = 4


@packet(
    "Request",
    version=1,
    direction=Direction.UP,
    history=False,
    desc="One-shot REQUEST (whitelisted action with an idempotent req_id, sent and retried by the request "
    "controller, never by hand). target = destination node id (1 vehicle, 2 Router, 3 ship radio node).",
)
class Request(Packet):
    req_id: int = f(desc="non-zero, new per action, reused on every retry")
    action: RequestAction = f()
    target: int = f(desc="destination node id")
    arg_i: int = f()
    arg_f: float = f()


@packet(
    "LinkConfig",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=2,
    history=False,
    desc="LINK_CONFIG (port 0x04) as seen on a link: the Router's commanded configuration for the ship radio "
    "node. The Router drops it from USB, so the ground station normally receives it only if a source sees the "
    "air side (display only, never sent by the ground station).",
)
class LinkConfig(Packet):
    tx_power_dbm: int = f(unit="dBm", desc="requested RF output power of the ship radio node")
    flags: int = f(desc="reserved, 0")
    from_node: int | None = f(desc="ExVectrNetwork node that sent it (2 = Router)")


class TelecommandType(IntEnum):
    """The legacy `TelecommandType` of the flight software (src/telecommand.hpp). TEMPORARY, see
    docs/telecommands.md."""

    CalibStart = 0
    CalibApply = 1
    Load = 2
    Save = 3
    SystemReset = 4
    Arm = 5
    SimulationMode = 6
    MissionBegin = 7
    MissionSelect = 8
    MissionAbort = 9


@packet(
    "Telecommand",
    version=1,
    direction=Direction.UP,
    history=False,
    desc="TEMPORARY legacy 8-byte telecommand (port 0x7F), sent once per deliberate confirmation by the "
    "telecommand controller, never by hand and never repeated. Delete with the flight software's T9b.",
)
class Telecommand(Packet):
    tc_type: TelecommandType = f(desc="legacy command code")
    subsystem: int = f(desc="legacy Subsystem (0 gyro, 1 accel, 2 magneto, ...)")
    param_int: int = f(desc="int16 parameter (magic value or mission time)")
    param_float: float = f(desc="unused by every vehicle handler")


@packet(
    "RadioLinkStats",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=2,
    history=False,
    desc="Planned type. Link quality as reported by the Router.",
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
    desc="LINK_STATS from the Router (node 2, the Pocket module; type name kept). Also fills RadioLinkStats.",
)
class TxModuleStats(_ModuleStats):
    pass


@packet(
    "RxModuleStats",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=2,
    history=False,
    desc="LINK_STATS from the ship radio node (node 3, type name kept), forwarded by the Router.",
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
