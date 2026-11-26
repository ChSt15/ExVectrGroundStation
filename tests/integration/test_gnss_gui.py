"""GNSS panel in a real headless browser against the pty fake vehicle (exvectrnet-1 on USB: GNSS 0x83 at 10 Hz,
HOME_POSITION, VEHICLE_STATUS.home_epoch). The cross-check logic itself is covered by web/test/gnss_logic.test.js.
Skipped when no Chromium-based browser is installed."""

import sys
from pathlib import Path

from .test_ops_gui import needs_browser, open_page, rig_with
from .test_radio_widget import until

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import browser_check  # noqa: E402

P = "[data-panel=gnss]"


def txt(sel: str) -> str:
    return f"document.querySelector({(P + ' ' + sel)!r}).textContent"


@needs_browser
async def test_gnss_panel_shows_position_fix_accuracy_and_a_passing_altitude_cross_check(tmp_path):
    async with rig_with(tmp_path) as rig, browser_check.Browser(1500, 1500, 9391) as b:
        await open_page(b, rig)
        await until(
            lambda: b.eval(f"document.querySelector({(P + ' [data-key=gnss-fix] .val')!r})?.dataset.state === 'fresh'"),
            timeout=8,
            what="fresh GNSS",
        )
        assert "3D" in await b.eval(txt("[data-key=gnss-fix]"))
        assert "12" in await b.eval(txt("[data-key=gnss-sats]"))  # satellites of the fake
        lat = await b.eval(txt("[data-key=gnss-lat]"))
        assert "51.5" in lat  # about 51.54 deg N, shown in degrees
        # NWU velocity row (label + N, W, U)
        assert await b.eval(f"document.querySelectorAll({(P + ' [data-row=gnss-vel-nwu] td')!r}).length") == 4
        assert "OK" in await b.eval(txt("[data-key=gnss-cc-line]"))
        # accuracy tiles: horizontal and vertical come with the radio's GNSS info
        for k in ("gnss-acc-h", "gnss-acc-v"):
            assert await b.eval(txt(f"[data-key={k}] .v")) not in ("", "—")
        # the home as reported (the fake repeats HOME_POSITION) and the GNSS position relative to it (NWU, metres)
        await until(lambda: b.eval(f"/epoch 1/.test({txt('[data-testid=home-pos]')})"), timeout=8, what="home line")
        assert "(simulation)" in await b.eval(txt("[data-testid=home-pos]")) and "51.5" in await b.eval(
            txt("[data-testid=home-pos]")
        )
        row = await b.eval(
            f"[...document.querySelectorAll({(P + ' [data-row=gnss-pos-home] td')!r})].map((c) => c.textContent)"
        )
        assert len(row) == 4 and all(c.strip() not in ("", "—", "n/s") for c in row[:3]), row
        assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_gnss_panel_in_replay_recomputes_metres_relative_to_home_from_the_recording(tmp_path):
    """Open a recording in REPLAY: home and the metres come from the logged HomePosition and Gps, so they agree with the
    NWU position the fake vehicle logged at the same time (to the earth model of the fake and the 0.1 s alignment)."""
    from tests.integration.test_api import make_app, running
    from tests.support_logs import make_log
    from tests.unit.test_home_replay import nwu_from_home

    app = make_app(speed=1.0)
    make_log(Path(app.library.directory), "dropouts", 30.0)
    async with running(app) as rig, browser_check.Browser(1500, 1500, 9393) as b:
        await b.goto(rig.url() + "/")
        await b.sleep(1.0)
        await b.eval("window.exgs.dock.open('recording')")
        await b.eval("document.querySelector('[data-panel=recording] .sect-title button').click()")
        await b.sleep(0.8)
        await b.eval("[...document.querySelectorAll('[data-panel=recording] .loglist button')][0].click()")
        await b.sleep(1.5)
        assert await b.eval("document.body.dataset.view") == "replay"
        await b.eval("document.querySelector('[data-panel=transport] .play').click()")
        await until(
            lambda: b.eval(f"/epoch 1/.test({txt('[data-testid=home-pos]')})"), timeout=12, what="replayed home"
        )
        await b.eval("document.querySelector('[data-panel=transport] .play').click()")  # pause
        await b.sleep(0.5)
        sel = P + " [data-row=gnss-pos-home] td"
        js = f"[...document.querySelectorAll({sel!r})].slice(0, 3).map((c) => parseFloat(c.textContent))"
        row = await b.eval(js)
        latest = {e.packet.TYPE: e.packet for e in app.replay_store.snapshot()}
        want = nwu_from_home(latest["Gps"], latest["HomePosition"])  # independent Python reference of the formula
        assert len(row) == 3 and all(abs(a - w) < 0.06 for a, w in zip(row, want, strict=True)), (row, want)
        pos = latest["Position"].pos  # and the fake's own NWU position at about the same time
        assert all(abs(a - p) < 1.0 + 0.01 * abs(p) for a, p in zip(row, pos, strict=True)), (row, pos)
        assert not [m for m in b.console if "EXCEPTION" in m], b.console
