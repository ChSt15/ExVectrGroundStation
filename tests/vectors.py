"""Paths and loaders of the shared test vectors in the pinned submodules."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIRE_VECTORS = ROOT / "lib" / "ExVectrNetwork" / "test" / "vectors" / "wire_v1.json"
PACKET_VECTORS = ROOT / "lib" / "ExVectrPackets" / "test" / "vectors" / "packets.json"


def wire_vectors() -> dict:
    return json.loads(WIRE_VECTORS.read_text())


def packet_vectors() -> dict:
    return json.loads(PACKET_VECTORS.read_text())
