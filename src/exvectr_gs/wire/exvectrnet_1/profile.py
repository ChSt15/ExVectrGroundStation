"""WireProfile `exvectrnet-1`: ExVectrNetwork stream frames, heartbeats and generated packets.

Layers inside the profile: `framing.NetFramer` (bytes to network/transport frames), this module
(presence, version check, duplicate suppression, port to packet class via `exvectr_packets`) and
`mapping` (wire packets to ground-station semantic packets). The profile is shared by all sources, so
per-link state (presence, sequence numbers) is keyed by the source id passed to `decode_many`.
"""

from __future__ import annotations

import logging
import math
import re
import struct

from . import _deps  # noqa: F401

import exvectr_packets as xp  # noqa: E402  isort: skip

from ...core.rates import FrameInfo, UplinkItem
from ...packets import REGISTRY, Direction, Packet
from ...packets.defs.host import GsEvent, GsEventKind, RawFrame
from ...packets.defs.link import NetworkStatus, RcOrigin, RcState
from ...packets.defs.link import Request as RequestPacket
from ...packets.defs.link import Telecommand as TelecommandPacket
from ..profile import Frame
from . import mapping
from .framing import BROADCAST, DEFAULT_TTL, HEARTBEAT_CHANNEL, NET_VERSION, NetFramer

log = logging.getLogger(__name__)

NODE_GS = 0
NODE_VEHICLE = 1
NODE_ROUTER = 2
AIR_NODES = (1, 3)  # frames from these nodes seen through the Router crossed the air
NODES = 4  # 0 ground station, 1 vehicle, 2 link TX, 3 link RX
PRESENCE_S = 3.0  # network-design 3.3.4
DUP_RESET_S = 1.0  # network-design 3.4: nothing on (src, port) for 1 s accepts the next frame
GS_RC_PORT = xp.RcState.PORT
GS_RC_PORT_HANDSET = GS_RC_PORT  # the same port carries the Pocket's copy (handset origin) towards the GS
# TEMPORARY legacy telecommand (flight-software docs/project/hardware-and-drivers.md section 10): port 0x7F, not in
# the packet schema. 8 bytes little-endian: type u8, subsystem u8, paramInt i16, paramFloat f32. DATA frame,
# QoS EVENT (2), not latest, TTL 3, DST 1, SRC 0 (CTRL byte 0x48). Remove with the flight software's T9b.
TELECOMMAND_PORT = 0x7F
RC_AIR_HZ = 25.0  # air profile v1: one RC_STATE in every uplink air packet
TELECOMMAND_QOS = 2


class ExVectrNet1Profile:
    name = "exvectrnet-1"

    def __init__(self) -> None:
        self.duplicates_dropped = 0
        self._last_seen: dict[tuple[str, int], int] = {}  # (source id, node) -> host t of last valid frame
        self._last_seq: dict[tuple[str, int, int], tuple[int, int]] = {}  # (source, node, port) -> (seq, t)
        self._incompat: dict[tuple[str, int], str] = {}  # (source, node) -> what differs
        self._tx_seq: dict[int, int] = {}  # port -> next sequence number
        self._gnss: dict[tuple[str, int], dict[str, dict]] = {}  # (source, node) -> last "pos" / "info" fields

    def make_framer(self) -> NetFramer:
        return NetFramer(node_id=NODE_GS)

    # ---- receive

    def decode(self, frame: Frame, src: str) -> Packet:
        """First packet of `decode_many`, or a RawFrame if the frame was dropped as a duplicate."""
        pkts = self.decode_many(frame, src)
        return pkts[0] if pkts else self._raw(frame, src, "duplicate frame (sequence rule)")

    def decode_many(self, frame: Frame, src: str) -> list[Packet]:
        """All semantic packets of one frame. Never raises. Empty list: duplicate, suppressed."""
        try:
            return self._decode(frame, src)
        except Exception as e:  # contract: never raises
            return [self._raw(frame, src, f"decode error: {e}")]

    @staticmethod
    def _raw(frame: Frame, src: str, err: str) -> Packet:
        return RawFrame(
            t=frame.t, src=src, channel=frame.channel, len=len(frame.payload), hex=frame.payload.hex(), err=err
        )

    def _decode(self, frame: Frame, src: str) -> list[Packet]:
        node = int(frame.meta.get("src", -1))
        if 0 <= node < NODES:
            self._last_seen[(src, node)] = frame.t
        if frame.channel == HEARTBEAT_CHANNEL:
            return self._heartbeat(frame, src, node)
        if self._is_duplicate(frame, src, node):
            self.duplicates_dropped += 1
            return []
        cls = xp.PACKETS_BY_PORT.get(frame.channel)
        if cls is None:
            return [self._raw(frame, src, "unknown port")]
        try:
            wire = cls.decode(frame.payload)
        except ValueError as e:
            return [self._raw(frame, src, str(e))]
        out = (
            mapping.MAPPERS[cls](wire, frame.t, src, {**frame.meta, "len": len(frame.payload)})
            if cls in mapping.MAPPERS
            else "no mapping"
        )
        if isinstance(out, str):
            return [self._raw(frame, src, f"{cls.__name__}: {out}")]
        if cls in (xp.AirGnss, xp.AirGnssInfo):
            out = self._merge_gnss(out[0], cls is xp.AirGnss, src, node)
        return out

    def _merge_gnss(self, gps: Packet, is_pos: bool, src: str, node: int) -> list[Packet]:
        """AIR_GNSS (10 Hz position and velocity) and AIR_GNSS_INFO (1 Hz fix type, satellites, accuracy) feed one
        `Gps` per source. Every AIR_GNSS emits a Gps carrying the last info values (None until the first
        AIR_GNSS_INFO; `info_t` is the host time of that info, so a panel can show its age). AIR_GNSS_INFO only
        updates the cache and emits nothing: re-emitting the last position under a new time stamp would make a
        stale position look fresh."""
        last = self._gnss.setdefault((src, node), {"pos": {}, "info": {}})
        names = ("lat", "lon", "alt", "vel") if is_pos else ("num_sats", "fix_type", "pos_acc", "alt_acc", "info_t")
        last["pos" if is_pos else "info"] = {n: getattr(gps, n) for n in names}
        if not is_pos:
            return []
        return [type(gps)(t=gps.t, src=gps.src, **{**last["pos"], **last["info"]})]

    def _is_duplicate(self, frame: Frame, src: str, node: int) -> bool:
        """Receiver rule of network-design 3.4: accept if (seq - last) mod 256 is in 1..127."""
        seq = frame.meta.get("seq")
        if seq is None:
            return False
        key = (src, node, frame.channel)
        prev = self._last_seq.get(key)
        accept = prev is None or frame.t - prev[1] > DUP_RESET_S * 1e9 or 1 <= (seq - prev[0]) % 256 <= 127
        if accept:
            self._last_seq[key] = (seq, frame.t)
        return not accept

    def _heartbeat(self, frame: Frame, src: str, node: int) -> list[Packet]:
        if not 0 <= node < NODES:
            return [self._raw(frame, src, f"heartbeat from unknown node {node}")]
        hb = xp.Heartbeat.decode(frame.payload)
        local = xp.SCHEMA_HASH
        diffs = []
        if hb.net_version != NET_VERSION:
            diffs.append(f"network version {hb.net_version} (GS {NET_VERSION})")
        if hb.schema_hash != local:
            diffs.append(f"schema hash 0x{hb.schema_hash:08X} (GS 0x{local:08X})")
        key = (src, node)
        detail = f"node {node}: " + ", ".join(diffs) if diffs else ""
        out: list[Packet] = []
        if detail != self._incompat.get(key, ""):
            self._incompat[key] = detail
            msg = detail or f"node {node}: compatible again"
            (log.warning if detail else log.info)("%s: %s", src, msg)
            out.append(GsEvent(t=frame.t, src="gs", kind=GsEventKind.link_warning, detail=f"{src}: {msg}"))
        present, age = [], []
        for n in range(NODES):
            seen = self._last_seen.get((src, n))
            a = (frame.t - seen) / 1e9 if seen is not None else math.nan
            age.append(a)
            present.append(int(seen is not None and a <= PRESENCE_S))
        problems = [d for (s, n), d in self._incompat.items() if s == src and d and present[n]]
        status = NetworkStatus(
            t=frame.t,
            src=src,
            node=node,
            role=int(hb.role),
            net_version=hb.net_version,
            schema_hash=hb.schema_hash,
            uptime=hb.uptime,
            present=present,
            age=age,
            incompatible=bool(problems),
            local_schema_hash=local,
            detail="; ".join(problems),
        )
        return [status, *out]

    def describe_frame(self, frame: Frame) -> FrameInfo:
        """For the data-rate view: wire packet name, origin node and whether the frame crossed the air when it
        arrives through the Router (nodes 1 and 3 did; node 2 is the Router itself; the Pocket's RC_STATE copy is
        Router-local whatever its SRC)."""
        node = int(frame.meta.get("src", -1))
        cls = xp.PACKETS_BY_PORT.get(frame.channel)
        if frame.channel == HEARTBEAT_CHANNEL:
            name, air, usb = "HEARTBEAT", 0, 0
        elif cls is None:
            name, air, usb = f"port 0x{frame.channel:02X}", 0, 0
        else:
            name = re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).upper()
            air, usb = cls.AIR_RATE_HZ, cls.USB_RATE_HZ
        if frame.channel == GS_RC_PORT_HANDSET or node == NODE_ROUTER:
            remote: bool | None = False
        elif node in AIR_NODES:
            remote = True
        else:
            remote = None
        return FrameInfo(name, node if node >= 0 else None, remote, float(air), float(usb))

    def uplink_air_items(self) -> list[UplinkItem]:
        """What the Router sends to the ship radio node (for the ESTIMATED uplink usage): the Pocket's RC_STATE once per
        air packet (25 Hz, air profile v1; the schema rate is 0 because the Router offers copies on its USB), the
        Router's LINK_CONFIG and its heartbeat, at their schema air rates and sizes."""
        items = [UplinkItem("RC_STATE", RC_AIR_HZ, xp.RcState.MAX_SIZE, needs_rc=True)]
        for cls in (xp.LinkConfig, xp.Heartbeat):
            if cls.AIR_RATE_HZ:
                name = re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).upper()
                items.append(UplinkItem(name, float(cls.AIR_RATE_HZ), cls.MAX_SIZE))
        return items

    # ---- send

    def encode(self, pkt: Packet) -> Frame | None:
        """Only three things are encodable (design 1.4): RC_STATE with origin gs, addressed to the vehicle (node 1,
        whether the GS sits on the vehicle's USB or behind the Router), a REQUEST (the request controller's
        whitelisted one-shot actions) addressed to its `target` node, and, TEMPORARILY (user decision 2026-11-20),
        the legacy Telecommand to the vehicle. Everything else is not sent. The ground station's own heartbeat is
        not a packet: it has its own method, `encode_heartbeat`."""
        try:
            if REGISTRY.get(pkt.TYPE).direction is not Direction.UP:
                return None
        except KeyError:
            return None
        if isinstance(pkt, RequestPacket):
            return self._encode_request(pkt)
        if isinstance(pkt, TelecommandPacket):
            return self._encode_telecommand(pkt)
        if not isinstance(pkt, RcState) or pkt.origin != RcOrigin.gs or pkt.ch_us is None or len(pkt.ch_us) != 16:
            return None
        R = xp.RcState
        ch = [R.CH_INVALID if us <= 0 else min(max((int(us) - 1500) * 2, R.CH_MIN), R.CH_MAX) for us in pkt.ch_us]
        flags = R.FLAGS_SOURCE_OK if pkt.source_ok in (None, True) else 0
        try:
            payload = R(flags=flags, ch=ch).encode()
        except (ValueError, TypeError):
            return None
        seq = self._tx_seq.get(GS_RC_PORT, 0)
        self._tx_seq[GS_RC_PORT] = (seq + 1) & 0xFF
        meta = {"dst": NODE_VEHICLE, "seq": seq, "qos": R.QOS, "latest": R.LATEST}
        return Frame(channel=GS_RC_PORT, payload=payload, t=pkt.t, meta=meta)

    def _encode_telecommand(self, pkt: TelecommandPacket) -> Frame | None:
        """TEMPORARY. One 8-byte legacy `Telecommand` to the vehicle (node 1)."""
        try:
            payload = struct.pack(
                "<BBhf",
                int(pkt.tc_type),
                int(pkt.subsystem or 0),
                int(pkt.param_int or 0),
                float(pkt.param_float or 0.0),
            )
        except (struct.error, TypeError, ValueError):
            return None
        seq = self._tx_seq.get(TELECOMMAND_PORT, 0)
        self._tx_seq[TELECOMMAND_PORT] = (seq + 1) & 0xFF
        meta = {"dst": NODE_VEHICLE, "seq": seq, "qos": TELECOMMAND_QOS, "latest": False}
        return Frame(channel=TELECOMMAND_PORT, payload=payload, t=pkt.t, meta=meta)

    def _encode_request(self, pkt: RequestPacket) -> Frame | None:
        if pkt.req_id is None or not 1 <= int(pkt.req_id) <= 0xFFFF or pkt.action is None or pkt.target is None:
            return None
        Q = xp.Request
        try:
            payload = Q(
                req_id=int(pkt.req_id),
                action=int(pkt.action),
                target=int(pkt.target),
                arg_i=int(pkt.arg_i or 0),
                arg_f=float(pkt.arg_f or 0.0),
            ).encode()
        except (ValueError, TypeError):
            return None
        seq = self._tx_seq.get(Q.PORT, 0)
        self._tx_seq[Q.PORT] = (seq + 1) & 0xFF
        meta = {"dst": int(pkt.target), "seq": seq, "qos": Q.QOS, "latest": Q.LATEST}
        return Frame(channel=Q.PORT, payload=payload, t=pkt.t, meta=meta)

    def encode_heartbeat(self, uptime_s: float) -> Frame:
        """The ground station's own heartbeat (network-design 4.5): role 0, network version, schema hash
        of the generated module, uptime, flags 0. Broadcast, TTL 3, QoS LINK, LATEST (spec values,
        from the generated class). It carries presence information only, never control data."""
        H = xp.Heartbeat
        ms = max(int(uptime_s * 1000), 0) & 0xFFFFFFFF
        payload = H(
            role=xp.HeartbeatRole.GROUND_STATION,
            net_version=NET_VERSION,
            schema_hash=xp.SCHEMA_HASH,
            uptime=ms / 1000,
            flags=0,
        ).encode()
        meta = {"dst": BROADCAST, "src": NODE_GS, "ttl": DEFAULT_TTL, "qos": H.QOS, "latest": H.LATEST}
        return Frame(channel=HEARTBEAT_CHANNEL, payload=payload, t=0, meta=meta)

    def supported_types(self) -> set[str]:
        return set(mapping.PRODUCED_TYPES)


def make_profile() -> ExVectrNet1Profile:
    return ExVectrNet1Profile()
