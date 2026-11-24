"""Mission status bar, Requests and Vehicle panels, Simulation testing page, raw sensor panel and link power block
in a real headless browser, against the pty fake vehicles (exvectrnet-1). Skipped without a Chromium browser."""

import contextlib
import sys
import tempfile
from pathlib import Path

import pytest

from exvectr_gs.core.app import CoreApp
from exvectr_gs.core.config import parse_config

from ..support_pty import FakeVehiclePty
from .test_api import running as api_running
from .test_radio_widget import let_go, press, until

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import browser_check  # noqa: E402

needs_browser = pytest.mark.skipif(browser_check.find_browser() is None, reason="no Chromium-based browser")

TX_RATES = {"VEHICLE_STATUS": 5.0, "LINK_STATS_TX": 4.0, "LINK_STATS_RX": 4.0, "LINK_CONFIG": 4.0}


@contextlib.asynccontextmanager
async def rig_with(tmp_path, usb_kw=None, tx_kw=None, with_usb=True, requests=None):
    v1 = FakeVehiclePty(tmp_path / "usb", speed=2.0, profile="exvectrnet-1", hb_hz=10.0, **(usb_kw or {}))
    v2 = FakeVehiclePty(
        tmp_path / "radio",
        speed=2.0,
        profile="exvectrnet-1",
        role="txmodule",
        hb_hz=10.0,
        rates=TX_RATES,
        **(tx_kw or {}),
    )
    v1.start()
    v2.start()
    sources = [
        {"id": "link", "kind": "link_module", "match": {"device": str(v2.link)}, "rc_role": "mirror", "poll_s": 0.05}
    ]
    if with_usb:
        sources.insert(
            0,
            {
                "id": "usb",
                "kind": "vehicle_usb",
                "match": {"device": str(v1.link)},
                "rc_role": "control",
                "poll_s": 0.05,
            },
        )
    app = CoreApp(
        parse_config(
            {
                "core": {"wire_profile": "exvectrnet-1", "source_priority": [s["id"] for s in sources]},
                "server": {"bind": "127.0.0.1", "port": 1},
                "source": sources,
                "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
                **({"requests": {"action": requests}} if requests else {}),
            }
        )
    )
    try:
        async with api_running(app) as rig:
            rig.v1, rig.v2, rig.app = v1, v2, app
            yield rig
    finally:
        v1.stop()
        v2.stop()


async def open_page(b, rig, panels=()):
    await b.goto(rig.url() + "/")
    await b.sleep(1.5)
    for p in panels:
        await b.eval(f"window.exgs.dock.open('{p}')")  # a tab of the Operations dock


async def text(b, sel):
    return await b.eval(f"document.querySelector({sel!r}).textContent")


def cell(key):
    return f"#statusbar [data-key={key}] .sb-v"


@needs_browser
async def test_status_bar_shows_only_reported_values_and_follows_a_simulation_toggle(tmp_path):
    async with rig_with(tmp_path) as rig, browser_check.Browser(1500, 1300, 9371) as b:
        await open_page(b, rig, ["vehicle"])
        await until(
            lambda: b.eval("document.querySelector('#statusbar').dataset.state === 'fresh'"), what="fresh status"
        )
        # the simulation flag is in the data-source banner (the bar does not repeat it)
        kind = "document.querySelector('[data-testid=source-banner]').dataset.kind"
        sim0 = await b.eval(kind)
        assert sim0 in ("sim", "real")
        assert await b.eval("document.querySelector('#statusbar [data-key=simulation]')") is None
        labels = await b.eval(
            "[...document.querySelectorAll('#statusbar .sb-cell:not([hidden]) .sb-l')].map(e => e.textContent)"
        )
        for want in (
            "Mode",
            "Mission",
            "Motors",
            "Failsafe",
            "Failures",
            "Ground station",
            "Command source",
            "Command age",
            "Last request",
            "Sensor state (raw)",
        ):
            assert want in labels, want
        assert await b.eval(f"document.querySelector({cell('motors')!r}).parentElement.dataset.loud") == "true"
        assert "0x" in await text(b, cell("failure_bits")) and "(" in await text(b, cell("failure_bits"))
        links = await b.eval("[...document.querySelectorAll('#statusbar .sb-link')].map(e => e.textContent)")
        assert any(x.strip().startswith("usb") for x in links) and any(x.strip().startswith("link") for x in links)
        # the bar and the banner show only what the vehicle reports: flip the fake vehicle's simulation flag
        rig.v1.sim_state = sim0 != "sim"
        want_kind = "real" if sim0 == "sim" else "sim"
        want = "REAL SENSORS" if sim0 == "sim" else "SIMULATION"
        await until(lambda: b.eval(f"{kind} === {want_kind!r}"), timeout=5, what="reported flip")
        assert await text(b, "[data-testid=tc-sim]") == f"vehicle reports: {want}"


REQ_ACTIONS = [
    {"action": "ZERO_ESTIMATORS", "target": 1, "label": "Zero estimators", "group": "Vehicle",
     "arg_f_min": 1.0, "arg_f_max": 60.0, "arg_f_default": 10.0, "arg_f_name": "window, s"},
    {"action": "SET_STOP_ALTITUDE", "target": 1, "label": "Stop altitude", "group": "Vehicle settings",
     "arg_f_min": 1.0, "arg_f_max": 1e6, "arg_f_default": 20.0},
]  # fmt: skip


async def set_field(b, sel, value):
    await b.eval(
        f"(() => {{ const e = document.querySelector({sel!r}); e.value = {value!r}; "
        "e.dispatchEvent(new Event('input')); })()"
    )


@needs_browser
async def test_zero_estimators_is_a_request_with_a_window_field_and_the_vehicles_ack(tmp_path):
    async with (
        rig_with(tmp_path, usb_kw={"request_rules": True}, requests=REQ_ACTIONS) as rig,
        browser_check.Browser(1500, 1300, 9372) as b,
    ):
        rig.v1.request_latched_session = rig.app.engine.session  # the vehicle's latched command session
        await open_page(b, rig, ["requests"])
        row = "[data-panel=requests] .rq-row[data-key='ZERO_ESTIMATORS@1']"
        btn, fld = f"{row} .rq-btn", f"{row} .tc-float"
        await until(lambda: b.eval(f"!!document.querySelector({fld!r})"), what="row built")
        assert await b.eval(f"document.querySelector({fld!r}).value") == "10"  # default window
        assert not await b.eval("!!document.querySelector('[data-panel=requests] [data-key=\"SET_STOP_ALTITUDE@1\"]')")
        await set_field(b, fld, "99")  # out of range: disabled
        assert await b.eval(f"document.querySelector({btn!r}).disabled") is True
        await set_field(b, fld, "3")
        await until(lambda: b.eval(f"!document.querySelector({btn!r}).disabled"), what="enabled")
        await press(b, btn)
        await until(lambda: len(rig.v1.received_requests) >= 1, timeout=3, what="request after the hold")
        await let_go(b, btn)
        req = rig.v1.received_requests[0]
        assert (req["action"], req["target"], req["arg_f"]) == (12, 1, 3.0) and req["session"] != 0
        await until(
            lambda: b.eval(f"document.querySelector({row + ' .rq-state'!r}).textContent.startsWith('ACK DONE')"),
            timeout=4,
            what="ACK DONE",
        )


MP_ACTIONS = [
    {"action": "SET_MISSION_PARAM", "target": 1, "label": f"p{i}", "group": "Mission parameters", "arg_i": i,
     "arg_f_min": lo, "arg_f_max": hi, "arg_f_default": d}
    for i, (lo, hi, d) in enumerate(
        [(0.5, 10.0, 7.0), (2.0, 1000.0, 120.0), (0.0, 600.0, 30.0), (0.0, 600.0, 10.0), (0.0, 600.0, 15.0),
         (0.0, 600.0, 15.0)], start=1)
]  # fmt: skip


@needs_browser
async def test_mission_parameters_panel_sends_all_changed_fields_paced_with_each_result_in_words(tmp_path):
    async with (
        rig_with(tmp_path, usb_kw={"request_rules": True}, requests=REQ_ACTIONS + MP_ACTIONS) as rig,
        browser_check.Browser(1500, 1300, 9373) as b,
    ):
        rig.v1.request_latched_session = rig.app.engine.session
        await open_page(b, rig, ["vehicle"])
        inp = lambda k: f"[data-testid=mp-input-{k}]"  # noqa: E731
        st = lambda k: f"[data-testid=mp-state-{k}]"  # noqa: E731
        send = "[data-testid=mp-send]"
        # the fields show the vehicle readback (MISSION_PARAMS, MISSION); nothing changed: Send disabled
        await until(
            lambda: b.eval(
                "document.querySelector('[data-testid=mp-vehicle-hover_hold_s]').textContent === 'vehicle: 30.0'"
            ),
            what="readback",
        )
        assert await b.eval(f"document.querySelector({send!r}).disabled") is True
        assert await b.eval(f"document.querySelector({inp('ascent_speed')!r}).value") == "7"
        # an out-of-range value is flagged and Send stays disabled; the apogee warning appears below 90 m
        await set_field(b, inp("ascent_speed"), "11")
        assert "allowed: 0.5 to 10 m/s" in await text(b, "[data-testid=mp-invalid-ascent_speed]")
        assert await b.eval(f"document.querySelector({send!r}).disabled") is True
        await set_field(b, inp("apogee_altitude"), "80")
        assert await text(b, "[data-testid=mp-apogee-warning]") == "below 90 m: no bellyflop, the flight ends in RTH"
        await set_field(b, inp("ascent_speed"), "5")
        await set_field(b, inp("hover_hold_s"), "45")
        await set_field(b, inp("stop_altitude"), "35")
        await until(lambda: b.eval(f"!document.querySelector({send!r}).disabled"), what="Send enabled")
        assert await text(b, send) == "Send (4)"
        await b.eval(f"document.querySelector({send!r}).click()")  # one plain click sends all four, one by one
        await until(
            lambda: b.eval("document.querySelector('[data-testid=mp-summary]').textContent === 'all 4 parameters set'"),
            timeout=15,
            what="summary",
        )
        for k in ("ascent_speed", "apogee_altitude", "hover_hold_s", "stop_altitude"):
            assert await text(b, st(k)) == "set", k
        sent = [(r["action"], r["arg_i"], r["arg_f"], r["req_id"]) for r in rig.v1.received_requests]
        assert [x[:3] for x in sent] == [(24, 1, 5.0), (24, 2, 80.0), (24, 3, 45.0), (13, 0, 35.0)]
        assert len({x[3] for x in sent}) == 4  # a new req_id per field
        assert rig.v1.mission_params[1] == 5.0 and rig.v1.stop_altitude == 35.0
        await until(
            lambda: b.eval(
                "document.querySelector('[data-testid=mp-vehicle-ascent_speed]').textContent === 'vehicle: 5.0'"
            ),
            what="readback updated",
        )
        assert await b.eval(f"document.querySelector({send!r}).disabled") is True
        # refusal: shown per row in words, the summary counts it
        rig.v1.request_on_ground = False
        await b.sleep(0.6)
        await set_field(b, inp("hover_hold_s"), "50")
        await b.eval(f"document.querySelector({send!r}).click()")
        await until(
            lambda: b.eval(
                "document.querySelector('[data-testid=mp-summary]').textContent === '0 of 1 set, 1 refused'"
            ),
            timeout=8,
            what="refusal summary",
        )
        assert await text(b, st("hover_hold_s")) == "refused: motors armed or in flight"
        # reset to defaults fills the inputs and sends nothing
        n = len(rig.v1.received_requests)
        await b.eval("document.querySelector('[data-testid=mp-reset]').click()")
        assert await b.eval(f"document.querySelector({inp('apogee_altitude')!r}).value") == "120"
        assert await b.eval(f"document.querySelector({inp('stop_altitude')!r}).value") == "20"
        assert len(rig.v1.received_requests) == n


HOME_ACTION = {"action": "GET_HOME_POSITION", "target": 1, "label": "Request home position", "group": "Vehicle"}


@needs_browser
async def test_home_position_request_shows_the_home_line_and_the_panel_locks_in_replay(tmp_path):
    async with (
        rig_with(tmp_path, usb_kw={"request_rules": True}, requests=REQ_ACTIONS + [HOME_ACTION]) as rig,
        browser_check.Browser(1500, 1300, 9372) as b,
    ):
        await open_page(b, rig, ["requests"])
        btn = "[data-panel=requests] .rq-row[data-key='GET_HOME_POSITION@1'] .rq-btn"
        await until(
            lambda: b.eval(f"!!document.querySelector({btn!r}) && !document.querySelector({btn!r}).disabled"),
            what="enabled",
        )
        assert "via usb" in await text(b, "[data-panel=requests] .rq-row[data-key='GET_HOME_POSITION@1'] .rq-route")
        rig.v1.request_latched_session = rig.app.engine.session
        await press(b, btn)
        await until(lambda: any(r["action"] == 23 for r in rig.v1.received_requests), timeout=3, what="request")
        await let_go(b, btn)
        await until(
            lambda: b.eval("document.querySelector('[data-testid=rq-home]').textContent.startsWith('home: ')"),
            timeout=5,
            what="home line",
        )
        await b.eval("document.querySelectorAll('header.top .toggle button')[1].click()")
        await b.sleep(0.5)
        assert await b.eval("document.querySelector('[data-testid=dock-lock]').hidden") is False  # inputs locked
        assert await b.eval(f"document.querySelector({btn!r}).disabled") is True


@needs_browser
async def test_raw_sensor_panel_shows_raw_and_calibrated_values_with_units_age_and_sources(tmp_path):
    async with rig_with(tmp_path) as rig, browser_check.Browser(1500, 1300, 9373) as b:
        await open_page(b, rig, ["rawsensors"])
        await until(
            lambda: b.eval("(document.querySelector('[data-q=accel] .v')?.textContent.length ?? 0) > 3"), what="values"
        )
        rows = await b.eval("[...document.querySelectorAll('[data-panel=rawsensors] tbody tr')].map(r => r.dataset.q)")
        assert rows == ["gyro", "accel", "mag", "pressure", "temperature", "omega_est"]
        for q, unit in (("gyro", "rad/s"), ("accel", "m/s²"), ("mag", "µT"), ("pressure", "Pa"), ("temperature", "K")):
            assert unit in await text(b, f"[data-panel=rawsensors] tr[data-q={q}] td:nth-child(2)")
        assert "m/s²" in await text(b, "[data-panel=rawsensors] tr[data-q=accel] td:nth-child(3)")  # |raw a|
        assert "µT" in await text(b, "[data-panel=rawsensors] tr[data-q=mag] td:nth-child(3)")
        await until(
            lambda: b.eval(
                "(document.querySelector('[data-q=accel] polyline')?.getAttribute('points') ?? '').length > 10"
            ),
            what="sparkline",
        )
        assert "raw (uncalibrated)" in await text(b, "[data-panel=rawsensors] .raw-head")
        # the pty vehicle sends CALIBRATION every 2 s (bench-like values): calibrated column, magnitudes, source, age
        await until(
            lambda: b.eval("document.querySelector('[data-testid=raw-cal-info]').dataset.state === 'fresh'"),
            timeout=8,
            what="CALIBRATION",
        )
        info = await text(b, "[data-testid=raw-cal-info]")
        assert "age" in info and "calib EEPROM, transform EEPROM, offset override (hard-coded)" in info  # gyro 0x07
        assert "accel calib EEPROM, transform EEPROM;" in info
        for q, unit in (("gyro", "rad/s"), ("accel", "m/s²"), ("mag", "µT")):
            assert unit in await text(b, f"[data-testid=raw-cal-{q}]")
        assert "m/s²" in await text(b, "[data-testid=raw-calmag-accel]")
        assert "µT" in await text(b, "[data-testid=raw-calmag-mag]")
        assert "rad/s" in await text(b, "[data-testid=raw-cal-omega_est]")
        assert await text(b, "[data-testid=raw-note]") == ""


@needs_browser
async def test_calibrated_column_waits_for_the_calibration_packet(tmp_path):
    from fake_vehicle_pty import NET_VEHICLE_RATES

    rates = {k: v for k, v in NET_VEHICLE_RATES.items() if k != "CALIBRATION"}
    async with rig_with(tmp_path, usb_kw={"rates": rates}) as rig, browser_check.Browser(1500, 1300, 9378) as b:
        await open_page(b, rig, ["rawsensors"])
        await until(
            lambda: b.eval("(document.querySelector('[data-q=accel] .v')?.textContent.length ?? 0) > 3"), what="values"
        )
        assert await text(b, "[data-testid=raw-cal-gyro]") == "waiting for CALIBRATION packet"
        assert await text(b, "[data-testid=raw-cal-omega_est]") == "waiting for CALIBRATION packet"
        assert "none received" in await text(b, "[data-testid=raw-cal-info]")


@needs_browser
async def test_raw_sensor_panel_says_so_without_a_vehicle_usb_source(tmp_path):
    async with rig_with(tmp_path, with_usb=False) as rig, browser_check.Browser(1500, 1300, 9374) as b:
        await open_page(b, rig, ["rawsensors"])
        await b.sleep(1.0)
        assert "No vehicle USB source is connected" in await text(b, "[data-testid=raw-note]")


@needs_browser
async def test_link_panel_flags_differing_and_over_limit_power(tmp_path):
    async with rig_with(tmp_path, tx_kw={}) as rig, browser_check.Browser(1500, 1300, 9375) as b:
        rig.v2.tx_power = 14  # both modules report 14 dBm applied, the Router commands 10
        await open_page(b, rig, ["link"])
        await until(lambda: b.eval("document.querySelectorAll('.pw-warn').length >= 2"), timeout=6, what="warnings")
        warns = await b.eval("[...document.querySelectorAll('.pw-warn')].map(e => e.textContent)")
        assert any("above the 10 dBm (10 mW) legal limit" in w for w in warns)
        assert any("Router commands 10 dBm, ship radio node applies 14 dBm" in w for w in warns)
        assert "Router power commanded" in await text(b, "[data-panel=link]")
        rig.v2.tx_power = 10
        await until(
            lambda: b.eval("document.querySelectorAll('.pw-warn').length === 0"), timeout=6, what="warnings cleared"
        )


@needs_browser
async def test_schema_mismatch_banner_shows_for_an_old_hash_and_clears_on_the_new_one(tmp_path):
    async with (
        rig_with(tmp_path, usb_kw={"schema_hash": 0x3AFA3CCE}) as rig,
        browser_check.Browser(1500, 1300, 9376) as b,
    ):
        await open_page(b, rig)
        await until(lambda: b.eval("!document.querySelector('.sb-banner').hidden"), timeout=6, what="banner")
        assert "0x3AFA3CCE" in await text(b, ".sb-banner")
    (tmp_path / "again").mkdir()
    async with rig_with(tmp_path / "again") as rig, browser_check.Browser(1500, 1300, 9377) as b:
        await open_page(b, rig)
        await b.sleep(1.5)
        assert await b.eval("document.querySelector('.sb-banner').hidden") is True


def test_calibration_vectors_for_the_js_tests_match_the_packets_vectors():
    """web/test/data/calibration_vectors.json is a copy of the imu_raw and calibration cases of the Packets test
    vectors (the JS formula test cannot read the submodule); this fails when they drift apart. It also pins the
    source bit numbers the JS decoder uses to the generated module."""
    import json

    import exvectr_packets as xp

    from ..vectors import packet_vectors

    mine = json.loads((ROOT / "web" / "test" / "data" / "calibration_vectors.json").read_text())
    cases = {c["name"]: c["values"] for c in packet_vectors()["cases"] if c["name"] in mine}
    assert mine == cases
    C = xp.Calibration
    assert (C.GYRO_SOURCE_CALIB_EEPROM, C.GYRO_SOURCE_TRANSFORM_EEPROM, C.GYRO_SOURCE_OFFSET_OVERRIDE) == (1, 2, 4)
    assert (C.GYRO_SOURCE_RAM_ONLY, C.GYRO_SOURCE_MEMORY_CLEARED) == (8, 16)


@needs_browser
async def test_simulation_testing_page_is_greyed_out_until_the_vehicle_reports_simulation(tmp_path):
    import tomllib

    cfg = tomllib.loads((ROOT / "config" / "gs.example.toml").read_text())
    sims = [a for a in cfg["requests"]["action"] if a["group"] == "Simulation"]
    assert len(sims) == 8
    async with (
        rig_with(tmp_path, usb_kw={"request_rules": True}, requests=REQ_ACTIONS + sims) as rig,
        browser_check.Browser(1500, 1300, 9373) as b,
    ):
        rig.v1.request_latched_session = rig.app.engine.session
        rig.v1.sim_state = False  # the vehicle reports real sensors
        await open_page(b, rig, ["requests", "simtests"])
        kind = "document.querySelector('[data-testid=source-banner]').dataset.kind"
        await until(lambda: b.eval(f"{kind} === 'real'"), what="reported real")
        assert await b.eval("window.exgs.simtests.sections().join()") == "wind,faults"
        # the generic Requests panel does not list the simulation entries
        assert await b.eval("!document.querySelector('[data-panel=requests] [data-key=\"SIM_FAULT_CLEAR@1\"]')")
        stuck = "[data-panel=simtests] [data-section=faults] .sf-flap[data-flap=BR]"
        apply_ = "[data-panel=simtests] [data-section=faults] .rq-btn"
        assert "simulation mode only" in await text(b, "[data-panel=simtests] .st-why")
        assert await b.eval(f"document.querySelector({stuck!r}).disabled") is True
        assert await b.eval(f"[...document.querySelectorAll({apply_!r})].every(e => e.disabled)") is True
        assert await b.eval("document.querySelectorAll('[data-panel=simtests] .st-off').length") == 2
        assert await b.eval("[...document.querySelectorAll('[data-panel=simtests] .st-tag')].length") == 2
        rig.v1.sim_state = True  # now the vehicle reports simulation
        await until(lambda: b.eval(f"{kind} === 'sim'"), timeout=5, what="reported sim")
        await b.eval("window.exgs.dock.open('simtests')")
        await until(lambda: b.eval(f"!document.querySelector({stuck!r}).disabled"), what="enabled when reported")
        await b.eval(f"document.querySelector({stuck!r}).click()")
        assert "0.7330 rad" in await text(b, "[data-testid=sf-rad]")
        stuck_btn = "[data-panel=simtests] [data-section=faults] .rq-btn"
        sent = "[data-testid=sf-sent]"
        # the fake vehicle is not in simulation as far as its executor goes: refused in plain words
        await press(b, stuck_btn)
        await until(lambda: len(rig.v1.received_requests) == 1, timeout=3, what="request after the hold")
        await let_go(b, stuck_btn)
        req = rig.v1.received_requests[0]
        assert (req["action"], req["arg_i"], req["session"]) == (20, 3, rig.app.engine.session)
        assert abs(req["arg_f"] - 0.7330383) < 1e-6
        await until(
            lambda: b.eval(f"document.querySelector({sent!r}).textContent.includes('not in simulation mode')"),
            timeout=5,
            what="refusal text",
        )
        rig.v1.request_simulation = True
        await b.sleep(0.7)  # the core's 500 ms spacing; a new user action gets a new req_id
        await press(b, stuck_btn)
        await until(
            lambda: b.eval(f"document.querySelector({sent!r}).textContent.includes('ACK DONE')"),
            timeout=5,
            what="ACK DONE",
        )
        await let_go(b, stuck_btn)
        ids = [r["req_id"] for r in rig.v1.received_requests if r["action"] == 20]
        assert len(set(ids)) == 2


@needs_browser
async def test_command_tab_is_first_and_header_abort_failsafe_need_a_500_ms_hold(tmp_path):
    async with rig_with(tmp_path) as rig, browser_check.Browser(1500, 1300, 9374) as b:
        await open_page(b, rig, ["command"])
        first = await b.eval("document.querySelector('#dock [role=tab]').textContent")
        assert "Command" in first
        panel = "[data-panel=command]"
        for k in ("begin", "abort", "motors", "real", "sim", "reset", "zero"):
            assert await b.eval(f"!!document.querySelector('{panel} [data-btn={k}]')"), k
        engine = rig.app.engine
        abort = "[data-testid=abort-btn]"
        fs = "[data-testid=failsafe-btn]"
        await until(lambda: b.eval(f"!document.querySelector({abort!r}).disabled"), what="abort enabled")
        await press(b, abort)  # released before 500 ms: nothing is sent
        await b.sleep(0.2)
        await let_go(b, abort)
        await b.sleep(0.6)
        assert engine.abort_count == 0
        await press(b, abort)
        await until(lambda: engine.abort_count == 1, timeout=3, what="abort after the 500 ms hold")
        await let_go(b, abort)
        await press(b, fs)
        await until(lambda: engine.core.failsafe_latched, timeout=3, what="failsafe latched")
        await let_go(b, fs)
        await until(
            lambda: b.eval(f"document.querySelector({fs!r}).textContent.includes('Clear failsafe')"),
            what="clear control",
        )
        assert "FAILSAFE REQUESTED" in await text(b, "[data-testid=safety-note]")
        await press(b, fs)
        await until(lambda: not engine.core.failsafe_latched, timeout=3, what="failsafe cleared by the hold")
        await let_go(b, fs)
        # the panel buttons drive the virtual switches: Mission begin takes the lease and shows what the engine sends
        await b.eval(f"document.querySelector('{panel} [data-btn=begin]').click()")
        await until(lambda: rig.app.rc.status().lease is not None, what="lease taken by the button")
