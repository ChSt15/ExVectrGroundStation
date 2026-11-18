"""`exgs` client commands against a real core process (FakeSource), as an AI agent would use them."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from exvectr_gs.packets import decode_line

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    cfg = tmp_path_factory.mktemp("cfg") / "gs.toml"
    rec_dir = tmp_path_factory.mktemp("rec")
    text = (ROOT / "config/gs.example.toml").read_text().replace('bind = "0.0.0.0"', 'bind = "127.0.0.1"')
    text = text.replace('directory = "~/exvectr-recordings"', f'directory = "{rec_dir}"')
    cfg.write_text(text)
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


def jl(r):
    return [json.loads(x) for x in r.stdout.splitlines()]


def test_status_json(core):
    r = exgs(core, "status", "--json")
    assert r.returncode == 0, r.stderr
    (d,) = jl(r)
    assert d["ok"] is True and d["sources"][0]["id"] == "fake" and d["sources"][0]["state"] == "connected"
    assert d["can_control"] is True and isinstance(d["clock"], float)
    assert "fake" in exgs(core, "status").stdout


def test_get_type_and_field_with_freshness(core):
    (d,) = jl(exgs(core, "get", "Attitude.q", "--json"))
    assert d["freshness"] == "fresh" and d["src"] == "fake" and len(d["value"]) == 4 and 0 <= d["age_s"] < 2
    r = exgs(core, "get", "VehicleState")
    assert r.returncode == 0 and " fake VehicleState mode=" in r.stdout and "# src=fake" in r.stdout
    r = exgs(core, "get", "Attitude.nope")
    assert r.returncode == 1 and "no field" in r.stderr
    r = exgs(core, "get", "RcState", "--json")  # never produced by the usb profile
    assert r.returncode == 1 and jl(r)[0]["error"]["code"] == "missing"
    assert exgs(core, "get", "Position.pos", "--src", "nobody").returncode == 1


def test_watch_prints_log_lines_and_json_lines(core):
    r = exgs(core, "watch", "--types", "Position,Attitude", "--count", "6")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert len(lines) == 6
    for ln in lines:
        assert decode_line(ln).TYPE in ("Position", "Attitude")
    r = exgs(core, "watch", "--types", "Position", "--count", "3", "--json")
    ds = jl(r)
    assert len(ds) == 3 and all(d["type"] == "Position" and isinstance(d["t"], float) for d in ds)
    r = exgs(core, "watch", "--src", "fake", "--duration", "0.5", "--types", "VehicleState")
    assert r.returncode == 0 and r.stdout.count("VehicleState") >= 5
    assert exgs(core, "watch", "--types", "Nope", "--count", "1").returncode == 1


def test_watch_count_not_reached_is_a_timeout(core):
    r = exgs(core, "watch", "--types", "RcState", "--count", "1", "--duration", "0.5")
    assert r.returncode == 2


def test_wait_succeeds_on_fake_data(core):
    r = exgs(core, "wait", "VehicleState.sim == true", "--timeout", "5")
    assert r.returncode == 0 and r.stdout.startswith("ok: VehicleState.sim == true")
    (d,) = jl(exgs(core, "wait", "Position.h_acc <= 5", "--timeout", "5", "--json"))
    assert d["ok"] and d["src"] == "fake" and d["waited_s"] < 5
    r = exgs(core, "wait", "VehicleState.mode == Running", "--timeout", "15")  # the scenario reaches it
    assert r.returncode == 0, r.stdout + r.stderr


def test_wait_timeout_exit_code_2_and_error_json(core):
    r = exgs(core, "wait", "VehicleState.mode == Failure", "--timeout", "1", "--json")
    assert r.returncode == 2
    (d,) = jl(r)
    assert d["ok"] is False and d["error"]["code"] == "timeout"
    r = exgs(core, "wait", "RcState.seq > 0", "--timeout", "0.5")
    assert r.returncode == 2 and "no value received" in r.stderr


def test_wait_bad_expression_and_usage_errors_exit_1(core):
    r = exgs(core, "wait", "nonsense", "--timeout", "1")
    assert r.returncode == 1 and "cannot parse" in r.stderr
    assert exgs(core, "wait", "VehicleState.armed > true", "--timeout", "1").returncode == 1
    assert exgs(core, "bogus-command").returncode == 1  # usage errors are 1, not 2 (2 = timeout)


@pytest.mark.parametrize(
    "args",
    [
        ("sources", "enable", "fake"),
    ],
)
def test_unimplemented_modules_report_not_implemented(core, args):
    r = exgs(core, *args, "--json")
    assert r.returncode == 1, (args, r.stdout, r.stderr)
    (d,) = jl(r)
    assert d["ok"] is False and d["error"]["code"] == "not_implemented"


def test_rc_without_control_source_is_denied(core):
    """The shared core's only source has rc_role = none: no control source, so no lease (exit 3)."""
    r = exgs(core, "rc", "acquire", "--duration", "5", "--json")
    assert r.returncode == 3
    assert jl(r)[0]["error"]["code"] == "control_denied"
    assert exgs(core, "rc", "set", "SA=1").returncode == 3  # no lease
    assert exgs(core, "rc", "show").returncode == 0


@pytest.mark.parametrize(
    ("args", "code"),
    [(("replay", "open", "x"), "not_found"), (("replay", "play"), "busy"), (("replay", "speed", "2"), None)],
)
def test_replay_without_open_log(core, args, code):
    r = exgs(core, *args, "--json")
    (d,) = jl(r)
    if code is None:  # speed can be set before a log is open
        assert d["ok"] is True
    else:
        assert r.returncode == 1 and d["error"]["code"] == code


def test_sources_list(core):
    (d,) = jl(exgs(core, "sources", "list", "--json"))
    assert d["sources"][0]["id"] == "fake"
    assert exgs(core, "sources", "list").stdout.startswith("fake")


def test_core_not_running_exits_1_with_hint():
    r = exgs(1, "status")  # nothing listens on port 1
    assert r.returncode == 1 and "cannot connect" in r.stderr and "exgs core" in r.stderr
    (d,) = jl(exgs(1, "status", "--json"))
    assert d["ok"] is False


def test_rc_set_argument_parsing():
    from exvectr_gs.cli.client import CliError
    from exvectr_gs.cli.cmd_rc import parse_assignments

    assert parse_assignments(["SA=1", "S1=0.4", "X=-2", "Y=1e-1"]) == {"SA": 1, "S1": 0.4, "X": -2, "Y": 0.1}
    for bad in (["SA"], ["=1"], ["SA=x"]):
        with pytest.raises(CliError):
            parse_assignments(bad)
