# Ground station docs

Index and quick start. The authoritative spec is [ground-station-design.md](ground-station-design.md). The other files are split out of it as the code lands (design section 8.3). A file marked "skeleton" is a pointer until its task is implemented.

## Quick start (fake source, no hardware)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'     # Python 3.11+
.venv/bin/exgs core --config config/gs.example.toml            # prints a 1 Hz summary; serves the API on :8765
.venv/bin/exgs schema                                          # packet types as JSON (--markdown for docs)
.venv/bin/pytest && .venv/bin/ruff check src tests tools
```

The example config enables only the fake source, which runs a scripted flight (Startup, SensorInit, Ready, Running with Hover, Ascent, Descent, Landing, Completed). Serial sources (`vehicle_usb`, `link_module`) are configured but disabled in the example (`wire_profile = "exvectrnet-1"`: vehicle USB is peer node 1, the Router on USB is peer node 2): enable them and set `match` for the real device, or point one at the pty fake vehicle (`tools/fake_vehicle_pty.py --profile exvectrnet-1`, see [wire-profiles.md](wire-profiles.md)). The generated packets come from the git submodules: `git submodule update --init` after cloning (the pinned commits are not pushed yet, see wire-profiles.md).

## Index

| File | Content | Status |
|---|---|---|
| [ground-station-design.md](ground-station-design.md) | Full design and task plan | spec |
| [architecture.md](architecture.md) | Modules, data flow, layers as built | T1 |
| [packet-types.md](packet-types.md) | Packet types and fields | generated |
| [wire-profiles.md](wire-profiles.md) | Wire profile and framer contract, `provisional-1`, `exvectrnet-1` (ports, mapping, uplink), fake vehicle | T1, T5, `exvectrnet-1` |
| [testing.md](testing.md) | Test layers, fake source scenarios | T1 |
| [log-format.md](log-format.md) | `.exlog` format, recorder, reader, index, library, `exgs log` tools | written (T2) |
| [api.md](api.md) | WebSocket protocol and CLI | T3 |
| [gui.md](gui.md) | GUI shell, panels, adding a panel, 3D view (config-driven model), virtual radio widget | T7, T8, T9 |
| [requests.md](requests.md) | Whitelisted one-shot REQUESTs (calibration, save, link upload): whitelist, ids and retry, routing, confirmation, API/CLI/GUI, test doubles | request sender (2026-11-19) |
| [telecommands.md](telecommands.md) | **TEMPORARY** legacy telecommand uplink (port 0x7F: arm, simulation toggle, mission, magnetometer calibration): frame layout, rules (one frame, no retry, no ACK), config, API/CLI/GUI, test doubles, what to delete at T9b | third uplink (2026-11-20) |
| [glossary.md](glossary.md) | User-facing names: Router (node 2), ship radio node (node 3), vehicle, Pocket | 2026-11-20 |
| [radio.md](radio.md) | Virtual radio, leases (connection, keepalive), locked controls, safety notes | T6, T9 |
| [operations.md](operations.md) | Field setup, udev, systemd | skeleton |
