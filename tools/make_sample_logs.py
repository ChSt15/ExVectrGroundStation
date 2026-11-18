#!/usr/bin/env python3
"""Generate sample `.exlog` files from FakeSource scenarios (used by tests, docs and the GUI e2e tests).

    python tools/make_sample_logs.py OUTDIR [--duration 60] [--only full,no-flapstate,dropouts,radio]

Logs go through the real Recorder, so they have the real header, `#type` and `#end` lines.
Packet times start at a fixed epoch time, so the logs are reproducible (same seed, same content).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from exvectr_gs.core.bus import Bus
from exvectr_gs.core.config import SourceConfig
from exvectr_gs.packets import REGISTRY
from exvectr_gs.recording import Recorder
from exvectr_gs.sources.fake_source import FakeSource

T0_NS = 1_763_471_000_000_000_000  # 2025-11-18 13:23:20 UTC, fixed so sample logs are reproducible

SCENARIOS: dict[str, dict] = {
    "full": {"label": "sample-full", "profile": "usb"},
    # Vehicle without flap telemetry: the GUI must show the default 45 degree flaps.
    "no-flapstate": {"label": "sample-no-flapstate", "profile": "usb", "rates": {"FlapState": 0}},
    # Telemetry gaps: Position drops out, then the whole source disconnects.
    "dropouts": {
        "label": "sample-dropouts",
        "profile": "usb",
        "faults": [
            {"kind": "dropout", "at": 15.0, "duration": 4.0, "types": ["Position"]},
            {"kind": "disconnect", "at": 30.0, "duration": 5.0},
            {"kind": "unknown_enum", "at": 40.0, "duration": 2.0},
        ],
    },
    "radio": {"label": "sample-radio", "profile": "radio"},
}


def make_log(directory: Path, name: str, duration_s: float, seed: int = 1) -> Path:
    spec = dict(SCENARIOS[name])
    label = spec.pop("label")
    src_id = "link" if spec["profile"] == "radio" else "usb"
    src = FakeSource(SourceConfig(id=src_id, kind="fake", options={**spec, "seed": seed}))
    src.reset(T0_NS)
    bus = Bus()
    now = [T0_NS]
    rec = Recorder(
        directory,
        REGISTRY,
        bus,
        {"gs": "sample", "host": "sample", "profile": "fake"},
        sources=[{"id": src_id, "kind": "fake", "device": "", "rc_role": "none"}],
        clock=lambda: now[0],
    )
    info = rec.start(label)
    step = 0.05
    t = 0.0
    while t <= duration_s:
        for pkt in src.step(t):
            now[0] = pkt.t
            bus.publish(pkt)
        t += step
    now[0] = T0_NS + int(duration_s * 1e9)
    rec.note("end of sample scenario")
    rec.stop()
    return info.path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--duration", type=float, default=60.0, help="seconds of scenario time per log")
    ap.add_argument("--only", help=f"comma-separated subset of {','.join(SCENARIOS)}")
    args = ap.parse_args(argv)
    names = args.only.split(",") if args.only else list(SCENARIOS)
    bad = [n for n in names if n not in SCENARIOS]
    if bad:
        print(f"unknown scenario(s) {bad}", file=sys.stderr)
        return 1
    for n in names:
        t = time.monotonic()
        p = make_log(args.outdir, n, args.duration)
        print(f"{p} ({p.stat().st_size / 1e6:.1f} MB, {time.monotonic() - t:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
