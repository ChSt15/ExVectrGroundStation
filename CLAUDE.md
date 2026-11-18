# ExVectr ground station

Python core (asyncio, headless) plus a browser GUI that records, visualises and replays data from the model Starship, and carries the virtual radio (RC channel state to the vehicle). Part of the ExVectrStarship workspace; this folder is its **own git repository**. Workspace-level rules live one folder up (`../CLAUDE.md`, `../docs/safety.md`, `../docs/system-architecture.md`).

**The spec is `docs/ground-station-design.md`** (interfaces, packet model, log format, API, GUI, task plan in section 9). Follow its names, signatures and layout. User decisions in its section 10.1 are binding and override anything else in the doc. Read only the sections your task needs, plus section 1 (safety).

## Safety rule (review rule)

The ground station **never makes control, mission or safety decisions** for the vehicle. It records, displays, and sends RC channel state under the control-lease rules in the design (section 1.4). A change that makes the GS compute a command, gate an action on vehicle state, send anything other than `UP` types through the RC sender, or persist RC state is a **safety change and needs user approval**. Consequences in code:

- Panels and the API show vehicle-reported values only. No optimistic UI, nothing inferred from sent commands.
- The only uplink is the RC state (`rc/`). No raw-send API. No legacy one-shot telecommands.
- RC state is never persisted. After a start every control is at its safe default and nothing is sent until a client takes the control lease. When the lease holder disappears the core stops sending (decision 10.1-1).
- No simulator, autopilot, arm macro or command sequencing in this repo.

## Layout and layers

`src/exvectr_gs/`: `packets/` (registry, text codec, type declarations) , `core/` (bus, state store, history, clock, config, app wiring), `wire/` (Frame, Framer, WireProfile protocols and profile plug-ins), `sources/` (Source, FakeSource, later serial), `recording/`, `replay/`, `rc/`, `api/`, `cli/`. Frontend in `web/` (plain ES modules, vendored pinned libraries, no build step, must work offline). Full tree: design section 8.1.

Keep the layers strict so each can be replaced later: source -> framer / wire profile -> packet registry -> codec -> bus / state store -> API -> panels. **Wire-format knowledge lives only in a wire profile plug-in.** Everything above it sees semantic `Packet` objects. Logs are semantic text and stay readable after the wire format changes.

Status: T1 to T5 and T7 done (packet model and fake source; recorder, log reader/index/library and the offline `exgs log` tools; API server and read-side CLI; replayer; serial source with the temporary `provisional-1` wire profile and the pty fake vehicle; GUI shell and data panels, `docs/gui.md`). Unimplemented interfaces (RcController) are stubs that raise `NotImplementedError` until their task lands.

## Setup and commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'     # Python 3.11+
.venv/bin/pytest                                               # all tests
.venv/bin/ruff check src tests tools && .venv/bin/ruff format --check src tests tools
.venv/bin/exgs core --config config/gs.example.toml            # fake source, 1 Hz summary (Ctrl-C to stop)
.venv/bin/python tools/run_js_tests.py                         # GUI unit tests (node --test, or headless Chromium if node is missing)
.venv/bin/exgs status --json                                   # client commands talk to the running core (docs/api.md)
.venv/bin/exgs schema --markdown > docs/packet-types.md        # regenerate after changing packet types
```

Run ruff only on `src tests tools` (it would also reformat code blocks in the Markdown docs). Runtime dependencies: `aiohttp`, `pyserial`, nothing else. No hardware is needed for development: use the `FakeSource` (`kind = "fake"`).

## Conventions

- SI units, time as `int` nanoseconds (JSON to clients uses float seconds). World frame NWU, quaternion `[w,x,y,z]` body -> world (workspace `CLAUDE.md`).
- Packet types are declared once in `packets/defs/`. Every field is optional (`None` = not supplied). Adding a field is backward compatible. Removing, renaming or changing a unit increments `version`.
- Docs rule: docs in `docs/` are versioned with the code and updated in the **same branch**. `docs/packet-types.md` is generated and a test checks it is current.
- Tests live with the code: `tests/unit`, `tests/integration`. Run the full suite and ruff before reporting a task done.

## Git workflow

`master` (released), `Development` (integration), `feature/<name>` cut from `Development`. Merge the latest `Development` into the feature branch and re-test before merging back. Never push or merge into `Development` or `master` without the user's approval.
