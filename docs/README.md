# Ground station docs

Index and quick start. The authoritative spec is [ground-station-design.md](ground-station-design.md). The other files are split out of it as the code lands (design section 8.3). A file marked "skeleton" is a pointer until its task is implemented.

## Quick start (fake source, no hardware)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'     # Python 3.11+
.venv/bin/exgs core --config config/gs.example.toml            # prints a 1 Hz summary
.venv/bin/exgs schema                                          # packet types as JSON (--markdown for docs)
.venv/bin/pytest && .venv/bin/ruff check src tests tools
```

The example config enables only the fake source, which runs a scripted flight (Startup, SensorInit, Ready, Running with Hover, Ascent, Descent, Landing, Completed). Serial sources are configured but disabled until task T5.

## Index

| File | Content | Status |
|---|---|---|
| [ground-station-design.md](ground-station-design.md) | Full design and task plan | spec |
| [architecture.md](architecture.md) | Modules, data flow, layers as built | T1 |
| [packet-types.md](packet-types.md) | Packet types and fields | generated |
| [wire-profiles.md](wire-profiles.md) | Wire profile and framer contract | T1 |
| [testing.md](testing.md) | Test layers, fake source scenarios | T1 |
| [log-format.md](log-format.md) | `.exlog` format (text codec is implemented, files in T2) | skeleton |
| [api.md](api.md) | WebSocket protocol and CLI | skeleton |
| [gui.md](gui.md) | GUI panels, 3D view | skeleton |
| [radio.md](radio.md) | Virtual radio, leases | skeleton |
| [operations.md](operations.md) | Field setup, udev, systemd | skeleton |
