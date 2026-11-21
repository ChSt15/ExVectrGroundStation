"""Analyse the 3D view's target smoothing on a recorded MissionState series (docs/gui.md, "Target position").

Replays the MissionState sp_pos / sp_vel of an .exlog as the browser would see it, at the logged rate (20 Hz USB) and
decimated to 5 Hz with the radio resolution (sp_pos 1 mm, sp_vel 0.01 m/s) and arrival jitter (AIR_MISSION path), and
draws the target at 60 Hz with three models: raw (hold the last packet), "old" (sp_pos + sp_vel*dt, 150 ms blend,
web/app/view3d/target_smooth.js before this change) and "new" (the follower of the current target_smooth.js, mirrored
here). The reference ("truth") is the logged 20 Hz sp_pos, linearly interpolated.

    python tools/analyse_target_smoothing.py [LOG ...]

Metrics, per frame (1/60 s): `jump` = change of the frame displacement vector of the drawn point minus that of the truth
(the visible stutter, an acceleration step), `err` = distance drawn point to truth. Stats are given for the continuous segments (frames not
within 4 s after a truth step of more than 2 m, a genuine guidance step) and for the steps separately.
"""

# ruff: noqa: B905, E501
from __future__ import annotations

import math
import random
import re
import sys
from pathlib import Path

DEFAULT_LOG = Path.home() / "exvectr-recordings" / "2026-11-20_12-06-28.exlog"
FRAME = 1 / 60

# the parameters of web/app/view3d/target_smooth.js
TAU_S = 0.25
MAX_EXTRAP_S = 0.5
EXTRAP_FACTOR = 1.25
EXTRAP_MARGIN_S = 0.05
STEP_SPEED = 30.0  # m/s: a faster implied speed between two packets is a guidance step, not motion


def load(path: Path):
    rows = []
    for line in path.read_text().splitlines():
        if " usb MissionState " not in line:
            continue
        t = int(line.split()[0]) / 1e9
        vel = [float(x) for x in re.search(r"sp_vel=\[([^\]]*)\]", line).group(1).split(",")]
        pos = [float(x) for x in re.search(r"sp_pos=\[([^\]]*)\]", line).group(1).split(",")]
        rows.append((t, pos, vel))
    return rows


def sub(a, b):
    return [x - y for x, y in zip(a, b)]


def add(a, b, k=1.0):
    return [x + y * k for x, y in zip(a, b)]


def norm(a):
    return math.sqrt(sum(x * x for x in a))


class Old:
    """Before this change: extrapolate sp_vel, decaying offset (tau 0.15 s), cap 0.5 s."""

    def __init__(self):
        self.pos = None

    def update(self, now, pos, vel):
        prev = self.at(now) if self.pos else None
        self.pos, self.vel, self.t, self.offT = pos, vel, now, now
        raw = self.pos
        self.off = sub(prev, raw) if prev else [0, 0, 0]

    def at(self, now):
        dt = min(0.5, max(0, now - self.t))
        f = math.exp(-max(0, now - self.offT) / 0.15)
        return [p + v * dt + o * f for p, v, o in zip(self.pos, self.vel, self.off)]


class New:
    """Critically damped follower of the reported sp_pos with feed-forward velocity (mirror of target_smooth.js)."""

    def __init__(self, tau=TAU_S, vel_mode="fd"):
        self.tau = tau
        self.vel_mode = vel_mode
        self.pos = None

    def update(self, now, pos, vel):
        if self.pos is None:
            self.x, self.xv, self.tNow = list(pos), [0, 0, 0], now
            self.v, self.interval = [0, 0, 0], None
        else:
            self.advance(now)
            dt = now - self.t
            v = list(vel)
            if 0.005 < dt < 1.0:
                fd = [d / dt for d in sub(pos, self.pos)]
                if norm(fd) <= STEP_SPEED:
                    if self.vel_mode == "fd":
                        v = fd
                    elif self.vel_mode == "lp":
                        v = fd if self.interval is None else add([0.5 * x for x in self.v], fd, 0.5)
                    elif self.vel_mode == "hybrid":
                        v = v if norm(sub(fd, v)) < 0.3 + 0.1 * norm(v) else fd
                    self.interval = dt if self.interval is None else 0.7 * self.interval + 0.3 * dt
                # else: a guidance step, keep sp_vel
            self.v = v
        self.pos, self.t = list(pos), now

    def cap(self):
        return (
            MAX_EXTRAP_S
            if self.interval is None
            else min(MAX_EXTRAP_S, EXTRAP_FACTOR * self.interval + EXTRAP_MARGIN_S)
        )

    def target(self, now):
        dt = min(self.cap(), max(0, now - self.t))
        moving = now - self.t < self.cap()
        return add(self.pos, self.v, dt), (self.v if moving else [0, 0, 0])

    def advance(self, now):
        w = 1 / self.tau
        while self.tNow < now - 1e-12:
            h = min(0.005, now - self.tNow)
            self.tNow += h
            r, vr = self.target(self.tNow)
            for i in range(3):
                a = w * w * (r[i] - self.x[i]) + 2 * w * (vr[i] - self.xv[i])
                self.xv[i] += a * h
                self.x[i] += self.xv[i] * h

    def at(self, now):
        self.advance(now)
        return list(self.x)


def make_stream(rows, hz, jitter, seed):
    """Packets as received: [(arrival, pos, vel)], decimated to `hz` of the 20 Hz log, quantised, jittered."""
    rnd = random.Random(seed)
    step = max(1, round(20 / hz))
    out = []
    last = -1.0
    for i in range(0, len(rows), step):
        t, pos, vel = rows[i]
        if hz < 20:
            pos = [round(x, 3) for x in pos]
            vel = [round(x, 2) for x in vel]
        arr = max(last + 1e-3, t + rnd.uniform(0, jitter))
        out.append((arr, pos, vel))
        last = arr
    return out


def truth_at(rows, ts, t):
    # rows are at a regular 20 Hz: index lookup
    i = min(len(rows) - 2, max(0, int((t - rows[0][0]) / 0.05)))
    t0, p0, _ = rows[i]
    t1, p1, _ = rows[i + 1]
    a = min(1, max(0, (t - t0) / (t1 - t0)))
    return [x + (y - x) * a for x, y in zip(p0, p1)]


def run(rows, stream, model):
    t0, t1 = stream[0][0], stream[-1][0]
    jumps, errs, step_jumps = [], [], []
    prev_d = prev_tr = prev_dd = prev_dt = None
    steps = [rows[i][0] for i in range(1, len(rows)) if norm(sub(rows[i][1], rows[i - 1][1])) > 2.0]
    k = 0
    t = t0
    while t < t1:
        while k < len(stream) and stream[k][0] <= t:
            model.update(stream[k][0], stream[k][1], stream[k][2])
            k += 1
        d = model.at(t) if model.__class__ is not str else None
        tr = truth_at(rows, None, t)
        if prev_d is not None:
            dd, dt_ = sub(d, prev_d), sub(tr, prev_tr)
            if prev_dd is not None:
                # visible stutter: change of the frame displacement vector, minus what the truth itself changes by
                j = max(0.0, norm(sub(dd, prev_dd)) - norm(sub(dt_, prev_dt)))
                in_step = any(-0.15 <= t - s <= 4.0 for s in steps)
                (step_jumps if in_step else jumps).append(j)
                if not in_step:
                    errs.append(norm(sub(d, tr)))
            prev_dd, prev_dt = dd, dt_
        prev_d, prev_tr = d, tr
        t += FRAME
    return jumps, errs, step_jumps


class Raw:
    def update(self, now, pos, vel):
        self.p = pos

    def at(self, now):
        return self.p


def pct(a, q):
    s = sorted(a)
    return s[min(len(s) - 1, int(q * len(s)))] if s else float("nan")


def main():
    paths = [Path(p) for p in sys.argv[1:]] or [DEFAULT_LOG]
    for path in paths:
        rows = load(path)
        print(f"{path.name}: {len(rows)} MissionState packets, {rows[-1][0] - rows[0][0]:.0f} s")
        # report where sp_vel is not the derivative of sp_pos
        bad = tot = 0
        for a, b in zip(rows, rows[1:]):
            dp = sub(b[1], a[1])
            if norm(dp) > 2.0:
                continue
            tot += 1
            if norm(sub([x / 0.05 for x in dp], b[2])) > 0.5:
                bad += 1
        print(f"  packets (no step) where |dp/dt - sp_vel| > 0.5 m/s: {bad}/{tot}")
        for label, hz, jit in (("20 Hz USB", 20, 0.002), ("5 Hz radio, 30 ms jitter", 5, 0.03)):
            stream = make_stream(rows, hz, jit, 1)
            print(
                f"  {label}: per-frame stutter `jump` (mm: p50 / p99 / max), continuous | steps max, err to truth (m: rms / max)"
            )
            for name, mk in (
                ("raw", Raw),
                ("old", Old),
                ("new (sp_vel only)", lambda: New(vel_mode="sp")),
                ("new (fd only)", New),
                ("new (shipped)", lambda: New(vel_mode="hybrid")),
            ):
                j, e, s = run(rows, stream, mk())
                rms = math.sqrt(sum(x * x for x in e) / len(e))
                print(
                    f"    {name:18s} {pct(j, 0.5) * 1000:6.1f} {pct(j, 0.99) * 1000:7.1f} {max(j) * 1000:7.1f} | step max {max(s, default=0) * 1000:7.1f} | err {rms:.3f} / {max(e):.3f}"
                )


if __name__ == "__main__":
    main()
