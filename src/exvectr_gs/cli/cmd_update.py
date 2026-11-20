"""Self-update commands: `exgs update status|check|apply --confirm` (talk to the running core) and
`exgs update --local` (the same procedure without a core, for when none is running). docs/updating.md."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..core.app import REPO_ROOT
from ..core.config import ConfigError, UpdateSection, load_config
from ..update import UpdateError, gitops
from . import client as C
from .client import EXIT_ERROR, EXIT_OK, CliError, CoreClient, add_client_command, emit


def _status_text(d: dict) -> str:
    if not d.get("git"):
        return d.get("message", "not a git checkout")
    lines = [
        f"version   {d.get('version', '?')}  commit {d['sha']} ({d['date'][:10]}) {d['subject']}",
        f"branch    {d['branch'] or '(detached HEAD)'}  following {d['remote']}/{d['configured_branch']}"
        + ("" if d.get("enabled", True) else "  [update disabled]"),
        f"tree      {'DIRTY: ' + '; '.join(d['dirty_files']) if d['dirty'] else 'clean'}",
        f"packets   schema {d.get('schema_hash')} on disk" + (
            f", {d['schema_hash_running']} running" if d.get("schema_hash_running") else ""),
    ]  # fmt: skip
    for s in d.get("submodules", []):
        lines.append(f"submodule {s['path']} {s['sha']} ({s['state']})")
    lc = d.get("last_check")
    if lc:
        lines.append(
            "last check " + (f"{lc['behind']} new commit(s)" if lc.get("ok") else f"failed: {lc.get('error')}")
        )
    if d.get("restart_pending"):
        lines.append("restart pending: the code on disk is newer than the running core")
    if d.get("guards"):
        lines.append("an update would be refused now: " + "; ".join(d["guards"]))
    return "\n".join(lines)


def _check_text(d: dict) -> str:
    if not d["behind"]:
        return f"up to date with {d['remote']}/{d['branch']}" + (
            f" ({d['ahead']} local commit(s) ahead)" if d["ahead"] else ""
        )
    lines = [f"{d['behind']} new commit(s) on {d['remote']}/{d['branch']}:"]
    lines += [f"  {c['sha']} {c['subject']}" for c in d["commits"]]
    if d["more_commits"]:
        lines.append(f"  ... and {d['more_commits']} more")
    if d["deps_changed"]:
        lines.append("pyproject.toml changed: `pip install -e .` will run")
    if d["submodules_changed"]:
        lines.append("submodule pins change")
    if d["diverged"]:
        lines.append(f"DIVERGED: {d['ahead']} local commit(s) not on the remote; a fast-forward update will refuse")
    return "\n".join(lines)


def _apply_text(d: dict) -> str:
    lines = [
        f"[{s['name']}] {'ok' if s['ok'] else 'FAILED'} ({s['seconds']} s)"
        + (f"\n{s['output']}" if s["output"] else "")
        for s in d["steps"]
    ]
    lines.append(d["message"])
    if d["restart"]:
        lines.append("the core restarts now (start.sh brings it back); reload the GUI if it does not reconnect")
    return "\n".join(lines)


async def _status(c: CoreClient, args: argparse.Namespace) -> int:
    d = await c.request("update.status")
    emit(args, d, _status_text(d))
    return EXIT_OK


async def _check(c: CoreClient, args: argparse.Namespace) -> int:
    d = await c.request("update.check", timeout=90.0)
    emit(args, d, _check_text(d))
    return EXIT_OK


async def _apply(c: CoreClient, args: argparse.Namespace) -> int:
    if not args.confirm:
        raise CliError("update apply needs --confirm (it restarts the core)")
    d = await c.request("update.apply", timeout=1200.0, confirm=True)
    emit(args, d, _apply_text(d))
    return EXIT_OK if d["ok"] else EXIT_ERROR


def _local(args: argparse.Namespace) -> int:
    """Offline update of this checkout: the same refusals (dirty tree, wrong branch, non-fast-forward) but no guards,
    because no core is running. Does not restart anything. A running core is NOT detected: stop it first."""
    cfg = UpdateSection()
    path = Path(args.config).expanduser() if args.config else None
    if path is not None or (REPO_ROOT / "config" / "gs.local.toml").exists():
        try:
            cfg = load_config(path or REPO_ROOT / "config" / "gs.local.toml").update
        except ConfigError as e:
            print(f"exgs: config error: {e}", file=sys.stderr)
            return EXIT_ERROR
    repo = Path(args.repo) if args.repo else REPO_ROOT

    def out(step: str, text: str) -> None:
        if not args.json:
            print(f"[{step}] {text}", flush=True)

    try:
        if args.dry_run:
            d = gitops.check(repo, cfg.branch, cfg.remote, cfg.allow_switch)
            emit(args, d, _check_text(d))
            return EXIT_OK
        res = gitops.apply(repo, cfg.branch, cfg.remote, cfg.allow_switch, progress=out)
    except UpdateError as e:
        e2 = CliError(f"{e.code}: {e.message}")
        C.report_error(args, e2)
        return EXIT_ERROR
    d = res.json()
    if args.json:
        emit(args, d)
    else:
        print(res.message, flush=True)
        if res.restart:
            print("restart the core (start.sh, or `exgs core`) to run the new version", flush=True)
    return EXIT_OK if res.ok else EXIT_ERROR


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("update", help="self-update from git: status, check, apply (or --local, offline)")
    sp.add_argument("--local", action="store_true", help="update this checkout without a running core (no guards)")
    sp.add_argument("--dry-run", action="store_true", help="with --local: only fetch and list the new commits")
    sp.add_argument("--config", help="with --local: gs.toml for [update] (default config/gs.local.toml if present)")
    sp.add_argument("--repo", help=argparse.SUPPRESS)
    sp.add_argument("--json", action="store_true", help="with --local: machine-readable output")
    ss = sp.add_subparsers(dest="update_cmd")
    add_client_command(ss, "status", "version, branch, tree state, last check", _status)
    add_client_command(ss, "check", "git fetch and list the new commits (control hosts only)", _check)
    p = add_client_command(ss, "apply", "update and restart the core (control hosts only, guards apply)", _apply)
    p.add_argument("--confirm", action="store_true", help="required: the update restarts the core")

    def top(args: argparse.Namespace) -> int:
        if not args.local:
            print("exgs: update needs a sub-command (status, check, apply) or --local", file=sys.stderr)
            return EXIT_ERROR
        return _local(args)

    sp.set_defaults(func=top)
