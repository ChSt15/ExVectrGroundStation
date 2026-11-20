// 'Software' panel (docs/updating.md): version, check for updates, new commits, 'Update and restart' (hold 1 s) with
// the reason when it is disabled, the progress output, and the reconnect after the restart. The core enforces the
// control-host rule, the confirm flag and the guards again.
import { h } from "../ui.js";
import { HoldTimer, UPDATE_HOLD_MS, applyState, checkNotes, describeVersion, liveGuards } from "./update_logic.js";

export function createUpdatePanel(el, ctx) {
  let status = null;
  let check = null; // the last check result of this page, or the core's last_check
  let summary = null; // the core's `update` push
  let busy = false;
  let awaitingRestart = null; // sha before the apply: after the reconnect a different sha reloads the page
  let poll = null;
  const version = h("div", { class: "up-version", "data-testid": "up-version" });
  const details = h("div", { class: "up-details", "data-testid": "up-details" });
  const checkBtn = h("button", { class: "up-check", "data-testid": "up-check" }, "Check for updates");
  const bar = h("span", { class: "bar" });
  const applyBtn = h("button", { class: "rq-btn up-apply", "data-testid": "up-apply", title: `hold ${UPDATE_HOLD_MS / 1000} s` }, h("span", { class: "rq-label", text: "Update and restart" }), bar);
  const why = h("div", { class: "up-why", "data-testid": "up-why" });
  const commits = h("ul", { class: "up-commits", "data-testid": "up-commits" });
  const notes = h("div", { class: "up-notes" });
  const out = h("pre", { class: "up-out", "data-testid": "up-out", hidden: true });
  const msg = h("div", { class: "rq-msg", role: "status", "data-testid": "up-msg" });
  el.append(version, details, h("div", { class: "up-actions" }, checkBtn, applyBtn), why, commits, notes, out, msg);

  const canControl = () => !!(ctx.api.welcome && ctx.api.welcome.can_control);
  const guards = () => liveGuards({
    apiState: ctx.api.state,
    view: ctx.view(),
    canControl: canControl(),
    vehicle: ctx.store.get("live", "VehicleStatus"),
    recorder: (ctx.status() || {}).recorder,
    rc: ctx.rc(),
    coreGuards: status && status.guards,
  });

  async function load() {
    try {
      status = await ctx.api.request("update.status", {}, 8000);
      if (!check && status.last_check && status.last_check.ok) check = status.last_check;
      if (awaitingRestart !== null && status.sha && status.sha !== awaitingRestart) {
        msg.textContent = `Restarted: now running ${status.sha}. Reloading the page ...`;
        setTimeout(() => location.reload(), 1200);
        awaitingRestart = null;
      } else if (awaitingRestart !== null) {
        msg.textContent = "Core is back, same version as before the update.";
        awaitingRestart = null;
      }
    } catch (e) {
      status = null;
      msg.textContent = `update.status: ${e.message}`;
    }
    render();
  }

  async function doCheck() {
    busy = true;
    msg.textContent = "Checking (git fetch) ...";
    render();
    try {
      check = await ctx.api.request("update.check", {}, 90000);
      msg.textContent = check.behind ? "" : "Up to date.";
    } catch (e) {
      check = null;
      msg.textContent = `check failed: ${e.message}`;
    }
    busy = false;
    await load();
  }

  async function doApply() {
    busy = true;
    out.hidden = false;
    out.textContent = "";
    msg.textContent = "Updating ...";
    awaitingRestart = status ? status.sha : "";
    render();
    try {
      const r = await ctx.api.request("update.apply", { confirm: true }, 1200000);
      msg.textContent = r.message + (r.restart ? ". The core restarts now; this page reconnects by itself." : "");
      if (!r.restart) awaitingRestart = null;
      if (!out.textContent) for (const s of r.steps) out.textContent += `[${s.name}] ${s.ok ? "ok" : "FAILED"}\n${s.output}\n`;
    } catch (e) {
      msg.textContent = `update refused or failed: ${e.message}`;
      awaitingRestart = null;
    }
    busy = false;
    check = null;
    await load();
  }

  const timer = new HoldTimer(() => { stopAnim(); doApply(); }, undefined, UPDATE_HOLD_MS);
  let anim = null;
  function stopAnim() {
    if (anim !== null) clearInterval(anim);
    anim = null;
    bar.style.width = "0";
  }
  const stop = () => { timer.cancel(); stopAnim(); };
  function start() {
    if (applyBtn.disabled || timer.holding) return;
    timer.start();
    anim = setInterval(() => { bar.style.width = `${Math.round(timer.progress() * 100)}%`; }, 40);
  }
  applyBtn.addEventListener("pointerdown", (e) => {
    try {
      applyBtn.setPointerCapture(e.pointerId);
    } catch (err) {
      /* synthetic pointer ids have no capture */
    }
    start();
  });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture", "pointerleave", "blur"]) applyBtn.addEventListener(ev, stop);
  applyBtn.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { start(); e.preventDefault(); } });
  applyBtn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") stop(); });
  checkBtn.addEventListener("click", doCheck);

  function render() {
    version.textContent = describeVersion(status);
    const g = guards();
    const pending = !!((summary && summary.restart_pending) || (status && status.restart_pending));
    const st = applyState({ status, check, guards: g, restartPending: pending });
    applyBtn.disabled = busy || !st.enabled;
    if (applyBtn.disabled) stop();
    applyBtn.dataset.why = busy ? "busy" : st.reason;
    why.textContent = busy ? "" : st.enabled ? (pending ? "The code on disk is newer than the running core: apply to restart." : "") : st.reason;
    checkBtn.disabled = busy || !canControl() || ctx.api.state !== "open" || !status || status.git === false || status.enabled === false;
    checkBtn.title = canControl() ? "git fetch and compare with the remote" : "this client is on a view-only host";
    const rows = [];
    if (status && status.git !== false) {
      rows.push(`following ${status.remote}/${status.configured_branch}${status.allow_switch ? " (allow_switch)" : ""}`);
      rows.push(status.dirty ? `LOCAL CHANGES: ${(status.dirty_files || []).join("; ")}` : "working tree clean");
      rows.push(`packets schema ${status.schema_hash || "?"} on disk${status.schema_hash_running ? `, ${status.schema_hash_running} running` : ""}`);
      for (const s of status.submodules || []) rows.push(`${s.path} ${s.sha} (${s.state})`);
      if (status.version) rows.push(`ground station ${status.version}`);
    }
    details.replaceChildren(...rows.map((t) => h("div", { text: t })));
    details.dataset.dirty = String(!!(status && status.dirty));
    const cs = check && check.commits ? check.commits : [];
    commits.replaceChildren(...cs.map((c) => h("li", { "data-sha": c.sha }, h("code", { text: c.sha }), ` ${c.subject}`)));
    notes.replaceChildren(...checkNotes(check).map((t) => h("div", { text: t })));
  }

  function mounted() {
    return !el.closest("[hidden]");
  }
  poll = setInterval(() => { if (!busy && ctx.api.state === "open" && mounted()) load(); }, 3000);

  return {
    onReady: load,
    onUpdate(m) {
      summary = m;
      render();
    },
    onUpdateProgress(m) {
      out.hidden = false;
      out.textContent += `[${m.step}] ${m.text}\n`;
    },
    onStatus: render,
    onRc: render,
    onTick: render,
    onViewChange: render,
    destroy() { stop(); clearInterval(poll); },
    debug: () => ({ status, check, summary, busy, awaitingRestart }),
  };
}
