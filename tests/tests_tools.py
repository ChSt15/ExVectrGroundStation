"""Imports the tools/ modules that tests use (`tools/` is not a package)."""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import fake_vehicle_net as net  # noqa: E402

__all__ = ["net"]
