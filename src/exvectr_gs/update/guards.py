"""Safety guards of the self-update (docs/updating.md). A restart drops RC leases, recordings and the serial
connections, so it is refused while anything is live. Checked in the core (not only in the GUI), once when the update
is requested and again right before the core exits."""

from __future__ import annotations

from typing import Any

# Latest known values count even when they are old: an armed vehicle whose link just dropped is still armed.
_RUNNING = 3  # MissionMode.Running


def _val(entry: Any, name: str) -> Any:
    return getattr(entry.packet, name, None) if entry is not None else None


def _mode_is_running(v: Any) -> bool:
    return v is not None and int(v) == _RUNNING


def update_guards(app: Any, now_ns: int | None = None) -> list[str]:
    """Reasons the update must not apply (empty list: allowed). `app` is the CoreApp (duck-typed)."""
    why: list[str] = []
    vs = app.store.get("VehicleStatus")
    ms = app.store.get("MissionState")
    if _val(vs, "armed") is True:
        why.append("the vehicle reports ARMED")
    if _val(vs, "motors_enabled") is True:
        why.append("the vehicle reports MOTORS ENABLED")
    if _mode_is_running(_val(vs, "mission_mode")) or _mode_is_running(_val(ms, "mission_mode")):
        why.append("the vehicle reports mission mode RUNNING")
    rec = getattr(app, "recorder", None)
    if rec is not None and rec.status().active:
        why.append("a recording is active: stop it first")
    rc = getattr(app, "rc", None)
    if rc is not None and rc.status().lease is not None:
        why.append("an RC control lease is held: release it first")
    rq = getattr(app, "requests", None)
    if rq is not None and rq.status().get("inflight"):
        why.append("a request is in flight")
    tc = getattr(app, "telecommands", None)
    if tc is not None:
        now = app.clock.now() if now_ns is None else now_ns
        recent = tc.status().get("recent") or []
        if recent and now - recent[0]["t"] < 3_000_000_000:  # one frame, no ACK: 'in flight' for 3 s
            why.append("a telecommand was sent less than 3 s ago")
    return why
