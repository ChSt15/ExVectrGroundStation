"""Mission status bar, 'Commands (temporary)' panel, raw sensor panel and the link power block in a real headless
browser, against the pty fake vehicles (exvectrnet-1). Skipped when no Chromium-based browser is installed."""

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
from .test_telecommand_wire import COMMANDS

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import browser_check  # noqa: E402

needs_browser = pytest.mark.skipif(browser_check.find_browser() is None, reason="no Chromium-based browser")

TX_RATES = {"VEHICLE_STATUS": 5.0, "LINK_STATS_TX": 4.0, "LINK_STATS_RX": 4.0, "LINK_CONFIG": 4.0}


@contextlib.asynccontextmanager
async def rig_with(tmp_path, usb_kw=None, tx_kw=None, with_usb=True):
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
                "telecommands": {"command": COMMANDS},
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
        await b.eval(f"document.querySelector('[data-panel={p}]').dataset.collapsed = 'false'")


async def text(b, sel):
    return await b.eval(f"document.querySelector({sel!r}).textContent")


def cell(key):
    return f"#statusbar [data-key={key}] .sb-v"


@needs_browser
async def test_status_bar_shows_only_reported_values_and_follows_a_simulation_toggle(tmp_path):
    async with rig_with(tmp_path) as rig, browser_check.Browser(1500, 1300, 9371) as b:
        await open_page(b, rig, ["telecommands"])
        await until(
            lambda: b.eval("document.querySelector('#statusbar').dataset.state === 'fresh'"), what="fresh status"
        )
        sim0 = await text(b, cell("simulation"))
        assert sim0 in ("SIMULATION", "REAL SENSORS")
        labels = await b.eval(
            "[...document.querySelectorAll('#statusbar .sb-cell:not([hidden]) .sb-l')].map(e => e.textContent)"
        )
        for want in (
            "Sensors",
            "Mode",
            "Mission",
            "Armed",
            "Motors",
            "Failsafe",
            "Failures",
            "Ground station",
            "Control source",
            "RC age",
            "Last request",
            "Sensor state (raw)",
        ):
            assert want in labels, want
        assert await b.eval(f"document.querySelector({cell('simulation')!r}).parentElement.dataset.loud") == "true"
        assert await b.eval(f"document.querySelector({cell('motors_enabled')!r}).parentElement.dataset.loud") == "true"
        assert "0x" in await text(b, cell("failure_bits")) and "(" in await text(b, cell("failure_bits"))
        links = await b.eval("[...document.querySelectorAll('#statusbar .sb-link')].map(e => e.textContent)")
        assert any(x.strip().startswith("usb") for x in links) and any(x.strip().startswith("link") for x in links)
        # nothing is shown for the toggle until the vehicle reports it: hold the button for 1 s
        btn = "[data-panel=telecommands] .rq-btn[data-name=SimulationMode]"
        await until(
            lambda: b.eval(f"document.querySelector({btn!r}) && !document.querySelector({btn!r}).disabled"),
            what="enabled",
        )
        await press(b, btn)
        await until(lambda: len(rig.v1.received_telecommands) == 1, timeout=3, what="telecommand after the hold")
        await let_go(b, btn)
        want = "REAL SENSORS" if sim0 == "SIMULATION" else "SIMULATION"
        await until(
            lambda: b.eval(f"document.querySelector({cell('simulation')!r}).textContent === {want!r}"),
            timeout=5,
            what="reported flip",
        )
        rep = await text(b, "[data-testid=tc-sim]")
        assert rep == f"vehicle reports: {want}"
        sent = await text(b, "[data-panel=telecommands] .tc-row[data-name=SimulationMode] .rq-state")
        assert "once via usb" in sent and "no ACK exists" in sent
        await b.sleep(2.0)
        assert len(rig.v1.received_telecommands) == 1  # never repeated


@needs_browser
async def test_commands_panel_is_hidden_in_replay_and_buttons_need_a_route(tmp_path):
    async with rig_with(tmp_path, with_usb=False) as rig, browser_check.Browser(1500, 1300, 9372) as b:
        await open_page(b, rig, ["telecommands"])
        btn = "[data-panel=telecommands] .rq-btn[data-name=Arm]"
        await until(
            lambda: b.eval(f"!!document.querySelector({btn!r}) && !document.querySelector({btn!r}).disabled"),
            what="enabled via the Router",
        )
        assert "via link" in await text(b, "[data-panel=telecommands] .tc-row[data-name=Arm] .rq-route")
        assert (
            await b.eval("document.querySelector('[data-panel=telecommands] h2').textContent") == "Commands (temporary)"
        )
        await b.eval("document.querySelectorAll('header.top .toggle button')[1].click()")
        await b.sleep(0.5)
        assert await b.eval("document.querySelector('[data-panel=telecommands]').hidden") is True
        assert len(rig.v1.received_telecommands) == 0 and len(rig.v2.received_telecommands) == 0


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
