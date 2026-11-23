"""Safety guards of the self-update (docs/updating.md). A restart drops panel leases, the command stream, recordings and
the serial connections, so it is refused while anything is live. Checked in the core (not only in the GUI), once when
the update is requested and again right before the core exits."""

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
        why.append("the vehicle reports MOTORS LIVE")
    if _mode_is_running(_val(vs, "mission_mode")) or _mode_is_running(_val(ms, "mission_mode")):
        why.append("the vehicle reports mission mode RUNNING")
    rec = getattr(app, "recorder", None)
    if rec is not None and rec.status().active:
        why.append("a recording is active: stop it first")
    rc = getattr(app, "rc", None)
    if rc is not None and rc.status().lease is not None:
        why.append("a virtual panel lease is held: release it first")
    eng = getattr(app, "engine", None)
    if eng is not None:
        out = eng.core.out
        if out.motors or out.go:
            why.append("the command state is motors armed or go: a restart ends the command stream (vehicle failsafe)")
    rq = getattr(app, "requests", None)
    if rq is not None and rq.status().get("inflight"):
        why.append("a request is in flight")
    return why
