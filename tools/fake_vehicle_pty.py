#!/usr/bin/env python3
"""Fake vehicle on a pseudo-terminal, speaking `provisional-1` bytes (design section 6.5).

Drives the same scripted scenario as FakeSource, but as bytes: COBS frames on ports 10/11/12/50/51
interleaved with console text. Used to test SerialSource, the framer, the profile, reconnect and the
RC uplink end to end without hardware. It is a *vehicle stand-in for tests*: it implements no vehicle
logic, it only records the frames the ground station sends (`received`).

The payload packers below are written independently of `exvectr_gs.wire.provisional_1.layouts`
(from the legacy docs), so that the decoder is checked against a second implementation.

Run:  python tools/fake_vehicle_pty.py --link /tmp/fake-vehicle [--speed 1] [--seed 1]
then point a source at `match = { device = "/tmp/fake-vehicle" }`.
"""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import pty
import struct
import threading
import time
from pathlib import Path

from exvectr_gs.sources.scenario import Scenario
from exvectr_gs.wire.profile import ConsoleText, Frame
from exvectr_gs.wire.provisional_1.framing import ProvisionalFramer, encode_frame

I16MAX = 32767


def fx(v: float, limit: float) -> int:
    """Vehicle-side packFixedPoint: saturating, truncating toward zero, NaN -> 0."""
    if v != v:
        return 0
    if v > limit:
        return I16MAX
    if v < -limit:
        return -I16MAX - 1
    return int(v * I16MAX / limit)


def pack_attitude(f: dict) -> bytes:
    lim = math.radians(1000)
    return struct.pack(
        "<9h",
        *(fx(x, lim) for x in f["omega"]),
        *(fx(x, 1.0) for x in f["q"]),
        fx(f["tilt_acc"], math.radians(360)),
        fx(f["north_acc"], math.radians(360)),
    )


def pack_position(f: dict) -> bytes:
    return struct.pack(
        "<8h",
        *(fx(x, 1000) for x in f["pos"]),
        *(fx(x, 100) for x in f["vel"]),
        fx(f["h_acc"], 10),
        fx(f["v_acc"], 10),
    )


def pack_gps(f: dict) -> bytes:
    return struct.pack(
        "<iihhhhBhhh",
        int(f["lat"] * 1e7),
        int(f["lon"] * 1e7),
        fx(f["alt"], 1000),
        *(fx(x, 100) for x in f["vel"]),
        f["num_sats"],
        fx(f["pos_acc"], 10),
        fx(f["alt_acc"], 10),
        fx(f["vel_acc"], 10),
    )


def pack_vehicle_state(f: dict) -> bytes:
    flags = [f[k] for k in ("fail_sensor", "fail_attitude", "fail_position", "fail_pos_oob", "fail_att_oob")]
    flags += [f["fail_radio_loss"], f["sim"], f["armed"]]
    sensors = [
        f[k] for k in ("sensor_gnss", "sensor_imu", "sensor_baro", "sensor_mag", "sensor_att_kf", "sensor_pos_kf")
    ]
    return struct.pack(
        "<15B6h",
        int(f["mode"]),
        *(int(s) for s in sensors),
        *(int(b) for b in flags),
        *(fx(x, 50) for x in f["tvc_thrust"]),
        fx(f["tvc_torque"], 50),
        fx(f["ground_dist"], 2),
        fx(f["ground_dist_cov"], 1),
    )


def pack_mission_state(f: dict) -> bytes:
    return struct.pack(
        "<6fBqBB",
        *f["sp_vel"],
        *f["sp_pos"],
        int(f["mission_mode"]),
        int(f["t_mission"]),
        int(f["mission_index"]),
        int(f["mission_phase"]),
    )


PACKERS = {
    "Attitude": (10, pack_attitude),
    "Position": (11, pack_position),
    "Gps": (12, pack_gps),
    "VehicleState": (50, pack_vehicle_state),
    "MissionState": (51, pack_mission_state),
}
DEFAULT_RATES = {"Attitude": 50.0, "Position": 25.0, "VehicleState": 10.0, "MissionState": 5.0, "Gps": 1.0}


class FakeVehiclePty:
    """Create with a symlink path (a stand-in for /dev/serial/by-id/...); `start()` / `stop()`.

    `stop()` simulates unplugging (the pty and the symlink vanish); `start()` again replugs.
    """

    def __init__(self, link: str | Path, seed: int = 1, speed: float = 1.0, rates: dict[str, float] | None = None,
                 console: bool = True):  # fmt: skip
        self.link = Path(link)
        self.scenario = Scenario(seed)
        self.speed = speed
        self.rates = dict(DEFAULT_RATES if rates is None else rates)
        self.console = console
        self.received: list[Frame] = []
        self.received_text: list[str] = []
        self._lock = threading.Lock()
        self._master = -1
        self._slave = -1
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._rx = ProvisionalFramer()

    @property
    def running(self) -> bool:
        return self._master >= 0

    def start(self) -> None:
        self._master, self._slave = pty.openpty()
        os.set_blocking(self._master, False)
        with contextlib.suppress(FileNotFoundError):
            self.link.unlink()
        self.link.symlink_to(os.ttyname(self._slave))
        self._stop.clear()
        self._rx = ProvisionalFramer()
        self._threads = [
            threading.Thread(target=self._tx_loop, daemon=True, name="fakeveh-tx"),
            threading.Thread(target=self._rx_loop, daemon=True, name="fakeveh-rx"),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads = []
        with self._lock:
            for fd in (self._master, self._slave):
                if fd >= 0:
                    with contextlib.suppress(OSError):
                        os.close(fd)
            self._master = self._slave = -1
        with contextlib.suppress(FileNotFoundError):
            self.link.unlink()

    # ---- raw injection (tests)

    def write_raw(self, data: bytes) -> None:
        with self._lock:
            if self._master >= 0:
                os.write(self._master, data)

    def send_frame(self, channel: int, payload: bytes) -> None:
        self.write_raw(encode_frame(channel, payload))

    def send_console(self, text: str) -> None:
        self.write_raw(text.encode() + b"\n")

    # ---- threads

    def _tx_loop(self) -> None:
        t0 = time.monotonic()
        due = {name: 0.0 for name in self.rates}
        next_console = 0.0
        while not self._stop.is_set():
            sim_t = (time.monotonic() - t0) * self.speed
            out = bytearray()
            for name, rate in self.rates.items():
                chan, pack = PACKERS[name]
                while rate > 0 and due[name] <= sim_t:
                    out += encode_frame(chan, pack(self.scenario.fields(name, due[name])))
                    due[name] += 1.0 / rate
            if self.console and next_console <= sim_t:
                mode, _, phase = self.scenario.phase_at(sim_t)
                out += f"[fake-vehicle] t={sim_t:.1f}s mode={mode.name} phase={phase.name}\n".encode()
                next_console += 1.0
            if out:
                try:
                    self.write_raw(bytes(out))
                except (BlockingIOError, OSError):
                    pass  # nobody reading yet (buffer full) or gone: drop, like a real USB CDC would
            time.sleep(0.005)

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = os.read(self._master, 4096)
            except (BlockingIOError, OSError):
                time.sleep(0.005)
                continue
            for item in self._rx.feed(data, time.monotonic_ns()):
                if isinstance(item, Frame):
                    self.received.append(item)
                elif isinstance(item, ConsoleText):
                    self.received_text.append(item.text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--link", default="/tmp/exvectr-fake-vehicle", help="symlink to the pty slave")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--speed", type=float, default=1.0)
    args = ap.parse_args()
    veh = FakeVehiclePty(args.link, seed=args.seed, speed=args.speed)
    veh.start()
    print(f"fake vehicle on {args.link} -> {os.ttyname(veh._slave)} (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1.0)
            print(f"received {len(veh.received)} frame(s) from the ground station")
    except KeyboardInterrupt:
        pass
    finally:
        veh.stop()


if __name__ == "__main__":
    main()
