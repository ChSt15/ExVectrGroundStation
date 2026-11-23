"""API server: aiohttp application with the WebSocket protocol of design section 4 plus static files.

The server only reads from and calls the core's modules. It makes no control decisions. The RC
operations are passed to the RC controller (T6), recording operations to the recorder (T2), replay
operations to the replayer (T4). A module the core does not have yet answers `not_implemented`.

App context attributes used (all optional except the first four): `cfg`, `clock`, `bus`, `store`,
`history`, `plot_history`, `sources`, `recorder`, `library`, `replayer`, `rc`, `replay_bus`,
`replay_store`, `replay_history`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from .. import __version__
from ..core.hosts import is_control_host  # noqa: F401  (re-exported)
from ..core.rates import WINDOWS_S, rates_to_json
from ..core.state import StateStore
from ..packets import REGISTRY
from ..rc.controller import ControlDenied, ControlLocked
from ..requests import RequestError
from ..telecommand import TelecommandError
from ..update import UpdateError
from . import protocol as P
from .hub import Client, Hub, View
from .protocol import ProtocolError

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
STATUS_PERIOD_S = 0.5
RC_PERIOD_S = 0.2
CONFIG_NAMES = ("radio", "layout", "model", "callouts")

Handler = Callable[[Client, dict], Awaitable[Any]]


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    if p.is_absolute() or p.exists():
        return p
    return REPO_ROOT / p


class ApiServer:
    """aiohttp application: GET / (GUI), /static/..., /config/{radio|layout|model|callouts}.json, /models/...,
    /ws (protocol section 4)."""

    @staticmethod
    async def _no_cache(request: web.Request, response: web.StreamResponse) -> None:
        """The GUI files change with every ground-station version: make browsers revalidate (ETag) on every load,
        otherwise a heuristically cached old module can mix with new ones and leave the page stuck on 'loading'."""
        if not request.path.startswith(("/ws",)):
            response.headers["Cache-Control"] = "no-cache"

    def __init__(self, app_ctx: Any, bind: str, port: int):
        self.ctx = app_ctx
        self.bind = bind
        self.port = port
        self.hub = Hub()
        self.web_dir = REPO_ROOT / "web"
        self.models_dir = REPO_ROOT / "models"
        self.app = web.Application()
        self.app.on_response_prepare.append(self._no_cache)
        self.app.router.add_get("/", self._index)
        self.app.router.add_get("/ws", self._ws)
        self.app.router.add_get("/config/{name}.json", self._config_http)
        if self.web_dir.is_dir():
            self.app.router.add_static("/static/", self.web_dir)
        if self.models_dir.is_dir():
            self.app.router.add_static("/models/", self.models_dir)
        self._runner: web.AppRunner | None = None
        self._tasks: list[asyncio.Task] = []
        self._last_source_state: dict[str, str] = {}
        self._last_rc: str | None = None
        self.hub.on_client_gone = self._client_gone
        self._handlers: dict[str, Handler] = {
            "subscribe": self._op_subscribe,
            "unsubscribe": self._op_unsubscribe,
            "get_snapshot": self._op_get_snapshot,
            "get_history": self._op_get_history,
            "get_rates": self._op_get_rates,
            "rec.start": self._op_rec_start,
            "rec.stop": self._op_rec_stop,
            "rec.note": self._op_rec_note,
            "logs.list": self._op_logs_list,
            "replay.open": self._op_replay_open,
            "replay.close": self._op_replay_simple("close"),
            "replay.play": self._op_replay_simple("play"),
            "replay.pause": self._op_replay_simple("pause"),
            "replay.seek": self._op_replay_seek,
            "replay.speed": self._op_replay_speed,
            "replay.markers": self._op_replay_markers,
            "rc.acquire": self._op_rc_acquire,
            "rc.release": self._op_rc_release,
            "rc.heartbeat": self._op_rc_heartbeat,
            "rc.set": self._op_rc_set,
            "engine.abort": self._op_engine_abort,
            "engine.failsafe": self._op_engine_failsafe,
            "rc.unlock": self._op_rc_unlock,
            "rc.lock": self._op_rc_lock,
            "request.send": self._op_request_send,
            "request.list": self._op_request_list,
            "request.status": self._op_request_status,
            "telecommand.send": self._op_telecommand_send,
            "telecommand.list": self._op_telecommand_list,
            "sources.list": self._op_sources_list,
            "sources.enable": self._op_sources_enable,
            "config.get": self._op_config_get,
            "update.status": self._op_update_status,
            "update.check": self._op_update_check,
            "update.apply": self._op_update_apply,
            "core.quit": self._op_core_quit,
        }
        self._setup_views()

    # ------------------------------------------------------------ lifecycle

    def _setup_views(self) -> None:
        c = self.ctx
        self.hub.add_view(View("live", c.bus, c.store, c.clock.now))
        rstore = getattr(c, "replay_store", None) or StateStore(REGISTRY, [])
        self.hub.add_view(View("replay", getattr(c, "replay_bus", None), rstore, self._replay_clock))

    def _replay_clock(self) -> int:
        rp = self._mod("replayer")
        if rp is not None:
            with contextlib.suppress(NotImplementedError):
                t = rp.status().t
                if t is not None:
                    return t
        return 0

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.bind, self.port)
        await site.start()
        if self.port == 0:  # tests: report the chosen port
            self.port = self._runner.addresses[0][1]
        rq = self._mod("requests")
        if rq is not None:
            rq.on_change = self._push_requests
        tc = self._mod("telecommands")
        if tc is not None:
            tc.on_change = self._push_telecommands
        up = self._mod("update")
        if up is not None:
            up.on_change = self._push_update
            up.on_progress = self._push_update_progress
        self._tasks = [
            asyncio.create_task(self._status_loop(), name="api-status"),
            asyncio.create_task(self._rc_loop(), name="api-rc"),
        ]
        log.info("API listening on %s:%d", self.bind, self.port)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks = []
        for c in list(self.hub.clients.values()):
            with contextlib.suppress(Exception):
                await c.close()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self.hub.close()

    # ------------------------------------------------------------ public helpers for other modules

    def event(self, level: str, text: str, toast: str | None = None) -> None:
        """Push an `event` message to all clients (source lost, recording failed, lease expired, ...). `toast` (a key)
        asks the GUI for a toast as well (the command engine's Pocket-loss warning)."""
        msg = {"op": "event", "level": level, "text": text, "t": self.ctx.clock.now() / 1e9}
        if toast:
            msg["toast"] = toast
        self.hub.broadcast(msg)

    def replay_reset(self) -> None:
        """Call after a replay open/seek: clients get a fresh `snapshot` for the replay view."""
        self.hub.resend_snapshots("replay")

    # ------------------------------------------------------------ context access

    def _mod(self, name: str) -> Any:
        return getattr(self.ctx, name, None)

    def _need(self, name: str, what: str) -> Any:
        m = self._mod(name)
        if m is None:
            raise ProtocolError("not_implemented", f"{what} is not available in this core")
        return m

    @staticmethod
    async def _call(fn: Callable[..., Any], *args: Any) -> Any:
        """Call a module method and translate its errors to protocol errors."""
        try:
            r = fn(*args)
            if asyncio.iscoroutine(r):
                r = await r
            return r
        except ProtocolError:
            raise
        except (RequestError, TelecommandError) as e:
            raise ProtocolError(e.code, e.message) from None
        except ControlDenied as e:
            raise ProtocolError("control_denied", str(e) or "control denied") from None
        except ControlLocked as e:
            raise ProtocolError("control_locked", str(e)) from None
        except NotImplementedError as e:
            raise ProtocolError("not_implemented", str(e) or "not implemented") from None
        except FileNotFoundError as e:
            raise ProtocolError("not_found", str(e)) from None
        except KeyError as e:
            raise ProtocolError("not_found", f"not found: {e}") from None
        except ValueError as e:
            raise ProtocolError("invalid_value", str(e)) from None
        except OSError as e:
            raise ProtocolError("io_error", str(e)) from None
        except RuntimeError as e:
            raise ProtocolError("busy", str(e)) from None

    # ------------------------------------------------------------ HTTP

    async def _index(self, request: web.Request) -> web.StreamResponse:
        idx = self.web_dir / "index.html"
        if not idx.is_file():
            raise web.HTTPNotFound(text="GUI not installed")
        return web.FileResponse(idx)

    def _config_json(self, name: str) -> Any:
        if name not in CONFIG_NAMES:
            raise ProtocolError("not_found", f"unknown config {name!r}")
        cfg = self.ctx.cfg
        path = _resolve(
            {
                "radio": cfg.radio.config,
                "layout": cfg.gui.layout,
                "model": cfg.gui.model,
                "callouts": cfg.gui.callouts,
            }[name]
        )
        try:
            if path.suffix == ".toml":
                with open(path, "rb") as fh:
                    return tomllib.load(fh)
            return json.loads(path.read_text())
        except FileNotFoundError:
            raise ProtocolError("not_found", f"config file not found: {path}") from None
        except (OSError, ValueError) as e:  # includes JSON and TOML decode errors
            raise ProtocolError("io_error", f"{path}: {e}") from None

    async def _config_http(self, request: web.Request) -> web.StreamResponse:
        try:
            return web.json_response(self._config_json(request.match_info["name"]))
        except ProtocolError as e:
            raise (
                web.HTTPNotFound(text=e.message)
                if e.code == "not_found"
                else web.HTTPInternalServerError(text=e.message)
            ) from None

    # ------------------------------------------------------------ WebSocket

    def _same_origin(self, request: web.Request) -> bool:
        """Browsers send Origin on WebSocket upgrades. A page from another origin must not be able to
        drive this core (it would be loopback to us and so allowed to control)."""
        origin = request.headers.get("Origin")
        if origin is None:
            return True  # not a browser (CLI, tests)
        return urlsplit(origin).netloc == request.headers.get("Host", "")

    async def _ws(self, request: web.Request) -> web.StreamResponse:
        if not self._same_origin(request):
            raise web.HTTPForbidden(text="cross-origin WebSocket refused")
        ws = web.WebSocketResponse(heartbeat=10.0, max_msg_size=1 << 20)
        await ws.prepare(request)
        addr = request.remote or ""
        client = Client(
            id=self.hub.new_client_id(),
            send=ws.send_str,
            close=ws.close,
            addr=addr,
            can_control=is_control_host(addr, self.ctx.cfg.server.control_hosts),
        )
        self.hub.add(client)
        try:
            async for msg in ws:
                if client.closed:
                    break
                if msg.type == WSMsgType.TEXT:
                    await self._on_text(client, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            await self.hub.remove(client)
            with contextlib.suppress(Exception):
                await ws.close()
        return ws

    async def _on_text(self, client: Client, text: str) -> None:
        rid = None
        try:
            try:
                msg = json.loads(text)
            except ValueError:
                raise ProtocolError("bad_request", "message is not valid JSON") from None
            if not isinstance(msg, dict) or not isinstance(msg.get("op"), str):
                raise ProtocolError("bad_request", "message must be an object with an 'op'")
            rid = msg.get("id")
            op = msg["op"]
            if op == "hello":
                data = self._hello(client, msg)
                client.post({"op": "welcome", "id": rid, **data})
                self._after_hello(client)
                return
            if not client.hello_done:
                raise ProtocolError("bad_request", "first message must be 'hello'")
            handler = self._handlers.get(op)
            if handler is None:
                raise ProtocolError("bad_request", f"unknown op {op!r}")
            data = await handler(client, msg)
            reply = {"op": "result", "id": rid, "ok": True, "data": {} if data is None else data}
        except ProtocolError as e:
            reply = {"op": "result", "id": rid, "ok": False, "error": {"code": e.code, "message": e.message}}
        except Exception as e:  # a handler bug must not kill the connection
            log.exception("handler failed")
            reply = {"op": "result", "id": rid, "ok": False, "error": {"code": "io_error", "message": repr(e)}}
        client.post(reply)

    # ------------------------------------------------------------ ops: session

    def _hello(self, client: Client, msg: dict) -> dict:
        if msg.get("protocol") != P.PROTOCOL_VERSION:
            raise ProtocolError("bad_request", f"unsupported protocol (this core speaks {P.PROTOCOL_VERSION})")
        kind = msg.get("client", "cli")
        if kind not in ("gui", "cli"):
            raise ProtocolError("bad_request", "client must be 'gui' or 'cli'")
        name = msg.get("name", kind)
        if not isinstance(name, str) or len(name) > 64:
            raise ProtocolError("bad_request", "name must be a string of at most 64 characters")
        client.kind, client.name, client.hello_done = kind, name, True
        return {
            "protocol": P.PROTOCOL_VERSION,
            "core_version": __version__,
            "client_id": client.id,
            "clock": self.ctx.clock.now() / 1e9,
            "can_control": client.can_control,
            "views": list(P.VIEWS),
        }

    def _after_hello(self, client: Client) -> None:
        client.post({"op": "schema", **REGISTRY.schema()})
        client.post(self._status_msg())
        rc = self._rc_msg(client)
        if rc is not None:
            client.post(rc)
        rqm = self._requests_msg()
        if rqm is not None:
            client.post(rqm)
        tcm = self._telecommands_msg()
        if tcm is not None:
            client.post(tcm)
        um = self._update_msg()
        if um is not None:
            client.post(um)

    async def _op_subscribe(self, client: Client, msg: dict) -> dict:
        req = P.parse_subscribe(msg)
        self.hub.subscribe(client, req)
        # the snapshot follows the result (design 4.2); it is computed when it is sent
        asyncio.get_running_loop().call_soon(self.hub.post_snapshot, client, req.view)
        return {}

    async def _op_unsubscribe(self, client: Client, msg: dict) -> dict:
        self.hub.unsubscribe(client, P.view_of(msg))
        return {}

    async def _op_get_snapshot(self, client: Client, msg: dict) -> dict:
        """Extension to design 4.2: optional `types`, `src` filters and `preferred` (one item per type, the
        state store's preferred source)."""
        src = msg.get("src")
        if src is not None and not isinstance(src, str):
            raise ProtocolError("bad_request", "src must be a string")
        return self.hub.snapshot(P.view_of(msg), P.check_types(msg.get("types", "*")), src, bool(msg.get("preferred")))

    async def _op_get_rates(self, client: Client, msg: dict) -> dict:
        """Per-stream data rates (docs/api.md). `view` "live" (default): frames measured on receive at host time;
        "replay": packet rates in replay time from the open log (empty when none is open). Observe-only."""
        view = P.view_of(msg)
        cfg = getattr(self.ctx, "cfg", None)
        if cfg is not None and hasattr(cfg, "rates"):
            cap = (float(cfg.rates.air_capacity_down_bytes_s), float(cfg.rates.air_capacity_up_bytes_s))
        else:
            cap = (6100.0, 6100.0)
        windows = WINDOWS_S
        if view == "live":
            tracker = self._need("rates", "the rate statistics")
            now = self.ctx.clock.now()
            streams = tracker.snapshot(now)
            return rates_to_json(streams, windows, now / 1e9, "live", cap, "frames", tracker, now)
        replayer = self._mod("replayer")
        streams, now = [], self._replay_clock()
        if replayer is not None:
            try:
                now, streams = await asyncio.to_thread(replayer.read_rates, windows)
            except (RuntimeError, NotImplementedError, OSError):
                streams = []  # no log open
        return rates_to_json(streams, windows, now / 1e9, "replay", cap, "packets")

    async def _op_get_history(self, client: Client, msg: dict) -> dict:
        """`buffer` (extension): "trail" (default, types with history=True at 20 Hz) or "plot" (the tuning
        graphs: full rate, about 2 min live from `plot_history`; in the replay view read from the open log)."""
        view, series, t0, t1, max_points = P.parse_history(msg)
        buffer = msg.get("buffer", "trail")
        if buffer not in ("trail", "plot"):
            raise ProtocolError("invalid_value", "buffer must be 'trail' or 'plot'")
        results: list[dict[str, list]] | None = None
        history = None
        if buffer == "trail":
            history = self.ctx.history if view == "live" else self._mod("replay_history")
        elif view == "live":
            history = self._mod("plot_history")
        else:
            replayer = self._mod("replayer")
            if replayer is not None and hasattr(replayer, "read_series"):
                try:
                    results = await asyncio.to_thread(replayer.read_series, series, t0, t1, max_points)
                except (RuntimeError, NotImplementedError, OSError):
                    results = None  # no log open (or it went away): empty series
        out = []
        for i, s in enumerate(series):
            if results is not None:
                res = results[i]
            elif history is None:
                res = {"t": [], **{f: [] for f in s["fields"]}}
            else:
                res = history.query(s["type"], s["src"], s["fields"], t0, t1, max_points)
            res = {**res, "t": [x / 1e9 for x in res["t"]]}
            ptype = REGISTRY.get(s["type"])
            for fname in s["fields"]:
                spec = ptype.field(fname)
                if spec is not None and spec.kind != "time_ns":
                    res[fname] = [_json_value(spec, v) for v in res[fname]]
                elif spec is not None:
                    res[fname] = [None if v is None else v / 1e9 for v in res[fname]]
            out.append({"type": s["type"], "src": s["src"], **res})
        return {"series": out}

    # ------------------------------------------------------------ ops: recording, logs, replay

    async def _op_rec_start(self, client: Client, msg: dict) -> dict:
        rec = self._need("recorder", "recording")
        label = msg.get("label")
        if label is not None and (not isinstance(label, str) or len(label) > 200):
            raise ProtocolError("bad_request", "label must be a string of at most 200 characters")
        return P.recording_info_json(await self._call(rec.start, label))

    async def _op_rec_stop(self, client: Client, msg: dict) -> dict:
        rec = self._need("recorder", "recording")
        info = await self._call(rec.stop)
        if info is None:
            raise ProtocolError("busy", "not recording")
        return P.recording_info_json(info)

    async def _op_rec_note(self, client: Client, msg: dict) -> dict:
        rec = self._need("recorder", "recording")
        text = P._req(msg, "text", str, "a string")
        if not text or len(text) > 1000:
            raise ProtocolError("invalid_value", "text must be 1..1000 characters")
        await self._call(rec.note, text)
        return {}

    async def _op_logs_list(self, client: Client, msg: dict) -> list:
        lib = self._need("library", "the log library")
        return [P.log_info_json(i) for i in await self._call(lib.list)]

    async def _op_replay_open(self, client: Client, msg: dict) -> dict:
        rp = self._need("replayer", "replay")
        name = P._req(msg, "name", str, "a string")
        info = await self._call(rp.open, name)
        self.replay_reset()
        return P.log_info_json(info)

    def _op_replay_simple(self, method: str) -> Handler:
        async def handler(client: Client, msg: dict) -> dict:
            rp = self._need("replayer", "replay")
            await self._call(getattr(rp, method))
            if method in ("close", "play"):  # play from `ended` restarts at the beginning
                self.replay_reset()
            return {}

        return handler

    async def _op_replay_seek(self, client: Client, msg: dict) -> dict:
        rp = self._need("replayer", "replay")
        t = P._number(msg, "t")
        assert t is not None
        await self._call(rp.seek, P.ns(t))
        self.replay_reset()
        return {}

    async def _op_replay_markers(self, client: Client, msg: dict) -> list:
        rp = self._need("replayer", "replay")
        return [{"t": m.t / 1e9, "kind": m.kind, "src": m.src, "label": m.label} for m in await self._call(rp.markers)]

    async def _op_replay_speed(self, client: Client, msg: dict) -> dict:
        rp = self._need("replayer", "replay")
        await self._call(rp.set_speed, P.parse_replay_speed(msg))
        return {}

    # ------------------------------------------------------------ ops: REQUEST (safety-relevant, thin pass-through)

    async def _op_request_send(self, client: Client, msg: dict) -> dict:
        rq = self._need("requests", "the request sender")
        if not client.can_control:  # control hosts only, enforced here as well as in the controller
            raise ProtocolError("control_denied", "this client is not on a control host (view only)")
        a = P.parse_request_send(msg)
        st = await self._call(
            lambda: rq.send(
                a["action"],
                a["target"],
                client=client.name or client.id,
                remote_addr=client.addr,
                confirm=a["confirm"],
                understand_link_stops=a["understand_link_stops"],
            )
        )
        self._push_requests()
        return rq._json(st)

    async def _op_request_list(self, client: Client, msg: dict) -> dict:
        return {"actions": self._need("requests", "the request sender").whitelist()}

    async def _op_request_status(self, client: Client, msg: dict) -> dict:
        return self._need("requests", "the request sender").status()

    def _request_routes(self) -> dict[str, str | None]:
        rq = self._mod("requests")
        return {} if rq is None else {str(t): rq.route(t) for t in (1, 2, 3)}

    def _requests_msg(self) -> dict | None:
        rq = self._mod("requests")
        return None if rq is None else {"op": "requests", **rq.status()}

    def _push_requests(self) -> None:
        m = self._requests_msg()
        if m is not None:
            self.hub.broadcast(m)

    # ------------------------------------------------------------ ops: TELECOMMAND (TEMPORARY, safety-relevant)

    async def _op_telecommand_send(self, client: Client, msg: dict) -> dict:
        tc = self._need("telecommands", "the telecommand sender")
        if not client.can_control:  # control hosts only, enforced here as well as in the controller
            raise ProtocolError("control_denied", "this client is not on a control host (view only)")
        a = P.parse_telecommand_send(msg)
        rec = await self._call(
            lambda: tc.send(
                a["name"],
                client=client.name or client.id,
                remote_addr=client.addr,
                confirm=a["confirm"],
                param=a["param"],
                param_float=a["param_float"],
            )
        )
        self._push_telecommands()
        return tc._json(rec)

    async def _op_telecommand_list(self, client: Client, msg: dict) -> dict:
        return {"commands": self._need("telecommands", "the telecommand sender").whitelist()}

    def _telecommands_msg(self) -> dict | None:
        tc = self._mod("telecommands")
        return None if tc is None else {"op": "telecommands", **tc.status(), "route": tc.route()}

    def _push_telecommands(self) -> None:
        m = self._telecommands_msg()
        if m is not None:
            self.hub.broadcast(m)

    # ------------------------------------------------------------ ops: self-update (docs/updating.md)

    _UPDATE_CODES = {  # UpdateError.code -> protocol error code
        "disabled": "not_implemented", "not_repo": "not_implemented", "busy": "busy", "git": "update_failed",
    }  # fmt: skip

    async def _update_call(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await fn()
        except UpdateError as e:
            raise ProtocolError(self._UPDATE_CODES.get(e.code, "update_refused"), e.message) from None

    def _update_svc(self) -> Any:
        return self._need("update", "self-update")

    async def _op_update_status(self, client: Client, msg: dict) -> dict:
        """Read-only, open to every client."""
        return await self._update_svc().status()

    async def _op_update_check(self, client: Client, msg: dict) -> dict:
        svc = self._update_svc()
        if not client.can_control:
            raise ProtocolError("control_denied", "only a control host may check for updates")
        return await self._update_call(svc.check)

    async def _op_update_apply(self, client: Client, msg: dict) -> dict:
        """Control hosts only, `confirm: true` required. The guards (armed, motors, mission running, recording, RC
        lease, request in flight) are checked in the service. On success the core restarts (exit code 42)."""
        svc = self._update_svc()
        if not client.can_control:
            raise ProtocolError("control_denied", "only a control host may apply an update")
        if msg.get("confirm") is not True:
            raise ProtocolError("invalid_value", "update.apply needs confirm: true")
        log.warning("update.apply requested by %s (%s)", client.name or client.id, client.addr)
        return await self._update_call(svc.apply)

    async def _op_core_quit(self, client: Client, msg: dict) -> dict:
        """Control hosts only, `confirm: true` required. Not a vehicle uplink: the core shuts down cleanly (recording
        closed, panel lease released, the command stream stopped, sources closed) and exits with code 0; start.sh
        does not restart."""
        if not client.can_control:
            raise ProtocolError("control_denied", "only a control host may stop the core")
        if msg.get("confirm") is not True:
            raise ProtocolError("invalid_value", "core.quit needs confirm: true")
        log.warning("core.quit requested by %s (%s)", client.name or client.id, client.addr)
        self.ctx.request_quit(f"{client.name or client.id} ({client.addr})")
        return {"ok": True, "message": "the core is stopping"}

    def _update_msg(self) -> dict | None:
        up = self._mod("update")
        return None if up is None else {"op": "update", **up.summary()}

    def _push_update(self) -> None:
        m = self._update_msg()
        if m is not None:
            self.hub.broadcast(m)

    def _push_update_progress(self, step: str, text: str) -> None:
        self.hub.broadcast({"op": "update_progress", "step": step, "text": text})

    # ------------------------------------------------------------ ops: RC (safety-relevant, thin pass-through)

    def _rc_lease_id(self, client: Client) -> str:
        """The lease this client may act on: its own, or (control hosts only) the current keepalive lease,
        so that separate short-lived CLI processes can drive and renew one keepalive lease (design 4.4)."""
        if client.lease_id:
            return client.lease_id
        rc = self._need("rc", "RC control")
        if client.can_control:
            try:
                lease = rc.status().lease
            except NotImplementedError:
                lease = None
            if lease is not None and lease.kind == "keepalive":
                return lease.id
        raise ProtocolError("no_lease", "no control lease held")

    async def _op_rc_acquire(self, client: Client, msg: dict) -> dict:
        rc = self._need("rc", "RC control")
        if not client.can_control:  # decision 10.1-4, enforced here as well as in the controller
            raise ProtocolError("control_denied", "this client is not on a control host (view only)")
        kind, idle = P.parse_rc_acquire(msg)
        if client.lease_id:
            cur = rc.status().lease  # a lease that ended (expiry, heartbeat loss) is not "held" any more
            if cur is not None and cur.id == client.lease_id:
                raise ProtocolError("busy", "this connection already holds a lease")
            client.lease_id = None
        lease = await self._call(rc.acquire, client.name or client.id, kind, idle, client.addr)
        client.lease_id = lease.id
        self._push_rc()
        return P.lease_json(lease)

    async def _op_rc_release(self, client: Client, msg: dict) -> dict:
        rc = self._need("rc", "RC control")
        lease_id = self._rc_lease_id(client)
        await self._call(rc.release, lease_id)
        client.lease_id = None
        self._push_rc()
        return {}

    async def _op_rc_heartbeat(self, client: Client, msg: dict) -> dict:
        rc = self._need("rc", "RC control")
        await self._call(rc.heartbeat, self._rc_lease_id(client))
        return {}

    async def _op_rc_set(self, client: Client, msg: dict) -> dict:
        rc = self._need("rc", "RC control")
        values = P.parse_rc_set(msg)
        hold_s = P.parse_rc_hold(msg)
        args = (self._rc_lease_id(client), values) if hold_s is None else (self._rc_lease_id(client), values, hold_s)
        st = await self._call(rc.set, *args)
        self._push_rc()
        return self._rc_body(st)

    def _engine_for_button(self, client: Client) -> Any:
        eng = self._mod("engine")
        if eng is None:
            raise ProtocolError("not_implemented", "no command engine in this core (replay or observer)")
        if not client.can_control:
            raise ProtocolError("control_denied", "this client is not on a control host (view only)")
        return eng

    async def _op_engine_abort(self, client: Client, msg: dict) -> dict:
        """GS ABORT: engine-level override, no lease needed (works with the Pocket live, the panel or no input)."""
        eng = self._engine_for_button(client)
        eng.abort()
        self._push_rc()
        return self._rc_body(self._mod("rc").status()) if self._mod("rc") else {"command": eng.status()}

    async def _op_engine_failsafe(self, client: Client, msg: dict) -> dict:
        """GS FAILSAFE request: `on` true latches it, false clears it (the engine's safe sequence)."""
        eng = self._engine_for_button(client)
        eng.set_failsafe(P._req(msg, "on", bool, "a boolean"))
        self._push_rc()
        return self._rc_body(self._mod("rc").status()) if self._mod("rc") else {"command": eng.status()}

    async def _op_rc_unlock(self, client: Client, msg: dict) -> dict:
        rc = self._need("rc", "RC control")
        st = await self._call(rc.unlock, self._rc_lease_id(client), P.parse_rc_control(msg))
        self._push_rc()
        return self._rc_body(st)

    async def _op_rc_lock(self, client: Client, msg: dict) -> dict:
        rc = self._need("rc", "RC control")
        st = await self._call(rc.lock, self._rc_lease_id(client), P.parse_rc_control(msg))
        self._push_rc()
        return self._rc_body(st)

    def _client_gone(self, client: Client) -> None:
        """Decision 10.1-1: when the controlling client disappears, sending stops. A connection lease ends
        with its connection. (Keepalive leases, used by short-lived CLI processes, end by idle expiry, at most 9 s.)"""
        rc = self._mod("rc")
        if rc is None or not client.lease_id:
            return
        try:
            st = rc.status()
            if st.lease is not None and st.lease.id == client.lease_id and st.lease.kind == "connection":
                rc.release(client.lease_id)
        except NotImplementedError:
            pass
        client.lease_id = None

    # ------------------------------------------------------------ ops: sources, config

    async def _op_sources_list(self, client: Client, msg: dict) -> list:
        return [P.source_status_json(s.status()) for s in self.ctx.sources]

    async def _op_sources_enable(self, client: Client, msg: dict) -> dict:
        """Enable or disable a source (docs/api.md). Disabling is a true disconnect: the port is closed and
        nothing is received or sent through it until it is enabled again. Control hosts only: it changes what
        reaches the vehicle."""
        sid = P._req(msg, "source", str, "a string")  # not `id`: that is the request id
        enabled = P._req(msg, "enabled", bool, "a boolean")
        src = next((s for s in self.ctx.sources if s.id == sid), None)
        if src is None:
            raise ProtocolError("not_found", f"unknown source {sid!r}")
        if not client.can_control:
            raise ProtocolError("control_denied", "only a control host may enable or disable a source")
        fn = getattr(self.ctx, "set_source_enabled", None)
        if fn is None:
            raise ProtocolError("not_implemented", "run-time enable/disable of sources is not implemented")
        await self._call(fn, sid, enabled)
        msg = self._status_msg()
        self._check_source_events(msg)
        self.hub.broadcast(msg)
        return P.source_status_json(src.status())

    async def _op_config_get(self, client: Client, msg: dict) -> Any:
        name = P._req(msg, "name", str, "a string")
        return self._config_json(name)

    # ------------------------------------------------------------ status and rc push

    def _rc_body(self, st: Any) -> dict:
        """The rc status plus the command engine's report. This class and `rc.*` send nothing: the engine is the
        only sender, so `sending`, `target` and `targets` come from it."""
        body = P.rc_status_json(st)
        eng = self._mod("engine")
        if eng is not None:
            cmd = eng.status()
            body["command"] = cmd
            body["sending"] = cmd["sending"]
            body["target"] = ", ".join(cmd["links"]) or None
            body["targets"] = list(cmd["links"])
        return body

    def _rc_json(self) -> dict | None:
        rc = self._mod("rc")
        if rc is None:
            return None
        try:
            return self._rc_body(rc.status())
        except NotImplementedError:
            return None

    def _rc_msg(self, client: Client, body: dict | None = None) -> dict | None:
        body = body if body is not None else self._rc_json()
        if body is None:
            return None
        lease = body["lease"]
        return {"op": "rc", **body, "holder_is_you": bool(lease and lease["id"] == client.lease_id)}

    def _status_msg(self) -> dict:
        c = self.ctx
        rec: dict[str, Any] = {"active": False, "available": False}
        recorder = self._mod("recorder")
        if recorder is not None:
            try:
                rec = {**P.recorder_status_json(recorder.status()), "available": True}
            except NotImplementedError:
                pass
        replay: dict[str, Any] = {"state": "closed", "available": False}
        rp = self._mod("replayer")
        if rp is not None:
            try:
                replay = {**P.replay_status_json(rp.status()), "available": True}
            except NotImplementedError:
                pass
        return {
            "op": "status",
            "clock": c.clock.now() / 1e9,
            "sources": [P.source_status_json(s.status()) for s in c.sources],
            "recorder": rec,
            "replay": replay,
            "rc": self._rc_json(),  # extension: null when no RC controller is present
            "telecommand_route": None if self._mod("telecommands") is None else self._mod("telecommands").route(),
            "request_routes": self._request_routes(),  # target node -> id of the source a request would use
            "clients": len(self.hub.clients),
        }

    def _check_source_events(self, status: dict) -> None:
        for s in status["sources"]:
            old = self._last_source_state.get(s["id"])
            if old is not None and old != s["state"]:
                lvl = "info" if s["state"] == "connected" else "warn"
                self.event(lvl, f"source {s['id']}: {old} -> {s['state']}")
            self._last_source_state[s["id"]] = s["state"]

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(STATUS_PERIOD_S)
            try:
                msg = self._status_msg()
                self._check_source_events(msg)
                self.hub.broadcast(msg)
            except Exception:
                log.exception("status loop")

    def _push_rc(self) -> None:
        body = self._rc_json()
        if body is None:
            return
        key = json.dumps({k: v for k, v in body.items() if k != "t"}, sort_keys=True)
        changed = key != self._last_rc
        self._last_rc = key
        if changed or body["sending"] or body["mode"] in ("pocket", "virtual"):
            for c in list(self.hub.clients.values()):
                if c.hello_done:
                    m = self._rc_msg(c, body)
                    if m:
                        c.post(m)

    async def _rc_loop(self) -> None:
        while True:
            await asyncio.sleep(RC_PERIOD_S)
            try:
                self._push_rc()
            except Exception:
                log.exception("rc loop")


def _json_value(spec: Any, v: Any) -> Any:
    """History values: same JSON forms as packets (enum names, non-finite floats as null)."""
    if v is None:
        return None
    val, _ = P._enc(spec, v)
    return val
