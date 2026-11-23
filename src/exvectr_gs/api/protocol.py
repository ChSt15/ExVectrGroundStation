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
MAX_RC_HOLD_S = 10.0
RC_KINDS = ("connection", "keepalive")
MAX_KEEPALIVE_IDLE_S = 9.0

ERROR_CODES = (
    "update_refused",  # self-update: a guard, a dirty tree, the wrong branch or a non-fast-forward (nothing changed)
    "update_failed",  # self-update: a git or pip step failed
    "bad_request",
    "unknown_type",
    "not_found",
    "busy",
    "control_denied",
    "control_locked",
    "no_lease",
    "invalid_value",
    "io_error",
    "not_whitelisted",  # request.send: the action (or action and target) is not in the whitelist
    "confirm_required",  # request.send: the confirmation arguments are missing or wrong
    "rate_limited",  # request.send: more than one new request per 500 ms from this client
    "request_in_flight",  # request.send: the same (action, target) is still waiting for its ACK
    "no_route",  # request.send, telecommand.send: no connected source can reach the target (nothing is queued)
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


def item_to_json(
    pkt: Packet,
    interval_s: float | None = None,
    stale_after_s: float | None = None,
    field_src: Mapping[str, Any] | None = None,
) -> dict:
    """`field_src` (merged items only): per field that came from another source than `src`, its own
    `{"src","t","stale_after"}` (`fs`)."""
    d = packet_to_json(pkt)
    if field_src:
        fs = {
            n: {"src": f.src, "t": f.t / 1e9, "stale_after": f.stale_after_s}
            for n, f in field_src.items()
            if f.src != pkt.src and n in d["f"]
        }
        if fs:
            d["fs"] = fs
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
        "disabled_by": getattr(st, "disabled_by", None),
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
    """Design 4.3 `status.recorder` from T2's `RecorderStatus` (active, info, last_error, free_bytes,
    dropped). Flat attributes (name, started, packets, bytes, error) are used if the object has them."""
    info = getattr(st, "info", None)

    def pick(flat: str, from_info: str | None = None, default: Any = None) -> Any:
        if hasattr(st, flat):
            return getattr(st, flat)
        if info is not None and from_info:
            return getattr(info, from_info, default)
        return default

    return {
        "active": bool(getattr(st, "active", False)),
        "name": pick("name", "name"),
        "started": secs(pick("started", "started")),
        "packets": pick("packets", "packets", 0),
        "bytes": pick("bytes", "bytes", 0),
        "free_bytes": getattr(st, "free_bytes", None),
        "error": pick("error", None) or getattr(st, "last_error", None),
        "dropped": getattr(st, "dropped", 0),
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
        "targets": list(st.targets),
        "pocket_active": st.pocket_active,
        "lease": None if lease is None else lease_json(lease),
        "controls": dict(st.controls),
        "channels_us": list(st.channels_us),
        "origin": st.origin,
        "t": secs(st.t),
        "locks": {k: dict(v) for k, v in st.locks.items()},
    }


def lease_json(lease: Any) -> dict:
    return {
        "id": lease.id,
        "holder": lease.holder,
        "kind": lease.kind,
        "expires": secs(lease.expires),
        "idle_s": getattr(lease, "idle_s", None),
    }


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
    """`kind`: connection (heartbeat at least every 1 s) or keepalive (renewed by every rc operation, ends
    after `idle_s`, default 5, max 9). Returns (kind, idle_s); idle_s is None for connection leases."""
    kind = msg.get("kind", "connection")
    if kind not in RC_KINDS:
        raise ProtocolError("bad_request", f"kind must be one of {RC_KINDS}")
    idle = _number(msg, "idle_s", optional=True)
    if kind == "keepalive":
        if idle is not None and not 0 < idle <= MAX_KEEPALIVE_IDLE_S:
            raise ProtocolError("invalid_value", f"idle_s must be within (0, {MAX_KEEPALIVE_IDLE_S:g}]")
    else:
        idle = None
    return kind, idle


def parse_rc_control(msg: Mapping) -> str:
    return _req(msg, "control", str, "a control id")


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


def parse_rc_hold(msg: Mapping) -> float | None:
    """Optional `hold_s` (extension): the core holds a momentary press for that long, then releases it."""
    hold = _number(msg, "hold_s", optional=True)
    if hold is not None and not 0 < hold <= MAX_RC_HOLD_S:
        raise ProtocolError("invalid_value", f"hold_s must be within (0, {MAX_RC_HOLD_S:g}]")
    return hold


def parse_request_send(msg: Mapping) -> dict[str, Any]:
    """`action` (name), optional `target` (node id), `confirm` (the action name repeated) and, for actions
    with confirm level `danger`, `understand_link_stops: true`. Semantics are the controller's."""
    action = _req(msg, "action", str, "an action name")
    target = msg.get("target")
    if target is not None and (isinstance(target, bool) or not isinstance(target, int)):
        raise ProtocolError("bad_request", "target must be an integer node id")
    confirm = msg.get("confirm")
    if confirm is not None and not isinstance(confirm, str):
        raise ProtocolError("bad_request", "confirm must be a string")
    ack = msg.get("understand_link_stops", False)
    if not isinstance(ack, bool):
        raise ProtocolError("bad_request", "understand_link_stops must be true or false")
    arg_f = msg.get("arg_f")
    if arg_f is not None and (isinstance(arg_f, bool) or not isinstance(arg_f, (int, float))):
        raise ProtocolError("bad_request", "arg_f must be a number")
    return {"action": action, "target": target, "confirm": confirm, "understand_link_stops": ack, "arg_f": arg_f}


def parse_telecommand_send(msg: Mapping) -> dict[str, Any]:
    """TEMPORARY. `name` (whitelist entry), `confirm` (the name repeated) and an optional integer `param`."""
    name = _req(msg, "name", str, "a telecommand name")
    confirm = msg.get("confirm")
    if confirm is not None and not isinstance(confirm, str):
        raise ProtocolError("bad_request", "confirm must be a string")
    param = msg.get("param")
    if param is not None and (isinstance(param, bool) or not isinstance(param, int)):
        raise ProtocolError("bad_request", "param must be an integer")
    pf = msg.get("param_float")
    if pf is not None and (isinstance(pf, bool) or not isinstance(pf, (int, float))):
        raise ProtocolError("bad_request", "param_float must be a number")
    return {"name": name, "confirm": confirm, "param": param, "param_float": pf}


def parse_replay_speed(msg: Mapping) -> float:
    x = _number(msg, "x")
    if x not in REPLAY_SPEEDS:
        raise ProtocolError("invalid_value", f"speed must be one of {list(REPLAY_SPEEDS)}")
    return x
