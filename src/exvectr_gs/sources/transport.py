"""Byte transport under the serial sources (design 6.1): what `SerialSource` needs from a port.

The source never knows whether bytes come from a tty (pyserial) or from a CDC-ACM device driven in userspace
(`usb_cdc.UsbCdcTransport`, for hosts whose kernel has no cdc_acm driver). The framing, the wire profile and
the reconnect loop are identical for both.
"""

from __future__ import annotations

from typing import Protocol


class TransportError(OSError):
    """The port is unusable or gone. Message is for the operator (shown as the source's last error)."""


class Transport(Protocol):
    def open(self) -> None:
        """Open the port (blocking). Raises TransportError / OSError."""

    def read(self) -> bytes:
        """Block for a short time (about 50 ms) and return the bytes that arrived, b'' on timeout.

        Raises TransportError / OSError when the port is gone.
        """

    def write(self, data: bytes) -> None:
        """Write all of `data` (blocking, bounded by a timeout). Raises TransportError / OSError."""

    def close(self) -> None:
        """Release the port. Never raises, safe to call twice, safe while read() is blocked in a thread."""
