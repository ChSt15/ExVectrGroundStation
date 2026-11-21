"""core.quit: confirm required, control hosts only, a clean exit with code 0 (no restart)."""

import asyncio

from exvectr_gs.core.app import CoreApp

from .test_api import Client, running
from .test_update_api import make_app


async def test_quit_is_control_hosts_only(tmp_path):
    app = make_app(tmp_path, control_hosts=("10.99.99.99",))
    async with running(app, sources=False) as r:
        c = await Client.connect(r)
        assert await c.err("core.quit", confirm=True) == "control_denied"
        assert not app._quit_requested
        await c.close()


async def test_quit_needs_confirm_true(tmp_path):
    app = make_app(tmp_path)
    async with running(app, sources=False) as r:
        c = await Client.connect(r)
        for kw in ({}, {"confirm": False}, {"confirm": "yes"}):
            assert await c.err("core.quit", **kw) == "invalid_value"
        assert not app._quit_requested
        await c.close()


async def test_quit_requests_a_clean_exit(tmp_path):
    app = make_app(tmp_path)
    async with running(app, sources=False) as r:
        c = await Client.connect(r)
        assert (await c.ok("core.quit", confirm=True))["ok"]
        assert app._quit_requested and app.exit_code == 0
        await c.close()


async def test_run_exits_with_code_0_after_quit(tmp_path):
    app = make_app(tmp_path)
    app.restart_delay_s = 0
    lines = []
    app.request_quit("test")
    await asyncio.wait_for(app.run(duration_s=60, summary=lines.append), timeout=10)
    assert app.exit_code == 0
    assert any("quit requested" in s for s in lines)
    assert not app._running
    assert isinstance(app, CoreApp)


from .test_cli_client import core, exgs  # noqa: E402,F401  (the `core` fixture)


def test_cli_quit_without_confirm_is_refused_before_anything_is_sent(core):  # noqa: F811
    r = exgs(core, "quit", "--json")
    assert r.returncode == 1 and "--confirm" in r.stdout
    assert exgs(core, "status", "--json").returncode == 0  # the core is still running
