"""Make the generated `exvectr_packets` package importable.

It lives in the git submodule `lib/ExVectrPackets/python` and is never copied. An installed or
`PYTHONPATH` copy wins; otherwise the submodule directory of this checkout is put on `sys.path`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
PACKETS_PYTHON = REPO_ROOT / "lib" / "ExVectrPackets" / "python"


def ensure_packets_importable() -> None:
    try:
        import exvectr_packets  # noqa: F401
    except ModuleNotFoundError:
        if not (PACKETS_PYTHON / "exvectr_packets").is_dir():
            raise ModuleNotFoundError(
                "exvectr_packets not found: run `git submodule update --init` (lib/ExVectrPackets)"
            ) from None
        sys.path.insert(0, str(PACKETS_PYTHON))
        import exvectr_packets  # noqa: F401


ensure_packets_importable()
