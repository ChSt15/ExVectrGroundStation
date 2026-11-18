# Wire profiles

Spec: design sections 2.4, 3.6 and 6.1. Wire-format knowledge lives **only** in a profile plug-in. Everything above it (sources, bus, store, recorder, API, GUI) sees semantic packets. `SerialSource` knows nothing about bytes: it moves bytes between the port and `Framer.feed` / `Framer.encode`, and frames between the framer and `WireProfile.decode` / `encode`.

## Contract (`exvectr_gs.wire.profile`)

- `Framer`: `feed(bytes, t)` yields `Frame` (channel, payload, t, meta) or `ConsoleText` (non-frame bytes, one line each); `encode(frame)`; `reset()` on (re)connect; `stats` (bytes, frames, crc_errors, resyncs).
- `WireProfile`: `name`, `make_framer()`, `decode(frame, src)` (never raises, undecodable frames become a `RawFrame` packet), `encode(packet)` (`None` if the profile cannot send that type), `supported_types()`.
- `load_profile(name)` imports `exvectr_gs.wire.<name with dashes as underscores>` and calls its `make_profile()`. Unknown names raise `ProfileNotFound`.

## Profiles

| Name | State |
|---|---|
| `fake` | Implemented (T1). No bytes: `FakeSource` emits packets directly. Decodes nothing, encodes nothing. |
| `provisional-1` | Implemented (T5), **temporary**. Legacy payload layouts in a provisional COBS framer. No vehicle speaks it yet. |
| `exvectrnet-2` | After roadmap step 3: COBS stream frames with CRC-16, 4-byte network header, 2-byte transport header (`docs/network-design.md` section 4). Replaces `provisional-1`. |

To add a profile: write the module with `make_profile()`, adjust the registry for new or changed semantic types, select it with `core.wire_profile` in `gs.toml`. Recorded logs are semantic text and stay readable. The package is `src/exvectr_gs/wire/provisional_1/` (`framing.py`, `layouts.py`, `profile.py`); a new profile is a sibling package and nothing else changes.

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

Only `UP` packet types can be encoded. Today that is `RcState` with `origin = gs`, 16 channels; channels are clamped to 0..65535. Anything else (handset-origin state, wrong channel count, any non-UP type) returns `None`. A received `RcState` (a handset mirror from a TX module) decodes with `origin = handset`. There is no encoder for telecommands and none will be added (design 1.4).

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
- `send()` returns `False` when not connected, when the packet is not an `UP` type, when the profile cannot encode it, and for `RcState` unless this source has `rc_role = "control"`. A mirror or `none` source never carries GS RC state, whatever a caller asks (design 6.2).
- `LinkStats` at `stats_period_s` per source: `connected`, `rx_bytes_s`, `rx_frames_s`, `tx_frames_s` (frames queued for the port), cumulative `crc_errors` (framer), `decode_errors` (frames that became a `RawFrame`) and `reconnects`.
- Several sources run side by side. Each has its own framer and `src` id and publishes to the one bus; the `StateStore` picks the preferred fresh source per type from `core.source_priority`. The default `["usb", "link", "fake"]` prefers the vehicle's USB link for telemetry when the radio chain is connected as well.

## Fake vehicle (`tools/fake_vehicle_pty.py`)

A pseudo-terminal that speaks `provisional-1` bytes from the scripted scenario, with console text interleaved. It is a test stand-in with no vehicle logic: it only records the frames the ground station sends (`received`). Its payload packers are written independently of `layouts.py`, so decode tests compare two implementations.

```bash
.venv/bin/python tools/fake_vehicle_pty.py --link /tmp/fake-vehicle   # symlink to the pty slave
# gs.toml: [[source]] id = "usb", kind = "vehicle_usb", match = { device = "/tmp/fake-vehicle" }
```

In tests it is used as `FakeVehiclePty(link).start()`; `stop()` unplugs (the pty and the link vanish) and `start()` replugs. `send_frame`, `send_console` and `write_raw` inject traffic (unknown channels, bad lengths, garbage).
