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
| CLI | `tests/integration/test_cli.py` | `exgs core` against the fake source, `exgs schema`, `docs/packet-types.md` is current. |

## Fake source scenarios

`kind = "fake"` options: `profile` (`usb` full set, `radio` reduced set with handset `RcState`), `seed`, `speed` (simulated seconds per wall second), `rates` (per-type override, Hz), `faults`. Fault entries: `{ kind, at, duration, types, scale }` with kinds `dropout` (no packets of `types`), `rate` (scale the rate), `disconnect` (no packets, `LinkStats connected=false`, `GsEvent` down/up), `nan` (one NaN in `Position.pos`), `unknown_enum` (`VehicleState.mode = #9`), `failure_flag` (`fail_sensor` from `at` on). Times are simulation seconds.
