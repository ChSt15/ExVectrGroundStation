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
        assert await b.eval(f"document.querySelector({(P + ' [data-testid=home-pos]')!r})") is None
        assert not [m for m in b.console if "EXCEPTION" in m], b.console
