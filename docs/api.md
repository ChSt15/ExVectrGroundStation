# WebSocket protocol and CLI

Implemented in task T3. Spec: [ground-station-design.md](ground-station-design.md) section 4. This file records what is built, including the places where it extends or differs from the design.

Code: `src/exvectr_gs/api/` (`protocol.py` message validation and JSON conversion, `hub.py` clients and subscriptions, `server.py` aiohttp app and operations) and `src/exvectr_gs/cli/`.

## Running

```bash
exgs core --config config/gs.example.toml            # core + API on server.bind:server.port (default 0.0.0.0:8765)
exgs core --port 0                                    # any free port (printed as "api: listening on ...")
exgs core --no-api                                    # sources and state only
```

HTTP routes: `GET /` (the GUI, docs/gui.md), `/static/...` (the `web/` folder), `/config/{radio|layout|model|callouts}.json`, `/models/...`, `GET /ws` (WebSocket).

## Conventions

- JSON text messages, one object per message, protocol version `1`. Every message has `op`. A request may carry `id`; the reply is `{"op":"result","id":..,"ok":true,"data":..}` or `{"op":"result","id":..,"ok":false,"error":{"code":..,"message":..}}`. `hello` is answered by `welcome` (carrying the request `id`).
- **Times are float seconds since the Unix epoch** (`t`, `clock`, `since`, `started`, `expires`, `time_ns` packet fields). Durations are float seconds. Logs keep integer ns.
- Packet JSON: `{"type","src","t","f":{field: value}}`. `None` fields are omitted. Enums are names (`"#n"` for a value not in the enum). A NaN or infinite float is sent as `null` with `"nf": {"field": "nan"|"inf"|"-inf"}`; for arrays `nf[field]` is a list with a marker or `null` per element. Packets of unknown type (only from logs) have `"raw": {name: text}`.
- Error codes: `bad_request`, `unknown_type`, `not_found`, `busy`, `control_denied`, `no_lease`, `invalid_value`, `io_error`, **`control_locked`** (a non-default value for a locked control, nothing applied; exit code 3 in the CLI) and **`not_implemented`** (added: the recorder, replayer or RC controller is not present in this core) and the request sender's codes (`request.send` only, exit code 1 in the CLI unless noted): **`not_whitelisted`** (action, or action with that target, is not in the whitelist, or the target is ambiguous), **`confirm_required`** (`confirm` missing or not the action name, or `understand_link_stops` missing for a `danger` action), **`rate_limited`** (more than one new request per 500 ms from this client host), **`request_in_flight`** (the same action and target still waits for its ACK) and **`no_route`** (no connected source can reach the target, nothing was sent or queued). `control_denied` (exit 3) also covers a `request.send` from a view-only host.
- Malformed JSON or an unknown op gives an error result; the connection stays open. The first message must be `hello`.

## Client to core

As in design 4.2. Additions and differences:

| op | Notes |
|---|---|
| `get_snapshot` | Optional filters (extension): `types`, `src`, and `preferred: true` for one item per type chosen by the state store (per field, the highest-priority source with a fresh value, see [field-sources.md](field-sources.md): an item gets `fs: {field: {src, t, stale_after}}` for every field that came from a source other than the item's `src`, and `exgs get Type.field` reports that field's own source and age). Every snapshot also carries `priority` (the configured `source_priority`) so clients merge the same way. Recording and replay are unchanged (per-source packets). |
| `telecommand.send` / `telecommand.list` | **TEMPORARY** legacy telecommands (user decision 2026-11-20, [telecommands.md](telecommands.md)). **Safety-relevant.** `telecommand.send {name, confirm, param?}`: control hosts only (checked here and in the controller), `confirm` must equal the whitelist name, `param` only for entries with a range; **one frame, no retry, no ACK**; replies with the record (`n`, `name`, `type`, `subsystem`, `param_int`, `client`, `source`, `t`), no state. `telecommand.list` replies `{commands: [{name, type, label, group, subsystem, param_int, param_min, param_max, confirm, note, route}]}`. Errors: `control_denied`, `not_whitelisted`, `confirm_required`, `rate_limited`, `no_route`, `invalid_value`. Open to every client (list). |
| `request.send` / `request.list` / `request.status` | Whitelisted one-shot REQUESTs (user decision 2026-11-19, [requests.md](requests.md)). **Safety-relevant.** `request.send {action, target?, confirm, understand_link_stops?}`: control hosts only (checked here and again in the controller), `confirm` must equal the action name, `danger` entries also need `understand_link_stops: true`; it sends the first attempt at once and replies with the request state (`req_id`, `action`, `target`, `state` = `sending`, `attempts`, `max_attempts`, `source`, ...). `request.list` replies `{actions: [{action, target, target_name, label, group, confirm, warning, route}]}`, `request.status` replies `{inflight, recent}` (state `sending`, `acked` with `status`, `status_name`, `detail`, or `timeout`, from ACKs only). Open to every client. |
| `sources.enable` | `{source, enabled}` (the source is named by **`source`**, because `id` is the request id). **Safety-relevant (it changes what reaches the vehicle), control hosts only** (`control_denied` otherwise), `not_found` for an unknown source. Implemented by `CoreApp.set_source_enabled` (user request 2026-11-20, a testing feature): **disable is a true disconnect**: the source task is cancelled, which closes the serial port (a port still opening is closed as soon as the open returns), so nothing is received and nothing is sent through it (no RC state, REQUEST, telecommand or heartbeat: every sender checks `state == "connected"`); `status.request_routes` and the telecommand route drop it at once (sends without another route are refused with `no_route`); every REQUEST still in flight is **aborted** (state `aborted` with `abort_reason`, no further attempt through any source); a `GsEvent` `source_down` "`<id>`: DISABLED by user (...)" is recorded. **Enable** starts the source from scratch (a new connection with an empty send queue, nothing buffered is flushed; the RC controller sends the safe defaults first to a control source that reappears, and with no lease nothing is sent at all), recorded as `source_up` "`<id>`: enabled by user". Replies with the source status, which has `disabled_by`: `"user"`, `"config"` (`enabled = false` in the config: such sources are built and start disabled) or `null`. Not persisted: a core restart uses the config. |
| `rec.*`, `logs.list` | Call `app.recorder` / `app.library` (T2). `rec.start` while recording is `busy`, `rec.stop` while idle is `busy`. |
| `replay.*` | Call `app.replayer` (T4). After `open`, `seek`, `close` and `play` (which restarts from `ended`) the core sends a fresh `snapshot` for the replay view. `replay.play`, `pause` and `seek` with no log open (or still loading) answer `busy`; `open` of an unknown log is `not_found`; `replay.speed` may be set before a log is open. |
| `get_rates` | Extension (user request 2026-11-20), read-only, open to every client. `{view?: "live" (default) | "replay"}`. Per-stream data rates, see "Data rates" below. |
| `get_history` | Extension `buffer`: `"trail"` (default, the types with `history=True`, at most 20 Hz, 600 s live; the 3D trail) or `"plot"` (the tuning graphs, docs/gui.md: full rate up to 200 Hz of `PLOT_TYPES` in `core/history.py`, the last 130 s live from `CoreApp.plot_history`; in the replay view it is read from the open log file in a worker thread, at most 600 s before `t1`, empty when no log is open). Other values: `invalid_value`. |
| `replay.markers` | Extension (T4): returns `[{t, kind, src, label}]` (`kind` is `event`, `note` or `mode`; `t` in seconds) from the open log's index, for the scrubber markers. `[]` when nothing is open. |
| `core.quit` | Stop the core cleanly (recording closed, RC lease released and sender stopped, sources closed, API stopped), then exit with code 0; `start.sh` does not restart on 0. Control hosts only (`control_denied`), needs `confirm: true` (`invalid_value`). Logs the requesting client. Not a vehicle uplink, sends nothing to the vehicle. Replies `{ok, message}`, the core exits about 1.5 s later. |
| `update.status` / `update.check` / `update.apply` | Self-update ([updating.md](updating.md)). `update.status` is read-only and open to every client. `update.check` (fetch and compare, up to 20 new commits, `deps_changed`, `submodules_changed`) and `update.apply {confirm: true}` are control hosts only (`control_denied`); `apply` without `confirm: true` is `invalid_value`. Refusals (guards, dirty tree, wrong branch, detached HEAD, non-fast-forward) are `update_refused`, a failed git step `update_failed`, a running check/apply `busy`, disabled or no git checkout `not_implemented`. A successful apply replies with the steps and `restart: true`, then the core exits with code 42. |
| `engine.abort`, `engine.failsafe {on: bool}` | GS ABORT and FAILSAFE request / clear, straight to the command engine (no lease, control hosts only: `control_denied` otherwise; `not_implemented` without an engine). Reply: the `rc` body with `command` (`abort_count` counts aborts). See `docs/radio.md`. |
| `rc.*` | Thin pass-through to `app.rc` (T6), see "RC rules" below and `docs/radio.md`. `rc.acquire` takes `kind` `connection` (default) or `keepalive` with `idle_s` (default 5, max 9; the former `timed` kind and `duration_s` are gone). `rc.set` takes an extension `hold_s` (momentary controls only, 0 < s <= 10): the core holds the press for that long and releases it. `rc.unlock {control}` and `rc.lock {control}` open and close a control that has `lock` in the radio config; each returns the RC status. Every operation on a keepalive lease renews it; a pushed `rc` change follows `rc.set`, `rc.unlock` and `rc.lock` at once. |

The server only looks at these optional attributes of the core application object: `plot_history`, `recorder`, `library`, `replayer`, `rc`, `requests`, `replay_bus`, `replay_store`, `replay_history` (`CoreApp` has all of them; `rc` is `None` when the radio config is invalid, the replay ones are filled by the Replayer). A missing attribute, or a module whose methods raise `NotImplementedError`, gives `not_implemented`.

What the modules must provide (contract for T2, T4, T6):

- `recorder.status()` returns T2's `RecorderStatus` (`active, info, last_error, free_bytes, dropped`, with `info.name, started, packets, bytes`); the API flattens it into `status.recorder` (`active, name, started, packets, bytes, free_bytes, error, dropped`). `start()` raises `RuntimeError` if already recording (mapped to `busy`), `OSError` (mapped to `io_error`), `ValueError` (`invalid_value`).
- `replayer.status()` is the `ReplayStatus` of the design. `FileNotFoundError` or `KeyError` from `open` gives `not_found`.
- `rc.acquire` raises `ControlDenied` (mapped to `control_denied`); `ControlLocked` gives `control_locked`; `ValueError` from `acquire`, `set`, `unlock` or `lock` (unknown control, a control without a lock, bad value, bad `hold_s`, bad `idle_s`) gives `invalid_value`. `set`, `heartbeat` with a lease id that no longer exists (expired, released) raise `ControlDenied` (`control_denied`). A connection whose lease ended can acquire again.

## Data rates (`get_rates`)

Code: `core/rates.py` (`RateTracker`, path rules), `SerialSource._observe`, `Replayer.read_rates`; the GUI is docs/gui.md "Data rates". Nothing is recorded: the statistics are in memory only.

Reply: `{view, now, windows_s: [2, 10], measure, streams, paths, air}`.

- `streams[]`: `{source, type, node, path, nominal_hz, age_s, count, rate_hz: [2 s, 10 s], bytes_s: [..] | null, payload_s: [..] | null}`, sorted by path (`air`, `usb direct`, `router local`, `simulated`, `unknown`), then wire bytes/s. `type` is the wire packet name (`VEHICLE_STATUS`, `HEARTBEAT`, `port 0x7F` for an unknown port) for serial sources, the ground-station packet type for packet-level streams. `node` is the origin node (null for packet-level streams). `nominal_hz` is the schema's `rate_air` (air path) or `rate_usb` (other paths), null when none. `age_s` is the time since the last frame, in the view's own time.
- **Path** (`air`, `usb direct`, `router local`): vehicle USB source: always `usb direct`. Router source: frames from node 1 or 3 crossed the air (`air`); frames from node 2 and every RC_STATE (the Pocket's copy) are `router local`; any other node is `unknown`. The rules live in the wire profile (`describe_frame`, optional) and `core/rates.py` (`frame_path`).
- **Measure**: `live` is `"frames"`: every valid wire frame a serial source receives is counted on receive (duplicates the profile drops included, they were on the wire) at the host receive time. `bytes_s` is the **wire size** as received (COBS, delimiter, SKIND, CRC, network header) when the profile reports it (`Frame.wire_len`, `exvectrnet-1` does), else the payload size; `payload_s` is always the payload. The fake source has no frames: it is counted per packet (`path` `simulated`, `bytes_s` null). `replay` is `"packets"`: computed from the open log lines of the last 10 s before the replay position (stateless, so a seek needs no reset; nothing when no log is open), `bytes_s` null, path from the log's `#source` kind and the type (a Router log: `TxModuleStats`, `RadioLinkStats`, `RcState`, `NetworkStatus` are `router local`, the rest `air`).
- Windows are averaged over the time actually observed when the core or replay position is younger than the window (never shorter than 0.5 s).
- `paths[]`: `{path, streams, rate_hz: [..], bytes_s: [..] | null, payload_s: [..] | null}`, the sums per path.
- `air` (**per direction**: air profile v1 is time-division with a 1:1 split, 25 air packets/s of 253 payload bytes in EACH direction, about 6.1 kB/s each, both at the same time): `{capacity_bytes_s, payload_s, utilisation, packets_max_s: 25, down: {...}, up: {...}}`. `capacity_bytes_s`, `payload_s` and `utilisation` at the top level are the **downlink** figures (compatibility). `down` = `{capacity_bytes_s, payload_s: [..] | null, utilisation: [..] | null, packets_s: number | null, estimated: false}`: payload bytes/s of the air path (frames from node 1 or 3 received through the Router, measured) against `[rates] air_capacity_down_bytes_s`; `packets_s` is the measured air packet rate, the Router's `air_rx_ok` counter (`TxModuleStats`) differenced over 10 s (null without recent stats). `up` = `{capacity_bytes_s, payload_s, utilisation, packets_s, estimated: true, items: [{type, hz | null, payload_s: [..], source}] | null}` against `[rates] air_capacity_up_bytes_s`: **an ESTIMATE**, the ground station cannot see uplink bytes. It sums the known uplink types at their nominal air rates and payload sizes (from the wire profile's `uplink_air_items()`: `RC_STATE` at 25 Hz, only counted while RC state is flowing, that is the Pocket's copies arriving or the ground station sending RC; `LINK_CONFIG` at its schema rate; the Router heartbeat has schema air rate 0 and is not counted) plus the frames this ground station queued towards a Router (`REQUEST`, `Telecommand`, measured at send over each window; a ground-station `RC_STATE` takes the same 25 Hz slot and is never added twice). `up.packets_s` is the ship radio node's `air_rx_ok` (`RxModuleStats`) rate. `up.payload_s` and `items` are null when the profile has no uplink items (fake profile) and in the replay view. `[rates] air_capacity_bytes_s` is kept as an alias that sets whichever direction is not configured. The Router's own air packet rates and LQ are also the `RadioLinkStats` packet (`up_hz`, `down_hz`, `lq_pct`).
- `LinkStats.rx_frames_s` / `rx_bytes_s` keep their meaning: the serial hop only.

## Core to client

Self-update pushes: `update` (after hello and on change: `{enabled, busy, restart_pending, checked_at, behind, error}`, the header badge) and `update_progress` (`{step, text}` while `update.apply` runs). New error codes `update_refused` and `update_failed`.

`welcome`, `schema`, `snapshot`, `packets`, `status`, `rc`, `event` as in design 4.3. The core sends `welcome`, `schema`, `status` (and `rc` if present) straight after `hello`. `subscribe` is answered by the result, then a `snapshot`.

- `status` is pushed at 2 Hz. Its `rc` key (extension) is the RC status or `null`. Each of `recorder` and `replay` has `available: false` when the module is not present.
- `rc` is pushed on change and at 5 Hz while sending or while the panel is held or the Pocket shows, with `holder_is_you`. The status is the **virtual panel** plus the **command engine's report**. `mode`: `virtual` (a lease holds the panel), `pocket` (no lease, a handset `RcState` arrived within 1 s) or `idle`. `sending`, `target` (ids of the command links, comma separated) and `targets` (list) are the engine's (it sends without any lease). `command` is the engine report: `session` (u32), `session16`, `vehicle_session16` (from `VEHICLE_STATUS.cmd_session`), `master` (`ok`, `not_master`, `unknown`), `multi_master`, `gs_failsafe_req`, `usb_fresh`, `uart_fresh` (vehicle-reported `ctrl_flags`), `input` (`pocket`, `virtual`, `none`), `pocket` (`absent`, `live`, `lost`), `pocket_age_s`, `ready`, `links`, `sending`, `sent`, `unsent`, `last_sent_t`, `max_interval_ms`, `failsafe_latched`, `out` (the sent `go`, `profile`, `vehicle_mode`, `motors`, `reset_button`, `failsafe`), `hints` (`sa`, `sd`, `se` waiting for a cycle, `sc` = the mode to flip to, or null) and `picked`. Also in the `rc.set` / `rc.unlock` / `rc.lock` replies. `event` messages may carry `toast` (a key): the Pocket loss / return warning. `pocket_active` (a fresh handset `RcState` is arriving, display only), `lease` (`id`, `holder`, `kind`, `expires`, `idle_s`), `controls`, `channels_us`, `origin`, `t` and `locks`: `{"SD": {"locked": true, "remaining_s": null, "timeout_s": 60}}` for every lockable control (`remaining_s` is the time until the core relocks it while unlocked).
- `telecommands` (extension, TEMPORARY) is pushed to every client after hello and after every send: `{recent: [...], route}` (the sends of this run, newest first; no results exist). `status` also carries `telecommand_route`.
- `requests` (extension) is pushed to every client after hello and on every change of a request (send, retry, ACK, timeout): `{inflight: [...], recent: [...]}`. `status` also carries `request_routes` (target node id to the id of the source a request would use now, or `null`).
- `event` is sent when a source changes state (and via `ApiServer.event(level, text)` for other modules).

### Backpressure (design 2.3)

- The bus handler only stores each packet in the interested clients' pending structures.
- `latest` subscriptions keep the newest packet per (type, source) and flush at `max_rate_hz` (0.1 to 500, default 30), with `iv` and `stale_after`.
- `all` subscriptions use a deque of 20000 packets per client and view; the oldest is dropped and counted in `dropped`.
- Each client has its own writer task. Control messages are queued (bound 2000); a client that exceeds it, or accepts nothing for 15 s, is disconnected. A slow client never blocks the event loop.

## RC rules enforced by the API layer

The API never computes or sends channel values itself; it passes requests to the RC controller (T6). It adds these checks:

1. **Control hosts only (decision 10.1-4).** `rc.acquire` is refused with `control_denied` unless the peer address is in `server.control_hosts` (IPs, CIDR networks or `localhost`; IPv4-mapped IPv6 addresses are normalised). `welcome.can_control` reports the result. The controller must check the address too (`remote_addr` is passed).
2. **Stop when the controlling client disappears (decision 10.1-1).** When a client's WebSocket closes, the core calls `rc.release` for its *connection* lease, which ends it at once. *Keepalive* leases (one-shot CLI commands) are not tied to the connection: they end when not renewed within their idle timeout (default 5 s, max 9 s).
3. **Cross-origin pages are refused.** A WebSocket upgrade with an `Origin` header that does not match `Host` gets 403. Otherwise a web page in the operator's browser could reach the core over loopback and count as a control host. Clients without `Origin` (the CLI) are unaffected.
4. Keepalive idle time is validated (`idle_s` in (0, 9]). A control host client without a lease of its own can use, and thereby renew, the *current keepalive lease* (`rc.set`, `rc.unlock`, `rc.lock`, `rc.release`, `rc.heartbeat`), so that separate short-lived `exgs` processes can drive one lease. `no_lease` otherwise.

## CLI

All client commands accept `--host` (default `127.0.0.1`, env `EXGS_HOST`), `--port` (default 8765, env `EXGS_PORT`) and `--json`, placed after the command. Default output is human text (the log line format for packets). With `--json` every result is **one JSON object per line on stdout** with `"ok": true`; an error is `{"ok": false, "error": {"code", "message"}}` on stdout (nothing on stderr). Without `--json`, errors go to stderr as `exgs: message`.

Exit codes: **0** ok, **1** error (including usage errors and "core not running"), **2** timeout, **3** control denied (`control_denied`, `no_lease`, `control_locked`).

| Command | Behaviour |
|---|---|
| `exgs core [--config] [--port] [--no-api] [--duration]` | Run the core. |
| `exgs schema [--markdown]` | Offline: packet types. |
| `exgs status` | Sources, recorder, replay, RC, clients. |
| `exgs watch [--types A,B] [--src S] [--view live\|replay] [--count N] [--duration S] [--max-rate HZ]` | Stream packets in order (`all` mode). Dropped packets are reported as `# dropped N` (JSON: `{"dropped": N}`). If `--count` is given but not reached within `--duration`: exit 2. |
| `exgs get Type[.field] [--src S] [--view V]` | Latest value with `age_s` and freshness (`fresh`, `stale`, `not supplied`). Nothing received: error `missing`, exit 1. |
| `exgs wait 'Type.field OP value' [--timeout S] [--src S] [--view V]` | Block until the condition holds on vehicle-reported data (default timeout 10 s, exit 2 on timeout). OP: `== != < > <= >=`. Values: numbers, `true`/`false`, enum names, an array index as in `Position.pos[2] > 1.5`. A stale value (older than its `stale_after`) at the start does not count. A field that is not supplied never satisfies. |
| `exgs rec start [--label L]` / `rec stop` / `rec note TEXT` | Recording (manual only). |
| `exgs logs` | List recordings. |
| `exgs replay open NAME` / `play` / `pause` / `seek T` / `speed X` / `close` | `T` is seconds from the log start (the CLI adds the log's `t_start`). |
| `exgs sources list` / `enable ID` / `disable ID` | Source status. |
| `exgs request list` / `request status` / `request send ACTION [--target N] --confirm ACTION [--i-understand-the-link-stops] [--no-wait]` | Whitelisted REQUESTs ([requests.md](requests.md)). **Safety-relevant.** `send` refuses without `--confirm ACTION` (the action name repeated, no default) and, for a `danger` action (`LINK_UPLOAD_MODE`), without `--i-understand-the-link-stops`; it then waits for the ACK (about 5 s at most) and prints the status exactly as reported. Exit 0 for `DONE`/`IN_PROGRESS`, 1 for any other status or error, 2 for a timeout (no ACK after 5 attempts), 3 control denied. |
| `exgs update status` / `check` / `apply --confirm` / `--local [--dry-run]` | Self-update ([updating.md](updating.md)). `apply` refuses without `--confirm`; `--local` works without a core (same refusals, no guards, no restart). |
| `exgs rc acquire [--idle S]` / `rc keepalive` / `rc release` / `rc show` | One-shot commands hold a **keepalive lease** (idle timeout 5 s, max 9 s). `acquire` takes it explicitly; `rc keepalive` renews it; every other rc command renews it too; `rc release` ends it at once; expiry means silence and defaults. `rc show` lists the lock state (`locks: SD=locked`, or `SD=UNLOCKED(42s)`). |
| `exgs rc set CTRL=V ... [--hold S] [--unlock CTRL] [--idle S]` | Takes the keepalive lease itself when none is held (and says so: a new lease starts from the defaults and every lock closed) and renews it otherwise. A non-default value for a locked control needs `--unlock CTRL` (the control must be one of those being set; the CLI calls `rc.unlock` first and locks it again if the request then fails). `--hold S`: a momentary control (for example `SE=1 --hold 0.5`) is pressed with `hold_s`, so the **core** releases it (a killed CLI cannot leave it pressed); any other control is set, then restored by the CLI to the value the core reported before (if the CLI is killed meanwhile that value stays until the lease ends). The CLI reads the control kinds from `config.get radio` and keeps the lease renewed while it waits. |
| `exgs rc unlock CTRL` / `rc lock CTRL` | Unlock a locked control (the core relocks it after `lock_timeout_s`, or when the lease ends) or lock it again (it returns to its default). |
| `exgs rc session [--script FILE] [--keep-going]` | One long-running process holding a **connection lease** with a 300 ms heartbeat. Commands, one per line (`#` comments): `set CTRL=V ... [--hold S] [--unlock CTRL]`, `unlock CTRL`, `lock CTRL`, `sleep S`, `show`, `release`, `quit`, from stdin or `--script FILE`; end of input releases and exits. A failing command stops the session with its exit code unless `--keep-going`. Crash or exit ends the lease at once (the connection drops), a frozen process within 1 s (heartbeat loss); if the lease is lost the session exits with code 3. |

Each command group is its own module `cli/cmd_<name>.py` with a `register(subparsers)` function; modules are discovered automatically, so new groups (`exgs log ...` offline tools) are added without touching `cli/main.py`.

Agent bench example (needs a connected control source; with none, `rc acquire` exits 3):

```bash
exgs status --json
exgs wait 'VehicleState.mode == Ready' --timeout 20 --json
exgs rec start --label agent-bench
exgs rc set SA=0                       # takes a keepalive lease (5 s idle); every rc command renews it
exgs rc set SA=1
# for a longer sequence use one process: exgs rc session --script bench.txt
exgs wait 'VehicleState.armed == true' --timeout 5 || echo "not armed (exit $?)"
exgs rc release
exgs rec stop
```
