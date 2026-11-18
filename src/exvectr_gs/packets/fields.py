"""Field declaration: FieldSpec and the `f()` helper used in packet dataclasses."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Literal

Kind = Literal["float", "int", "bool", "enum", "str", "time_ns", "float_array", "int_array"]


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: Kind
    length: int | None = None  # for arrays
    unit: str = ""  # SI unit string, e.g. "m", "m/s", "rad", "rad/s", "N", "us", "dBm", "%"
    enum: type[IntEnum] | None = None
    optional: bool = True  # None allowed (field not supplied by this link/profile)
    desc: str = ""
    labels: tuple[str, ...] = ()  # element names for arrays, e.g. ("w","x","y","z")


def f(
    unit: str = "",
    desc: str = "",
    labels: tuple[str, ...] = (),
    optional: bool = True,
    *,
    kind: Kind | None = None,
    length: int | None = None,
) -> Any:
    """Dataclass field with metadata. `kind` is inferred from the annotation unless given
    (needed for `time_ns`). Array length defaults to `len(labels)`."""
    return dataclasses.field(
        default=None,
        metadata={
            "unit": unit,
            "desc": desc,
            "labels": tuple(labels),
            "optional": optional,
            "kind": kind,
            "length": length,
        },
    )
