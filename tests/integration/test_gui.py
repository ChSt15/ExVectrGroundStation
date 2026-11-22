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
        ids.update(re.findall(r'registerPanel\(\{\s*id: "([a-z0-9_]+)"', p.read_text()))
    return ids


def test_every_panel_file_is_imported_and_ids_are_unique():
    index = (WEB / "panels" / "index.js").read_text()
    files = {p.stem for p in (WEB / "panels").glob("*.js")} - {"index", "registry"}
    assert files == set(re.findall(r'import "\./([a-z0-9]+)\.js"', index))
    all_ids = [
        i
        for p in (WEB / "panels").glob("*.js")
        for i in re.findall(r'registerPanel\(\{\s*id: "([a-z0-9_]+)"', p.read_text())
    ]
    assert len(all_ids) == len(set(all_ids)) == len(files)


def test_relative_imports_resolve():
    for p in WEB.rglob("*.js"):
        for rel in re.findall(r'from "(\.[^"]+)"|import "(\.[^"]+)"', p.read_text()):
            target = (p.parent / (rel[0] or rel[1])).resolve()
            assert target.is_file(), f"{p.relative_to(ROOT)} imports missing {rel}"


def test_default_layout_is_consistent_with_the_registered_panels():
    layout = json.loads((ROOT / "config" / "layout.default.json").read_text())
    assert set(layout["views"]) == {"bench", "flight", "tuning"} and layout["default"] == "bench"
    ids = panel_ids()
    dock = [t["panel"] for t in layout["dock"]["tabs"]]
    assert set(dock) <= ids and len(dock) == len(set(dock))
    for name, view in layout["views"].items():
        if name != "tuning":
            assert [g["title"] for g in view["groups"]] == ["Vehicle", "Guidance", "Mission"], name
        used = [e["panel"] for g in view["groups"] for e in g["panels"]]
        assert set(used) <= ids, f"{name}: unknown panels {set(used) - ids}"
        assert len(used) == len(set(used)), f"{name}: panel listed twice"
        assert not set(used) & set(dock), f"{name}: dock panel inside a group"
        assert "view3d" in used or name == "tuning"


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
            for pid in ("attitude", "mission", "position", "targetvs", "actuators", "sensors", "link"):
                assert early[pid]["fresh"] > 0, (pid, early[pid])
            for pid in ("attitude", "position", "mission", "link"):
                assert early[pid]["tagged"] > 0, (pid, early[pid])
            fail = await b.eval(
                "[...document.querySelectorAll('[data-panel=failures] .frow:not([data-key^=\"calib.\"]) .pill')].map(e => e.textContent)"  # noqa: E501
            )
            assert fail and all(t == "ok" for t in fail), fail  # VehicleStatus failure bits, all clear
            # calibration rows: the fake vehicle reports calib_state (all valid, ready to arm) once it is ready, and the
            # structured state is primary: a log warning must not turn a row red
            sel = "[data-panel=failures] [data-key=\"calib.gyro\"], [data-panel=failures] [data-key=\"calib.accel\"], [data-panel=failures] [data-key=\"calib.mag\"], [data-panel=failures] [data-key=\"calib.ready\"]"  # noqa: E501
            rows_js = f"[...document.querySelectorAll('{sel}')].map(e => [e.querySelector('.pill').textContent, e.querySelector('.pill').dataset.tone, e.querySelector('.note')?.textContent || ''])"  # noqa: E501
            await b.eval(
                "window.exgs.callouts().log.onLog({f: {text: '[node 1 WARNING] WARNING calibration invalid, Arm and MissionBegin are refused (repeats every 10 s): gyro: ok, accel: parameters out of range, mag: ok'}})"  # noqa: E501
            )
            await b.sleep(0.8)
            cal = await b.eval(rows_js)
            assert cal == [["ok", "ok", ""]] * 3 + [["ready", "ok", ""]], cal
            assert await b.eval("document.querySelector('[data-testid=calib-hint]').hidden") is True
            # Attitude stops (dropout from t=1.5 s sim time): its values go stale and show an age
            await b.sleep(5.0)
            late = await b.eval(JS_PANEL_STATES)
            assert late["attitude"]["stale"] > 0 and any(a for a in late["attitude"]["ages"]), late["attitude"]
            assert late["position"]["stale"] == 0  # Position keeps arriving
            badge = await b.eval("document.querySelector('[data-panel=attitude] .badge-age').textContent")
            assert badge.startswith("stale"), badge
            # LIVE / REPLAY switch: transport bar appears, data panels follow the (empty) replay view
            assert await b.eval("document.querySelector('[data-panel=transport]').hidden") is True
            await b.eval(
                "[...document.querySelectorAll('header.top .toggle button')].find(x => x.textContent === 'REPLAY').click()"  # noqa: E501
            )
            await b.sleep(0.8)
            assert await b.eval("document.body.dataset.view") == "replay"
            assert await b.eval("document.querySelector('[data-panel=transport]').hidden") is False
            assert await b.eval("document.querySelector('[data-panel=position] .val').dataset.state") == "missing"
            assert await b.eval("document.querySelector('[data-testid=source-banner]').dataset.kind") == "replay"
            await b.eval(
                "[...document.querySelectorAll('header.top .toggle button')].find(x => x.textContent === 'LIVE').click()"  # noqa: E501
            )
            await b.sleep(1.0)
            assert await b.eval("document.querySelector('[data-panel=position] .val').dataset.state") == "fresh"
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_browser_narrow_window_has_no_horizontal_scroll():
    async with running(make_app()) as rig:
        async with browser_check.Browser(390, 844, 9342) as b:
            await b.goto(rig.url() + "/")
            await b.sleep(1.5)
            assert await b.eval("window.exgs.layout.view()") == "bench"  # no device presets any more
            assert await b.eval("document.documentElement.scrollWidth") <= 390
            assert await b.eval("document.querySelectorAll('.panel').length") >= 10
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_browser_recording_controls_and_console_do_not_break():
    async with running(make_app()) as rig:
        async with browser_check.Browser(1500, 1000, 9343) as b:
            await b.goto(rig.url() + "/")
            await b.sleep(1.0)
            assert await b.eval("document.querySelector('[data-testid=rec-state]').dataset.active") == "false"
            await b.eval("document.querySelector('[data-testid=rec-start]').click()")
            await b.sleep(1.5)
            assert await b.eval("document.querySelector('[data-testid=rec-state]').dataset.active") == "true"
            assert (await b.eval("document.querySelector('[data-testid=rec-state]').textContent")).startswith("REC ")
            assert await b.eval("document.querySelector('[data-testid=rec-stop]').hidden") is False
            await b.eval("document.querySelector('[data-testid=rec-stop]').click()")  # no confirmation
            await b.sleep(2.0)
            assert await b.eval("document.querySelector('[data-testid=rec-state]').dataset.active") == "false"
            await b.eval("window.exgs.dock.open('recording')")
            await b.eval("document.querySelector('[data-panel=recording] .sect-title button').click()")
            await b.sleep(0.8)
            assert "exlog" in await b.eval("document.querySelector('[data-panel=recording] .loglist').textContent")
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_browser_views_and_source_banner_real_and_simulation():
    for sim, kind in ((False, "real"), (True, "sim")):
        async with running(make_app(simulation=sim)) as rig:
            async with browser_check.Browser(1366, 768, 9344) as b:
                await b.goto(rig.url() + "/")
                await b.sleep(1.5)
                assert await b.eval("document.querySelector('[data-testid=source-banner]').dataset.kind") == kind
                assert await b.eval("window.exgs.source().inputsLocked") is False
                for view, dock in (("flight", "rail"), ("bench", "open")):
                    await b.eval(f"window.exgs.layout.set('{view}')")
                    await b.sleep(0.5)
                    titles = await b.eval("[...document.querySelectorAll('.group-title')].map(e => e.textContent)")
                    assert titles == ["Vehicle", "Guidance", "Mission"], titles
                    assert await b.eval("window.exgs.dock.mode()") == dock
                    assert await b.eval("!!document.querySelector('#panels [data-panel=view3d]')")
                # Flight at 1366x768: every group fits without scrolling
                await b.eval("window.exgs.layout.set('flight')")
                await b.sleep(1.0)
                over = await b.eval(
                    "[...document.querySelectorAll('.group')].map(g => g.scrollHeight - g.clientHeight)"
                )
                assert max(over) <= 2, over
                # the radio stays mounted across view changes (a held lease is not dropped by a view change)
                assert await b.eval("document.querySelectorAll('[data-panel=radio]').length") == 1
                assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_browser_replay_banner_names_the_log_and_locks_inputs():
    from pathlib import Path

    from tests.support_logs import make_log

    app = make_app()
    log = make_log(Path(app.library.directory), "full", 20.0)
    async with running(app) as rig:
        async with browser_check.Browser(1366, 768, 9345) as b:
            await b.goto(rig.url() + "/")
            await b.sleep(1.0)
            await b.eval("window.exgs.dock.open('recording')")
            await b.eval("document.querySelector('[data-panel=recording] .sect-title button').click()")
            await b.sleep(0.8)
            await b.eval("[...document.querySelectorAll('[data-panel=recording] .loglist button')][0].click()")
            await b.sleep(2.5)
            assert await b.eval("document.body.dataset.view") == "replay"
            src = await b.eval("window.exgs.source()")
            assert src["kind"] == "replay" and src["inputsLocked"] is True
            assert src["lockReason"] == "replay: inputs locked"
            assert src["details"][0] == log.name and src["recorded"] == "SIMULATION", src
            assert await b.eval("document.querySelector('[data-panel=transport]').hidden") is False
            for tab in ("radio", "requests", "telecommands"):
                await b.eval(f"window.exgs.dock.open('{tab}')")
                await b.sleep(0.3)
                assert await b.eval("document.querySelector('[data-testid=dock-lock]').hidden") is False, tab
            await b.eval("window.exgs.dock.open('radio')")
            await b.sleep(0.3)
            assert await b.eval("document.querySelector('[data-panel=radio] .rc-take').disabled") is True
            assert await b.eval("document.querySelector('[data-testid=radio-lock]').hidden") is False
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_browser_source_switch_disconnects_and_reconnects():
    async with running(make_app()) as rig:
        async with browser_check.Browser(1500, 1000, 9346) as b:
            await b.goto(rig.url() + "/")
            await b.sleep(1.5)
            await b.eval("window.exgs.dock.open('link')")
            sw = "[data-panel=link] .src-sw[data-source=fake]"
            assert await b.eval(f"document.querySelector('{sw}').getAttribute('aria-checked')") == "true"
            await b.eval(f"document.querySelector('{sw}').click()")
            await b.sleep(0.3)
            assert await b.eval("!!document.querySelector('.rc-modal')")  # disconnect needs a confirmation
            await b.eval("document.querySelector('.rc-modal .rc-ok').click()")
            await b.sleep(1.2)
            row = "[data-panel=link] .src-row[data-source=fake] .src-state"
            assert await b.eval(f"document.querySelector('{row}').textContent") == "DISABLED by user"
            assert rig.app.sources[0].status().state == "disabled"
            assert "DISABLED by user" in await b.eval("document.querySelector('#statusbar .sb-links').textContent")
            await b.eval(f"document.querySelector('{sw}').click()")  # on: no confirmation
            await b.sleep(1.5)
            assert rig.app.sources[0].status().state == "connected"
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_browser_tuning_graphs_live_history_reload_and_replay():
    from pathlib import Path

    from tests.support_logs import make_log

    app = make_app(speed=1.0)
    make_log(Path(app.library.directory), "full", 20.0)
    async with running(app) as rig:
        async with browser_check.Browser(1600, 1000, 9347) as b:
            await b.goto(rig.url() + "/")
            await b.sleep(1.0)
            await b.eval("window.exgs.layout.set('tuning')")
            await b.sleep(4.0)
            d = await b.eval("window.exgs.tuning.debug()")
            assert len(d["graphs"]) == 8 and await b.eval("document.querySelectorAll('.tn-card canvas').length") == 8
            assert d["points"] > 500 and d["frameMs"] < 30, d
            for sig in ("att.roll", "rate.x", "imu.gyro.x", "cal.gyro.x", "act.thrust", "std.att.N", "err.pos.U"):
                assert sig in d["supplied"], sig
            n_rate = d["rings"]["rate.x"]
            assert n_rate > 250  # 100 Hz Attitude, every sample (mode "all"), not the 20 Hz latest stream
            # a reload keeps the history (the core's plot buffer)
            await b.goto(rig.url() + "/")
            await b.sleep(2.0)
            d2 = await b.eval("window.exgs.tuning.debug()")
            assert d2["rings"].get("rate.x", 0) >= n_rate, (n_rate, d2["rings"].get("rate.x"))
            # pause freezes the window
            await b.eval("document.querySelector('.tn-bar .pause').click()")
            assert (await b.eval("window.exgs.tuning.debug()"))["paused"] is True
            await b.eval("document.querySelector('.tn-bar .pause').click()")
            # replay: history comes from the log
            await b.eval("window.exgs.dock.open('recording')")
            await b.eval("document.querySelector('[data-panel=recording] .sect-title button').click()")
            await b.sleep(0.8)
            await b.eval("[...document.querySelectorAll('[data-panel=recording] .loglist button')][0].click()")
            await b.sleep(1.5)
            await b.eval("document.querySelector('[data-panel=transport] .play').click()")
            await b.sleep(3.0)
            d3 = await b.eval("window.exgs.tuning.debug()")
            assert d3["rings"].get("att.roll", 0) > 50, d3["rings"]
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_browser_tuning_graphs_after_a_replay_seek_show_the_whole_history():
    """Seeking ahead (also while paused) must not leave a gap: the graphs hold the log's data up to the new position."""
    from pathlib import Path

    from tests.support_logs import make_log

    app = make_app(speed=1.0)
    make_log(Path(app.library.directory), "full", 20.0)
    async with running(app) as rig:
        async with browser_check.Browser(1600, 1000, 9348) as b:
            await b.goto(rig.url() + "/")
            await b.sleep(1.0)
            await b.eval("window.exgs.layout.set('tuning')")
            await b.eval("window.exgs.dock.open('recording')")
            await b.eval("document.querySelector('[data-panel=recording] .sect-title button').click()")
            await b.sleep(0.8)
            await b.eval("[...document.querySelectorAll('[data-panel=recording] .loglist button')][0].click()")
            await b.sleep(1.5)
            slider = "document.querySelector('[data-panel=transport] input[type=range]')"
            for frac in (0.25, 0.8, 0.5):  # forward, forward again, back
                set_value = f"s.value = {int(frac * 1000)}; s.dispatchEvent(new Event('input'));"
                await b.eval(f"(() => {{ const s = {slider}; {set_value} }})()")
                await b.sleep(2.0)
                d = await b.eval("window.exgs.tuning.debug()")
                lo, hi = d["ranges"]["att.roll"]
                n = d["rings"]["att.roll"]
                span = hi - lo
                assert span > 20.0 * frac * 0.9, (frac, d["ranges"]["att.roll"])
                assert n > 100 * span * 0.9, (frac, n, span)  # 100 Hz Attitude at full rate, no gap
            assert not [m for m in b.console if "EXCEPTION" in m], b.console
