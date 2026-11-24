# Command engine, virtual Pocket, safety notes

Spec: workspace `docs/gs-control-design.md` (the GS-as-control-source design, owner decisions 2026-11-23) and [ground-station-design.md](ground-station-design.md) section 1.4. Implemented in `src/exvectr_gs/rc/`: `command.py` (pure logic), `engine.py` (the sender), `controller.py` (the virtual panel and its leases), `radio_config.py`; wired in `core/app.py` (`CoreApp.engine`, `CoreApp.rc`), exposed by the `rc.*` API operations and `exgs rc ...` (see [api.md](api.md)). The radio widget (`web/app/radio/`, [gui.md](gui.md)) is the GUI side.

**Safety-relevant.** The ground station is now the vehicle's **only control source**. The command state `CMD_STATE` (port 0x13, node 0 to vehicle node 1) is the one thing it sends continuously. It still makes no control, mission or safety decision: the engine copies the operator's switch positions into the command state and applies a pickup rule so that no start, reconnect or input change can produce a safe to unsafe change; the vehicle decides what each field means (workspace `docs/safety.md`, rules marked "Owner decision 2026-11-23"). The old `RC_STATE` uplink, the master/slave lease and the "silence is the failsafe" lease rule are gone.

## Data flow

```
 Pocket --CRSF--> Router (node 2) --USB--> GS core: RcState origin=handset, node 2, 16 channels (never to the air)
                                           |
 GUI virtual panel (lease holder) ---------+--> CommandEngine (in the core, not the GUI)
                                                   CMD_STATE src 0 -> dst 1, 25 Hz + at once on change
                                   +---------------+---------------+
                       Router link source (air)          vehicle USB source (bench)
                       identical frame, same sequence number on every link
```

- One `CommandEngine` per core, started with the core, **independent of GUI clients and leases**. A closed browser tab, a lease end or a CLI exit never stops the stream (it only changes the virtual input). It runs as an asyncio task with a 5 ms loop, no blocking I/O; the `CommandCore` it drives is pure (a fake clock in the tests). It logs a warning when the loop itself ran more than 100 ms late and reports `max_interval_ms`.
- Replay and observers never send: the engine reads the live bus and sends only through the connected live sources.
- **Command links:** every connected source with `rc_role = "control"` (the Router link source and/or the vehicle USB source; `mirror` and `none` never carry commands). The engine builds **one** frame, with its own sequence number, and hands the same packet to every link, so the vehicle dedupes one stream that arrives twice. A source that is not connected is skipped; one that refuses or raises does not stop the others. With no command link connected the engine keeps running and counts `unsent`.
- **Single sender:** the engine is the only sender of `CmdState` in a core, the profile encodes no `RcState` (the `RcState` packet type is receive-only now) and `SerialSource.send` refuses `CmdState` on a non-`control` source, so two senders cannot exist in one core.
- **Session id:** a random non-zero u32 per core start (`new_session()`), kept for the life of the core. The vehicle latches one session and rejects frames of another; a new session is taken over by the vehicle only when it is not committed or in Failure and the old session was silent for 1 s. Every `REQUEST` to the vehicle (target 1) carries it in `REQUEST.session`; the vehicle accepts a request only from its latched session.
- **QoS and framing** (from the generated class): control class, latest-wins, port 0x13, 14 bytes, version 1, flags `VALID` plus `POCKET_LIVE` / `INPUT_VIRTUAL` (only 1, 3, 5, 7 are valid), reserved 0. A field out of range is never sent (the profile refuses to encode it; the vehicle would reject the whole frame).
- **Recording:** the sent `CmdState` is published on the bus on change and at least once per second, so logs hold what was sent (never the vehicle's applied state: that is `CmdApplied`).

## Pocket to command mapping

The Pocket's switches arrive as `RcState` (origin `handset`, from node 2, `SOURCE_OK`) on channels 5 to 9, decoded with the bands of the old vehicle decoder (wire value <= -500 low, -200..200 middle, >= 500 high, i.e. 1250 us / 1400..1600 us / 1750 us; anything else, and an unprovided channel, is invalid). Sticks (channels 1 to 4) and S1 are displayed only.

| Switch | Ch | Command field | Safe value | Meaning (decided by the vehicle) |
|---|---|---|---|---|
| SA | 5 | `go` NOT_GO / GO | NOT_GO | up = go (mission begin edge request), down = abort |
| SB | 6 | `profile` 0, 1, 2 | - | mission profile; always follows the switch; the profile at session start is the first valid SB, else 2 |
| SC | 7 | `vehicle_mode` REAL 0 / SIMULATION 1 | - | Pocket position 0 = REAL, positions 1 and 2 = SIMULATION (1 <-> 2 is no change) |
| SD | 8 | `motors` DISABLED / ENABLED | DISABLED | **motors armed / disarmed** (up = armed) |
| SE | 9 | `reset_button` RELEASED / PRESSED | RELEASED | short press = reset system state, hold >= 1 s = zero estimators while held (the vehicle classifies); a press is stretched to at least 160 ms (4 frames) |
| GS FAILSAFE button | - | `failsafe` 0 / 1 | 0 | `engine.set_failsafe(True)` (see below) |

An invalid channel decodes to the safe value (SA NOT_GO, SD DISABLED, SE RELEASED); an invalid SB or SC holds the current output.

## The pickup rule (D-GS4)

Per switch the engine keeps a `picked up` bit. The input is the Pocket or the virtual panel (below).

1. **Session start:** the output is go NOT_GO, motors DISABLED, reset RELEASED, failsafe 0, profile as above, `vehicle_mode` = the vehicle's reported applied mode (`VEHICLE_STATUS.simulation`), REAL if nothing is reported within 2 s. Nothing is sent until the mode is known or the 2 s are over. Every pickup is false.
2. **Every input sample:** SA, SD, SE: an input at the safe value is applied at once (and counts as picked up). An input in the unsafe direction is applied only if the input has been seen at the value being sent (picked up); an input equal to the output is picked up. SC and SB always follow the switch, also at session start and after a Pocket reconnect (no pickup). SC is applied only after a settle of `MODE_SETTLE_NS` (0.3 s, at least 3 frames at 25 Hz) from the first frame: the vehicle boots REAL and takes a mode change only as an observed change between consecutive accepted frames, so the first frames carry the start mode (REAL, or the mode the vehicle reports) and SIMULATION follows if SC says so. SB is read by the vehicle only at the go edge.
3. **Pocket lost** (no valid handset `RcState` for 300 ms): a **WARNING** event only (`GsEvent` link_warning, shown as a banner and toast), no abort and no failsafe, the output is held and the engine **keeps sending its last command state**, with one exception: **SE is forced RELEASED** while no input exists (a held SE would keep the vehicle zeroing). Every pickup is forgotten.
4. **Pocket back** (3 consecutive valid frames): rule 2 applies. SA released sends the abort at once, SD down disarms at once; SA or SD up while the output shows NOT_GO or DISABLED does nothing until the switch is cycled; SC and SB follow the switch at once. The display shows what the operator must do ("SA is up while go is off: flip it down first").
5. **Input change:** see below. The virtual panel is preset to the output when it becomes the input (no output change).
6. **Buttons** (the failsafe level, later also abort and motors off) act on the output regardless of the input and clear the affected pickups, so the input must be cycled before it can undo them.

Consequence: no start, reconnect or input change can produce a safe to unsafe change of the output. The vehicle also resets its transition detectors on a session change, so a buggy GS would need a second observed edge. `tests/unit/test_command_core.py` checks this with an independent oracle over every start combination of SA, SD and SC and random sequences of loss, return, failsafe and samples.

### Failsafe level and the reset after it

`engine.set_failsafe(True)` latches `failsafe = 1` with motors DISABLED and go NOT_GO in the same frame and holds it until `set_failsafe(False)`; the switches cannot undo it and each affected switch must be cycled afterwards. (The header FAILSAFE button calls `engine.failsafe`; see "GS ABORT and FAILSAFE buttons".) The vehicle refuses an SE reset while the failsafe request is still 1 (owner decision Q6), so when an SE press is taken while the failsafe is latched the engine **clears the failsafe field first, sends that frame, and sends the press in a later frame**.

### GS ABORT and FAILSAFE buttons, Command panel

**Safety-relevant, GS commands via `CMD_STATE`, no confirmation beyond the hold.**

- **ABORT** (`engine.abort()`, `CommandCore.abort`): go NOT_GO at once and the go pickup is cleared, so the next mission begin needs go (SA) seen down and then up again. An SA left up (Pocket or virtual) stays ignored. It never touches motors, failsafe or any other field and works with the Pocket live, the virtual input or none. It only moves toward safe (checked by the independent oracle test, which includes abort steps). Logs a `GsEvent` and a notification "ABORT sent".
- **FAILSAFE** (`engine.set_failsafe(True)`): unchanged latch (failsafe 1, motors DISABLED, go NOT_GO in the same frame). Clearing (`set_failsafe(False)`) keeps the safe sequence: failsafe goes to 0 in a frame before any SE reset press; go and motors still need a cycle. Notifications "FAILSAFE requested" / "GS failsafe request cleared".
- Both are API ops (`engine.abort`, `engine.failsafe {on}`), control hosts only, no lease: the buttons call the core, so the engine acts even if the tab closes right after. The GUI holds each for 500 ms (progress fill, release cancels, no dialog). They are disabled only while the core is offline, there is no engine or the client is view only, and the header says why.
- **Command panel** (first Operations tab): Mission begin, Abort, Arm (hold 1 s) / Disarm motors, Mode real / simulation, Reset system state, Zero estimators (SE held while pressed, refreshed every 100 ms). They set the VIRTUAL panel (`rc.acquire` if needed, `rc.set`, `rc.unlock SD` for arming) exactly like the virtual switches, so the same pickup rule applies; a button that would be ignored for lack of a pickup is disabled with the reason ("press Abort first"). The panel shows what the engine sends (`rc.command.out`), not the click, next to the vehicle's CMD_APPLIED. While the Pocket is live it says "Pocket in control" and all buttons except Abort are disabled. Known behaviour of the virtual panel: the SD guard times out like the virtual SD switch (back to disarmed).

## Input selection: the Pocket wins, the virtual panel replaces it

- Input = **Pocket** while its stream is live. Otherwise, **virtual** while a client holds the virtual panel lease. Otherwise **none** (the output is held, SE released).
- The `INPUT_VIRTUAL` flag is set while the virtual panel is the input, `POCKET_LIVE` while the Pocket stream is live. Both are information only; the vehicle reports them and never acts on them.
- A client that takes the lease starts the panel at what is being sent (`RcController.preset`, called from `acquire` through the engine): taking the panel changes nothing that is sent, and an edit made right after is a deliberate change. When the Pocket is lost while the panel is held, the panel becomes the input the same way (preset to the output). When the Pocket returns it takes over with the reconnect rule.
- SD stays behind its lock (`lock = true`), which guards ARMING only: the panel cannot arm the motors without a deliberate unlock (1 s padlock hold, Command panel Arm hold, or `exgs rc ... --unlock SD`). Disarming (SD to 0) is always immediate, locked or not. An unlock has NO timeout and no timer ever changes SD (an earlier 60 s auto-relock disarmed motors in flight, 52-60 s after arming: removed). It stays open until the user locks it (`rc.lock` / Lock button: SD back to disarmed, guard closed) or a reset event closes it (`relock_all`: core start, SE system reset, GS failsafe request, lease end; SD returns to disarmed on the panel). `locks[id]` reports `{locked, armed}`; `lock_timeout_s` is still accepted in old configs and ignored. A panel preset with SD armed opens the guard. A lease end, heartbeat loss or closed tab returns the PANEL to its defaults but does not disarm: the engine has no virtual input then and holds its last output (D-GS5, test `test_armed_virtual_sd_stays_armed_...`).

## Virtual panel and leases (`controller.py`)

Leases only decide **who may move the virtual switches** (D-GS5). The rules of the old controller that remain: control hosts only (`server.control_hosts`), one holder, a `connection` lease (GUI, `exgs rc session`) needs a heartbeat at least every 1 s (the GUI sends one every 300 ms; the WebSocket closing ends it at once), a `keepalive` lease (one-shot CLI commands) ends when it was not renewed for its idle time (default 5 s, max 9 s), expiry is checked before every operation, every lease end returns the panel to its defaults and closes every lock, a momentary control (SE) is held by the core (`hold_s` or 300 ms after its last refresh), the SD lock and the validation (`invalid_value`, `control_locked`, nothing of a bad request applied). What changed: no source needs to be connected to take the panel, and a lease end **does not stop or change what is sent**: the engine holds its last command state (a closed tab with the motors armed leaves them armed; so does a Pocket loss, by the owner's decision).

`RcStatus.mode`: `virtual` (a lease holds the panel), `pocket` (no lease and a handset `RcState` arrived within 1 s: the Pocket's positions are shown), `idle`. `controls` is the panel state (virtual), the Pocket's positions (pocket) or the defaults. `sending`, `target`, `targets` of the `rc` push come from the engine.

## Radio config (`config/radio.pocket.toml`)

A config, never code: the layout and defaults of the virtual panel. The engine reads the Pocket on the fixed channels 5 to 9 itself (`rc/command.py` `CHANNELS`). `send_rate_hz` is kept for compatibility and no longer used (the engine's rate is fixed at 25 Hz).

| Key | Meaning |
|---|---|
| `name`, `mapping_note` | Informational. |
| `send_rate_hz` | Unused since the cutover (valid range 1..100 kept). |
| `channel_count` | 1..16 (the `RcState` packet carries 16 channels). |
| `channel_default_us`, `channel_defaults` | Pulse width of every channel not owned by a control (display of the panel's channel vector). |
| `[[controls]]` | `id` (stable id for API/CLI), `kind` (`toggle2`, `switch3`, `momentary`, `slider`), `channel` (1-based), `values_us` (one per position, ascending, lowest first; switches) or `range_us` (slider), `default` (switch index or slider position: the **safe** position), `label`, `meaning`, `svg = {x, y}`, `lock` (default false), `lock_timeout_s` (default 60). |
| `[sticks]` | Display only: which channels the two gimbals show. |

Pocket set shipped: SA (toggle2, ch 5), SB (switch3, ch 6), SC (switch3, ch 7), SD (toggle2, ch 8, `lock = true`), SE (momentary, ch 9), S1 (slider, ch 10); sticks on channels 1 to 4. The `meaning` texts are labels for the operator only. Validation (`RadioConfigError`) is unchanged: unique ids and channels, channels within `channel_count`, pulse widths within 1000..2000 us, ascending `values_us`, defaults in range, a momentary control defaults to released, `lock_timeout_s` within (0, 3600], no unknown keys. If the config is invalid or missing, `CoreApp.rc` is `None` (the API answers `not_implemented` to `rc.*`) and the engine still runs with the Pocket as its only input.

Control values: switches take a position **index** (0 = lowest), sliders 0..1, momentary 0 or 1. Anything else is rejected with `invalid_value` and nothing of that request is applied.

## What reaches the vehicle, and what happens on each path

Through the engine only `CmdState` packets, to the connected `control` sources. Besides it the ground station sends whitelisted `REQUEST`s ([requests.md](requests.md), now with the session); the legacy telecommands are retired ([telecommands.md](telecommands.md)). No other packet type can be sent (the serial source refuses non-`UP` types; the profile encodes only these; there is no raw-send API).

| Event | Result |
|---|---|
| GUI tab closed, crashed or lost; a lease ends; `exgs rc` exits | The panel lease ends (panel back to defaults). The command stream is unaffected: the output is held. |
| Pocket stream lost (Router on USB, Pocket off or unplugged) | WARNING only. The stream goes on with the last state, SE released. On return the pickup rule applies. |
| Command link unplugged or disabled by the user | No frame reaches it. The engine goes on (same session, same output); the other link carries the stream if there is one. A link that returns just receives the current frames. |
| Core killed or crashed, PC dead, Router USB unplugged | No command: the vehicle's own protection applies (motors off after 1 s, latched failsafe after 2 s while committed). **Accepted by the owner (2026-11-23).** |
| Core restart | New session, safe output, nothing picked up: a switch left up must be cycled. The vehicle takes the new session only when not committed or in Failure and the old one was silent for 1 s. |
| Second ground station | The vehicle keeps the first session and reports `MULTI_MASTER`; the other core shows NOT MASTER (display only: it keeps sending, the vehicle rejects it). |
| Malformed field | Never sent (the profile refuses it). |

The vehicle's own checks (observed edges, preconditions, 1 s motor gate, 2 s control timeout) are the protection; the GS guarantees only that it does not synthesise transitions.

## What the vehicle and the Router must provide

The vehicle: `CMD_STATE` ingress for node 0 on USB and UART with the session latch and per-session sequence, `CMD_APPLIED` (0x84, USB) and `VEHICLE_STATUS` with `cmd_session`, `ctrl_source` GS, `ctrl_flags` USB_FRESH / UART_FRESH / MULTI_MASTER / GS_FAILSAFE_REQ and `failure_bits` bit 14. The Router: forward port 0x13 from USB to the air as a control-class latest-wins frame (src 0, dst 1 only) and keep the Pocket `RC_STATE` on its USB (profile flag `pocket_rc_to_air` off). See workspace `docs/gs-control-design.md` section 7.

## Stick display (GUI, display only)

The radio panel shows the two sticks of the Pocket: what arrives from the handset when it is live, the channel defaults otherwise (**the ground station sends no stick values and this display adds none**), in replay the recorded `RcState`. The optional `[sticks]` table of the radio config (mode 1 or 2, distinct channels) is validated by `rc/radio_config.py` and never used for sending; `web/app/radio/sticks.js` maps pulse widths to -1..1 and 0..1.

## Source disabled by the user

`sources.enable {enabled: false}` closes the source's port: it is no longer `connected`, so the engine skips it like any dropped link (nothing is received or sent through it). When it is enabled again it is a new connection with an empty queue and receives the engine's current frames (same session, same output). Tested in `tests/integration/test_source_enable.py`.
