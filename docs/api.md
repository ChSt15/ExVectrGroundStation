# WebSocket protocol and CLI

Implemented in task T3. Spec: [ground-station-design.md](ground-station-design.md) section 4. This file records what is built, including the places where it extends or differs from the design.

Code: `src/exvectr_gs/api/` (`protocol.py` message validation and JSON conversion, `hub.py` clients and subscriptions, `server.py` aiohttp app and operations) and `src/exvectr_gs/cli/`.

## Running

```bash
exgs core --config config/gs.example.toml            # core + API on server.bind:server.port (default 0.0.0.0:8765)
exgs core --port 0                                    # any free port (printed as "api: listening on ...")
exgs core --no-api                                    # sources and state only
```

HTTP routes: `GET /` (the GUI, docs/gui.md), `/static/...` (the `web/` folder), `/config/{radio|layout|model}.json`, `/models/...`, `GET /ws` (WebSocket).

## Conventions

- JSON text messages, one object per message, protocol version `1`. Every message has `op`. A request may carry `id`; the reply is `{"op":"result","id":..,"ok":true,"data":..}` or `{"op":"result","id":..,"ok":false,"error":{"code":..,"message":..}}`. `hello` is answered by `welcome` (carrying the request `id`).
- **Times are float seconds since the Unix epoch** (`t`, `clock`, `since`, `started`, `expires`, `time_ns` packet fields). Durations are float seconds. Logs keep integer ns.
- Packet JSON: `{"type","src","t","f":{field: value}}`. `None` fields are omitted. Enums are names (`"#n"` for a value not in the enum). A NaN or infinite float is sent as `null` with `"nf": {"field": "nan"|"inf"|"-inf"}`; for arrays `nf[field]` is a list with a marker or `null` per element. Packets of unknown type (only from logs) have `"raw": {name: text}`.
- Error codes: `bad_request`, `unknown_type`, `not_found`, `busy`, `control_denied`, `no_lease`, `invalid_value`, `io_error`, **`control_locked`** (a non-default value for a locked control, nothing applied; exit code 3 in the CLI) and **`not_implemented`** (added: the recorder, replayer or RC controller is not present in this core).
- Malformed JSON or an unknown op gives an error result; the connection stays open. The first message must be `hello`.

## Client to core

As in design 4.2. Additions and differences:

| op | Notes |
|---|---|
| `get_snapshot` | Optional filters (extension): `types`, `src`, and `preferred: true` for one item per type chosen by the state store (highest-priority fresh source). Used by `exgs get`. |
| `sources.enable` | The source is named by **`source`** (design: `id`), because `id` is the request id. The Source interface has no run-time enable yet: answers `not_implemented` unless a source provides `set_enabled(bool)` (T5). |
| `rec.*`, `logs.list` | Call `app.recorder` / `app.library` (T2). `rec.start` while recording is `busy`, `rec.stop` while idle is `busy`. |
| `replay.*` | Call `app.replayer` (T4). After `open`, `seek`, `close` and `play` (which restarts from `ended`) the core sends a fresh `snapshot` for the replay view. `replay.play`, `pause` and `seek` with no log open (or still loading) answer `busy`; `open` of an unknown log is `not_found`; `replay.speed` may be set before a log is open. |
| `replay.markers` | Extension (T4): returns `[{t, kind, src, label}]` (`kind` is `event`, `note` or `mode`; `t` in seconds) from the open log's index, for the scrubber markers. `[]` when nothing is open. |
| `rc.*` | Thin pass-through to `app.rc` (T6), see "RC rules" below and `docs/radio.md`. `rc.acquire` takes `kind` `connection` (default) or `keepalive` with `idle_s` (default 5, max 9; the former `timed` kind and `duration_s` are gone). `rc.set` takes an extension `hold_s` (momentary controls only, 0 < s <= 10): the core holds the press for that long and releases it. `rc.unlock {control}` and `rc.lock {control}` open and close a control that has `lock` in the radio config; each returns the RC status. Every operation on a keepalive lease renews it; a pushed `rc` change follows `rc.set`, `rc.unlock` and `rc.lock` at once. |

The server only looks at these optional attributes of the core application object: `recorder`, `library`, `replayer`, `rc`, `replay_bus`, `replay_store`, `replay_history` (`CoreApp` has all of them; `rc` is `None` when the radio config is invalid, the replay ones are filled by the Replayer). A missing attribute, or a module whose methods raise `NotImplementedError`, gives `not_implemented`.

What the modules must provide (contract for T2, T4, T6):

- `recorder.status()` returns T2's `RecorderStatus` (`active, info, last_error, free_bytes, dropped`, with `info.name, started, packets, bytes`); the API flattens it into `status.recorder` (`active, name, started, packets, bytes, free_bytes, error, dropped`). `start()` raises `RuntimeError` if already recording (mapped to `busy`), `OSError` (mapped to `io_error`), `ValueError` (`invalid_value`).
- `replayer.status()` is the `ReplayStatus` of the design. `FileNotFoundError` or `KeyError` from `open` gives `not_found`.
- `rc.acquire` raises `ControlDenied` (mapped to `control_denied`); `ControlLocked` gives `control_locked`; `ValueError` from `acquire`, `set`, `unlock` or `lock` (unknown control, a control without a lock, bad value, bad `hold_s`, bad `idle_s`) gives `invalid_value`. `set`, `heartbeat` with a lease id that no longer exists (expired, released) raise `ControlDenied` (`control_denied`). A connection whose lease ended can acquire again.

## Core to client

`welcome`, `schema`, `snapshot`, `packets`, `status`, `rc`, `event` as in design 4.3. The core sends `welcome`, `schema`, `status` (and `rc` if present) straight after `hello`. `subscribe` is answered by the result, then a `snapshot`.

- `status` is pushed at 2 Hz. Its `rc` key (extension) is the RC status or `null`. Each of `recorder` and `replay` has `available: false` when the module is not present.
- `rc` is pushed on change and at 5 Hz while sending or in slave, with `holder_is_you`. The RC status has `mode` (`master`, `slave` or `idle`, user decision 2026-11-19; the old `controlling`, `mirroring` and `conflict` are gone), `sending`, `target` (ids of the sources sent to, comma separated), `targets` (the same as a list; empty unless master), `pocket_active` (a fresh handset `RcState` is arriving: in master it drives the GUI warning "Pocket also active", display only), `lease` (`id`, `holder`, `kind`, `expires`, `idle_s`), `controls`, `channels_us`, `origin`, `t` and `locks`: `{"SD": {"locked": true, "remaining_s": null, "timeout_s": 60}}` for every lockable control (`remaining_s` is the time until the core relocks it while unlocked).
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
