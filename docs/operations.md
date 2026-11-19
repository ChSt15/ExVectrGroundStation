# Field setup, udev rules, systemd unit, pre-flight checklist (design section 8.3)

Spec: [ground-station-design.md](ground-station-design.md). The udev section was written in T5; the rest (systemd unit, pre-flight checklist) is filled in by task T10.

## Serial access and udev rules (T5)

The ground station opens the vehicle (Teensy) and the TX module as plain USB serial ports with pyserial. No custom driver is needed.

1. Put the user in the serial group: `sudo usermod -aG dialout $USER` (Debian, Ubuntu, Fedora) or `uucp` (Arch). Log out and in again.
2. Install the rules below as `/etc/udev/rules.d/99-exvectr.rules`, then `sudo udevadm control --reload && sudo udevadm trigger`.
3. Unplug and replug. Stable names appear under `/dev/serial/by-id/`; use them in `match = { device = ... }`. The names survive a Teensy reboot or reflash (the `ttyACM*` number does not).

```udev
# Teensy 4.0 USB serial (vehicle): VID 16c0, PID 0483
SUBSYSTEM=="tty", ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="0483", MODE="0660", GROUP="dialout", ENV{ID_MM_DEVICE_IGNORE}="1"
# Teensy in bootloader mode (flashing), so that a flash tool can reach it
SUBSYSTEM=="usb", ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="0478", MODE="0660", GROUP="dialout"
# Radiomaster Pocket ExpressLRS TX module with a CP210x bridge: VID 10c4, PID ea60
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", MODE="0660", GROUP="dialout", ENV{ID_MM_DEVICE_IGNORE}="1"
# ESP32-S3 native USB serial (TX module): VID 303a, PID 1001
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", MODE="0660", GROUP="dialout", ENV{ID_MM_DEVICE_IGNORE}="1"
```

`ID_MM_DEVICE_IGNORE` stops ModemManager from probing the ports (it can send AT commands to them). Check the real IDs with `lsusb` and adjust: the TX module's USB IDs depend on the module hardware.

Matching in `gs.toml`:

```toml
[[source]]
id = "usb"
kind = "vehicle_usb"
match = { vid = "16c0", pid = "0483" }                      # or { device = "/dev/serial/by-id/usb-Teensyduino_USB_Serial_XXXX-if00" }
rc_role = "control"

[[source]]
id = "link"
kind = "link_module"
match = { device = "/dev/serial/by-id/usb-Silicon_Labs_CP2102_XXXX-if00-port0" }
baud = 921600
rc_role = "control"        # master sends RC here too (the TX module rejects it); "mirror" = receive-only
```

In slave (the Pocket's state arriving) the ground station sends nothing; it sends RC state only after the user switches to master, then to every connected `rc_role = "control"` source ([radio.md](radio.md)).

When two Teensys are attached, a VID/PID match picks the first; add `serial = "..."` or use the by-id path. When the vehicle is on USB and the radio chain is also connected, the default `source_priority = ["usb", "link", "fake"]` shows the vehicle's USB telemetry in the panels; both streams are recorded.

Without hardware: run `tools/fake_vehicle_pty.py` and match its symlink (see [wire-profiles.md](wire-profiles.md)).

Troubleshooting: a source in state `error` with "Permission denied" means the group or the rule is missing; `error` with "Resource temporarily unavailable" means another program (for example a serial monitor) holds the port, which is opened exclusively; `searching` means the device is not present.
