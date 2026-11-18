"""Packet base class, direction enum and the catch-all UnknownPacket."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar


class Direction(Enum):
    DOWN = "down"
    UP = "up"
    LOCAL = "local"  # host-generated, never on the wire


_NAN = object()


def _norm(v: Any) -> Any:
    """Normalise a value so that NaN compares equal to NaN."""
    if isinstance(v, float) and v != v:
        return _NAN
    if isinstance(v, (list, tuple)):
        return tuple(_norm(x) for x in v)
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


@dataclass(kw_only=True, eq=False)
class Packet:
    """Base for all packet dataclasses.

    `t` is the host receive time (ns since the Unix epoch), `src` the id of the source that delivered
    it. `extra` keeps unknown fields (raw text) from newer logs so they survive a round trip.
    Equality is NaN-aware. Treat instances as immutable once published.
    """

    TYPE: ClassVar[str] = ""
    VERSION: ClassVar[int] = 0

    t: int = 0
    src: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return NotImplemented
        return all(_norm(getattr(self, f.name)) == _norm(getattr(other, f.name)) for f in dataclasses.fields(self))

    __hash__ = None  # type: ignore[assignment]


@dataclass(kw_only=True, eq=False)
class UnknownPacket(Packet):
    """A line whose type is not in the registry. `raw_fields` holds the raw `name=value` text."""

    TYPE: ClassVar[str] = "Unknown"

    type: str = ""
    raw_fields: dict[str, str] = field(default_factory=dict)
