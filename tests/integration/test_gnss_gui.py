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
async def test_gnss_panel_shows_position_fix_home_and_a_passing_altitude_cross_check(tmp_path):
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
        # NED velocity row is the NWU row with W and U negated
        assert await b.eval(f"document.querySelectorAll({(P + ' [data-row=gnss-vel-ned] td')!r}).length") == 4
        # home position and the epoch of the status and of the copy
        await until(
            lambda: b.eval(
                f"document.querySelector({(P + ' [data-testid=home-pos]')!r}).textContent.includes('simulation')"
            ),
            timeout=6,
            what="home position",
        )
        await until(
            lambda: b.eval(
                f"document.querySelector({(P + ' [data-testid=home-epoch]')!r}).textContent.trim() === '1 / 1'"
            ),
            timeout=6,
            what="home epoch",
        )
        assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_gnss_panel_compact_in_flight_view_shows_tiles_and_one_check_line(tmp_path):
    async with rig_with(tmp_path) as rig, browser_check.Browser(1366, 768, 9392) as b:
        await open_page(b, rig)
        await b.eval("window.exgs.layout.set('flight')")
        await until(
            lambda: b.eval(f"document.querySelector({(P + ' [data-key=gnss-fix] .val')!r})?.dataset.state === 'fresh'"),
            timeout=8,
            what="fresh GNSS",
        )
        assert "3D" in await b.eval(txt("[data-key=gnss-fix]"))
        assert "OK" in await b.eval(txt("[data-key=gnss-cc-line]"))
        assert await b.eval(f"document.querySelectorAll({(P + ' .section')!r}).length") == 0
        assert await b.eval(f"document.querySelector({(P + ' [data-key=gnss-lat]')!r})") is None
