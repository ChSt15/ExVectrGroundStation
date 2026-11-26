"""Legacy ExVectrController CSV import: layouts, conversions (hand-computed values), time base, outputs."""

import math
import tomllib
from pathlib import Path

import pytest

from exvectr_gs.packets.defs.vehicle import MissionMode, VehicleMode
from exvectr_gs.recording import LogReader
from exvectr_gs.recording.legacy import (
    LegacyFormatError,
    convert_file,
    convert_files,
    detect_layout,
    parse_csv,
    row_packets,
    update_manifest,
    words_to_double,
)

DATA = Path(__file__).resolve().parents[1] / "data" / "legacy"
S = 1_000_000_000


def packets_of(name: str, row: int = 0):
    pf = parse_csv((DATA / name).read_text())
    return pf, {p.TYPE: p for p in row_packets(pf.rows[row], 123, pf.layout)}


@pytest.mark.parametrize(
    "name,layout",
    [
        ("hdop22.txt", "hdop-22"),
        ("backup/pacc22.txt", "pacc-22"),
        ("tvc28.txt", "tvc-28"),
        ("tvc31.txt", "tvc-31"),
        ("tvcground33.txt", "tvc-ground-33"),
    ],
)
def test_layout_detection(name, layout):
    pf = parse_csv((DATA / name).read_text())
    assert pf.layout.name == layout


def test_unknown_header_and_empty():
    with pytest.raises(LegacyFormatError, match="unknown header"):
        detect_layout(["foo", "bar"])
    with pytest.raises(LegacyFormatError, match="empty"):
        parse_csv("")
    with pytest.raises(LegacyFormatError, match="no valid data rows"):
        parse_csv((DATA / "tvc31.txt").read_text().split("\n")[0] + "\n1,2,3\n")


def test_words_to_double_hand_computed():
    # -1.25 = sign 1, exponent 0x3FF, mantissa .25 -> 0xBFF40000_00000000; printed as lo=0, hi=-1074528256
    assert words_to_double(0, -1074528256) == -1.25
    # first row of missionData_FirstFull: float 0.051 promoted to double = 0x3FAA1CAC_00000000
    assert words_to_double(0, 1068113068) == 0.051


def test_hdop22_tail_is_sats_and_modes():
    pf, p = packets_of("hdop22.txt")
    assert pf.rows[0]["Tail"] == [13, 4, 1]
    assert p["Gps"].num_sats == 13 and p["Gps"].vel is None and p["Gps"].pos_acc is None
    assert p["VehicleStatus"].mode is VehicleMode.Running
    assert p["VehicleStatus"].mission_mode is MissionMode.Initialisation
    assert p["MissionState"].t_mission == -5 * S
    # second row: VehicleMode 5 = Failure; MissionMode 9 is outside the enum -> None, kept in LegacyRow
    _, p = packets_of("hdop22.txt", 1)
    assert p["VehicleState"].mode is VehicleMode.Failure and p["MissionState"].mission_mode is None
    assert p["LegacyRow"].MissionMode == 9
    # frames are copied unchanged: NWU position, [w,x,y,z] body -> world (90 deg about +Y here)
    assert p["Position"].pos == [1.5, -2.25, 3.0] and p["Position"].vel == [0.1, -0.2, 0.3]
    assert p["Attitude"].q == [0.7071, 0.0, 0.7071, 0.0] and p["Attitude"].omega is None
    assert p["MissionState"].sp_pos == [0.0, 0.0, 1.0] and p["MissionState"].sp_vel is None


def test_pacc22_reconstructs_gps_vel_x_and_drops_garbled_columns():
    pf, p = packets_of("backup/pacc22.txt")
    gps = p["Gps"]
    assert gps.vel[0] == 0.051 and math.isnan(gps.vel[1]) and math.isnan(gps.vel[2])
    assert gps.num_sats is None and gps.pos_acc is None and gps.vel_acc is None
    assert gps.lat == 0.8732329 and gps.lon == 0.1166708 and gps.alt == 399.83  # radians, metres: unchanged
    assert "VehicleStatus" not in p and "VehicleState" not in p and "Actuators" not in p
    assert p["MissionState"].mission_mode is None
    row = p["LegacyRow"]
    assert row.Tail == [0, 1068113068, -2147483648] and row.GPSVelX == 0.051 and row.VehicleMode is None
    # second row: all-zero quaternion -> q None; lat/lon/alt all zero -> no fix
    _, p = packets_of("backup/pacc22.txt", 1)
    assert p["Attitude"].q is None
    assert p["Gps"].lat is None and p["Gps"].lon is None and p["Gps"].alt is None and p["Gps"].vel[0] == -1.25


def test_tvc28_single_thrust():
    _, p = packets_of("tvc28.txt", 1)
    assert p["Actuators"].thrust == 10.1 and p["Actuators"].roll_torque is None
    assert p["VehicleState"].tvc_thrust is None
    assert p["Gps"].pos_acc == 0.91 and p["Gps"].vel_acc == 0.25 and p["Gps"].vel == [0.03, 0.09, 0.02]
    assert p["MissionState"].mission_mode is MissionMode.Running and p["MissionState"].t_mission == 2_400_000_000


def test_tvc31_vector_thrust_and_bad_row():
    pf, p = packets_of("tvc31.txt", 1)
    assert pf.bad_rows and "line 4" in pf.bad_rows[0]
    assert len(pf.rows) == 2
    assert p["VehicleState"].tvc_thrust == [3.0, 0.0, 4.0] and p["VehicleState"].tvc_torque == 0.01
    assert p["Actuators"].thrust == 5.0  # |(3, 0, 4)|
    assert p["Actuators"].roll_torque == 0.01 and p["Position"].ground_distance is None


def test_tvc_ground33():
    _, p = packets_of("tvcground33.txt", 1)
    vs = p["VehicleState"]
    assert vs.ground_dist == 1.25 and vs.ground_dist_cov == 0.5 and vs.tvc_thrust == [0.1, -0.2, 7.5]
    assert p["Position"].ground_distance == 1.25
    assert p["MissionState"].t_mission == 38 * S and p["MissionState"].sp_pos == [0.0, 0.0, 120.0]
    assert p["Attitude"].q == [0.5, 0.5, 0.5, 0.5]
    assert p["Gps"].num_sats == 0 and p["Gps"].pos_acc == -0.0 and p["Gps"].vel_acc == 10.0


def test_summary_values():
    pf = parse_csv((DATA / "tvcground33.txt").read_text())
    from exvectr_gs.recording.legacy import summarise

    s = summarise(pf)
    assert s.rows == 3 and abs(s.duration_s - 0.2) < 1e-9
    assert s.max_altitude_m == 120.5 and s.mission_time_resets == 1 and s.position_jumps == 2
    assert s.mission_modes_seen == ["Startup", "Running", "Finished"] and s.last_mission_mode == "Finished"
    assert s.inferred_kind == "simulation"  # > 3 m without GNSS satellites


def test_convert_file_outputs_and_idempotent(tmp_path):
    r = convert_file(DATA / "tvcground33.txt", tmp_path)
    assert r.status == "converted" and r.exlog.name == "tvcground33.exlog"
    reader = LogReader(r.exlog)
    assert reader.header()["profile"] == "legacy-import" and reader.header()["legacy_layout"] == "tvc-ground-33"
    assert reader.sources()[0]["kind"] == "legacy-import"
    pkts = [p for _, p in reader.iter_packets()]
    pos = [p for p in pkts if p.TYPE == "Position"]
    assert len(pos) == 3 and pos[1].t - pos[0].t == 100_000_000  # LocalTime 21.21 -> 21.31
    assert not reader.bad_lines
    meta = tomllib.loads(r.meta.read_text())
    assert meta["format"] == "exlog (converted from legacy csv)" and meta["auto_summary"]["max_altitude_m"] == 120.5
    assert "NWU" in meta["format_notes"] and "modification time" in meta["date_note"]  # not in the table: mtime
    first = r.exlog.read_bytes()
    r2 = convert_file(DATA / "tvcground33.txt", tmp_path)
    assert r2.status == "unchanged" and r2.exlog.read_bytes() == first


def test_time_base_uses_table_mtime(tmp_path):
    from exvectr_gs.recording.legacy import _time_base

    base, date, note = _time_base("a7ce4ca75c09e176", 481.0, None)  # missionData_flight6
    assert date == "2025-06-25" and base + 481 * S == 1750862912 * S and "mtime" in note
    base, date, note = _time_base("cdfd41d600d7cdc6", 300.0, 315525950.0)  # firsttrue: no clock
    assert date == "2025-04-28" and "estimated" in note and base == 1745841600 * S


def test_convert_files_reports_failures_and_duplicates(tmp_path):
    dup = tmp_path / "in" / "backup"
    dup.mkdir(parents=True)
    (dup / "tvc28.txt").write_bytes((DATA / "tvc28.txt").read_bytes())
    names = ["tvc28.txt", "empty.txt", "unknown_header.txt", "hdop22.txt"]
    files = [DATA / names[0], dup / "tvc28.txt"] + [DATA / n for n in names[1:]]
    res = convert_files(files, tmp_path / "out", rel_to=DATA)
    st = [(r.name, r.status) for r in res]
    assert st == [
        ("tvc28", "converted"),
        ("backup_tvc28", "skipped"),
        ("empty", "failed"),
        ("unknown_header", "failed"),
        ("hdop22", "converted"),
    ]
    manifest = tmp_path / "manifest.toml"
    manifest.write_text('# Index\n[[entry]]\nfolder = "x"\n')
    update_manifest(manifest, res, "legacy-converted")
    update_manifest(manifest, res, "legacy-converted")  # idempotent: one block
    text = manifest.read_text()
    assert text.count("# BEGIN legacy-import") == 1 and text.startswith("# Index")
    m = tomllib.loads(text)
    assert [e.get("file") for e in m["entry"]] == [None, "hdop22.exlog", "tvc28.exlog"]
