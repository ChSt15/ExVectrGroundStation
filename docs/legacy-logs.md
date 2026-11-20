# Legacy ExVectrController logs: import to `.exlog`

The old (2025) system logged each mission on the **handset** (ExVectrController, ESP32 with SD card) as a CSV file `missionData_N.txt`, one row every 100 ms with the latest telemetry it had received. Backups of these files and of the old sources are in the workspace data folder, `data/legacy/ExVectrController/` (its `README.md`). The importer converts them to `.exlog` so old flights can be replayed and plotted in the ground station and used for the simulation work.

Code: `exvectr_gs.recording.legacy` (stdlib only), packet type `LegacyRow` in `packets/defs/legacy.py`, CLI `exgs log import-legacy`, batch script `tools/convert_legacy_log.py`. Tests: `tests/unit/test_legacy_import.py` (synthetic CSV per layout in `tests/data/legacy/`), `tests/integration/test_legacy_import.py` (CLI, `exgs log validate/stats`, replay through the core, physical checks of a real file).

## Usage

```
exgs log import-legacy FILE... [--out DIR] [--manifest PATH] [--folder NAME] [--relative-to DIR] [--strict]
python tools/convert_legacy_log.py [FILE...] [--data DIR] [--out DIR] [--manifest PATH | --no-manifest]
```

- Each `X.txt` gives `DIR/X.exlog` and `DIR/X.meta.toml` (a file in a folder named `backup` gives `backup_X`). Byte-identical duplicates are converted once and reported as skipped. Empty or corrupt files are reported (`failed`) and the others are still converted; the exit code is 1 only if every file failed, or with `--strict` if any failed. A row with the wrong number of values or a value that is not a number is skipped and counted (`bad_rows`).
- Re-running gives the same files; unchanged outputs are not rewritten. Output is written to a temporary file and renamed.
- `--manifest` rewrites the block between `# BEGIN legacy-import` and `# END legacy-import` in that `manifest.toml` (one `[[entry]]` per converted file, sorted by date) and leaves the rest of the file alone.
- `tools/convert_legacy_log.py` without arguments converts every `*.txt` in `<data>/legacy/ExVectrController/tools/data/` and `backup/` to `<data>/legacy-converted/` and updates `<data>/manifest.toml`. `<data>` is the workspace `data/` folder next to this repository.

## What a converted file contains

- Header: `#meta ... host=legacy-import profile=legacy-import converter=legacy-import-1 legacy_file=<path> legacy_layout=<layout> legacy_sha256_16=<hash> time_base=LocalTime` and `#source id=legacy kind=legacy-import device=<path> rc_role=none layout=<layout>`. It is written by the normal `Recorder`, so it has the `GsEvent rec_start` / `rec_stop` lines, `#type` lines and `#end`, plus one `Note` at the start.
- Per CSV row, all at the row's time, source `legacy`: `Position`, `Attitude`, `MissionState`, `VehicleState` (if it has a value), `VehicleStatus` (if a mode is logged), `Gps`, `Actuators` (if TVC or ground distance columns exist) and `LegacyRow` (every column as logged, with the old names).
- `X.meta.toml` follows `data/meta.template.toml`. `format_notes` holds the layout and all conventions below, `[auto_summary]` the numbers computed from the data (rows, duration, max altitude, max GNSS altitude gain, max speed, mission and vehicle modes seen and last, GNSS satellites, mission-time restarts, time going backwards, longest gap, NaN count, position jumps over 5 m between rows, invalid enum values) and `kind` an inferred kind: `flight` (max altitude over 3 m and real GNSS: at least 4 satellites, or lat/lon changing where satellites are not logged), `simulation` (over 3 m without GNSS) or `bench`. The owner should confirm `kind` and fill in `outcome`; re-running the converter rewrites the file.

## Column layouts

Detected from the header row (exact match). The base columns are always the first 19: `Index, LocalTime, MissionTime, SetPositionX/Y/Z, PositionX/Y/Z, VelocityX/Y/Z, AttitudeQ0..Q3, GPSLat, GPSLon, GPSAlt`.

| Layout | Header | Values per row | Files (2026-11-20 backup) |
|---|---|---|---|
| `hdop-22` | 27 names, `GPSHDOP, GPSVDOP` | 22: base, then `GPSNumSats, VehicleMode, MissionMode` (GPS velocity, HDOP, VDOP were never printed) | `logdata.txt`, `backup/missionData_firsttrue.txt` |
| `pacc-22` | 27 names, `GPSPACC, GPSVACC` | 22: base, then three garbled integers (below) | `missionData_FirstFull.txt` (and its duplicate in `backup/`), `backup/missionData_0, 6, 10, 11, 12, 22, 26, 29` |
| `tvc-28` | `... MissionMode, TVCThrust` | 28, as named | `backup/missionData_flight4.txt` |
| `tvc-31` | `... MissionMode, TVCThrustX/Y/Z, TVCTorque` | 31, as named | `backup/missionData_flight5.txt`, `missionData_flight6.txt` |
| `tvc-ground-33` | `... TVCTorque, GroundDistance, GroundDistanceCov` | 33, as named (the logger in `src-reference/data_logger.hpp`) | `missionData_5, 13, 14, 15, 16, 17, 19` |

`missionData_18.txt` is empty (0 bytes) and is reported as failed.

**`pacc-22`, the garbled tail.** The logger's header and argument list had been extended (GPS velocity, accuracies, satellites, modes) but its format string still had the old 22 conversions ending in three `%d`. On the ESP32 the `%d`s read the 32-bit words of the next variadic arguments, which are doubles (promoted floats): low word of `GPSVelX`, high word of `GPSVelX`, low word of `GPSVelY`. Evidence: in `missionData_FirstFull` the first and third integers only take the values `k * 2^29` (the low word of a promoted float has only its top 3 bits set), and the rebuilt `GPSVelX` is plausible (-1.4 to 2.2 m/s, correlation 0.61 with `VelocityX`, first row exactly 0.051). In the files with three zeros the GNSS velocity was exactly 0. So the importer rebuilds `GPSVelX` (`Gps.vel = [GPSVelX, NaN, NaN]`); `GPSVelY/Z`, the accuracies, satellites and both modes are lost. The three integers stay in `LegacyRow.Tail`.

## Conventions and conversions

The old firmware already used the conventions of today's system for everything in these logs, so the import copies values and only re-labels them. The evidence comes from the old sources (`src-reference/`, `tools/log_analysis.py`, the old `ExVectrPackets/packet_vehicle.hpp`) and was checked against the data (numbers below from the real files).

| Quantity | Old column | Converted to | Conversion | Confidence |
|---|---|---|---|---|
| Packet time | `LocalTime` (s, **handset** uptime, `Core::NOWSeconds()` on the logger) | packet `t` (ns since epoch) | `base + LocalTime * 1e9` (base below) | certain (relative), wall-clock approximate |
| Mission time | `MissionTime` (s, vehicle `missionTime / SECONDS`) | `MissionState.t_mission` (ns) | `* 1e9` | certain. The old firmware restarts it at some phase changes (counted in `mission_time_resets`). |
| Position, velocity | `PositionX/Y/Z`, `VelocityX/Y/Z` (m, m/s) | `Position.pos`, `.vel` (NWU) | none | high |
| Position setpoint | `SetPositionX/Y/Z` (= `positionSetpoint[3..5]`) | `MissionState.sp_pos` | none | high |
| Attitude | `AttitudeQ0..Q3` | `Attitude.q` `[w,x,y,z]`, body -> world | none (norm < 0.5 -> None) | high |
| GNSS lat/lon | `GPSLat`, `GPSLon` | `Gps.lat`, `.lon` (rad) | none: the values are radians | high |
| GNSS altitude | `GPSAlt` (m) | `Gps.alt` | none | high |
| GNSS velocity | `GPSVelX/Y/Z` (m/s) | `Gps.vel` | none: already in the NWU frame | medium-high |
| GNSS accuracy | `GPSPACC` (m), `GPSVACC` | `Gps.pos_acc` (m), `Gps.vel_acc` (m/s) | none. `GPSVACC` is the **velocity** accuracy (`velocityAccuracy` in the logger), not vertical. | high (from the source) |
| Satellites | `GPSNumSats` | `Gps.num_sats` | none | high |
| Modes | `VehicleMode`, `MissionMode` | `VehicleState.mode`, `VehicleStatus.mode` / `.mission_mode`, `MissionState.mission_mode` | same enum numbering as the old `telemetry.hpp` and today's GS enums; out of range -> None | high |
| TVC thrust | `TVCThrustX/Y/Z` (N, body frame) | `VehicleState.tvc_thrust`; `Actuators.thrust` = magnitude (derived) | none | high (unit, from the source); body frame medium-high (Z carries the thrust) |
| TVC thrust, one value | `TVCThrust` (layout `tvc-28`) | `Actuators.thrust` | none | medium (assumed magnitude) |
| TVC torque | `TVCTorque` (N*m) | `VehicleState.tvc_torque`, `Actuators.roll_torque` | none | medium (assumed roll torque about body Z, the only torque in the old struct) |
| Ground distance | `GroundDistance` (m), `GroundDistanceCov` (m^2) | `VehicleState.ground_dist`, `.ground_dist_cov`, `Actuators.ground_distance` | none | high |

Not in the logs, left None: `Attitude.omega`, `MissionState.sp_vel` and `mission_phase`, sensor and failure states, `armed`, `VehicleStatus.simulation` and `vehicle_time` (the logged clock is the handset's, not the vehicle's). Lat, lon and alt all exactly 0 means no fix: `Gps.lat/lon/alt` None.

**Time base.** `base` puts the last row at the file's SD card modification time (the handset clock when the logger closed the file), recorded in the converter's table `KNOWN_FILES` by file hash because git does not keep modification times. That time was read on a CET machine and the timezone of the handset clock is unverified, so wall-clock times can be off by about 2 h; relative times are exact. Files whose SD time is the FAT zero date (the handset had no clock; there the SD time equals the FAT epoch plus `LocalTime`, which confirms that `LocalTime` is the logger's uptime) get 12:00 UTC of an estimated date: by logger version they lie between `logdata.txt` (2025-04-28) and `missionData_flight4` (2025-06-19), and the meta says `estimated`. A file not in the table uses its modification time if plausible, else the placeholder 2025-01-01.

**Rows are snapshots.** A row holds the latest received telemetry of each kind, so values repeat between radio updates and jump when a new packet arrives (most `position_jumps` in the summaries are this: the position stays for several rows during a fast descent and then moves by 5 to 10 m).

### Evidence

- **Position frame NWU, GNSS in radians.** North and East displacement computed from `GPSLat/GPSLon` taken as radians (R = 6371 km) against the estimator position, over the rows where the GNSS fix changed: `missionData_flight6` corr(X, North) 0.997, slope 0.98; corr(Y, East) -0.998, slope -1.02. `backup/missionData_flight5` 0.998 / 1.02 and -0.999 / -1.01. `missionData_FirstFull` 0.986 / 0.91 and -0.998 / -1.05. So X = North, Y = West, and lat/lon are radians (as degrees the slope would be 57 times smaller). The old header comment "degrees multiplied by 1e7" in `PacketGPS` is wrong for what was stored: the sensor passed radians. Positions 0.8711..0.8732 rad, 0.1166..0.1169 rad (49.9..50.0 N, 6.7 E) for the real flights.
- **Z up.** `PositionZ` follows the GNSS altitude: corr 0.9995 to 0.9997, slope 0.93 to 1.01 in flight5, flight6, FirstFull; ascents go to positive Z (flight4/5 to 120 m with GNSS altitude +123/+128 m).
- **GNSS velocity frame.** `GPSVelX/Y/Z` against `VelocityX/Y/Z` in `missionData_flight6`: corr 0.86, 0.91, 0.96 (same sign on all axes, so the old GNSS driver had already rotated it into NWU). In flight5 X is weak (0.14; little X motion, stale GNSS) but Y 0.58 and Z 0.93 have the same sign.
- **Quaternion order [w,x,y,z].** On the pad the logged quaternions are close to `[1,0,0,0]` with `AttitudeQ0` first (for example 0.9973, 0.0317, 0.0223, -0.0617 in flight6: about 5 degrees of tilt). Read as `[x,y,z,w]` the vehicle would be upside down. `tools/log_analysis.py` also reads them as `[w,x,y,z]` (`R.from_quat([q1, q2, q3, q0])`).
- **Rotation direction body -> world.** During powered ascent (VelocityZ > 0.5 m/s, Z > 3 m) the horizontal acceleration (from the velocity) should follow the horizontal part of the nose axis. With the nose axis `R(q) e_z` (q body -> world) the correlations (x, y) are flight5 0.88, 0.90; flight6 0.81, 0.83; flight4 0.62, 0.50; the simulation `missionData_16` 0.96, 0.93. With the conjugate (world -> body) they are negative (flight5 -0.87, -0.90). The bellyflop agrees with today's convention: flight5 at 231.5 s, q = [0.446, -0.491, -0.525, -0.534] puts body +X at world (-0.12, 0.04, 0.99), i.e. up, belly (-X) down.
- **Old `ExVectrMath` quaternion product.** Before ExVectrMath `b221829` (2026-01-29) `a * b` computed `b ⊗ a` (`flight-software/docs/libs/ExVectrMath.md`). The 2025 firmware used the old library, but the checks above are on the logged numbers, so whatever the code did internally, the logged q behaves as body -> world.
- **Units.** The old packets are fixed point with the scales in `packet_vehicle.hpp` and `telemetry.hpp` (position /1000 m, velocity /100 m/s, TVC /50 N); the handset logged the unpacked SI values, so no unit conversion is needed. Rounding of the CSV: 0.01 m, 0.0001 for q, 1e-10 rad for lat/lon (the wire had 1e-7 rad, about 0.6 m).

### Owner to confirm

- `kind` of each file (inferred) and `outcome`; whether the `pacc-22` backup files (constant GNSS position, 80 to 100 m climbs) are simulations as inferred.
- `TVCTorque` as roll torque, `TVCThrust` (layout `tvc-28`) as a thrust magnitude.
- The dates estimated for the files without a clock, and the handset clock's timezone.
- `backup/missionData_firsttrue`: `PositionZ` goes to 17.7 m while the GNSS altitude changes by only 7.7 m (corr 0.22): the old estimator probably diverged; the data are converted as logged.
