# Ground station docs

Index and quick start. The authoritative spec is [ground-station-design.md](ground-station-design.md). The other files are split out of it as the code lands (design section 8.3). A file marked "skeleton" is a pointer until its task is implemented.

## Quick start (fake source, no hardware)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'     # Python 3.11+
.venv/bin/exgs core --config config/gs.example.toml            # prints a 1 Hz summary; serves the API on :8765
.venv/bin/exgs schema                                          # packet types as JSON (--markdown for docs)
.venv/bin/pytest && .venv/bin/ruff check src tests tools
```

The example config enables only the fake source, which runs a scripted flight (Startup, SensorInit, Ready, Running with Hover, Ascent, Descent, Landing, Completed). Serial sources (`vehicle_usb`, `link_module`) are configured but disabled in the example: enable them and set `match` for the real device, or point one at the pty fake vehicle (`tools/fake_vehicle_pty.py`, see [wire-profiles.md](wire-profiles.md)).

## Index

| File | Content | Status |
|---|---|---|
| [ground-station-design.md](ground-station-design.md) | Full design and task plan | spec |
| [architecture.md](architecture.md) | Modules, data flow, layers as built | T1 |
| [packet-types.md](packet-types.md) | Packet types and fields | generated |
| [wire-profiles.md](wire-profiles.md) | Wire profile and framer contract | T1 |
| [testing.md](testing.md) | Test layers, fake source scenarios | T1 |
| [log-format.md](log-format.md) | `.exlog` format, recorder, reader, index, library, `exgs log` tools | written (T2) |
| [api.md](api.md) | WebSocket protocol and CLI | T3 |
| [gui.md](gui.md) | GUI shell, panels, adding a panel (3D view and radio: later) | T7 done |
| [radio.md](radio.md) | Virtual radio, leases | skeleton |
| [operations.md](operations.md) | Field setup, udev, systemd | skeleton |
