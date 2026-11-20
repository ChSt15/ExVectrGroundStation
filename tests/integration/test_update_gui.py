"""Software tab end to end in a headless browser, against temporary git repositories (no network). Check, header
badge, commit list, hold-to-apply, the progress output and the guard reasons. Skipped without a Chromium browser."""

import sys
from pathlib import Path

import pytest

from exvectr_gs.packets.defs.vehicle import VehicleStatus
from exvectr_gs.update import gitops

from .test_api import running
from .test_radio_widget import let_go, press, until
from .test_update_api import make_app, push_new, repos  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import browser_check  # noqa: E402

needs_browser = pytest.mark.skipif(browser_check.find_browser() is None, reason="no Chromium-based browser")
pytestmark = pytest.mark.browser

APPLY = "[data-panel=update] [data-testid=up-apply]"
CHECK = "[data-panel=update] [data-testid=up-check]"


async def text(b, sel):
    return await b.eval(f"document.querySelector({sel!r}).textContent")


async def open_tab(b, rig):
    await b.goto(rig.url() + "/")
    await b.sleep(1.2)
    await b.eval("window.exgs.dock.open('update')")
    await until(lambda: b.eval("!!window.exgs.update.debug().status"), what="update.status")


@needs_browser
async def test_check_shows_commits_badge_and_hold_applies(repos, monkeypatch):  # noqa: F811
    monkeypatch.setattr(gitops, "PIP_COMMAND", ["true"])
    app = make_app(repos.gs)
    app.restart_delay_s = 0
    async with running(app, sources=False) as rig, browser_check.Browser(1500, 1200, 9371) as b:
        await open_tab(b, rig)
        assert "add a.txt" in await text(b, "[data-testid=up-version]")
        assert await b.eval(f"document.querySelector({APPLY!r}).disabled") is True  # no check yet
        assert "check for updates first" in await text(b, "[data-testid=up-why]")
        push_new(repos, "b.txt")
        await b.eval(f"document.querySelector({CHECK!r}).click()")
        await until(
            lambda: b.eval("document.querySelectorAll('[data-testid=up-commits] li').length === 1"), 8, "commits"
        )
        assert "new b.txt" in await text(b, "[data-testid=up-commits]")
        await until(lambda: b.eval("!document.querySelector('[data-testid=update-badge]').hidden"), what="badge")
        assert "1 new" in await text(b, "[data-testid=update-badge]")
        await until(lambda: b.eval(f"document.querySelector({APPLY!r}).disabled === false"), what="apply enabled")
        # a short hold applies nothing
        await press(b, APPLY)
        await b.sleep(0.4)
        await let_go(b, APPLY)
        await b.sleep(0.8)
        assert not (repos.gs / "b.txt").exists()
        # a 1 s hold applies
        await press(b, APPLY)
        await until(lambda: (repos.gs / "b.txt").exists(), 6, "update applied")
        await let_go(b, APPLY)
        await until(
            lambda: b.eval("document.querySelector('[data-testid=up-out]').textContent.includes('[merge]')"),
            what="progress",
        )
        assert app._restart_requested


@needs_browser
async def test_guard_reason_disables_the_button(repos):  # noqa: F811
    app = make_app(repos.gs)
    async with running(app, sources=False) as rig, browser_check.Browser(1500, 1200, 9372) as b:
        push_new(repos, "b.txt")
        await open_tab(b, rig)
        await b.eval(f"document.querySelector({CHECK!r}).click()")
        await until(lambda: b.eval(f"document.querySelector({APPLY!r}).disabled === false"), 8, "enabled")
        app.store.update(VehicleStatus(t=app.clock.now(), src="fake", armed=True))
        await until(lambda: b.eval(f"document.querySelector({APPLY!r}).disabled === true"), 4, "disabled when armed")
        assert "ARMED" in await text(b, "[data-testid=up-why]")


@needs_browser
async def test_view_only_host_cannot_check(repos):  # noqa: F811
    async with (
        running(make_app(repos.gs, control_hosts=("10.99.99.99",)), sources=False) as rig,
        browser_check.Browser(1500, 1200, 9373) as b,
    ):
        await open_tab(b, rig)
        assert await b.eval(f"document.querySelector({CHECK!r}).disabled") is True
        assert "view-only" in await text(b, "[data-testid=up-why]")
