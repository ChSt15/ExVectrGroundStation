"""Conditions for `exgs wait`: `Type.field OP value`, one field per expression."""

from __future__ import annotations

import operator
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..packets import REGISTRY, FieldSpec, Packet

_RE = re.compile(r"^\s*(\w+)\.(\w+)(?:\[(\d+)\])?\s*(==|!=|<=|>=|<|>)\s*(.+?)\s*$")
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    ">": operator.gt,
    "<=": operator.le,
    ">=": operator.ge,
}


class ExprError(ValueError):
    pass


@dataclass
class Condition:
    text: str
    type: str
    field: str
    index: int | None
    op: str
    value: Any
    spec: FieldSpec

    def value_of(self, pkt: Packet) -> Any:
        """The field value in this packet (None = not supplied)."""
        v = getattr(pkt, self.field, None)
        if v is not None and self.index is not None:
            v = v[self.index] if self.index < len(v) else None
        return v

    def holds(self, pkt: Packet) -> bool:
        v = self.value_of(pkt)
        if v is None:  # not supplied never satisfies a condition
            return False
        return _OPS[self.op](v, self.value)


def _literal(tok: str, spec: FieldSpec, indexed: bool) -> Any:
    kind = "float" if indexed else spec.kind
    if kind == "bool":
        if tok not in ("true", "false"):
            raise ExprError(f"{spec.name} is a bool: compare with true or false")
        return tok == "true"
    if kind == "enum":
        assert spec.enum is not None
        if tok.lstrip("-").isdigit():
            return int(tok)
        try:
            return int(spec.enum[tok])
        except KeyError:
            raise ExprError(f"{spec.name}: unknown enum name {tok!r} (one of {[m.name for m in spec.enum]})") from None
    if kind in ("float", "int", "time_ns"):
        try:
            return float(tok) if kind == "float" or "." in tok or "e" in tok.lower() else int(tok)
        except ValueError:
            raise ExprError(f"{spec.name}: {tok!r} is not a number") from None
    if kind == "str":
        return tok[1:-1] if len(tok) >= 2 and tok[0] == tok[-1] == '"' else tok
    raise ExprError(f"{spec.name}: arrays need an index, e.g. {spec.name}[0]")


def parse_condition(text: str) -> Condition:
    m = _RE.match(text)
    if not m:
        raise ExprError(f"cannot parse {text!r}. Expected: Type.field OP value, e.g. 'VehicleState.armed == true'")
    tname, fname, idx, op, rhs = m.groups()
    try:
        ptype = REGISTRY.get(tname)
    except KeyError:
        raise ExprError(f"unknown packet type {tname!r}") from None
    spec = ptype.field(fname)
    if spec is None:
        raise ExprError(f"{tname} has no field {fname!r}")
    indexed = idx is not None
    if indexed and not spec.kind.endswith("_array"):
        raise ExprError(f"{tname}.{fname} is not an array")
    if indexed and spec.length is not None and int(idx) >= spec.length:
        raise ExprError(f"{tname}.{fname}: index {idx} out of range (length {spec.length})")
    if op not in ("==", "!=") and (spec.kind in ("bool", "str") and not indexed):
        raise ExprError(f"{tname}.{fname}: only == and != apply to {spec.kind}")
    return Condition(text.strip(), tname, fname, int(idx) if indexed else None, op, _literal(rhs, spec, indexed), spec)
