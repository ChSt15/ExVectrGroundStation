"""Recorder interface. Implemented in task T2 (stub: every method raises NotImplementedError)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..core.bus import Bus
from ..packets import PacketRegistry
from ..packets.base import Packet

_T2 = "implemented in task T2"


@dataclass
class RecordingInfo:
    path: Path
    name: str
    started: int
    stopped: int | None
    packets: int
    bytes: int


@dataclass
class RecorderStatus:
    """active, current info, last error, free disk (fields defined in T2)."""


@dataclass
class LogIndex:
    """Keyframe index of a log (fields defined in T2)."""


@dataclass
class LogInfo:
    name: str
    path: Path
    started: int
    duration_s: float | None
    size: int
    complete: bool
    sources: list[str]
    sim: bool | None
    label: str | None


class Recorder:
    def __init__(self, directory: Path, registry: PacketRegistry, bus: Bus, meta: Mapping[str, str]):
        pass

    def start(self, label: str | None = None) -> RecordingInfo:
        raise NotImplementedError(_T2)

    def stop(self) -> RecordingInfo | None:
        raise NotImplementedError(_T2)

    def note(self, text: str) -> None:
        raise NotImplementedError(_T2)

    def status(self) -> RecorderStatus:
        raise NotImplementedError(_T2)


class LogReader:
    def __init__(self, path: Path):
        pass

    def header(self) -> dict[str, str]:
        raise NotImplementedError(_T2)

    def iter_packets(self, start_offset: int = 0) -> Iterator[tuple[int, Packet]]:
        raise NotImplementedError(_T2)

    def build_index(self, keyframe_s: float = 1.0) -> LogIndex:
        raise NotImplementedError(_T2)


class LogLibrary:
    def __init__(self, directory: Path):
        pass

    def list(self) -> list[LogInfo]:
        raise NotImplementedError(_T2)
