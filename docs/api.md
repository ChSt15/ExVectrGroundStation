# WebSocket protocol and CLI

Implemented in task T3. Spec: [ground-station-design.md](ground-station-design.md) section 4. This file records what is built, including the places where it extends or differs from the design.

Code: `src/exvectr_gs/api/` (`protocol.py` message validation and JSON conversion, `hub.py` clients and subscriptions, `server.py` aiohttp app and operations) and `src/exvectr_gs/cli/`.

## Running

```bash
exgs core --config config/gs.example.toml            # core + API on server.bind:server.port (default 0.0.0.0:8765)
exgs core --port 0                                    # any free port (printed as "api: listening on ...")
exgs core --no-api                                    # sources and state only
```

HTTP routes: `GET /` (GUI, currently a raw message stream, replaced by T7), `/static/...` (the `web/` folder), `/config/{radio|layout|model}.json`, `/models/...`, `GET /ws` (WebSocket).

## Conventions

- JSON text messages, one object per message, protocol version `1`. Every message has `op`. A request may carry `id`; the reply is `{"op":"result","id":..,"ok":true,"data":..}` or `{"op":"result","id":..,"ok":false,"error":{"code":..,"message":..}}`. `hello` is answered by `welcome` (carrying the request `id`).
- **Times are float seconds since the Unix epoch** (`t`, `clock`, `since`, `started`, `expires`, `time_ns` packet fields). Durations are float seconds. Logs keep integer ns.
- Packet JSON: `{"type","src","t","f":{field: value}}`. `None` fields are omitted. Enums are names (`"#n"` for a value not in the enum). A NaN or infinite float is sent as `null` with `"nf": {"field": "nan"|"inf"|"-inf"}`; for arrays `nf[field]` is a list with a marker or `null` per element. Packets of unknown type (only from logs) have `"raw": {name: text}`.
- Error codes: `bad_request`, `unknown_type`, `not_found`, `busy`, `control_denied`, `no_lease`, `invalid_value`, `io_error`, and **`not_implemented`** (added: the recorder, replayer or RC controller is not present in this core).
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
| `rc.*` | Thin pass-through to `app.rc` (T6), see "RC rules" below. |

The server only looks at these optional attributes of the core application object: `recorder`, `library`, `replayer`, `rc`, `replay_bus`, `replay_store`, `replay_history` (`CoreApp` has all of them except `rc`; the replay ones are filled by the Replayer). A missing attribute, or a module whose methods raise `NotImplementedError`, gives `not_implemented`.

What the modules must provide (contract for T2, T4, T6):

- `recorder.status()` returns an object with attributes `active, name, started (ns), packets, bytes, free_bytes, error`. `start()` raises `RuntimeError` if already recording (mapped to `busy`), `OSError` (mapped to `io_error`), `ValueError` (`invalid_value`).
- `replayer.status()` is the `ReplayStatus` of the design. `FileNotFoundError` or `KeyError` from `open` gives `not_found`.
- `rc.acquire` raises `ControlDenied` (mapped to `control_denied`); `ValueError` or `KeyError` from `set` gives `invalid_value`.

## Core to client

`welcome`, `schema`, `snapshot`, `packets`, `status`, `rc`, `event` as in design 4.3. The core sends `welcome`, `schema`, `status` (and `rc` if present) straight after `hello`. `subscribe` is answered by the result, then a `snapshot`.

- `status` is pushed at 2 Hz. Its `rc` key (extension) is the RC status or `null`. Each of `recorder` and `replay` has `available: false` when the module is not present.
- `rc` is pushed on change and at 5 Hz while sending or mirroring, with `holder_is_you`.
- `event` is sent when a source changes state (and via `ApiServer.event(level, text)` for other modules).

### Backpressure (design 2.3)

- The bus handler only stores each packet in the interested clients' pending structures.
- `latest` subscriptions keep the newest packet per (type, source) and flush at `max_rate_hz` (0.1 to 500, default 30), with `iv` and `stale_after`.
- `all` subscriptions use a deque of 20000 packets per client and view; the oldest is dropped and counted in `dropped`.
- Each client has its own writer task. Control messages are queued (bound 2000); a client that exceeds it, or accepts nothing for 15 s, is disconnected. A slow client never blocks the event loop.

## RC rules enforced by the API layer

The API never computes or sends channel values itself; it passes requests to the RC controller (T6). It adds these checks:

1. **Control hosts only (decision 10.1-4).** `rc.acquire` is refused with `control_denied` unless the peer address is in `server.control_hosts` (IPs, CIDR networks or `localhost`; IPv4-mapped IPv6 addresses are normalised). `welcome.can_control` reports the result. The controller must check the address too (`remote_addr` is passed).
2. **Stop when the controlling client disappears (decision 10.1-1).** When a client's WebSocket closes, the core calls `rc.release` for its *connection* lease. Timed leases (CLI) end by expiry in the controller.
3. **Cross-origin pages are refused.** A WebSocket upgrade with an `Origin` header that does not match `Host` gets 403. Otherwise a web page in the operator's browser could reach the core over loopback and count as a control host. Clients without `Origin` (the CLI) are unaffected.
4. Timed lease length is validated (`duration_s` in (0, 600]). A control host client without a lease of its own can use the *current timed lease* (`rc.set`, `rc.release`, `rc.heartbeat`), so that separate short-lived `exgs` processes can drive one lease. `no_lease` otherwise.

## CLI

All client commands accept `--host` (default `127.0.0.1`, env `EXGS_HOST`), `--port` (default 8765, env `EXGS_PORT`) and `--json`, placed after the command. Default output is human text (the log line format for packets). With `--json` every result is **one JSON object per line on stdout** with `"ok": true`; an error is `{"ok": false, "error": {"code", "message"}}` on stdout (nothing on stderr). Without `--json`, errors go to stderr as `exgs: message`.

Exit codes: **0** ok, **1** error (including usage errors and "core not running"), **2** timeout, **3** control denied (`control_denied`, `no_lease`).

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
| `exgs rc acquire --duration S` / `rc release` / `rc show` / `rc set CTRL=V ... [--hold S]` | Timed lease and controls. `--hold S` sets, waits, then restores the values the core reported before (client side; if the CLI is killed meanwhile the value stays until the lease ends). |

Each command group is its own module `cli/cmd_<name>.py` with a `register(subparsers)` function; modules are discovered automatically, so new groups (`exgs log ...` offline tools) are added without touching `cli/main.py`.

Agent bench example (works as soon as T2 and T6 are merged; today `rec`, `rc` answer `not_implemented`):

```bash
exgs status --json
exgs wait 'VehicleState.mode == Ready' --timeout 20 --json
exgs rec start --label agent-bench
exgs rc acquire --duration 120
exgs rc set SA=0
exgs rc set SA=1
exgs wait 'VehicleState.armed == true' --timeout 5 || echo "not armed (exit $?)"
exgs rc release
exgs rec stop
```
