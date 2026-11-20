"""`exgs update ...`: the client commands against a real core (read-only ones only) and the offline `--local`."""

import json
import subprocess
import sys

import pytest

from exvectr_gs.cli.main import main
from exvectr_gs.update import gitops

from ..unit.test_update_gitops import rig  # noqa: F401
from .test_cli_client import core, exgs  # noqa: F401  (the `core` fixture)


@pytest.fixture(autouse=True)
def no_real_pip(monkeypatch):
    monkeypatch.setattr(gitops, "PIP_COMMAND", ["true"])


def test_status_against_a_running_core(core):  # noqa: F811
    r = exgs(core, "update", "status", "--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["ok"] and "enabled" in d and "guards" in d
    t = exgs(core, "update", "status")
    assert t.returncode == 0 and "branch" in t.stdout or "not a git checkout" in t.stdout


def test_apply_without_confirm_is_refused_before_anything_is_sent(core):  # noqa: F811
    r = exgs(core, "update", "apply", "--json")
    assert r.returncode == 1 and "--confirm" in json.loads(r.stdout)["error"]["message"]


def test_update_needs_a_subcommand_or_local():
    r = subprocess.run([sys.executable, "-m", "exvectr_gs.cli.main", "update"], capture_output=True, text=True)
    assert r.returncode == 1


def test_local_update_fast_forwards(rig, capsys):  # noqa: F811
    rig.push()
    assert main(["update", "--local", "--repo", str(rig.gs)]) == 0
    assert (rig.gs / "b.txt").exists()
    assert "restart the core" in capsys.readouterr().out


def test_local_dry_run_only_lists(rig, capsys):  # noqa: F811
    rig.push(msg="the new thing")
    assert main(["update", "--local", "--dry-run", "--repo", str(rig.gs)]) == 0
    assert "the new thing" in capsys.readouterr().out and not (rig.gs / "b.txt").exists()


def test_local_refuses_dirty_tree_and_wrong_branch(rig, capsys):  # noqa: F811
    from ..unit.test_update_gitops import git

    rig.push()
    (rig.gs / "a.txt").write_text("edit")
    assert main(["update", "--local", "--repo", str(rig.gs)]) == 1
    assert "local changes" in capsys.readouterr().err
    git(rig.gs, "checkout", "--", "a.txt")
    git(rig.gs, "checkout", "-b", "other")
    assert main(["update", "--local", "--repo", str(rig.gs)]) == 1
    assert "update.branch" in capsys.readouterr().err
    assert not (rig.gs / "b.txt").exists()
