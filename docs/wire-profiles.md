# Wire profiles

Spec: design sections 2.4 and 3.6. Wire-format knowledge lives **only** in a profile plug-in. Everything above it sees semantic packets.

## Contract (`exvectr_gs.wire.profile`)

- `Framer`: `feed(bytes, t)` yields `Frame` (channel, payload, t, meta) or `ConsoleText` (non-frame bytes, one line each); `encode(frame)`; `reset()` on (re)connect; `stats` (bytes, frames, crc_errors, resyncs).
- `WireProfile`: `name`, `make_framer()`, `decode(frame, src)` (never raises, undecodable frames become a `RawFrame` packet), `encode(packet)` (`None` if the profile cannot send that type), `supported_types()`.
- `load_profile(name)` imports `exvectr_gs.wire.<name with dashes as underscores>` and calls its `make_profile()`. Unknown names raise `ProfileNotFound`.

## Profiles

| Name | State |
|---|---|
| `fake` | Implemented (T1). No bytes: `FakeSource` emits packets directly. Decodes nothing, encodes nothing. |
| `provisional-1` | Planned (T5). Today's payload layouts (ports 10/11/12/50/51) in a provisional COBS framer: `channel u16 | len u16 | payload | crc16`. No vehicle speaks it yet. |
| `exvectrnet-2` | After roadmap step 3. |

To add a profile: write the module with `make_profile()`, adjust the registry for new or changed semantic types, select it with `core.wire_profile` in `gs.toml`.
