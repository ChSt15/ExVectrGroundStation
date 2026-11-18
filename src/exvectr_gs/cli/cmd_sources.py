"""Source commands: `exgs sources list|enable|disable`."""

from __future__ import annotations

import argparse

from .client import EXIT_OK, CoreClient, add_client_command, emit


async def _list(c: CoreClient, args: argparse.Namespace) -> int:
    srcs = await c.request("sources.list")
    lines = [
        f"{s['id']:<8} {s['kind']:<12} {'enabled' if s['enabled'] else 'disabled':<8} {s['state']:<10} "
        f"device={s['device']} reconnects={s['reconnects']}" + (f" error={s['last_error']}" if s["last_error"] else "")
        for s in srcs
    ]
    emit(args, {"sources": srcs}, "\n".join(lines))
    return EXIT_OK


def _enable(enabled: bool):
    async def body(c: CoreClient, args: argparse.Namespace) -> int:
        d = await c.request("sources.enable", source=args.id, enabled=enabled)
        emit(args, d, f"{d['id']}: {'enabled' if d['enabled'] else 'disabled'} ({d['state']})")
        return EXIT_OK

    return body


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("sources", help="data sources: list, enable, disable")
    ss = sp.add_subparsers(dest="sources_cmd", required=True)
    add_client_command(ss, "list", "list sources and their state", _list)
    for name, flag in (("enable", True), ("disable", False)):
        p = add_client_command(ss, name, f"{name} a source", _enable(flag))
        p.add_argument("id")
