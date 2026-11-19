"""WebSocket client of the core, shared by the `exgs` read/control commands.

Exit codes (design 4.4): 0 ok, 1 error, 2 timeout, 3 control denied.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import itertools
import json
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from ..api.protocol import PROTOCOL_VERSION

EXIT_OK, EXIT_ERROR, EXIT_TIMEOUT, EXIT_DENIED = 0, 1, 2, 3
DEFAULT_PORT = 8765
CONNECT_TIMEOUT_S = 5.0
REQUEST_TIMEOUT_S = 10.0


class CliError(Exception):
    """A command failed. `code` is the process exit code."""

    def __init__(self, message: str, exit_code: int = EXIT_ERROR, error_code: str = "error"):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.error_code = error_code


class CoreError(CliError):
    """The core answered `ok: false`."""

    def __init__(self, code: str, message: str):
        exit_code = EXIT_DENIED if code in ("control_denied", "no_lease", "control_locked") else EXIT_ERROR
        super().__init__(f"{code}: {message}", exit_code, code)


class CoreClient:
    def __init__(self, host: str, port: int, name: str = "exgs"):
        self.host, self.port, self.name = host, port, name
        self.welcome: dict = {}
        self.schema: dict = {}
        self.messages: asyncio.Queue[dict] = asyncio.Queue()  # everything that is not a result
        self.last: dict[str, dict] = {}  # newest message per op (status, rc, ...)
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None

    async def __aenter__(self) -> CoreClient:
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=CONNECT_TIMEOUT_S))
        try:
            self._ws = await self._session.ws_connect(f"http://{self.host}:{self.port}/ws", max_msg_size=1 << 24)
        except (aiohttp.ClientError, OSError, TimeoutError) as e:
            await self._session.close()
            raise CliError(
                f"cannot connect to the core at {self.host}:{self.port} ({e}). Is `exgs core` running?"
            ) from None
        self._reader = asyncio.create_task(self._read())
        hello = self._send_request("hello", client="cli", name=self.name, protocol=PROTOCOL_VERSION)
        try:
            self.welcome = await asyncio.wait_for(hello, REQUEST_TIMEOUT_S)
        except TimeoutError:
            raise CliError("core did not answer hello", EXIT_TIMEOUT) from None
        schema = await self.next_message("schema", REQUEST_TIMEOUT_S)
        self.schema = schema
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()

    def _send_request(self, op: str, **fields: Any) -> asyncio.Future:
        rid = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        assert self._ws is not None
        asyncio.ensure_future(self._ws.send_str(json.dumps({"op": op, "id": rid, **fields})))
        return fut

    async def _read(self) -> None:
        assert self._ws is not None
        async for m in self._ws:
            if m.type != aiohttp.WSMsgType.TEXT:
                continue
            msg = json.loads(m.data)
            if msg.get("op") in ("result", "welcome") and msg.get("id") in self._pending:
                fut = self._pending.pop(msg["id"])
                if msg["op"] == "welcome":
                    fut.set_result(msg)
                elif msg["ok"]:
                    fut.set_result(msg["data"])
                else:
                    fut.set_exception(CoreError(msg["error"]["code"], msg["error"]["message"]))
            else:
                self.last[msg["op"]] = msg
                self.messages.put_nowait(msg)
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CliError("connection to the core closed"))
        self.messages.put_nowait({"op": "_closed"})

    async def request(self, op: str, timeout: float = REQUEST_TIMEOUT_S, **fields: Any) -> Any:
        try:
            return await asyncio.wait_for(self._send_request(op, **fields), timeout)
        except TimeoutError:
            raise CliError(f"no answer to {op} within {timeout:g} s", EXIT_TIMEOUT) from None

    async def next_message(self, op: str | None = None, timeout: float | None = None) -> dict:
        """Next pushed message (optionally of one op, others are skipped). Raises CliError on close/timeout."""
        deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
        while True:
            left = None if deadline is None else max(0.0, deadline - asyncio.get_running_loop().time())
            try:
                msg = await asyncio.wait_for(self.messages.get(), left)
            except TimeoutError:
                raise CliError(f"timeout waiting for {op or 'a message'}", EXIT_TIMEOUT) from None
            if msg["op"] == "_closed":
                raise CliError("connection to the core closed")
            if op is None or msg["op"] == op:
                return msg

    @staticmethod
    def loop_time() -> float:
        return asyncio.get_running_loop().time()

    async def status(self) -> dict:
        """The current `status` message (the core sends one right after the handshake)."""
        if "status" not in self.last:
            await self.next_message("status", REQUEST_TIMEOUT_S)
        return self.last["status"]


# ---------------------------------------------------------------- command plumbing


def add_connection_options(p: argparse.ArgumentParser) -> None:
    """Options every client command accepts (after the sub-command, as in the design examples)."""
    g = p.add_argument_group("connection and output")
    g.add_argument("--host", default=os.environ.get("EXGS_HOST", "127.0.0.1"), help="core host (env EXGS_HOST)")
    g.add_argument(
        "--port", type=int, default=int(os.environ.get("EXGS_PORT", DEFAULT_PORT)), help="core port (env EXGS_PORT)"
    )
    g.add_argument("--json", action="store_true", help="machine-readable output: JSON Lines on stdout")


def emit(args: argparse.Namespace, obj: dict, text: str | None = None) -> None:
    """One result: a JSON line with --json, else the human text."""
    if args.json:
        print(json.dumps({"ok": True, **obj}, separators=(",", ":")), flush=True)
    elif text is not None:
        print(text, flush=True)


def report_error(args: argparse.Namespace, e: CliError) -> None:
    if getattr(args, "json", False):
        print(
            json.dumps({"ok": False, "error": {"code": e.error_code, "message": e.message}}, separators=(",", ":")),
            flush=True,
        )
    else:
        print(f"exgs: {e.message}", file=sys.stderr)


def run_client_command(
    args: argparse.Namespace, body: Callable[[CoreClient, argparse.Namespace], Awaitable[int | None]]
) -> int:
    """Connect, run `body(client, args)`, map errors to exit codes. Output errors go through report_error."""

    async def go() -> int:
        async with CoreClient(args.host, args.port) as c:
            return (await body(c, args)) or EXIT_OK

    try:
        return asyncio.run(go())
    except CliError as e:
        report_error(args, e)
        return e.exit_code
    except KeyboardInterrupt:
        return EXIT_OK


def add_client_command(
    sub: argparse._SubParsersAction,
    name: str,
    help: str,
    body: Callable[[CoreClient, argparse.Namespace], Awaitable[int | None]],
) -> argparse.ArgumentParser:
    """Register a sub-command that connects to the core and runs `body`."""
    p = sub.add_parser(name, help=help)
    add_connection_options(p)
    p.set_defaults(func=lambda a: run_client_command(a, body))
    return p
