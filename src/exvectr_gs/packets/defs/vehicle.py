"""Packet types reported by the vehicle (design section 3.2)."""

from enum import IntEnum

from ..base import Direction, Packet
from ..fields import f
from ..registry import packet


class VehicleMode(IntEnum):
    Startup = 0
    SensorInit = 1
    SensorCalib = 2
    Ready = 3
    Running = 4
    Failure = 5


class SensorState(IntEnum):
    """`TelemetrySensor` in the flight software."""

    Init = 0
    Calib = 1
    Ready = 2
    Failure = 3


class MissionMode(IntEnum):
    Idle = 0
    Initialisation = 1
    Startup = 2
    Running = 3
    Finished = 4


class MissionPhase(IntEnum):
    Idle = 0
    Hover = 1
    Ascent = 2
    Descent = 3
    Deceleration = 4
    RTH = 5
    Landing = 6
    Completed = 7


@packet(
    "Attitude",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=5,
    history=True,
    desc="Attitude estimate. q rotates body -> world (NWU).",
)
class Attitude(Packet):
    omega: list[float] = f(unit="rad/s", labels=("x", "y", "z"), desc="angular velocity, body frame")
    q: list[float] = f(labels=("w", "x", "y", "z"), desc="unit quaternion, body -> world")
    tilt_acc: float | None = f(unit="rad")
    north_acc: float | None = f(unit="rad")


@packet(
    "Position",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=10,
    history=True,
    desc="Position and velocity estimate in the NWU world frame.",
)
class Position(Packet):
    pos: list[float] = f(unit="m", labels=("x", "y", "z"), desc="position, NWU")
    vel: list[float] = f(unit="m/s", labels=("x", "y", "z"), desc="velocity, NWU")
    h_acc: float | None = f(desc="horizontal accuracy. Legacy value has no physical unit (known issue)")
    v_acc: float | None = f(desc="vertical accuracy. Legacy value has no physical unit (known issue)")


@packet(
    "Gps",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=1,
    history=False,
    desc="GNSS fix. Legacy wire holds rad x 1e7 (known issue), decoded here as radians.",
)
class Gps(Packet):
    lat: float = f(unit="rad")
    lon: float = f(unit="rad")
    alt: float = f(unit="m")
    vel: list[float] = f(unit="m/s", labels=("x", "y", "z"))
    num_sats: int = f()
    pos_acc: float = f(unit="m")
    alt_acc: float = f(unit="m")
    vel_acc: float = f(unit="m/s")


@packet(
    "VehicleState",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=5,
    history=False,
    desc="Vehicle mode, sensor and failure states as reported by the vehicle.",
)
class VehicleState(Packet):
    mode: VehicleMode = f()
    sensor_gnss: SensorState = f()
    sensor_imu: SensorState = f()
    sensor_baro: SensorState = f()
    sensor_mag: SensorState = f()
    sensor_att_kf: SensorState = f()
    sensor_pos_kf: SensorState = f()
    fail_sensor: bool = f()
    fail_attitude: bool = f()
    fail_position: bool = f()
    fail_pos_oob: bool = f(desc="position out of bounds")
    fail_att_oob: bool = f(desc="attitude out of bounds")
    fail_radio_loss: bool = f()
    sim: bool = f(desc="vehicle is running its on-board simulation")
    armed: bool = f()
    tvc_thrust: list[float] = f(unit="N", labels=("x", "y", "z"))
    tvc_torque: float = f(unit="N*m")
    ground_dist: float = f(unit="m")
    ground_dist_cov: float = f()


@packet(
    "MissionState",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=2,
    history=False,
    desc="Mission progress and guidance setpoints.",
)
class MissionState(Packet):
    sp_vel: list[float] = f(unit="m/s", labels=("x", "y", "z"), desc="velocity setpoint, NWU")
    sp_pos: list[float] = f(unit="m", labels=("x", "y", "z"), desc="position setpoint, NWU")
    mission_mode: MissionMode = f()
    mission_phase: MissionPhase = f()
    t_mission: int = f(unit="ns", kind="time_ns", desc="mission time, negative during countdown")
    mission_index: int = f()


@packet(
    "FlapState",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=10,
    history=True,
    desc="Planned type. Flap angles: 90 deg retracted, 0 deg extended.",
)
class FlapState(Packet):
    angle: list[float] = f(unit="rad", labels=("tl", "tr", "bl", "br"))
    cmd: list[float] = f(unit="rad", labels=("tl", "tr", "bl", "br"))
    enabled: bool = f()


@packet(
    "TvcState",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=10,
    history=True,
    desc="Planned type, USB debug set. TVC vane angles and motor commands.",
)
class TvcState(Packet):
    vane: list[float] = f(unit="rad", labels=("xp", "xn", "yp", "yn"))
    motor: list[float] = f(unit="1", labels=("m1", "m2"), desc="0..1")
    enabled: bool = f()
    motors_enabled: bool = f()
