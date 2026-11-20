#!/usr/bin/env bash
# Start the ExVectr ground station core and open the GUI (docs/install-frame.md, docs/updating.md).
#
# Usage: ./start.sh [config-file] [extra `exgs core` arguments...]
# Config: the argument, else config/gs.local.toml (untracked, per machine) if it exists, else config/gs.example.toml
# (fake vehicle, no hardware needed).
#
# The core is restarted when it exits with code 42 ("update applied, restart"); any other exit code ends the loop.
# Environment: EXGS_PORT (default 8765), GS_NAME (mDNS alias, default gs -> gs.local), GS_NO_BROWSER=1 (do not open
# the GUI), GS_NO_MDNS=1 (no alias).
set -u

main() {
  local ROOT
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "$ROOT" || return 1

  local CONFIG="${1:-}"
  if [ -n "$CONFIG" ]; then
    shift
  elif [ -f config/gs.local.toml ]; then
    CONFIG=config/gs.local.toml
  else
    CONFIG=config/gs.example.toml
  fi

  # First run: virtual environment and the git submodules (the packet definitions are one).
  if [ ! -x .venv/bin/exgs ]; then
    echo "Creating .venv (first run, needs network for pip) ..."
    python3 -m venv .venv && .venv/bin/pip install -e . || { echo "Setting up .venv failed." >&2; return 1; }
  fi
  if [ -d .git ] && [ ! -d lib/ExVectrPackets/python ]; then
    git submodule update --init --recursive || { echo "git submodule update failed." >&2; return 1; }
  fi

  local PORT="${EXGS_PORT:-8765}"
  local URL="http://localhost:$PORT/"

  # Announce this machine as gs.local (mDNS alias through avahi, no system rename), so other devices can open
  # http://gs.local:$PORT/ without an IP. GS_NAME changes the name.
  local GS_NAME="${GS_NAME:-gs}" ALIAS_NOTE=""
  if [ -z "${GS_NO_MDNS:-}" ] && command -v avahi-publish >/dev/null 2>&1; then
    local GS_IP
    GS_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)"
    if [ -n "$GS_IP" ]; then
      avahi-publish -a -R "$GS_NAME.local" "$GS_IP" >/dev/null 2>&1 &
      trap 'kill $! 2>/dev/null' EXIT
      ALIAS_NOTE="  or from other devices: http://$GS_NAME.local:$PORT/ ($GS_IP)"
    fi
  fi
  echo "ExVectr ground station: $URL$ALIAS_NOTE"
  echo "config: $CONFIG   (Ctrl-C or closing this window stops it; the GUI's Update button restarts it)"

  # Open the GUI once the core answers (and only once: after an update restart the page reconnects by itself).
  # Chromium app mode (own window, no address bar): Flatpak org.chromium.Chromium, else a native Chromium or Chrome,
  # else the default browser.
  open_gui() {
    for _ in $(seq 1 60); do
      curl -s -o /dev/null --max-time 1 "$URL" && break
      sleep 0.5
    done
    [ -n "${GS_NO_BROWSER:-}" ] && return 0
    if command -v flatpak >/dev/null 2>&1 && flatpak info org.chromium.Chromium >/dev/null 2>&1; then
      flatpak run org.chromium.Chromium --app="$URL" >/dev/null 2>&1 &
      return 0
    fi
    local b
    for b in chromium-browser chromium google-chrome google-chrome-stable; do
      if command -v "$b" >/dev/null 2>&1; then
        "$b" --app="$URL" --user-data-dir="${XDG_CACHE_HOME:-$HOME/.cache}/exgs-gui" >/dev/null 2>&1 &
        return 0
      fi
    done
    command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL" >/dev/null 2>&1 &
    return 0
  }
  open_gui &

  local rc
  while true; do
    .venv/bin/exgs core --config "$CONFIG" "$@"
    rc=$?
    if [ "$rc" -eq 42 ]; then
      echo "Update applied: restarting the core ..."
      sleep 1
      continue
    fi
    break
  done

  # Ended on its own: keep an interactive window open so a message can be read.
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 130 ] && [ -t 0 ]; then
    read -r -p "Ground station stopped (exit code $rc). Press Enter to close. " _
  fi
  return "$rc"
}

# The whole script is one function call, so bash has read everything before an update replaces this file.
main "$@"
exit $?
