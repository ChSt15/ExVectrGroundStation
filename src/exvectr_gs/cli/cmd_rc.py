"""RC control commands: `exgs rc acquire|release|show|set` (the RC controller is T6).

SAFETY-RELEVANT but thin: every command is a request to the core, which enforces the control-lease
rules (control hosts only, stop sending when the lease ends, safe defaults). The CLI uses *timed*
leases because CLI processes are short-lived. Nothing here sends anything to the vehicle itself.
"""

from __future__ import annotations

import argparse
import asyncio

from .client import EXIT_OK, CliError, CoreClient, add_client_command, emit
from .cmd_read import rc_text


def parse_assignments(items: list[str]) -> dict[str, float | int]:
    """`SA=1 S1=0.4` -> {"SA": 1, "S1": 0.4}."""
    out: dict[str, float | int] = {}
    for it in items:
        name, eq, val = it.partition("=")
        if not eq or not name:
            raise CliError(f"expected CONTROL=VALUE, got {it!r}", error_code="bad_request")
        try:
            out[name] = float(val) if any(ch in val for ch in ".eE") else int(val)
        except ValueError:
            raise CliError(f"{name}: {val!r} is not a number", error_code="bad_request") from None
    return out


async def _acquire(c: CoreClient, args: argparse.Namespace) -> int:
    d = await c.request("rc.acquire", kind="timed", duration_s=args.duration)
    emit(args, d, f"lease {d['id']} (timed, {args.duration:g} s) held by {d['holder']}")
    return EXIT_OK


async def _release(c: CoreClient, args: argparse.Namespace) -> int:
    await c.request("rc.release")
    emit(args, {"op": "rc.release"}, "released (the core stops sending)")
    return EXIT_OK


async def _show(c: CoreClient, args: argparse.Namespace) -> int:
    rc = (await c.status())["rc"]
    if rc is None:
        raise CliError("rc: not available in this core", error_code="not_implemented")
    emit(args, rc, rc_text(rc))
    return EXIT_OK


async def _set(c: CoreClient, args: argparse.Namespace) -> int:
    values = parse_assignments(args.assignments)
    if args.hold is not None and args.hold <= 0:
        raise CliError("--hold must be > 0", error_code="bad_request")
    before = ((await c.status())["rc"] or {}).get("controls", {}) if args.hold is not None else {}
    d = await c.request("rc.set", values=values)
    emit(args, d, "set " + " ".join(f"{k}={v}" for k, v in values.items()))
    if args.hold is not None:
        await asyncio.sleep(args.hold)
        restore = {k: before[k] for k in values if k in before}
        if not restore:
            raise CliError("--hold: the previous values are unknown, nothing restored", error_code="invalid_value")
        d = await c.request("rc.set", values=restore)
        emit(args, d, "restored " + " ".join(f"{k}={v}" for k, v in restore.items()))
    return EXIT_OK


def register(sub: argparse._SubParsersAction) -> None:
    rc = sub.add_parser("rc", help="RC control lease and controls (control hosts only)")
    rs = rc.add_subparsers(dest="rc_cmd", required=True)
    a = add_client_command(rs, "acquire", "take a timed control lease", _acquire)
    a.add_argument("--duration", type=float, required=True, metavar="S", help="lease length in seconds (max 600)")
    add_client_command(rs, "release", "release the lease (the core stops sending at once)", _release)
    add_client_command(rs, "show", "show the RC status", _show)
    s = add_client_command(rs, "set", "set controls, e.g. SA=1 S1=0.4", _set)
    s.add_argument("assignments", nargs="+", metavar="CONTROL=VALUE")
    s.add_argument("--hold", type=float, metavar="S", help="restore the previous values after S seconds")
