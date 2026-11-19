"""Read-side commands: status, watch, get, wait."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time

from ..api.protocol import packet_from_json
from ..packets import REGISTRY
from .client import EXIT_ERROR, EXIT_OK, EXIT_TIMEOUT, CliError, CoreClient, add_client_command, emit
from .expr import ExprError, parse_condition
from .format import age_text, fmt_value, line_of


def _split(s: str | None) -> list[str] | None:
    return [x for x in s.split(",") if x] if s else None


def _check_types(types: list[str] | None) -> None:
    for t in types or []:
        try:
            REGISTRY.get(t)
        except KeyError:
            raise CliError(f"unknown packet type {t!r} (see `exgs schema`)") from None


# ---------------------------------------------------------------- status


def status_text(welcome: dict, st: dict) -> str:
    clock = st["clock"]
    out = [f"core {welcome['core_version']}  clients={st['clients']}  can_control={welcome['can_control']}"]
    out.append("sources:")
    for s in st["sources"]:
        since = f"{clock - s['since']:.0f}s" if s["since"] else "-"
        err = f"  error={s['last_error']}" if s["last_error"] else ""
        out.append(
            f"  {s['id']:<8} {s['kind']:<12} {s['state']:<10} device={s['device']} for={since}"
            f" reconnects={s['reconnects']}{err}"
        )
    r = st["recorder"]
    if not r.get("available"):
        out.append("recorder: not available")
    elif r["active"]:
        out.append(f"recorder: recording {r['name']} packets={r['packets']} bytes={r['bytes']}")
    else:
        out.append("recorder: idle")
    p = st["replay"]
    if not p.get("available"):
        out.append("replay: not available")
    else:
        out.append(f"replay: {p['state']}" + (f" {p['name']} t={p['t']}" if p["name"] else ""))
    out.append(rc_text(st["rc"]))
    return "\n".join(out)


def rc_text(rc: dict | None) -> str:
    if rc is None:
        return "rc: not available"
    lease = rc["lease"]
    holder = f" lease={lease['id']} holder={lease['holder']} kind={lease['kind']}" if lease else ""
    ctl = " ".join(f"{k}={fmt_value(v)}" for k, v in rc["controls"].items())
    locks = " ".join(
        f"{k}=locked" if v["locked"] else f"{k}=UNLOCKED({v['remaining_s']:.0f}s)"
        for k, v in rc.get("locks", {}).items()
    )
    return (
        f"rc: {rc['mode']} sending={rc['sending']} target={rc['target']}{holder}"
        + (f"\n  controls: {ctl}" if ctl else "")
        + (f"\n  locks: {locks}" if locks else "")
    )


async def _status(c: CoreClient, args: argparse.Namespace) -> int:
    st = await c.status()
    body = {k: v for k, v in st.items() if k != "op"}
    emit(args, {**body, "core_version": c.welcome["core_version"], "can_control": c.welcome["can_control"]},
         status_text(c.welcome, st))  # fmt: skip
    return EXIT_OK


# ---------------------------------------------------------------- watch


async def _watch(c: CoreClient, args: argparse.Namespace) -> int:
    types = _split(args.types)
    _check_types(types)
    await c.request(
        "subscribe",
        view=args.view,
        types=types or "*",
        mode="all",
        max_rate_hz=args.max_rate,
    )
    loop = asyncio.get_running_loop()
    end = None if args.duration is None else loop.time() + args.duration
    n = 0
    while True:
        left = None if end is None else end - loop.time()
        if left is not None and left <= 0:
            break
        try:
            msg = await c.next_message(timeout=left)
        except CliError as e:
            if e.exit_code == EXIT_TIMEOUT:
                break
            raise
        if msg["op"] != "packets" or msg["view"] != args.view:
            continue
        if msg.get("dropped"):
            if args.json:
                print(json.dumps({"ok": True, "dropped": msg["dropped"]}), flush=True)
            else:
                print(f"# dropped {msg['dropped']} packets (slow reader)", flush=True)
        for item in msg["items"]:
            if args.src and item["src"] != args.src:
                continue
            if args.json:
                print(json.dumps({"ok": True, **item}, separators=(",", ":")), flush=True)
            else:
                print(line_of(item), flush=True)
            n += 1
            if args.count is not None and n >= args.count:
                return EXIT_OK
    if args.count is not None and n < args.count:
        raise CliError(f"only {n} of {args.count} packets seen within {args.duration:g} s", EXIT_TIMEOUT)
    return EXIT_OK


# ---------------------------------------------------------------- get


def _freshness(item: dict, clock: float, field: str | None) -> tuple[str, float]:
    age = max(0.0, clock - item["t"])
    if field is not None and field not in item["f"]:
        return "not supplied", age
    return ("fresh" if age <= item.get("stale_after", math.inf) else "stale"), age


async def _get(c: CoreClient, args: argparse.Namespace) -> int:
    tname, _, fname = args.target.partition(".")
    try:
        ptype = REGISTRY.get(tname)
    except KeyError:
        raise CliError(f"unknown packet type {tname!r} (see `exgs schema`)") from None
    if fname and ptype.field(fname) is None:
        raise CliError(f"{tname} has no field {fname!r}")
    snap = await c.request("get_snapshot", view=args.view, types=[tname], src=args.src, preferred=args.src is None)
    if not snap["items"]:
        where = f" from source {args.src}" if args.src else ""
        raise CliError(f"missing: no {tname} received{where}", EXIT_ERROR, "missing")
    item = snap["items"][0]
    state, age = _freshness(item, snap["clock"], fname or None)
    res = {
        "target": args.target,
        "type": tname,
        "src": item["src"],
        "t": item["t"],
        "age_s": age,
        "freshness": state,
        "interval_s": item.get("iv"),
    }
    if fname:
        res["field"] = fname
        res["value"] = item["f"].get(fname)
        if fname in item.get("nf", {}):
            res["nf"] = item["nf"][fname]
        shown = (
            fmt_value(item["nf"][fname] if fname in item.get("nf", {}) else res["value"]) if fname in item["f"] else "-"
        )
    else:
        res["f"] = item["f"]
        if "nf" in item:
            res["nf"] = item["nf"]
        shown = line_of(item)
    emit(args, res, f"{args.target} = {shown}" if fname else shown)
    if not args.json:
        print(f"# src={item['src']} age={age_text(age)} {state}", flush=True)
    return EXIT_OK


# ---------------------------------------------------------------- wait


async def _wait(c: CoreClient, args: argparse.Namespace) -> int:
    try:
        cond = parse_condition(args.condition)
    except ExprError as e:
        raise CliError(str(e), EXIT_ERROR, "bad_expression") from None
    t0 = time.monotonic()
    await c.request("subscribe", view=args.view, types=[cond.type], mode="latest", max_rate_hz=100)
    loop = asyncio.get_running_loop()
    end = loop.time() + args.timeout
    last: str | None = None
    while True:
        try:
            msg = await c.next_message(timeout=max(0.0, end - loop.time()))
        except CliError as e:
            if e.exit_code != EXIT_TIMEOUT:
                raise
            seen = f"; last value {last}" if last is not None else "; no value received"
            raise CliError(
                f"timeout after {args.timeout:g} s: {cond.text!r} not satisfied{seen}", EXIT_TIMEOUT, "timeout"
            ) from None
        if msg["op"] not in ("snapshot", "packets") or msg["view"] != args.view:
            continue
        for item in msg["items"]:
            if item["type"] != cond.type or (args.src and item["src"] != args.src):
                continue
            if msg["op"] == "snapshot" and msg["clock"] - item["t"] > item.get("stale_after", math.inf):
                continue  # a stale value does not count as the vehicle reporting the condition
            pkt = packet_from_json(item)
            v = cond.value_of(pkt)
            last = fmt_value(item["f"].get(cond.field)) if v is not None else "not supplied"
            if cond.holds(pkt):
                emit(
                    args,
                    {"condition": cond.text, "value": item["f"].get(cond.field), "src": item["src"],
                     "t": item["t"], "waited_s": time.monotonic() - t0},
                    f"ok: {cond.text} (value {last}, src {item['src']}, after {time.monotonic() - t0:.2f}s)",
                )  # fmt: skip
                return EXIT_OK


def register(sub: argparse._SubParsersAction) -> None:
    add_client_command(sub, "status", "sources, recorder, replay, RC, clients", _status)
    w = add_client_command(sub, "watch", "stream packets (all of them, in order)", _watch)
    w.add_argument("--types", help="comma-separated packet types (default: all)")
    w.add_argument("--src", help="only this source id")
    w.add_argument("--view", choices=("live", "replay"), default="live")
    w.add_argument("--count", type=int, help="stop after N packets")
    w.add_argument("--duration", type=float, help="stop after S seconds")
    w.add_argument("--max-rate", type=float, default=100.0, help="batches per second from the core (default 100)")
    g = add_client_command(sub, "get", "latest value of Type or Type.field with age and freshness", _get)
    g.add_argument("target", metavar="Type[.field]")
    g.add_argument("--src")
    g.add_argument("--view", choices=("live", "replay"), default="live")
    wt = add_client_command(sub, "wait", "block until a vehicle-reported condition holds (exit 2 on timeout)", _wait)
    wt.add_argument("condition", help="e.g. 'VehicleState.armed == true' (== != < > <= >=, one field)")
    wt.add_argument("--timeout", type=float, default=10.0, help="seconds (default 10)")
    wt.add_argument("--src", help="only this source id")
    wt.add_argument("--view", choices=("live", "replay"), default="live")
