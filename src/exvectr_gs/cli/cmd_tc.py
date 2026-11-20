"""TEMPORARY legacy telecommands: `exgs tc send|list` (controller: `telecommand/`, docs/telecommands.md).

TODO(T9b): delete this file with the telecommand package when the flight software's T9b lands.

SAFETY-RELEVANT but thin: the core enforces everything. The CLI adds the same guard: `send NAME` needs
`--confirm NAME` (the name repeated, no default). Exactly ONE frame is sent per call: there is no retry and no ACK.
The vehicle de-duplicates nothing, and SimulationMode TOGGLES. What happened is whatever the vehicle reports
afterwards (`exgs status`, VehicleStatus): this command never says a command worked.
"""

from __future__ import annotations

import argparse

from .client import EXIT_OK, CliError, CoreClient, add_client_command, emit


def _ff(r: dict) -> str:
    return f" float {r['param_float']:g}" if r.get("param_float") else ""


def _line(r: dict) -> str:
    return (
        f"{r['name']} ({r['type']} subsystem {r['subsystem']} param {r['param_int']}"
        f"{_ff(r)}) handed to {r['source']} once; "
        "no ACK exists: check what the vehicle reports"
    )


async def _list(c: CoreClient, args: argparse.Namespace) -> int:
    d = await c.request("telecommand.list")
    lines = []
    for e in d["commands"]:
        rng = "" if e["param_min"] is None else f" param {e['param_min']}..{e['param_max']} (default {e['param_int']})"
        if e.get("param_float_min") is not None:
            rng += (
                f" {e['param_float_name'] or 'float'} {e['param_float_min']:g}..{e['param_float_max']:g}"
                f" (default {e['param_float']:g})"
            )
        lines.append(
            f"{e['group']:<12} {e['name']:<18} {e['type']:<15} sub {e['subsystem']} param {e['param_int']}{rng} "
            f"confirm={e['confirm']} route={e['route'] or '-'}  {e['label']}"
        )
    emit(args, d, "\n".join(lines) or "(whitelist is empty)")
    return EXIT_OK


async def _send(c: CoreClient, args: argparse.Namespace) -> int:
    name = args.name
    if args.confirm != name:
        raise CliError(
            f"refusing to send {name}: add --confirm {name} (the name repeated; there is no default). "
            "A telecommand is sent exactly once and never repeated",
            error_code="confirm_required",
        )
    r = await c.request(
        "telecommand.send", name=name, confirm=args.confirm, param=args.param, param_float=args.param_float
    )
    emit(args, r, _line(r))
    return EXIT_OK


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("tc", help="TEMPORARY legacy telecommands (arm, simulation toggle, mission ...)")
    ss = sp.add_subparsers(dest="tc_cmd", required=True)
    add_client_command(ss, "list", "the telecommand whitelist", _list)
    p = add_client_command(ss, "send", "send ONE whitelisted telecommand (needs --confirm NAME)", _send)
    p.add_argument("name", help="name from the whitelist, for example Arm or MissionBegin")
    p.add_argument("--confirm", help="the name repeated: there is no default confirmation")
    p.add_argument("--param", type=int, help="int16 parameter, only for entries that list a range (MissionBegin)")
    p.add_argument(
        "--param-float",
        type=float,
        help="float parameter, only for entries that list a range (ZeroEstimators: seconds)",
    )
