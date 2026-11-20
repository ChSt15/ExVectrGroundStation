"""Packet types written only by offline importers (direction LOCAL, never on the wire)."""

from ..base import Direction, Packet
from ..fields import f
from ..registry import packet


@packet(
    "LegacyRow",
    version=1,
    direction=Direction.LOCAL,
    nominal_rate_hz=10,
    history=False,
    desc="One row of an old ExVectrController CSV log, columns as logged (names and units of the old logger, no "
    "frame or unit conversion), written by `exgs log import-legacy` next to the converted packets so nothing is "
    "lost (docs/legacy-logs.md). A field is None when the file's layout has no value for that column.",
)
class LegacyRow(Packet):
    layout: str = f(desc="detected column layout (docs/legacy-logs.md)")
    Index: int = f(desc="row index as logged")
    LocalTime: float = f(unit="s", desc="handset (logger) uptime")
    MissionTime: float = f(unit="s", desc="vehicle mission time, negative during countdown")
    SetPositionX: float = f(unit="m")
    SetPositionY: float = f(unit="m")
    SetPositionZ: float = f(unit="m")
    PositionX: float = f(unit="m")
    PositionY: float = f(unit="m")
    PositionZ: float = f(unit="m")
    VelocityX: float = f(unit="m/s")
    VelocityY: float = f(unit="m/s")
    VelocityZ: float = f(unit="m/s")
    AttitudeQ0: float = f()
    AttitudeQ1: float = f()
    AttitudeQ2: float = f()
    AttitudeQ3: float = f()
    GPSLat: float = f(unit="rad", desc="logged in radians (the old header comment says degrees; see the docs)")
    GPSLon: float = f(unit="rad")
    GPSAlt: float = f(unit="m")
    GPSVelX: float = f(
        unit="m/s", desc="in layout pacc-22 reconstructed from the two printed 32-bit words (docs/legacy-logs.md)"
    )
    GPSVelY: float = f(unit="m/s")
    GPSVelZ: float = f(unit="m/s")
    GPSPACC: float = f(unit="m", desc="GNSS position accuracy")
    GPSVACC: float = f(unit="m/s", desc="GNSS velocity accuracy (the logger prints velocityAccuracy here)")
    GPSNumSats: int = f()
    VehicleMode: int = f(desc="old VehicleMode enum value as logged")
    MissionMode: int = f(desc="old MissionMode enum value as logged")
    TVCThrust: float = f(unit="N", desc="layout tvc-28 only: one thrust value")
    TVCThrustX: float = f(unit="N")
    TVCThrustY: float = f(unit="N")
    TVCThrustZ: float = f(unit="N")
    TVCTorque: float = f(unit="N*m")
    GroundDistance: float = f(unit="m")
    GroundDistanceCov: float = f(unit="m^2")
    Tail: list[int] = f(
        length=3, desc="the three trailing integers of the 22-value layouts exactly as printed (docs/legacy-logs.md)"
    )
