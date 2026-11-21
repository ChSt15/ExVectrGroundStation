# TEMPORARY legacy telecommand uplink

> **TEMPORARY. TODO: delete this whole path when the flight software's T9b lands** (it replaces the legacy telecommand by `RC_STATE` mapping and `REQUEST`s): the package `src/exvectr_gs/telecommand/`, `[telecommands]` in the configs, the API ops `telecommand.*`, `src/exvectr_gs/cli/cmd_tc.py` (`exgs tc`), the GUI panels `telecommands` and `simtests` (`web/app/telecommand/`, `web/app/panels/telecommands.js`, `web/app/panels/simtests.js`), the packet `Telecommand` in `packets/defs/link.py`, `_encode_telecommand` in the wire profile, `answer_telecommands` in the fake source, the telecommand parts of the pty fake vehicle, the `telecommand.*` tests and this file.

Spec: user decision 2026-11-20 (explicit approval), flight-software `docs/project/hardware-and-drivers.md` section 10, `src/comms/telemetry_mapping.*` (`LegacyTelecommandPacket`), `src/telecommand.hpp`. Code: `src/exvectr_gs/telecommand/controller.py` (`TelecommandController`), config `core/config.py` (`[telecommands]`), API ops in `api/server.py`, CLI `cli/cmd_tc.py`, GUI `web/app/telecommand/`.

**Safety-relevant.** This is a **third uplink**, besides RC state ([radio.md](radio.md)) and the whitelisted REQUESTs ([requests.md](requests.md)). It exists because the vehicle (until T9b) can only be armed, put into simulation, reset or sent on a mission by this legacy command. The ground station makes no decision for the vehicle: it does not interpret a command, it checks no vehicle state, and it never shows an assumed result.

## The frame

The vehicle accepts the old 8-byte `Telecommand` on ExVectrNetwork **port 0x7F** from **node 0**, addressed to node 1. It is not in the ExVectrPackets schema, so the encoder is a plain 8-byte pack inside the wire profile (`wire/exvectrnet_1/profile.py`, `_encode_telecommand`), the only place that knows the layout.

| Byte | Field | Notes |
|---|---|---|
| 0 | `type` | `TelecommandType`: 0 CalibStart, 1 CalibApply, 2 Load (not handled by the vehicle), 3 Save, 4 SystemReset, 5 Arm, 6 SimulationMode, 7 MissionBegin, 8 MissionSelect, 9 MissionAbort, 10 ZeroEstimators |
| 1 | `subsystem` | `Subsystem`: 0 Gyro, 1 Accel, 2 Magneto, 3 Baro, 4 GNSS, 5 AttFilter, 6 PosFilter |
| 2-3 | `paramInt` | int16, little-endian |
| 4-7 | `paramFloat` | float32, little-endian; 0 for every command except `ZeroEstimators` (duration in s) |

Network frame: CTRL `0x48` (DATA, QoS EVENT, not latest), TTL 3, DST 1, SRC 0, PORT 0x7F, SEQ (+1 per command), then the 8 bytes. A test checks these bytes against an independent decoder (`tools/fake_vehicle_net.py`). Vehicle semantics (magic values, preconditions) are the flight software's; the ground station knows only the whitelist below.

## Rules (enforced in `TelecommandController.send`)

- **Whitelist only** (`[telecommands]`). A client names an entry; it cannot name a type or send a raw frame. The only values a client may choose are the int16 parameter of an entry that has a range (`param_min`/`param_max`, today `MissionBegin`) and the float parameter of an entry that has a float range (`param_float_min`/`param_float_max`, today `ZeroEstimators`), each only within its range (NaN and infinity are refused).
- **One frame per confirmation. No retry, no repeat, no ACK.** The vehicle de-duplicates nothing, so a repeat would run the command twice. `SimulationMode` **toggles** on the vehicle; a second frame would switch back. There is no timer, no queue and no retry loop in the controller. A frame that no source accepts is refused with `no_route` and **nothing is queued** (a reconnecting source sends nothing).
- **Result only from what the vehicle reports.** There is no acknowledgement packet. The GUI shows `sent <time> ago once via <source>` and nothing else about the outcome; the effect is read in the mission status bar from `VehicleStatus` (`simulation`, `armed`, `mode`, ...). The simulation state is never assumed: only `VehicleStatus.simulation` as reported.
- **Control hosts only** (`server.control_hosts`, the same as RC control), checked in the API (`control_denied`) and again in the controller from the peer address.
- **Deliberate confirmation:** GUI hold of 1 s on the button (progress bar; a click, short hold, blur or leaving the button sends nothing); CLI `exgs tc send NAME --confirm NAME` (the name repeated, no default); API `telecommand.send` needs `confirm` equal to the entry name (`confirm_required` otherwise).
- **Rate limit:** at most one new telecommand per 500 ms per client host (`rate_limited`, keyed by peer address; a refused send does not count). **Documented exception (owner approval 2026-11-21):** a command whose entry has a non-empty `rate_group` (only the five simulation wind entries, `rate_group = "simwind"`) may follow another command of the same group from the same client after 100 ms (`GROUP_MIN_INTERVAL_S`, the vehicle's tick); the first of a group, and every other command, still needs 500 ms after the client's previous send.
- **Route:** the first connected source in preference order that reaches the vehicle: the vehicle USB first, else the Router link (over the air). Order: `[telecommands] source_preference`, default vehicle USB, Router link, fake. `rc_role` is irrelevant. Never to a source that is not connected, never in replay (the replay view has no controller and the GUI panel is hidden there).
- **Never automatic**, nothing persisted. Each send is recorded as a `GsEvent` of kind `telecommand_sent` (and the packet as `Telecommand` in the log, info only).

## Config

```toml
[telecommands]
source_preference = ["usb", "link", "fake"]

[[telecommands.command]]
name = "Arm"               # whitelist key (the confirm string), no spaces
type = "Arm"               # TelecommandType name
label = "Arm"
group = "Vehicle"
param_int = 0xA5           # int16: fixed, or the default when a range is given
# subsystem = 2            # Subsystem number, default 0
# param_min = -3600        # with param_max: a client may choose the parameter within the range
# param_max = 3600
# param_float = 10.0       # float32 of the frame (ZeroEstimators: duration in s); fixed, or the default with a range
# param_float_min = 1.0    # with param_float_max: a client may choose the float within the range
# param_float_max = 60.0
# param_float_name = "duration_s"
# confirm = "hold"         # the only level
# note = "shown next to the button"
```

Validation (`ConfigError`, the core does not start): unknown `type`, `param_float*` not finite numbers, `param_float_min` without `param_float_max`, a float default outside its range, duplicate or empty `name`, parameter outside int16, `param_min` without `param_max`, default outside the range, subsystem not 0..6, `confirm` not `hold`, unknown keys and unknown source ids. No `[telecommands]` section means an empty whitelist: nothing can be sent.

Shipped in `config/gs.example.toml` and `config/gs.pocket.toml` (the values are the vehicle's, from the flight docs section 10):

| Name | type, subsystem, param | Vehicle behaviour (flight software, not interpreted here) |
|---|---|---|
| `Arm` | Arm, 0, `0xA5` | arms; any other value disarms |
| `Disarm` | Arm, 0, `0` | disarms |
| `SystemReset` | SystemReset, 0, `0xC5` | resets the system state only (failures, mode, mission); **no longer zeroes the estimators** (flight software change 2026-11-20, use `ZeroEstimators`). Label in the GUI: "Reset system state (does NOT zero estimators)" |
| `ZeroEstimators` | ZeroEstimators, 0, `0x2E`, float `duration_s` default 10, range 1..60 | zeroes the estimators for the duration (0 = the vehicle's default 10 s; the vehicle clamps to 1..60 s) and reports it in `VEHICLE_STATUS.flags` bit 7 `ZEROING`. The vehicle also zeroes by itself for 10 s after boot. One frame per confirmation, no retry, no ACK |
| `SimulationMode` | SimulationMode, 0, `0xA9` | **toggles** simulation mode |
| `MissionBegin` | MissionBegin, 0, default -20, range -3600..3600 | `paramInt` = initial mission time in s (-18 or less is accepted in real mode); needs mode Ready; arming is not required |
| `MissionAbort` | MissionAbort | in a running mission: return to home; otherwise actuators off, mission reset |
| `Calib{Gyro,Accel,Mag}Start` | CalibStart, 0/1/2, 1 | starts the calibration session (needs disarmed, on the ground, not simulation) |
| `Calib*Stop` | CalibStart, 0/1/2, 0 | stop and fit |
| `Calib*Verify` | CalibStart, 0/1/2, 2 | verify the active calibration, no candidate |
| `Calib*Discard` | CalibStart, 0/1/2, -1 | discard a candidate or end a collection |
| `Calib*Apply` | CalibApply, 0/1/2 | the vehicle applies only a PASS or WARN candidate, RAM only until saved; invalidates the estimator zero |
| `CalibSave` | Save, 0 | writes the whole EEPROM image (any subsystem saves all), read back and compared |

The 16 calibration entries (user approval 2026-11-21, widening the whitelist for exactly these) follow the flight software's [calibration.md](../../flight-software/docs/project/calibration.md) (`feature/calibration-service`); the vehicle interprets them and logs progress as `LOG_TEXT` (Console panel). The GUI groups them as Calibration in the Telecommands panel with the same 1 s hold, one frame, no retry, control hosts only and never in replay rules. The ground station gates nothing on vehicle state. The `CALIBRATION` packet carries no session state; the raw sensor panel shows the per-sensor source bits (EEPROM, RAM only, ...). The older `CalibMag*` and `MagSave` names are replaced by these.

`MissionBegin` default is now -20 s: while calibration is required the vehicle refuses a `paramInt` above -18 (simulation mode is exempt). The fixed 15 s countdown comes with T9b and is not implemented here.

`Load` and `MissionSelect` are not whitelisted (not handled or no-op). A range of -3600..3600 s for `MissionBegin` is the ground station's own bound (the vehicle takes any int16); change it in the config.

## API and CLI

`telecommand.send {name, confirm, param?, param_float?}` replies with the record (`n`, `name`, `type`, `subsystem`, `param_int`, `param_float`, `client`, `source`, `t`): there is deliberately **no state or result field**. `telecommand.list` replies `{commands: [{name, type, label, group, subsystem, param_int, param_min, param_max, confirm, note, route}]}` (`route` is the id of the source a send would use now, display only). The core pushes `{"op": "telecommands", recent, route}` after hello and after every send; the 2 Hz `status` push carries `telecommand_route`. Error codes: `control_denied`, `not_whitelisted`, `confirm_required`, `rate_limited`, `no_route`, `invalid_value` ([api.md](api.md)).

```bash
exgs tc list
exgs tc send SimulationMode --confirm SimulationMode      # ONE frame; prints "no ACK exists"
exgs tc send MissionBegin --confirm MissionBegin --param -30
exgs tc send ZeroEstimators --confirm ZeroEstimators [--param-float 10]   # seconds, 1..60, default 10
```

`send` prints that the frame was handed to a source once and that no ACK exists; check `exgs status --json` or the status bar for what the vehicle reports. Exit codes: 0 sent, 1 any error, 3 control denied.

## GUI: panel "Commands (temporary)"

Panel `telecommands` (`views = ["live"]`, shipped collapsed, next to the Requests panel). A warning bar states that it is temporary and that there is no ACK and no retry. Each button needs a 1 s hold; `MissionBegin` has a number field (the parameter, validated against its range; the button is disabled outside it). Per row: `#n sent <age> ago once via <source>, param p; no ACK exists`. The panel also shows `vehicle reports: SIMULATION` or `REAL SENSORS` from `VehicleStatus.simulation` (never an assumed state; "no VehicleStatus received" without data). Disabled in the replay view, without a connection, on a view-only host and when no source reaches the vehicle. Logic: `web/app/telecommand/telecommand_logic.js` (`web/test/telecommand_logic.test.js`); end to end: `tests/integration/test_ops_gui.py`.

## Test doubles (no vehicle logic)

- `FakeSource` option `answer_telecommands = true`: accepts `Telecommand` packets (`received_telecommands`) and does nothing else.
- The pty fake vehicle (`tools/fake_vehicle_pty.py`, `exvectrnet-1`): a frame on port 0x7F is parsed by the independent decoder in `tools/fake_vehicle_net.py` (`parse_telecommand`) into `received_telecommands` (type, subsystem, param, src, dst, seq, ctrl, ttl). As `vehicle` it also applies two effects to what it reports in `VEHICLE_STATUS`: `Arm` with `0xA5` arms (any other value disarms) and `SimulationMode` with `0xA9` toggles `simulation`, once per frame. These are test doubles, not vehicle logic.

## ZeroEstimators and the ZEROING indicator (user request 2026-11-20)

`SystemReset` no longer zeroes the estimators on the vehicle; `ZeroEstimators` (type 10, `paramInt` 0x2E validation value, `paramFloat` duration in s) does. Same rules as every entry: control hosts, 500 ms rate limit, hold 1 s, one frame, no retry, no ACK, never in replay, disabled without a route; the GS adds no vehicle-state gate. The vehicle reports the state in `VEHICLE_STATUS.flags` bit 7 `ZEROING`, mapped to `VehicleStatus.zeroing` (bool, optional). The mapping looks the constant (`FLAGS_ZEROING`) up by name and gives `None` ("n/s" in the GUI) while the pinned ExVectrPackets schema does not have it, the same pattern as every other flag; it starts working with the pin update, no code change needed. The mission status bar shows the amber **ZEROING** cell from that flag only, plus "ZeroEstimators sent N s ago" from the GS's own send record; no countdown is computed from the commanded duration. The pty fake vehicle decodes type 10 with the independent decoder and sets its reported ZEROING bit for the clamped duration (test double).

## Simulation wind (owner approval 2026-11-21)

The owner widened the whitelist for exactly five simulation-only commands, types 11..15 (`TelecommandType.SimWindSpeed` .. `SimGustSeed`). Frame: the usual 8 bytes `<BBhf` on port 0x7F, `subsystem` 0, `paramInt` = 0x57 (the vehicle's magic, fixed), the value in `paramFloat`. The vehicle refuses them outside simulation mode, with a wrong magic or with non-finite values, clamps out-of-range values, and handles only the newest command per 100 ms tick. Wind and gusts reset to zero whenever simulation mode switches on or off on the vehicle; `SystemReset` keeps them. The GS interprets nothing.

| Name | Type | Value (float) | Whitelist range |
|---|---|---|---|
| `SimWindSpeed` | 11 | wind speed, m/s | 0..20 |
| `SimWindDirection` | 12 | direction the air moves TOWARD, rad, counterclockwise from North toward West (NWU: 0 = toward North, pi/2 = toward West), wrapped to +-pi | -3.1416..3.1416 |
| `SimGustStrength` | 13 | peak extra speed along the mean direction, m/s | 0..10 |
| `SimGustPeriod` | 14 | gust period, s | 0.5..30 |
| `SimGustSeed` | 15 | integer-valued float | 0..16777215 |

Examples (checked by `tests/unit/test_sim_wind_telecommands.py`): speed 5.0 = `0B 00 57 00 00 00 A0 40`; direction pi/2 = `0C 00 57 00 DB 0F C9 3F`; gust 3.0 = `0D 00 57 00 00 00 40 40`; period 4.0 = `0E 00 57 00 00 00 80 40`; seed 42 = `0F 00 57 00 00 00 28 42`.

Rules are those above: whitelist, control hosts, hold 1 s, never automatic, never in replay, one frame per `telecommand.send`, no retry, no ACK. The only differences: the `rate_group` spacing exception, and the GUI panel sends a **group** of frames per hold (below). Entries with a `rate_group` are listed in `telecommand.list` (field `rate_group`) and hidden from the generic Commands panel.

### GUI: page "Simulation testing", section Wind

Dock tab `Simulation testing` (panel `simtests`, `web/app/telecommand/simtests_panel.js`; the Wind section is the former `Sim wind` panel, unchanged: `web/app/telecommand/simwind_panel.js`, logic `simwind_logic.js`, tests `web/test/simwind_logic.test.js`). Fields: wind speed, direction in degrees with a convention selector, gust strength, gust period, gust seed. Direction: **"Wind from"** (compass bearing the wind comes from, clockwise, 0 = N, 90 = E; the vehicle value is `-(deg + 180)` degrees, wrapped) or **"Air moves toward"** (counterclockwise from North, 90 = West; the vehicle value as is). The converted radian value the vehicle receives is always shown. **Apply wind** (hold 1 s, one hold for the group) sends the five commands in the order speed, direction, gust strength, gust period, seed, 150 ms apart, once each; the sequence stops at the first refusal and nothing is retried. **Clear wind** (hold 1 s) sends speed 0 and gust strength 0 (two frames, 150 ms apart). Both buttons are enabled only while `VehicleStatus.simulation` reports true (display gating; the vehicle refuses anyway), in the live view, on a control host with a route. The panel shows, per command, `#n sent <age> ago once via <source>, param ...; no ACK exists` from the core's record, and the simulation state exactly as the vehicle reports it; nothing else about the outcome. The wind arrow in the 3D view is not implemented.

## Simulation servo faults (owner approval 2026-11-21)

Three more simulation-only commands, types 16..18 (`SimFlapStuck`, `SimFlapSlow`, `SimFaultClear`), group "Simulation tests" in the configs. Frame: the usual 8 bytes `<BBhf` on port 0x7F, `paramInt` = 0x57 (magic, fixed). The vehicle refuses them outside simulation mode and writes a log line (visible in the GUI Console) whenever a fault is set or released. No ACK. **No `rate_group`**: they are single commands under the normal 500 ms per-client rule; the wind group exception is not widened. One frame per hold, no retry, control hosts only, never in replay.

| Type | Subsystem byte | Float | Whitelist range |
|---|---|---|---|
| 16 `SimFlapStuck` | flap index only (0 TL, 1 TR, 2 BL, 3 BR); the 0x10 'arm for the descent' bit was removed on the vehicle and is refused | the flap's setting is forced to this angle in RADIANS at once, 0..pi/2; **-1.0 = hold the current angle** | -1.0..1.5708 |
| 17 `SimFlapSlow` | flap index 0..3 | servo rate factor | 0.05..1.0 |
| 18 `SimFaultClear` | 0 | 0 | none (fixed) |

The subsystem byte is part of the whitelist entry (not client-settable): 4 `SimFlapStuck{TL,TR,BL,BR}Now` entries (subsystem 0..3; the name keeps the old `Now` suffix, there is no `Descent` variant any more), 4 `SimFlapSlow{TL,TR,BL,BR}` and `SimFaultClear`. `subsystem` in the config accepts 0..6. The GUI finds the entry by type and subsystem. Entries of group "Simulation tests" are hidden from the generic Commands panel.

Examples (checked by `tests/unit/test_sim_fault_telecommands.py`): TL forced at 42 deg (0.7330383 rad) = `10 00 57 00 66 A8 3B 3F`; BR hold the current angle = `10 03 57 00 00 00 80 BF`; slow factor 0.3 on TL = `11 00 57 00 9A 99 99 3E`; clear = `12 00 57 00 00 00 00 00`.

### GUI: page "Simulation testing" (panel `simtests`)

One dock tab hosting a **registry of sections** (`web/app/telecommand/simtests_registry.js`: `registerSimSection({id, title, order, create})`; a new simulation test is one new section file imported by `simtests_panel.js`). Sections: **Wind** (above) and **Servo faults** (`simfaults_section.js`, logic `simtests_logic.js`, tests `web/test/simtests_logic.test.js`, browser test in `tests/integration/test_ops_gui.py`). Every section is greyed out, every button and field disabled and labelled "simulation mode only" unless the vehicle reports `VehicleStatus.simulation` true, in the live view, on a control host with a route (display gating only; the vehicle also refuses). The page says why a section is disabled.

Servo faults UI: general and simple, no arming and no presets. Flap selector (TL, TR, BL, BR in a 2x2 layout around a "nose up" body), angle in degrees 0..90 (default 42; the converted radian value is shown) or "hold the current angle" (-1.0), **Force flap** (hold 1 s; one `SimFlapStuck` frame, subsystem = flap index, the flap setting is forced at once), servo rate factor with **Apply slow servo** (hold 1 s) and **Release all faults** (hold 1 s, `SimFaultClear`). The section also lists the flap angles the vehicle reports (`FlapState.angle`); a flap shows `(forced?)` when its commanded angle (`FlapState.cmd`) and true angle differ by more than 2 degrees for more than 1 s. That hint is display only and is skipped when `cmd` is not reported. The 3D view and the Actuators panel already show the reported true angle. Guards unchanged: simulation mode only (greyed out otherwise), live view, control host, route, one frame per press, no retry, 500 ms rule, never in replay. The page shows `<command>: #n sent N s ago once via X; no ACK exists` from the core's record; the vehicle's log line appears in the Console.
