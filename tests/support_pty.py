"""Helpers for tests that use the pty fake vehicle (tools/fake_vehicle_pty.py)."""

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "fake_vehicle_pty.py"
_spec = importlib.util.spec_from_file_location("fake_vehicle_pty", _TOOL)
fake_vehicle_pty = importlib.util.module_from_spec(_spec)
sys.modules["fake_vehicle_pty"] = fake_vehicle_pty
_spec.loader.exec_module(fake_vehicle_pty)

FakeVehiclePty = fake_vehicle_pty.FakeVehiclePty


async def wait_for(cond, timeout=5.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")
