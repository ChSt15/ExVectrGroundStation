# Architecture (as built)

Spec: [ground-station-design.md](ground-station-design.md) sections 1-3 and 6. This file records what exists in code.

## Safety boundary

The ground station is a thin client: it records, displays and sends RC channel state (under the control lease) and its own 1 Hz presence heartbeat (information only, independent of the lease). It makes no control, mission or safety decision (design section 1.4, `CLAUDE.md` review rule). The `FakeSource` is a test fixture that produces telemetry. It cannot be connected to a vehicle and implements no vehicle logic.

## Layers

```
Source (run/send/status) -> Framer -> WireProfile.decode / decode_many -> Packet(s) -> Bus -> StateStore / History (later: Recorder, Hub)
FakeSource ---------------------------------------------> Packet -^
```

| Layer | Module | Implemented |
|---|---|---|
| Packet model, registry, `@packet`, `f()` | `exvectr_gs.packets` (`base`, `fields`, `registry`) | T1 |
| Text codec (`GenericTextCodec`, `encode_line`, `decode_line`) | `exvectr_gs.packets.textcodec` | T1 |
| Type declarations (14 types of design 3.2, plus the `exvectrnet-1` types: `VehicleStatus`, `Actuators`, `HandsetTelem`, `ImuRaw`, `Estimator`, `TaskTiming`, `TxModuleStats`, `RxModuleStats`, `RequestAck`, `NetworkStatus`) | `exvectr_gs.packets.defs` (`vehicle`, `link`, `host`) | T1 |
| Bus, StateStore, History, clocks, config, app wiring | `exvectr_gs.core` | T1 |
| Frame, Framer, WireProfile protocols, `load_profile`, `fake` profile | `exvectr_gs.wire` | T1 |
| Source ABC, `FakeSource`, scripted scenario | `exvectr_gs.sources` | T1 |
| Recorder, LogReader, LogIndex, LogLibrary | `exvectr_gs.recording` | implemented (T2) |
| ApiServer, Hub, protocol helpers | `exvectr_gs.api` | implemented (T3), see [api.md](api.md) |
| Replayer | `exvectr_gs.replay` | implemented (T4) |
| `SerialSource`, `provisional-1` profile, `tools/fake_vehicle_pty.py` | `exvectr_gs.sources.serial_source`, `exvectr_gs.wire.provisional_1` | implemented (T5), see [wire-profiles.md](wire-profiles.md) |
| `exvectrnet-1` profile (real ExVectrNetwork wire format), fake vehicle `exvectrnet-1` mode | `exvectr_gs.wire.exvectrnet_1`, `tools/fake_vehicle_net.py` | implemented, see [wire-profiles.md](wire-profiles.md) |
| Data-rate statistics (`RateTracker`, paths; fed per frame by `SerialSource`, per packet by `CoreApp` for the fake source and by `Replayer.read_rates` in replay) | `exvectr_gs.core.rates` | implemented (`feature/data-rates`), see [api.md](api.md) |
| Data-rate statistics (`RateTracker`, paths; fed per frame by `SerialSource`, per packet by `CoreApp` for the fake source, `Replayer.read_rates` in replay) | `exvectr_gs.core.rates` | implemented (`feature/data-rates`), [api.md](api.md) |
| RcController, radio config | `exvectr_gs.rc` | implemented (T6), `docs/radio.md` |
| RequestController (whitelisted REQUEST sender, second uplink) | `exvectr_gs.requests` | implemented (`feature/request-sender`), `docs/requests.md` |

## Notes on the implementation

- Packets are `kw_only` dataclasses. `t` (host receive time, ns) and `src` are ordinary fields, `extra` keeps unknown fields of newer logs as raw text. Equality is NaN-aware. `@packet` attaches `Cls.PTYPE` (the `PacketType`) and registers into `REGISTRY` (a private registry can be passed for tests).
- Field kinds are inferred from annotations (`float`, `int`, `bool`, `str`, `list[float]`, `list[int]`, `IntEnum`). `time_ns` is chosen with `f(kind="time_ns")`. Array length comes from `length=` or `len(labels)`.
- Enums decode unknown numbers without failing: the packet holds a plain `int`, the text form is `#<n>`.
- The state store judges freshness against a view clock: `get(now=...)`, else the injected clock (the live core passes its host clock), else the newest packet time (replay).
- `Bus.publish` is synchronous. A failing handler is logged and counted (`handler_errors`) and does not stop other handlers.
- Config (`core/config.py`) rejects unknown keys. `recorder.raw_frames` accepts only `"unknown"`. There is no recording auto-start option (design decision 10.1-3).
- Recording (`recording/`): `Recorder` subscribes to the bus only while a recording is active and hands packets to a writer thread through a bounded queue (overflow is counted in `status().dropped`, never blocks the bus). The writer encodes lines, writes `#type` before the first packet of each type, flushes every `flush_interval_ms`, fsyncs every `fsync_interval_ms`, and writes `#end` on stop. A write error (disk full) stops that recording, sets `last_error` and calls `on_error` from the writer thread. Nothing starts a recording except an explicit `start()`: `CoreApp` creates the recorder but opens no file.
- `LogReader`/`LogIndex`/`LogLibrary` (`recording/logfile.py`) work on bytes and line offsets. A last line without a newline is truncated and ignored everywhere. `build_index()` returns `times`/`offsets` (`array('q')`, running maximum so they are sorted), a keyframe per second of log time (a gap gets one keyframe), history series (`history=True` types, at most 20 Hz) and markers (events, notes, vehicle mode changes).
- `exgs log validate|stats|cat` live in `cli/cmd_log.py` (auto-discovered like the other `cmd_*.py` modules).
- Heartbeat: `CoreApp._heartbeat_loop` is a separate 1 Hz task that calls `Source.send_heartbeat` on every source (`SerialSource` implements it for every `rc_role`; the default is `False`). It is not part of the RcController and does not read leases or RC state; see `docs/wire-profiles.md` ("Heartbeat").
- `CoreApp` falls back to the `fake` wire profile when `provisional-1` is not available and no non-fake source is enabled.
- `FakeSource.step(sim_t)` is a pure, deterministic generator (no clock, no I/O). `run()` only paces it. Faults (`faults = [...]` in the source config): `dropout`, `rate`, `disconnect`, `nan`, `unknown_enum`, `failure_flag`.
- Replay (`replay/replayer.py`): not a Source. `CoreApp` builds `replay_bus`, `replay_store`, `replay_history` and `replayer`; the Replayer is the only publisher on `replay_bus`, so replayed data never reaches the live bus or the recorder, and a recording continues during replay. `open()` builds the log index in a worker thread (`state = loading`), preloads the whole log's history series (`History.load`, no retention window) and seeks to `t_start`, paused. `seek(t)` decodes the keyframe's per-(type, src) latest lines, applies the lines up to `t` in file order and resets the replay store; it publishes nothing (the API then sends a `snapshot`). The state after a seek equals sequential replay, as far as the latest packet per (type, src) goes. `Entry.count` and `interval_s` are not reconstructed (they restart from the keyframe), so staleness uses the nominal rate until a few packets have been replayed. Playback: a task steps a replay clock every 10 ms of wall time (a stall of more than 0.5 s is not caught up) at `speed` and publishes every line with index time <= clock, updating the replay store first. After the last line the state is `ended`; `play` from `ended` restarts at `t_start`, a seek before the end pauses.
- `exvectrnet-1` depends on the generated `exvectr_packets` module from the git submodule `lib/ExVectrPackets` (not copied; `pyproject.toml` pytest path and `wire/exvectrnet_1/_deps.py`) and on `lib/ExVectrNetwork` for the test vectors. Layering inside the profile: `framing.py` (bytes to network/transport frames) -> `profile.py` (presence, version check, duplicates, port to generated class) -> `mapping.py` (generated class to GS packets). A profile may offer `decode_many` (one wire packet to several GS packets, or none for a suppressed duplicate); `wire.decode_frame` is the one call `SerialSource` makes. The profile is stateful per source id (presence, sequence numbers), so one profile instance serves all sources of a core.
- `CoreApp` also falls back to the `fake` profile when a profile's module cannot be imported (submodule not checked out) and no serial source is enabled.
