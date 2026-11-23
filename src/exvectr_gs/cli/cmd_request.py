"""REQUEST commands: `exgs request send|list|status` (controller: `requests/`, docs/requests.md).

SAFETY-RELEVANT but thin: the core enforces everything (whitelist, control hosts, rate limit, confirmation).
The CLI adds the same guard on its side so that a typo can never send anything:

- `send ACTION` needs `--confirm ACTION` (the action name repeated). There is no default confirmation.
- An action with confirm level `danger` (LINK_UPLOAD_MODE, LINK_REBOOT) also needs `--i-understand-the-link-stops`.

`send` waits for the vehicle's REQUEST_ACK (or the retry timeout, about 5 s) and prints the status exactly as
reported. Exit codes: 0 DONE or IN_PROGRESS, 1 any other status or an error, 2 timeout (no ACK), 3 control denied.
"""

from __future__ import annotations

import argparse

from .client import EXIT_ERROR, EXIT_OK, EXIT_TIMEOUT, CliError, CoreClient, add_client_command, emit

WAIT_S = 8.0  # the core gives up after 5 attempts, 1 s apart
DANGER_FLAG = "--i-understand-the-link-stops"


def _line(r: dict) -> str:
    s = f"{r['action']} -> node {r['target']} id {r['req_id']:#06x}"
    if r["state"] == "sending":
        return f"{s}: sending (attempt {r['attempts']}/{r['max_attempts']} via {r['source']})"
    if r["state"] == "timeout":
        return f"{s}: TIMEOUT, no REQUEST_ACK after {r['attempts']} attempts"
    return f"{s}: ACK {r['status_name']} (detail {r['detail']}) after {r['attempts']} attempt(s)"


async def _list(c: CoreClient, args: argparse.Namespace) -> int:
    d = await c.request("request.list")
    rows = d["actions"]
    lines = [
        f"{e['group']:<14} {e['action']:<18} target {e['target']} ({e['target_name']:<9}) confirm={e['confirm']:<6} "
        f"route={e['route'] or '-'}  {e['label']}"
        for e in rows
    ]
    emit(args, d, "\n".join(lines) or "(whitelist is empty)")
    return EXIT_OK


async def _status(c: CoreClient, args: argparse.Namespace) -> int:
    d = await c.request("request.status")
    lines = [_line(r) for r in d["inflight"]] + [_line(r) for r in d["recent"]]
    emit(args, d, "\n".join(lines) or "no requests")
    return EXIT_OK


async def _send(c: CoreClient, args: argparse.Namespace) -> int:
    action = args.action
    if args.confirm != action:
        raise CliError(
            f"refusing to send {action}: add --confirm {action} (the action name repeated; there is no default)",
            error_code="confirm_required",
        )
    rows = (await c.request("request.list"))["actions"]
    entries = [e for e in rows if e["action"] == action and (args.target is None or e["target"] == args.target)]
    if not entries:
        raise CliError(
            f"{action} is not in the request whitelist (see `exgs request list`)", error_code="not_whitelisted"
        )
    warning = next((e["warning"] for e in entries if e.get("warning")), "")
    if any(e["confirm"] == "danger" for e in entries) and not args.i_understand_the_link_stops:
        raise CliError(
            f"{warning or action + ' stops the radio link of a link module'}"
            f" (a link module always obeys, even in flight). Add {DANGER_FLAG} to send it",
            error_code="confirm_required",
        )
    r = await c.request(
        "request.send",
        action=action,
        target=args.target,
        confirm=args.confirm,
        understand_link_stops=bool(args.i_understand_the_link_stops),
        **({} if args.arg_i is None else {"arg_i": args.arg_i}),
        **({} if args.arg_f is None else {"arg_f": args.arg_f}),
    )
    if args.no_wait:
        emit(args, r, _line(r))
        return EXIT_OK
    end = c.loop_time() + WAIT_S
    cur = r
    while cur["state"] == "sending":
        left = end - c.loop_time()
        if left <= 0:
            raise CliError("no final state within the wait time", EXIT_TIMEOUT)
        msg = await c.next_message("requests", left)
        cur = next((x for x in msg["inflight"] + msg["recent"] if x["req_id"] == r["req_id"]), cur)
    emit(args, cur, _line(cur))
    if cur["state"] == "timeout":
        return EXIT_TIMEOUT
    return EXIT_OK if cur["status_name"] in ("DONE", "IN_PROGRESS") else EXIT_ERROR


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser(
        "request", help="whitelisted one-shot REQUESTs (calibration, save, link upload, link reboot ...)"
    )
    ss = sp.add_subparsers(dest="request_cmd", required=True)
    add_client_command(ss, "list", "the request whitelist", _list)
    add_client_command(ss, "status", "requests in flight and recently finished (from ACKs)", _status)
    p = add_client_command(ss, "send", "send one whitelisted REQUEST (needs --confirm ACTION)", _send)
    p.add_argument("action", help="action name from the whitelist, for example CALIB_APPLY")
    p.add_argument("--target", type=int, help="node id (1 vehicle, 2 Router, 3 ship radio node); needed when ambiguous")
    p.add_argument("--confirm", help="the action name repeated: there is no default confirmation")
    p.add_argument(
        DANGER_FLAG,
        action="store_true",
        dest="i_understand_the_link_stops",
        help="also required for `danger` actions (LINK_UPLOAD_MODE, LINK_REBOOT): the module stops its link or reboots",
    )
    p.add_argument(
        "--arg-i", type=int, help="integer argument: selects a row with a fixed one (CALIB_CONTROL) or is the value"
    )
    p.add_argument("--arg-f", type=float, help="float argument of rows that have a range (default when absent)")
    p.add_argument("--no-wait", action="store_true", help="return after the first attempt without waiting for the ACK")
