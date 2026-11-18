"""Provisional COBS stream framing (profile `provisional-1`).

Stream grammar (provisional, deliberately simple, replaced by the real ExVectrNetwork stream framing):

    stream  := ( console-text | frame )*
    frame   := 0x00 cobs(body) 0x00
    body    := channel u16 LE | len u16 LE | payload[len] | crc16 u16 LE
    crc16   := CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection) over channel..payload

COBS output never contains 0x00, and console text (`Serial.print`) never contains 0x00, so a zero byte
always marks a frame boundary. Bytes outside frames are console text, split into lines on `\\n`.
A leading and a trailing delimiter are both sent, so back-to-back frames share no ambiguity and a
frame that starts mid-garbage still resynchronises on its leading delimiter.
"""

from __future__ import annotations

import binascii
import struct
from collections.abc import Iterator

from ..profile import ConsoleText, Frame, FramerStats

MAX_PAYLOAD = 1024
MAX_BODY = 4 + MAX_PAYLOAD + 2
MAX_ENCODED = MAX_BODY + MAX_BODY // 254 + 2
MAX_TEXT_LINE = 1024


def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


def cobs_encode(data: bytes) -> bytes:
    out = bytearray()
    block = bytearray()
    for b in data:
        if b == 0:
            out.append(len(block) + 1)
            out += block
            block.clear()
        else:
            block.append(b)
            if len(block) == 254:
                out.append(255)
                out += block
                block.clear()
    out.append(len(block) + 1)
    out += block
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    """Raises ValueError on malformed input (zero byte inside, truncated block)."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        code = data[i]
        if code == 0:
            raise ValueError("zero byte inside COBS data")
        end = i + code
        if end > n:
            raise ValueError("truncated COBS block")
        block = data[i + 1 : end]
        if 0 in block:
            raise ValueError("zero byte inside COBS data")
        out += block
        i = end
        if code != 255 and i < n:
            out.append(0)
    return bytes(out)


def encode_frame(channel: int, payload: bytes) -> bytes:
    if not 0 <= channel <= 0xFFFF:
        raise ValueError("channel out of range")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too long")
    body = struct.pack("<HH", channel, len(payload)) + payload
    body += struct.pack("<H", crc16(body))
    return b"\x00" + cobs_encode(body) + b"\x00"


class ProvisionalFramer:
    def __init__(self) -> None:
        self.stats = FramerStats()
        self._in_frame = False
        self._buf = bytearray()
        self._text = bytearray()

    def reset(self) -> None:
        """Drop partial state (stats are kept: they count the whole session)."""
        self._in_frame = False
        self._buf.clear()
        self._text.clear()

    def encode(self, frame: Frame) -> bytes:
        return encode_frame(frame.channel, frame.payload)

    def feed(self, data: bytes, t: int) -> list[Frame | ConsoleText]:
        self.stats.bytes += len(data)
        return list(self._scan(data, t))

    def _scan(self, data: bytes, t: int) -> Iterator[Frame | ConsoleText]:
        for b in data:
            if b == 0:
                if self._in_frame:
                    if self._buf:
                        yield from self._finish(t)
                    # empty body: closing delimiter directly followed by the next leading one
                else:
                    yield from self._flush_text(t)
                    self._in_frame = True
                continue
            if self._in_frame:
                self._buf.append(b)
                if len(self._buf) > MAX_ENCODED:
                    self.stats.resyncs += 1
                    self._buf.clear()
                    self._in_frame = False
            elif b == 0x0A:
                yield from self._flush_text(t)
            else:
                self._text.append(b)
                if len(self._text) >= MAX_TEXT_LINE:
                    yield from self._flush_text(t)

    def _flush_text(self, t: int) -> Iterator[ConsoleText]:
        if self._text:
            line = self._text.decode("utf-8", errors="replace").rstrip("\r")
            self._text.clear()
            if line:
                yield ConsoleText(text=line, t=t)

    def _finish(self, t: int) -> Iterator[Frame]:
        encoded = bytes(self._buf)
        self._buf.clear()
        # A good frame's trailing delimiter returns to text mode. A bad one stays in frame mode: its
        # closing 0x00 may really be the leading delimiter of the next frame (we lost sync mid-frame).
        self._in_frame = True
        try:
            body = cobs_decode(encoded)
        except ValueError:
            self.stats.resyncs += 1
            return
        if len(body) < 6:
            self.stats.resyncs += 1
            return
        channel, length = struct.unpack_from("<HH", body)
        if len(body) != 4 + length + 2:
            self.stats.resyncs += 1
            return
        (crc,) = struct.unpack_from("<H", body, len(body) - 2)
        if crc != crc16(body[:-2]):
            self.stats.crc_errors += 1
            return
        self._in_frame = False
        self.stats.frames += 1
        yield Frame(channel=channel, payload=body[4:-2], t=t, meta={})
