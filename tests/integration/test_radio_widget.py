"""Virtual radio widget end to end (T9): a real headless browser against the core with the FakeSource.

Skipped when no Chromium-based browser is installed. The RC controller runs for real, the FakeSource
echoes channel 5 as MissionState.mission_index and records what reached it (`received_rc`)."""

import asyncio
import sys
import time
from pathlib import Path

import pytest

from .test_rc_loop import make_rc_app, make_two_source_app, rc_running

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import browser_check  # noqa: E402

needs_browser = pytest.mark.skipif(browser_check.find_browser() is None, reason="no Chromium-based browser")

DEBUG = "window.exgs.radio.debug()"


async def center(b, sel):
    return await b.eval(
        f"(() => {{ const e = document.querySelector({sel!r}); e.scrollIntoView({{block: 'center'}});"
        " const r = e.getBoundingClientRect();"
        " return [r.left + r.width / 2, r.top + r.height / 2, r.top, r.height]; })()"
    )


async def mouse(b, kind, x, y):
    await b.cmd(
        "Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left" if kind != "mouseMoved" else "none",
        clickCount=1, buttons=0 if kind == "mouseReleased" else 1,
    )  # fmt: skip


async def press(b, sel):
    x, y, *_ = await center(b, sel)
    await mouse(b, "mouseMoved", x, y)
    await mouse(b, "mousePressed", x, y)


async def let_go(b, sel):
    x, y, *_ = await center(b, sel)
    await mouse(b, "mouseReleased", x, y)


async def click(b, sel, frac=0.5):
    x, y, top, height = await center(b, sel)
    y = top + height * frac
    await mouse(b, "mouseMoved", x, y)
    await mouse(b, "mousePressed", x, y)
    await mouse(b, "mouseReleased", x, y)


async def until(pred, timeout=3.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if asyncio.iscoroutine(v):
            v = await v
        if v:
            return v
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def ctl(cid, inner=""):
    return f"[data-panel=radio] .rc-ctl[data-id={cid}] {inner}".strip()


async def take_control(b, rig):
    await b.eval("document.querySelector('[data-panel=radio] .rc-take').click()")
    await b.sleep(0.5)
    assert rig.app.rc.status().lease is not None


@needs_browser
async def test_take_control_operate_every_control_kind_and_see_the_core_reported_state():
    async with rc_running() as rig, browser_check.Browser(1500, 1000, 9351) as b:
        rc = rig.app.rc
        await b.goto(rig.url() + "/")
        await b.sleep(1.2)
        d = await b.eval(DEBUG)
        assert d["banner"] == "NOT SENDING" and d["tone"] == "idle" and d["role"] == "IDLE"
        assert d["take"]["disabled"] is False and set(d["controls"]) == {"SA", "SB", "SC", "SD", "SE", "S1"}
        assert not any(c["enabled"] for c in d["controls"].values())  # read-only until control is taken

        await take_control(b, rig)
        d = await b.eval(DEBUG)
        assert d["banner"].startswith("MASTER · sending to fake · 25 Hz"), d["banner"]
        assert d["release"]["hidden"] is False and d["role"] == "MASTER" and d["warning"] == ""

        # toggle2: displayed position is the one the core reports
        await click(b, ctl("SA", "button.rc-switch"))
        await until(lambda: rc.status().controls["SA"] == 1, what="SA set in the core")
        await b.sleep(0.4)
        assert (await b.eval(DEBUG))["controls"]["SA"]["pos"] == 1
        assert (await b.eval(DEBUG))["controls"]["SA"]["us"] == "2000 µs"
        await until(lambda: rig.app.sources[0].received_rc and rig.app.sources[0].received_rc[-1].ch_us[4] == 2000)

        # switch3: top, middle and bottom zones
        for frac, pos in ((0.1, 2), (0.5, 1), (0.9, 0)):
            await click(b, ctl("SB", ".rc-switch3"), frac)
            await until(lambda pos=pos: rc.status().controls["SB"] == pos, what=f"SB={pos}")
        await b.sleep(0.4)
        assert (await b.eval(DEBUG))["controls"]["SB"]["pos"] == 0

        # slider
        await b.eval(
            "(() => { const i = document.querySelector('.rc-ctl[data-id=S1] input'); i.value = 700;"
            " i.dispatchEvent(new Event('input', {bubbles: true})); })()"
        )
        await until(lambda: abs(rc.status().controls["S1"] - 0.7) < 1e-9, what="S1 set")
        await b.sleep(0.6)
        assert (await b.eval(DEBUG))["controls"]["S1"]["pos"] == pytest.approx(0.7)

        # momentary: held (refreshed) while the pointer is down, released on pointerup
        await press(b, ctl("SE", "button"))
        await b.sleep(0.8)  # longer than the core's 300 ms lapse: only the 100 ms repeat keeps it pressed
        assert rc.status().controls["SE"] == 1 and (await b.eval(DEBUG))["controls"]["SE"]["pos"] == 1
        await let_go(b, ctl("SE", "button"))
        await until(lambda: rc.status().controls["SE"] == 0, 1.0, "SE released")
        # ... and on window blur
        await press(b, ctl("SE", "button"))
        await b.sleep(0.3)
        assert rc.status().controls["SE"] == 1
        await b.eval("window.dispatchEvent(new Event('blur'))")
        await until(lambda: rc.status().controls["SE"] == 0, 1.0, "SE released on blur")
        await let_go(b, ctl("SE", "button"))

        # release button: silence and defaults
        await b.eval("document.querySelector('[data-panel=radio] .rc-release').click()")
        await until(lambda: rc.status().lease is None, what="released")
        await b.sleep(0.4)
        d = await b.eval(DEBUG)
        assert d["banner"] == "NOT SENDING" and d["controls"]["SA"]["pos"] == 0 and d["controls"]["S1"]["pos"] == 0
        assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_locked_sd_padlock_needs_a_one_second_hold_and_can_be_relocked():
    async with rc_running() as rig, browser_check.Browser(1500, 1000, 9352) as b:
        rc = rig.app.rc
        await b.goto(rig.url() + "/")
        await b.sleep(1.2)
        await take_control(b, rig)
        d = (await b.eval(DEBUG))["controls"]["SD"]
        assert d["padlock"] is True and d["lock"] == "locked" and d["enabled"] is False
        sd_toggle_hidden = "document.querySelector('.rc-ctl[data-id=SD] .rc-switch').hidden"
        assert await b.eval(sd_toggle_hidden) is True
        # only SD has a padlock: it comes from `lock = true` in the radio config
        assert await b.eval("document.querySelectorAll('.rc-padlock').length") == 1

        await press(b, ctl("SD", ".rc-padlock"))
        await b.sleep(0.5)
        await let_go(b, ctl("SD", ".rc-padlock"))  # released after 0.5 s: stays locked
        await b.sleep(1.0)
        assert rc.status().locks["SD"]["locked"] is True

        await press(b, ctl("SD", ".rc-padlock"))
        await until(lambda: rc.status().locks["SD"]["locked"] is False, 2.5, "unlock after the 1 s hold")
        await let_go(b, ctl("SD", ".rc-padlock"))
        await b.sleep(0.5)
        d = (await b.eval(DEBUG))["controls"]["SD"]
        assert d["padlock"] is False and d["lock"] == "unlocked" and d["enabled"] is True
        assert (await b.eval("document.querySelector('.rc-ctl[data-id=SD] .rc-lockinfo').textContent")).startswith(
            "unlocked · "
        )  # noqa: E501
        await click(b, ctl("SD", "button.rc-switch"))
        await until(lambda: rc.status().controls["SD"] == 1, what="SD on while unlocked")
        await until(lambda: rig.app.sources[0].received_rc[-1].ch_us[7] == 2000)
        await b.eval("document.querySelector('.rc-ctl[data-id=SD] .rc-relock').click()")
        await until(lambda: rc.status().controls["SD"] == 0 and rc.status().locks["SD"]["locked"], what="relock")
        await b.sleep(0.5)
        d = (await b.eval(DEBUG))["controls"]["SD"]
        assert d["padlock"] is True and d["pos"] == 0
        assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_closing_the_tab_stops_sending_within_one_second():
    async with rc_running() as rig, browser_check.Browser(1500, 1000, 9353) as b:
        await b.goto(rig.url() + "/")
        await b.sleep(1.2)
        await take_control(b, rig)
        await until(lambda: len(rig.app.sources[0].received_rc) > 5, what="frames")
        await b.goto("about:blank")  # the page and its WebSocket go away
        t0 = time.monotonic()
        await until(lambda: rig.app.rc.status().lease is None, 1.0, "lease end after closing the tab")
        await asyncio.sleep(0.15)
        n = len(rig.app.sources[0].received_rc)
        await asyncio.sleep(0.4)
        assert len(rig.app.sources[0].received_rc) == n and time.monotonic() - t0 < 1.5
        assert rig.app.rc.status().controls == rig.app.rc.cfg.default_state()


@needs_browser
async def test_slave_shows_handset_positions_read_only():
    app = make_rc_app(rc_role="mirror", profile="radio")
    async with rc_running(app) as rig, browser_check.Browser(1500, 1000, 9354) as b:
        await b.goto(rig.url() + "/")
        await b.sleep(2.0)
        d = await b.eval(DEBUG)
        assert d["banner"].startswith("SLAVE · showing the Pocket · read-only"), d["banner"]
        assert d["tone"] == "mirror" and d["role"] == "SLAVE" and d["warning"] == ""
        assert d["take"]["disabled"] is False  # master is the user's choice (denied below: mirror-only)
        assert not any(c["enabled"] for c in d["controls"].values())
        # the fake handset sends 1500 us on the switch channels: the middle position / half slider
        assert d["controls"]["SB"]["pos"] == 1 and d["controls"]["S1"]["pos"] == 0.5
        assert d["controls"]["SB"]["us"] == "1500 µs"
        # a click on a read-only control changes nothing, and the core still sends nothing
        await click(b, ctl("SB", ".rc-switch3"), 0.1)
        await b.sleep(0.3)
        assert (await b.eval(DEBUG))["controls"]["SB"]["pos"] == 1
        assert rig.app.rc.status().lease is None and not rig.app.sources[0].received_rc
        await b.eval("document.querySelector('[data-panel=radio] .rc-take').click()")
        await b.sleep(0.5)
        assert rig.app.rc.status().lease is None and not rig.app.sources[0].received_rc, "no RC-capable source: denied"
        assert (await b.eval(DEBUG))["role"] == "SLAVE"
        assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_slave_to_master_with_pocket_warning_and_back():
    app = make_two_source_app()  # a radio-profile source provides the Pocket's RC_STATE
    async with rc_running(app) as rig, browser_check.Browser(1500, 1000, 9358) as b:
        await b.goto(rig.url() + "/")
        await until(lambda: app.rc.status().mode == "slave", what="slave")
        await b.sleep(1.0)
        d = await b.eval(DEBUG)
        assert d["role"] == "SLAVE" and d["warning"] == "" and d["take"]["disabled"] is False
        assert not any(c["enabled"] for c in d["controls"].values())
        assert await b.eval("document.querySelector('[data-panel=radio] .rc-take').textContent") == "Switch to master"
        assert all(not s.received_rc for s in app.sources)
        await take_control(b, rig)
        await b.sleep(0.5)
        d = await b.eval(DEBUG)
        assert d["role"] == "MASTER" and d["banner"].startswith("MASTER · sending to pocket, usb")
        assert d["warning"] == "Pocket also active: the radio rejects ground-station controls"
        assert any(c["enabled"] for c in d["controls"].values())
        assert app.sources[0].received_rc and app.sources[1].received_rc and not app.sources[2].received_rc
        rel = "document.querySelector('[data-panel=radio] .rc-release').textContent"
        assert await b.eval(rel) == "Release (back to slave)"
        await b.eval("document.querySelector('[data-panel=radio] .rc-release').click()")
        await until(lambda: app.rc.status().mode == "slave", what="back to slave")
        await b.sleep(0.5)
        d = await b.eval(DEBUG)
        assert d["role"] == "SLAVE" and d["warning"] == ""
        assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_switching_to_replay_while_controlling_prompts_and_releases():
    async with rc_running() as rig, browser_check.Browser(1500, 1000, 9355) as b:
        rc = rig.app.rc
        await b.goto(rig.url() + "/")
        await b.sleep(1.2)
        await take_control(b, rig)
        replay_btn = (
            "[...document.querySelectorAll('header.top .toggle button')].find(x => x.textContent === 'REPLAY').click()"
        )
        await b.eval(replay_btn)
        await b.sleep(0.3)
        assert await b.eval("!!document.querySelector('.rc-modal')") is True
        assert await b.eval("document.body.dataset.view") == "live"  # nothing happens before the answer
        await b.eval("document.querySelector('.rc-modal .rc-cancel').click()")
        await b.sleep(0.5)
        assert await b.eval("document.body.dataset.view") == "live" and rc.status().lease is not None
        assert await b.eval("!!document.querySelector('.rc-modal')") is False
        await b.eval(replay_btn)
        await b.sleep(0.3)
        await b.eval("document.querySelector('.rc-modal .rc-ok').click()")
        await until(lambda: rc.status().lease is None, 2.0, "release by the prompt")
        await b.sleep(0.5)
        assert await b.eval("document.body.dataset.view") == "replay"
        d = await b.eval(DEBUG)
        assert d["banner"].startswith("REPLAY") and d["take"]["disabled"] is True
        assert not any(c["enabled"] for c in d["controls"].values())
        assert rc.status().controls == rc.cfg.default_state()
        assert not [m for m in b.console if "EXCEPTION" in m], b.console
