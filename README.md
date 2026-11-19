# ExVectr ground station

Records, visualises and replays data from the ExVectr model Starship and carries the virtual radio (RC channel state under lease rules) and a whitelisted REQUEST sender for one-shot actions such as calibration (`exgs request`, Requests panel; every send needs a deliberate confirmation, see [docs/requests.md](docs/requests.md)). Python core plus browser GUI. See [docs/README.md](docs/README.md) for the index and quick start, and [CLAUDE.md](CLAUDE.md) for the project rules.

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/exgs core --config config/gs.example.toml
```
