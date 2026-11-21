# Wire profiles

Spec: design sections 2.4, 3.6 and 6.1. Wire-format knowledge lives **only** in a profile plug-in. Everything above it (sources, bus, store, recorder, API, GUI) sees semantic packets. `SerialSource` knows nothing about bytes: it moves bytes between the port and `Framer.feed` / `Framer.encode`, and frames between the framer and `WireProfile.decode` / `encode`.

## Contract (`exvectr_gs.wire.profile`)

- `Framer`: `feed(bytes, t)` yields `Frame` (channel, payload, t, meta) or `ConsoleText` (non-frame bytes, one line each); `encode(frame)`; `reset()` on (re)connect; `stats` (bytes, frames, crc_errors, resyncs).
- `WireProfile`: `name`, `make_framer()`, `decode(frame, src)` (never raises, undecodable frames become a `RawFrame` packet), `encode(packet)` (`None` if the profile cannot send that type), `supported_types()`. A profile may also offer `decode_many(frame, src) -> list[Packet]` (one wire packet feeding several semantic types, or none for a suppressed duplicate). Callers use `decode_frame(profile, frame, src)`, which falls back to `[decode(...)]`. `SerialSource` does.
- `load_profile(name)` imports `exvectr_gs.wire.<name with dashes as underscores>` and calls its `make_profile()`. Unknown names raise `ProfileNotFound`.

Optional profile extension `uplink_air_items() -> list[core.rates.UplinkItem]`: the packet types the Router sends to the ship radio node with their air rate and payload size (`exvectrnet-1`: `RC_STATE` at 25 Hz from air profile v1, `LINK_CONFIG` and the heartbeat at their schema air rates), used only for the ESTIMATED uplink bar (docs/api.md `get_rates`).

Optional profile extension for the data-rate view (docs/api.md "Data rates"): `describe_frame(frame) -> core.rates.FrameInfo` (wire packet name, origin node, whether the frame crossed the air, schema rate_air / rate_usb). `Frame.wire_len` is the frame's size on the stream including framing (0 when the framer does not know; `NetFramer` sets it, `provisional-1` does not, so its byte rates fall back to the payload size and it has no per-frame statistics).

## Profiles

| Name | State |
|---|---|
| `fake` | Implemented (T1). No bytes: `FakeSource` emits packets directly. Decodes nothing, encodes nothing. |
| `provisional-1` | Implemented (T5), **temporary**, kept working. Legacy payload layouts in a provisional COBS framer. No vehicle speaks it. |
| `exvectrnet-1` | Implemented, **the real format**: COBS stream frames with CRC-16, 4-byte network header, 2-byte transport header, heartbeats, packets from the generated `exvectr_packets` (`docs/network-design.md` section 4). Section "`exvectrnet-1`" below. |

To add a profile: write the module with `make_profile()`, adjust the registry for new or changed semantic types, select it with `core.wire_profile` in `gs.toml`. Recorded logs are semantic text and stay readable. The package is `src/exvectr_gs/wire/provisional_1/` (`framing.py`, `layouts.py`, `profile.py`); a new profile is a sibling package and nothing else changes.

## `exvectrnet-1`

Package `src/exvectr_gs/wire/exvectrnet_1/`: `framing.py` (stream frame, network and transport header, `NetFramer`), `profile.py` (`ExVectrNet1Profile`: presence, version check, duplicate suppression, port to class via `exvectr_packets`, uplink) and `mapping.py` (wire packet to GS semantic packets). Spec: `docs/network-design.md` 4.1 to 4.5 and 4.7. `framing.py` reuses `cobs_encode`, `cobs_decode` and `crc16` of `provisional_1/framing.py` (unit tests check them against `ExVectrNetwork/test/vectors/wire_v1.json`, including the 254/255 byte COBS edge cases and the invalid vectors; the independent encoder of the fake vehicle is checked against the same file).

### Dependencies

**Pin (2026-11-21):** `lib/ExVectrPackets` is at commit `18b6884` (branch `feature/setpoint-attitude`, on GitHub as `origin/feature/setpoint-attitude`), schema hash **`0xBFF82392`**. New since `53a7020`: `setpoint_q` (4 values, quaternion [w,x,y,z] body to world, NWU, as `POSE.q`) appended to `MISSION` (f32) and `AIR_MISSION` (fixed i16, scale 1/32768), mapped to `MissionState.sp_q`. All four zero (no attitude controller active) or NaN (older sender) gives `sp_q = None` (not available). A node on an older hash is still flagged as a mismatch. Previous pin text follows.

**Pin (2026-11-20, updated again):** `lib/ExVectrPackets` is at commit `53a7020` (local-only commit from `flight-software/lib/ExVectrPackets`, branch `feature/split-reset-zero`; not on GitHub yet), schema hash **`0x761D5378`**. New since `2c9c121` (`0x4FE838D2`): `VEHICLE_STATUS.flags` bit 7 `ZEROING` (generated constant `FLAGS_ZEROING` = 128, mapped to `VehicleStatus.zeroing`, status bar cell "Estimators"), and the air packets `AIR_ACTUATORS` (0x22) and `AIR_MISSION` (0x23), mapped below. Earlier pins: `2c9c121` (`AIR_POSE.pos` i32 at 1 mm, flag `UART_PEER_SEEN`), `a96bd7b` (`0x8D56E26D`: `LINK_REBOOT`, `LINK_CONFIG` port 0x04) and the vehicle packet `CALIBRATION` (0x93). A node on another schema hash is flagged by the mismatch banner in the status bar and `NetworkStatus.incompatible` until it is reflashed to the pinned hash.


Git submodules `lib/ExVectrPackets` (generated Python packets, schema, vectors, docs) and `lib/ExVectrNetwork` (spec vectors), pinned to commits that are **not on GitHub yet** (they come from the local `Development` clones; `git submodule update --init` needs them pushed or fetched from the local clone). `exvectr_packets` is imported from `lib/ExVectrPackets/python` without copying: `pyproject.toml` puts it on the pytest path, and `exvectrnet_1/_deps.py` adds it to `sys.path` at import if it is not installed. Hash values, flag masks, ports and scalings are always taken from the generated module, never written here: the profile never contains the schema hash or a flag value (the `MOTOR_ENABLE` flag of `RC_STATE` is expected to leave the schema soon: nothing here refers to it).

### Framing and headers

```
wire    := COBS(decoded) 0x00                      (no leading delimiter; empty runs are ignored)
decoded := SKIND 0x01 | network frame (4..240 B) | CRC-16/CCITT-FALSE LE over SKIND..frame
network := CTRL | TTL | DST | SRC | (DATA) PORT | SEQ | payload   or   (HEARTBEAT) 11 B payload
```

- The framer drops a frame (and only that frame) when COBS fails, the decoded length is outside 7..243, the CRC differs, SKIND is not 1, the network version is not 1, the type is reserved, or a DATA frame has no full transport header. Bad CRC counts as `crc_errors`, everything else as `resyncs`. More than 256 bytes without a delimiter are discarded up to the next delimiter. No console text exists on this stream (the vehicle's `LOG_TEXT` packet replaces it).
- `Frame.channel` is the port (DATA) or `0x100` (heartbeat). `Frame.meta` has `type`, `qos`, `latest`, `ttl`, `dst`, `src` (node id) and `seq`.
- Node ids: 0 ground station, 1 vehicle, 2 Router, 3 ship radio node, 0xFF broadcast.

### Heartbeats, presence, duplicates

- Every valid frame from node 0..3 counts as presence for that node, per source id. A heartbeat produces a `NetworkStatus` packet: `present` and `age` of the four nodes (present = a valid frame within 3 s, evaluated when a heartbeat arrives), the sender's role, version, schema hash and uptime, `incompatible` (any present node with another network version or schema hash than this GS: `exvectr_packets.SCHEMA_HASH`) and `detail`. A change of that state also emits one `GsEvent` of kind `link_warning` (and a log warning). Frames are still decoded (append-only evolution).
- Duplicate suppression (design 3.4): per (source id, node, port) a DATA frame is accepted if `(seq - last) mod 256` is in 1..127, or if nothing arrived on that key for 1 s, or it is the first. Otherwise it is dropped (`decode_many` returns `[]`, `profile.duplicates_dropped` counts them). Keys include the source id, so the same frame arriving over two links (vehicle USB and TX module) is shown for both and the state store picks the preferred source. Consequence of the rule: after a vehicle reboot with a fresh random sequence number, frames can be dropped until the sequence passes the old value or one second of silence has gone by. Heartbeats carry no sequence number and are not filtered.

### Ports and mapping (wire packet to GS types)

Decoded values are SI already. Where the wire does not carry a field, the GS field stays `None`; nothing is derived or inferred. Enumerations the GS knows are decoded to the enum, other values stay plain integers. A wire packet may feed more than one GS type so that the existing panels keep working.

| Port | Wire packet | GS packet types | Notes |
|---|---|---|---|
| - | `HEARTBEAT` | `NetworkStatus`, `GsEvent` (on change) | see above |
| 0x01 | `LINK_STATS` | node 2: `TxModuleStats` and `RadioLinkStats`; node 3: `RxModuleStats` | other senders: `RawFrame`. `RadioLinkStats` gets `rssi_dbm = [rssi]` (one element), `lq_pct`, `snr_db`, `antenna` (RX winner); `up_hz`, `down_hz` are not supplied. Antenna byte split into `rx_antenna` and `tx_antenna` |
| 0x04 | `LINK_CONFIG` | `LinkConfig` (`tx_power_dbm`, `flags`, `from_node`) | the Router's commanded configuration for the ship radio node. A real Router sends it to the air only and drops it from USB, so the ground station normally receives it only when a source carries it. Display only (link panel) |
| 0x02 | `LOG_TEXT` | `ConsoleText` | text is `[node N SEVERITY] text`, shown in the console panel |
| 0x03 | `HANDSET_TELEM` | `HandsetTelem` | flags split into bools; shown as the vehicle sent it (no inference) |
| 0x10 | `RC_STATE` | `RcState` | origin `gs` if sent by node 0, otherwise `handset` (the Pocket via the Router). `ch_us = 1500 + ch / 2` (round half up), `CH_INVALID` becomes 0, `seq` is the wire sequence, `flags` and `source_ok` as received |
| 0x11 | `REQUEST` | `RawFrame` | an uplink packet, not expected from a peer (the GS sends it, see "Uplink" below) |
| 0x12 | `REQUEST_ACK` | `RequestAck` | |
| 0x20 | `VEHICLE_STATUS` | `VehicleStatus`, `VehicleState` | `VehicleState` gets `mode`, `armed`, `sim` only (what the wire states directly) |
| 0x21 | `AIR_POSE` | `Attitude` (`q` only), `Position` (`pos`, `vel`) | radio set; fixed point values already scaled |
| 0x22 | `AIR_ACTUATORS` | `Actuators`, `FlapState` | **two samples per frame** (25 frames/s, 50 samples/s): the newest at the frame's host receive time `t`, the previous at `t - prev_age` (about 20 ms older), emitted older first; each sample gives one `Actuators` (`flaps`, `tvc`, `thrust`, new optional field `roll_torque` in N*m) and one `FlapState` (`angle`). A NaN `prev_age` means no previous sample: only the newest is emitted. Sentinels decode to NaN. `ground_distance`, `FlapState.cmd`, `enabled` not supplied. Air only |
| 0x23 | `AIR_MISSION` | `MissionState` | `mission_time` to `t_mission` ns (negative in the countdown), `mission_mode`, `phase`, `index`, `setpoint_pos`/`setpoint_vel` to `sp_pos`/`sp_vel`, new optional field `waypoint` (255 = not the waypoint mission becomes `None`). Air only, 5 Hz; `setpoint_q` to `sp_q` (zero or NaN = None) |
| 0x80 | `POSE` | `Attitude` (`omega`, `q`), `Position` (`pos`, `vel`) | USB; accuracy fields not supplied |
| 0x81 | `ACTUATORS` | `Actuators`, `FlapState` (`angle` = flaps) | `FlapState.cmd` and `enabled` not supplied |
| 0x82 | `MISSION` | `MissionState` | `mission_time` in seconds to `t_mission` ns; `mission_mode` not on this packet (see `VehicleStatus`); `setpoint_q` to `sp_q` (zero or NaN = None) |
| 0x83 | `GNSS` | `Gps` | radians; `h_acc`, `v_acc`, `s_acc` to `pos_acc`, `alt_acc`, `vel_acc`; new field `fix_type` |
| 0x90 | `IMU_RAW` | `ImuRaw` | debug set, USB only |
| 0x91 | `ESTIMATOR` | `Estimator` | debug set; `att_std` is the 1 sigma about the WORLD axes [North, West, Up] (packet version 2 here) |
| 0x93 | `CALIBRATION` | `Calibration` | USB only, every 2 s: offsets, row-major 3x3 matrices, source bits and the EKF gyro bias; the panel computes calibrated values from it (display only) |
| 0x92 | `TASK_TIMING` | `TaskTiming` | debug set; `NaN` marks an invalid entry |
| other | unknown port | `RawFrame` | `err = "unknown port"`; a known port with a bad payload gives `err = "<PACKET>: ..."` |

Vehicle time (`t_us` of each packet) is not carried into the GS packets: `t` is the host receive time. `VehicleStatus.vehicle_time` keeps the full vehicle clock. `RawFrame` stays the only raw-bytes record.

### Uplink (`encode`)

Only `RC_STATE` with `origin = gs` and 16 channels. `ch = clamp((us - 1500) * 2, CH_MIN, CH_MAX)`, a pulse width of 0 or less becomes `CH_INVALID`. `flags` contains only `SOURCE_OK` (the ground-station input is active: the core sends only while a client holds the lease), unless the packet says `source_ok = False`. No other flag is ever set; the motor enable is just a channel in the vehicle's fixed mapping. The frame is addressed to the vehicle (DST 1, SRC 0, TTL 3, QoS and LATEST from the generated class), whichever link carries it. The sequence number counts per port in the profile (the `RcState.seq` of the controller is not used). `encode` sends no other RC packet. `SerialSource` still sends `RcState` only through the source with `rc_role = "control"`: configure the vehicle USB as `control` and the TX module as `mirror`, because the vehicle rejects node 0 on the radio path.

### Sources

| Source | Peer | Role |
|---|---|---|
| `kind = "vehicle_usb"` | vehicle, node 1, direct | full USB rates and debug set; `rc_role = "control"` |
| `kind = "link_module"` | Router, node 2 | forwards the vehicle radio set (node 1), ship radio node (node 3), the Pocket's `RC_STATE` and its own `LINK_STATS`; `rc_role = "control"` in the example config (master sends RC to it, the Router rejects it), `"mirror"` for receive-only |

`source_priority = ["usb", "link", "fake"]` prefers the vehicle USB per type when both are fresh.

## `provisional-1`

### Framing (`framing.py`)

```
stream := ( console-text | frame )*
frame  := 0x00  cobs(body)  0x00
body   := channel u16 LE | len u16 LE | payload[len] | crc16 u16 LE
crc16  := CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection) over channel..payload
```

- It uses the same CRC and COBS as the real stream framing will (`binascii.crc_hqx(data, 0xFFFF)`), so the real profile can reuse `cobs_encode` / `cobs_decode` / `crc16`. Header and delimiters differ: this is deliberately not the network frame.
- Both a leading and a trailing `0x00` are sent. Neither COBS output nor console text contains `0x00`, so a zero byte always marks a frame boundary.
- Bytes outside frames are console text (`Serial.print`), split on `\n` (CR stripped, invalid UTF-8 replaced, lines over 1024 bytes split). Each line becomes a `ConsoleText` packet.
- Errors: a bad COBS block, a length mismatch or a short frame counts as `resyncs`; a CRC mismatch counts as `crc_errors`. After a failed frame the closing `0x00` is also treated as the next frame's leading delimiter, so one bad frame costs one frame. Console text immediately after a bad frame can be swallowed. A frame body longer than the maximum (1024 payload bytes) is dropped (`resyncs`). `reset()` drops partial state but keeps the counters, which describe the whole session.

### Channels and layouts (`layouts.py`)

Little-endian, packed. Fixed point: `value = raw * limit / 32767` (the vehicle saturates and truncates; `INT16_MIN` therefore decodes to -1.00003 x limit and is kept as is). A payload whose length differs from the layout becomes a `RawFrame` with `err = "<Type>: length N, expected M"`. An unknown channel becomes a `RawFrame` with `err = "unknown channel"` (for example port 100, the connection counter).

| Channel | Packet | Bytes | Layout and scaling |
|---|---|---|---|
| 10 | `Attitude` | 18 | `int16[3]` omega (limit 1000 deg/s as rad/s), `int16[4]` q w,x,y,z (limit 1), `int16` tilt_acc, `int16` north_acc (limit 360 deg as rad) |
| 11 | `Position` | 16 | `int16[3]` pos (limit 1000 m), `int16[3]` vel (limit 100 m/s), `int16` h_acc, `int16` v_acc (limit 10) |
| 12 | `Gps` | 23 | `int32` lat, `int32` lon (value / 1e7), `int16` alt (limit 1000 m), `int16[3]` vel (limit 100 m/s), `uint8` num_sats, `int16` pos_acc, alt_acc, vel_acc (limit 10) |
| 50 | `VehicleState` | 27 | 15 x `uint8`: mode, 6 sensor states (gnss, imu, baro, mag, att_kf, pos_kf), 8 flags (fail_sensor, fail_attitude, fail_position, fail_pos_oob, fail_att_oob, fail_radio_loss, sim, armed); then `int16` tvc_thrust x,y,z and tvc_torque (limit 50), ground_dist (limit 2 m), ground_dist_cov (limit 1) |
| 51 | `MissionState` | 35 | `float32[6]` sp_vel then sp_pos, `uint8` mission_mode, `int64` t_mission (ns, negative in the countdown), `uint8` mission_index, `uint8` mission_phase |
| 16 (0x10) | `RcState` | 17 | `uint8` seq, `uint16[16]` channel pulse width in us. Provisional layout, the port is the `RC_STATE` port of `docs/network-design.md` |

Source of the layouts: `flight-software/docs/libs/ExVectrPackets.md` and `flight-software/docs/project/hardware-and-drivers.md` section 9.

Known legacy quirks, decoded as the vehicle sends them (not corrected):

- `Gps.lat` and `lon` are **radians** x 1e7 on the wire (the struct comments say degrees). The semantic type is in radians.
- `Position.h_acc` and `v_acc` carry covariance / 1000, which is not a length. The packet field description says so. `Gps.pos_acc`, `alt_acc` and `vel_acc` carry variances (m^2), not accuracies.
- `Attitude.tilt_acc` and `north_acc` are always 0 on the vehicle.
- Enumerations: a value outside the declared enum is kept as a plain integer (shown as `#n`), never an error.
- Values outside a field's limit have already saturated on the vehicle. NaN on the vehicle arrives as 0.

### Uplink (`encode`)

### Heartbeat (ground station presence, `encode_heartbeat`)

A profile may offer the optional method `encode_heartbeat(uptime_s) -> Frame` (looked up with `getattr`; `provisional-1` and `fake` have none and send nothing). The heartbeat is deliberately **not** a ground-station packet type: it is not in the registry, is never recorded or shown, and `encode()` returns `None` for it, so no code path that handles `Packet`s (RC controller, API) can produce one. `exvectrnet-1` builds the 11-byte payload of `docs/network-design.md` 4.5 (role 0 = ground station, network version 1, `exvectr_packets.SCHEMA_HASH`, uptime in ms as u32, flags 0) as a `Frame` with `channel = 0x100`, and the framer writes CTRL `0x56` (QoS LINK, LATEST from the generated `Heartbeat` class), TTL 3, DST `0xFF` (broadcast), SRC 0. It never touches the RC sequence counter.

Who sends it: `CoreApp._heartbeat_loop` (`core/app.py`), a periodic task of its own (1 Hz) that calls `Source.send_heartbeat(uptime_s)` on every source. `SerialSource.send_heartbeat` sends on any connected source whatever its `rc_role` (`control`, `mirror`, `none`). It returns `False` and sends nothing if the source is down, the profile has no heartbeat or the writer queue is full (never blocking, and never displacing a queued frame). The task is independent of the `RcController`, leases and RC state: nothing reads them, and nothing in the RC path reads heartbeat results. Over a TX module the router drops node 0 heartbeats before the air link, so the vehicle never sees them over radio. Over a direct USB connection the vehicle receives them as presence information. A ground-station failure or silence never causes a failsafe, and a heartbeat never keeps the vehicle alive.

### REQUEST (whitelisted one-shot actions)

`encode` also accepts the `Request` packet (the request controller's, [requests.md](requests.md)): `req_id` 1..65535, `action` (`RequestAction`), `target`, `arg_i`, `arg_f` become `xp.Request` (port 0x11, QoS and LATEST from the generated class), addressed to **DST = `target`** (1 vehicle, 2 TX module, 3 RX module), SRC 0, with its own per-port sequence counter. A missing or out-of-range field returns `None`. The wire profile does not know which actions are allowed: the whitelist lives in the request controller. `REQUEST_ACK` (0x12) decodes to `RequestAck` as before. `SerialSource` sends a `Request` on any `rc_role`, but only for a target within its `SourceCaps.request_targets` (the vehicle's USB reaches node 1; a TX module reaches 1, 2 and 3).

### Legacy Telecommand (TEMPORARY, port 0x7F)

`encode` also accepts the `Telecommand` packet (the telecommand controller's, [telecommands.md](telecommands.md)): `tc_type`, `subsystem`, `param_int` and `param_float` are packed little-endian as `<BBhf` (8 bytes) on **port 0x7F**, which is **not in the ExVectrPackets schema** (so the layout lives here, in `_encode_telecommand`, and nowhere else), addressed to node 1, DATA frame, QoS EVENT (2), not latest, TTL 3, so CTRL is `0x48`. The sequence number counts per port. `SerialSource.send` carries it only if the source reaches node 1. Delete with the flight software's T9b.

### RC state

Only `UP` packet types can be encoded. Today that is `RcState` with `origin = gs`, 16 channels, `Request` (above) and, TEMPORARILY, `Telecommand` (above); channels are clamped to 0..65535. Anything else (handset-origin state, wrong channel count, any non-UP type) returns `None`. A received `RcState` (a handset mirror from a TX module) decodes with `origin = handset`. There is no encoder for legacy telecommands and none will be added (design 1.4).

## Serial source (`exvectr_gs.sources.serial_source`)

Configuration (`[[source]]`, `kind = "vehicle_usb"` or `"link_module"`):

| Key | Meaning |
|---|---|
| `match` | `{ device = "/dev/serial/by-id/..." }` (a path, preferred) or `{ vid = "16c0", pid = "0483", serial = "..." }` (hex, case-insensitive, matched through `serial.tools.list_ports`; the first match wins) |
| `baud` | default 115200; ignored by Teensy USB CDC |
| `rc_role` | `control`, `mirror` or `none` |
| `deassert_dtr_rts` | default `false` for `vehicle_usb` (the Teensy needs DTR), `true` for `link_module` (CP210x/CH34x bridges reset the ESP32 when DTR/RTS toggle). Set before opening the port. On Linux the kernel raises DTR briefly at open, so the very first open after plugging a module can still glitch it |
| `poll_s` | how often to look for the device while `searching` (default 1.0) |
| `stats_period_s` | `LinkStats` period (default 1.0) |

Behaviour:

- States: `searching` (device not present), `connected`, `error` (device present but cannot be opened, for example permissions; `last_error` says why), `disabled` (not running). `source_up` and `source_down` `GsEvent`s mark transitions. On loss the framer is reset, a `LinkStats connected=false` is emitted and the source goes back to `searching` with no backoff. `reconnects` counts re-connections after the first one.
- One reader thread and one writer thread per connection. The reader hands bytes to the event loop (`call_soon_threadsafe`); framing, decoding and the sink run on the loop. The port is opened exclusively. Writes go through a queue of 8 frames; when full the oldest is dropped (latest wins).
- `send()` returns `False` when not connected, when the packet is not an `UP` type, when the profile cannot encode it, for `RcState` unless this source has `rc_role = "control"`, and for a `Request` whose target this source cannot reach. A mirror or `none` source never carries GS RC state (several sources may be `control`; master sends to all), whatever a caller asks (design 6.2).
- `LinkStats` at `stats_period_s` per source: `connected`, `rx_bytes_s`, `rx_frames_s`, `tx_frames_s` (frames queued for the port), cumulative `crc_errors` (framer), `decode_errors` (frames that became a `RawFrame`) and `reconnects`.
- Several sources run side by side. Each has its own framer and `src` id and publishes to the one bus; the `StateStore` picks the preferred fresh source per type from `core.source_priority`. The default `["usb", "link", "fake"]` prefers the vehicle's USB link for telemetry when the radio chain is connected as well.

## Fake vehicle (`tools/fake_vehicle_pty.py`)

A pseudo-terminal that speaks `provisional-1` or (`profile="exvectrnet-1"`) real ExVectrNetwork bytes from the scripted scenario. `exvectrnet-1` has two roles: `role="vehicle"` is node 1 on USB (heartbeat, `VEHICLE_STATUS`, `POSE`, `ACTUATORS`, `MISSION`, `GNSS`, `HANDSET_TELEM`, debug packets), `role="txmodule"` is node 2 forwarding node 1's radio set, node 3 and node 2 `LINK_STATS`, heartbeats of nodes 1, 2 and 3, and the Pocket's `RC_STATE` (node 2, 25 Hz). Its encoder (`tools/fake_vehicle_net.py`: COBS, CRC, headers, struct packers) is written from the wire spec and the packet reference tables, independent of `exvectrnet_1` and of the generated encoders (only the schema hash value is read from `exvectr_packets`, and `schema_hash=` / `net_version=` can override it to provoke the mismatch warning); a test decodes its packers with the generated module. `received` holds the DATA frames the ground station sent (parsed by the independent decoder, `meta` has src, dst, seq). `received_heartbeats` / `heartbeats_from(node=0)` hold the heartbeats it sent (dicts with ctrl, ttl, dst, src, payload, t). Role `txmodule` emulates the router rule: `forwarded` lists what it would pass to its vehicle side (DATA frames, other nodes' heartbeats) and never a node 0 heartbeat.

The `provisional-1` mode: It is a test stand-in with no vehicle logic: it only records the frames the ground station sends (`received`). Its payload packers are written independently of `layouts.py`, so decode tests compare two implementations.

```bash
.venv/bin/python tools/fake_vehicle_pty.py --link /tmp/fake-vehicle   # symlink to the pty slave
.venv/bin/python tools/fake_vehicle_pty.py --link /tmp/fake-vehicle --profile exvectrnet-1 [--role txmodule]
# gs.toml: [[source]] id = "usb", kind = "vehicle_usb", match = { device = "/tmp/fake-vehicle" }
```

In tests it is used as `FakeVehiclePty(link).start()`; `stop()` unplugs (the pty and the link vanish) and `start()` replugs. `send_frame`, `send_console` and `write_raw` inject traffic (unknown channels, bad lengths, garbage).
