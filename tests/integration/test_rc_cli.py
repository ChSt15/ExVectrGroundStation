"""`exgs rc ...` against a real core process whose fake source is the control link with the test echo."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

CONFIG = """
[core]
source_priority = ["fake"]
[server]
bind = "127.0.0.1"
control_hosts = ["127.0.0.1", "::1"]
[recorder]
directory = "{rec}"
[[source]]
id = "fake"
kind = "fake"
rc_role = "control"
echo_rc = true
[radio]
config = "config/radio.pocket.toml"
"""


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    cfg = tmp_path_factory.mktemp("cfg") / "gs.toml"
    cfg.write_text(CONFIG.format(rec=tmp_path_factory.mktemp("rec")))
    p = subprocess.Popen(
        [sys.executable, "-m", "exvectr_gs.cli.main", "core", "--config", str(cfg), "--port", "0", "--duration", "300"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    port = None
    for line in p.stdout:
        if line.startswith("api: listening on"):
            port = int(line.rsplit(":", 1)[1])
            break
    assert port, "core did not start"
    yield port
    p.terminate()
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()
    p.stdout.close()


def exgs(port, *args, timeout=30):
    return subprocess.run(
        [sys.executable, "-m", "exvectr_gs.cli.main", *args, "--port", str(port)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def show(port):
    r = exgs(port, "rc", "show", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.splitlines()[0])


def test_agent_bench_sequence_and_hold(core):
    assert show(core)["mode"] == "idle" and show(core)["lease"] is None
    assert exgs(core, "rc", "set", "SA=1").returncode == 3  # no lease: denied
    r = exgs(core, "rc", "acquire", "--duration", "20", "--json")
    assert r.returncode == 0, r.stderr
    assert exgs(core, "rc", "acquire", "--duration", "5").returncode == 3  # held by the first process
    try:
        assert exgs(core, "rc", "set", "SA=0").returncode == 0  # the safe position first
        assert exgs(core, "rc", "set", "SA=1", "S1=0.5").returncode == 0
        st = show(core)
        assert st["mode"] == "controlling" and st["sending"] and st["controls"]["SA"] == 1
        assert st["channels_us"][4] == 2000 and st["channels_us"][9] == 1500
        r = exgs(core, "wait", "MissionState.mission_index == 2", "--timeout", "5")  # the test echo
        assert r.returncode == 0, r.stdout + r.stderr
        # bad controls and values
        assert exgs(core, "rc", "set", "SA=5", "--json").returncode == 1
        assert exgs(core, "rc", "set", "XX=1", "--json").returncode == 1
        assert show(core)["controls"]["SA"] == 1  # unchanged by the rejected requests
        # momentary press held by the core, then released
        t0 = time.monotonic()
        r = exgs(core, "rc", "set", "SE=1", "--hold", "0.5")
        assert r.returncode == 0, r.stderr
        assert time.monotonic() - t0 >= 0.5
        assert show(core)["controls"]["SE"] == 0
        # a non-momentary control with --hold is restored by the CLI
        assert exgs(core, "rc", "set", "SB=2", "--hold", "0.3").returncode == 0
        assert show(core)["controls"]["SB"] == 0
        assert exgs(core, "rc", "set", "SE=1", "--hold", "99").returncode == 1  # bounded
    finally:
        assert exgs(core, "rc", "release").returncode == 0
    st = show(core)
    assert st["mode"] == "idle" and not st["sending"] and st["controls"]["SA"] == 0 and st["lease"] is None


def test_timed_lease_expires_and_sending_stops(core):
    assert exgs(core, "rc", "acquire", "--duration", "0.6").returncode == 0
    assert show(core)["sending"] is True
    time.sleep(0.9)
    st = show(core)
    assert st["lease"] is None and st["sending"] is False and st["mode"] == "idle"
