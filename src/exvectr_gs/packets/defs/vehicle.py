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


class ControlSource(IntEnum):
    """Active control source as applied by the vehicle (`ControlSource` in ExVectrPackets)."""

    NONE = 0
    RADIO = 1
    USB = 2
    UART = 3


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
    desc="GNSS fix, lat/lon in radians (provisional-1: legacy wire holds rad x 1e7, a known issue).",
)
class Gps(Packet):
    lat: float = f(unit="rad")
    lon: float = f(unit="rad")
    alt: float = f(unit="m")
    vel: list[float] = f(unit="m/s", labels=("x", "y", "z"))
    num_sats: int = f()
    fix_type: int | None = f(desc="GNSS fix type as reported (exvectrnet-1 profile only)")
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
    waypoint: int = f(
        desc="current waypoint of the waypoint mission; only AIR_MISSION carries it, None = not that mission"
    )


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


@packet(
    "VehicleStatus",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=5,
    history=False,
    desc="exvectrnet-1 VEHICLE_STATUS: what the vehicle applied (mode, control source, motor gate, failsafe).",
)
class VehicleStatus(Packet):
    vehicle_time: int = f(unit="ns", kind="time_ns", desc="vehicle clock, not the host clock")
    mode: VehicleMode = f()
    mission_mode: MissionMode = f()
    mission_phase: MissionPhase = f()
    ctrl_source: ControlSource = f(desc="active control source as applied by the vehicle")
    armed: bool = f(desc="applied arm state")
    simulation: bool = f()
    motors_enabled: bool = f(desc="the hard motor gate is currently open")
    gs_present: bool = f()
    uart_peer_seen: bool = f(
        desc="the vehicle heard the ship radio node (node 3) on its UART in the last 3 s; information only"
    )
    zeroing: bool = f(
        desc="the vehicle is zeroing its estimators (VEHICLE_STATUS flag ZEROING, bit 7); information only"
    )
    incompatible: bool = f(desc="vehicle reports a version or schema mismatch")
    failsafe: bool = f()
    failure_bits: int = f()
    sensor_state: int = f(desc="bit field, numbering owned by the flight software")
    last_req_id: int = f(desc="last executed REQUEST id, 0 = none")
    last_req_status: int = f()
    rc_age: float | None = f(unit="s", desc="age of the last accepted RC_STATE on the vehicle, NaN = none")


@packet(
    "Actuators",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=10,
    history=True,
    desc="exvectr_packets ACTUATORS: flap angles, TVC deflection, thrust, ground distance.",
)
class Actuators(Packet):
    flaps: list[float] = f(unit="rad", labels=("tl", "tr", "bl", "br"), desc="0 = extended, increasing = folded")
    tvc: list[float] = f(unit="rad", labels=("x", "y"), desc="thrust vector deflection")
    thrust: float = f(unit="N")
    ground_distance: float = f(unit="m", desc="NaN if unavailable")
    roll_torque: float = f(unit="N*m", desc="TVC twist (roll) torque about body Z; only AIR_ACTUATORS carries it")


@packet(
    "HandsetTelem",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=2,
    history=False,
    desc="Display-ready values the vehicle sends for the handset (HANDSET_TELEM), shown as sent.",
)
class HandsetTelem(Packet):
    armed: bool = f()
    failure: bool = f()
    simulation: bool = f()
    gnss_fix: bool = f()
    mode_text: str = f()
    latitude: float = f(unit="rad")
    longitude: float = f(unit="rad")
    ground_speed: float = f(unit="m/s")
    heading: float = f(unit="rad")
    altitude: float = f(unit="m", desc="above the reference")
    satellites: int = f()
    vertical_speed: float = f(unit="m/s")
    pitch: float = f(unit="rad")
    roll: float = f(unit="rad")
    yaw: float = f(unit="rad")
    battery: float = f(unit="V", desc="NaN = unknown")


@packet(
    "ImuRaw",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=100,
    history=False,
    desc="Debug set (USB only): raw sensor values.",
)
class ImuRaw(Packet):
    gyro: list[float] = f(unit="rad/s", labels=("x", "y", "z"))
    accel: list[float] = f(unit="m/s^2", labels=("x", "y", "z"))
    mag: list[float] = f(unit="T", labels=("x", "y", "z"))
    pressure: float = f(unit="Pa")
    temperature: float = f(unit="K")


@packet(
    "Estimator",
    version=2,
    direction=Direction.DOWN,
    nominal_rate_hz=20,
    history=False,
    desc="Debug set (USB only): estimator accuracy as standard deviations. Version 2: att_std is the 1 sigma of "
    "small rotations about the WORLD axes [North, West, Up] (it was a per-quaternion-axis value before).",
)
class Estimator(Packet):
    att_std: list[float] = f(
        unit="rad", labels=("north", "west", "up"), desc="1 sigma of rotation about the WORLD axes [North, West, Up]"
    )
    vel_std: list[float] = f(unit="m/s", labels=("x", "y", "z"))
    pos_std: list[float] = f(unit="m", labels=("x", "y", "z"))


@packet(
    "TaskTiming",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=5,
    history=False,
    desc="Debug set (USB only): execution time of the first scheduler tasks (NaN = invalid entry).",
)
class TaskTiming(Packet):
    count: int = f(desc="number of valid entries, at most 8")
    avg: list[float] = f(unit="s", length=8)
    max: list[float] = f(unit="s", length=8)


@packet(
    "Calibration",
    version=1,
    direction=Direction.DOWN,
    nominal_rate_hz=0.5,
    history=False,
    desc="CALIBRATION (USB only, every 2 s): the sensor calibration the vehicle applies. For each sensor "
    "cal = M * (raw - offset), M row-major (M[r][c] = matrix[3r + c]), raw = ImuRaw in raw_frame; SI. The ground "
    "station only displays the result.",
)
class Calibration(Packet):
    raw_frame: int = f(desc="frame of the ImuRaw values the parameters apply to (0 = SENSOR, driver output)")
    gyro_source: int = f(
        desc="bits: 0 calib EEPROM, 1 transform EEPROM, 2 offset override, 3 RAM only, 4 memory cleared"
    )
    accel_source: int = f(desc="as gyro_source")
    mag_source: int = f(desc="as gyro_source")
    gyro_offset: list[float] = f(unit="rad/s", length=3, labels=("x", "y", "z"))
    gyro_matrix: list[float] = f(length=9, desc="row-major 3x3, scale and misalignment times sensor to body")
    accel_offset: list[float] = f(unit="m/s^2", length=3, labels=("x", "y", "z"))
    accel_matrix: list[float] = f(length=9, desc="row-major 3x3")
    mag_offset: list[float] = f(unit="T", length=3, labels=("x", "y", "z"))
    mag_matrix: list[float] = f(length=9, desc="row-major 3x3, dimensionless")
    gyro_bias_est: list[float] = f(unit="rad/s", length=3, labels=("x", "y", "z"), desc="EKF gyro bias, body frame")
