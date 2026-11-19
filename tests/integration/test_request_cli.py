"""`exgs request ...` against a real core process (fake sources as answering vehicles with different behaviour)."""

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
answer_requests = true
request_targets = [1]
[[source]]
id = "rej"
kind = "fake"
answer_requests = true
request_targets = [2]
request_status = 2
[[source]]
id = "mute"
kind = "fake"
answer_requests = true
request_targets = [3]
request_drop_first = 99
[[requests.action]]
action = "CALIB_APPLY"
target = 1
label = "Apply"
group = "Calibration"
[[requests.action]]
action = "SAVE_CONFIG"
target = 1
label = "Save"
group = "Vehicle"
[[requests.action]]
action = "LINK_UPLOAD_MODE"
target = 2
label = "TX upload"
group = "Link modules"
confirm = "danger"
[[requests.action]]
action = "LINK_UPLOAD_MODE"
target = 3
label = "RX upload"
group = "Link modules"
confirm = "danger"
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
    time.sleep(0.55)  # the core allows one new request per 500 ms per client host
    return subprocess.run(
        [sys.executable, "-m", "exvectr_gs.cli.main", *args, "--port", str(port)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def status(port):
    r = exgs(port, "request", "status", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.splitlines()[0])


def test_list_shows_the_whitelist(core):
    r = exgs(core, "request", "list")
    assert r.returncode == 0
    assert "CALIB_APPLY" in r.stdout and "confirm=danger" in r.stdout and "SYSTEM_RESET" not in r.stdout
    d = json.loads(exgs(core, "request", "list", "--json").stdout.splitlines()[0])
    assert d["ok"] and [e["action"] for e in d["actions"]][-1] == "LINK_UPLOAD_MODE"


def test_send_without_confirm_or_with_a_wrong_one_sends_nothing(core):
    for extra in ([], ["--confirm", "SAVE_CONFIG"], ["--confirm", "calib_apply"]):
        r = exgs(core, "request", "send", "CALIB_APPLY", *extra)
        assert r.returncode == 1 and "--confirm CALIB_APPLY" in r.stderr, (extra, r.stderr)
    r = exgs(core, "request", "send", "CALIB_APPLY", "--json")
    assert r.returncode == 1 and json.loads(r.stdout)["error"]["code"] == "confirm_required"
    assert status(core) == {"ok": True, "inflight": [], "recent": []}


def test_unknown_action_and_wrong_target_are_refused(core):
    r = exgs(core, "request", "send", "SYSTEM_RESET", "--confirm", "SYSTEM_RESET")
    assert r.returncode == 1 and "not in the request whitelist" in r.stderr
    r = exgs(core, "request", "send", "CALIB_APPLY", "--target", "2", "--confirm", "CALIB_APPLY")
    assert r.returncode == 1 and "whitelist" in r.stderr
    assert status(core)["recent"] == []


def test_danger_needs_the_extra_flag(core):
    r = exgs(core, "request", "send", "LINK_UPLOAD_MODE", "--target", "2", "--confirm", "LINK_UPLOAD_MODE")
    assert r.returncode == 1 and "--i-understand-the-link-stops" in r.stderr
    assert "stops the radio link" in r.stderr
    assert status(core)["recent"] == []


def test_send_waits_for_the_ack_and_shows_it(core):
    r = exgs(core, "request", "send", "CALIB_APPLY", "--confirm", "CALIB_APPLY")
    assert r.returncode == 0, r.stderr
    assert "ACK DONE" in r.stdout and "node 1" in r.stdout
    d = json.loads(exgs(core, "request", "send", "SAVE_CONFIG", "--confirm", "SAVE_CONFIG", "--json").stdout)
    assert d["ok"] and d["state"] == "acked" and d["status_name"] == "DONE" and d["action"] == "SAVE_CONFIG"
    recent = status(core)["recent"]
    assert [x["action"] for x in recent][:2] == ["SAVE_CONFIG", "CALIB_APPLY"]


def test_vehicle_rejection_is_reported_exactly_with_a_failing_exit_code(core):
    r = exgs(
        core, "request", "send", "LINK_UPLOAD_MODE", "--target", "2", "--confirm", "LINK_UPLOAD_MODE",
        "--i-understand-the-link-stops",
    )  # fmt: skip
    assert r.returncode == 1 and "ACK REJECTED_STATE" in r.stdout


def test_missing_ack_is_a_timeout_after_five_attempts(core):
    t0 = time.monotonic()
    r = exgs(
        core, "request", "send", "LINK_UPLOAD_MODE", "--target", "3", "--confirm", "LINK_UPLOAD_MODE",
        "--i-understand-the-link-stops",
    )  # fmt: skip
    assert r.returncode == 2 and "TIMEOUT" in r.stdout and "5 attempts" in r.stdout
    assert 4.5 < time.monotonic() - t0 < 12
    assert status(core)["recent"][0]["state"] == "timeout"


def test_no_wait_returns_after_the_first_attempt(core):
    r = exgs(core, "request", "send", "SAVE_CONFIG", "--confirm", "SAVE_CONFIG", "--no-wait", "--json")
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert d["state"] in ("sending", "acked") and d["attempts"] == 1
