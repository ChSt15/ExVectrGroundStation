"""Virtual panel commands: `exgs rc acquire|keepalive|release|show|set|unlock|lock|session` (controller: T6).

SAFETY-RELEVANT but thin: every command is a request to the core, which enforces the lease rules (control
hosts only, safe defaults, control locks). Nothing here sends anything to the vehicle itself. Since the GS-control
cutover the core's command engine is the only sender and runs without any client: a lease only decides who may
move the VIRTUAL switches (the input of the engine while the Pocket is not live). Two ways to hold the panel (user
decision 2026-11-19):

- One-shot commands (`rc set`, `rc unlock`, ...) take or renew a *keepalive* lease with an idle timeout
  (default 5 s, max 9 s). Every rc command and `rc keepalive` renews it. When nothing renews it, the panel returns
  to its defaults and the engine holds its last command state.
- `rc session` is one long-running process holding a *connection* lease with a 300 ms heartbeat. When the
  process exits or crashes the connection drops and the lease ends at once (the command stream continues).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import shlex
import sys
import threading
from collections.abc import Iterable

from .client import EXIT_OK, CliError, CoreClient, CoreError, add_client_command, emit
from .cmd_read import rc_text

KEEPALIVE_DEFAULT_S = 5.0  # the core's default; --idle may lower it, the core rejects anything above 9 s
HEARTBEAT_PERIOD_S = 0.3  # session lease heartbeat (the core needs one at least every 1 s)


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


class Rc:
    """RC operations over one core connection.

    `auto_lease` (one-shot commands): an operation that finds no lease takes a keepalive lease first and
    says so (taking a lease starts from the defaults and every lock closed). Without it (session) the
    connection lease is already held and a missing lease is an error.
    """

    def __init__(self, c: CoreClient, args: argparse.Namespace, auto_lease: bool, idle_s: float | None = None):
        self.c, self.args, self.auto_lease, self.idle_s = c, args, auto_lease, idle_s
        self.kinds: dict[str, str] | None = None

    async def request(self, op: str, **fields: object) -> dict:
        try:
            return await self.c.request(op, **fields)
        except CoreError as e:
            if e.error_code != "no_lease" or not self.auto_lease:
                raise
        extra = {} if self.idle_s is None else {"idle_s": self.idle_s}
        d = await self.c.request("rc.acquire", kind="keepalive", **extra)
        emit(
            self.args,
            {"op": "rc.acquire", **d},
            f"took keepalive lease {d['id']} (idle {d['idle_s']:g} s; controls start at their defaults)",
        )
        return await self.c.request(op, **fields)

    async def control_kinds(self) -> dict[str, str]:
        if self.kinds is None:
            cfg = await self.c.request("config.get", name="radio")
            self.kinds = {x["id"]: x["kind"] for x in cfg.get("controls", [])}
        return self.kinds

    async def sleep_renewing(self, seconds: float) -> None:
        """Wait while keeping a keepalive lease alive (renewal at most every 1 s, a third of a short idle)."""
        step = min(1.0, (self.idle_s or KEEPALIVE_DEFAULT_S) / 3)
        end = asyncio.get_running_loop().time() + seconds
        while (left := end - asyncio.get_running_loop().time()) > 0:
            await asyncio.sleep(min(step, left))
            if self.auto_lease and asyncio.get_running_loop().time() < end:
                with contextlib.suppress(CliError):
                    await self.c.request("rc.heartbeat")

    async def set(self, values: dict[str, float | int], hold: float | None = None, unlock: Iterable[str] = ()) -> None:
        unlock = list(dict.fromkeys(unlock))
        if hold is not None and hold <= 0:
            raise CliError("--hold must be > 0", error_code="bad_request")
        stray = [u for u in unlock if u not in values]
        if stray:
            raise CliError(f"--unlock {stray[0]}: not among the controls being set", error_code="bad_request")
        unlocked: list[str] = []
        try:
            for cid in unlock:  # the explicit deliberate step: the core unlocks only on this request
                await self.request("rc.unlock", control=cid)
                unlocked.append(cid)
                emit(
                    self.args,
                    {"op": "rc.unlock", "control": cid},
                    f"unlocked {cid} (the core relocks it after its timeout)",
                )
            await self._set(values, hold)
        except CliError as e:
            for cid in unlocked:  # a failed request leaves nothing unlocked
                with contextlib.suppress(CliError):
                    await self.c.request("rc.lock", control=cid)
            if e.error_code == "control_locked":
                e.message += " (use --unlock CONTROL to unlock it for this command)"
            raise

    async def _set(self, values: dict[str, float | int], hold: float | None) -> None:
        if hold is None:
            d = await self.request("rc.set", values=values)
            emit(self.args, d, "set " + " ".join(f"{k}={v}" for k, v in values.items()))
            return
        # Momentary controls are held and released by the core (a killed CLI cannot leave one pressed).
        # Any other control is restored by this client after the hold, using the values the core reported.
        kinds = await self.control_kinds()
        momentary = {k: v for k, v in values.items() if kinds.get(k) == "momentary"}
        other = {k: v for k, v in values.items() if k not in momentary}
        before: dict = {}
        if other:
            before = ((await self.c.status())["rc"] or {}).get("controls", {})
        restore = {k: before[k] for k in other if k in before}
        if other and len(restore) != len(other):
            raise CliError("--hold: the previous values are unknown, nothing set", error_code="invalid_value")
        if other:
            d = await self.request("rc.set", values=other)
            emit(self.args, d, "set " + " ".join(f"{k}={v}" for k, v in other.items()))
        if momentary:
            d = await self.request("rc.set", values=momentary, hold_s=hold)
            text = "pressed " + " ".join(f"{k}={v}" for k, v in momentary.items())
            emit(self.args, d, text + f" (core releases after {hold:g} s)")
        await self.sleep_renewing(hold)
        if restore:
            d = await self.c.request("rc.set", values=restore)
            emit(self.args, d, "restored " + " ".join(f"{k}={v}" for k, v in restore.items()))


# ---------------------------------------------------------------- one-shot commands


def _rc(c: CoreClient, args: argparse.Namespace) -> Rc:
    return Rc(c, args, auto_lease=True, idle_s=getattr(args, "idle", None))


async def _acquire(c: CoreClient, args: argparse.Namespace) -> int:
    fields = {} if args.idle is None else {"idle_s": args.idle}
    d = await c.request("rc.acquire", kind="keepalive", **fields)
    emit(
        args,
        d,
        f"keepalive lease {d['id']} (idle timeout {d['idle_s']:g} s) held by {d['holder']}; "
        "renew it with any rc command or `rc keepalive`",
    )
    return EXIT_OK


async def _keepalive(c: CoreClient, args: argparse.Namespace) -> int:
    await c.request("rc.heartbeat")
    emit(args, {"op": "rc.heartbeat"}, "keepalive lease renewed")
    return EXIT_OK


async def _release(c: CoreClient, args: argparse.Namespace) -> int:
    await c.request("rc.release")
    emit(args, {"op": "rc.release"}, "released (the virtual panel is free; the command stream continues)")
    return EXIT_OK


async def _show(c: CoreClient, args: argparse.Namespace) -> int:
    rc = (await c.status())["rc"]
    if rc is None:
        raise CliError("rc: not available in this core", error_code="not_implemented")
    emit(args, rc, rc_text(rc))
    return EXIT_OK


async def _set(c: CoreClient, args: argparse.Namespace) -> int:
    await _rc(c, args).set(parse_assignments(args.assignments), args.hold, args.unlock or ())
    return EXIT_OK


async def _unlock(c: CoreClient, args: argparse.Namespace) -> int:
    d = await _rc(c, args).request("rc.unlock", control=args.control)
    emit(args, d, f"unlocked {args.control} (the core relocks it after its timeout, or when the lease ends)")
    return EXIT_OK


async def _lock(c: CoreClient, args: argparse.Namespace) -> int:
    d = await _rc(c, args).request("rc.lock", control=args.control)
    emit(args, d, f"locked {args.control} (back at its default)")
    return EXIT_OK


# ---------------------------------------------------------------- session

SESSION_HELP = """\
commands (one per line, # starts a comment):
  set CONTROL=VALUE ... [--hold S] [--unlock CONTROL ...]
  unlock CONTROL | lock CONTROL
  keepalive            (no-op: the session already heartbeats)
  show | sleep S | release | quit"""


def _set_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="set", add_help=False, exit_on_error=False)
    p.add_argument("assignments", nargs="+")
    p.add_argument("--hold", type=float)
    p.add_argument("--unlock", action="append")
    return p


async def run_line(rc: Rc, line: str) -> bool:
    """Run one session line. Returns False when the session should end."""
    parts = shlex.split(line, comments=True)
    if not parts:
        return True
    cmd, rest = parts[0].lower(), parts[1:]
    args = rc.args
    if cmd in ("quit", "exit"):
        return False
    if cmd == "release":
        await rc.c.request("rc.release")
        emit(args, {"op": "rc.release"}, "released (the virtual panel is free; the command stream continues)")
        return False
    if cmd == "keepalive":
        return True
    if cmd == "show":
        # the newest `rc` push (the core pushes on every change, so it is current after any request)
        st = {k: v for k, v in (rc.c.last.get("rc") or {"rc": None}).items() if k not in ("op", "holder_is_you")}
        st = st if "mode" in st else (await rc.c.status())["rc"]
        emit(args, st, rc_text(st))
        return True
    if cmd == "sleep":
        try:
            secs = float(rest[0])
        except (IndexError, ValueError):
            raise CliError("sleep needs a number of seconds", error_code="bad_request") from None
        await asyncio.sleep(max(0.0, secs))
        return True
    if cmd in ("unlock", "lock"):
        if len(rest) != 1:
            raise CliError(f"{cmd} needs one control id", error_code="bad_request")
        d = await rc.request(f"rc.{cmd}", control=rest[0])
        emit(args, d, f"{cmd}ed {rest[0]}")
        return True
    if cmd == "set":
        try:
            ns = _set_parser().parse_args(rest)
        except (argparse.ArgumentError, SystemExit) as e:
            raise CliError(f"set: {e}", error_code="bad_request") from None
        await rc.set(parse_assignments(ns.assignments), ns.hold, ns.unlock or ())
        return True
    raise CliError(f"unknown session command {cmd!r}\n{SESSION_HELP}", error_code="bad_request")


async def _heartbeats(c: CoreClient, lost: asyncio.Event) -> None:
    """Connection-lease heartbeat every 300 ms. A failure means the lease is gone: the session ends."""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_PERIOD_S)
            await c.request("rc.heartbeat", timeout=1.0)
    except CliError:
        lost.set()


def _start_line_reader(src, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
    """Lines of `src` (stdin or the script) on a queue, read by a daemon thread so that a blocked read can
    never keep the process alive after the session ended (EOF is the empty string)."""
    q: asyncio.Queue[str] = asyncio.Queue()

    def pump() -> None:
        try:
            for line in iter(src.readline, ""):
                loop.call_soon_threadsafe(q.put_nowait, line)
        except (OSError, ValueError):
            pass
        loop.call_soon_threadsafe(q.put_nowait, "")

    threading.Thread(target=pump, name="rc-session-input", daemon=True).start()
    return q


async def _session(c: CoreClient, args: argparse.Namespace) -> int:
    d = await c.request("rc.acquire", kind="connection")
    emit(
        args,
        d,
        f"session lease {d['id']} (connection, heartbeat {HEARTBEAT_PERIOD_S * 1000:.0f} ms) held by {d['holder']}",
    )
    lost = asyncio.Event()
    hb = asyncio.create_task(_heartbeats(c, lost))
    rc = Rc(c, args, auto_lease=False)
    src = open(args.script) if args.script else sys.stdin  # noqa: SIM115 (closed below)
    lines = _start_line_reader(src, asyncio.get_running_loop())
    try:
        while True:
            reader = asyncio.ensure_future(lines.get())
            lost_w = asyncio.ensure_future(lost.wait())
            await asyncio.wait({reader, lost_w}, return_when=asyncio.FIRST_COMPLETED)
            if lost.is_set():
                reader.cancel()
                raise CliError("the panel lease was lost (the command stream continues)", 3, "no_lease")
            lost_w.cancel()
            line = reader.result()
            if not line:  # EOF: end of script or stdin
                break
            try:
                if not await run_line(rc, line):
                    break
            except CliError as e:
                if args.keep_going:
                    print(f"exgs: {e.message}", file=sys.stderr, flush=True)
                    continue
                raise
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
        # Ending the session releases the lease; the connection closing right after would do so anyway.
        with contextlib.suppress(CliError):
            await c.request("rc.release", timeout=2.0)
    return EXIT_OK


def register(sub: argparse._SubParsersAction) -> None:
    rc = sub.add_parser("rc", help="RC control lease and controls (control hosts only)")
    rs = rc.add_subparsers(dest="rc_cmd", required=True)

    def idle(p: argparse.ArgumentParser) -> None:
        p.add_argument("--idle", type=float, metavar="S", help="keepalive idle timeout in seconds (default 5, max 9)")

    a = add_client_command(rs, "acquire", "take a keepalive lease (idle timeout 5 s)", _acquire)
    idle(a)
    add_client_command(rs, "keepalive", "renew the keepalive lease", _keepalive)
    add_client_command(rs, "release", "release the virtual panel lease (the command stream continues)", _release)
    add_client_command(rs, "show", "show the RC status", _show)
    s = add_client_command(rs, "set", "set controls, e.g. SA=1 S1=0.4 (takes or renews a keepalive lease)", _set)
    s.add_argument("assignments", nargs="+", metavar="CONTROL=VALUE")
    s.add_argument(
        "--hold",
        type=float,
        metavar="S",
        help="hold for S seconds, then release (momentary: held by the core; others: restored by this command)",
    )
    s.add_argument(
        "--unlock",
        action="append",
        metavar="CONTROL",
        help="unlock a locked control first (needed to set it to a non-default value); repeatable",
    )
    idle(s)
    u = add_client_command(rs, "unlock", "unlock a locked control (the core relocks it after its timeout)", _unlock)
    u.add_argument("control", metavar="CONTROL")
    idle(u)
    lk = add_client_command(rs, "lock", "lock a control again (it returns to its default)", _lock)
    lk.add_argument("control", metavar="CONTROL")
    idle(lk)
    ss = add_client_command(
        rs,
        "session",
        "long-running control session: connection lease with a 300 ms heartbeat, commands from stdin or --script",
        _session,
    )
    ss.add_argument("--script", metavar="FILE", help="read commands from FILE instead of stdin")
    ss.add_argument("--keep-going", action="store_true", help="report a failing command and continue (default: stop)")
    ss.epilog = SESSION_HELP
    ss.formatter_class = argparse.RawDescriptionHelpFormatter
