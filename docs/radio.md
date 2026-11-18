# Virtual radio, leases, safety notes

Spec: [ground-station-design.md](ground-station-design.md) sections 1.4, 5.6, 4.2 and decisions 10.1-1 and 10.1-4. Implemented in task T6: `src/exvectr_gs/rc/` (`radio_config.py`, `controller.py`), wired in `core/app.py` (`CoreApp.rc`), exposed by the `rc.*` API operations and `exgs rc ...` (see [api.md](api.md)). The radio widget (T9) is the GUI side.

**Safety-relevant.** The RC state is the only thing the ground station ever sends to the vehicle. The ground station makes no control, mission or safety decision: it maps control positions to channel values from a config and sends them while somebody holds a lease. It does not know what any switch means (the Starship's motor enable, mission go, and so on); the vehicle firmware interprets the channels with its own fixed mapping (workspace `docs/safety.md`, `docs/system-architecture.md` "Switch mapping").

## Radio config (`config/radio.pocket.toml`)

A config, never code: the vehicle-side channel mapping is fixed in the vehicle firmware and may change, then only this file changes. The core loads it at start (`[radio] config` in `gs.toml`) and the GUI reads it over `config.get radio` or `GET /config/radio.json`.

| Key | Meaning |
|---|---|
| `name`, `mapping_note` | Informational. `mapping_note` should name the vehicle channel table revision this file matches. |
| `send_rate_hz` | Fixed sender rate while a lease is active (1..100, default 25). |
| `channel_count` | 1..16 (the `RcState` packet carries 16 channels). |
| `channel_default_us`, `channel_defaults` | Pulse width of every channel not owned by a control: sticks neutral (1500 us) and, by `channel_defaults = { 3 = 1000 }` (1-based channel), throttle low. |
| `[[controls]]` | `id` (stable id for API/CLI), `kind` (`toggle2`, `switch3`, `momentary`, `slider`), `channel` (1-based), `values_us` (one per position, ascending, lowest first; switches) or `range_us` (slider), `default` (switch index or slider position: the **safe** position), `label`, `meaning`, `svg = {x, y}`. |

Pocket set shipped: SA (toggle2, ch 5), SB (switch3, ch 6), SC (switch3, ch 7), SD (toggle2, ch 8), SE (momentary, ch 9), S1 (slider, ch 10); the sticks are channels 1 to 4 at neutral. `meaning` texts (SA mission go, SB mission type, SC vehicle mode, SD motor enable, SE reset hold, S1 unassigned) are **labels for the operator only**: nothing in the core reads them.

Validation (`RadioConfigError`, the RC controller does not start): unique ids and channels, channels within `channel_count`, all pulse widths within 1000..2000 us, `values_us` ascending with 2 (toggle2, momentary) or 3 (switch3) entries, slider range ascending, defaults in range, a momentary control defaults to released, no unknown keys. If the config is invalid or missing, `CoreApp.rc` is `None` and an error is logged; the rest of the core runs and the API answers `not_implemented` to `rc.*`.

Control values: switches take a position **index** (0 = lowest), sliders 0..1, momentary 0 (released) or 1 (pressed). Anything else is rejected with `invalid_value` and **nothing** of that request is applied.

## Safe defaults, no persistence

Every control starts at its configured default (lowest/off, sticks neutral, throttle low) on every core start, on every `acquire`, whenever a lease ends, and whenever the control source drops. RC state is never written to disk and never resumed: after any end of control the next holder starts from the defaults, and the vehicle sees the safe state first (safety.md section 2, rule 3). The GS never toggles anything by itself.

## Control lease and sender

- **One holder, control hosts only.** `acquire` is denied (`ControlDenied` / `control_denied`) when a lease is already held, when no control source is connected, or when the client address is not in `server.control_hosts`. The API checks the address and the controller checks again (`remote_addr`), so a missing or unknown address is denied. A CLI on the core host can take a **timed** lease (<= 600 s) so an agent can drive simulation tests.
- **Control source:** the single `[[source]]` with `rc_role = "control"`. RC state is sent only to it. A source with `rc_role = "mirror"` (TX module) never receives GS RC state: `SerialSource.send()` refuses it and the controller never offers it. With no control source, `acquire` is denied.
- **Fixed rate.** One timer at `send_rate_hz` sends the complete channel vector (origin `gs`, 16 channels, `seq` counter modulo 256) while a lease is active, independent of clicks. Clicks only change the core's state. The timer never bursts to catch up.
- **Lease end means silence.** A connection lease needs `rc.heartbeat` at least every 1 s (the GUI sends one every 300 ms); a timed lease ends at its expiry; `rc.release` and the WebSocket closing end it at once. Expiry is checked on every tick and before every operation, so a late heartbeat cannot revive a lease. When a lease ends the core stops sending entirely (the vehicle sees silence and enters its link-loss failsafe) and returns all controls to their defaults. The last state is never kept sending (decision 10.1-1). Sending stops within one period (40 ms at 25 Hz); a release stops it before the next tick.
- **Refused send = skip.** If `Source.send()` returns `False` (not connected, not encodable) or raises, that tick is skipped, nothing is queued up, and the lease stays. While the control source is not connected the controls are reset to defaults every tick, so the vehicle sees the safe state first when the link returns.
- **Momentary controls are held by the core.** `rc.set {"SE": 1, "hold_s": 0.5}` presses for 0.5 s and the core releases it, even if the client is killed. Without `hold_s` a press lapses 300 ms after the last refresh (the GUI repeats `SE=1` every 100 ms while the pointer is down). Setting 0 releases at once. A lease ending releases it. `hold_s` is only valid for momentary controls (0 < s <= 10).
- **Recording.** What was commanded is published on the bus as `RcState` with `src = "gs"`, origin `gs`, on change and at least once per second while sending, and only for frames the control source accepted. It is recorded in logs like any packet and never replayed back out (replay has its own bus). Its `t` is the send time.

## Modes (`RcStatus.mode`)

| Mode | Condition |
|---|---|
| `controlling` | A lease is held and the control source is connected: sending. `controls` and `channels_us` are the state being sent, `origin = gs`. |
| `mirroring` | A handset `RcState` (origin `handset`, from any source, never our own `gs` packets) arrived within 1 s and we are not controlling. Nothing is sent. `controls` are the handset channels mapped to the nearest configured position (sliders continuous), `origin = handset`. This is the flight-test setup: the Pocket's TX module on USB with `rc_role = "mirror"`; the GS only displays what the Pocket sends. |
| `idle` | Neither. Nothing is sent; `controls` shows the defaults. |
| `conflict` | Controlling and a handset state seen within 1 s (vehicle on USB and radio together). Display only: the GS does not change what it sends; the vehicle's authority rules decide. |

## What reaches the vehicle, and what happens on each disconnect path

Only `RcState` (origin `gs`) packets, produced by the controller from the configured mapping, to the one control source, and only while a lease is active and the source is connected. No other packet type can be sent (the serial source refuses non-`UP` types and the provisional profile encodes only `RcState`); there is no raw-send API.

| Event | Result |
|---|---|
| Controlling GUI tab closed, crashed, or loses the network | Connection dropped or heartbeat missing for 1 s: lease ends, silence, defaults. |
| GUI still connected but not heartbeating | Same after 1 s. |
| CLI process exits or crashes | Timed lease continues until its expiry (at most the requested duration, max 600 s), then silence. `exgs rc release` ends it earlier. Its core-held momentary press still releases. |
| Core process killed or crashed | No process, no frames: silence. A restart sends nothing and starts at defaults. |
| Control source unplugged | `send` fails, ticks are skipped, controls reset to defaults; sending resumes with defaults if the lease is still valid when the device returns. |
| Mirror-only setup | No lease can be taken: nothing is ever sent. |
| Client on another host | `control_denied`; view only. |

The vehicle's own failsafes (link timeout, motor interlock, transition-based enabling) are the real protection; the GS only guarantees it does not keep a stale state alive and does not synthesise transitions.

## Notes for the GUI (T9)

Use `rc.acquire {"kind": "connection"}`, heartbeat every 300 ms, `rc.set` for changes, `rc.set {"SE": 1}` repeated every 100 ms while the momentary control is pressed (or `hold_s`), and release on `pointerup`, `pointercancel`, blur and visibility change. Show the `rc` push messages (the state the core sends), never an optimistic local state.
