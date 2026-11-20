"""Audio callouts and notifications in a real headless browser against the pty fake vehicle (a scripted mission).
speechSynthesis and AudioContext are replaced by fakes injected before the page loads, so the spoken list, the styles
and the notifications can be checked. Real speech cannot be checked headless. Skipped without Chromium."""

import json
import sys
from pathlib import Path

from ..support_logs import T0_NS, make_log
from .test_api import make_app
from .test_api import running as api_running
from .test_ops_gui import needs_browser, open_page, rig_with
from .test_radio_widget import until

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import browser_check  # noqa: E402

FAKES = """
window.__spoken = []; window.__beeps = [];
class U { constructor(t) { this.text = t; } }
window.SpeechSynthesisUtterance = U;
Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
  getVoices: () => [{ voiceURI: 'fake-1', name: 'Fake voice', lang: 'en-US' }],
  speak(u) { if (u.text) window.__spoken.push({text: u.text, rate: u.rate, pitch: u.pitch, volume: u.volume});
             setTimeout(() => u.onend && u.onend(), 30); },
  cancel() {},
}});
window.AudioContext = class { constructor() { this.currentTime = 0; this.destination = {}; this.state = 'running'; }
  createOscillator() { return { frequency: {}, connect() {}, start() { window.__beeps.push('tone'); }, stop() {} }; }
  createGain() { return { gain: { setValueAtTime(v) { if (v > 0) window.__gain = v; } }, connect() {} }; } };
"""


def preset(settings: dict | None) -> str:
    # tiny scenario: altitude thresholds the fake mission (peak 5 m) actually crosses
    s = {"altitude": {"levels": [2, 4], "hysteresis_m": 0.2}}
    s.update(settings or {})
    js = json.dumps(json.dumps(s))
    return f"if (!localStorage.getItem('exgs.callouts')) localStorage.setItem('exgs.callouts', {js});"


async def start(b, rig, settings=None):
    await b.cmd("Page.addScriptToEvaluateOnNewDocument", source=FAKES + preset(settings))
    await open_page(b, rig, ["callouts"])


SPOKEN = "window.__spoken.map(s => s.text)"
HISTORY = "[...document.querySelectorAll('[data-testid=co-history] li')].map(l => l.textContent.slice(9))"


@needs_browser
async def test_mission_callouts_are_spoken_with_styles_and_shown(tmp_path):
    async with rig_with(tmp_path) as rig, browser_check.Browser(1366, 768, 9381) as b:
        await start(b, rig)
        # audio is off until the user enables it; text notifications work regardless
        assert await b.eval("document.querySelector('[data-testid=audio-state]').dataset.tone") == "bad"
        assert await b.eval("document.querySelector('[data-testid=audio-bar]').hidden") is False
        await b.eval("document.querySelector('[data-testid=toast-audio-enable]').click()")
        assert await b.eval("document.querySelector('[data-testid=audio-bar]').hidden") is True
        assert await b.eval("document.querySelector('[data-testid=audio-state]').textContent") == "audio on"
        assert await b.eval("localStorage.getItem('exgs.callouts.audio')") == "1"
        await until(lambda: b.eval(f"{SPOKEN}.includes('Landed')"), timeout=45, what="the whole mission spoken")
        spoken = await b.eval(SPOKEN)
        for want in ("Ascent", "Belly flop", "Landing", "Landed", "Altitude 2", "Altitude 4"):
            assert want in spoken, (want, spoken)
        assert spoken.index("Ascent") < spoken.index("Belly flop") < spoken.index("Landing") < spoken.index("Landed")
        # up and down: Altitude 4, then later Altitude 2 again on the way down
        assert spoken.count("Altitude 4") == 2 and spoken.count("Altitude 2") == 2
        first = await b.eval("window.__spoken.find(s => s.text === 'Ascent')")
        assert first["rate"] == 1.0 and first["pitch"] == 1.0 and abs(first["volume"] - 0.9 * 0.8) < 1e-6
        # the same events are in the notification history
        await until(lambda: b.eval(f"{HISTORY}.includes('Landed')"), what="history updated")
        hist = await b.eval(HISTORY)
        assert "Landed" in hist and "Belly flop" in hist
        assert "Simulation" in hist  # announced at connect, before audio was enabled: shown, not spoken
        # master volume slider: percentage shown, applied to the next callout, persisted; Test volume
        await b.eval(
            "(() => { const v = document.querySelector('[data-testid=co-volume]'); v.value = 50;"
            " v.dispatchEvent(new Event('input')); })()"
        )
        assert await b.eval("document.querySelector('[data-testid=co-volume-pct]').textContent") == "50 %"
        assert json.loads(await b.eval("localStorage.getItem('exgs.callouts')"))["volume"] == 0.5
        n = len(await b.eval(SPOKEN))
        await b.eval("document.querySelector('[data-testid=co-volume-test]').click()")
        await until(lambda: b.eval(f"{SPOKEN}.length > {n}"), what="volume test speech")
        last = await b.eval("window.__spoken[window.__spoken.length - 1]")
        assert last["text"] == "Volume test" and abs(last["volume"] - 0.9 * 0.5) < 1e-6
        assert await b.eval("window.__beeps.length") >= 2 and abs(await b.eval("window.__gain") - 0.35 * 0.5) < 1e-6
        # failure test: failure style (rate 1.3, pitch 0.7, volume 1.0 x master) preceded by the two-tone beep
        await b.eval("document.querySelector('[data-testid=co-test-failure]').click()")
        await until(lambda: b.eval(f"{SPOKEN}.includes('Position failure')"), what="failure test speech")
        f = await b.eval("window.__spoken.find(s => s.text === 'Position failure')")
        assert f["rate"] == 1.3 and f["pitch"] == 0.7 and abs(f["volume"] - 0.5) < 1e-6
        # voice list from getVoices(), speaker icon state
        assert await b.eval("document.querySelector('[data-testid=co-voice]').options.length") == 2
        assert await b.eval("document.querySelector('[data-testid=callouts-icon]').dataset.muted") == "false"
        # reset to defaults restores 80 %
        await b.eval("document.querySelector('[data-testid=co-reset]').click()")
        assert await b.eval("document.querySelector('[data-testid=co-volume-pct]').textContent") == "80 %"


@needs_browser
async def test_toasts_do_not_cover_status_bar_or_dock_at_1366x768(tmp_path):
    async with rig_with(tmp_path) as rig, browser_check.Browser(1366, 768, 9382) as b:
        await start(b, rig)
        await b.eval("window.exgs.layout.set('flight')")
        await b.sleep(0.5)
        for k in ("failure", "link", "state", "phase", "altitude", "countdown"):
            await b.eval(f"window.exgs.callouts().test({k!r})")
        rects = await b.eval(
            "(() => { const r = (e) => { const x = e.getBoundingClientRect();"
            " return [x.left, x.top, x.right, x.bottom]; };"
            " const toasts = [...document.querySelectorAll('.toast')].map(r);"
            " const sel = ['#top', '#statusbar', '#dock', '.dock-tab', '.dock-head'];"
            " const others = sel.flatMap(s => [...document.querySelectorAll(s)].map(e => [s, r(e)]));"
            " const t0 = r(document.querySelector('.toast'));"
            " const at = document.elementFromPoint((t0[0] + t0[2]) / 2, (t0[1] + t0[3]) / 2);"
            " const hit = at.closest('.toast') !== null;"
            " return {toasts, others, hit, n: toasts.length, w: innerWidth, h: innerHeight}; })()"
        )
        assert 1 <= rects["n"] <= 5, rects["n"]  # six were shown: at most five visible
        assert rects["hit"] is True  # toasts are on top of the panels, not hidden under them

        def overlap(a, c):
            return a[0] < c[2] and c[0] < a[2] and a[1] < c[3] and c[1] < a[3]

        for t in rects["toasts"]:
            assert t[2] <= rects["w"] and t[3] <= rects["h"]
            for sel, o in rects["others"]:
                assert not overlap(t, o), (sel, t, o)
        # also with the dock closed to its rail
        await b.eval("window.exgs.dock.set('rail')")
        await b.sleep(0.3)
        await b.eval("window.exgs.callouts().test('phase')")
        r2 = await b.eval(
            "(() => { const r = (e) => { const x = e.getBoundingClientRect();"
            " return [x.left, x.top, x.right, x.bottom]; };"
            " return {t: [...document.querySelectorAll('.toast')].map(r), dock: r(document.querySelector('#dock')),"
            " top: r(document.querySelector('#top'))}; })()"
        )
        for t in r2["t"]:
            assert not overlap(t, r2["dock"]) and not overlap(t, r2["top"])


@needs_browser
async def test_audio_off_still_notifies_and_master_off_is_silent(tmp_path):
    async with rig_with(tmp_path) as rig, browser_check.Browser(1366, 768, 9383) as b:
        await start(b, rig)  # audio never enabled
        await until(lambda: b.eval(f"{HISTORY}.includes('Ascent')"), timeout=30, what="Ascent notification")
        assert await b.eval(SPOKEN) == []  # nothing spoken without the user's enable
        assert await b.eval("document.querySelectorAll('.toast').length") >= 1
        assert await b.eval("document.querySelector('[data-testid=callouts-icon]').dataset.audio") == "off"
    (tmp_path / "m").mkdir()
    async with rig_with(tmp_path / "m") as rig, browser_check.Browser(1366, 768, 9384) as b:
        await start(b, rig, {"master": False})
        await b.eval("document.querySelector('[data-testid=toast-audio-enable]')?.click()")
        await b.eval("window.exgs.callouts().enableAudio()")
        await b.sleep(12)  # the scripted mission passes Ascent meanwhile
        assert await b.eval(SPOKEN) == []
        assert await b.eval(HISTORY) == [""]
        assert await b.eval("document.querySelectorAll('.toast').length") == 0
        assert await b.eval("document.querySelector('[data-testid=callouts-icon]').dataset.muted") == "true"


async def _replay_run(app, b, rig, settings, seek_after):
    log = make_log(app.library.directory, "full", 30.0)
    await app.replayer.open(log.name)
    app.replayer.seek(T0_NS + 5_000_000_000)
    await start(b, rig, settings)
    await b.eval("window.exgs.callouts().enableAudio()")
    await b.eval("document.querySelector('[data-dv=replay]').click()")
    await b.sleep(0.5)
    app.replayer.set_speed(2.0)
    app.replayer.play()
    await b.sleep(3.5)  # replay time 5 s -> 12 s: Ascent at 6 s passes
    spoken = await b.eval(SPOKEN)
    if seek_after:
        n = len(spoken)
        app.replayer.pause()
        app.replayer.seek(T0_NS + 27_000_000_000)  # skips Belly flop (16 s) and Landing (26 s)
        await b.sleep(1.5)
        return spoken, await b.eval(SPOKEN), n
    return spoken, None, None


@needs_browser
async def test_callouts_are_off_in_replay_by_default(tmp_path):
    app = make_app()
    async with api_running(app, sources=False) as rig, browser_check.Browser(1366, 768, 9385) as b:
        spoken, _, _ = await _replay_run(app, b, rig, None, False)
        assert spoken == []
        assert await b.eval("document.querySelectorAll('.toast').length") == 0


@needs_browser
async def test_replay_follows_the_timeline_and_a_seek_is_silent(tmp_path):
    app = make_app()
    async with api_running(app, sources=False) as rig, browser_check.Browser(1366, 768, 9386) as b:
        before, after, n = await _replay_run(app, b, rig, {"speakInReplay": True}, True)
        assert "Ascent" in before  # normal speed: the recorded timeline is followed
        assert "Belly flop" not in after and "Landing" not in after and "Landed" not in after
        assert after == before  # nothing at all was said for the seek
