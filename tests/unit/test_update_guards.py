"""Guards of the self-update and the restart path (exit code 42) in the core."""

import asyncio
import tempfile

import pytest

from exvectr_gs.cli.main import main
from exvectr_gs.core.app import RESTART_EXIT_CODE, CoreApp
from exvectr_gs.core.config import ConfigError, parse_config
from exvectr_gs.packets.defs.vehicle import MissionMode, MissionState, VehicleStatus
from exvectr_gs.rc.controller import Lease
from exvectr_gs.update import update_guards


def make_app(update=None):
    return CoreApp(
        parse_config(
            {
                "core": {"source_priority": ["fake"]},
                "server": {"bind": "127.0.0.1", "port": 1},
                "source": [{"id": "fake", "kind": "fake", "profile": "usb", "seed": 1}],
                "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
                "update": update or {},
            }
        )
    )


def vs(app, **kw):
    app.store.update(VehicleStatus(t=app.clock.now(), src="fake", **kw))


def hold_lease(app):
    app.rc._lease = Lease("L1", "test", "connection", None)  # no RC-capable source exists in a unit test
    app.rc._hb_deadline = 1 << 62
    return app.rc._lease


def test_no_guard_on_a_quiet_core():
    assert update_guards(make_app()) == []


@pytest.mark.parametrize(
    "fields, text",
    [
        ({"armed": True}, "ARMED"),
        ({"motors_enabled": True}, "MOTORS"),
        ({"mission_mode": MissionMode.Running}, "RUNNING"),
    ],
)
def test_vehicle_state_guards(fields, text):
    app = make_app()
    vs(app, **fields)
    assert any(text in w for w in update_guards(app))


def test_disarmed_motors_off_idle_is_allowed():
    app = make_app()
    vs(app, armed=False, motors_enabled=False, mission_mode=MissionMode.Idle)
    assert update_guards(app) == []


def test_mission_state_running_guard():
    app = make_app()
    app.store.update(MissionState(t=app.clock.now(), src="fake", mission_mode=MissionMode.Running))
    assert any("RUNNING" in w for w in update_guards(app))


def test_recording_guard():
    app = make_app()
    app.recorder.start("t")
    try:
        assert any("recording" in w for w in update_guards(app))
    finally:
        app.recorder.stop()
    assert update_guards(app) == []


def test_rc_lease_guard():
    app = make_app()
    lease = hold_lease(app)
    assert any("lease" in w for w in update_guards(app))
    app.rc.release(lease.id)
    assert update_guards(app) == []


def test_request_in_flight_guard(monkeypatch):
    app = make_app()
    monkeypatch.setattr(app.requests, "status", lambda: {"inflight": [{"action": "X"}], "recent": []})
    assert any("request" in w for w in update_guards(app))


def test_recent_telecommand_guard(monkeypatch):
    app = make_app()
    now = app.clock.now()
    monkeypatch.setattr(app.telecommands, "status", lambda: {"recent": [{"t": now - 1_000_000_000}]})
    assert any("telecommand" in w for w in update_guards(app))
    monkeypatch.setattr(app.telecommands, "status", lambda: {"recent": [{"t": now - 10_000_000_000}]})
    assert update_guards(app) == []


def test_config_update_section():
    assert make_app().cfg.update.branch == "master" and make_app().cfg.update.enabled
    for bad in ({"auto_check_interval_s": 10}, {"branch": "a b"}, {"remote": "-x"}, {"nope": 1}):
        with pytest.raises(ConfigError):
            make_app(bad)
    assert make_app({"auto_check_interval_s": 60}).cfg.update.auto_check_interval_s == 60


# ---------------------------------------------------------------- restart path


async def test_restart_exits_with_code_42():
    app = make_app()
    app.restart_delay_s = 0
    app.request_restart("test update")
    await asyncio.wait_for(app.run(summary=lambda s: None), 10)
    assert app.exit_code == RESTART_EXIT_CODE


async def test_restart_cancelled_when_a_guard_fails_at_the_last_moment():
    app = make_app()
    app.restart_delay_s = 0
    app.request_restart("test update")
    app.update._guards = lambda: ["the vehicle reports ARMED"]  # became armed between the request and the exit
    lines = []
    await asyncio.wait_for(app.run(duration_s=2.5, summary=lines.append), 10)
    assert app.exit_code == 0 and any("restart cancelled" in x for x in lines)


async def test_clean_shutdown_closes_recording_and_releases_lease():
    app = make_app()
    app.recorder.start("t")
    hold_lease(app)
    app._shutdown_cleanly()
    assert not app.recorder.status().active and app.rc.status().lease is None


def test_cli_core_returns_the_exit_code(monkeypatch, tmp_path):
    orig = CoreApp.run

    async def run(self, *a, **kw):
        self.restart_delay_s = 0
        self.request_restart("cli test")
        return await orig(self, *a, **kw)

    monkeypatch.setattr(CoreApp, "run", run)
    cfg = tmp_path / "gs.toml"
    cfg.write_text(
        f'[server]\nbind="127.0.0.1"\n[recorder]\ndirectory="{tmp_path}/rec"\n'
        '[core]\nsource_priority=["fake"]\n[[source]]\nid="fake"\nkind="fake"\nprofile="usb"\n'
    )
    assert main(["core", "--config", str(cfg), "--port", "0", "--duration", "30"]) == RESTART_EXIT_CODE
