"""Git operations of the self-update (docs/updating.md). Synchronous, with a timeout on every step, so callers run
them off the event loop. Nothing here reads the vehicle state: the guards live in `guards.py`.

Rules: never anything but `git merge --ff-only`; never `reset --hard`; local changes are never touched (a dirty tree
or the wrong branch is refused before anything changes).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 60.0
LOCAL_TIMEOUT_S = 20.0
MERGE_TIMEOUT_S = 60.0
SUBMODULE_TIMEOUT_S = 300.0
PIP_TIMEOUT_S = 900.0
MAX_COMMITS = 20
OUTPUT_LIMIT = 8000
PIP_COMMAND = [sys.executable, "-m", "pip", "install", "-e", "."]  # the running interpreter's environment

Progress = Callable[[str, str], None]  # (step, text)


class UpdateError(Exception):
    """A refusal or failure. `code`: not_repo, disabled, detached, wrong_branch, dirty, diverged, git."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Step:
    name: str
    ok: bool
    output: str
    seconds: float


@dataclass
class ApplyResult:
    ok: bool = False
    steps: list[Step] = field(default_factory=list)
    old: str = ""
    new: str = ""
    deps_changed: bool = False
    submodules_changed: bool = False
    restart: bool = False  # the code on disk changed (or a restart is still owed): the core must restart
    message: str = ""

    def json(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "old": self.old, "new": self.new, "deps_changed": self.deps_changed,
            "submodules_changed": self.submodules_changed, "restart": self.restart, "message": self.message,
            "steps": [
                {"name": s.name, "ok": s.ok, "output": s.output, "seconds": round(s.seconds, 2)} for s in self.steps
            ],
        }  # fmt: skip


def run(cmd: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    """Run one command, return (exit code, combined output). A timeout is exit code 124."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_ASKPASS": "true"}
    try:
        p = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, f"{out}\ntimed out after {timeout:g} s"
    except OSError as e:
        return 127, f"cannot run {cmd[0]}: {e}"
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def _git(repo: Path, *args: str, timeout: float = LOCAL_TIMEOUT_S) -> tuple[int, str]:
    return run(["git", *args], repo, timeout)


def _ok(repo: Path, *args: str, timeout: float = LOCAL_TIMEOUT_S) -> str:
    rc, out = _git(repo, *args, timeout=timeout)
    if rc != 0:
        raise UpdateError("git", f"git {' '.join(args)}: {out or 'exit ' + str(rc)}")
    return out


def is_repo(repo: Path) -> bool:
    rc, out = _git(repo, "rev-parse", "--show-toplevel")
    return rc == 0 and Path(out).resolve() == Path(repo).resolve()


def current_branch(repo: Path) -> str | None:
    """The checked-out branch name, or None for a detached HEAD."""
    rc, out = _git(repo, "symbolic-ref", "--short", "-q", "HEAD")
    return out if rc == 0 and out else None


def dirty_entries(repo: Path) -> list[str]:
    """`git status --porcelain` lines of tracked files (untracked files are not 'local changes' here; a clash with
    an incoming file makes the merge itself fail, without touching anything)."""
    return [x for x in _ok(repo, "status", "--porcelain", "--untracked-files=no").splitlines() if x.strip()]


def submodule_pins(repo: Path) -> list[dict[str, str]]:
    """`[{path, sha, state}]`, state `ok`, `not checked out`, `differs` (checked out commit is not the pinned one),
    `conflict`. An unreadable submodule list gives []."""
    rc, out = _git(repo, "submodule", "status")
    if rc != 0:
        return []
    pins = []
    for line in out.splitlines():
        m = re.match(r"^([ +\-U])([0-9a-f]{7,64}) (\S+)", line)
        if m:
            state = {" ": "ok", "-": "not checked out", "+": "differs", "U": "conflict"}[m.group(1)]
            pins.append({"path": m.group(3), "sha": m.group(2)[:10], "state": state})
    return pins


def schema_hash(repo: Path) -> str | None:
    """SCHEMA_HASH of the checked-out ExVectrPackets, read from the file (not the imported, possibly older module)."""
    try:
        text = (repo / "lib" / "ExVectrPackets" / "python" / "exvectr_packets" / "__init__.py").read_text()
    except OSError:
        return None
    m = re.search(r"^SCHEMA_HASH\s*=\s*(0x[0-9A-Fa-f]+)", text, re.M)
    return f"0x{int(m.group(1), 16):08X}" if m else None


def head_info(repo: Path) -> dict[str, str]:
    rc, out = _git(repo, "log", "-1", "--format=%H%x09%h%x09%cI%x09%s")
    if rc != 0:
        return {"sha": "", "full": "", "date": "", "subject": ""}
    full, short, date, subject = (out.split("\t", 3) + ["", "", "", ""])[:4]
    return {"sha": short, "full": full, "date": date, "subject": subject}


def status(repo: Path, branch: str, remote: str) -> dict[str, Any]:
    """Read-only description of the checkout (`update.status`)."""
    if not is_repo(repo):
        return {"git": False, "message": f"{repo} is not a git checkout (installed copy): self-update is unavailable"}
    cur = current_branch(repo)
    try:
        dirty = dirty_entries(repo)
    except UpdateError as e:
        dirty = [e.message]
    return {
        "git": True, "branch": cur, "configured_branch": branch, "remote": remote, "detached": cur is None,
        **head_info(repo), "dirty": bool(dirty), "dirty_files": dirty[:10], "submodules": submodule_pins(repo),
        "schema_hash": schema_hash(repo),
    }  # fmt: skip


def _target_branch(repo: Path, branch: str, allow_switch: bool) -> str:
    """The branch to follow, after the refusals that apply to check and apply alike."""
    if not is_repo(repo):
        raise UpdateError("not_repo", "not a git checkout: nothing to update")
    cur = current_branch(repo)
    if cur is None:
        raise UpdateError(
            "detached", f"detached HEAD (a rollback state?): check out {branch} first (git checkout {branch})"
        )
    if cur != branch and not allow_switch:
        raise UpdateError(
            "wrong_branch",
            f"checked-out branch is {cur!r} but update.branch is {branch!r} (update.allow_switch = false)",
        )
    return cur if allow_switch else branch


def _limit(text: str) -> str:
    return text if len(text) <= OUTPUT_LIMIT else "...\n" + text[-OUTPUT_LIMIT:]


def _compare(repo: Path, remote: str, branch: str) -> dict[str, Any]:
    ref = f"{remote}/{branch}"
    rc, out = _git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
    if rc != 0:
        raise UpdateError(
            "git", f"{ref} does not exist (is the remote and branch name right? try `git fetch {remote}`)"
        )
    ahead, behind = (int(x) for x in _ok(repo, "rev-list", "--left-right", "--count", f"HEAD...{ref}").split())
    commits = []
    if behind:
        log_out = _ok(repo, "log", f"-n{MAX_COMMITS}", "--format=%h%x09%s", f"HEAD..{ref}")
        commits = [dict(zip(("sha", "subject"), ln.split("\t", 1), strict=False)) for ln in log_out.splitlines()]
    deps = subs = False
    if behind:
        raw = _ok(repo, "diff", "--raw", "--no-renames", "HEAD", ref)
        for ln in raw.splitlines():
            meta, _, path = ln.partition("\t")
            deps = deps or path == "pyproject.toml"
            subs = subs or meta.startswith(":160000") or " 160000 " in meta
    return {
        "ahead": ahead, "behind": behind, "commits": commits, "more_commits": max(0, behind - MAX_COMMITS),
        "deps_changed": deps, "submodules_changed": subs, "diverged": bool(ahead and behind),
        "remote_sha": _ok(repo, "rev-parse", "--short", ref),
    }  # fmt: skip


def check(
    repo: Path, branch: str, remote: str, allow_switch: bool = False, timeout: float = FETCH_TIMEOUT_S
) -> dict[str, Any]:
    """`git fetch`, then compare HEAD with `<remote>/<branch>`. Changes nothing but the remote-tracking refs."""
    target = _target_branch(repo, branch, allow_switch)
    t0 = time.monotonic()
    rc, out = _git(repo, "fetch", "--prune", remote, timeout=timeout)
    if rc != 0:
        raise UpdateError("git", f"git fetch {remote} failed: {_limit(out)}")
    res = _compare(repo, remote, target)
    res.update(branch=target, remote=remote, fetch_s=round(time.monotonic() - t0, 2), dirty=bool(dirty_entries(repo)))
    return res


def apply(
    repo: Path,
    branch: str,
    remote: str,
    allow_switch: bool = False,
    progress: Progress | None = None,
    restart_owed: bool = False,
) -> ApplyResult:
    """The update procedure. Raises UpdateError for a refusal (nothing was changed); a failure after the merge is a
    result with `ok=False` and the failing step last. `restart_owed`: an earlier apply changed the code but the
    restart did not happen (guards failed): an up-to-date tree then still asks for the restart."""
    target = _target_branch(repo, branch, allow_switch)
    dirty = dirty_entries(repo)
    if dirty:
        raise UpdateError(
            "dirty",
            "local changes in the working tree, update refused (commit, stash or discard them first): "
            + "; ".join(dirty[:5]),
        )
    res = ApplyResult(old=head_info(repo)["sha"])

    def step(name: str, cmd: list[str], timeout: float, cwd: Path = repo) -> bool:
        t0 = time.monotonic()
        log.info("update: %s: %s", name, " ".join(cmd))
        rc, out = run(cmd, cwd, timeout)
        out = _limit(out)
        res.steps.append(Step(name, rc == 0, out, time.monotonic() - t0))
        if progress:
            progress(name, f"{'ok' if rc == 0 else 'FAILED'}: {out}" if out else ("ok" if rc == 0 else "FAILED"))
        (log.info if rc == 0 else log.error)(
            "update: %s %s %s", name, "ok" if rc == 0 else f"FAILED ({rc})", out[-300:]
        )
        return rc == 0

    def fail(msg: str) -> ApplyResult:
        res.ok, res.message = False, msg
        res.new = head_info(repo)["sha"]
        res.restart = False
        return res

    if not step("fetch", ["git", "fetch", "--prune", remote], FETCH_TIMEOUT_S):
        return fail(f"git fetch failed: {res.steps[-1].output}")
    cmp = _compare(repo, remote, target)
    if cmp["ahead"]:
        raise UpdateError(
            "diverged",
            f"the checkout has {cmp['ahead']} commit(s) that are not on {remote}/{target}: "
            "not a fast-forward, nothing changed",
        )
    res.deps_changed, res.submodules_changed = cmp["deps_changed"], cmp["submodules_changed"]
    pins_off = any(p["state"] != "ok" for p in submodule_pins(repo))
    if cmp["behind"] == 0 and not pins_off:
        res.ok, res.new = True, res.old
        res.restart = restart_owed
        res.message = "already up to date" + ("; the restart is still pending" if restart_owed else "")
        return res
    if cmp["behind"]:
        if not step("merge", ["git", "merge", "--ff-only", f"{remote}/{target}"], MERGE_TIMEOUT_S):
            return fail(f"git merge --ff-only failed (nothing was changed by it): {res.steps[-1].output}")
    if not step("submodules", ["git", "submodule", "update", "--init", "--recursive"], SUBMODULE_TIMEOUT_S):
        return fail(
            "the code was updated but `git submodule update` failed (a pinned commit missing on the remote?). "
            "NOT restarting. Fix it and run the update again: " + res.steps[-1].output
        )
    if res.deps_changed and not step("pip", PIP_COMMAND, PIP_TIMEOUT_S):
        return fail(
            "the code was updated but `pip install -e .` failed. NOT restarting. "
            "Run it by hand in the venv, then restart: " + res.steps[-1].output
        )
    res.new = head_info(repo)["sha"]
    res.ok, res.restart = True, True
    res.message = f"updated {res.old} -> {res.new}"
    return res
