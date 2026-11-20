# TEMPORARY legacy telecommand uplink

> **TEMPORARY. TODO: delete this whole path when the flight software's T9b lands** (it replaces the legacy telecommand by `RC_STATE` mapping and `REQUEST`s): the package `src/exvectr_gs/telecommand/`, `[telecommands]` in the configs, the API ops `telecommand.*`, `src/exvectr_gs/cli/cmd_tc.py` (`exgs tc`), the GUI panel `telecommands` (`web/app/telecommand/`, `web/app/panels/telecommands.js`), the packet `Telecommand` in `packets/defs/link.py`, `_encode_telecommand` in the wire profile, `answer_telecommands` in the fake source, the telecommand parts of the pty fake vehicle, the `telecommand.*` tests and this file.

Spec: user decision 2026-11-20 (explicit approval), flight-software `docs/project/hardware-and-drivers.md` section 10, `src/comms/telemetry_mapping.*` (`LegacyTelecommandPacket`), `src/telecommand.hpp`. Code: `src/exvectr_gs/telecommand/controller.py` (`TelecommandController`), config `core/config.py` (`[telecommands]`), API ops in `api/server.py`, CLI `cli/cmd_tc.py`, GUI `web/app/telecommand/`.

**Safety-relevant.** This is a **third uplink**, besides RC state ([radio.md](radio.md)) and the whitelisted REQUESTs ([requests.md](requests.md)). It exists because the vehicle (until T9b) can only be armed, put into simulation, reset or sent on a mission by this legacy command. The ground station makes no decision for the vehicle: it does not interpret a command, it checks no vehicle state, and it never shows an assumed result.

## The frame

The vehicle accepts the old 8-byte `Telecommand` on ExVectrNetwork **port 0x7F** from **node 0**, addressed to node 1. It is not in the ExVectrPackets schema, so the encoder is a plain 8-byte pack inside the wire profile (`wire/exvectrnet_1/profile.py`, `_encode_telecommand`), the only place that knows the layout.

| Byte | Field | Notes |
|---|---|---|
| 0 | `type` | `TelecommandType`: 0 CalibStart, 1 CalibApply, 2 Load (not handled by the vehicle), 3 Save, 4 SystemReset, 5 Arm, 6 SimulationMode, 7 MissionBegin, 8 MissionSelect, 9 MissionAbort |
| 1 | `subsystem` | `Subsystem`: 0 Gyro, 1 Accel, 2 Magneto, 3 Baro, 4 GNSS, 5 AttFilter, 6 PosFilter |
| 2-3 | `paramInt` | int16, little-endian |
| 4-7 | `paramFloat` | float32, little-endian; unused by every handler, sent as 0 |

Network frame: CTRL `0x48` (DATA, QoS EVENT, not latest), TTL 3, DST 1, SRC 0, PORT 0x7F, SEQ (+1 per command), then the 8 bytes. A test checks these bytes against an independent decoder (`tools/fake_vehicle_net.py`). Vehicle semantics (magic values, preconditions) are the flight software's; the ground station knows only the whitelist below.

## Rules (enforced in `TelecommandController.send`)

- **Whitelist only** (`[telecommands]`). A client names an entry; it cannot name a type or send a raw frame. The only value a client may choose is the int16 parameter of an entry that has a range (`param_min`/`param_max`, today `MissionBegin`), and only within that range.
- **One frame per confirmation. No retry, no repeat, no ACK.** The vehicle de-duplicates nothing, so a repeat would run the command twice. `SimulationMode` **toggles** on the vehicle; a second frame would switch back. There is no timer, no queue and no retry loop in the controller. A frame that no source accepts is refused with `no_route` and **nothing is queued** (a reconnecting source sends nothing).
- **Result only from what the vehicle reports.** There is no acknowledgement packet. The GUI shows `sent <time> ago once via <source>` and nothing else about the outcome; the effect is read in the mission status bar from `VehicleStatus` (`simulation`, `armed`, `mode`, ...). The simulation state is never assumed: only `VehicleStatus.simulation` as reported.
- **Control hosts only** (`server.control_hosts`, the same as RC control), checked in the API (`control_denied`) and again in the controller from the peer address.
- **Deliberate confirmation:** GUI hold of 1 s on the button (progress bar; a click, short hold, blur or leaving the button sends nothing); CLI `exgs tc send NAME --confirm NAME` (the name repeated, no default); API `telecommand.send` needs `confirm` equal to the entry name (`confirm_required` otherwise).
- **Rate limit:** at most one new telecommand per 500 ms per client host (`rate_limited`, keyed by peer address; a refused send does not count).
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
# confirm = "hold"         # the only level
# note = "shown next to the button"
```

Validation (`ConfigError`, the core does not start): unknown `type`, duplicate or empty `name`, parameter outside int16, `param_min` without `param_max`, default outside the range, subsystem not 0..6, `confirm` not `hold`, unknown keys and unknown source ids. No `[telecommands]` section means an empty whitelist: nothing can be sent.

Shipped in `config/gs.example.toml` and `config/gs.pocket.toml` (the values are the vehicle's, from the flight docs section 10):

| Name | type, subsystem, param | Vehicle behaviour (flight software, not interpreted here) |
|---|---|---|
| `Arm` | Arm, 0, `0xA5` | arms; any other value disarms |
| `Disarm` | Arm, 0, `0` | disarms |
| `SystemReset` | SystemReset, 0, `0xC5` | mode Startup, re-init, clears failures, **disarms**, resets the mission and position reference; does not leave simulation mode |
| `SimulationMode` | SimulationMode, 0, `0xA9` | **toggles** simulation mode |
| `MissionBegin` | MissionBegin, 0, default -10, range -3600..3600 | `paramInt` = initial mission time in s (-10 or less gives the full countdown); needs mode Ready; arming is not required |
| `MissionAbort` | MissionAbort | in a running mission: return to home; otherwise actuators off, mission reset |
| `CalibMagStart` / `CalibMagStop` | CalibStart, Magneto, 1 / 0 | `> 0` starts the magnetometer calibration, `<= 0` stops it |
| `CalibMagApply` | CalibApply, Magneto | applies the fit to RAM until saved |
| `MagSave` | Save, Magneto | writes the EEPROM image |

Only the magnetometer is handled by the vehicle's calibration commands; `Load` and `MissionSelect` are not whitelisted (not handled or no-op). A range of -3600..3600 s for `MissionBegin` is the ground station's own bound (the vehicle takes any int16); change it in the config.

## API and CLI

`telecommand.send {name, confirm, param?}` replies with the record (`n`, `name`, `type`, `subsystem`, `param_int`, `client`, `source`, `t`): there is deliberately **no state or result field**. `telecommand.list` replies `{commands: [{name, type, label, group, subsystem, param_int, param_min, param_max, confirm, note, route}]}` (`route` is the id of the source a send would use now, display only). The core pushes `{"op": "telecommands", recent, route}` after hello and after every send; the 2 Hz `status` push carries `telecommand_route`. Error codes: `control_denied`, `not_whitelisted`, `confirm_required`, `rate_limited`, `no_route`, `invalid_value` ([api.md](api.md)).

```bash
exgs tc list
exgs tc send SimulationMode --confirm SimulationMode      # ONE frame; prints "no ACK exists"
exgs tc send MissionBegin --confirm MissionBegin --param -30
```

`send` prints that the frame was handed to a source once and that no ACK exists; check `exgs status --json` or the status bar for what the vehicle reports. Exit codes: 0 sent, 1 any error, 3 control denied.

## GUI: panel "Commands (temporary)"

Panel `telecommands` (`views = ["live"]`, shipped collapsed, next to the Requests panel). A warning bar states that it is temporary and that there is no ACK and no retry. Each button needs a 1 s hold; `MissionBegin` has a number field (the parameter, validated against its range; the button is disabled outside it). Per row: `#n sent <age> ago once via <source>, param p; no ACK exists`. The panel also shows `vehicle reports: SIMULATION` or `REAL SENSORS` from `VehicleStatus.simulation` (never an assumed state; "no VehicleStatus received" without data). Disabled in the replay view, without a connection, on a view-only host and when no source reaches the vehicle. Logic: `web/app/telecommand/telecommand_logic.js` (`web/test/telecommand_logic.test.js`); end to end: `tests/integration/test_ops_gui.py`.

## Test doubles (no vehicle logic)

- `FakeSource` option `answer_telecommands = true`: accepts `Telecommand` packets (`received_telecommands`) and does nothing else.
- The pty fake vehicle (`tools/fake_vehicle_pty.py`, `exvectrnet-1`): a frame on port 0x7F is parsed by the independent decoder in `tools/fake_vehicle_net.py` (`parse_telecommand`) into `received_telecommands` (type, subsystem, param, src, dst, seq, ctrl, ttl). As `vehicle` it also applies two effects to what it reports in `VEHICLE_STATUS`: `Arm` with `0xA5` arms (any other value disarms) and `SimulationMode` with `0xA9` toggles `simulation`, once per frame. These are test doubles, not vehicle logic.
