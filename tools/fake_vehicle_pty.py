#!/usr/bin/env python3
"""Fake vehicle on a pseudo-terminal, speaking `provisional-1` or `exvectrnet-1` bytes (design section 6.5).

Drives the same scripted scenario as FakeSource, but as bytes: COBS frames on ports 10/11/12/50/51
interleaved with console text. Used to test SerialSource, the framer, the profile, reconnect and the
RC uplink end to end without hardware. It is a *vehicle stand-in for tests*: it implements no vehicle
logic, it only records the frames the ground station sends (`received`).

The payload packers below are written independently of `exvectr_gs.wire.provisional_1.layouts`
(from the legacy docs), so that the decoder is checked against a second implementation.

`profile="exvectrnet-1"` speaks the real ExVectrNetwork format with its own encoder (`fake_vehicle_net.py`,
written from the wire spec): role `vehicle` is node 1 on USB (heartbeat, VEHICLE_STATUS, POSE, ACTUATORS,
MISSION, GNSS, HANDSET_TELEM, debug packets 0x90-0x92), role `txmodule` is node 2 on USB forwarding the
vehicle's radio set (node 1), the RX module (node 3) and the Pocket's RC_STATE (node 2, 25 Hz).

Run:  python tools/fake_vehicle_pty.py --link /tmp/fake-vehicle [--profile exvectrnet-1 --role vehicle]
      [--speed 1] [--seed 1]
then point a source at `match = { device = "/tmp/fake-vehicle" }`.
"""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import pty
import struct
import sys
import threading
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_vehicle_net as net  # noqa: E402  isort: skip
from exvectr_gs.sources.scenario import Scenario  # noqa: E402
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
# exvectrnet-1, packet name -> Hz. Vehicle on USB sends the full set, the TX module forwards the radio set.
NET_VEHICLE_RATES = {
    "VEHICLE_STATUS": 20.0, "POSE": 50.0, "ACTUATORS": 10.0, "MISSION": 10.0, "GNSS": 10.0, "HOME_POSITION": 0.5,
    "MISSION_PARAMS": 1.0,
    "HANDSET_TELEM": 2.0,
    "IMU_RAW": 20.0, "ESTIMATOR": 10.0, "TASK_TIMING": 5.0, "CALIBRATION": 0.5,
    "CMD_APPLIED": 10.0,
}  # fmt: skip
NET_TX_RATES = {
    "VEHICLE_STATUS": 5.0, "AIR_POSE": 25.0, "AIR_ACTUATORS": 25.0, "AIR_MISSION": 5.0,
    "AIR_GNSS": 10.0, "AIR_GNSS_INFO": 1.0, "HANDSET_TELEM": 2.0, "LINK_STATS_TX": 2.0, "LINK_STATS_RX": 2.0,
    "POCKET_RC": 25.0,
}  # fmt: skip


class FakeVehiclePty:
    """Create with a symlink path (a stand-in for /dev/serial/by-id/...); `start()` / `stop()`.

    `stop()` simulates unplugging (the pty and the symlink vanish); `start()` again replugs.
    """

    def __init__(self, link: str | Path, seed: int = 1, speed: float = 1.0, rates: dict[str, float] | None = None,
                 console: bool = True, profile: str = "provisional-1", role: str = "vehicle",
                 hb_hz: float = 1.0, schema_hash: int | None = None, net_version: int = 1,
                 request_status: int = 0, request_drop_first: int = 0,
                 request_rules: bool = False, request_on_ground: bool = True,
                 request_latched_session: int | None = None, request_simulation: bool = False,
                 request_calib_refuse: bool = False, request_no_home: bool = False):  # fmt: skip
        if profile not in ("provisional-1", "exvectrnet-1") or role not in ("vehicle", "txmodule"):
            raise ValueError(f"unknown profile {profile!r} or role {role!r}")
        self.profile = profile
        self.role = role
        self.hb_hz = hb_hz
        self.net_version = net_version
        if schema_hash is None:
            import exvectr_packets  # only the hash value is taken from the generated module

            schema_hash = exvectr_packets.SCHEMA_HASH
        self.schema_hash = schema_hash
        self._seq: dict[tuple[int, int], int] = {}
        if rates is None:
            rates = (
                DEFAULT_RATES
                if profile == "provisional-1"
                else (NET_VEHICLE_RATES if role == "vehicle" else NET_TX_RATES)
            )
        self.link = Path(link)
        self.scenario = Scenario(seed)
        self.speed = speed
        self.rates = dict(rates)
        self.console = console
        self.received: list[Frame] = []
        self.received_text: list[str] = []
        # exvectrnet-1: heartbeats received from the ground station (dicts of `parse_stream`, with `t`),
        # and what a TX module would pass on to its vehicle side (the router rule: node 0 heartbeats
        # are dropped before the air link, everything else is forwarded).
        # exvectrnet-1 REQUEST test double (no vehicle logic): a REQUEST addressed to this role's node is
        # answered with a REQUEST_ACK of `request_status` (idempotent per req_id, the first
        # `request_drop_first` attempts of each id get no answer). `received_requests` lists every attempt.
        self.request_status = request_status
        self.request_drop_first = request_drop_first
        # `request_rules`: the documented executor answers for ZERO_ESTIMATORS (12) and SET_STOP_ALTITUDE (13), like
        # the fake source (test double, no vehicle logic): wrong target 3/2, not on the ground 2/1, session differs
        # from `request_latched_session` (None = the session of the first accepted CMD_STATE, none latched = refused)
        # 2/3, SET_STOP_ALTITUDE below 1 or not finite 3/1, else DONE.
        self.request_rules = request_rules
        self.request_on_ground = request_on_ground
        self.request_latched_session = request_latched_session
        # the same test-double answers as the fake source for the actions 7 and 14..23 (no argument range checks here)
        self.request_simulation = request_simulation
        self.request_calib_refuse = request_calib_refuse
        self.request_no_home = request_no_home
        self.stop_altitude = 20.0
        # SET_MISSION_PARAM (24) ids 1..6 -> (default, min, max); `mission_params` = current values by id
        self.mission_param_spec = {
            1: (7.0, 0.5, 10.0), 2: (120.0, 2.0, 1000.0), 3: (30.0, 0.0, 600.0),
            4: (10.0, 0.0, 600.0), 5: (15.0, 0.0, 600.0), 6: (15.0, 0.0, 600.0),
        }  # fmt: skip
        self.mission_params = {i: v[0] for i, v in self.mission_param_spec.items()}
        self.received_requests: list[dict] = []
        # CMD_STATE test double (port 0x13, no vehicle logic): every frame is parsed strictly like the vehicle
        # (`net.parse_cmd_state`); `received_cmd` lists every frame (dict with src, dst, seq and the fields, or
        # `rejected` = True), `cmd_session` is the first accepted session, `cmd_rejects` counts rejected frames.
        self.received_cmd: list[dict] = []
        self.last_cmd: dict | None = None
        self.cmd_session: int | None = None
        self.cmd_rejects = 0
        self._req_attempts: dict[int, int] = {}
        # Test setters (the telecommand port is gone): when not None, `sim_state` / `armed_state` override the
        # reported simulation/armed flags and `zeroing_until` (monotonic s) shows the ZEROING flag. Until set, they
        # come from the scenario.
        self.sim_state: bool | None = None
        self.armed_state: bool | None = None
        self.zeroing_until: float | None = None  # monotonic s; ZEROING flag (VEHICLE_STATUS bit 7) while it is ahead
        self.tx_power = 10  # dBm reported in LINK_STATS (txmodule role: both nodes 2 and 3)
        self.link_config_power = 10  # dBm in LINK_CONFIG (rate "LINK_CONFIG" must be enabled)
        self.received_heartbeats: list[dict] = []
        self.forwarded: list[dict] = []
        self._lock = threading.Lock()
        self._master = -1
        self._slave = -1
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._rx = ProvisionalFramer()
        self._rx_buf = bytearray()

    @property
    def running(self) -> bool:
        return self._master >= 0

    def start(self) -> None:
        self._master, self._slave = pty.openpty()
        tty.setraw(self._slave)  # no echo: the vehicle must not read its own transmissions back
        os.set_blocking(self._master, False)
        with contextlib.suppress(FileNotFoundError):
            self.link.unlink()
        self.link.symlink_to(os.ttyname(self._slave))
        self._stop.clear()
        self._rx = ProvisionalFramer()
        self._rx_buf.clear()
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
        """provisional-1: a frame on `channel`. exvectrnet-1: a DATA frame from this role's node, port=channel."""
        if self.profile == "exvectrnet-1":
            self.send_net(self.node, 0, channel, payload)
        else:
            self.write_raw(encode_frame(channel, payload))

    @property
    def node(self) -> int:
        return 1 if self.role == "vehicle" else 2

    def send_net(self, src: int, dst: int, port: int, payload: bytes, seq: int | None = None) -> None:
        """exvectrnet-1: one DATA frame from node `src` (the sequence number counts up per (src, port))."""
        if seq is None:
            seq = self._seq.get((src, port), 0)
            self._seq[(src, port)] = (seq + 1) & 0xFF
        self.write_raw(net.stream_frame(net.net_data(src, dst, port, seq, payload)))

    def send_heartbeat(self, src: int | None = None, **kw) -> None:
        src = self.node if src is None else src
        self.write_raw(net.stream_frame(net.net_heartbeat(src, role=src, **kw)))

    def heartbeats_from(self, node: int = 0) -> list[dict]:
        """Heartbeats received from `node` (default: the ground station), oldest first."""
        return [h for h in self.received_heartbeats if h["src"] == node]

    def send_console(self, text: str) -> None:
        self.write_raw(text.encode() + b"\n")

    # ---- threads

    def _tx_loop(self) -> None:
        t0 = time.monotonic()
        due = {name: 0.0 for name in self.rates}
        hb_due = 0.0
        next_console = 0.0
        while not self._stop.is_set():
            sim_t = (time.monotonic() - t0) * self.speed
            out = bytearray()
            for name, rate in self.rates.items():
                while rate > 0 and due[name] <= sim_t:
                    out += self._emit(name, due[name])
                    due[name] += 1.0 / rate
            if self.profile == "exvectrnet-1":
                while self.hb_hz > 0 and hb_due <= sim_t:
                    out += self._heartbeats(hb_due)
                    hb_due += 1.0 / self.hb_hz
            if self.console and self.profile == "provisional-1" and next_console <= sim_t:
                mode, _, phase = self.scenario.phase_at(sim_t)
                out += f"[fake-vehicle] t={sim_t:.1f}s mode={mode.name} phase={phase.name}\n".encode()
                next_console += 1.0
            if out:
                try:
                    self.write_raw(bytes(out))
                except (BlockingIOError, OSError):
                    pass  # nobody reading yet (buffer full) or gone: drop, like a real USB CDC would
            time.sleep(0.005)

    def _next_seq(self, src: int, port: int) -> int:
        seq = self._seq.get((src, port), 0)
        self._seq[(src, port)] = (seq + 1) & 0xFF
        return seq

    def _data(self, src: int, dst: int, port: int, payload: bytes) -> bytes:
        return net.stream_frame(net.net_data(src, dst, port, self._next_seq(src, port), payload))

    def _heartbeats(self, t: float) -> bytes:
        nodes = (1,) if self.role == "vehicle" else (1, 2, 3)
        ms = int(t * 1000)
        return b"".join(
            net.stream_frame(net.net_heartbeat(n, n, self.schema_hash, ms, self.net_version)) for n in nodes
        )

    def _emit(self, name: str, t: float) -> bytes:
        """Wire bytes for the packet `name` (a key of NET_*_RATES, or a provisional-1 type) at sim time t."""
        if self.profile == "provisional-1":
            chan, pack = PACKERS[name]
            return encode_frame(chan, pack(self.scenario.fields(name, t)))
        sc = self.scenario
        t_us = int(t * 1e6) & 0xFFFFFFFF
        att, pos, gps = sc.fields("Attitude", t), sc.fields("Position", t), sc.fields("Gps", t)
        vs, ms = sc.fields("VehicleState", t), sc.fields("MissionState", t)
        src = 1
        if name == "VEHICLE_STATUS":
            vs = dict(vs)
            if self.sim_state is not None:
                vs["sim"] = self.sim_state
            if self.armed_state is not None:
                vs["armed"] = self.armed_state
            zeroing = self.zeroing_until is not None and time.monotonic() < self.zeroing_until
            pl = net.pack_vehicle_status(
                int(t * 1e9),
                {**vs, "uart_peer_seen": True},
                ms["mission_mode"],
                ms["mission_phase"],
                flags_extra=128 if zeroing else 0,
                home_epoch=sc.fields("VehicleStatus", t)["home_epoch"],
                calib_state=sc.fields("VehicleStatus", t)["calib_state"],
                ctrl=(0x180, 0, 0, 255, self.cmd_rejects),  # USB_FRESH | UART_FRESH, nothing refused, no profile
                cmd_session=0 if self.cmd_session is None else self.cmd_session,
            )
            return self._data(src, 0, net.PORT_VEHICLE_STATUS, pl)
        if name == "POSE":
            return self._data(src, 0, net.PORT_POSE, net.pack_pose(t_us, att, pos))
        if name == "AIR_POSE":
            return self._data(src, 0, net.PORT_AIR_POSE, net.pack_air_pose(t_us, att, pos))
        if name == "AIR_ACTUATORS":

            def sample(tt: float) -> dict:
                a = sc.fields("Actuators", tt)
                return {"tvc": a["tvc"], "roll_torque": a["roll_torque"], "thrust": a["thrust"], "flaps": a["flaps"]}

            pl = net.pack_air_actuators(t_us, 0.020, sample(t), sample(t - 0.020))
            return self._data(src, 0, net.PORT_AIR_ACTUATORS, pl)
        if name == "AIR_MISSION":
            return self._data(src, 0, net.PORT_AIR_MISSION, net.pack_air_mission(t_us, ms))
        if name == "ACTUATORS":
            flaps = sc.fields("FlapState", t)["angle"]
            vane = sc.fields("TvcState", t)["vane"]
            pl = net.pack_actuators(
                t_us,
                flaps,
                [vane[0], vane[2]],
                vs["tvc_thrust"][2],
                vs["ground_dist"],
                sc.fields("Actuators", t)["roll_torque"],
            )
            return self._data(src, 0, net.PORT_ACTUATORS, pl)
        if name == "MISSION":
            if self.request_rules:  # the echo of SET_STOP_ALTITUDE
                ms = {**ms, "stop_altitude": self.stop_altitude}
            return self._data(src, 0, net.PORT_MISSION, net.pack_mission(t_us, ms))
        if name == "GNSS":
            return self._data(src, 0, net.PORT_GNSS, net.pack_gnss(t_us, gps))
        if name == "AIR_GNSS":
            return self._data(src, 0, net.PORT_AIR_GNSS, net.pack_air_gnss(t_us, gps))
        if name == "AIR_GNSS_INFO":
            return self._data(src, 0, net.PORT_AIR_GNSS_INFO, net.pack_air_gnss_info(t_us, gps))
        if name == "MISSION_PARAMS":
            vals = [self.mission_params[i] for i in range(1, 7)]
            return self._data(src, 0, net.PORT_MISSION_PARAMS, net.pack_mission_params(vals))
        if name == "HOME_POSITION":  # the real vehicle answers a request; the fake repeats it for GUI testing
            return self._data(src, 0, net.PORT_HOME_POSITION, net.pack_home_position(sc.fields("HomePosition", t)))
        if name == "HANDSET_TELEM":
            return self._data(src, 0, net.PORT_HANDSET, net.pack_handset(vs, gps, att, vs["mode"].name[:8]))
        if name == "IMU_RAW":
            return self._data(src, 0, net.PORT_IMU, net.pack_imu(t_us, t))
        if name == "CALIBRATION":
            return self._data(src, 0, net.PORT_CALIBRATION, net.pack_calibration(t_us))
        if name == "ESTIMATOR":
            return self._data(src, 0, net.PORT_ESTIMATOR, net.pack_estimator(t_us))
        if name == "CMD_APPLIED":  # the last accepted CMD_STATE of the latched session (no vehicle logic)
            c = self.last_cmd or {}
            flags = (1 if c.get("pocket_live") else 0) | (2 if c.get("input_virtual") else 0)
            pl = net.pack_cmd_applied(
                t_us, c.get("go", 0), c.get("profile", 0), c.get("vehicle_mode", 0), c.get("motors", 0),
                c.get("reset_button", 0), c.get("failsafe", 0), flags, 0b1111, self.cmd_session or 0,
                0.04, 0.04, math.nan, (self.cmd_rejects, 0, 0, 0),
            )  # fmt: skip
            return self._data(src, 0, net.PORT_CMD_APPLIED, pl)
        if name == "TASK_TIMING":
            return self._data(src, 0, net.PORT_TIMING, net.pack_timing(t_us))
        if name in ("LINK_STATS_TX", "LINK_STATS_RX"):
            node = 2 if name.endswith("TX") else 3
            pl = net.pack_link_stats(
                int(t * 1000), 3, 97 - node, -70 - node, 9, 0x10 | (node & 1), int(t * 25), self.tx_power
            )
            return self._data(node, 0xFF, net.PORT_LINK_STATS, pl)
        if name == "LINK_CONFIG":  # Router to ship radio node; a real Router drops it from USB (test double only)
            return self._data(2, 3, net.PORT_LINK_CONFIG, net.pack_link_config(self.link_config_power))
        if name == "POCKET_RC":  # the Pocket's RC state as the TX module forwards it
            ch = [(c - 1500) * 2 for c in sc.handset_channels(t)]
            return self._data(2, 1, net.PORT_RC_STATE, net.pack_rc_state(1, ch))
        raise KeyError(name)

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = os.read(self._master, 4096)
            except (BlockingIOError, OSError):
                time.sleep(0.005)
                continue
            if self.profile == "exvectrnet-1":
                self._rx_net(data)
                continue
            for item in self._rx.feed(data, time.monotonic_ns()):
                if isinstance(item, Frame):
                    self.received.append(item)
                elif isinstance(item, ConsoleText):
                    self.received_text.append(item.text)

    def _on_request(self, f: dict) -> None:
        """A REQUEST frame: record it, and answer if it is addressed to this node (test double only)."""
        try:
            req = net.parse_request(f["payload"])
        except Exception:
            return
        req["dst"], req["src"] = f["dst"], f["src"]
        self.received_requests.append(req)
        if f["dst"] != self.node:
            return
        n = self._req_attempts[req["req_id"]] = self._req_attempts.get(req["req_id"], 0) + 1
        if n <= self.request_drop_first:
            return
        status, detail = self.request_status, 0
        if self.request_rules and req["action"] in (7, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24):
            if req["target"] != self.node:
                status, detail = 3, 2
            elif not req["session"] or req["session"] != (self.request_latched_session or self.cmd_session):
                status, detail = 2, 3
            elif not self.request_on_ground:
                status, detail = 2, 1
            elif req["action"] == 13 and not (math.isfinite(req["arg_f"]) and req["arg_f"] >= 1.0):
                status, detail = 3, 1
            elif req["action"] == 24 and not (
                req["arg_i"] in self.mission_param_spec
                and math.isfinite(req["arg_f"])
                and self.mission_param_spec[req["arg_i"]][1] <= req["arg_f"] <= self.mission_param_spec[req["arg_i"]][2]
            ):
                status, detail = 3, 1
            elif 15 <= req["action"] <= 22 and not self.request_simulation:
                status, detail = 2, 5
            elif req["action"] in (7, 14) and self.request_calib_refuse:
                status, detail = 2, 6
            elif req["action"] == 23 and self.request_no_home:
                status, detail = 2, 4
            else:
                status, detail = 0, 0
                if req["action"] == 13:
                    self.stop_altitude = req["arg_f"]
                if req["action"] == 24:
                    self.mission_params[req["arg_i"]] = req["arg_f"]
        self.send_net(self.node, 0, net.PORT_REQUEST_ACK, net.pack_request_ack(req["req_id"], status, detail))
        if self.request_rules and req["action"] == 23 and status == 0 and self.role == "vehicle":
            # DONE, then the plain HOME_POSITION packet (like the vehicle)
            home = self.scenario.fields("HomePosition", time.monotonic())
            self.send_net(self.node, 0, net.PORT_HOME_POSITION, net.pack_home_position(home))

    def _on_cmd_state(self, f: dict) -> None:
        """A CMD_STATE frame: parsed strictly, recorded, the first valid session is latched."""
        cmd = net.parse_cmd_state(f["payload"])
        meta = {"src": f["src"], "dst": f["dst"], "seq": f["seq"], "t": time.monotonic_ns()}
        if (
            cmd is None or f["src"] != 0 or (self.role == "vehicle" and f["dst"] != self.node)
        ):  # a Router forwards dst 1
            self.cmd_rejects = (self.cmd_rejects + 1) & 0xFF
            self.received_cmd.append({**meta, "rejected": True})
            return
        self.received_cmd.append({**meta, **cmd, "rejected": False})
        if self.cmd_session is None:
            self.cmd_session = cmd["session"]
        if cmd["session"] == self.cmd_session:
            self.last_cmd = cmd

    def _rx_net(self, data: bytes) -> None:
        """What the ground station sends: DATA frames, parsed with the independent decoder."""
        for b in data:
            if b:
                self._rx_buf.append(b)
                continue
            raw, self._rx_buf = bytes(self._rx_buf), bytearray()
            try:
                f = net.parse_stream(net.cobs_decode(raw)) if raw else None
            except ValueError:
                f = None
            if f and f["type"] == "heartbeat":
                f["t"] = time.monotonic_ns()
                self.received_heartbeats.append(f)
                if self.role != "txmodule" or f["src"] != 0:
                    self.forwarded.append(f)
            elif f and self.role == "txmodule":
                self.forwarded.append(f)
            if f and f["type"] == "data" and f["port"] == net.PORT_REQUEST:
                self._on_request(f)
            if f and f["type"] == "data" and f["port"] == net.PORT_CMD_STATE:
                self._on_cmd_state(f)
            if f and f["type"] == "data":
                meta = {"src": f["src"], "dst": f["dst"], "seq": f["seq"], "ttl": f["ttl"]}
                self.received.append(Frame(channel=f["port"], payload=f["payload"], t=time.monotonic_ns(), meta=meta))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--link", default="/tmp/exvectr-fake-vehicle", help="symlink to the pty slave")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--profile", choices=["provisional-1", "exvectrnet-1"], default="provisional-1")
    ap.add_argument("--role", choices=["vehicle", "txmodule"], default="vehicle", help="exvectrnet-1 only")
    ap.add_argument("--request-status", type=int, default=0, help="REQUEST_ACK status to answer with (0 DONE, 2 ...)")
    ap.add_argument("--request-drop-first", type=int, default=0, help="swallow the first N attempts of each REQUEST id")
    ap.add_argument(
        "--request-rules",
        action="store_true",
        help="answer the vehicle actions (7, 12..23) like the vehicle executor",
    )
    ap.add_argument("--request-simulation", action="store_true", help="SIM_* requests are done")
    ap.add_argument("--request-calib-refuse", action="store_true", help="calibration requests: REJECTED_STATE 2/6")
    ap.add_argument("--request-no-home", action="store_true", help="GET_HOME_POSITION: REJECTED_STATE 2/4")
    args = ap.parse_args()
    veh = FakeVehiclePty(
        args.link, seed=args.seed, speed=args.speed, profile=args.profile, role=args.role,
        request_status=args.request_status, request_drop_first=args.request_drop_first,
        request_rules=args.request_rules,
        request_simulation=args.request_simulation,
        request_calib_refuse=args.request_calib_refuse,
        request_no_home=args.request_no_home,
    )  # fmt: skip
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
