"""`exgs` command line. T1 provides `core` and `schema`; the rest arrives with T3."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from ..core.config import ConfigError, load_config
from ..packets import REGISTRY
from . import logcmd

DEFAULT_CONFIG = "~/.config/exvectr-gs/gs.toml"


def _cmd_core(args: argparse.Namespace) -> int:
    from ..core.app import CoreApp

    path = Path(args.config or DEFAULT_CONFIG).expanduser()
    try:
        app = CoreApp(load_config(path))
    except ConfigError as e:
        print(f"exgs: config error: {e}", file=sys.stderr)
        return 1
    print(f"exgs core: {len(app.sources)} source(s), config {path}", flush=True)
    try:
        asyncio.run(app.run(args.duration, summary=lambda s: print(s, flush=True)))
    except KeyboardInterrupt:
        print("exgs core: stopped", flush=True)
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    if args.markdown:
        sys.stdout.write(REGISTRY.markdown())
    else:
        print(json.dumps(REGISTRY.schema(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="exgs", description="ExVectr ground station")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("core", help="run the core in the foreground")
    c.add_argument("--config", help=f"gs.toml path (default {DEFAULT_CONFIG})")
    c.add_argument("--duration", type=float, help="stop after this many seconds (default: run until Ctrl-C)")
    c.set_defaults(func=_cmd_core)
    s = sub.add_parser("schema", help="print the packet types (JSON, or Markdown with --markdown)")
    s.add_argument("--markdown", action="store_true")
    s.set_defaults(func=_cmd_schema)
    logcmd.register(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
