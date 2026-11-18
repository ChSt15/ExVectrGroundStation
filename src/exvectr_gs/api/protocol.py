"""WebSocket protocol helpers (design section 4): JSON conversion, error codes, message validation.

Times in JSON are float seconds since the Unix epoch (ints in ns exceed JavaScript's exact range).
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any

from ..packets import REGISTRY, Packet, PacketRegistry, UnknownPacket
from ..packets.fields import FieldSpec

PROTOCOL_VERSION = 1
VIEWS = ("live", "replay")
REPLAY_SPEEDS = (0.25, 0.5, 1, 2, 4, 8)
RC_KINDS = ("connection", "timed")
MAX_TIMED_LEASE_S = 600.0

ERROR_CODES = (
    "bad_request",
    "unknown_type",
    "not_found",
    "busy",
    "control_denied",
    "no_lease",
    "invalid_value",
    "io_error",
    "not_implemented",  # the backing module (recorder, replayer, RC controller) has not landed yet
)


class ProtocolError(Exception):
    """Raised by handlers and validators; becomes an `ok: false` result."""

    def __init__(self, code: str, message: str):
        assert code in ERROR_CODES, code
        super().__init__(message)
        self.code = code
        self.message = message


def secs(ns: int | None) -> float | None:
    return None if ns is None else ns / 1e9


def ns(seconds: float) -> int:
    return round(seconds * 1e9)


# ---------------------------------------------------------------- packets <-> JSON


def _nonfinite(v: float) -> str | None:
    if math.isnan(v):
        return "nan"
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    return None


def _enc(spec: FieldSpec, v: Any) -> tuple[Any, Any]:
    """Return (json value, non-finite marker or None)."""
    k = spec.kind
    if k == "float":
        m = _nonfinite(v)
        return (None, m) if m else (float(v), None)
    if k == "float_array":
        vals, marks = [], []
        for x in v:
            m = _nonfinite(x)
            vals.append(None if m else float(x))
            marks.append(m)
        return vals, (marks if any(marks) else None)
    if k == "int_array":
        return [int(x) for x in v], None
    if k == "enum":
        n = int(v)
        try:
            return spec.enum(n).name, None  # type: ignore[misc]
        except ValueError:
            return f"#{n}", None
    if k == "time_ns":
        return int(v) / 1e9, None
    if k == "bool":
        return bool(v), None
    if k == "int":
        return int(v), None
    return v, None  # str


def packet_to_json(pkt: Packet, registry: PacketRegistry = REGISTRY) -> dict:
    """`{"type","src","t","f":{...}}`; None fields omitted; `nf` marks NaN/inf values (as null)."""
    if isinstance(pkt, UnknownPacket):
        return {"type": pkt.type, "src": pkt.src, "t": pkt.t / 1e9, "f": {}, "raw": dict(pkt.raw_fields)}
    ptype = registry.get(pkt.TYPE)
    fields: dict[str, Any] = {}
    nf: dict[str, Any] = {}
    for spec in ptype.fields:
        v = getattr(pkt, spec.name)
        if v is None:
            continue
        val, mark = _enc(spec, v)
        fields[spec.name] = val
        if mark is not None:
            nf[spec.name] = mark
    out: dict[str, Any] = {"type": pkt.TYPE, "src": pkt.src, "t": pkt.t / 1e9, "f": fields}
    if nf:
        out["nf"] = nf
    return out


def _dec_nonfinite(m: str) -> float:
    return {"nan": math.nan, "inf": math.inf, "-inf": -math.inf}[m]


def packet_from_json(d: Mapping[str, Any], registry: PacketRegistry = REGISTRY) -> Packet:
    """Inverse of `packet_to_json` (time precision: float seconds, about 0.2 us). Used by the CLI."""
    t = round(float(d["t"]) * 1e9)
    try:
        ptype = registry.get(d["type"])
    except KeyError:
        return UnknownPacket(t=t, src=d["src"], type=d["type"], raw_fields=dict(d.get("raw", {})))
    nf = d.get("nf", {})
    values: dict[str, Any] = {}
    for name, v in d.get("f", {}).items():
        spec = ptype.field(name)
        if spec is None:
            continue
        mark = nf.get(name)
        if spec.kind == "float" and mark:
            v = _dec_nonfinite(mark)
        elif spec.kind == "float_array":
            marks = mark or [None] * len(v)
            v = [_dec_nonfinite(m) if m else x for x, m in zip(v, marks, strict=True)]
        elif spec.kind == "enum":
            v = int(v[1:]) if isinstance(v, str) and v.startswith("#") else spec.enum[v]  # type: ignore[index]
        elif spec.kind == "time_ns":
            v = round(v * 1e9)
        values[name] = v
    return ptype.cls(t=t, src=d["src"], **values)


def item_to_json(pkt: Packet, interval_s: float | None = None, stale_after_s: float | None = None) -> dict:
    d = packet_to_json(pkt)
    if interval_s is not None:
        d["iv"] = interval_s
    if stale_after_s is not None:
        d["stale_after"] = stale_after_s
    return d


# ---------------------------------------------------------------- generic status serialisation


def jsonable(v: Any) -> Any:
    """Best-effort JSON form of status dataclasses (Path, Enum, nested)."""
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: jsonable(getattr(v, f.name)) for f in dataclasses.fields(v)}
    if isinstance(v, Enum):
        return v.name if isinstance(v.value, int) else v.value
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, Mapping):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def source_status_json(st: Any) -> dict:
    return {
        "id": st.id,
        "kind": st.kind.value,
        "enabled": st.enabled,
        "state": st.state,
        "device": st.device,
        "since": secs(st.since),
        "reconnects": st.reconnects,
        "last_error": st.last_error,
    }


def recording_info_json(info: Any) -> dict:
    return {
        "name": info.name,
        "path": str(info.path),
        "started": secs(info.started),
        "stopped": secs(info.stopped),
        "packets": info.packets,
        "bytes": info.bytes,
    }


def log_info_json(info: Any) -> dict:
    return {
        "name": info.name,
        "path": str(info.path),
        "started": secs(info.started),
        "duration_s": info.duration_s,
        "size": info.size,
        "complete": info.complete,
        "sources": list(info.sources),
        "sim": info.sim,
        "label": info.label,
    }


def recorder_status_json(st: Any) -> dict:
    """Design 4.3 `status.recorder`. Reads the attributes T2's `RecorderStatus` provides:
    active, name, started (ns), packets, bytes, free_bytes, error."""
    return {
        "active": bool(getattr(st, "active", False)),
        "name": getattr(st, "name", None),
        "started": secs(getattr(st, "started", None)),
        "packets": getattr(st, "packets", 0),
        "bytes": getattr(st, "bytes", 0),
        "free_bytes": getattr(st, "free_bytes", None),
        "error": getattr(st, "error", None),
    }


def replay_status_json(st: Any) -> dict:
    return {
        "state": st.state,
        "name": st.name,
        "t": secs(st.t),
        "t_start": secs(st.t_start),
        "t_end": secs(st.t_end),
        "speed": st.speed,
    }


def rc_status_json(st: Any) -> dict:
    lease = st.lease
    return {
        "mode": st.mode,
        "sending": st.sending,
        "target": st.target,
        "lease": None
        if lease is None
        else {"id": lease.id, "holder": lease.holder, "kind": lease.kind, "expires": secs(lease.expires)},
        "controls": dict(st.controls),
        "channels_us": list(st.channels_us),
        "origin": st.origin,
        "t": secs(st.t),
    }


def lease_json(lease: Any) -> dict:
    return {"id": lease.id, "holder": lease.holder, "kind": lease.kind, "expires": secs(lease.expires)}


# ---------------------------------------------------------------- request validation


def _req(msg: Mapping, key: str, kind: type | tuple[type, ...], what: str | None = None) -> Any:
    if key not in msg:
        raise ProtocolError("bad_request", f"missing field {key!r}")
    v = msg[key]
    if isinstance(v, bool) and bool not in (kind if isinstance(kind, tuple) else (kind,)):
        raise ProtocolError("bad_request", f"field {key!r} must be {what or kind}")
    if not isinstance(v, kind):
        raise ProtocolError("bad_request", f"field {key!r} must be {what or kind}")
    return v


def _number(msg: Mapping, key: str, *, optional: bool = False, default: float | None = None) -> float | None:
    if key not in msg or msg[key] is None:
        if optional:
            return default
        raise ProtocolError("bad_request", f"missing field {key!r}")
    v = _req(msg, key, (int, float), "a number")
    if not math.isfinite(v):
        raise ProtocolError("invalid_value", f"field {key!r} must be finite")
    return float(v)


def view_of(msg: Mapping) -> str:
    v = msg.get("view", "live")
    if v not in VIEWS:
        raise ProtocolError("bad_request", f"view must be one of {VIEWS}")
    return v


def check_types(types: Any, registry: PacketRegistry = REGISTRY) -> frozenset[str] | None:
    """`"*"` -> None (all). Otherwise a list of known type names (UnknownPacket lines are not subscribable)."""
    if types == "*" or types is None:
        return None
    if not isinstance(types, list) or not all(isinstance(x, str) for x in types):
        raise ProtocolError("bad_request", 'types must be "*" or a list of names')
    known = {p.name for p in registry.all()}
    bad = [x for x in types if x not in known]
    if bad:
        raise ProtocolError("unknown_type", f"unknown packet type(s) {bad}")
    return frozenset(types)


@dataclasses.dataclass
class SubscribeRequest:
    view: str
    types: frozenset[str] | None
    mode: str
    max_rate_hz: float


def parse_subscribe(msg: Mapping, registry: PacketRegistry = REGISTRY) -> SubscribeRequest:
    mode = msg.get("mode", "latest")
    if mode not in ("latest", "all"):
        raise ProtocolError("bad_request", "mode must be 'latest' or 'all'")
    rate = _number(msg, "max_rate_hz", optional=True, default=30.0)
    assert rate is not None
    if not 0.1 <= rate <= 500:
        raise ProtocolError("invalid_value", "max_rate_hz must be within 0.1..500")
    return SubscribeRequest(view_of(msg), check_types(msg.get("types", "*"), registry), mode, rate)


def parse_history(msg: Mapping, registry: PacketRegistry = REGISTRY) -> tuple[str, list[dict], int, int, int]:
    series = _req(msg, "series", list, "a list")
    out = []
    for s in series:
        if not isinstance(s, dict):
            raise ProtocolError("bad_request", "series entries must be objects")
        tname = _req(s, "type", str, "a string")
        try:
            ptype = registry.get(tname)
        except KeyError:
            raise ProtocolError("unknown_type", f"unknown packet type {tname!r}") from None
        fields = _req(s, "fields", list, "a list")
        bad = [x for x in fields if not isinstance(x, str) or ptype.field(x) is None]
        if bad:
            raise ProtocolError("invalid_value", f"{tname}: unknown field(s) {bad}")
        src = s.get("src")
        if src is not None and not isinstance(src, str):
            raise ProtocolError("bad_request", "series src must be a string")
        out.append({"type": tname, "src": src, "fields": fields})
    t0 = _number(msg, "t0")
    t1 = _number(msg, "t1")
    mp = msg.get("max_points", 1000)
    if not isinstance(mp, int) or isinstance(mp, bool) or not 1 <= mp <= 100_000:
        raise ProtocolError("invalid_value", "max_points must be an integer within 1..100000")
    assert t0 is not None and t1 is not None
    return view_of(msg), out, ns(t0), ns(t1), mp


def parse_rc_acquire(msg: Mapping) -> tuple[str, float | None]:
    kind = msg.get("kind", "connection")
    if kind not in RC_KINDS:
        raise ProtocolError("bad_request", f"kind must be one of {RC_KINDS}")
    dur = _number(msg, "duration_s", optional=True)
    if kind == "timed":
        if dur is None or not 0 < dur <= MAX_TIMED_LEASE_S:
            raise ProtocolError("invalid_value", f"timed lease needs duration_s within (0, {MAX_TIMED_LEASE_S:g}]")
    else:
        dur = None
    return kind, dur


def parse_rc_set(msg: Mapping) -> dict[str, float | int]:
    vals = _req(msg, "values", dict, "an object")
    out: dict[str, float | int] = {}
    for k, v in vals.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise ProtocolError("invalid_value", f"control {k!r}: value must be a finite number")
        out[str(k)] = v
    if not out:
        raise ProtocolError("bad_request", "values is empty")
    return out


def parse_replay_speed(msg: Mapping) -> float:
    x = _number(msg, "x")
    if x not in REPLAY_SPEEDS:
        raise ProtocolError("invalid_value", f"speed must be one of {list(REPLAY_SPEEDS)}")
    return x
