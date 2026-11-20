#!/usr/bin/env python3
"""Run only the tests that relate to what changed (development loop). Full suite: `pytest` before a merge.

    python tools/test_relevant.py            # files changed vs HEAD (staged, unstaged, untracked)
    python tools/test_relevant.py main       # files changed vs another ref (for example the branch base)
    python tools/test_relevant.py --list     # only print the test files it would run
    python tools/test_relevant.py --browser  # include the browser tests (skipped by default)

Rule: a changed module `src/exvectr_gs/<pkg>/<name>.py` selects every test file under tests/ that mentions `<name>`
or `<pkg>`; a changed test file selects itself; a changed web/ file selects the JS tests (`tools/run_js_tests.py`)
and, with --browser, the browser tests. If nothing matches it runs the unit tests only.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sh(*cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout.splitlines()


def changed(base):
    files = set(sh("git", "diff", "--name-only", base or "HEAD"))
    files |= set(sh("git", "ls-files", "--others", "--exclude-standard"))
    return sorted(f for f in files if f)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    files = changed(args[0] if args else None)
    tests = sorted((ROOT / "tests").rglob("test_*.py"))
    texts = {t: t.read_text() for t in tests}
    selected, js = set(), False
    for f in files:
        p = Path(f)
        if f.startswith("tests/") and p.name.startswith("test_"):
            selected.add(ROOT / f)
        elif f.startswith("src/exvectr_gs/") and p.suffix == ".py":
            keys = {p.stem, p.parent.name} - {"__init__", "exvectr_gs", "defs"}
            for t, text in texts.items():
                if any(k in t.name or k in text for k in keys):
                    selected.add(t)
        elif f.startswith("web/"):
            js = True
    browser = {t for t in selected if "browser_check" in texts.get(t, "")}
    if "--browser" not in flags:
        selected -= browser
    if not selected and not js:
        selected = {t for t in tests if "unit" in t.parts}
    names = [str(t.relative_to(ROOT)) for t in sorted(selected)]
    print(
        f"changed files: {len(files)}; test files: {len(names)}; js: {js}; skipped browser tests: {len(browser) if '--browser' not in flags else 0}"
    )
    for n in names:
        print("  ", n)
    if "--list" in flags:
        return 0
    rc = 0
    if names:
        rc |= subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *names], cwd=ROOT)
    if js:
        rc |= subprocess.call([sys.executable, "tools/run_js_tests.py"], cwd=ROOT)
    return rc


if __name__ == "__main__":
    sys.exit(main())
