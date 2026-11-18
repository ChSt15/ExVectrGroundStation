import json

from tests.integration.test_cli import exgs
from tests.support_logs import make_log


def test_validate_ok_incomplete_and_corrupt(tmp_path):
    p = make_log(tmp_path, "full", 5.0)
    r = exgs("log", "validate", str(p))
    assert r.returncode == 0 and "0 error(s), 0 warning(s)" in r.stdout

    data = p.read_bytes()
    inc = tmp_path / "inc.exlog"
    inc.write_bytes(data[: data.index(b"#end")] + b"1763471005000000000 usb Posi")
    r = exgs("log", "validate", str(inc))
    assert r.returncode == 0  # a crashed recording is valid
    assert "no #end line" in r.stdout and "truncated last line" in r.stdout

    bad = tmp_path / "bad.exlog"
    bad.write_bytes(data.replace(b"tilt_acc=0.0035", b"tilt_acc=abc", 1))
    r = exgs("log", "validate", str(bad))
    assert r.returncode == 1 and "error:" in r.stdout

    miscount = tmp_path / "miscount.exlog"
    miscount.write_bytes(data.replace(b"packets=", b"packets=1"))
    assert exgs("log", "validate", str(miscount)).returncode == 1

    assert exgs("log", "validate", str(tmp_path / "nope.exlog")).returncode == 1
    junk = tmp_path / "junk.exlog"
    junk.write_text("hello\n")
    assert exgs("log", "validate", str(junk)).returncode == 1


def test_stats_text_and_json(tmp_path):
    p = make_log(tmp_path, "no-flapstate", 10.0)
    r = exgs("log", "stats", str(p), "--json")
    assert r.returncode == 0
    d = json.loads(r.stdout)
    types = {(t["type"], t["src"]): t for t in d["types"]}
    assert d["complete"] and abs(d["duration_s"] - 10.0) < 0.01
    assert ("FlapState", "usb") not in types
    assert abs(types[("Attitude", "usb")]["rate_hz"] - 100) < 1
    assert "Attitude" in exgs("log", "stats", str(p)).stdout


def test_cat_filters(tmp_path):
    p = make_log(tmp_path, "full", 10.0)
    r = exgs("log", "cat", str(p), "--types", "Note,GsEvent")
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert len(lines) == 4 and all(" gs " in ln for ln in lines)
    r = exgs("log", "cat", str(p), "--types", "Attitude", "--src", "usb", "--start", "2", "--end", "3", "--count", "5")
    lines = r.stdout.splitlines()
    assert len(lines) == 5 and all(" usb Attitude " in ln for ln in lines)
    t0 = int(lines[0].split()[0])
    assert 2_000_000_000 <= t0 - 1_763_471_000_000_000_000 <= 2_020_000_000
    assert "#exlog" in exgs("log", "cat", str(p), "--meta", "--count", "1").stdout


def test_core_has_recorder_but_never_auto_records(tmp_path):
    from exvectr_gs.core.app import CoreApp
    from exvectr_gs.core.config import parse_config

    cfg = parse_config(
        {
            "core": {"source_priority": ["fake"]},
            "recorder": {"directory": str(tmp_path / "rec")},
            "source": [{"id": "fake", "kind": "fake"}],
        },
    )
    app = CoreApp(cfg)
    assert not app.recorder.status().active
    info = app.recorder.start("t")
    app.recorder.stop()
    assert info.path.parent == tmp_path / "rec"
    assert "#source id=fake kind=fake" in info.path.read_text()
