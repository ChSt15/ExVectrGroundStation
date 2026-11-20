#!/usr/bin/env python3
"""Convert the old ExVectrController CSV mission logs to `.exlog` (docs/legacy-logs.md).

    python tools/convert_legacy_log.py                      # all logs of the workspace data folder
    python tools/convert_legacy_log.py FILE... [--out DIR] [--manifest PATH]

Without FILE it converts every `*.txt` in `<data>/legacy/ExVectrController/tools/data/` and its `backup/` folder to
`<data>/legacy-converted/<name>.exlog` plus `<name>.meta.toml` and rewrites the generated block of
`<data>/manifest.toml`. `<data>` defaults to the workspace's `data/` folder next to this repository. Re-running
gives the same files (idempotent); unchanged files are not rewritten. Empty or corrupt files are reported and
skipped, the rest is converted. Same as `exgs log import-legacy`, with the workspace defaults.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from exvectr_gs.cli.cmd_log import run_import

REPO = Path(__file__).resolve().parents[1]
LEGACY_REL = Path("legacy/ExVectrController/tools/data")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, metavar="FILE")
    ap.add_argument("--data", type=Path, default=REPO.parent / "data", help="workspace data folder")
    ap.add_argument("--out", type=Path, help="output directory (default: <data>/legacy-converted)")
    ap.add_argument("--manifest", type=Path, help="manifest to update (default: <data>/manifest.toml)")
    ap.add_argument("--no-manifest", action="store_true", help="do not touch any manifest")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any file failed")
    args = ap.parse_args(argv)
    src_dir = args.data / LEGACY_REL
    files = args.files or sorted(src_dir.glob("*.txt")) + sorted((src_dir / "backup").glob("*.txt"))
    if not files:
        print(f"no input files (looked in {src_dir})", file=sys.stderr)
        return 1
    out = args.out or args.data / "legacy-converted"
    manifest = None if args.no_manifest else (args.manifest or args.data / "manifest.toml")
    return run_import(files, out, manifest, out.name, args.data, args.strict)


if __name__ == "__main__":
    sys.exit(main())
