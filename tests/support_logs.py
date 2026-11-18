"""Helpers shared by log tests: synthetic logs written without the recorder."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from make_sample_logs import T0_NS, make_log  # noqa: E402

__all__ = ["T0_NS", "make_log", "write_long_log"]


def write_long_log(path: Path, hours: float, base_log: Path) -> int:
    """Repeat the packet lines of `base_log` (whole seconds long) with shifted times up to `hours`. Returns lines."""
    lines = base_log.read_bytes().split(b"\n")
    head = [ln for ln in lines if ln.startswith(b"#") and not ln.startswith(b"#end")]
    body = [ln for ln in lines if ln and not ln.startswith(b"#")]
    block_ns = 20_000_000_000  # the base log is 20 s long
    n_blocks = int(hours * 3600 / 20)
    n = 0
    with open(path, "wb") as fh:
        fh.write(b"\n".join(head) + b"\n")
        for b in range(n_blocks):
            shift = b * block_ns
            out = []
            for ln in body:
                t, rest = ln.split(b" ", 1)
                out.append(b"%d %s" % (int(t) + shift, rest))
            fh.write(b"\n".join(out) + b"\n")
            n += len(out)
    return n
