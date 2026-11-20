"""Requests panel end to end: a real headless browser against the core with the FakeSource as the answering vehicle.

Skipped when no Chromium-based browser is installed. Hold-to-confirm (1 s), the danger modal (confirm disabled for
2 s) and the display of ACK / timeout states, all from what the core reports."""

import sys
from pathlib import Path

import pytest

from .test_radio_widget import click, let_go, press, until
from .test_request_api import make_app, req_running

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import browser_check  # noqa: E402

needs_browser = pytest.mark.skipif(browser_check.find_browser() is None, reason="no Chromium-based browser")

DEBUG = "window.exgs.requests.debug()"


def btn(action, target=1):
    return f"[data-panel=requests] .rq-btn[data-action={action}][data-target='{target}']"


def row(action, target=1):
    return f"[data-panel=requests] .rq-row[data-key='{action}@{target}'] .rq-state"


async def open_panel(b, rig):
    await b.goto(rig.url() + "/")
    await b.sleep(1.0)
    await b.eval("document.querySelector('[data-panel=requests]').dataset.collapsed = 'false'")  # shipped collapsed
    await until(lambda: b.eval(f"{DEBUG}.rows.length > 0"), what="whitelist loaded")
    await b.sleep(0.7)  # one status push for the routes


async def text(b, sel):
    return await b.eval(f"document.querySelector({sel!r}).textContent")


@needs_browser
async def test_hold_one_second_sends_and_the_ack_shown_comes_from_the_core():
    async with req_running() as rig, browser_check.Browser(1500, 1200, 9361) as b:
        fake = rig.app.sources[0]
        await open_panel(b, rig)
        groups = await b.eval(
            "[...document.querySelectorAll('[data-panel=requests] .rq-group h4')].map(e => e.textContent)"
        )
        assert groups == ["Calibration", "Vehicle", "Link"]
        assert await b.eval(f"document.querySelector({btn('CALIB_APPLY')!r}).disabled") is False
        # a click and a short hold send nothing
        await click(b, btn("CALIB_APPLY"))
        await press(b, btn("CALIB_APPLY"))
        await b.sleep(0.5)
        await let_go(b, btn("CALIB_APPLY"))
        await b.sleep(0.8)
        assert len(fake.received_requests) == 0 and await text(b, row("CALIB_APPLY")) == ""
        # holding for more than 1 s sends exactly once
        await press(b, btn("CALIB_APPLY"))
        await until(lambda: len(fake.received_requests) == 1, timeout=3, what="request after the hold")
        await let_go(b, btn("CALIB_APPLY"))
        await until(lambda: text(b, row("CALIB_APPLY")), what="state text")
        await until(
            lambda: b.eval(f"document.querySelector({row('CALIB_APPLY')!r}).textContent.startsWith('ACK DONE')"),
            what="ACK",
        )
        assert await b.eval(f"document.querySelector({row('CALIB_APPLY')!r}).dataset.tone") == "ok"
        assert len(fake.received_requests) == 1


@needs_browser
async def test_rejected_status_and_timeout_are_shown_as_reported():
    async with req_running(make_app(request_status=2)) as rig, browser_check.Browser(1500, 1200, 9362) as b:
        await open_panel(b, rig)
        await press(b, btn("SAVE_CONFIG"))
        await until(
            lambda: b.eval(
                f"document.querySelector({row('SAVE_CONFIG')!r}).textContent.startsWith('ACK REJECTED_STATE')"
            ),
            timeout=4,
            what="rejected ACK",
        )
        assert await b.eval(f"document.querySelector({row('SAVE_CONFIG')!r}).dataset.tone") == "bad"
        await let_go(b, btn("SAVE_CONFIG"))
    async with req_running(make_app(request_drop_first=99)) as rig, browser_check.Browser(1500, 1200, 9363) as b:
        await open_panel(b, rig)
        await press(b, btn("SAVE_CONFIG"))
        await until(
            lambda: b.eval(f"document.querySelector({row('SAVE_CONFIG')!r}).textContent.startsWith('sending')"),
            timeout=4,
            what="sending",
        )
        await let_go(b, btn("SAVE_CONFIG"))
        assert await b.eval(f"document.querySelector({btn('SAVE_CONFIG')!r}).disabled") is True  # one in flight
        await until(
            lambda: b.eval(f"document.querySelector({row('SAVE_CONFIG')!r}).textContent.startsWith('TIMEOUT')"),
            timeout=9,
            what="timeout",
        )
        assert await b.eval(f"document.querySelector({btn('SAVE_CONFIG')!r}).disabled") is False


@needs_browser
async def test_danger_action_needs_hold_then_modal_with_a_confirm_disabled_for_two_seconds():
    async with req_running() as rig, browser_check.Browser(1500, 1200, 9364) as b:
        fake = rig.app.sources[0]
        await open_panel(b, rig)
        sel = btn("LINK_UPLOAD_MODE", 2)
        assert await b.eval(f"document.querySelector({sel!r}).dataset.confirm") == "danger"
        await press(b, sel)
        await until(lambda: b.eval("!!document.querySelector('[data-testid=danger-modal]')"), timeout=3, what="modal")
        await let_go(b, sel)
        assert await text(b, ".rq-danger-text") == (
            "This acts on a link module (the Router or the ship radio node): its radio link stops or restarts. "
            "The module always obeys, even in flight."
        )
        assert len(fake.received_requests) == 0  # the hold alone sent nothing
        ok = "[data-testid=danger-modal] .rc-ok"
        assert await b.eval(f"document.querySelector({ok!r}).disabled") is True
        # a click on the disabled confirm button does nothing
        await b.eval(f"document.querySelector({ok!r}).click()")
        await b.sleep(0.7)
        assert (
            await b.eval("!!document.querySelector('[data-testid=danger-modal]')") and len(fake.received_requests) == 0
        )
        await until(
            lambda: b.eval(f"document.querySelector({ok!r}).disabled === false"),
            timeout=3,
            what="confirm enabled after 2 s",
        )
        await b.eval(f"document.querySelector({ok!r}).click()")
        await until(lambda: len(fake.received_requests) == 1, what="the request after the confirm")
        assert fake.received_requests[0].target == 2
        assert await b.eval("!!document.querySelector('[data-testid=danger-modal]')") is False
        await until(
            lambda: b.eval(
                f"document.querySelector({row('LINK_UPLOAD_MODE', 2)!r}).textContent.startsWith('ACK DONE')"
            ),
            what="ACK",
        )


@needs_browser
async def test_cancel_and_escape_close_the_modal_without_sending():
    async with req_running() as rig, browser_check.Browser(1500, 1200, 9365) as b:
        fake = rig.app.sources[0]
        await open_panel(b, rig)
        sel = btn("LINK_UPLOAD_MODE", 3)
        await press(b, sel)
        await until(lambda: b.eval("!!document.querySelector('[data-testid=danger-modal]')"), timeout=3, what="modal")
        await let_go(b, sel)
        await b.eval("document.querySelector('[data-testid=danger-modal] .rc-cancel').click()")
        assert await b.eval("!!document.querySelector('[data-testid=danger-modal]')") is False
        await press(b, sel)
        await until(lambda: b.eval("!!document.querySelector('[data-testid=danger-modal]')"), timeout=3, what="modal")
        await let_go(b, sel)
        await b.cmd("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
        await b.sleep(0.2)
        assert await b.eval("!!document.querySelector('[data-testid=danger-modal]')") is False
        await b.sleep(2.5)
        assert len(fake.received_requests) == 0


@needs_browser
async def test_view_only_host_and_replay_view_cannot_send():
    async with (
        req_running(make_app(control_hosts=("10.99.99.99",))) as rig,
        browser_check.Browser(1500, 1200, 9366) as b,
    ):
        await open_panel(b, rig)
        assert await b.eval(f"document.querySelector({btn('CALIB_APPLY')!r}).disabled") is True
        assert "view-only" in await b.eval(f"document.querySelector({btn('CALIB_APPLY')!r}).dataset.why")
    async with req_running() as rig, browser_check.Browser(1500, 1200, 9367) as b:
        await open_panel(b, rig)
        assert await b.eval(f"document.querySelector({btn('CALIB_APPLY')!r}).disabled") is False
        await b.eval(
            "[...document.querySelectorAll('header.top .toggle button')]"
            ".find((x) => x.textContent === 'REPLAY').click()"
        )
        await until(lambda: b.eval("document.body.dataset.view === 'replay'"), what="replay view")
        assert await b.eval("document.querySelector('[data-panel=requests]').hidden") is True
        assert await b.eval(f"document.querySelector({btn('CALIB_APPLY')!r}).disabled") is True
        assert "replay" in await b.eval(f"document.querySelector({btn('CALIB_APPLY')!r}).dataset.why")
