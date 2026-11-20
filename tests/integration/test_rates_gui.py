"""Data rates panel and the status bar figures in a real headless browser: pty fake vehicles (real frame path)
and a replay. Skipped when no Chromium-based browser is installed."""

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

ROWS = (
    "[...document.querySelectorAll('[data-testid=rates-row]')].map(r => ({type: r.dataset.type, path: r.dataset.path,"
    " source: r.dataset.source, state: r.dataset.state, tone: r.dataset.tone,"
    " cells: [...r.children].map(c => c.textContent)}))"
)


@needs_browser
async def test_rates_panel_and_status_bar_label_the_paths(tmp_path):
    async with rig_with(tmp_path) as rig, browser_check.Browser(1500, 1300, 9372) as b:
        await open_page(b, rig, ["rates"])

        async def has_rows():
            rows = await b.eval(ROWS)
            return {r["path"] for r in rows} >= {"usb direct", "air", "router local"}

        await until(has_rows, what="rows of all three paths", timeout=15)
        rows = await b.eval(ROWS)
        usb = [r for r in rows if r["source"] == "usb"]
        assert usb and all(r["path"] == "usb direct" for r in usb)
        pose = next(r for r in usb if r["type"] == "POSE")
        assert pose["cells"][5] == "100" and pose["cells"][3] not in ("—", "0.00")  # nominal rate_usb of POSE
        link = [r for r in rows if r["source"] == "link"]
        assert any(r["path"] == "air" for r in link) and any(r["path"] == "router local" for r in link)
        assert all("n/a" not in r["cells"][6] for r in rows)  # live frames: wire bytes are measured
        # sorted by path (air first), totals row after each path's streams
        paths = [r["path"] for r in rows]
        assert paths == sorted(paths, key=["air", "usb direct", "router local"].index)
        totals = await b.eval("[...document.querySelectorAll('[data-testid=rates-total]')].map(r => r.dataset.path)")
        assert totals == [p for p in ["air", "usb direct", "router local"] if p in totals] and "air" in totals
        # air utilisation bar and the Router's LINK_STATS next to it
        assert await b.eval("document.querySelector('[data-testid=air-bar]').dataset.known") == "true"
        assert float(await b.eval("document.querySelector('.rt-fill').style.width.replace('%','')")) > 0
        # both directions are drawn: the downlink measured, the uplink labelled as an estimate
        await until(
            lambda: b.eval("document.querySelector('[data-testid=air-bar-up]')?.dataset.known === 'true'"),
            what="uplink bar",
        )
        assert await b.eval("document.querySelector('[data-testid=air-bar-up]').dataset.estimated") == "true"
        assert await b.eval("document.querySelector('[data-testid=air-bar]').dataset.estimated") == "false"
        assert "estimated" in await b.eval("document.querySelector('[data-testid=air-bar-up] .rt-bar-t').textContent")
        # ship node UART chip (VEHICLE_STATUS UART_PEER_SEEN, information only): the fake vehicle reports it seen
        await until(
            lambda: b.eval(
                "document.querySelector('#statusbar [data-key=uart_peer_seen] .sb-v')?.textContent === 'seen'"
            ),
            what="ship node UART chip",
        )
        # status bar: the serial-hop label with its tooltip, and the air figure in B/s
        label = await b.eval("document.querySelector('[data-testid=serial-hop-label]').textContent")
        assert label == "frames/s on the serial hop:"
        tip = await b.eval("document.querySelector('[data-testid=serial-hop-label]').title")
        assert "serial" in tip and "RC_STATE" in tip and "not the radio rate" in tip.lower()
        assert "pkt/s" not in await b.eval("document.querySelector('#statusbar').textContent")
        await until(
            lambda: b.eval("/air: \\d+ B\\/s/.test(document.querySelector('[data-testid=air-rate]').textContent)"),
            what="air B/s in the status bar",
        )


@needs_browser
async def test_rates_panel_in_replay_uses_replay_time(tmp_path):
    app = make_app()
    log = make_log(app.library.directory, "full", 12.0)
    async with api_running(app, sources=False) as rig, browser_check.Browser(1500, 1300, 9373) as b:
        await app.replayer.open(log.name)
        app.replayer.seek(T0_NS + 8_000_000_000)
        await open_page(b, rig, ["rates"])
        await b.eval("document.querySelector('[data-dv=replay]').click()")
        await until(lambda: b.eval(f"({ROWS}).length > 0"), what="replay rows", timeout=10)
        rows = await b.eval(ROWS)
        att = next(r for r in rows if r["type"] == "Attitude")
        assert att["path"] == "simulated" and att["cells"][6] == "n/a" and float(att["cells"][3]) > 1
        assert att["state"] == "fresh"  # age is measured against the replay clock, not the wall clock
        note = await b.eval("document.querySelector('[data-testid=rates-note]').textContent")
        assert note.startswith("Replay:")
        assert await b.eval("document.querySelector('[data-testid=air-rate]').dataset.known") == "false"
