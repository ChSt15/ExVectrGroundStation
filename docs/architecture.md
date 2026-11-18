# Architecture (as built)

Spec: [ground-station-design.md](ground-station-design.md) sections 1-3 and 6. This file records what exists in code.

## Safety boundary

The ground station is a thin client: it records, displays and sends RC channel state. It makes no control, mission or safety decision (design section 1.4, `CLAUDE.md` review rule). The `FakeSource` is a test fixture that produces telemetry. It cannot be connected to a vehicle and implements no vehicle logic.

## Layers

```
Source (run/send/status) -> Framer -> WireProfile.decode -> Packet -> Bus -> StateStore / History (later: Recorder, Hub)
FakeSource ---------------------------------------------> Packet -^
```

| Layer | Module | Implemented |
|---|---|---|
| Packet model, registry, `@packet`, `f()` | `exvectr_gs.packets` (`base`, `fields`, `registry`) | T1 |
| Text codec (`GenericTextCodec`, `encode_line`, `decode_line`) | `exvectr_gs.packets.textcodec` | T1 |
| Type declarations (14 types of design 3.2) | `exvectr_gs.packets.defs` (`vehicle`, `link`, `host`) | T1 |
| Bus, StateStore, History, clocks, config, app wiring | `exvectr_gs.core` | T1 |
| Frame, Framer, WireProfile protocols, `load_profile`, `fake` profile | `exvectr_gs.wire` | T1 |
| Source ABC, `FakeSource`, scripted scenario | `exvectr_gs.sources` | T1 |
| ApiServer, Hub | `exvectr_gs.api` | stub (T3) |
| Recorder, LogReader, LogIndex, LogLibrary | `exvectr_gs.recording` | T2 |
| Replayer | `exvectr_gs.replay` | stub (T4) |
| Serial source, `provisional-1` profile | `exvectr_gs.sources`, `exvectr_gs.wire` | T5 |
| RcController, radio config | `exvectr_gs.rc` | stub (T6) |

## Notes on the implementation

- Packets are `kw_only` dataclasses. `t` (host receive time, ns) and `src` are ordinary fields, `extra` keeps unknown fields of newer logs as raw text. Equality is NaN-aware. `@packet` attaches `Cls.PTYPE` (the `PacketType`) and registers into `REGISTRY` (a private registry can be passed for tests).
- Field kinds are inferred from annotations (`float`, `int`, `bool`, `str`, `list[float]`, `list[int]`, `IntEnum`). `time_ns` is chosen with `f(kind="time_ns")`. Array length comes from `length=` or `len(labels)`.
- Enums decode unknown numbers without failing: the packet holds a plain `int`, the text form is `#<n>`.
- The state store judges freshness against a view clock: `get(now=...)`, else the injected clock (the live core passes its host clock), else the newest packet time (replay).
- `Bus.publish` is synchronous. A failing handler is logged and counted (`handler_errors`) and does not stop other handlers.
- Config (`core/config.py`) rejects unknown keys. `recorder.raw_frames` accepts only `"unknown"`. There is no recording auto-start option (design decision 10.1-3).
- Recording (`recording/`): `Recorder` subscribes to the bus only while a recording is active and hands packets to a writer thread through a bounded queue (overflow is counted in `status().dropped`, never blocks the bus). The writer encodes lines, writes `#type` before the first packet of each type, flushes every `flush_interval_ms`, fsyncs every `fsync_interval_ms`, and writes `#end` on stop. A write error (disk full) stops that recording, sets `last_error` and calls `on_error` from the writer thread. Nothing starts a recording except an explicit `start()`: `CoreApp` creates the recorder but opens no file.
- `LogReader`/`LogIndex`/`LogLibrary` (`recording/logfile.py`) work on bytes and line offsets. A last line without a newline is truncated and ignored everywhere. `build_index()` returns `times`/`offsets` (`array('q')`, running maximum so they are sorted), a keyframe per second of log time (a gap gets one keyframe), history series (`history=True` types, at most 20 Hz) and markers (events, notes, vehicle mode changes).
- `exgs log validate|stats|cat` live in `cli/logcmd.py` (own module, registered with one line in `cli/main.py`).
- `CoreApp` falls back to the `fake` wire profile when `provisional-1` is not available and no non-fake source is enabled.
- `FakeSource.step(sim_t)` is a pure, deterministic generator (no clock, no I/O). `run()` only paces it. Faults (`faults = [...]` in the source config): `dropout`, `rate`, `disconnect`, `nan`, `unknown_enum`, `failure_flag`.
