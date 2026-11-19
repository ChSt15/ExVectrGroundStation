# Virtual radio, leases, safety notes

Spec: [ground-station-design.md](ground-station-design.md) sections 1.4, 5.6, 4.2 and decisions 10.1-1, 10.1-4, 10.1-6 and 10.1-7. Implemented in task T6: `src/exvectr_gs/rc/` (`radio_config.py`, `controller.py`), wired in `core/app.py` (`CoreApp.rc`), exposed by the `rc.*` API operations and `exgs rc ...` (see [api.md](api.md)). The radio widget (T9, `web/app/radio/`, [gui.md](gui.md)) is the GUI side. The lock on guarded controls, the keepalive lease that replaced the timed lease, and master/slave mode (below) are user decisions of 2026-11-19.

**Safety-relevant.** The RC state is the only thing the ground station ever sends to the vehicle. The ground station makes no control, mission or safety decision: it maps control positions to channel values from a config and sends them while somebody holds a lease. It does not know what any switch means (the Starship's motor enable, mission go, and so on); the vehicle firmware interprets the channels with its own fixed mapping (workspace `docs/safety.md`, `docs/system-architecture.md` "Switch mapping").

## Master and slave (user decision 2026-11-19, design 10.1-8)

The user's words: "If the ground station receives an RC packet (from the Pocket), then it goes into slave mode and simply shows the current settings of the Pocket. I can set it to master and then it starts sending the controls to the serial port. The Pocket should always reject them."

- **Slave** is automatic: a handset `RcState` (origin `handset`) arrived within 1 s and nobody holds the lease. The radio widget shows the Pocket's positions read-only and **the core sends nothing**. Nobody needs to do anything.
- **Idle** is the existing state when no handset data arrives and nobody holds the lease: nothing is sent.
- **Master** is the explicit user choice, never automatic. The control lease **is** the master switch: GUI "Switch to master", `exgs rc acquire`, `exgs rc session`, the keepalive lease of a one-shot `exgs rc` command. It is allowed while handset data arrives (the user decides). The rules of master are the old control rules, unchanged: 25 Hz sender, safe defaults on every start and every switch, SD lock, connection / keepalive lease timing, silence when the lease ends. "Release (back to slave)" ends the lease, resets every control to its default and closes every lock.
- **Warning.** In master while the Pocket's state also arrives, `pocket_active` is true and the GUI shows "Pocket also active: the radio rejects ground-station controls". This is display only; the sender does not change. It replaces the old `conflict` mode.
- **Where master sends.** To **every connected source with `rc_role = "control"`** (several are allowed), including the TX-module USB source. A source with `rc_role = "mirror"` (or `none`) is receive-only: `SerialSource.send()` refuses RC state for it and the controller never offers it. The shipped `config/gs.example.toml` sets `control` on both the vehicle USB and the TX-module sources; set `rc_role = "mirror"` on a source to make it receive-only. A source that is not connected is skipped; one source refusing a packet does not stop the others; a source that (re)appears while master is first sent the safe defaults.
- **Who rejects.** The rejection of ground-station `RC_STATE` is done by the **TX module** (it already drops `RC_STATE` arriving from USB towards the air) and later by the vehicle's authority rules. It is **never** done by the ground station: the GS neither filters nor refuses on that ground. Consequence, by user decision: the ground station's old own refusal (never send through the TX module) is no longer a defence layer, so a TX-module bug that failed to drop GS `RC_STATE` would reach the air, where the vehicle's own rules are then the protection.

## Radio config (`config/radio.pocket.toml`)

A config, never code: the vehicle-side channel mapping is fixed in the vehicle firmware and may change, then only this file changes. The core loads it at start (`[radio] config` in `gs.toml`) and the GUI reads it over `config.get radio` or `GET /config/radio.json`.

| Key | Meaning |
|---|---|
| `name`, `mapping_note` | Informational. `mapping_note` should name the vehicle channel table revision this file matches. |
| `send_rate_hz` | Fixed sender rate while a lease is active (1..100, default 25). |
| `channel_count` | 1..16 (the `RcState` packet carries 16 channels). |
| `channel_default_us`, `channel_defaults` | Pulse width of every channel not owned by a control: sticks neutral (1500 us) and, by `channel_defaults = { 3 = 1000 }` (1-based channel), throttle low. |
| `[[controls]]` | `id` (stable id for API/CLI), `kind` (`toggle2`, `switch3`, `momentary`, `slider`), `channel` (1-based), `values_us` (one per position, ascending, lowest first; switches) or `range_us` (slider), `default` (switch index or slider position: the **safe** position), `label`, `meaning`, `svg = {x, y}` (position in the 200 x 140 viewBox of `web/app/radio/pocket.svg`), `lock` (default false), `lock_timeout_s` (default 60). |

Pocket set shipped: SA (toggle2, ch 5), SB (switch3, ch 6), SC (switch3, ch 7), SD (toggle2, ch 8, **`lock = true`**), SE (momentary, ch 9), S1 (slider, ch 10); the sticks are channels 1 to 4 at neutral. `meaning` texts (SA mission go, SB mission type, SC vehicle mode, SD motor enable, SE reset hold, S1 unassigned) are **labels for the operator only**: nothing in the core reads them.

Validation (`RadioConfigError`, the RC controller does not start): unique ids and channels, channels within `channel_count`, all pulse widths within 1000..2000 us, `values_us` ascending with 2 (toggle2, momentary) or 3 (switch3) entries, slider range ascending, defaults in range, a momentary control defaults to released, `lock` a boolean, `lock_timeout_s` within (0, 3600], no unknown keys. If the config is invalid or missing, `CoreApp.rc` is `None` and an error is logged; the rest of the core runs and the API answers `not_implemented` to `rc.*`.

Control values: switches take a position **index** (0 = lowest), sliders 0..1, momentary 0 (released) or 1 (pressed). Anything else is rejected with `invalid_value` and **nothing** of that request is applied.

## Locked controls (user decision 2026-11-19)

A control with `lock = true` in the radio config is a **deliberate-action guard**, not a safety decision. In the shipped Pocket config it is set on SD, which the vehicle interprets as the motor enable (workspace `docs/safety.md` section 3); the vehicle's own 1 s motor gate is unchanged. The ground station does not know what SD means: the lock is a generic per-control option.

- A locked control is forced to, and held at, its default. Setting it to a **non-default value while locked is rejected** (`control_locked` in the API, exit 3 in the CLI) and applies **nothing** of that request. Setting it to its default is always allowed.
- `rc.unlock {control}` needs a held lease. The control is then operable for `lock_timeout_s` (default 60 s), after which it **relocks and returns to its default (off)**. Unlocking again restarts the timer.
- `rc.lock {control}` relocks at once and resets the control to its default.
- **Every end of control relocks and resets:** a lease end (release, heartbeat loss, keepalive expiry, connection drop), a control-source drop, an `acquire`, and a core start all return every control to its defaults and close every lock. Nothing about a lock is persisted.
- The lock state is part of the RC status: `locks: {"SD": {"locked": true, "remaining_s": null, "timeout_s": 60}}` for every lockable control (also while idle, in slave and in `status.rc`). Expiry is applied by the sender tick and before every operation, like lease expiry.
- GUI: shown as a padlock, hold it 1 s to unlock, remaining time and a Relock button ([gui.md](gui.md)). CLI: `exgs rc set SD=1 --unlock SD` (the CLI calls `rc.unlock` first), `exgs rc unlock SD`, `exgs rc lock SD`.

## Safe defaults, no persistence

Every control starts at its configured default (lowest/off, sticks neutral, throttle low) on every core start, on every `acquire`, whenever a lease ends, and whenever the control source drops. Every lock is closed at the same moments. RC state is never written to disk and never resumed: after any end of control the next holder starts from the defaults, and the vehicle sees the safe state first (safety.md section 2, rule 3). The GS never toggles anything by itself.

## Control lease and sender

- **One holder, control hosts only.** `acquire` is denied (`ControlDenied` / `control_denied`) when a lease is already held, when no control source is connected, or when the client address is not in `server.control_hosts`. The API checks the address and the controller checks again (`remote_addr`), so a missing or unknown address is denied.
- **Two lease kinds, both short (user decision 2026-11-19; the 600 s timed lease is gone).** If nobody is actively in control, sending stops within single-digit seconds:

  | Kind | Held by | Kept alive by | Ends |
  |---|---|---|---|
  | `connection` | The GUI, and `exgs rc session` | `rc.heartbeat` at least every 1 s (the GUI and the session send one every 300 ms) | Heartbeat missing for 1 s; the WebSocket closing (crash, exit, closed tab) ends it **at once**; `rc.release` |
  | `keepalive` | One-shot `exgs rc ...` commands (separate short-lived processes) | **Every** lease operation (`rc.set`, `rc.unlock`, `rc.lock`, `rc.heartbeat`), so every `exgs rc` command renews it, and `exgs rc keepalive` renews it without doing anything else | Not renewed for its `idle_s` (default **5 s**, configurable per `rc.acquire` or `--idle`, **max 9 s**); `rc.release`. It does **not** end with the WebSocket of the process that took it |

  A keepalive lease is renewed by operations only, never by reading the status. An `exgs rc set` with no lease held takes a keepalive lease itself (and says so: a new lease starts from the defaults with every lock closed, so a script that pauses longer than the idle time starts over from the safe state). A control-host client without a lease of its own can use and renew the current keepalive lease, so separate `exgs` processes drive one lease. `exgs rc session` is the way to hold control for longer: one process, one connection lease.
- **Source and expiry.** Expiry is checked on every tick **and before every operation**, so a late heartbeat or renewal cannot revive an expired lease (the operation is refused with `no_lease`, the check comes before the renewal).
- **Targets:** every connected `[[source]]` with `rc_role = "control"` (see "Master and slave"). A `mirror` or `none` source never receives GS RC state. With no connected `control` source, `acquire` is denied.
- **Fixed rate.** One timer at `send_rate_hz` sends the complete channel vector (origin `gs`, 16 channels, `seq` counter modulo 256) while a lease is active, independent of clicks. Clicks only change the core's state. The timer never bursts to catch up.
- **Lease end means silence.** When a lease ends the core stops sending entirely (the vehicle sees silence and enters its link-loss failsafe) and returns all controls to their defaults, with every lock closed. The last state is never kept sending (decision 10.1-1). Sending stops within one period (40 ms at 25 Hz); a release stops it before the next tick.
- **Refused send = skip.** If `Source.send()` returns `False` (not connected, not encodable) or raises, that tick is skipped, nothing is queued up, and the lease stays. While the control source is not connected the controls are reset to defaults (and relocked) every tick, so the vehicle sees the safe state first when the link returns.
- **Momentary controls are held by the core.** `rc.set {"SE": 1, "hold_s": 0.5}` presses for 0.5 s and the core releases it, even if the client is killed. Without `hold_s` a press lapses 300 ms after the last refresh (the GUI repeats `SE=1` every 100 ms while the pointer is down). Setting 0 releases at once. A lease ending releases it. `hold_s` is only valid for momentary controls (0 < s <= 10). An `exgs rc set SE=1 --hold S` keeps its keepalive lease renewed while it waits.
- **Recording.** What was commanded is published on the bus as `RcState` with `src = "gs"`, origin `gs`, on change and at least once per second while sending, and only for frames the control source accepted. It is recorded in logs like any packet and never replayed back out (replay has its own bus). Its `t` is the send time.

## Modes (`RcStatus.mode`)

| Mode | Condition |
|---|---|
| `master` | A lease is held and at least one `control` source is connected: sending to all of them (`targets`, `target` is the comma-joined ids). `controls` and `channels_us` are the state being sent, `origin = gs`. `pocket_active` is true while handset state also arrives (warning only). |
| `slave` | A handset `RcState` (origin `handset`, from any source, never our own `gs` packets) arrived within 1 s and we are not master. Nothing is sent. `controls` are the handset channels mapped to the nearest configured position (sliders continuous), `origin = handset`, `pocket_active = true`. This is the flight-test default: the Pocket's TX module on USB; the GS only displays what the Pocket sends. |
| `idle` | Neither. Nothing is sent; `controls` shows the defaults. |

The old modes `controlling`, `mirroring` and `conflict` are gone (renamed or replaced by `pocket_active`).

## What reaches the vehicle, and what happens on each disconnect path

Only `RcState` (origin `gs`) packets, produced by the controller from the configured mapping, to the connected `control` sources, and only while a lease is active (master). No other packet type can be sent (the serial source refuses non-`UP` types and the provisional profile encodes only `RcState`); there is no raw-send API.

| Event | Result |
|---|---|
| Controlling GUI tab closed, crashed, or loses the network | Connection dropped (at once) or heartbeat missing for 1 s: lease ends, silence, defaults, locks closed. |
| GUI still connected but not heartbeating (hidden tab throttled, frozen) | Same after 1 s. |
| `exgs rc session` exits or crashes | Its connection drops: lease ends at once. Frozen without crashing: the heartbeat is missing, lease ends within 1 s (the session then exits with code 3 when it resumes). |
| One-shot `exgs rc ...` process exits | The keepalive lease continues until its idle timeout (default 5 s, at most 9 s) unless the next command renews it. `exgs rc release` ends it at once. Its core-held momentary press still releases on its own. |
| Nobody renews a keepalive lease (script paused, agent crashed) | Silence and defaults after the idle timeout, at most 9 s. |
| Core process killed or crashed | No process, no frames: silence. A restart sends nothing and starts at defaults, every lock closed. |
| Control source unplugged | `send` fails, ticks are skipped, controls reset to defaults and relock; sending resumes with defaults if the lease is still valid when the device returns (with several sources: while any other stays connected they keep receiving, and the returning one is sent the defaults first). |
| Mirror-only setup (every source `mirror` or `none`) | No lease can be taken: nothing is ever sent. |
| One of several `control` sources unplugged | The others keep receiving; when it returns it is first sent the safe defaults. |
| Client on another host | `control_denied`; view only. |

The vehicle's own failsafes (link timeout, motor interlock, transition-based enabling) are the real protection; the GS only guarantees it does not keep a stale state alive and does not synthesise transitions.

## GUI behaviour (T9)

Details in [gui.md](gui.md) ("Virtual radio"). The rules that matter here: the widget shows the state the core reports in its `rc` pushes (labelled "sent"), never an optimistic local state, with a MASTER / SLAVE / IDLE indicator; a client holds control only through `rc.acquire {"kind": "connection"}` and heartbeats every 300 ms; `rc.set {"SE": 1}` repeats every 100 ms while the momentary control is pressed and the widget sends `SE=0` on pointerup, pointercancel, blur and when the page is hidden; a locked control is a padlock that needs a 1 s hold; switching to the replay view while holding control asks for the release first, and a client never holds control while it shows replay data.
