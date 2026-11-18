"""`exgs` command line.

`core` and `schema` live here. Every other command lives in its own module `cli/cmd_<name>.py` that
exposes `register(subparsers)`; they are discovered automatically, so a new command group (for example
T2's `exgs log ...`) needs no change to this file.

Exit codes: 0 ok, 1 error (including usage errors), 2 timeout, 3 control denied.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import pkgutil
import sys
from pathlib import Path

from ..core.config import ConfigError, load_config
from ..packets import REGISTRY

DEFAULT_CONFIG = "~/.config/exvectr-gs/gs.toml"


def _cmd_core(args: argparse.Namespace) -> int:
    from ..core.app import CoreApp

    path = Path(args.config or DEFAULT_CONFIG).expanduser()
    try:
        app = CoreApp(load_config(path))
    except ConfigError as e:
        print(f"exgs: config error: {e}", file=sys.stderr)
        return 1
    if args.port is not None:
        app.cfg.server.port = args.port
    print(f"exgs core: {len(app.sources)} source(s), config {path}", flush=True)
    try:
        asyncio.run(app.run(args.duration, summary=lambda s: print(s, flush=True), serve=not args.no_api))
    except KeyboardInterrupt:
        print("exgs core: stopped", flush=True)
    except OSError as e:  # for example the port is in use
        print(f"exgs: cannot start the API server on port {app.cfg.server.port}: {e}", file=sys.stderr)
        return 1
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    if args.markdown:
        sys.stdout.write(REGISTRY.markdown())
    else:
        print(json.dumps(REGISTRY.schema(), indent=2))
    return 0


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # usage errors exit 1 (2 means timeout)
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _register_commands(sub: argparse._SubParsersAction) -> None:
    from . import __path__ as cli_path

    for mod in sorted(pkgutil.iter_modules(cli_path), key=lambda m: m.name):
        if mod.name.startswith("cmd_"):
            importlib.import_module(f"{__package__}.{mod.name}").register(sub)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="exgs", description="ExVectr ground station")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("core", help="run the core in the foreground")
    c.add_argument("--config", help=f"gs.toml path (default {DEFAULT_CONFIG})")
    c.add_argument("--duration", type=float, help="stop after this many seconds (default: run until Ctrl-C)")
    c.add_argument("--port", type=int, help="API port (default: server.port from the config, 0 = any free port)")
    c.add_argument("--no-api", action="store_true", help="do not start the API server")
    c.set_defaults(func=_cmd_core)
    s = sub.add_parser("schema", help="print the packet types (JSON, or Markdown with --markdown)")
    s.add_argument("--markdown", action="store_true")
    s.set_defaults(func=_cmd_schema)
    _register_commands(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
