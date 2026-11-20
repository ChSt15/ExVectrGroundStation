# Self-update

The ground station updates itself from its git remote (`github.com/ChSt15/ExVectrGroundStation`, branch `master`): the **Software** tab of the Operations dock, `exgs update`, or the API ops `update.*` ([api.md](api.md)). It is not a vehicle uplink and sends nothing to the vehicle, but the restart drops RC leases, recordings and the serial connections, so it has guards.

Code: `src/exvectr_gs/update/` (`gitops.py` git steps, `guards.py`, `service.py`), `CoreApp.request_restart`, `cli/cmd_update.py`, `web/app/update/`, `start.sh`.

## Config (`[update]`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | `false`: `update.check` and `update.apply` answer `not_implemented` (status still works). |
| `branch` | `"master"` | The branch that is followed. |
| `remote` | `"origin"` | |
| `auto_check_interval_s` | `0` | 0 = only on demand; otherwise at least 60: a quiet periodic `git fetch` and compare (failures only in the debug log). The result feeds the header badge. |
| `allow_switch` | `false` | `true`: also update when another branch is checked out (follows that branch's `<remote>/<name>`). A detached HEAD is always refused. |

## Operations

- `update.status`: read-only, every client. Branch, commit (short sha, date, subject), configured branch, dirty flag and files, submodule pins and state, schema hash of the checked-out ExVectrPackets (read from the file) and of the running core, last check, `restart_pending`, and the guards that would refuse an update now.
- `update.check` (control hosts only): `git fetch --prune` (60 s timeout), then HEAD against `<remote>/<branch>`: ahead and behind counts, the new commit subjects (at most 20), whether `pyproject.toml` or a submodule pin changes.
- `update.apply {confirm: true}` (control hosts only).

## Apply procedure

1. Refuse (nothing changed) when: a guard fails (below), the checked-out branch is not the configured one (unless `allow_switch`), HEAD is detached, the working tree has **local changes** (tracked files; untracked files do not count), the checkout has commits that are not on the remote (not a fast-forward).
2. `git fetch --prune`.
3. `git merge --ff-only <remote>/<branch>`. Never a merge commit, never `reset --hard`, local changes are never touched.
4. `git submodule update --init --recursive`. If a pinned commit is not on the remote this fails; the git error is returned, **no restart**, the new code stays on disk.
5. If `pyproject.toml` changed: `sys.executable -m pip install -e .` (the running interpreter's environment). A failure is reported, no restart.
6. Request the restart: the core waits 1.5 s (so the reply reaches the client), re-checks the guards, and exits with **code 42** after closing the recorder, releasing the RC lease and cancelling the sources (serial ports close). `start.sh` restarts it on 42 and stops on any other code. If a guard fails at that last moment the restart is cancelled (event and log line); `restart_pending` stays set and applying again restarts without a new fetch.

Every step has a timeout (fetch 60 s, merge 60 s, submodules 300 s, pip 900 s), runs in a worker thread, is logged, and its output is returned in the result and pushed to all clients as `update_progress` messages. One check or apply runs at a time (`busy`).

## Guards (checked in the core, not only in the GUI)

The update is refused while any of these holds: the vehicle reports **armed**, **motors enabled**, or **mission mode Running** (latest known values of `VehicleStatus` and `MissionState`, even when old: an armed vehicle whose link dropped is still armed); a **recording** is active (stop it first); an **RC lease** is held; a **request** is in flight; a telecommand was sent less than 3 s ago (telecommands have no ACK, so "in flight" is approximated); the client is not a control host. The replay view is refused by the GUI only (the core has no view per client).

## Offline: `exgs update --local`

For when no core runs: the same steps and refusals (dirty tree, wrong branch, non-fast-forward), no guards, no restart (start the core afterwards). `--dry-run` only fetches and lists the new commits. It does not detect a running core: stop it first.

## Limits

- A GUI change is picked up by a page reload; the Software tab reloads the page once after the restart when the commit changed.
- Without a launcher that restarts on code 42 (`start.sh`), the core simply stops after an update.
- An installed copy that is not a git checkout reports `git: false` and cannot update.
- Rollback: see [install-frame.md](install-frame.md).
