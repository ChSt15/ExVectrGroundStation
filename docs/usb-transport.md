# USB transport (userspace CDC-ACM)

`SerialSource` reads and writes bytes through a transport. The default is **pyserial** (`transport = "serial"`, a
`/dev/ttyACM*` or by-id path or a vid/pid match through `serial.tools.list_ports`), unchanged. For hosts whose kernel has
no `cdc_acm` driver (the Steam Frame, kernel 6.18 without `CONFIG_USB_ACM`: the device shows in `lsusb` but no tty
exists) there is a second transport, `transport = "usb"`, which drives the same CDC-ACM device in userspace with
**pyusb** (`src/exvectr_gs/sources/usb_cdc.py`). Framing, wire profile, RC lease rules, reconnect loop and statistics
are the same for both; nothing sent to the vehicle changes, only how the bytes travel.

## When to use it

| Host | Transport |
|---|---|
| PC with ttyACM (normal Linux) | `serial` (default) |
| Frame, or any host without `/dev/ttyACM*` | `usb` |
| One config for both | `auto`: serial when the matching tty exists, otherwise usb if pyusb imports (decided at each connection attempt) |

## Config

```toml
[[source]]                                  # the Router (ESP32-S3 USB Serial/JTAG)
id = "link"
kind = "link_module"
transport = "usb"
match = { vid = "303a", pid = "1001" }      # hex strings; optional serial = "..." (alias serial_number)
rc_role = "mirror"                          # unchanged
enabled = true

[[source]]                                  # the Teensy
id = "usb"
kind = "vehicle_usb"
transport = "usb"
match = { vid = "16c0", pid = "0483" }
rc_role = "control"
```

`transport = "usb"` needs `vid` and `pid` in `match` (a `device` path does not apply and is a config error), and `baud`
is ignored (CDC has none). `auto` with a path-only match always means serial. An invalid value is a `ConfigError`.

## Install

pyusb is an **optional** dependency, imported lazily: `pip install -e '.[usb]'` (or `pip install pyusb`), plus libusb-1.0
on the host. Without them the source shows state `error` with the fix as `last_error` and keeps retrying; nothing else
is affected.

Permissions: the device node `/dev/bus/usb/BBB/DDD` must be accessible. On the Frame the user is in group `input`, which
owns the nodes, so no sudo is needed. Otherwise add the user to the group that owns `/dev/bus/usb`, or install a udev
rule for the vendor and product. A permission error is reported as such in `last_error`.

## Behaviour

- Interfaces and endpoints are found from the active configuration: data interface = class 0x0A with a bulk IN and a
  bulk OUT endpoint, control interface = class 0x02. Other interfaces (the Router's vendor JTAG interface) are never
  touched. Both CDC interfaces are claimed; a bound kernel driver is detached only if `is_kernel_driver_active` is true
  (logged as a warning) and re-attached at close.
- On open the host asserts DTR and RTS with `SET_CONTROL_LINE_STATE` (`0x21 0x22 value=0x03`, `wIndex` = the control
  interface number). The Router and the Teensy transmit only while DTR is asserted. This is why the usb transport
  ignores the `deassert_dtr_rts` default of `link_module` sources (that exists for CP210x/CH34x bridges that reset the
  module on DTR); set `deassert_dtr_rts = true` explicitly to override.
- Close deasserts DTR, releases the interfaces and disposes the device.
- Disconnect (ENODEV, EIO, EPIPE, ESHUTDOWN or any other USB error on a transfer) closes the connection, emits
  `source_down` and the normal reconnect poll (`poll_s`, default 1 s) finds the device again.

## Threads, throughput, limits

One reader thread and one writer thread per connection, like pyserial; bytes reach the event loop with
`call_soon_threadsafe`, so the asyncio loop is never blocked. Reads use a 50 ms timeout and request **one max-packet
(64 B at full speed) per call**: pyusb discards the data of a timed-out transfer, so a larger partly filled request
could lose bytes; one packet always completes. Cost: more Python calls per second than a big read, which is fine for
the 11 kB/s of the Router (about 200 calls/s at most) and the Teensy's streams (full speed tops out near 1 kB/ms).
Latency is the USB bulk polling plus at most one call: below a few milliseconds. Writes go out in max-packet chunks,
with a zero-length packet when the data ends on a packet boundary, with a 1 s timeout. Close waits for an in-flight
read (at most 50 ms). The Teensy and the Router must not be opened by another program at the same time (the claim
fails with "resource busy").

Tests use a fake `usb.core` (`tests/integration/test_usb_cdc.py`); no real USB device is opened.
