"""WireProfile `provisional-1`: legacy payload layouts in the provisional COBS framer."""

from __future__ import annotations

from ...packets import REGISTRY, Direction, Packet
from ...packets.defs.host import RawFrame
from ...packets.defs.link import RcOrigin, RcState
from ..profile import Frame
from . import layouts
from .framing import ProvisionalFramer


class Provisional1Profile:
    name = "provisional-1"

    def make_framer(self) -> ProvisionalFramer:
        return ProvisionalFramer()

    def decode(self, frame: Frame, src: str) -> Packet:
        def raw(err: str) -> Packet:
            return RawFrame(
                t=frame.t,
                src=src,
                channel=frame.channel,
                len=len(frame.payload),
                hex=frame.payload.hex(),
                err=err,
            )

        try:
            lay = layouts.LAYOUTS.get(frame.channel)
            if lay is None:
                return raw("unknown channel")
            if len(frame.payload) != lay.size:
                return raw(f"{lay.type_name}: length {len(frame.payload)}, expected {lay.size}")
            return REGISTRY.get(lay.type_name).cls(t=frame.t, src=src, **lay.decode(frame.payload))
        except Exception as e:  # never raises (contract)
            return raw(f"decode error: {e}")

    def encode(self, pkt: Packet) -> Frame | None:
        """Only UP types can be encoded; today that is RcState with origin gs."""
        try:
            if REGISTRY.get(pkt.TYPE).direction is not Direction.UP:
                return None
        except KeyError:
            return None
        if isinstance(pkt, RcState):
            if pkt.origin != RcOrigin.gs or pkt.ch_us is None or pkt.seq is None:
                return None
            try:
                payload = layouts.encode_rc_state(pkt.seq, list(pkt.ch_us))
            except (ValueError, TypeError):
                return None
            return Frame(channel=layouts.PORT_RC_STATE, payload=payload, t=pkt.t, meta={})
        return None

    def supported_types(self) -> set[str]:
        return {lay.type_name for lay in layouts.LAYOUTS.values()}


def make_profile() -> Provisional1Profile:
    return Provisional1Profile()
