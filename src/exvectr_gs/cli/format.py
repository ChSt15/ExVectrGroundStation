"""Human-readable formatting for CLI output (default: the log line format)."""

from __future__ import annotations

from typing import Any

from ..api.protocol import packet_from_json
from ..packets import encode_line


def line_of(item: dict) -> str:
    """Log line (design 3.4) of a JSON packet item."""
    return encode_line(packet_from_json(item))


def fmt_value(v: Any) -> str:
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, list):
        return "[" + ",".join(fmt_value(x) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def age_text(age_s: float) -> str:
    return f"{age_s:.3f}s" if age_s < 100 else f"{age_s:.0f}s"
