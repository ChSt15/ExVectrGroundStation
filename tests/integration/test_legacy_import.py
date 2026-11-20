"""Legacy import end to end: CLI, `exgs log validate/stats`, and replay of a converted log through the real core."""

import json
import shutil
from pathlib import Path

import pytest

from exvectr_gs.recording.legacy import convert_file
from tests.integration.test_api import Client, make_app, running
from tests.integration.test_cli import exgs
from tests.integration.test_replay_api import take

DATA = Path(__file__).resolve().parents[1] / "data" / "legacy"
REAL = Path(__file__).resolve().parents[3] / "data/legacy/ExVectrController/tools/data/missionData_flight6.txt"
SRC = REAL if REAL.is_file() else DATA / "tvcground33.txt"  # real file when the workspace data folder is present


def test_cli_import_validate_stats(tmp_path):
    out = tmp_path / "out"
    r = exgs("log", "import-legacy", str(SRC), str(DATA / "empty.txt"), "--out", str(out))
    assert r.returncode == 0, r.stderr
    assert "converted" in r.stdout and "empty file" in r.stdout and "1 failed" in r.stdout
    log = out / f"{SRC.stem}.exlog"
    assert (out / f"{SRC.stem}.meta.toml").is_file()
    v = exgs("log", "validate", str(log))
    assert v.returncode == 0 and "0 error(s), 0 warning(s)" in v.stdout
    st = json.loads(exgs("log", "stats", str(log), "--json").stdout)
    types = {t["type"] for t in st["types"]}
    assert {"Position", "Attitude", "MissionState", "Gps", "LegacyRow"} <= types and st["complete"]
    assert exgs("log", "import-legacy", str(DATA / "empty.txt"), "--out", str(out)).returncode == 1  # all failed
    assert exgs("log", "import-legacy", str(SRC), "--out", str(out), "--strict").returncode == 0


@pytest.mark.skipif(not REAL.is_file(), reason="workspace data folder not present")
def test_real_flight_values():
    """missionData_flight6: physical sanity checks of the converted values (docs/legacy-logs.md, evidence)."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        r = convert_file(REAL, Path(d))
        s = r.summary
        assert r.layout == "tvc-31" and s.rows == 522 and abs(s.duration_s - 51.94) < 0.01
        assert 50 < s.max_altitude_m < 53 and s.max_gnss_sats == 22 and s.inferred_kind == "flight"
        assert s.max_gps_alt_gain_m and abs(s.max_gps_alt_gain_m - s.max_altitude_m) < 2  # Z is up


async def test_converted_log_replays_through_core(tmp_path):
    app = make_app()
    lib = Path(app.library.directory)
    r = convert_file(SRC, tmp_path)
    shutil.copy(r.exlog, lib / r.exlog.name)
    async with running(app, sources=False) as rig:
        c = await Client.connect(rig)
        names = [i["name"] for i in await c.ok("logs.list")]
        assert r.exlog.name in names
        await c.ok("subscribe", view="replay", types="*", mode="latest", max_rate_hz=30)
        await take(c, "snapshot")
        c.inbox.clear()
        info = await c.ok("replay.open", name=r.exlog.name)
        snap = await take(c, "snapshot")
        st = (await take(c, "status"))["replay"]
        t0, t1 = st["t_start"], st["t_end"]
        assert info["name"] == r.exlog.name and t1 - t0 == pytest.approx(r.summary.duration_s, abs=0.01)
        await c.ok("replay.seek", t=t0 + (t1 - t0) * 0.8)
        snap = await take(c, "snapshot")
        items = {i["type"]: i for i in snap["items"]}
        assert {"Position", "Attitude", "MissionState", "Gps"} <= set(items)
        h = await c.ok(
            "get_history",
            view="replay",
            buffer="plot",
            series=[{"type": "Position", "fields": ["pos"]}, {"type": "Attitude", "fields": ["q"]}],
            t0=t0,
            t1=t1,
            max_points=100_000,
        )
        pos, att = h["series"]
        assert len(pos["t"]) == r.summary.rows and len(att["q"]) == r.summary.rows
        assert max(p[2] for p in pos["pos"]) == pytest.approx(r.summary.max_altitude_m)
        await c.ok("replay.close")
        await c.close()
