"""Mission event list: the core's change-filtered packet buffer (live and whole-log replay read), the get_events op and
the panel in a real browser (docs/gui.md "Mission events")."""

import sys
from pathlib import Path

from exvectr_gs.core.event_history import EventFilter, EventHistory
from exvectr_gs.packets.defs.host import ConsoleText
from exvectr_gs.packets.defs.vehicle import MissionPhase, MissionState

from ..support_logs import T0_NS
from ..support_mission_log import S, make_mission_log
from .test_api import Client, make_app, running
from .test_ops_gui import needs_browser, open_page
from .test_radio_widget import until

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import browser_check  # noqa: E402


def ms(sec, phase=MissionPhase.Idle, tm=0, src="usb"):
    return MissionState(t=T0_NS + int(sec * S), src=src, mission_phase=phase, t_mission=tm, mission_index=0)


def test_event_filter_keeps_changes_and_a_regular_sample():
    f = EventFilter()
    kept = [f.accept(ms(s * 0.25)) for s in range(0, 12)]  # unchanged for 3 s at 4 Hz
    assert kept.count(True) == 3  # the first one, then one per second
    assert f.accept(ms(3.0, MissionPhase.Hover))  # a change is kept at once
    assert f.accept(ms(3.0, src="link"))  # per source
    assert f.accept(ConsoleText(t=T0_NS, src="usb", text="a")) and f.accept(ConsoleText(t=T0_NS, src="usb", text="a"))


def test_event_history_is_bounded_and_ordered():
    h = EventHistory(capacity=5)
    for i in range(20):
        h.update(ConsoleText(t=T0_NS + i, src="usb", text=f"line {i}"))
    assert [p.text for p in h.packets()] == [f"line {i}" for i in range(15, 20)]
    h.clear()
    assert h.packets() == []


async def test_get_events_replay_reads_the_whole_log_at_once(tmp_path):
    app = make_app()
    log = make_mission_log(app.library.directory)
    async with running(app, sources=False) as rig:
        c = await Client.connect(rig)
        empty = await c.ok("get_events", view="replay")
        assert empty["packets"] == []  # no log open
        await app.replayer.open(log.name)
        res = await c.ok("get_events", view="replay")  # position still at the start
        await c.close()
    assert res["truncated"] is False
    types = [p["type"] for p in res["packets"]]
    assert "ConsoleText" in types and "GsEvent" in types and "MissionState" in types
    ts = [p["t"] for p in res["packets"]]
    assert ts == sorted(ts) and max(ts) > T0_NS / 1e9 + 20  # events up to the end, not just up to the position
    phases = [p["f"]["mission_phase"] for p in res["packets"] if p["type"] == "MissionState"]
    assert {"Idle", "Hover", "Ascent", "Completed"} <= set(phases)
    assert len([p for p in res["packets"] if p["type"] == "VehicleStatus"]) < 12  # reduced to the changes


async def test_get_events_live_buffer(tmp_path):
    app = make_app()
    async with running(app, sources=False) as rig:
        app.event_history.update(ConsoleText(t=T0_NS, src="usb", text="[node 1 WARNING] hi"))
        c = await Client.connect(rig)
        res = await c.ok("get_events")
        await c.close()
    assert [p["f"]["text"] for p in res["packets"]] == ["[node 1 WARNING] hi"]


@needs_browser
async def test_events_panel_replay_shows_everything_and_seeks(tmp_path):
    app = make_app()
    log = make_mission_log(app.library.directory)
    async with running(app, sources=False) as rig, browser_check.Browser(1500, 1300, 9374) as b:
        await app.replayer.open(log.name)
        await open_page(b, rig)
        await b.eval("document.querySelector('[data-dv=replay]').click()")
        rows_js = "[...document.querySelectorAll('[data-testid=ev-row]')].map(r => ({t: r.dataset.t, state: r.dataset.state, tx: r.querySelector('.evtx').textContent, tm: r.querySelector('.evtm').textContent}))"  # noqa: E501
        await until(lambda: b.eval(f"({rows_js}).length > 8"), what="event rows", timeout=15)
        rows = await b.eval(rows_js)
        texts = [r["tx"] for r in rows]
        # everything of the recording is there although the replay stands at the start
        assert "Mission begin (T0)" in texts and "Mission end: Completed" in texts and "Failsafe set" in texts
        assert any(r["state"] == "upcoming" for r in rows if r["tx"] == "Mission end: Completed")
        begin = next(r for r in rows if r["tx"] == "Mission begin (T0)")
        assert begin["tm"] == "T0"
        head = await b.eval("document.querySelector('[data-testid=ev-head]').textContent")
        assert head.startswith("Mission 1, begun at ")
        # a click seeks the replay to the event
        await b.eval(
            "[...document.querySelectorAll('[data-testid=ev-row]')].find(r => r.textContent.includes('Mission end: Completed')).click()"  # noqa: E501
        )
        await until(
            lambda: b.eval(
                "document.querySelector('[data-testid=ev-row][data-state=current]')?.textContent.includes('Mission end')"  # noqa: E501
            ),
            what="current row after the seek",
            timeout=10,
        )
        assert abs(app.replayer.status().t - (T0_NS + 20 * S)) < S
        # the category filter hides rows and is remembered
        await b.eval("document.querySelector('[data-testid=ev-cat-mission]').click()")
        await until(
            lambda: b.eval(
                "![...document.querySelectorAll('[data-testid=ev-row]')].some(r => r.dataset.cat === 'mission')"
            ),
            what="filter",
        )


@needs_browser
async def test_events_panel_clear_hides_rows_new_events_show_and_show_cleared_restores(tmp_path):
    app = make_app()
    for i in range(3):
        app.bus.publish(ConsoleText(t=T0_NS + i * S, src="usb", text=f"[node 1 WARNING] old {i}"))
    async with running(app, sources=False) as rig, browser_check.Browser(1500, 1300, 9375) as b:
        await open_page(b, rig)
        tx = "[...document.querySelectorAll('[data-testid=ev-row] .evtx')].map(e => e.textContent)"
        click = "document.querySelector('[data-testid=%s]').click()"
        await until(lambda: b.eval(f"({tx}).length >= 3"), what="old rows", timeout=15)
        await b.eval(click % "ev-clear")
        await until(lambda: b.eval(f"({tx}).length === 0"), what="cleared")
        assert await b.eval("document.querySelector('[data-testid=ev-showcleared]').textContent") == "Show cleared (3)"
        app.bus.publish(ConsoleText(t=T0_NS + 10 * S, src="usb", text="[node 1 WARNING] fresh"))
        await until(lambda: b.eval(f"({tx}).length === 1"), what="new event after the clear", timeout=10)
        assert (await b.eval(tx)) == ["fresh"]
        await b.eval(click % "ev-showcleared")
        await until(lambda: b.eval(f"({tx}).length === 4"), what="restored")
