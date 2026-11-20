"""Import of the old ExVectrController CSV mission logs into `.exlog` (docs/legacy-logs.md).

Stdlib only. Each CSV becomes one `.exlog` written through the real `Recorder`, so the file has the normal header,
`#type` and `#end` lines and `exgs log validate` passes. Every row gives the converted packets (Position, Attitude,
MissionState, VehicleState, VehicleStatus, Gps, Actuators, where the layout has the columns) plus one `LegacyRow`
with the columns as logged, so nothing is lost.

Conventions (determined from the old sources and checked against the data; evidence in docs/legacy-logs.md):
position, velocity and setpoints are already NWU metres / m/s, the quaternion is already [w,x,y,z] body -> world,
GNSS lat/lon are already radians. So the conversion is a re-labelling, not a transformation; only time
(seconds -> ns) and the garbled columns of two early layouts need work.
"""

from __future__ import annotations

import dataclasses
import filecmp
import hashlib
import json
import math
import os
import struct
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .. import __version__
from ..core.bus import Bus
from ..packets import REGISTRY, Packet
from ..packets.defs.host import Note
from ..packets.defs.legacy import LegacyRow
from ..packets.defs.vehicle import (
    Actuators,
    Attitude,
    Gps,
    MissionMode,
    MissionState,
    Position,
    VehicleMode,
    VehicleState,
    VehicleStatus,
)
from .recorder import Recorder

CONVERTER = "legacy-import-1"
SRC = "legacy"
S = 1_000_000_000

_BASE = (
    "Index,LocalTime,MissionTime,SetPositionX,SetPositionY,SetPositionZ,PositionX,PositionY,PositionZ,"
    "VelocityX,VelocityY,VelocityZ,AttitudeQ0,AttitudeQ1,AttitudeQ2,AttitudeQ3,GPSLat,GPSLon,GPSAlt"
).split(",")
_GPS_PACC = "GPSVelX,GPSVelY,GPSVelZ,GPSPACC,GPSVACC,GPSNumSats,VehicleMode,MissionMode".split(",")
_GPS_HDOP = "GPSVelX,GPSVelY,GPSVelZ,GPSHDOP,GPSVDOP,GPSNumSats,VehicleMode,MissionMode".split(",")
_TVC4 = ["TVCThrustX", "TVCThrustY", "TVCThrustZ", "TVCTorque"]


@dataclass(frozen=True)
class Layout:
    name: str
    header: tuple[str, ...]
    n_values: int  # values per data row (the 22-value layouts print fewer values than the header names)
    desc: str
    tail: tuple[str, ...] | None = None  # meaning of the values after GPSAlt in the 22-value layouts


LAYOUTS: tuple[Layout, ...] = (
    Layout(
        "hdop-22",
        tuple(_BASE + _GPS_HDOP),
        22,
        "header names 27 columns (GPSHDOP/GPSVDOP) but the logger printed 22 values: the 19 base columns, then "
        "GPSNumSats, VehicleMode, MissionMode. GPS velocity, HDOP and VDOP were never printed.",
        tail=("GPSNumSats", "VehicleMode", "MissionMode"),
    ),
    Layout(
        "pacc-22",
        tuple(_BASE + _GPS_PACC),
        22,
        "header names 27 columns (GPSPACC/GPSVACC) but the format string still had 22 conversions: the 19 base "
        "columns, then three %d that read the 32-bit words of the double arguments: low and high word of GPSVelX "
        "(reconstructed), low word of GPSVelY (unusable). GPSVelY/Z, accuracies, satellites and both modes are lost.",
        tail=("GPSVelX.lo", "GPSVelX.hi", "GPSVelY.lo"),
    ),
    Layout("tvc-28", tuple(_BASE + _GPS_PACC + ["TVCThrust"]), 28, "as logged, plus one thrust value TVCThrust."),
    Layout("tvc-31", tuple(_BASE + _GPS_PACC + _TVC4), 31, "as logged, plus TVC thrust vector and torque."),
    Layout(
        "tvc-ground-33",
        tuple(_BASE + _GPS_PACC + _TVC4 + ["GroundDistance", "GroundDistanceCov"]),
        33,
        "as logged (the last old data_logger.hpp), plus ground distance and its covariance.",
    ),
)
_INT_COLS = {"Index", "GPSNumSats", "VehicleMode", "MissionMode"}

# Dates of the original files, recorded from the SD card modification times (= handset clock when the logger
# closed the file) of the copies made on 2026-11-20, because git does not keep mtimes. Key: first 16 hex digits
# of the file's sha256. Value: (end of logging as epoch seconds or None, date, how it is known).
_FAT_ZERO = "no clock: the SD card time is the FAT zero date plus uptime"
_EST_EARLY = (
    f"estimated: {_FAT_ZERO}; by logger version (layout) after logdata.txt (2025-04-28) and before "
    "missionData_flight4 (2025-06-19)"
)
KNOWN_FILES: dict[str, tuple[int | None, str, str]] = {
    "19ea3b33c2c3f890": (1745830236, "2025-04-28", "SD card mtime (end of logging)"),
    "d03ed811e183a03e": (1756988625, "2025-09-04", "SD card mtime (end of logging)"),
    "184c2f7a725dbe84": (1756988966, "2025-09-04", "SD card mtime (end of logging)"),
    "9c7077f484e310aa": (1756989203, "2025-09-04", "SD card mtime (end of logging)"),
    "7476fb57f6d55446": (1756997256, "2025-09-04", "SD card mtime (end of logging)"),
    "0072836836f7fc3f": (1757007146, "2025-09-04", "SD card mtime (end of logging)"),
    "d45a62429a2719d3": (1757007364, "2025-09-04", "SD card mtime (end of logging)"),
    "2c3b2d09f61ff0a0": (1756580963, "2025-08-30", "SD card mtime (end of logging)"),
    "a7ce4ca75c09e176": (1750862912, "2025-06-25", "SD card mtime (end of logging)"),
    "5a4486a95019a1e6": (1750318069, "2025-06-19", "SD card mtime (end of logging)"),
    "f0d0a5082d5a3c5c": (1750607458, "2025-06-22", "SD card mtime (end of logging)"),
    "f3105556b8a522a0": (None, "2025-04-28", _EST_EARLY),
    "92c0bade7e159e81": (None, "2025-04-28", _EST_EARLY),
    "4d1f5151563ca87f": (None, "2025-04-28", _EST_EARLY),
    "0238ced40635e238": (None, "2025-04-28", _EST_EARLY),
    "6539b3c99a560229": (None, "2025-04-28", _EST_EARLY),
    "0b4e5e0ff3821a2c": (None, "2025-04-28", _EST_EARLY),
    "ddb78135476bcfd6": (None, "2025-04-28", _EST_EARLY),
    "7dde2879f937243e": (None, "2025-04-28", _EST_EARLY),
    "6832fb1cea2010af": (None, "2025-04-28", _EST_EARLY),
    "cdfd41d600d7cdc6": (None, "2025-04-28", _EST_EARLY),
}

CONVENTIONS = (
    "Time: packet time = base + LocalTime (handset uptime when the row was logged, s -> ns); base puts the last row "
    "at the SD card modification time where that is known (timezone of the handset clock unverified, about +-2 h), "
    "else 12:00 UTC of the estimated date: only relative times are reliable. Rows are 10 Hz snapshots of the latest "
    "telemetry the handset had received, so values can repeat. MissionTime (s) -> MissionState.t_mission (ns); "
    "the old firmware restarts it at some phase changes. "
    "Frames: position, velocity, setpoint position and GNSS velocity are already NWU (X North, Y West, Z Up) in "
    "m and m/s; copied unchanged. Attitude AttitudeQ0..Q3 is already [w,x,y,z] body -> world; copied unchanged "
    "(no angular velocity in the logs: omega None). GNSS lat/lon are radians (the old header comment says "
    "degrees, the data are radians); copied unchanged; GPSAlt m. GPSPACC -> Gps.pos_acc (m), GPSVACC -> "
    "Gps.vel_acc (m/s: the logger prints velocityAccuracy). TVCThrustX/Y/Z (N, body frame) -> "
    "VehicleState.tvc_thrust; TVCTorque (N*m) -> VehicleState.tvc_torque and Actuators.roll_torque (assumed roll "
    "torque about body Z); Actuators.thrust = |TVCThrust vector| (derived) or TVCThrust (layout tvc-28); "
    "GroundDistance (m) -> VehicleState.ground_dist and Actuators.ground_distance; GroundDistanceCov (m^2) -> "
    "VehicleState.ground_dist_cov. VehicleMode/MissionMode: same enum numbering as the old telemetry.hpp; values "
    "outside the enum -> None. Not in the logs (None): omega, sp_vel, mission_phase, sensor and failure states, "
    "armed, simulation. Lat/lon/alt all exactly 0 -> no fix (Gps lat/lon/alt None). A quaternion with norm < 0.5 "
    "-> q None. Every original column is also kept as LegacyRow."
)


# ---------------------------------------------------------------- parsing


class LegacyFormatError(ValueError):
    pass


@dataclass
class ParsedFile:
    layout: Layout
    rows: list[dict[str, object]]  # column name -> value (int/float), with "Tail" for the 22-value layouts
    bad_rows: list[str] = field(default_factory=list)  # "line N: reason"
    truncated_last_line: bool = False


def detect_layout(header: Sequence[str]) -> Layout:
    h = tuple(c.strip() for c in header)
    for lay in LAYOUTS:
        if lay.header == h:
            return lay
    raise LegacyFormatError(f"unknown header ({len(h)} columns: {','.join(h)[:200]})")


def words_to_double(lo: int, hi: int) -> float:
    """Rebuild a double from the two 32-bit words a `%d` printed (little-endian ESP32, low word first).

    The double is a promoted float, so the result is rounded to float precision (shortest form, 7 digits)."""
    v = struct.unpack("<d", struct.pack("<II", lo & 0xFFFFFFFF, hi & 0xFFFFFFFF))[0]
    return float(f"{v:.7g}") if math.isfinite(v) else v


def _num(col: str, tok: str) -> object:
    tok = tok.strip()
    if col in _INT_COLS or col.startswith("Tail"):
        return int(tok)
    return float(tok)


def parse_row(layout: Layout, values: Sequence[str]) -> dict[str, object]:
    if len(values) != layout.n_values:
        raise LegacyFormatError(f"{len(values)} values, layout {layout.name} has {layout.n_values}")
    if layout.tail is None:
        return {c: _num(c, v) for c, v in zip(layout.header, values, strict=True)}
    row: dict[str, object] = {c: _num(c, v) for c, v in zip(_BASE, values[:19], strict=True)}
    tail = [int(v) for v in values[19:]]
    row["Tail"] = tail
    if layout.name == "hdop-22":
        row.update(zip(layout.tail, tail, strict=True))
    else:  # pacc-22
        row["GPSVelX"] = words_to_double(tail[0], tail[1])
    return row


def parse_csv(text: str) -> ParsedFile:
    lines = text.split("\n")
    truncated = bool(lines) and lines[-1] != ""
    if truncated:
        lines = lines[:-1]  # a last line without newline is a cut-off write
    lines = [ln.rstrip("\r") for ln in lines]
    if not lines or not lines[0].strip():
        raise LegacyFormatError("empty file")
    layout = detect_layout(lines[0].split(","))
    pf = ParsedFile(layout, [], truncated_last_line=truncated)
    for i, ln in enumerate(lines[1:], start=2):
        if not ln.strip():
            continue
        try:
            row = parse_row(layout, ln.split(","))
        except (ValueError, LegacyFormatError) as e:
            pf.bad_rows.append(f"line {i}: {e}")
            continue
        pf.rows.append(row)
    if not pf.rows:
        raise LegacyFormatError("no valid data rows")
    return pf


# ---------------------------------------------------------------- conversion to packets


def _vec(row: dict, *cols: str) -> list[float] | None:
    vals = [row.get(c) for c in cols]
    if all(v is None for v in vals):
        return None
    return [float("nan") if v is None else float(v) for v in vals]  # type: ignore[arg-type]


def _enum(cls, v):
    if v is None:
        return None
    try:
        return cls(int(v))
    except ValueError:
        return None


def row_packets(row: dict[str, object], t: int, layout: Layout) -> list[Packet]:
    """The converted packets for one row (all at time `t`)."""
    g = row.get
    out: list[Packet] = [
        Position(t=t, src=SRC, pos=_vec(row, "PositionX", "PositionY", "PositionZ"),
                 vel=_vec(row, "VelocityX", "VelocityY", "VelocityZ")),
    ]  # fmt: skip
    q = _vec(row, "AttitudeQ0", "AttitudeQ1", "AttitudeQ2", "AttitudeQ3")
    if q is not None and math.sqrt(sum(x * x for x in q)) < 0.5:
        q = None
    out.append(Attitude(t=t, src=SRC, q=q))
    vmode = _enum(VehicleMode, g("VehicleMode"))
    mmode = _enum(MissionMode, g("MissionMode"))
    mt = g("MissionTime")
    out.append(
        MissionState(
            t=t,
            src=SRC,
            sp_pos=_vec(row, "SetPositionX", "SetPositionY", "SetPositionZ"),
            mission_mode=mmode,
            t_mission=None if mt is None else round(float(mt) * S),  # type: ignore[arg-type]
        )
    )
    tvc = _vec(row, *_TVC4[:3]) if "TVCThrustX" in row else None
    vs = VehicleState(
        t=t,
        src=SRC,
        mode=vmode,
        tvc_thrust=tvc,
        tvc_torque=g("TVCTorque"),  # type: ignore[arg-type]
        ground_dist=g("GroundDistance"),  # type: ignore[arg-type]
        ground_dist_cov=g("GroundDistanceCov"),  # type: ignore[arg-type]
    )
    if any(getattr(vs, k) is not None for k in ("mode", "tvc_thrust", "tvc_torque", "ground_dist")):
        out.append(vs)
    if vmode is not None or mmode is not None:
        out.append(VehicleStatus(t=t, src=SRC, mode=vmode, mission_mode=mmode))
    lat, lon, alt = g("GPSLat"), g("GPSLon"), g("GPSAlt")
    nofix = lat == 0 and lon == 0 and alt == 0
    out.append(
        Gps(
            t=t,
            src=SRC,
            lat=None if nofix else lat,  # type: ignore[arg-type]
            lon=None if nofix else lon,  # type: ignore[arg-type]
            alt=None if nofix else alt,  # type: ignore[arg-type]
            vel=_vec(row, "GPSVelX", "GPSVelY", "GPSVelZ"),
            num_sats=g("GPSNumSats"),  # type: ignore[arg-type]
            pos_acc=g("GPSPACC"),  # type: ignore[arg-type]
            vel_acc=g("GPSVACC"),  # type: ignore[arg-type]
        )
    )
    thrust = g("TVCThrust")
    if thrust is None and tvc is not None:
        thrust = math.sqrt(sum(x * x for x in tvc))
    if thrust is not None or "TVCTorque" in row or "GroundDistance" in row:
        out.append(
            Actuators(
                t=t,
                src=SRC,
                thrust=thrust,  # type: ignore[arg-type]
                roll_torque=g("TVCTorque"),  # type: ignore[arg-type]
                ground_distance=g("GroundDistance"),  # type: ignore[arg-type]
            )
        )
    names = {fl.name for fl in dataclasses.fields(LegacyRow)}
    vals = {k: v for k, v in row.items() if k in names}
    out.append(LegacyRow(t=t, src=SRC, layout=layout.name, **vals))  # type: ignore[arg-type]
    return out


# ---------------------------------------------------------------- summary


@dataclass
class Summary:
    rows: int = 0
    bad_rows: int = 0
    duration_s: float = 0.0
    max_altitude_m: float | None = None  # max PositionZ (above the reset point)
    max_gps_alt_gain_m: float | None = None
    max_speed_m_s: float | None = None
    max_horizontal_distance_m: float | None = None
    mission_modes_seen: list[str] = field(default_factory=list)
    last_mission_mode: str | None = None
    vehicle_modes_seen: list[str] = field(default_factory=list)
    last_vehicle_mode: str | None = None
    max_gnss_sats: int | None = None
    gnss_position_changes: bool = False
    mission_time_resets: int = 0
    time_backwards: int = 0
    max_time_gap_s: float = 0.0
    nan_values: int = 0
    position_jumps: int = 0  # consecutive rows more than 5 m apart
    invalid_enum_values: int = 0
    inferred_kind: str = ""

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def summarise(pf: ParsedFile) -> Summary:
    s = Summary(rows=len(pf.rows), bad_rows=len(pf.bad_rows))
    rows = pf.rows
    times = [float(r["LocalTime"]) for r in rows]  # type: ignore[arg-type]
    s.duration_s = round(times[-1] - times[0], 3)
    for a, b in zip(times, times[1:], strict=False):
        if b < a:
            s.time_backwards += 1
        s.max_time_gap_s = max(s.max_time_gap_s, round(b - a, 3))
    z = [float(r["PositionZ"]) for r in rows]  # type: ignore[arg-type]
    s.max_altitude_m = max(z)
    alts = [float(r["GPSAlt"]) for r in rows if r["GPSAlt"] != 0]  # type: ignore[arg-type]
    if alts:
        s.max_gps_alt_gain_m = round(max(alts) - alts[0], 2)
    s.max_speed_m_s = round(
        max(math.sqrt(sum(float(r[c]) ** 2 for c in ("VelocityX", "VelocityY", "VelocityZ"))) for r in rows), 2
    )  # type: ignore[arg-type]
    s.max_horizontal_distance_m = round(max(math.hypot(r["PositionX"], r["PositionY"]) for r in rows), 2)  # type: ignore[arg-type]
    for r in rows:
        s.nan_values += sum(1 for v in r.values() if isinstance(v, float) and v != v)
    prev = None
    for r in rows:
        p = (r["PositionX"], r["PositionY"], r["PositionZ"])
        if prev is not None and math.dist(p, prev) > 5.0:  # type: ignore[arg-type]
            s.position_jumps += 1
        prev = p
    mts = [float(r["MissionTime"]) for r in rows]  # type: ignore[arg-type]
    s.mission_time_resets = sum(1 for a, b in zip(mts, mts[1:], strict=False) if b < a - 0.5)
    for key, cls, seen in (
        ("MissionMode", MissionMode, s.mission_modes_seen),
        ("VehicleMode", VehicleMode, s.vehicle_modes_seen),
    ):
        last = None
        for r in rows:
            if key not in r:
                break
            e = _enum(cls, r[key])
            if e is None:
                s.invalid_enum_values += 1
                continue
            if e.name not in seen:
                seen.append(e.name)
            last = e.name
        if key == "MissionMode":
            s.last_mission_mode = last
        else:
            s.last_vehicle_mode = last
    sats = [int(r["GPSNumSats"]) for r in rows if "GPSNumSats" in r]  # type: ignore[call-overload]
    s.max_gnss_sats = max(sats) if sats else None
    s.gnss_position_changes = len({(r["GPSLat"], r["GPSLon"]) for r in rows}) > 1
    real_gnss = (s.max_gnss_sats or 0) >= 4 if sats else s.gnss_position_changes
    if s.max_altitude_m is not None and s.max_altitude_m > 3.0:
        s.inferred_kind = "flight" if real_gnss else "simulation"
    else:
        s.inferred_kind = "bench"
    return s


# ---------------------------------------------------------------- writing


@dataclass
class ConvertResult:
    source: Path
    name: str  # output stem
    status: str  # "converted", "unchanged", "skipped", "failed"
    message: str = ""
    layout: str | None = None
    exlog: Path | None = None
    meta: Path | None = None
    summary: Summary | None = None
    date: str | None = None
    date_note: str = ""
    sha16: str = ""
    rel_source: str = ""


def output_stem(path: Path) -> str:
    """`backup/missionData_0.txt` -> `backup_missionData_0`; other files keep their stem."""
    return f"{path.parent.name}_{path.stem}" if path.parent.name == "backup" else path.stem


def _time_base(sha16: str, last_local_s: float, mtime: float | None) -> tuple[int, str, str]:
    """(base ns, date, note). Packet time = base + LocalTime."""
    known = KNOWN_FILES.get(sha16)
    end_s, date, note = known if known else (None, None, "")
    if known is None and mtime is not None and mtime >= datetime(2000, 1, 1, tzinfo=UTC).timestamp():
        end_s, note = int(mtime), "file modification time (end of logging), not in the converter's table"
        date = datetime.fromtimestamp(end_s, UTC).strftime("%Y-%m-%d")
    if end_s is not None:
        return int(end_s * S - round(last_local_s * S)), date, note  # type: ignore[return-value]
    if date is None:
        date, note = "2025-01-01", "unknown: no clock and not in the converter's table (placeholder date)"
    noon = int(datetime.fromisoformat(date).replace(hour=12, tzinfo=UTC).timestamp()) * S
    return noon, date, note


def write_exlog(pf: ParsedFile, out: Path, base_ns: int, meta: dict[str, str], source_info: dict[str, str]) -> int:
    """Write the converted log through the Recorder to `out` (atomically). Returns the packet count."""
    bus = Bus()
    rows = pf.rows
    t_of = [base_ns + round(float(r["LocalTime"]) * S) for r in rows]  # type: ignore[arg-type]
    now = [t_of[0]]
    tmp = out.with_name(out.name + ".tmp")
    tmp.unlink(missing_ok=True)
    rec = Recorder(
        out.parent,
        REGISTRY,
        bus,
        meta,
        sources=[source_info],
        clock=lambda: now[0],
        opener=lambda _p: open(tmp, "xb"),  # noqa: SIM115
        flush_interval_ms=1000,
        fsync_interval_ms=60_000,
    )
    rec.start(meta.get("label"))
    bus.publish(
        Note(
            t=t_of[0],
            src="gs",
            text=f"legacy import of {source_info['device']} (layout {pf.layout.name}); conventions in the meta.toml",
        )
    )
    for r, t in zip(rows, t_of, strict=True):
        now[0] = t
        for p in row_packets(r, t, pf.layout):
            bus.publish(p)
    now[0] = t_of[-1]
    info = rec.stop()
    st = rec.status()
    if info is None or st.last_error or st.dropped:
        tmp.unlink(missing_ok=True)
        raise OSError(f"recorder failed: {st.last_error or f'{st.dropped} packets dropped'}")
    if out.exists() and filecmp.cmp(tmp, out, shallow=False):
        tmp.unlink()
    else:
        os.replace(tmp, out)
    return info.packets


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if not (isinstance(v, float) and v != v) else "nan"
    if v is None:
        return '""'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def meta_toml(res: ConvertResult, layout: Layout, rel_source: str, bad_rows: Sequence[str]) -> str:
    s = res.summary
    assert s is not None
    modes = ", ".join(s.mission_modes_seen) or "not logged"
    outcome = (
        f"auto (inferred {s.inferred_kind}): duration {s.duration_s:.1f} s, max altitude {s.max_altitude_m:.1f} m, "
        f"mission modes {modes}, last {s.last_mission_mode or 'not logged'}"
    )
    top = {
        "title": f"Legacy ExVectrController log {Path(rel_source).name}",
        "date": res.date,
        "date_note": res.date_note,
        "kind": s.inferred_kind,
        "kind_note": "inferred by the converter (altitude > 3 m and GNSS present -> flight, without GNSS -> "
        "simulation, else bench); confirm",
        "flight_id": "",
        "vehicle": "model Starship, old flight software (2025)",
        "firmware": "unknown (old ExVectrController handset logger, layout " + layout.name + ")",
        "ground_station": f"exvectr_gs {__version__} legacy importer {CONVERTER}",
        "link": "radio (rows logged on the handset from received telemetry)",
        "format": "exlog (converted from legacy csv)",
        "format_notes": f"Layout {layout.name}: {layout.desc} {CONVENTIONS}",
        "source": rel_source,
        "source_sha256_16": res.sha16,
        "files": [res.exlog.name if res.exlog else ""],
        "sensors": "",
        "conditions": "",
        "outcome": "",
        "notes": "Generated by tools/convert_legacy_log.py; edit outcome/notes here but note that re-running the "
        "converter rewrites this file. " + (f"Skipped rows: {'; '.join(bad_rows[:5])}" if bad_rows else ""),
    }
    lines = ["# Generated by the ground station's legacy importer (docs/legacy-logs.md). Re-running overwrites it."]
    lines += [f"{k} = {_toml(v)}" for k, v in top.items()]
    lines += ["", "[auto_summary]", f"text = {_toml(outcome)}"]
    lines += [f"{k} = {_toml(v)}" for k, v in s.as_dict().items() if v is not None]
    return "\n".join(lines) + "\n"


def _write_if_changed(path: Path, text: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def convert_file(path: Path, out_dir: Path, rel_source: str | None = None) -> ConvertResult:
    """Convert one CSV into `<out_dir>/<stem>.exlog` and `<stem>.meta.toml`. Never raises for bad input."""
    path = Path(path)
    stem = output_stem(path)
    rel = rel_source or path.name
    res = ConvertResult(source=path, name=stem, status="failed", rel_source=rel)
    try:
        data = path.read_bytes()
    except OSError as e:
        res.message = f"cannot read: {e}"
        return res
    res.sha16 = hashlib.sha256(data).hexdigest()[:16]
    try:
        pf = parse_csv(data.decode("utf-8", "replace"))
    except LegacyFormatError as e:
        res.message = str(e)
        return res
    res.layout = pf.layout.name
    res.summary = summarise(pf)
    last_local = float(pf.rows[-1]["LocalTime"])  # type: ignore[arg-type]
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    base, res.date, res.date_note = _time_base(res.sha16, last_local, mtime)
    out_dir.mkdir(parents=True, exist_ok=True)
    res.exlog = out_dir / f"{stem}.exlog"
    res.meta = out_dir / f"{stem}.meta.toml"
    meta = {
        "gs": __version__,
        "host": "legacy-import",
        "profile": "legacy-import",
        "label": f"legacy {stem}",
        "converter": CONVERTER,
        "legacy_file": rel,
        "legacy_layout": pf.layout.name,
        "legacy_sha256_16": res.sha16,
        "time_base": "LocalTime",
    }
    src_info = {"id": SRC, "kind": "legacy-import", "device": rel, "rc_role": "none", "layout": pf.layout.name}
    before = res.exlog.read_bytes() if res.exlog.exists() else None
    try:
        write_exlog(pf, res.exlog, base, meta, src_info)
    except OSError as e:
        res.message = str(e)
        return res
    meta_text = meta_toml(res, pf.layout, rel, pf.bad_rows)
    old_meta = res.meta.read_text(encoding="utf-8") if res.meta.exists() else None
    _write_if_changed(res.meta, meta_text)
    same = before is not None and before == res.exlog.read_bytes() and old_meta == meta_text
    res.status = "unchanged" if same else "converted"
    notes = []
    if pf.bad_rows:
        notes.append(f"{len(pf.bad_rows)} bad row(s) skipped")
    if pf.truncated_last_line:
        notes.append("truncated last line ignored")
    res.message = "; ".join(notes)
    return res


def convert_files(
    paths: Iterable[Path],
    out_dir: Path,
    rel_to: Path | None = None,
    progress: Callable[[ConvertResult], None] | None = None,
) -> list[ConvertResult]:
    """Convert many files. Byte-identical duplicates (same sha256) are converted once and reported as skipped."""
    results: list[ConvertResult] = []
    seen: dict[str, str] = {}
    for p in paths:
        p = Path(p)
        rel = str(p.relative_to(rel_to)) if rel_to is not None and p.is_relative_to(rel_to) else p.name
        try:
            sha = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        except OSError:
            sha = ""
        if sha and sha in seen:
            r = ConvertResult(
                p, output_stem(p), "skipped", f"byte-identical duplicate of {seen[sha]}", sha16=sha, rel_source=rel
            )
        else:
            r = convert_file(p, out_dir, rel)
            if sha and r.status in ("converted", "unchanged"):
                seen[sha] = rel
        results.append(r)
        if progress is not None:
            progress(r)
    return results


# ---------------------------------------------------------------- manifest

MANIFEST_BEGIN = "# BEGIN legacy-import (generated by the ground station's legacy importer; do not edit inside)"
MANIFEST_END = "# END legacy-import"


def update_manifest(manifest: Path, results: Sequence[ConvertResult], folder: str) -> None:
    """Replace the generated block in `manifest` (created if missing) with one entry per converted file."""
    entries = sorted((r for r in results if r.status in ("converted", "unchanged")), key=lambda r: (r.date, r.name))
    block = [MANIFEST_BEGIN]
    for r in entries:
        s = r.summary
        assert s is not None and r.exlog is not None and r.meta is not None
        outcome = (
            f"auto: {s.inferred_kind} (inferred), {s.duration_s:.0f} s, max altitude {s.max_altitude_m:.1f} m, "
            f"last mission mode {s.last_mission_mode or 'not logged'}"
        )
        block += [
            "[[entry]]",
            f"folder = {_toml(folder)}",
            f"file = {_toml(r.exlog.name)}",
            f"meta = {_toml(r.meta.name)}",
            f"source = {_toml(r.rel_source)}",
            f"date = {_toml(r.date)}",
            f"date_estimated = {_toml('estimated' in r.date_note or 'unknown' in r.date_note)}",
            f"kind = {_toml(s.inferred_kind)}",
            f"outcome = {_toml(outcome)}",
            "",
        ]
    if block[-1] == "":
        block.pop()
    block.append(MANIFEST_END)
    text = manifest.read_text(encoding="utf-8") if manifest.exists() else ""
    if MANIFEST_BEGIN in text and MANIFEST_END in text:
        head, rest = text.split(MANIFEST_BEGIN, 1)
        _, tail = rest.split(MANIFEST_END, 1)
        new = head + "\n".join(block) + tail
    else:
        new = (text.rstrip("\n") + "\n\n" if text.strip() else "") + "\n".join(block) + "\n"
    _write_if_changed(manifest, new)
