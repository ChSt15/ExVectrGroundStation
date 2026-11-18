"""Text line codec: `<t> <src> <Type> <field>=<value> ...` (format: design section 3.4)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Protocol

from .base import Packet, UnknownPacket
from .fields import FieldSpec
from .registry import REGISTRY, PacketType


class CodecError(ValueError):
    """A line could not be decoded."""


class TextCodec(Protocol):
    def encode(self, pkt: Packet) -> str: ...  # one line, no newline

    def decode(self, line: str, ptype: PacketType) -> Packet: ...  # raises CodecError


# ---------------------------------------------------------------- value encoding


def _enc_float(v: float) -> str:
    return repr(float(v))  # shortest round trip; 'nan', 'inf', '-inf'


def _enc_scalar(spec: FieldSpec, kind: str, v: object) -> str:
    if kind == "float":
        return _enc_float(v)  # type: ignore[arg-type]
    if kind in ("int", "time_ns"):
        return str(int(v))  # type: ignore[call-overload]
    if kind == "bool":
        return "true" if v else "false"
    if kind == "enum":
        n = int(v)  # type: ignore[call-overload]
        try:
            return spec.enum(n).name  # type: ignore[misc]
        except ValueError:
            return f"#{n}"
    if kind == "str":
        return json.dumps(v, ensure_ascii=True)
    raise CodecError(f"unsupported kind {kind}")


def _enc_value(spec: FieldSpec, v: object) -> str:
    if spec.kind == "float_array":
        return "[" + ",".join(_enc_float(x) for x in v) + "]"  # type: ignore[attr-defined]
    if spec.kind == "int_array":
        return "[" + ",".join(str(int(x)) for x in v) + "]"  # type: ignore[attr-defined]
    return _enc_scalar(spec, spec.kind, v)


def _dec_scalar(spec: FieldSpec, kind: str, tok: str) -> object:
    if kind == "float":
        return float(tok)
    if kind in ("int", "time_ns"):
        return int(tok)
    if kind == "bool":
        if tok not in ("true", "false"):
            raise ValueError(f"bad bool {tok!r}")
        return tok == "true"
    if kind == "enum":
        if tok.startswith("#"):
            return int(tok[1:])
        try:
            return spec.enum[tok]  # type: ignore[index]
        except KeyError:
            raise ValueError(f"unknown enum name {tok!r}") from None
    if kind == "str":
        v = json.loads(tok)
        if not isinstance(v, str):
            raise ValueError("not a string")
        return v
    raise ValueError(f"unsupported kind {kind}")


def _dec_value(spec: FieldSpec, tok: str) -> object:
    if spec.kind in ("float_array", "int_array"):
        if not (tok.startswith("[") and tok.endswith("]")):
            raise ValueError("array must be in [...]")
        inner = tok[1:-1]
        elem = "float" if spec.kind == "float_array" else "int"
        return [] if inner == "" else [_dec_scalar(spec, elem, x) for x in inner.split(",")]
    return _dec_scalar(spec, spec.kind, tok)


# ---------------------------------------------------------------- tokenising


def _tokens(rest: str) -> Iterator[tuple[str, str]]:
    """Yield (name, raw value) pairs. String values may contain spaces."""
    i, n = 0, len(rest)
    while i < n:
        if rest[i] == " ":
            i += 1
            continue
        eq = rest.find("=", i)
        if eq < 0:
            raise CodecError(f"expected name=value at {rest[i : i + 20]!r}")
        name = rest[i:eq]
        if not name or " " in name:
            raise CodecError(f"bad field name {name!r}")
        j = eq + 1
        if j < n and rest[j] == '"':
            k = j + 1
            while k < n and rest[k] != '"':
                k += 2 if rest[k] == "\\" else 1
            if k >= n:
                raise CodecError(f"unterminated string in field {name}")
            end = k + 1
            if end < n and rest[end] != " ":
                raise CodecError(f"garbage after string in field {name}")
        else:
            end = rest.find(" ", j)
            end = n if end < 0 else end
        yield name, rest[j:end]
        i = end


def _split_line(line: str) -> tuple[int, str, str, str]:
    parts = line.split(" ", 3)
    if len(parts) < 3:
        raise CodecError("expected '<t> <src> <Type> ...'")
    try:
        t = int(parts[0])
    except ValueError:
        raise CodecError(f"bad timestamp {parts[0]!r}") from None
    return t, parts[1], parts[2], parts[3] if len(parts) > 3 else ""


# ---------------------------------------------------------------- codecs


class GenericTextCodec:
    """Default codec for every type, driven by the type's FieldSpecs."""

    def encode(self, pkt: Packet) -> str:
        ptype: PacketType = type(pkt).PTYPE  # type: ignore[attr-defined]
        if " " in pkt.src or not pkt.src:
            raise CodecError(f"invalid src {pkt.src!r}")
        cols = [str(int(pkt.t)), pkt.src, ptype.name]
        for spec in ptype.fields:
            v = getattr(pkt, spec.name)
            if v is not None:
                cols.append(f"{spec.name}={_enc_value(spec, v)}")
        cols += [f"{k}={raw}" for k, raw in pkt.extra.items()]
        return " ".join(cols)

    def decode(self, line: str, ptype: PacketType) -> Packet:
        t, src, tname, rest = _split_line(line)
        if tname != ptype.name:
            raise CodecError(f"line type {tname!r} is not {ptype.name!r}")
        values: dict[str, object] = {}
        extra: dict[str, str] = {}
        for name, raw in _tokens(rest):
            if name in values or name in extra:
                raise CodecError(f"duplicate field {name!r}")
            spec = ptype.field(name)
            if spec is None:
                extra[name] = raw
                continue
            try:
                values[name] = _dec_value(spec, raw)
            except (ValueError, TypeError) as e:
                raise CodecError(f"{ptype.name}.{name}: {e}") from None
        return ptype.cls(t=t, src=src, extra=extra, **values)


def encode_line(pkt: Packet) -> str:
    """One text line (no newline). Dispatches through the packet's registered codec."""
    if isinstance(pkt, UnknownPacket):
        cols = [str(int(pkt.t)), pkt.src, pkt.type] + [f"{k}={v}" for k, v in pkt.raw_fields.items()]
        return " ".join(cols)
    ptype = getattr(type(pkt), "PTYPE", None)
    if ptype is None:
        raise CodecError(f"{type(pkt).__name__} is not a registered packet")
    return ptype.codec.encode(pkt)


def decode_line(line: str) -> Packet | None:
    """Decode one line. None for blank and `#` meta lines. Unknown type gives UnknownPacket."""
    line = line.rstrip("\r\n")
    if not line.strip() or line.startswith("#"):
        return None
    t, src, tname, rest = _split_line(line)
    try:
        ptype = REGISTRY.get(tname)
    except KeyError:
        return UnknownPacket(t=t, src=src, type=tname, raw_fields=dict(_tokens(rest)))
    return ptype.codec.decode(line, ptype)
