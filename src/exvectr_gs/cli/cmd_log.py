"""Offline `exgs log ...` tools (no core needed): validate, stats, cat, import-legacy."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from ..packets import REGISTRY
from ..packets.base import UnknownPacket
from ..packets.textcodec import decode_line
from ..recording.logfile import parse_kv, read_header_block, read_tail, scan_lines


def _fail(msg: str) -> int:
    print(f"exgs log: {msg}", file=sys.stderr)
    return 1


def _check_file(path: Path) -> int | None:
    if not path.is_file():
        return _fail(f"no such file: {path}")
    return None


def cmd_validate(args: argparse.Namespace) -> int:
    """Exit 0 if there are no errors (warnings are allowed), 1 otherwise."""
    path = Path(args.file)
    if (rc := _check_file(path)) is not None:
        return rc
    errors: list[str] = []
    warnings: list[str] = []
    header, _ = read_header_block(path)
    if "v" not in header:
        errors.append("first line is not '#exlog v=...'")
    elif header["v"] != "1":
        errors.append(f"unsupported format version {header['v']!r}")
    packets = 0
    bad = 0
    unknown: dict[str, int] = defaultdict(int)
    backwards = 0
    last_t = None
    trailer = None
    end = 0
    size = path.stat().st_size if not path.name.endswith(".gz") else None
    for off, raw in scan_lines(path):
        end = off + len(raw)
        text = raw.decode("utf-8", "replace").rstrip("\r\n")
        if text.startswith("#end"):
            trailer = parse_kv(text[4:])
            continue
        if text.startswith("#") or not text.strip():
            continue
        try:
            pkt = decode_line(text)
        except Exception as e:
            bad += 1
            if bad <= 10:
                errors.append(f"byte {off}: {e}")
            continue
        packets += 1
        if isinstance(pkt, UnknownPacket):
            unknown[pkt.type] += 1
        if last_t is not None and pkt.t < last_t:
            backwards += 1
        last_t = pkt.t
    if bad > 10:
        errors.append(f"... {bad - 10} more undecodable line(s)")
    if size is not None and end < size:
        warnings.append(f"truncated last line ({size - end} bytes) ignored (crash during write)")
    if trailer is None:
        warnings.append("no #end line: incomplete recording (crash or still recording); readable up to the last line")
    elif trailer.get("packets", "").lstrip("-").isdigit() and int(trailer["packets"]) != packets:
        errors.append(f"#end says packets={trailer['packets']} but the file has {packets}")
    if unknown:
        warnings.append("unknown packet type(s): " + ", ".join(f"{k} ({v})" for k, v in sorted(unknown.items())))
    if backwards:
        warnings.append(f"{backwards} packet(s) with a timestamp earlier than the previous one")
    for e in errors:
        print(f"error: {e}")
    for w in warnings:
        print(f"warning: {w}")
    print(f"{path.name}: {packets} packets, {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


def cmd_stats(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if (rc := _check_file(path)) is not None:
        return rc
    per: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # n, first, last, max gap
    total = 0
    t0 = t1 = None
    for _, raw in scan_lines(path):
        if raw[:1] == b"#":
            continue
        parts = raw.split(b" ", 3)
        try:
            t = int(parts[0])
            key = (parts[2].decode(), parts[1].decode())
        except (ValueError, IndexError):
            continue
        total += 1
        t0 = t if t0 is None else min(t0, t)
        t1 = t if t1 is None else max(t1, t)
        e = per[key]
        if e[0] == 0:
            e[1] = t
        else:
            e[3] = max(e[3], t - e[2])
        e[0] += 1
        e[2] = t
    header, sources = read_header_block(path)
    trailer, _ = read_tail(path)
    dur = (t1 - t0) / 1e9 if t0 is not None else 0.0
    rows = []
    for (typ, src), (n, first, last, gap) in sorted(per.items()):
        span = (last - first) / 1e9
        rows.append(
            {
                "type": typ,
                "src": src,
                "count": n,
                "rate_hz": (n - 1) / span if span > 0 else None,
                "max_gap_s": gap / 1e9,
            }
        )
    if args.json:
        print(
            json.dumps(
                {
                    "file": path.name,
                    "header": header,
                    "sources": sources,
                    "complete": trailer is not None,
                    "packets": total,
                    "duration_s": dur,
                    "types": rows,
                }
            )
        )
        return 0
    print(f"{path.name}: {total} packets, {dur:.1f} s, {'complete' if trailer is not None else 'INCOMPLETE (no #end)'}")
    print("  " + " ".join(f"{k}={v}" for k, v in header.items()))
    for s in sources:
        print("  source " + " ".join(f"{k}={v}" for k, v in s.items()))
    print(f"  {'type':<16}{'src':<8}{'count':>9}{'rate Hz':>10}{'max gap s':>11}")
    for r in rows:
        rate = f"{r['rate_hz']:.1f}" if r["rate_hz"] is not None else "-"
        print(f"  {r['type']:<16}{r['src']:<8}{r['count']:>9}{rate:>10}{r['max_gap_s']:>11.2f}")
    return 0


def cmd_cat(args: argparse.Namespace) -> int:
    """Print packet lines as stored (the log line format), optionally filtered."""
    path = Path(args.file)
    if (rc := _check_file(path)) is not None:
        return rc
    types = set(args.types.split(",")) if args.types else None
    if types:
        unknown = sorted(t for t in types if not _known(t))
        if unknown:
            print(f"exgs log: warning: type(s) not in the registry: {', '.join(unknown)}", file=sys.stderr)
    t_from = t_to = None
    first_t = None
    n = 0
    try:
        for _, raw in scan_lines(path):
            if raw[:1] == b"#":
                if args.meta:
                    sys.stdout.write(raw.decode("utf-8", "replace"))
                continue
            parts = raw.split(b" ", 3)
            try:
                t = int(parts[0])
            except (ValueError, IndexError):
                continue
            if first_t is None:
                first_t = t
                t_from = None if args.start is None else t + int(args.start * 1e9)
                t_to = None if args.end is None else t + int(args.end * 1e9)
            if t_from is not None and t < t_from:
                continue
            if t_to is not None and t > t_to:
                break
            if types is not None and parts[2].decode() not in types:
                continue
            if args.src is not None and parts[1].decode() != args.src:
                continue
            sys.stdout.write(raw.decode("utf-8", "replace"))
            n += 1
            if args.count is not None and n >= args.count:
                break
    except BrokenPipeError:
        pass
    return 0


def print_import_results(results) -> None:
    """Per-file table of a legacy import (shared with tools/convert_legacy_log.py)."""
    print(f"{'source':40} {'status':10} {'layout':14} {'rows':>5} {'dur s':>7} {'max alt m':>9}  modes / note")
    for r in results:
        s = r.summary
        cols = f"{r.rel_source or r.source.name:40} {r.status:10} {r.layout or '-':14}"
        if s is not None:
            modes = ",".join(s.mission_modes_seen) or "(modes not logged)"
            cols += f" {s.rows:5d} {s.duration_s:7.1f} {s.max_altitude_m:9.2f}  {s.inferred_kind}: {modes}"
        else:
            cols += f" {'-':>5} {'-':>7} {'-':>9}"
        if r.message:
            cols += f"  [{r.message}]"
        print(cols)


def run_import(files, out: Path, manifest: Path | None, folder: str | None, rel_to: Path | None, strict: bool) -> int:
    from ..recording.legacy import convert_files, update_manifest

    paths = [Path(f) for f in files]
    results = convert_files(paths, out, rel_to=rel_to)
    print_import_results(results)
    if manifest is not None:
        update_manifest(manifest, results, folder or out.name)
        print(f"manifest updated: {manifest}")
    ok = sum(1 for r in results if r.status in ("converted", "unchanged"))
    failed = [r for r in results if r.status == "failed"]
    print(f"{ok} converted or unchanged, {len(failed)} failed, {len(results) - ok - len(failed)} skipped -> {out}")
    if failed and (strict or ok == 0):
        return 1
    return 0


def cmd_import_legacy(args: argparse.Namespace) -> int:
    rel_to = Path(args.relative_to) if args.relative_to else None
    manifest = Path(args.manifest) if args.manifest else None
    return run_import(args.files, Path(args.out), manifest, args.folder, rel_to, args.strict)


def _known(name: str) -> bool:
    try:
        REGISTRY.get(name)
    except KeyError:
        return False
    return True


def register(sub: argparse._SubParsersAction) -> None:
    """Add the `log` command group to the `exgs` parser."""
    lg = sub.add_parser("log", help="offline log tools (no core needed): validate, stats, cat, import-legacy")
    lsub = lg.add_subparsers(dest="log_cmd", required=True)
    v = lsub.add_parser("validate", help="check a log file (exit 1 on errors; warnings for crashed/incomplete files)")
    v.add_argument("file")
    v.set_defaults(func=cmd_validate)
    s = lsub.add_parser("stats", help="packets per type and source, rates, gaps")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_stats)
    c = lsub.add_parser("cat", help="print packet lines, filtered")
    c.add_argument("file")
    c.add_argument("--types", help="comma-separated packet types")
    c.add_argument("--src", help="only this source id")
    c.add_argument("--start", type=float, help="from this many seconds after the first packet")
    c.add_argument("--end", type=float, help="up to this many seconds after the first packet")
    c.add_argument("--count", type=int, help="stop after N lines")
    c.add_argument("--meta", action="store_true", help="also print # metadata lines")
    c.set_defaults(func=cmd_cat)
    i = lsub.add_parser(
        "import-legacy",
        help="convert old ExVectrController CSV logs to .exlog (+ .meta.toml), docs/legacy-logs.md",
    )
    i.add_argument("files", nargs="+", metavar="FILE")
    i.add_argument("--out", default=".", help="output directory (default: current directory)")
    i.add_argument("--manifest", help="also write the entries into this manifest.toml (generated block)")
    i.add_argument("--folder", help="folder name used in the manifest entries (default: the --out directory name)")
    i.add_argument("--relative-to", help="name sources relative to this directory (backup/x.txt)")
    i.add_argument("--strict", action="store_true", help="exit 1 if any file failed (default: only if all failed)")
    i.set_defaults(func=cmd_import_legacy)
