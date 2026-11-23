# Legacy telecommand uplink (RETIRED)

**Retired 2026-11-23 (branch `feature/gs-requests-all`).** The vehicle's telecommand port (0x7F) is deleted, so the ground station no longer has a telecommand controller, a `[telecommands]` config section (a config that still has one is refused with an explanation), the `telecommand.send` / `telecommand.list` API ops, the `exgs tc` command, the `telecommands` push or the 'Commands (temporary)' panel. Everything that used telecommands is a REQUEST now ([requests.md](requests.md)):

| Former telecommand | Request now | Where in the GUI |
|---|---|---|
| `CalibStart` (start 1, stop and fit 0, verify 2, discard -1), per subsystem | `CALIB_CONTROL` (14), `arg_i = subsystem * 10 + code` (code 1 start, 2 verify, 0 stop and fit, 9 discard; subsystem 0 gyro, 1 accel, 2 mag) | Requests panel, group Calibration |
| `CalibApply` | `CALIB_APPLY` (7), `arg_i` = subsystem | Requests panel, group Calibration |
| `Save` | `SAVE_CONFIG` (8) | Requests panel |
| `ZeroEstimators` | `ZERO_ESTIMATORS` (12) | Requests panel |
| `SetStopAltitude` | `SET_STOP_ALTITUDE` (13) | Vehicle panel |
| `SimWind*` (5 types) | `SIM_WIND_SPEED` 15, `SIM_WIND_DIRECTION` 16, `SIM_GUST_STRENGTH` 17, `SIM_GUST_PERIOD` 18, `SIM_GUST_SEED` 19 | Simulation testing page, Wind |
| `SimFlapStuck`, `SimFlapSlow`, `SimFaultClear` | `SIM_FLAP_STUCK` 20, `SIM_FLAP_SLOW` 21, `SIM_FAULT_CLEAR` 22 | Simulation testing page, Servo faults |
| (new) | `GET_HOME_POSITION` (23): the vehicle acks DONE, then sends the plain `HOME_POSITION` packet | Requests panel |
| `Arm`, `SimulationMode`, `MissionBegin`, `MissionSelect`, `MissionAbort`, `SystemReset` | none: the command state `CMD_STATE` (virtual radio switches) | [radio.md](radio.md) |

What stays in the code, only so that old recordings decode: the `Telecommand` packet type and the `TelecommandType` enum (`packets/defs/link.py`) and the `telecommand_sent` event kind. The wire profile never encodes a `Telecommand`, no source sends one.
