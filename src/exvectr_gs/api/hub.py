"""Hub: tracks clients and their per-view subscriptions and pushes messages (design 2.3, 4.3).

Backpressure rules (never block the core):
- The bus handler only stores the packet in each interested client's pending structure (O(1)).
- `latest` subscriptions coalesce: only the newest packet per (type, src) is kept until the next flush.
- `all` subscriptions use a bounded deque that drops the oldest packet and counts `dropped`.
- Each client has its own writer task, so a slow socket only delays its own task.
- Control messages (results, status, events) queue per client with a bound. A client that does not read
  them is disconnected.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.bus import Bus
from ..core.state import StateStore
from ..packets import REGISTRY, Packet, PacketRegistry
from .protocol import SubscribeRequest, item_to_json

log = logging.getLogger(__name__)

ALL_QUEUE_MAX = 20_000  # packets per client and view in `all` mode
CTRL_QUEUE_MAX = 2_000  # control messages per client
WRITE_TIMEOUT_S = 15.0  # a socket that accepts nothing for this long is dropped


def _type_of(pkt: Packet) -> str:
    return pkt.TYPE if pkt.TYPE != "Unknown" else getattr(pkt, "type", "Unknown")


@dataclass
class View:
    name: str
    bus: Bus | None
    store: StateStore
    clock: Callable[[], int]  # view clock in ns
    unsubscribe: Callable[[], None] | None = None


class Subscription:
    def __init__(self, req: SubscribeRequest):
        self.types = req.types
        self.mode = req.mode
        self.period = 1.0 / req.max_rate_hz
        self.latest: dict[tuple[str, str], Packet] = {}
        self.queue: deque[Packet] = deque(maxlen=ALL_QUEUE_MAX)
        self.dropped = 0
        self.last_flush = 0.0  # monotonic seconds

    def wants(self, type_name: str) -> bool:
        return self.types is None or type_name in self.types

    def offer(self, pkt: Packet) -> None:
        if self.mode == "latest":
            self.latest[(_type_of(pkt), pkt.src)] = pkt
        else:
            if len(self.queue) == self.queue.maxlen:
                self.dropped += 1
            self.queue.append(pkt)

    @property
    def pending(self) -> bool:
        return bool(self.latest) if self.mode == "latest" else bool(self.queue)

    def take(self) -> tuple[list[Packet], int]:
        if self.mode == "latest":
            pkts = list(self.latest.values())
            self.latest.clear()
        else:
            pkts = list(self.queue)
            self.queue.clear()
        dropped, self.dropped = self.dropped, 0
        return pkts, dropped


@dataclass(eq=False)
class Client:
    id: str
    send: Callable[[str], Awaitable[None]]
    close: Callable[[], Awaitable[None]]
    addr: str = ""
    can_control: bool = False
    name: str = ""
    kind: str = ""
    hello_done: bool = False
    lease_id: str | None = None  # lease this client acquired (connection leases end with the client)
    subs: dict[str, Subscription] = field(default_factory=dict)
    ctrl: deque[str] = field(default_factory=deque)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False
    task: asyncio.Task | None = None
    sent_messages: int = 0

    def post(self, msg: dict) -> None:
        """Queue a control message (JSON object). Over the bound, the client is considered dead."""
        if self.closed:
            return
        if len(self.ctrl) >= CTRL_QUEUE_MAX:
            self.closed = True
            self.ctrl.clear()
            self.wake.set()
            return
        self.ctrl.append(json.dumps(msg, separators=(",", ":")))
        self.wake.set()


class Hub:
    def __init__(self, registry: PacketRegistry = REGISTRY, clock: Callable[[], float] = time.monotonic):
        self.registry = registry
        self._clock = clock
        self.views: dict[str, View] = {}
        self.clients: dict[str, Client] = {}
        self._next_id = 1
        self.on_client_gone: Callable[[Client], None] | None = None

    # ---- views

    def add_view(self, view: View) -> None:
        self.views[view.name] = view
        if view.bus is not None:
            view.unsubscribe = view.bus.subscribe(lambda pkt, n=view.name: self._on_packet(n, pkt))

    def close(self) -> None:
        for v in self.views.values():
            if v.unsubscribe:
                v.unsubscribe()
                v.unsubscribe = None

    def view_clock_s(self, view: str) -> float:
        return self.views[view].clock() / 1e9

    # ---- bus side (synchronous, must stay cheap)

    def _on_packet(self, view: str, pkt: Packet) -> None:
        name = _type_of(pkt)
        for c in self.clients.values():
            sub = c.subs.get(view)
            if sub is not None and sub.wants(name):
                was = sub.pending
                sub.offer(pkt)
                if not was:
                    c.wake.set()

    # ---- clients

    def new_client_id(self) -> str:
        cid = f"c{self._next_id}"
        self._next_id += 1
        return cid

    def add(self, client: Client) -> None:
        self.clients[client.id] = client
        client.task = asyncio.create_task(self._writer(client), name=f"ws-writer-{client.id}")

    async def remove(self, client: Client) -> None:
        client.closed = True
        self.clients.pop(client.id, None)
        client.wake.set()
        if client.task and client.task is not asyncio.current_task():
            client.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await client.task
        if self.on_client_gone:
            try:
                self.on_client_gone(client)
            except Exception:
                log.exception("on_client_gone failed")

    def broadcast(self, msg: dict, only_hello: bool = True) -> None:
        for c in list(self.clients.values()):
            if c.hello_done or not only_hello:
                c.post(msg)

    # ---- subscriptions

    def subscribe(self, client: Client, req: SubscribeRequest) -> None:
        client.subs[req.view] = Subscription(req)

    def unsubscribe(self, client: Client, view: str) -> None:
        client.subs.pop(view, None)

    def snapshot(
        self, view: str, types: frozenset[str] | None = None, src: str | None = None, preferred: bool = False
    ) -> dict:
        v = self.views[view]
        now = v.clock()
        entries = v.store.snapshot()
        fsrc: dict[int, Any] = {}
        if preferred:
            seen: dict[str, Any] = {}
            for e in entries:
                n = _type_of(e.packet)
                if n not in seen:
                    m = v.store.get_merged(n, now) if src is None else None
                    if m is not None:
                        seen[n] = m.entry
                        fsrc[id(m.entry)] = m.fields
                    else:
                        seen[n] = v.store.get(n, src, now)
            entries = [e for e in seen.values() if e is not None]
        items = [
            item_to_json(e.packet, e.interval_s, e.stale_after_s, fsrc.get(id(e)))
            for e in entries
            if (types is None or _type_of(e.packet) in types) and (src is None or e.packet.src == src)
        ]
        return {"view": view, "clock": now / 1e9, "priority": v.store.priority, "items": items}

    def post_snapshot(self, client: Client, view: str) -> None:
        sub = client.subs.get(view)
        if sub is not None:
            client.post({"op": "snapshot", **self.snapshot(view, sub.types)})

    def resend_snapshots(self, view: str) -> None:
        """After a replay seek or open: clients replace their store for that view."""
        for c in self.clients.values():
            if view in c.subs:
                self.post_snapshot(c, view)

    # ---- writer

    def _flush_msgs(self, client: Client, now_m: float) -> tuple[list[str], float | None]:
        """Build due packet batches. Returns (messages, seconds until the next batch is due)."""
        out: list[str] = []
        wait: float | None = None
        for view, sub in list(client.subs.items()):
            if not sub.pending:
                continue
            due = sub.last_flush + sub.period - now_m
            if due > 0:
                wait = due if wait is None else min(wait, due)
                continue
            pkts, dropped = sub.take()
            sub.last_flush = now_m
            v = self.views[view]
            items = []
            for p in pkts:
                if sub.mode == "latest":
                    e = v.store.get(_type_of(p), p.src)
                    items.append(item_to_json(p, e.interval_s if e else None, e.stale_after_s if e else None))
                else:
                    items.append(item_to_json(p))
            msg: dict[str, Any] = {"op": "packets", "view": view, "clock": v.clock() / 1e9, "items": items}
            if dropped:
                msg["dropped"] = dropped
            out.append(json.dumps(msg, separators=(",", ":")))
        return out, wait

    async def _writer(self, client: Client) -> None:
        try:
            while not client.closed:
                wait: float | None = None
                while client.ctrl:
                    await asyncio.wait_for(client.send(client.ctrl.popleft()), WRITE_TIMEOUT_S)
                    client.sent_messages += 1
                msgs, wait = self._flush_msgs(client, self._clock())
                for m in msgs:
                    await asyncio.wait_for(client.send(m), WRITE_TIMEOUT_S)
                    client.sent_messages += 1
                if client.ctrl or msgs:
                    await asyncio.sleep(0)  # let the loop run other tasks between batches
                    continue
                client.wake.clear()
                if client.closed:
                    break
                try:
                    await asyncio.wait_for(client.wake.wait(), wait)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as e:  # send failure, timeout: the client is gone
            log.info("client %s writer ended: %r", client.id, e)
        finally:
            client.closed = True
            self.clients.pop(client.id, None)
            with contextlib.suppress(Exception):
                await client.close()
