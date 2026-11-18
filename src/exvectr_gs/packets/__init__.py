"""Semantic packet model: declarations, registry, text codec."""

from . import defs  # noqa: E402,F401  (populates REGISTRY)
from .base import Direction, Packet, UnknownPacket
from .fields import FieldSpec, f
from .registry import REGISTRY, PacketRegistry, PacketType, packet
from .textcodec import CodecError, GenericTextCodec, TextCodec, decode_line, encode_line

__all__ = [
    "REGISTRY",
    "CodecError",
    "Direction",
    "FieldSpec",
    "GenericTextCodec",
    "Packet",
    "PacketRegistry",
    "PacketType",
    "TextCodec",
    "UnknownPacket",
    "decode_line",
    "encode_line",
    "f",
    "packet",
]
