"""update.status / update.check / update.apply over the WebSocket API, against temporary git repositories."""

import asyncio
import subprocess
import tempfile
from pathlib import Path

import pytest

from exvectr_gs.core.app import CoreApp
from exvectr_gs.core.config import parse_config
from exvectr_gs.packets.defs.vehicle import VehicleStatus
from exvectr_gs.update import gitops

from ..unit.test_update_gitops import commit, git  # noqa: F401
from .test_api import Client, running


@pytest.fixture(autouse=True)
def no_real_pip(monkeypatch):
    monkeypatch.setattr(gitops, "PIP_COMMAND", ["true"])


@pytest.fixture
def repos(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "master", str(remote)], check=True, capture_output=True)
    dev = tmp_path / "dev"
    git(tmp_path, "clone", str(remote), str(dev))
    git(dev, "checkout", "-b", "master")
    commit(dev, "a.txt")
    git(dev, "push", "-u", "origin", "master")
    gs = tmp_path / "gs"
    git(tmp_path, "clone", "-b", "master", str(remote), str(gs))

    class R:
        pass

    r = R()
    r.dev, r.gs = dev, gs
    return r


def make_app(repo, control_hosts=("127.0.0.1", "::1"), update=None):
    app = CoreApp(
        parse_config(
            {
                "core": {"source_priority": ["fake"]},
                "server": {"bind": "127.0.0.1", "port": 1, "control_hosts": list(control_hosts)},
                "source": [{"id": "fake", "kind": "fake", "profile": "usb", "seed": 1}],
                "recorder": {"directory": tempfile.mkdtemp(prefix="exgs-test-rec-")},
                "update": update or {},
            }
        )
    )
    app.update.repo = Path(repo)
    return app


def push_new(repos, name="b.txt"):
    commit(repos.dev, name, msg=f"new {name}")
    git(repos.dev, "push", "origin", "master")


async def test_status_is_open_to_view_only_clients(repos):
    async with running(make_app(repos.gs, control_hosts=("10.99.99.99",)), sources=False) as r:
        c = await Client.connect(r)
        assert c.welcome["can_control"] is False
        st = await c.ok("update.status")
        assert st["git"] and st["branch"] == "master" and st["configured_branch"] == "master"
        assert st["dirty"] is False and st["subject"] == "add a.txt" and st["guards"] == []
        assert "submodules" in st and "last_check" in st
        await c.close()


async def test_check_and_apply_are_control_hosts_only(repos):
    async with running(make_app(repos.gs, control_hosts=("10.99.99.99",)), sources=False) as r:
        c = await Client.connect(r)
        assert await c.err("update.check") == "control_denied"
        assert await c.err("update.apply", confirm=True) == "control_denied"
        await c.close()


async def test_apply_needs_confirm_true(repos):
    async with running(make_app(repos.gs), sources=False) as r:
        c = await Client.connect(r)
        push_new(repos)
        for kw in ({}, {"confirm": False}, {"confirm": "yes"}):
            assert await c.err("update.apply", **kw) == "invalid_value"
        assert not (repos.gs / "b.txt").exists()
        await c.close()


async def test_check_reports_new_commits_and_pushes_summary(repos):
    async with running(make_app(repos.gs), sources=False) as r:
        c = await Client.connect(r)
        push_new(repos)
        d = await c.ok("update.check")
        assert d["behind"] == 1 and d["commits"][0]["subject"] == "new b.txt"
        await asyncio.sleep(0.2)
        await c.ok("update.status")  # flushes pushes into the inbox
        msg = [m for m in c.inbox if m["op"] == "update"][-1]
        assert not msg["busy"] and msg["behind"] == 1 and msg["checked_at"] and msg["enabled"]
        assert (await c.ok("update.status"))["last_check"]["behind"] == 1
        await c.close()


async def test_apply_updates_then_the_core_restarts(repos):
    app = make_app(repos.gs)
    app.restart_delay_s = 0
    async with running(app, sources=False) as r:
        c = await Client.connect(r)
        push_new(repos)
        res = await c.ok("update.apply", confirm=True)
        assert res["ok"] and res["restart"] and [s["name"] for s in res["steps"]][:2] == ["fetch", "merge"]
        assert (repos.gs / "b.txt").exists()
        progress = [m for m in c.inbox if m["op"] == "update_progress"]
        assert [m["step"] for m in progress][:2] == ["fetch", "merge"]
        assert app._restart_requested and app.update.restart_pending
        await c.close()


@pytest.mark.parametrize(
    "setup, text",
    [
        (lambda app: app.store.update(VehicleStatus(t=app.clock.now(), src="fake", armed=True)), "ARMED"),
        (lambda app: app.recorder.start("t"), "recording"),
    ],
)
async def test_apply_refused_by_guards_and_nothing_changes(repos, setup, text):
    app = make_app(repos.gs)
    async with running(app, sources=False) as r:
        c = await Client.connect(r)
        push_new(repos)
        setup(app)
        r1 = await c.call("update.apply", confirm=True)
        assert not r1["ok"] and r1["error"]["code"] == "update_refused" and text in r1["error"]["message"]
        assert not (repos.gs / "b.txt").exists() and not app._restart_requested
        assert text in " ".join((await c.ok("update.status"))["guards"])
        if app.recorder.status().active:
            app.recorder.stop()
        await c.close()


async def test_apply_refused_for_dirty_tree_and_wrong_branch(repos):
    async with running(make_app(repos.gs), sources=False) as r:
        c = await Client.connect(r)
        push_new(repos)
        (repos.gs / "a.txt").write_text("edit")
        r1 = await c.call("update.apply", confirm=True)
        assert r1["error"]["code"] == "update_refused" and "local changes" in r1["error"]["message"]
        git(repos.gs, "checkout", "--", "a.txt")
        git(repos.gs, "checkout", "-b", "other")
        r2 = await c.call("update.apply", confirm=True)
        assert r2["error"]["code"] == "update_refused" and "update.branch" in r2["error"]["message"]
        await c.close()


async def test_disabled_and_unavailable(repos):
    async with running(make_app(repos.gs, update={"enabled": False}), sources=False) as r:
        c = await Client.connect(r)
        assert await c.err("update.check") == "not_implemented"
        assert await c.err("update.apply", confirm=True) == "not_implemented"
        assert (await c.ok("update.status"))["enabled"] is False
        await c.close()


async def test_second_apply_while_running_is_busy(repos, monkeypatch):
    app = make_app(repos.gs)
    app.restart_delay_s = 0
    gate = asyncio.Event()
    real = gitops.apply

    def slow(*a, **kw):
        import time

        while not gate.is_set():
            time.sleep(0.02)
        return real(*a, **kw)

    monkeypatch.setattr(gitops, "apply", slow)
    async with running(app, sources=False) as r:
        c1, c2 = await Client.connect(r), await Client.connect(r)
        push_new(repos)
        t = asyncio.create_task(c1.call("update.apply", confirm=True))
        await asyncio.sleep(0.3)
        assert await c2.err("update.apply", confirm=True) == "busy"
        gate.set()
        assert (await t)["ok"]
        await c1.close()
        await c2.close()
