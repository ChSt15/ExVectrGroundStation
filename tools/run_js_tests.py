"""Run the GUI unit tests in web/test.

Uses `node --test web/test` when node is installed. Otherwise runs the same files in headless Chromium
(tools/browser_check.py) with small stand-ins for node:test and node:assert/strict (web/test/shim).
Exit code 0 when all tests pass, 1 on failure, 2 if neither node nor a browser is available.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_check  # noqa: E402


def run_with_node() -> int:
    return subprocess.call(["node", "--test", str(WEB / "test")])


async def run_in_browser(port: int = 9334) -> tuple[int, list[dict]]:
    files = sorted(p.name for p in (WEB / "test").glob("*.test.js"))
    page = f"""<!doctype html><meta charset=utf-8>
<script type=importmap>{{"imports":{{"node:test":"/web/test/shim/test.js",
"node:assert/strict":"/web/test/shim/assert.js"}}}}</script>
<script type=module>
import {{ runAll }} from "/web/test/shim/test.js";
for (const f of {json.dumps(files)}) await import("/web/test/" + f);
window.__results = await runAll();
</script>"""
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text=page, content_type="text/html"))
    app.router.add_static("/web/", WEB)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    http_port = runner.addresses[0][1]
    try:
        async with browser_check.Browser(800, 600, port) as b:
            await b.goto(f"http://127.0.0.1:{http_port}/")
            results = None
            for _ in range(100):
                results = await b.eval("window.__results || null")
                if results is not None:
                    break
                await b.sleep(0.1)
            if results is None:
                results = [{"name": "load test files", "ok": False, "error": "\n".join(b.console)}]
    finally:
        await runner.cleanup()
    return sum(1 for r in results if not r["ok"]), results


def main() -> int:
    if shutil.which("node"):
        return run_with_node()
    if browser_check.find_browser() is None:
        print("neither node nor a Chromium-based browser found", file=sys.stderr)
        return 2
    failed, results = asyncio.run(run_in_browser())
    for r in results:
        print(("ok     " if r["ok"] else "FAIL   ") + r["name"])
        if not r["ok"]:
            print(r["error"])
    print(f"{len(results) - failed} passed, {failed} failed (browser runner)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
