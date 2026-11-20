"""The update procedure against temporary git repositories: a bare 'remote', a 'dev' clone that pushes commits and
the 'gs' clone that updates. No network, no hardware."""

import subprocess
from pathlib import Path

import pytest

from exvectr_gs.update import UpdateError, gitops


def git(cwd, *args):
    r = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "protocol.file.allow=always", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return r.stdout.strip()


def commit(repo, name, text="x", msg=None):
    (Path(repo) / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", msg or f"add {name}")


@pytest.fixture
def rig(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "master", str(remote)], check=True, capture_output=True)
    dev = tmp_path / "dev"
    git(tmp_path, "clone", str(remote), str(dev))
    git(dev, "checkout", "-b", "master")
    commit(dev, "pyproject.toml", "[project]\n")
    commit(dev, "a.txt")
    git(dev, "push", "-u", "origin", "master")
    gs = tmp_path / "gs"
    git(tmp_path, "clone", "-b", "master", str(remote), str(gs))

    class Rig:
        pass

    r = Rig()
    r.remote, r.dev, r.gs = remote, dev, gs
    r.push = lambda name="b.txt", text="x", msg=None: (
        commit(dev, name, text, msg),
        git(dev, "push", "origin", "master"),
    )
    return r


@pytest.fixture(autouse=True)
def no_real_pip(monkeypatch):
    monkeypatch.setattr(gitops, "PIP_COMMAND", ["true"])  # a command that succeeds; tests that need it replace it


def steps(res):
    return [s.name for s in res.steps]


def test_status_clean_and_dirty(rig):
    st = gitops.status(rig.gs, "master", "origin")
    assert st["git"] and st["branch"] == "master" and not st["dirty"] and st["subject"] == "add a.txt"
    (rig.gs / "a.txt").write_text("changed")
    st = gitops.status(rig.gs, "master", "origin")
    assert st["dirty"] and "a.txt" in st["dirty_files"][0]


def test_status_not_a_repo(tmp_path):
    assert gitops.status(tmp_path, "master", "origin")["git"] is False


def test_check_lists_new_commits(rig):
    rig.push("b.txt", msg="first new")
    rig.push("c.txt", msg="second new")
    res = gitops.check(rig.gs, "master", "origin")
    assert res["behind"] == 2 and res["ahead"] == 0 and not res["diverged"]
    assert [c["subject"] for c in res["commits"]] == ["second new", "first new"]
    assert not res["deps_changed"] and not res["submodules_changed"]


def test_check_caps_commit_list_and_flags_pyproject(rig):
    for i in range(23):
        rig.push(f"f{i}.txt")
    rig.push("pyproject.toml", "[project]\nname='x'\n")
    res = gitops.check(rig.gs, "master", "origin")
    assert res["behind"] == 24 and len(res["commits"]) == gitops.MAX_COMMITS and res["more_commits"] == 4
    assert res["deps_changed"]


def test_apply_fast_forward(rig):
    rig.push("b.txt")
    old = git(rig.gs, "rev-parse", "--short", "HEAD")
    res = gitops.apply(rig.gs, "master", "origin")
    assert res.ok and res.restart and res.old == old and res.new != old
    assert steps(res) == ["fetch", "merge", "submodules"]  # no pyproject change: no pip step
    assert (rig.gs / "b.txt").exists()
    assert git(rig.gs, "rev-parse", "HEAD") == git(rig.dev, "rev-parse", "HEAD")


def test_apply_up_to_date_does_nothing(rig):
    res = gitops.apply(rig.gs, "master", "origin")
    assert res.ok and not res.restart and "up to date" in res.message
    assert gitops.apply(rig.gs, "master", "origin", restart_owed=True).restart  # a pending restart is still owed


def test_progress_callback(rig):
    rig.push()
    seen = []
    gitops.apply(rig.gs, "master", "origin", progress=lambda s, t: seen.append(s))
    assert seen == ["fetch", "merge", "submodules"]


def test_dirty_tree_refused_and_untouched(rig):
    rig.push()
    (rig.gs / "a.txt").write_text("my local edit")
    with pytest.raises(UpdateError) as e:
        gitops.apply(rig.gs, "master", "origin")
    assert e.value.code == "dirty" and "local changes" in e.value.message
    assert (rig.gs / "a.txt").read_text() == "my local edit"
    assert not (rig.gs / "b.txt").exists()


def test_wrong_branch_refused(rig):
    git(rig.gs, "checkout", "-b", "other")
    with pytest.raises(UpdateError) as e:
        gitops.apply(rig.gs, "master", "origin")
    assert e.value.code == "wrong_branch"
    with pytest.raises(UpdateError) as e:
        gitops.check(rig.gs, "master", "origin")
    assert e.value.code == "wrong_branch"


def test_allow_switch_follows_the_checked_out_branch(rig):
    git(rig.dev, "checkout", "-b", "other")
    commit(rig.dev, "o.txt")
    git(rig.dev, "push", "origin", "other")
    git(rig.gs, "fetch", "origin")
    git(rig.gs, "checkout", "-b", "other", "master")
    git(rig.gs, "branch", "--set-upstream-to=origin/other")
    # the local 'other' is behind origin/other by the o.txt commit
    res = gitops.apply(rig.gs, "master", "origin", allow_switch=True)
    assert res.ok and (rig.gs / "o.txt").exists()


def test_detached_head_refused(rig):
    git(rig.gs, "checkout", "--detach", "HEAD~1")
    with pytest.raises(UpdateError) as e:
        gitops.apply(rig.gs, "master", "origin")
    assert e.value.code == "detached"


def test_non_fast_forward_refused(rig):
    rig.push()
    commit(rig.gs, "local.txt", msg="local only")
    with pytest.raises(UpdateError) as e:
        gitops.apply(rig.gs, "master", "origin")
    assert e.value.code == "diverged"
    assert git(rig.gs, "log", "-1", "--format=%s") == "local only"  # nothing was changed
    assert not (rig.gs / "b.txt").exists()


def test_local_commits_ahead_only_is_not_behind_but_refused_by_apply(rig):
    commit(rig.gs, "local.txt")
    res = gitops.check(rig.gs, "master", "origin")
    assert res["ahead"] == 1 and res["behind"] == 0
    with pytest.raises(UpdateError):
        gitops.apply(rig.gs, "master", "origin")


def test_pyproject_change_triggers_pip(rig, monkeypatch, tmp_path):
    marker = tmp_path / "pip-ran"
    monkeypatch.setattr(gitops, "PIP_COMMAND", ["touch", str(marker)])
    rig.push("pyproject.toml", "[project]\nname='y'\n")
    res = gitops.apply(rig.gs, "master", "origin")
    assert res.ok and res.deps_changed and steps(res)[-1] == "pip" and marker.exists()


def test_pip_failure_reported_without_restart(rig, monkeypatch):
    monkeypatch.setattr(gitops, "PIP_COMMAND", ["false"])
    rig.push("pyproject.toml", "[project]\nname='y'\n")
    res = gitops.apply(rig.gs, "master", "origin")
    assert not res.ok and not res.restart and steps(res)[-1] == "pip" and "pip install" in res.message
    assert res.new != res.old  # the code did change on disk


def test_submodule_failure_reported(rig):
    # a pin on a repository that does not exist: `git submodule update --init` must fail and be reported
    (rig.dev / ".gitmodules").write_text('[submodule "lib/x"]\n\tpath = lib/x\n\turl = /nonexistent/repo.git\n')
    git(rig.dev, "add", ".gitmodules")
    git(rig.dev, "update-index", "--add", "--cacheinfo", "160000," + "ab" * 20 + ",lib/x")
    git(rig.dev, "commit", "-m", "add submodule pin")
    git(rig.dev, "push", "origin", "master")
    res = gitops.apply(rig.gs, "master", "origin")
    assert not res.ok and not res.restart
    assert steps(res) == ["fetch", "merge", "submodules"] and not res.steps[-1].ok
    assert res.submodules_changed and "submodule" in res.message and res.steps[-1].output
    assert git(rig.gs, "rev-parse", "HEAD") == git(rig.dev, "rev-parse", "HEAD")  # merged, restart withheld


def test_fetch_failure(rig):
    git(rig.gs, "remote", "set-url", "origin", "/nonexistent/repo.git")
    res = gitops.apply(rig.gs, "master", "origin")
    assert not res.ok and steps(res) == ["fetch"]
    with pytest.raises(UpdateError) as e:
        gitops.check(rig.gs, "master", "origin")
    assert e.value.code == "git"


def test_step_timeout(tmp_path):
    rc, out = gitops.run(["sleep", "5"], tmp_path, 0.2)
    assert rc == 124 and "timed out" in out


def test_schema_hash_read_from_file(tmp_path):
    d = tmp_path / "lib" / "ExVectrPackets" / "python" / "exvectr_packets"
    d.mkdir(parents=True)
    (d / "__init__.py").write_text("SCHEMA_HASH = 0x761D5378\n")
    assert gitops.schema_hash(tmp_path) == "0x761D5378"
