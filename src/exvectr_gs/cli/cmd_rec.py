"""Recording and log-library commands: `exgs rec start|stop|note` and `exgs logs`.

These call the core (which owns the recorder, T2). Recording is manual only (design 10.1-3).
"""

from __future__ import annotations

import argparse
import time

from .client import EXIT_OK, CoreClient, add_client_command, emit


def _info_text(verb: str, d: dict) -> str:
    return f"{verb} {d['name']} ({d['packets']} packets, {d['bytes']} bytes) {d['path']}"


async def _start(c: CoreClient, args: argparse.Namespace) -> int:
    d = await c.request("rec.start", label=args.label) if args.label else await c.request("rec.start")
    emit(args, d, _info_text("recording", d))
    return EXIT_OK


async def _stop(c: CoreClient, args: argparse.Namespace) -> int:
    d = await c.request("rec.stop")
    emit(args, d, _info_text("stopped", d))
    return EXIT_OK


async def _note(c: CoreClient, args: argparse.Namespace) -> int:
    await c.request("rec.note", text=args.text)
    emit(args, {"note": args.text}, "note recorded")
    return EXIT_OK


async def _logs(c: CoreClient, args: argparse.Namespace) -> int:
    logs = await c.request("logs.list")
    if args.json:
        emit(args, {"logs": logs})
        return EXIT_OK
    for lg in logs:
        dur = f"{lg['duration_s']:.0f}s" if lg["duration_s"] is not None else "?"
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(lg["started"]))
        flags = ("" if lg["complete"] else " incomplete") + (" sim" if lg["sim"] else "")
        label = f" [{lg['label']}]" if lg["label"] else ""
        print(f"{lg['name']}  {started}  {dur}  {lg['size']} B{flags}{label}")
    return EXIT_OK


def register(sub: argparse._SubParsersAction) -> None:
    rec = sub.add_parser("rec", help="recording: start, stop, note")
    rs = rec.add_subparsers(dest="rec_cmd", required=True)
    s = add_client_command(rs, "start", "start a recording (manual only, never automatic)", _start)
    s.add_argument("--label", help="label stored in the log header")
    add_client_command(rs, "stop", "stop the recording", _stop)
    n = add_client_command(rs, "note", "add an operator note to the recording", _note)
    n.add_argument("text")
    add_client_command(sub, "logs", "list recordings", _logs)
