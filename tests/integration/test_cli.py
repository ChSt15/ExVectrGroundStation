import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def exgs(*args, timeout=30):
    return subprocess.run(
        [sys.executable, "-m", "exvectr_gs.cli.main", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout
    )


def test_core_runs_with_fake_source_and_prints_summary():
    r = exgs("core", "--config", "config/gs.example.toml", "--duration", "3", "--no-api")
    assert r.returncode == 0, r.stderr
    summary = [ln for ln in r.stdout.splitlines() if ln.startswith("fake:connected")]
    assert len(summary) >= 2 and "pkt/s" in summary[0] and "mode=" in summary[-1]


def test_core_bad_config_fails_cleanly(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text('[recorder]\nraw_frames = "all"\n')
    r = exgs("core", "--config", str(bad))
    assert r.returncode == 1 and "config error" in r.stderr


def test_schema_json_and_markdown():
    schema = json.loads(exgs("schema").stdout)
    assert any(t["name"] == "Attitude" for t in schema["types"])
    assert exgs("schema", "--markdown").stdout.startswith("# Packet types")


def test_packet_types_doc_is_up_to_date():
    assert (ROOT / "docs" / "packet-types.md").read_text() == exgs("schema", "--markdown").stdout
