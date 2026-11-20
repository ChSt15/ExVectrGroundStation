"""`exgs tc ...` against a real core process with a fake source that accepts telecommands."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

CONFIG = """
[core]
source_priority = ["ok"]
[server]
bind = "127.0.0.1"
control_hosts = ["127.0.0.1", "::1"]
[recorder]
directory = "{rec}"
[[source]]
id = "ok"
kind = "fake"
answer_telecommands = true
request_targets = [1]
[[telecommands.command]]
name = "Arm"
type = "Arm"
param_int = 165
[[telecommands.command]]
name = "ZeroEstimators"
type = "ZeroEstimators"
param_int = 0x2E
param_float = 10.0
param_float_min = 1.0
param_float_max = 60.0
param_float_name = "duration_s"
[[telecommands.command]]
name = "MissionBegin"
type = "MissionBegin"
param_int = -10
param_min = -3600
param_max = 3600
"""


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    cfg = tmp_path_factory.mktemp("cfg") / "gs.toml"
    cfg.write_text(CONFIG.format(rec=tmp_path_factory.mktemp("rec")))
    p = subprocess.Popen(
        [sys.executable, "-m", "exvectr_gs.cli.main", "core", "--config", str(cfg), "--port", "0", "--duration", "120"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip
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


def exgs(port, *args):
    time.sleep(0.55)  # one new telecommand per 500 ms per client host
    return subprocess.run(
        [sys.executable, "-m", "exvectr_gs.cli.main", *args, "--port", str(port)],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )  # fmt: skip


def test_list(core):
    r = exgs(core, "tc", "list")
    assert r.returncode == 0 and "Arm" in r.stdout and "-3600..3600" in r.stdout


def test_send_needs_the_name_repeated_and_never_defaults(core):
    for extra in ([], ["--confirm", "MissionBegin"], ["--confirm", "arm"]):
        r = exgs(core, "tc", "send", "Arm", *extra)
        assert r.returncode == 1 and "--confirm Arm" in r.stderr, (extra, r.stderr)
    r = exgs(core, "tc", "send", "Format", "--confirm", "Format")
    assert r.returncode == 1 and "whitelist" in r.stderr


def test_send_once_and_say_there_is_no_ack(core):
    r = exgs(core, "tc", "send", "Arm", "--confirm", "Arm")
    assert r.returncode == 0 and "once" in r.stdout and "no ACK" in r.stdout
    d = json.loads(
        exgs(core, "tc", "send", "MissionBegin", "--confirm", "MissionBegin", "--param", "-30", "--json").stdout
    )
    assert d["ok"] and d["param_int"] == -30 and "state" not in d
    r = exgs(core, "tc", "send", "MissionBegin", "--confirm", "MissionBegin", "--param", "99999", "--json")
    assert r.returncode == 1 and json.loads(r.stdout)["error"]["code"] == "invalid_value"


def test_zero_estimators_float_parameter(core):
    r = exgs(core, "tc", "list")
    assert "duration_s 1..60 (default 10)" in r.stdout
    d = json.loads(exgs(core, "tc", "send", "ZeroEstimators", "--confirm", "ZeroEstimators", "--json").stdout)
    assert d["ok"] and (d["param_int"], d["param_float"]) == (0x2E, 10.0)
    d = json.loads(
        exgs(
            core, "tc", "send", "ZeroEstimators", "--confirm", "ZeroEstimators", "--param-float", "30", "--json"
        ).stdout
    )
    assert d["ok"] and d["param_float"] == 30.0 and "state" not in d
    r = exgs(core, "tc", "send", "ZeroEstimators", "--confirm", "ZeroEstimators", "--param-float", "99", "--json")
    assert r.returncode == 1 and json.loads(r.stdout)["error"]["code"] == "invalid_value"
    r = exgs(core, "tc", "send", "Arm", "--confirm", "Arm", "--param-float", "5", "--json")
    assert r.returncode == 1 and json.loads(r.stdout)["error"]["code"] == "invalid_value"
    r = exgs(core, "tc", "send", "ZeroEstimators", "--confirm", "Zero")
    assert r.returncode == 1 and "--confirm ZeroEstimators" in r.stderr
