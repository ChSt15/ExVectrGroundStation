"""`exgs rc ...` (the virtual panel) against a real core process whose fake source is the command link with the test
echo (the echoed field is `go`)."""

import contextlib
import json
import os
import signal
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
echo_field = "go"
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


def wait_until(pred, timeout=5.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def test_agent_bench_sequence_and_hold(core):
    assert show(core)["mode"] == "idle" and show(core)["lease"] is None
    assert exgs(core, "rc", "keepalive").returncode == 3  # nothing to renew
    r = exgs(core, "rc", "acquire", "--idle", "9", "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.splitlines()[0])["kind"] == "keepalive"
    assert exgs(core, "rc", "acquire", "--idle", "5").returncode == 3  # held by the first process
    try:
        assert exgs(core, "rc", "set", "SA=0").returncode == 0  # the safe position first (renews the lease)
        assert exgs(core, "rc", "set", "SA=1", "S1=0.5").returncode == 0
        st = show(core)
        assert st["mode"] == "virtual" and st["sending"] and st["controls"]["SA"] == 1
        assert st["channels_us"][4] == 2000 and st["channels_us"][9] == 1500
        assert st["lease"]["kind"] == "keepalive" and st["lease"]["idle_s"] == 9.0
        r = exgs(core, "wait", "MissionState.mission_index == 1", "--timeout", "5")  # the test echo
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
        assert exgs(core, "rc", "set", "SB=1", "--hold", "0.3").returncode == 0
        assert show(core)["controls"]["SB"] == 2  # restored to what it was: the panel starts at the engine's profile 2
        assert exgs(core, "rc", "set", "SE=1", "--hold", "99").returncode == 1  # bounded
        assert exgs(core, "rc", "keepalive").returncode == 0
    finally:
        assert exgs(core, "rc", "release").returncode == 0
    st = show(core)
    assert (
        st["mode"] == "idle" and st["sending"] and st["controls"]["SA"] == 0 and st["lease"] is None
    )  # engine goes on


def test_one_shot_set_takes_a_keepalive_lease_that_lapses_to_the_panel_defaults(core):
    r = exgs(core, "rc", "set", "SA=1", "--idle", "1.2", "--json")  # no lease held: this command takes one
    assert r.returncode == 0, r.stderr
    lines = [json.loads(x) for x in r.stdout.splitlines()]
    assert any(d.get("op") == "rc.acquire" and d["kind"] == "keepalive" for d in lines)
    st = show(core)
    assert st["sending"] and st["controls"]["SA"] == 1 and st["lease"]["idle_s"] == 1.2
    time.sleep(1.8)  # nobody renews it
    st = show(core)
    assert st["lease"] is None and st["sending"] and st["controls"]["SA"] == 0 and st["mode"] == "idle"


def test_keepalive_command_renews_the_lease(core):
    assert exgs(core, "rc", "acquire", "--idle", "3").returncode == 0
    try:
        t0 = time.monotonic()
        for _ in range(2):
            time.sleep(1.5)
            assert exgs(core, "rc", "keepalive").returncode == 0
        assert time.monotonic() - t0 > 3.0 and show(core)["lease"] is not None  # outlived its idle time by renewals
    finally:
        exgs(core, "rc", "release")
    wait_until(lambda: show(core)["lease"] is None, what="release")


def test_cli_idle_timeout_is_capped_at_9_seconds(core):
    r = exgs(core, "rc", "acquire", "--idle", "10", "--json")
    assert r.returncode == 1 and json.loads(r.stdout)["error"]["code"] == "invalid_value"
    assert show(core)["lease"] is None


def test_locked_sd_needs_the_explicit_unlock_flag(core):
    try:
        r = exgs(core, "rc", "set", "SD=1", "--idle", "9", "--json")
        assert r.returncode == 3 and json.loads(r.stdout.splitlines()[-1])["error"]["code"] == "control_locked"
        assert "--unlock" in json.loads(r.stdout.splitlines()[-1])["error"]["message"]
        st = show(core)
        assert st["controls"]["SD"] == 0 and st["locks"]["SD"]["locked"] is True
        assert exgs(core, "rc", "set", "SA=1", "--unlock", "SD").returncode == 1  # not among the controls being set
        r = exgs(core, "rc", "set", "SD=1", "--unlock", "SD")
        assert r.returncode == 0, r.stderr
        st = show(core)
        assert st["controls"]["SD"] == 1 and st["locks"]["SD"]["locked"] is False
        assert 0 < st["locks"]["SD"]["remaining_s"] <= 60
        assert "UNLOCKED" in exgs(core, "rc", "show").stdout
        # a failed request after an unlock leaves nothing unlocked
        r = exgs(core, "rc", "set", "SD=1", "SB=9", "--unlock", "SD")
        assert r.returncode == 1
        st = show(core)
        assert st["locks"]["SD"]["locked"] is True and st["controls"]["SD"] == 0
        # explicit unlock / lock commands; lock returns SD to off
        assert exgs(core, "rc", "unlock", "SD").returncode == 0
        assert exgs(core, "rc", "set", "SD=1").returncode == 0 and show(core)["controls"]["SD"] == 1
        assert exgs(core, "rc", "lock", "SD").returncode == 0
        st = show(core)
        assert st["controls"]["SD"] == 0 and st["locks"]["SD"]["locked"] is True
        assert exgs(core, "rc", "unlock", "SA").returncode == 1  # no lock configured
    finally:
        exgs(core, "rc", "release")
    wait_until(lambda: show(core)["lease"] is None, what="release")


def session(core, *extra):
    return subprocess.Popen(
        [sys.executable, "-m", "exvectr_gs.cli.main", "rc", "session", "--port", str(core), *extra],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_session_holds_a_connection_lease_and_a_crash_ends_it_at_once(core):
    p = session(core)
    try:
        assert "connection" in p.stdout.readline()
        p.stdin.write("set SA=1 S1=0.5\n")
        p.stdin.flush()
        st = wait_until(lambda: (s := show(core))["controls"]["SA"] == 1 and s, what="session set")
        assert st["lease"]["kind"] == "connection" and st["sending"]
        time.sleep(1.5)  # longer than the 1 s heartbeat limit: only the 300 ms heartbeat keeps it
        assert show(core)["lease"] is not None
        p.kill()  # crash: the connection drops
        p.wait(5)
        wait_until(lambda: show(core)["lease"] is None, 0.8, "lease end after a crash")
    finally:
        p.kill()
        p.wait(5)
        p.stdin.close()
        p.stdout.close()
        p.stderr.close()
    st = show(core)
    assert st["sending"] and st["controls"]["SA"] == 0 and st["mode"] == "idle"  # the stream never needed the session


def test_session_that_stops_heartbeating_loses_the_lease_within_a_second(core):
    p = session(core)
    try:
        assert "connection" in p.stdout.readline()
        assert show(core)["lease"] is not None
        os.kill(p.pid, signal.SIGSTOP)  # alive, connected, but silent
        t0 = time.monotonic()
        wait_until(lambda: show(core)["lease"] is None, 2.0, "heartbeat loss")
        assert time.monotonic() - t0 < 1.6  # 1 s timeout plus the polling subprocess start-up
        os.kill(p.pid, signal.SIGCONT)
        p.wait(5)
        assert p.returncode == 3  # the session notices that its lease was lost
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(p.pid, signal.SIGCONT)
            p.kill()
        p.wait(5)
        p.stdin.close()
        p.stdout.close()
        p.stderr.close()


def test_session_script_commands_unlock_and_end_releases(core, tmp_path):
    script = tmp_path / "s.txt"
    script.write_text(
        "# bench sequence\nset SA=0\nunlock SD\nset SD=1 SA=1\nsleep 0.3\nset SE=1 --hold 0.2\nshow\n"
        "set SD=0 SA=0\nsleep 0.2\nquit\n"
    )  # the engine holds the last state after the session: so the script disarms and aborts first
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "exvectr_gs.cli.main",
            "rc",
            "session",
            "--script",
            str(script),
            "--port",
            str(core),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    shown = [json.loads(x) for x in r.stdout.splitlines() if '"mode"' in x]
    assert any(
        d["controls"]["SD"] == 1 and d["controls"]["SA"] == 1 and d["lease"]["kind"] == "connection" for d in shown
    )
    st = show(core)  # the session ended: lease released, everything back at defaults and locked
    assert st["lease"] is None and st["controls"]["SD"] == 0 and st["locks"]["SD"]["locked"] is True


def test_session_stops_on_a_failing_command_unless_keep_going(core, tmp_path):
    script = tmp_path / "s.txt"
    script.write_text("set SD=1\nset SA=1\nshow\n")  # SD is locked: the first line fails
    base = [sys.executable, "-m", "exvectr_gs.cli.main", "rc", "session", "--script", str(script), "--port", str(core)]
    r = subprocess.run(base + ["--json"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert r.returncode == 3 and "control_locked" in r.stdout
    assert '"mode"' not in r.stdout  # stopped before `show`
    r = subprocess.run(base + ["--keep-going"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "control_locked" in r.stderr and "rc: virtual" in r.stdout
    assert show(core)["lease"] is None


def test_session_denied_while_another_holder_exists(core):
    assert exgs(core, "rc", "acquire", "--idle", "9").returncode == 0
    try:
        r = subprocess.run(
            [sys.executable, "-m", "exvectr_gs.cli.main", "rc", "session", "--port", str(core), "--json"],
            cwd=ROOT, input="quit\n", capture_output=True, text=True, timeout=30,
        )  # fmt: skip
        assert r.returncode == 3 and "control_denied" in r.stdout
    finally:
        exgs(core, "rc", "release")
