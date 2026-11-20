"""start.sh: the restart loop (exit code 42 restarts, anything else ends), the config choice and the first-run setup,
with a stub `exgs` in a copied folder. No core, no browser."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def setup_folder(tmp_path, codes):
    d = tmp_path / "gs"
    (d / ".venv/bin").mkdir(parents=True)
    (d / "config").mkdir()
    shutil.copy(ROOT / "start.sh", d / "start.sh")
    (d / "config/gs.example.toml").write_text("")
    counter = d / "runs"
    stub = d / ".venv/bin/exgs"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{d}/args"\n'
        f'n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "{counter}"\n'
        f"codes=({' '.join(map(str, codes))})\n"
        'exit "${codes[$((n-1))]}"\n'
    )
    stub.chmod(0o755)
    return d


def run(d, *args):
    env = {**os.environ, "GS_NO_BROWSER": "1", "GS_NO_MDNS": "1"}
    return subprocess.run(
        ["bash", str(d / "start.sh"), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        stdin=subprocess.DEVNULL,
    )


def test_exit_42_restarts_and_other_codes_end(tmp_path):
    d = setup_folder(tmp_path, [42, 42, 0])
    r = run(d)
    assert r.returncode == 0 and (d / "runs").read_text().strip() == "3"
    assert r.stdout.count("restarting the core") == 2
    d2 = setup_folder(tmp_path / "b", [42, 3, 0])
    r = run(d2)
    assert r.returncode == 3 and (d2 / "runs").read_text().strip() == "2"  # 3 ends the loop


def test_config_choice(tmp_path):
    d = setup_folder(tmp_path, [0])
    run(d)
    assert "--config config/gs.example.toml" in (d / "args").read_text()
    (d / "config/gs.local.toml").write_text("")
    (d / "args").unlink()
    (d / "runs").unlink()
    run(d)
    assert "--config config/gs.local.toml" in (d / "args").read_text()
    (d / "args").unlink()
    (d / "runs").unlink()
    run(d, "/some/other.toml", "--port", "0")
    assert "--config /some/other.toml --port 0" in (d / "args").read_text()
