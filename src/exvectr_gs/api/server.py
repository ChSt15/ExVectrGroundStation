"""API server interface. Implemented in task T3 (stub: every method raises NotImplementedError)."""

from __future__ import annotations

from typing import Any

_T3 = "implemented in task T3"


class Hub:
    """Tracks clients and their subscriptions per view; pushes packets, status, rc and event messages."""

    def __init__(self) -> None:
        pass


class ApiServer:
    """aiohttp application: GET / (GUI), /static/..., /config/{radio|layout|model}.json, /models/...,
    /ws (protocol section 4)."""

    def __init__(self, app_ctx: Any, bind: str, port: int):
        pass

    async def start(self) -> None:
        raise NotImplementedError(_T3)

    async def stop(self) -> None:
        raise NotImplementedError(_T3)
