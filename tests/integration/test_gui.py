"""GUI tests: static consistency of web/, the JS unit tests, and a real-browser smoke test against the core
with the fake source (skipped when no Chromium-based browser is installed). Tests write only to temp dirs."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.test_api import make_app, running

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "app"
sys.path.insert(0, str(ROOT / "tools"))
import browser_check  # noqa: E402

needs_browser = pytest.mark.skipif(browser_check.find_browser() is None, reason="no Chromium-based browser")


def panel_ids() -> set[str]:
    ids = set()
    for p in (WEB / "panels").glob("*.js"):
        ids.update(re.findall(r'registerPanel\(\{\s*id: "([a-z_]+)"', p.read_text()))
    return ids


def test_every_panel_file_is_imported_and_ids_are_unique():
    index = (WEB / "panels" / "index.js").read_text()
    files = {p.stem for p in (WEB / "panels").glob("*.js")} - {"index", "registry"}
    assert files == set(re.findall(r'import "\./([a-z]+)\.js"', index))
    all_ids = [
        i
        for p in (WEB / "panels").glob("*.js")
        for i in re.findall(r'registerPanel\(\{\s*id: "([a-z_]+)"', p.read_text())
    ]
    assert len(all_ids) == len(set(all_ids)) == len(files)


def test_relative_imports_resolve():
    for p in WEB.rglob("*.js"):
        for rel in re.findall(r'from "(\.[^"]+)"|import "(\.[^"]+)"', p.read_text()):
            target = (p.parent / (rel[0] or rel[1])).resolve()
            assert target.is_file(), f"{p.relative_to(ROOT)} imports missing {rel}"


def test_default_layout_is_consistent_with_the_registered_panels():
    layout = json.loads((ROOT / "config" / "layout.default.json").read_text())
    assert {"default", "bench", "flight", "phone"} <= set(layout["presets"])
    ids = panel_ids()
    for name, preset in layout["presets"].items():
        used = [e["panel"] for e in preset["panels"]]
        assert set(used) <= ids, f"{name}: unknown panels {set(used) - ids}"
        assert len(used) == len(set(used)), f"{name}: panel listed twice"
        areas = {a for row in preset["areas"] for a in row.split()}
        assert {e["area"] for e in preset["panels"]} <= areas, name


def test_index_page_has_no_external_resources():
    html = (ROOT / "web" / "index.html").read_text() + (WEB / "style.css").read_text()
    assert not re.search(r'(src|href)="https?://', html)
    assert "url(http" not in html


def test_js_unit_tests_pass():
    if not shutil.which("node") and browser_check.find_browser() is None:
        pytest.skip("neither node nor a browser available")
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "run_js_tests.py")], capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 0, r.stdout + r.stderr


JS_PANEL_STATES = """
(() => {
  const out = {};
  for (const p of document.querySelectorAll('.panel')) {
    const vals = [...p.querySelectorAll('.val')];
    out[p.dataset.panel] = {
      n: vals.length,
      fresh: vals.filter(v => v.dataset.state === 'fresh').length,
      stale: vals.filter(v => v.dataset.state === 'stale').length,
      tagged: vals.filter(v => v.dataset.state === 'fresh' && v.querySelector('.src')?.textContent).length,
      ages: vals.filter(v => v.dataset.state === 'stale').map(v => v.querySelector('.age')?.textContent),
    };
  }
  return out;
})()
"""


@needs_browser
async def test_browser_panels_show_fresh_then_stale_values_and_views_switch():
    faults = [{"kind": "dropout", "at": 1.5, "duration": 1000, "types": ["Attitude"]}]
    async with running(make_app(faults=faults, speed=2.0)) as rig:
        async with browser_check.Browser(1500, 1000, 9341) as b:
            await b.goto(rig.url() + "/")
            await b.sleep(1.0)
            early = await b.eval(JS_PANEL_STATES)
            # every data panel shows fresh values with source tags
            for pid in ("modes", "mission", "failures", "position", "guidance", "actuators", "sensors", "link"):
                assert early[pid]["fresh"] > 0, (pid, early[pid])
                assert early[pid]["tagged"] > 0, (pid, early[pid])
            # Attitude stops (dropout from t=1.5 s sim time): its values go stale and show an age
            await b.sleep(5.0)
            late = await b.eval(JS_PANEL_STATES)
            assert late["position"]["stale"] > 0 and any(a for a in late["position"]["ages"]), late["position"]
            assert late["modes"]["stale"] == 0  # VehicleState keeps arriving
            badge = await b.eval("document.querySelector('[data-panel=position] .badge-age').textContent")
            assert badge.startswith("stale"), badge
            # LIVE / REPLAY switch: transport bar appears, data panels follow the (empty) replay view
            assert await b.eval("document.querySelector('[data-panel=transport]').hidden") is True
            await b.eval(
                "[...document.querySelectorAll('header.top .toggle button')].find(x => x.textContent === 'REPLAY').click()"  # noqa: E501
            )
            await b.sleep(0.8)
            assert await b.eval("document.body.dataset.view") == "replay"
            assert await b.eval("document.querySelector('[data-panel=transport]').hidden") is False
            assert await b.eval("document.querySelector('[data-panel=modes] .val').dataset.state") == "missing"
            await b.eval(
                "[...document.querySelectorAll('header.top .toggle button')].find(x => x.textContent === 'LIVE').click()"  # noqa: E501
            )
            await b.sleep(1.0)
            assert await b.eval("document.querySelector('[data-panel=modes] .val').dataset.state") == "fresh"
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_browser_phone_width_has_no_horizontal_scroll_and_uses_phone_preset():
    async with running(make_app()) as rig:
        async with browser_check.Browser(390, 844, 9342) as b:
            await b.goto(rig.url() + "/")
            await b.sleep(1.5)
            assert await b.eval("document.querySelector('header.top select').value") == "phone"
            assert await b.eval("document.documentElement.scrollWidth") <= 390
            assert await b.eval("document.querySelectorAll('.panel').length") >= 10
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_browser_recording_controls_and_console_do_not_break():
    async with running(make_app()) as rig:
        async with browser_check.Browser(1500, 1000, 9343) as b:
            await b.goto(rig.url() + "/")
            await b.sleep(1.0)
            await b.eval("document.querySelector('[data-panel=recording] button.danger').click()")
            await b.sleep(1.5)
            assert (await b.eval("document.querySelector('header.top .rec').textContent")).startswith("REC ●")
            await b.eval(
                "[...document.querySelectorAll('[data-panel=recording] .toolbar button')].find(x => x.textContent === 'Stop').click()"  # noqa: E501
            )
            await b.sleep(2.0)
            assert (await b.eval("document.querySelector('header.top .rec').textContent")).startswith("REC ○")
            assert "exlog" in await b.eval("document.querySelector('[data-panel=recording] .loglist').textContent")
            assert not [m for m in b.console if "EXCEPTION" in m], b.console
