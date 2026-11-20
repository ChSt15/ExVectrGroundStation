"""ExVectrNetwork stream framing and network/transport headers (docs/network-design.md 4.2 to 4.4).

    wire    := COBS(decoded) 0x00
    decoded := SKIND (0x01) | network frame (4..240 B) | CRC-16/CCITT-FALSE LE over SKIND..frame
    network := CTRL | TTL | DST | SRC | (DATA: PORT | SEQ | payload) or (HEARTBEAT: payload)
    CTRL    := VER(2) << 6 | TYPE(2) << 4 | QOS(2) << 2 | LATEST << 1 | reserved

COBS and CRC are the helpers of `provisional_1` (checked against the shared vectors in
`ExVectrNetwork/test/vectors/wire_v1.json`). The receiver drops a bad frame and nothing else: the
stream resynchronises on the next delimiter.
"""

from __future__ import annotations

import struct

from ..profile import ConsoleText, Frame, FramerStats
from ..provisional_1.framing import cobs_decode, cobs_encode, crc16

SKIND_NETWORK_V1 = 0x01
NET_VERSION = 1
TYPE_DATA = 0
TYPE_HEARTBEAT = 1
HEARTBEAT_CHANNEL = 0x100  # `Frame.channel` of a heartbeat: outside the 8-bit port range
BROADCAST = 0xFF
DEFAULT_TTL = 3
MAX_NET_FRAME = 240
MIN_DECODED = 7
MAX_DECODED = 243
MAX_ENCODED = 256  # discard more than this without a delimiter (spec 4.2)


def make_ctrl(type_: int, qos: int, latest: bool) -> int:
    return (NET_VERSION << 6) | (type_ << 4) | (qos << 2) | (int(latest) << 1)


def build_stream_frame(net_frame: bytes) -> bytes:
    """Wire bytes (COBS + delimiter) of one network frame."""
    if not 4 <= len(net_frame) <= MAX_NET_FRAME:
        raise ValueError(f"network frame length {len(net_frame)} outside 4..{MAX_NET_FRAME}")
    decoded = bytes([SKIND_NETWORK_V1]) + net_frame
    decoded += struct.pack("<H", crc16(decoded))
    return cobs_encode(decoded) + b"\x00"


def build_data_frame(
    src: int, dst: int, port: int, seq: int, payload: bytes, qos: int, latest: bool, ttl: int
) -> bytes:
    if not 0 <= port <= 0xFF:
        raise ValueError("port out of range")
    hdr = struct.pack("<BBBBBB", make_ctrl(TYPE_DATA, qos, latest), ttl, dst, src, port, seq & 0xFF)
    return hdr + payload


def parse_net_frame(frame: bytes, t: int, wire_len: int = 0) -> Frame | None:
    """Network frame to `Frame`. None for an unsupported version, reserved type or short frame."""
    if len(frame) < 4:
        return None
    ctrl, ttl, dst, src = frame[0], frame[1], frame[2], frame[3]
    if ctrl >> 6 != NET_VERSION:
        return None
    type_ = (ctrl >> 4) & 3
    meta = {"type": type_, "qos": (ctrl >> 2) & 3, "latest": bool(ctrl & 2), "ttl": ttl, "dst": dst, "src": src}
    if type_ == TYPE_DATA:
        if len(frame) < 6:
            return None
        meta["seq"] = frame[5]
        return Frame(channel=frame[4], payload=bytes(frame[6:]), t=t, meta=meta, wire_len=wire_len)
    if type_ == TYPE_HEARTBEAT:
        return Frame(channel=HEARTBEAT_CHANNEL, payload=bytes(frame[4:]), t=t, meta=meta, wire_len=wire_len)
    return None


class NetFramer:
    def __init__(self, node_id: int = 0) -> None:
        self.stats = FramerStats()
        self.node_id = node_id  # SRC of frames this framer encodes
        self._buf = bytearray()
        self._discarding = False

    def reset(self) -> None:
        """Drop partial state (stats are kept: they count the whole session)."""
        self._buf.clear()
        self._discarding = False

    def feed(self, data: bytes, t: int) -> list[Frame | ConsoleText]:
        self.stats.bytes += len(data)
        out: list[Frame | ConsoleText] = []
        for b in data:
            if b != 0:
                if self._discarding:
                    continue
                self._buf.append(b)
                if len(self._buf) > MAX_ENCODED:
                    self.stats.resyncs += 1
                    self._buf.clear()
                    self._discarding = True
                continue
            if self._discarding:
                self._discarding = False
            elif self._buf:
                frame = self._finish(t)
                if frame is not None:
                    out.append(frame)
            self._buf.clear()
        return out

    def _finish(self, t: int) -> Frame | None:
        try:
            dec = cobs_decode(bytes(self._buf))
        except ValueError:
            self.stats.resyncs += 1
            return None
        if not MIN_DECODED <= len(dec) <= MAX_DECODED:
            self.stats.resyncs += 1
            return None
        if struct.unpack_from("<H", dec, len(dec) - 2)[0] != crc16(dec[:-2]):
            self.stats.crc_errors += 1
            return None
        if dec[0] != SKIND_NETWORK_V1:
            self.stats.resyncs += 1
            return None
        frame = parse_net_frame(dec[1:-2], t, len(self._buf) + 1)  # + the 0x00 delimiter
        if frame is None:
            self.stats.resyncs += 1
            return None
        self.stats.frames += 1
        return frame

    def encode(self, frame: Frame) -> bytes:
        """DATA frames (`meta`: dst (default 1 = vehicle), seq, qos, latest, ttl, src) and heartbeats
        (`channel == HEARTBEAT_CHANNEL`, `meta`: qos, latest, ttl, dst (default broadcast), src)."""
        m = frame.meta
        if frame.channel == HEARTBEAT_CHANNEL:
            hdr = struct.pack(
                "<BBBB",
                make_ctrl(TYPE_HEARTBEAT, int(m.get("qos", 1)), bool(m.get("latest", True))),
                int(m.get("ttl", DEFAULT_TTL)),
                int(m.get("dst", BROADCAST)),
                int(m.get("src", self.node_id)),
            )
            return build_stream_frame(hdr + frame.payload)
        net = build_data_frame(
            src=int(m.get("src", self.node_id)),
            dst=int(m.get("dst", 1)),
            port=frame.channel,
            seq=int(m.get("seq", 0)),
            payload=frame.payload,
            qos=int(m.get("qos", 3)),
            latest=bool(m.get("latest", False)),
            ttl=int(m.get("ttl", DEFAULT_TTL)),
        )
        return build_stream_frame(net)
