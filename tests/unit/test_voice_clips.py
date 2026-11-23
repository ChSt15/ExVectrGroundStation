"""Recorded callout clips: the phrase list covers every text the callout engine can say, and the generated
manifest and files match the list. No Piper needed (the generator is imported for its pure parts only)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import generate_voice_clips as gen  # noqa: E402

CALLOUTS = ROOT / "web" / "app" / "callouts"
VOICE_DIR = ROOT / "web" / "audio" / gen.VOICE_ID


# T9b-4 phrases added to phrases.json without regenerating the clips (the callout falls back to the browser voice)
PENDING_IDS = {
    "begin",
    "till-upright",
    "control-source-lost",
    "gnss-lost",
    "magnetometer-lost",
    "barometer-failure",
    "gyro-failure",
    "accelerometer-failure",
    "attitude-estimate-failure",
    "position-estimate-failure",
    "interlock-watchdog-failure",
    "gs-failsafe",
    "interlock-open",
    "interlock-closed",
    "toggle-sd",
    "go-refused",
}
# 2026-11-23: the texts were renamed to "motors armed / disarmed" (and "motors live" for the output gate); the clip ids
# are unchanged and the clips are NOT regenerated, so the recorded audio still says the old words until they are. The
# engine says the new text, which has no clip yet: the callout falls back to the browser voice or beeps.
RENAMED_TEXT_IDS = {"interlock-open", "interlock-closed", "motors-enabled", "interlock-watchdog-failure"}
STALE_IDS = {"radio-connection-loss"}  # renamed to control-source-lost; the old clip file stays until regeneration


def engine_texts() -> set[str]:
    """Fixed spoken texts found in the engine, settings and controller sources; templates give their literal prefix."""
    engine = (CALLOUTS / "engine.js").read_text(encoding="utf-8")
    settings = (CALLOUTS / "settings.js").read_text(encoding="utf-8")
    controller = (CALLOUTS / "controller.js").read_text(encoding="utf-8")
    texts: set[str] = set()
    for m in re.finditer(r'\bev\(\s*(?:`[^`]*`|"[^"]*")\s*,\s*(?:`[^`]*`|"[^"]*")\s*,\s*(["`])(.*?)\1', engine):
        texts.add(m.group(2))
    for m in re.finditer(r'\? "([A-Z][^"]*)" : "([A-Z][^"]*)"', engine):
        texts.update(m.groups())
    texts.update(re.findall(r'text: "([A-Z][^"]*)"', engine))  # object-literal active.set(...) entries
    fail = re.search(r"FAILURE_TEXT = \[(.*?)\];", engine, re.S)
    assert fail, "FAILURE_TEXT not found"
    texts.update(re.findall(r'"([^"]+)"', fail.group(1)))
    texts.update(re.findall(r"`([A-Za-z ]+) \$\{", engine))  # `Failure bit ${i}`, `Altitude ${n}`
    phase = re.search(r"phaseNames: \{(.*?)\n  \},", settings, re.S)
    assert phase, "phaseNames not found"
    texts.update(re.findall(r':\s*"([^"]+)"', phase.group(1)))
    cfg = json.loads((ROOT / "config" / "callouts.default.json").read_text(encoding="utf-8"))
    texts.update(cfg.get("phaseNames", {}).values())
    texts.update(re.findall(r'text: "([A-Za-z][^"]*)"', controller))  # SAMPLES and the volume test
    out = {re.sub(r"\s+\d+$", "", re.sub(r"\s*\$\{.*", "", t)).strip() for t in texts if t}
    out.discard("")  # a bare number template (`${l}`): numbers are composed from the num-N clips
    return out


def test_clip_table_is_consistent():
    table = gen.clip_table()
    assert len(set(table.values())) == len(table), "two clip ids speak the same text"
    for n in range(100):
        assert table[f"num-{n}"]
    assert table["num-100"] == "one hundred" and table["num-900"] == "nine hundred"
    assert gen.number_words(47) == "forty-seven"
    for cid in table:
        assert re.fullmatch(r"[a-z0-9-]+", cid)


def test_every_engine_phrase_has_a_clip():
    spoken = {t.lower() for t in gen.clip_table().values()}
    texts = engine_texts()
    known = {"Motors armed", "Begin", "Failure bit", "Sensor failure", "Belly flop", "Volume test"}
    assert known <= texts, "extraction broke"
    # no recorded clip yet (piper was not available when the GNSS cross-check was added): the callout falls back to
    # the browser voice or beeps until the phrase is added to phrases.json and the clips are regenerated
    pending = {"Altitude mismatch"}
    missing = sorted(t for t in texts if t.lower() not in spoken and not t.isdigit() and t not in pending)
    assert not missing, f"engine phrases without a clip (add them to web/audio/phrases.json and regenerate): {missing}"


def test_composable_parts_are_present():
    spoken = {t.lower() for t in gen.clip_table().values()}
    parts = ("minus", "point", "meters", "meters per second", "seconds", "degrees", "percent", "t minus", "thousand")
    for part in parts:
        assert part in spoken
    table = gen.clip_table()
    assert all(f"num-{n}" in table for n in range(61))  # countdown 1..60


def test_length_scale_and_licence_constants():
    assert gen.LENGTH_SCALE == 1.1
    assert "CC-BY-SA" in gen.LICENCE
    assert "CC-BY-SA" in gen.licence_text(gen.LENGTH_SCALE)


def test_trim_cuts_silence_and_fades():
    np = pytest.importorskip("numpy")
    rate = 22050
    x = np.concatenate([np.zeros(rate), 0.5 * np.ones(rate // 10), np.zeros(rate)]).astype("float32")
    y = gen.trim(x, rate)
    assert 0.1 < y.size / rate < 0.2
    assert y[0] == 0.0 and abs(y[-1]) < 0.01 and y.max() == pytest.approx(0.5)


@pytest.mark.skipif(not (VOICE_DIR / "manifest.json").exists(), reason="clips not generated")
def test_generated_manifest_matches_the_phrase_list_and_files():
    man = json.loads((VOICE_DIR / "manifest.json").read_text(encoding="utf-8"))
    table = gen.clip_table()
    assert man["voice"] == gen.VOICE_ID
    assert man["length_scale"] == gen.LENGTH_SCALE
    assert set(man["clips"]) - STALE_IDS == set(table) - PENDING_IDS, "regenerate: tools/generate_voice_clips.py"
    total = 0
    for cid, clip in man["clips"].items():
        if cid in STALE_IDS:
            continue
        if cid not in RENAMED_TEXT_IDS:
            assert clip["text"] == table[cid]
        f = VOICE_DIR / clip["file"]
        assert f.is_file() and f.stat().st_size > 100, cid
        assert 0.1 < clip["duration"] < 5, cid
        total += f.stat().st_size
    assert total < 5 * 1024 * 1024
    assert (VOICE_DIR / "LICENSE-VOICE.txt").is_file()
