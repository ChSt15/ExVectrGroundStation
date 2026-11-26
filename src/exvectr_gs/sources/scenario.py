"""Deterministic scripted flight used by the fake source and the fake vehicle (design section 6.5).

Pure functions of simulation time `t` (seconds) and a seed: no state, no RNG calls, so the same
(seed, t) always gives the same values. NWU world frame, quaternion body -> world.
"""

from __future__ import annotations

import math
import random
from typing import Any

from ..packets.defs.vehicle import ControlSource, MissionMode, MissionPhase, SensorState, VehicleMode

T_READY = 2.0
# VehicleStatus.calib_state of the fake vehicle once ready: gyro, accel and mag valid, ready to arm (bits 0, 1, 2, 4)
CALIB_READY = 0b10111
CALIB_FIELDS_READY = {
    "calib_gyro_ok": True,
    "calib_accel_ok": True,
    "calib_mag_ok": True,
    "calib_session": False,
    "ready_to_arm": True,
}
T_START = 4.0  # mission begins (Hover)
T_ASCENT = 6.0
T_DESCENT = 16.0
T_LANDING = 26.0
T_DONE = 30.0
G = 9.81
HOME_LAT = 0.8996  # rad, fake home point (about 51.54 deg N, 0 deg E)
HOME_LON = 0.0
HOME_ALT = 100.0  # m, GNSS datum
R_EARTH = 6_378_137.0

# (start time, vehicle mode, mission mode, mission phase)
TIMELINE = [
    (0.0, VehicleMode.Startup, MissionMode.Idle, MissionPhase.Idle),
    (1.0, VehicleMode.SensorInit, MissionMode.Idle, MissionPhase.Idle),
    (T_READY, VehicleMode.Ready, MissionMode.Idle, MissionPhase.Idle),
    (T_START, VehicleMode.Running, MissionMode.Running, MissionPhase.Hover),
    (T_ASCENT, VehicleMode.Running, MissionMode.Running, MissionPhase.Ascent),
    (T_DESCENT, VehicleMode.Running, MissionMode.Running, MissionPhase.Descent),
    (T_LANDING, VehicleMode.Running, MissionMode.Running, MissionPhase.Landing),
    (T_DONE, VehicleMode.Running, MissionMode.Finished, MissionPhase.Completed),
]


def _smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _quat_from_euler(roll: float, pitch: float, yaw: float) -> list[float]:
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return [
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    ]


class Scenario:
    def __init__(self, seed: int = 1):
        rng = random.Random(seed)
        self.seed = seed
        self._ph = [rng.uniform(0, 2 * math.pi) for _ in range(8)]

    # ---- state machine

    def phase_at(self, t: float) -> tuple[VehicleMode, MissionMode, MissionPhase]:
        cur = TIMELINE[0]
        for entry in TIMELINE:
            if t >= entry[0]:
                cur = entry
        return cur[1], cur[2], cur[3]

    # ---- kinematics

    def _env(self, t: float) -> float:
        """0 on the ground, 1 while flying (smooth in and out)."""
        return _smooth((t - T_START) / 2) * (1 - _smooth((t - T_LANDING) / 4))

    def pos(self, t: float) -> list[float]:
        if t < T_START:
            z = 0.0
        elif t < T_ASCENT:
            z = _smooth(t - T_START)
        elif t < T_DESCENT:
            z = 1 + 4 * _smooth((t - T_ASCENT) / 10)
        elif t < T_LANDING:
            z = 5 - 4 * _smooth((t - T_DESCENT) / 10)
        elif t < T_DONE:
            z = 1 - _smooth((t - T_LANDING) / 4)
        else:
            z = 0.0
        env = self._env(t)
        x = 0.5 * math.sin(0.3 * (t - T_START) + self._ph[0]) * env
        y = 0.4 * math.sin(0.23 * (t - T_START) + self._ph[1]) * env
        return [x, y, z]

    def vel(self, t: float, h: float = 1e-3) -> list[float]:
        a, b = self.pos(t - h), self.pos(t + h)
        return [(q - p) / (2 * h) for p, q in zip(a, b, strict=True)]

    def euler(self, t: float) -> list[float]:
        env = self._env(t)
        ph = self._ph
        roll = 0.05 * math.sin(0.8 * t + ph[2]) * env + 0.002 * math.sin(17 * t + ph[3])
        pitch = 0.05 * math.sin(0.6 * t + ph[4]) * env + 0.002 * math.sin(13 * t + ph[5])
        yaw = 0.1 * math.sin(0.1 * t + ph[6])
        return [roll, pitch, yaw]

    def omega(self, t: float, h: float = 1e-3) -> list[float]:
        a, b = self.euler(t - h), self.euler(t + h)
        return [(q - p) / (2 * h) for p, q in zip(a, b, strict=True)]

    # ---- packet fields (full "usb" set; sources drop what their profile does not supply)

    def fields(self, type_name: str, t: float) -> dict[str, Any]:
        return getattr(self, f"_f_{type_name}")(t)

    def _f_Attitude(self, t: float) -> dict[str, Any]:
        return {
            "omega": self.omega(t),
            "q": _quat_from_euler(*self.euler(t)),
            "tilt_acc": 0.0035 + 0.0005 * math.sin(t),
            "north_acc": 0.021,
        }

    def _lidar(self, t: float) -> float | None:
        """Fake downward lidar: valid only in its useful range (0.4 to 1.5 m, like the vehicle), else None (NaN)."""
        z = self.pos(t)[2]
        return z if 0.4 <= z <= 1.5 else None

    def _f_Position(self, t: float) -> dict[str, Any]:
        return {"pos": self.pos(t), "vel": self.vel(t), "h_acc": 0.4, "v_acc": 0.2, "ground_distance": self._lidar(t)}

    def _f_Gps(self, t: float) -> dict[str, Any]:
        """Position around the fake home point, following the fake NWU position (x north, y west, z up)."""
        x, y, z = self.pos(t)
        return {
            "lat": HOME_LAT + x / R_EARTH,
            "lon": HOME_LON - y / (R_EARTH * math.cos(HOME_LAT)),
            "alt": HOME_ALT + z,
            "vel": self.vel(t),
            "num_sats": 12,
            "fix_type": 3,
            "pos_acc": 1.5,
            "alt_acc": 2.0,
            "vel_acc": 0.1,
        }

    def _f_MissionParams(self, t: float) -> dict[str, Any]:
        return {}  # the fake source fills in its parameter values

    def _f_HomePosition(self, t: float) -> dict[str, Any]:
        return {
            "latlon_valid": True,
            "alt_valid": True,
            "simulation": True,
            "zeroing": False,
            "lat": HOME_LAT,
            "lon": HOME_LON,
            "alt": HOME_ALT,
            "h_acc": 1.0,
            "epoch": 1,
            "set_time": int(T_READY * 1e9),
        }

    def _f_VehicleState(self, t: float) -> dict[str, Any]:
        mode, _, _ = self.phase_at(t)
        ready = t >= T_READY
        sensor = SensorState.Ready if ready else SensorState.Init
        running = mode == VehicleMode.Running
        z = self.pos(t)[2]
        az = self.vel(t + 0.05)[2] - self.vel(t - 0.05)[2]
        return {
            "mode": mode,
            "sensor_gnss": sensor,
            "sensor_imu": sensor,
            "sensor_baro": sensor,
            "sensor_mag": sensor,
            "sensor_att_kf": sensor,
            "sensor_pos_kf": sensor,
            "fail_sensor": False,
            "fail_attitude": False,
            "fail_position": False,
            "fail_pos_oob": False,
            "fail_att_oob": False,
            "fail_radio_loss": False,
            "sim": True,
            "armed": running,
            "tvc_thrust": [0.1 * math.sin(t), -0.05, (G + az * 10) * 0.9] if running else [0.0, 0.0, 0.0],
            "tvc_torque": 0.01 if running else 0.0,
            "ground_dist": min(z, 2.0),
            "ground_dist_cov": 0.01,
        }

    def _f_MissionState(self, t: float) -> dict[str, Any]:
        _, mmode, phase = self.phase_at(t)
        ahead = t + 1.0
        # attitude setpoint: slowly varying unit quaternion near the vehicle attitude while Running, none otherwise
        sp_q = None
        if self.phase_at(t)[0] == VehicleMode.Running:
            r, p, y = self.euler(t)
            sp_q = _quat_from_euler(r + 0.08 * math.sin(0.5 * t), p + 0.06 * math.cos(0.4 * t), y + 0.05)
        return {
            "sp_q": sp_q,
            "sp_vel": self.vel(ahead),
            "sp_pos": self.pos(ahead),
            "mission_mode": mmode,
            "mission_phase": phase,
            "t_mission": int((t - T_START) * 1e9),
            # whole-mission clock: T-minus before T0, continuous over all steps, final value held after the mission
            "mission_elapsed": min(t, T_DONE) - T_START,
            "mission_index": 0,
            "waypoint": None,  # not the waypoint mission
        }

    def _f_FlapState(self, t: float) -> dict[str, Any]:
        def angle(tt: float) -> list[float]:  # sweeps 15..90 deg
            return [math.radians(52.5 + 37.5 * math.sin(0.7 * tt + i * math.pi / 2)) for i in range(4)]

        return {
            "angle": angle(t),
            "cmd": angle(t + 0.1),
            "enabled": self.phase_at(t)[0] == VehicleMode.Running,
        }

    def _f_TvcState(self, t: float) -> dict[str, Any]:
        mode, _, _ = self.phase_at(t)
        running = mode == VehicleMode.Running
        motor = min(1.0, max(0.0, 0.3 + 0.1 * self.pos(t)[2])) if running else 0.0
        return {
            "vane": [0.1 * math.sin(1.3 * t + i) for i in range(4)],
            "motor": [motor, motor],
            "enabled": running,
            "motors_enabled": running,
        }

    def _f_VehicleStatus(self, t: float) -> dict[str, Any]:
        mode, mmode, phase = self.phase_at(t)
        running = mode == VehicleMode.Running
        ready = t >= T_READY
        return {
            "vehicle_time": int(t * 1e9),
            "mode": mode,
            "mission_mode": mmode,
            "mission_phase": phase,
            "ctrl_source": ControlSource.NONE,
            "armed": running,
            "simulation": True,
            "motors_enabled": False,
            "gs_present": True,
            "uart_peer_seen": True,
            "incompatible": False,
            "failsafe": False,
            "failure_bits": 0,
            "sensor_state": 0xAAA if ready else 0,  # six sensors Ready (2) / Init (0)
            "last_req_id": 0,
            "last_req_status": 0,
            "rc_age": None,
            "home_epoch": 1 if ready else 0,
            "calib_state": CALIB_READY if ready else 0,
            **(CALIB_FIELDS_READY if ready else {}),
        }

    def _f_Actuators(self, t: float) -> dict[str, Any]:
        running = self.phase_at(t)[0] == VehicleMode.Running
        az = self.vel(t + 0.05)[2] - self.vel(t - 0.05)[2]
        return {
            "flaps": self._f_FlapState(t + 0.1)["angle"],
            "tvc": [0.05 * math.sin(1.1 * t), 0.04 * math.cos(0.9 * t)] if running else [0.0, 0.0],
            "thrust": (G + az * 10) * 0.9 if running else 0.0,
            "roll_torque": 0.02 * math.sin(0.5 * t) if running else 0.0,
        }

    def _f_ImuRaw(self, t: float) -> dict[str, Any]:
        w = self.omega(t)
        bias = [0.01, -0.02, 0.005]
        return {
            "gyro": [w[i] + bias[i] + 0.002 * math.sin(37 * t + i) for i in range(3)],
            "accel": [0.05 * math.sin(3 * t), 0.1, G + 0.2 * math.sin(2 * t)],
            "mag": [2e-5, 0.0, 4e-5],
            "pressure": 101325.0 - 12.0 * self.pos(t)[2],
            "temperature": 293.15,
        }

    def _f_Estimator(self, t: float) -> dict[str, Any]:
        k = 1.0 + 0.3 * math.sin(0.5 * t)
        return {
            "att_std": [0.01 * k, 0.01 * k, 0.03 * k],
            "vel_std": [0.1 * k, 0.1 * k, 0.05 * k],
            "pos_std": [0.5 * k, 0.5 * k, 0.3 * k],
        }

    def _f_TaskTiming(self, t: float) -> dict[str, Any]:
        avg = [1e-4 + 1e-5 * i + 5e-6 * math.sin(t + i) for i in range(4)] + [math.nan] * 4
        return {"count": 4, "avg": avg, "max": [a * 2 for a in avg[:4]] + [math.nan] * 4}

    def _f_Calibration(self, t: float) -> dict[str, Any]:
        eye = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        return {
            "raw_frame": 0,
            "gyro_source": 3,
            "accel_source": 3,
            "mag_source": 3,
            "gyro_offset": [0.01, -0.02, 0.0],
            "gyro_matrix": eye,
            "accel_offset": [0.0, 0.1, 0.0],
            "accel_matrix": eye,
            "mag_offset": [0.0, 0.0, 0.0],
            "mag_matrix": eye,
            "gyro_bias_est": [0.0, 0.0, 0.005],
        }

    def _f_RadioLinkStats(self, t: float) -> dict[str, Any]:
        return {
            "rssi_dbm": [-71 + 2 * math.sin(t), -74 + 2 * math.cos(t)],
            "lq_pct": 98.0,
            "snr_db": 9.5,
            "up_hz": 50.0,
            "down_hz": 48.0,
            "antenna": 0,
        }

    def _module_stats(self, t: float, lq: int, rssi: int, snr: int) -> dict[str, Any]:
        n = int(t * 2)
        return {
            "module_uptime": t,
            "air_state": 3,
            "rx_lq": lq,
            "rssi": rssi,
            "snr": snr,
            "tx_power": 10,
            "rx_antenna": 0,
            "tx_antenna": 1,
            "air_rx_ok": n % 65536,
            "air_rx_bad": 0,
            "frames_dropped": 0,
            "frames_expired": 0,
            "queue_peak": 5,
        }

    def _f_TxModuleStats(self, t: float) -> dict[str, Any]:
        # the Router hears the ship: downlink quality (differs from the uplink on purpose)
        return self._module_stats(t, 92, -62, 8)

    def _f_RxModuleStats(self, t: float) -> dict[str, Any]:
        # the ship radio node hears the ground station: uplink quality
        return self._module_stats(t, 96, -77, 6)

    def handset_channels(self, t: float) -> list[int]:
        ch = [1500] * 16
        ch[2] = 1000
        ch[0] = 1500 + int(100 * math.sin(t))
        return ch
