"""UpdateService: status / check / apply for the API and the CLI, with the guards and the restart request."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.config import UpdateSection
from . import gitops
from .gitops import UpdateError

log = logging.getLogger(__name__)


class UpdateService:
    def __init__(
        self,
        cfg: UpdateSection,
        repo: Path,
        guards: Callable[[], list[str]],
        request_restart: Callable[[str], None],
    ):
        self.cfg = cfg
        self.repo = Path(repo)
        self._guards = guards
        self._request_restart = request_restart
        self.last_check: dict[str, Any] | None = None  # {at, ok, behind, ..., error}
        self.restart_pending = False  # code changed on disk, restart refused by a guard or not yet done
        self.busy = False
        self.on_change: Callable[[], None] | None = None  # the API pushes the summary to all clients
        self.on_progress: Callable[[str, str], None] | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ reads

    def guards(self) -> list[str]:
        return self._guards()

    def summary(self) -> dict[str, Any]:
        """What every client gets pushed (the header badge): the last check and the pending restart."""
        lc = self.last_check or {}
        return {
            "enabled": self.cfg.enabled, "busy": self.busy, "restart_pending": self.restart_pending,
            "checked_at": lc.get("at"), "behind": lc.get("behind", 0) if lc.get("ok") else 0, "error": lc.get("error"),
        }  # fmt: skip

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    async def status(self) -> dict[str, Any]:
        st = await asyncio.to_thread(gitops.status, self.repo, self.cfg.branch, self.cfg.remote)
        try:
            from .. import __version__

            st["version"] = __version__
            import exvectr_packets

            st["schema_hash_running"] = f"0x{exvectr_packets.SCHEMA_HASH:08X}"
        except (ImportError, AttributeError):
            pass
        st.update(enabled=self.cfg.enabled, allow_switch=self.cfg.allow_switch, last_check=self.last_check,
                  restart_pending=self.restart_pending, busy=self.busy, guards=self.guards())  # fmt: skip
        return st

    # ------------------------------------------------------------ check / apply

    def _need_enabled(self) -> None:
        if not self.cfg.enabled:
            raise UpdateError("disabled", "self-update is disabled in the config ([update] enabled = false)")

    async def check(self, quiet: bool = False) -> dict[str, Any]:
        self._need_enabled()
        if self._lock.locked():
            raise UpdateError("busy", "an update check or apply is already running")
        async with self._lock:
            self.busy = True
            self._changed()
            try:
                res = await asyncio.to_thread(
                    gitops.check, self.repo, self.cfg.branch, self.cfg.remote, self.cfg.allow_switch
                )
                self.last_check = {"at": time.time(), "ok": True, **res}
                (log.debug if quiet else log.info)("update check: behind %d, ahead %d", res["behind"], res["ahead"])
                return res
            except UpdateError as e:
                self.last_check = {"at": time.time(), "ok": False, "error": e.message, "code": e.code}
                (log.debug if quiet else log.warning)("update check failed: %s", e.message)
                raise
            finally:
                self.busy = False
                self._changed()

    async def apply(self) -> dict[str, Any]:
        """Refuses on a guard, then runs the procedure off the loop. On success asks the core to restart."""
        self._need_enabled()
        if self._lock.locked():
            raise UpdateError("busy", "an update check or apply is already running")
        async with self._lock:
            why = self.guards()
            if why:
                raise UpdateError("guard", "update refused: " + "; ".join(why))
            self.busy = True
            self._changed()
            loop = asyncio.get_running_loop()

            def progress(step: str, text: str) -> None:
                if self.on_progress:
                    loop.call_soon_threadsafe(self.on_progress, step, text)

            try:
                res = await asyncio.to_thread(
                    gitops.apply, self.repo, self.cfg.branch, self.cfg.remote, self.cfg.allow_switch, progress,
                    self.restart_pending,
                )  # fmt: skip
            finally:
                self.busy = False
            if res.new != res.old:
                self.restart_pending = True  # the code on disk changed, whatever happened afterwards
            if res.restart:
                why = self.guards()  # state may have changed during the steps
                if why:
                    res.restart = False
                    res.message += "; restart NOT done (" + "; ".join(why) + "). Apply again when it is safe."
                else:
                    self._request_restart(res.message)
            elif res.ok:
                self.restart_pending = False
            self.last_check = None if res.ok else self.last_check
            self._changed()
            return res.json()

    async def auto_check_loop(self) -> None:
        """Quiet periodic check (`auto_check_interval_s` > 0): a failure only goes to the debug log."""
        if not self.cfg.enabled or self.cfg.auto_check_interval_s <= 0:
            return
        while True:
            await asyncio.sleep(self.cfg.auto_check_interval_s)
            with contextlib.suppress(UpdateError):
                await self.check(quiet=True)
