"""Replay control: `exgs replay open|play|pause|seek|speed|close` (the replayer is T4)."""

from __future__ import annotations

import argparse

from .client import EXIT_OK, CliError, CoreClient, add_client_command, emit

SPEEDS = (0.25, 0.5, 1, 2, 4, 8)


async def _open(c: CoreClient, args: argparse.Namespace) -> int:
    d = await c.request("replay.open", timeout=60.0, name=args.name)  # builds the index
    emit(args, d, f"replay opened {d['name']}")
    return EXIT_OK


def _simple(op: str, text: str):
    async def body(c: CoreClient, args: argparse.Namespace) -> int:
        await c.request(op)
        emit(args, {"op": op}, text)
        return EXIT_OK

    return body


async def _seek(c: CoreClient, args: argparse.Namespace) -> int:
    st = (await c.status())["replay"]
    if st.get("t_start") is None:
        raise CliError("no replay is open", error_code="not_found")
    await c.request("replay.seek", t=st["t_start"] + args.t)  # T is seconds from the log start
    emit(args, {"op": "replay.seek", "t": args.t}, f"seek {args.t:g} s from start")
    return EXIT_OK


async def _speed(c: CoreClient, args: argparse.Namespace) -> int:
    await c.request("replay.speed", x=args.x)
    emit(args, {"op": "replay.speed", "x": args.x}, f"speed {args.x:g}x")
    return EXIT_OK


def register(sub: argparse._SubParsersAction) -> None:
    rp = sub.add_parser("replay", help="replay control: open, play, pause, seek, speed, close")
    rs = rp.add_subparsers(dest="replay_cmd", required=True)
    o = add_client_command(rs, "open", "open a recording by name (paused at the start)", _open)
    o.add_argument("name")
    add_client_command(rs, "play", "play", _simple("replay.play", "playing"))
    add_client_command(rs, "pause", "pause", _simple("replay.pause", "paused"))
    s = add_client_command(rs, "seek", "seek to T seconds from the log start", _seek)
    s.add_argument("t", metavar="T", type=float)
    sp = add_client_command(rs, "speed", "set the replay speed", _speed)
    sp.add_argument("x", metavar="X", type=float, choices=SPEEDS)
    add_client_command(rs, "close", "close the replay", _simple("replay.close", "closed"))
