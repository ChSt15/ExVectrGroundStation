"""Drive a headless Chromium over the DevTools protocol (development and test helper, no extra dependencies).

Used to check the GUI in a real browser: load a URL, wait, evaluate JavaScript, take screenshots.
    python tools/browser_check.py http://127.0.0.1:8765/ --wait 4 --shot out.png --width 390 --eval 'document.title'
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request

import aiohttp

CANDIDATES = ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable")


def find_browser() -> str | None:
    for c in CANDIDATES:
        p = shutil.which(c)
        if p:
            return p
    return None


class Browser:
    """One headless browser with one page. Use as an async context manager."""

    def __init__(self, width: int = 1500, height: int = 1000, port: int = 9333):
        self.width, self.height, self.port = width, height, port
        self.proc: subprocess.Popen | None = None
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.n = 0
        self.console: list[str] = []
        self._tmp = tempfile.TemporaryDirectory(prefix="exgs-chrome-", ignore_cleanup_errors=True)

    async def __aenter__(self) -> Browser:
        exe = find_browser()
        if exe is None:
            raise RuntimeError("no Chromium or Chrome found")
        self.proc = subprocess.Popen(
            [exe, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
             f"--remote-debugging-port={self.port}", f"--user-data-dir={self._tmp.name}",
             f"--window-size={self.width},{self.height}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        target = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=1) as r:
                    pages = [t for t in json.load(r) if t.get("type") == "page"]
                    if pages:
                        target = pages[0]
                        break
            except OSError:
                pass
            await asyncio.sleep(0.1)
        if target is None:
            raise RuntimeError("browser did not start")
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(target["webSocketDebuggerUrl"], max_msg_size=64 << 20)
        await self.cmd("Runtime.enable")
        await self.cmd("Page.enable")
        await self.cmd(
            "Emulation.setDeviceMetricsOverride",
            width=self.width, height=self.height, deviceScaleFactor=1, mobile=self.width < 600,
        )  # fmt: skip
        return self

    async def __aexit__(self, *exc) -> None:
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        self._tmp.cleanup()

    async def cmd(self, method: str, **params):
        self.n += 1
        mid = self.n
        await self.ws.send_json({"id": mid, "method": method, "params": params})
        while True:
            msg = await self.ws.receive_json()
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if msg.get("method") == "Runtime.consoleAPICalled":
                self.console.append(
                    " ".join(str(a.get("value", a.get("description", ""))) for a in msg["params"]["args"])
                )
            elif msg.get("method") == "Runtime.exceptionThrown":
                self.console.append("EXCEPTION " + json.dumps(msg["params"]["exceptionDetails"])[:300])

    async def goto(self, url: str) -> None:
        await self.cmd("Page.navigate", url=url)

    async def eval(self, expr: str, await_promise: bool = True):
        r = await self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=await_promise)
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:400])
        return r["result"].get("value")

    async def sleep(self, seconds: float) -> None:
        """Wait while still draining DevTools events (console messages)."""
        await self.eval(f"new Promise(r => setTimeout(r, {int(seconds * 1000)}))")

    async def screenshot(self, path: str) -> None:
        r = await self.cmd("Page.captureScreenshot", format="png")
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(r["data"]))


async def _main(args) -> int:
    async with Browser(args.width, args.height, args.port) as b:
        await b.goto(args.url)
        await b.sleep(args.wait)
        for expr in args.eval or []:
            print(json.dumps(await b.eval(expr), indent=1))
        if args.shot:
            await b.screenshot(args.shot)
        for line in b.console:
            print("console:", line, file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url")
    ap.add_argument("--wait", type=float, default=3.0)
    ap.add_argument("--shot")
    ap.add_argument("--width", type=int, default=1500)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--eval", action="append")
    return asyncio.run(_main(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
