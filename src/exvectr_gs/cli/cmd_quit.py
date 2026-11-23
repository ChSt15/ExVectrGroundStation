"""`exgs quit --confirm`: stop the running core cleanly (control hosts only). Not a vehicle uplink."""

from __future__ import annotations

import argparse

from .client import EXIT_OK, CliError, CoreClient, add_client_command, emit


async def _quit(c: CoreClient, args: argparse.Namespace) -> int:
    if not args.confirm:
        raise CliError(
            "quit needs --confirm (it stops recording and the command stream: the vehicle falls back to its timeouts)"
        )
    d = await c.request("core.quit", confirm=True)
    emit(args, d, "the core is stopping (clean shutdown, exit code 0; start.sh does not restart it)")
    return EXIT_OK


def register(sub: argparse._SubParsersAction) -> None:
    p = add_client_command(sub, "quit", "stop the running core cleanly (control hosts only)", _quit)
    p.add_argument("--confirm", action="store_true", help="required: the core stops")
