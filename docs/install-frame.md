# Installing on the Steam Frame (SteamOS, aarch64)

The Frame runs the core from a git clone and updates itself from GitHub ([updating.md](updating.md)). Assumed: user `steamos`, ssh alias `frame`.

## Install

```bash
git clone --recurse-submodules https://github.com/ChSt15/ExVectrGroundStation.git ~/exvectr-gs
cd ~/exvectr-gs && ./start.sh
```

The first `start.sh` creates `.venv` (`python3 -m venv`, `pip install -e .`, needs network once), starts the core, and opens the GUI in Chromium app mode. `start.sh` **replaces the temporary `run-frame.sh`**: delete that script, and if the old clone has an untracked `config/gs.local.toml` copied from `gs.example.toml`, replace it as described under Config.

Chromium is the system Flatpak `org.chromium.Chromium`; `start.sh` runs `flatpak run org.chromium.Chromium --app=URL`. Started over ssh, set the display first: `DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 ./start.sh` (add `GS_NO_BROWSER=1` to skip the window). To use the same profile directory as before, start Chromium yourself with `--user-data-dir=$HOME/.cache/exgs-gui`.

Optional desktop entry: `sed "s#@INSTALL_DIR@#$HOME/exvectr-gs#g" tools/exvectr-gs.desktop.in > ~/.local/share/applications/exvectr-gs.desktop`.

## Config

```bash
cp config/gs.pocket.toml config/gs.local.toml      # untracked (.gitignore); start.sh prefers it
```

Edit `config/gs.local.toml`: enable the sources that exist on the Frame, and set `[server] control_hosts` to the addresses that may send RC state (default: loopback only, so the Frame itself). `[update]` follows `origin/master`.

## Serial / USB access (known limit)

The Frame's kernel (6.18, custom, no module directory) is built **without `CONFIG_USB_ACM`**. The Router (ESP32-S3, Espressif `303a:1001`) and the Teensy (`16c0:0483`) show up in `lsusb` but there is **no `/dev/ttyACM*`**, so pyserial cannot open them. Group membership (`uucp`) and udev rules do not help.

Userspace USB works without root: the user is in group `input` and `/dev/bus/usb/001/00x` is `root:input crw-rw----`, so no sudo and no udev rule are needed. libusb-1.0 is already at `/usr/lib/libusb-1.0.so.0`. Install pyusb into the venv (optional extra `usb`):

```bash
cd ~/exvectr-gs && .venv/bin/pip install -e '.[usb]'      # or:  .venv/bin/pip install pyusb
```

Then select the userspace transport per source in `config/gs.local.toml` (details: `docs/usb-transport.md`):

```toml
[[source]]
id = "link"
kind = "link_module"                    # the Router (ESP32-S3 USB Serial/JTAG)
transport = "usb"
match = { vid = "303a", pid = "1001" }
rc_role = "mirror"                      # or "control"; unchanged by the transport
enabled = true

[[source]]
id = "usb"
kind = "vehicle_usb"                    # the Teensy
transport = "usb"
match = { vid = "16c0", pid = "0483" }
rc_role = "control"
enabled = true
```

`transport = "auto"` uses `/dev/ttyACM*` when it exists and the userspace transport otherwise, so one config works on the PC and on the Frame.

## Update

Software tab (Operations dock): Check for updates, then hold "Update and restart" for 1 s. Or `.venv/bin/exgs update check` and `exgs update apply --confirm`; `exgs update --local` when no core runs. Refused while armed, motors enabled, mission running, recording, or an RC lease is held; refused with local changes ("local changes"), another branch, or non-fast-forward history. The page reconnects by itself and reloads once when the version changed.

## Rollback

```bash
cd ~/exvectr-gs && git log --oneline -5         # pick the good commit
git checkout <sha>                               # detached HEAD: the Software tab then refuses to update
git submodule update --init --recursive
# restart start.sh. Back to following master:  git checkout master
```

## Troubleshooting

- `local changes` refusal: `git status` in the clone, commit, stash or discard.
- `not a fast-forward`: the clone has commits that are not on GitHub; `git log origin/master..`.
- Submodule step fails: a pinned commit is not on GitHub yet (push the library first); the code is already updated, apply again after fixing.
- Core exits and the window stays: read the message, exit code other than 42 ends `start.sh` by design.
- No `Check for updates` button: the browser is on a view-only host (`control_hosts`).
- GUI shows an old version after the update: reload the page (Ctrl+R).
