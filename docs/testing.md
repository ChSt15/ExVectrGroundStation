# Testing

Strategy: design section 8.2. Run everything with `.venv/bin/pytest`; lint with `.venv/bin/ruff check src tests tools` and `ruff format --check src tests tools`.

| Layer | Where | What |
|---|---|---|
| Codec round trip | `tests/unit/test_textcodec.py` | Hypothesis: for every registered type `decode_line(encode_line(p)) == p` (NaN, inf, None, extreme ints, unknown enum values, Unicode strings). Extra fields, unknown types, malformed lines. |
| Golden file | `tests/data/golden.exlog` | One line per type, decodes to known values and re-encodes byte-identically. Change it only for a deliberate format change. |
| Registry | `tests/unit/test_registry.py` | Declarations, field specs, schema. |
| State store, history, bus, clock, config | `tests/unit/test_state.py`, `test_core_parts.py` | Freshness with a fake clock, EWMA, preferred source, downsampling. |
| Fake source | `tests/unit/test_fake_source.py` | Rates per profile, determinism, scripted flight, fault injection. |
| Wire, stubs | `tests/unit/test_wire_and_stubs.py` | `fake` profile, stubs raise `NotImplementedError`. |
| API, hub, CLI client | `tests/unit/test_api_protocol.py`, `tests/integration/test_api.py`, `tests/integration/test_cli_client.py` | Real server and WebSocket clients on loopback, FakeSource data, `exgs` subprocesses against a core process. |
| Recorder | `tests/unit/test_recorder.py` | Header/`#type`/`#end` lines, never overwrites (pre-created file, same-second starts), no auto-start, a restart leaves the old file untouched and starts a new one, flush interval, disk-full path (file wrapper raising ENOSPC), queue overflow, crash (a subprocess is killed with SIGKILL: readable, at most about 100 ms lost). |
| Log reader, index, library | `tests/unit/test_logfile.py` | Truncated last line, bad lines, offsets, gzip, keyframes and seek, gaps, markers, history, index of a 1 h synthetic log (about 1 M lines) in under 10 s, `LogLibrary.list()` for complete, crashed and foreign files. |
| `exgs log` | `tests/integration/test_log_cli.py` | validate (ok, incomplete, corrupt, miscount), stats (text, JSON), cat filters, `CoreApp.recorder` does not auto-record. |
| CLI | `tests/integration/test_cli.py` | `exgs core` against the fake source, `exgs schema`, `docs/packet-types.md` is current. |
| GUI | `web/test/*.test.js`, `tests/integration/test_gui.py` | `fmt` (freshness states, units, NaN markers, derived values, quaternion maths), store and view clock, layout validation and presets, reconnect backoff: `node --test web/test`, or `tools/run_js_tests.py` (same files in headless Chromium when node is missing). Static checks of `web/` (panel registration, imports resolve, layout names registered panels, no external resources). Browser smoke test against the core with the fake source: panels show fresh values with source tags, a dropout turns values stale with an age, LIVE/REPLAY switch, phone width without horizontal scroll, record start/stop. Browser tests skip without Chromium or Chrome. |

## Fake source scenarios

`kind = "fake"` options: `profile` (`usb` full set, `radio` reduced set with handset `RcState`), `seed`, `speed` (simulated seconds per wall second), `rates` (per-type override, Hz), `faults`. Fault entries: `{ kind, at, duration, types, scale }` with kinds `dropout` (no packets of `types`), `rate` (scale the rate), `disconnect` (no packets, `LinkStats connected=false`, `GsEvent` down/up), `nan` (one NaN in `Position.pos`), `unknown_enum` (`VehicleState.mode = #9`), `failure_flag` (`fail_sensor` from `at` on). Times are simulation seconds.
