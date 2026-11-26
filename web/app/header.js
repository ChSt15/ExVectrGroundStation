// Header bar: brand, layout views (Bench / Tuning), LIVE / REPLAY data view, RC state (as the core reports
// it), radio link quality, the recorder block (start / stop, name, duration, size, free space, errors), connection
// and theme. Recording is manual: Start begins a recording at once, Stop needs no confirmation.
import { h } from "./ui.js";
import { DIRECTIONS, chipText, chipTitle, directionState } from "./link/lq_logic.js";
import { describeRecorder } from "./status/source_logic.js";
import { badgeState } from "./update/update_logic.js";
import { holdControl } from "./command/hold_ctl.js";
import { HOLD_MS, headerButtons } from "./command/button_logic.js";

export const HEADER_CONSUMES = ["TxModuleStats", "RxModuleStats"];

const RC_TEXT = { virtual: "VIRTUAL", pocket: "POCKET", idle: "IDLE" };

export function createHeader(ctx, opts) {
  // layout views
  const viewBtns = new Map();
  const views = h("nav", { class: "seg views", role: "tablist", "aria-label": "layout view" });
  opts.views.forEach((v, i) => {
    const b = h("button", { role: "tab", text: v.label, title: `${v.hint || v.label} (Alt+${i + 1})`, "data-view": v.id, onclick: () => opts.onLayoutView(v.id) });
    viewBtns.set(v.id, b);
    views.append(b);
  });
  // data view
  const dataBtns = {};
  const toggle = h("div", { class: "toggle seg", role: "group", "aria-label": "data view" });
  for (const v of ["live", "replay"]) {
    dataBtns[v] = h("button", { text: v.toUpperCase(), "data-dv": v, onclick: () => ctx.switchView(v) });
    toggle.append(dataBtns[v]);
  }
  // RC and link chips
  const rc = h("span", { class: "chip rc-chip", "data-testid": "rc-chip", title: "Command stream of the core's command engine and its input (docs/radio.md)" });
  rc.addEventListener("click", () => window.exgs?.dock?.open("radio"));
  // one chip per link direction (link/lq_logic.js): DL = downlink ship -> GS (TxModuleStats), UL = uplink GS -> ship
  const linkChips = DIRECTIONS.map((dir) => {
    const chip = h("span", { class: "chip link-chip", "data-testid": `link-chip-${dir.id}`, "data-direction": dir.id });
    return { dir, chip };
  });
  // software update badge (docs/updating.md): shown when a check found new commits or a restart is pending
  const upd = h("button", { class: "chip upd-chip", "data-testid": "update-badge", hidden: true, "aria-label": "software update available: open the Software tab" });
  upd.addEventListener("click", () => window.exgs?.dock?.open("update"));
  // vehicle calibration problem chip (callouts/logwatch.js): from the repeated warning line, clears when it stops
  const calChip = h("button", { class: "chip cal-chip", "data-testid": "cal-chip", hidden: true, "aria-label": "vehicle calibration problem: open the Console" });
  calChip.addEventListener("click", () => window.exgs?.dock?.open("console"));
  // recorder block
  const recDot = h("span", { class: "rec-dot" });
  const recState = h("span", { class: "rec-state", "data-testid": "rec-state" }, recDot, h("span", { class: "rec-t" }));
  const recName = h("span", { class: "rec-name" });
  const recSize = h("span", { class: "rec-size" });
  const recFree = h("span", { class: "rec-free" });
  const recErr = h("span", { class: "rec-err", role: "alert" });
  const recMsg = h("span", { class: "rec-err" });
  const start = h("button", { class: "rec-start", "data-testid": "rec-start", title: "start recording now (manual only)" }, h("span", { class: "rec-dot" }), " Record");
  const stop = h("button", { class: "rec-stop", "data-testid": "rec-stop", title: "stop recording (no confirmation)" }, "■ Stop");
  const rec = h("div", { class: "rec", role: "group", "aria-label": "recording" }, recState, recName, recSize, recFree, start, stop, recErr, recMsg);
  async function recOp(op) {
    start.disabled = stop.disabled = true;
    try {
      await ctx.api.request(op, {});
      recMsg.textContent = "";
    } catch (e) {
      recMsg.textContent = `${op}: ${e.message}`;
    }
  }
  start.addEventListener("click", () => recOp("rec.start"));
  stop.addEventListener("click", () => recOp("rec.stop"));
  // connection, theme
  const conn = h("span", { class: "chip conn", "data-testid": "conn" }, h("span", { class: "dot" }), h("span", { class: "conn-t" }));
  const spk = h("button", { class: "chip spk", "data-testid": "callouts-icon", "aria-label": "callouts (audio notifications): open settings" });
  spk.addEventListener("click", () => window.exgs?.dock?.open("callouts"));
  const theme = h("button", { class: "theme", title: "switch dark / light (high contrast)", "aria-label": "switch dark or light theme", onclick: () => opts.onTheme() }, "◐");
  // GS ABORT (yellow) and FAILSAFE (red): held 500 ms, no dialog. They call the core's command engine (`engine.abort`,
  // `engine.failsafe`), so they act with any input and even if this tab closes right after.
  let failsafeOn = true;
  const sendEngine = (op, params) => ctx.api.request(op, params, 3000).catch((e) => { recMsg.textContent = `${op}: ${e.message}`; });
  const abortB = holdControl({ label: "ABORT", ms: HOLD_MS, cls: "abort-btn", testid: "abort-btn", title: "hold 500 ms: go off at once", onDone: () => sendEngine("engine.abort", {}) });
  const failB = holdControl({ label: "FAILSAFE", ms: HOLD_MS, cls: "failsafe-btn", testid: "failsafe-btn", title: "hold 500 ms: request failsafe", onDone: () => sendEngine("engine.failsafe", { on: failsafeOn }) });
  const safetyNote = h("span", { class: "safety-note", "data-testid": "safety-note" });
  const safety = h("div", { class: "safety-btns", role: "group", "aria-label": "ground station abort and failsafe" }, safetyNote, abortB.btn, failB.btn);
  const el = h(
    "header",
    { class: "top" },
    h("strong", { class: "brand", text: "ExVectr GS" }),
    views,
    toggle,
    h("span", { class: "grow" }),
    calChip,
    upd,
    rc,
    ...linkChips.map((c) => c.chip),
    rec,
    spk,
    conn,
    theme,
    safety,
  );

  function setLayoutView(id) {
    for (const [v, b] of viewBtns) b.setAttribute("aria-selected", String(v === id));
  }

  function render() {
    const view = ctx.view();
    for (const v of Object.keys(dataBtns)) dataBtns[v].setAttribute("aria-pressed", String(v === view));
    const now = ctx.store.now(view);
    for (const { dir, chip } of linkChips) {
      const e = ctx.store.get(view, dir.type);
      const st = directionState(dir, e, now ?? Infinity, ctx.store.schema.get(dir.type));
      chip.textContent = chipText(st);
      chip.title = chipTitle(st);
      chip.dataset.state = st.state;
      chip.dataset.tone = st.tone;
    }
    // RC state: what the core reports it does (never inferred from clicks)
    const r = ctx.rc();
    const open = ctx.connState() === "open";
    rc.textContent = !open ? "CMD —" : !r ? "CMD n/a" : `CMD ${r.sending ? "SENDING" : "NO LINK"} · ${RC_TEXT[r.mode] || r.mode}${r.command && r.command.pocket === "lost" ? " · POCKET LOST" : ""}`;
    rc.dataset.mode = open && r ? r.mode : "";
    rc.dataset.sending = String(!!(open && r && r.sending));
    const hb = headerButtons({ open, rc: r, canControl: ctx.api.welcome && ctx.api.welcome.can_control });
    abortB.setDisabled(hb.abort.disabled);
    failB.setDisabled(hb.failsafe.disabled);
    abortB.btn.title = hb.abort.title;
    failB.btn.title = hb.failsafe.title;
    failB.setLabel(hb.failsafe.label);
    failB.btn.dataset.state = hb.failsafe.state;
    failsafeOn = hb.failsafe.on;
    safetyNote.textContent = hb.failsafe.banner || (hb.abort.disabled ? "ABORT / FAILSAFE unavailable: " + hb.abort.title.replace(/^ABORT unavailable: /, "") : "");
    safetyNote.dataset.tone = hb.failsafe.banner ? "bad" : hb.abort.disabled ? "warn" : "";
    const ub = badgeState(ctx.update && ctx.update());
    const cc = opts.callouts?.()?.log.chip() || null;
    calChip.hidden = !cc;
    if (cc) {
      calChip.textContent = cc.text;
      calChip.title = `reported by the vehicle (gyro ${cc.detail.gyro ?? "?"}, accel ${cc.detail.accel ?? "?"}, mag ${cc.detail.mag ?? "?"}); click to open the Console`;
    }
    upd.hidden = !ub.show;
    upd.textContent = ub.text;
    upd.dataset.tone = ub.tone || "";
    // recorder
    const st = ctx.status();
    const d = describeRecorder(st && st.recorder, st && st.clock);
    rec.dataset.active = String(d.active);
    rec.dataset.available = String(d.available);
    recState.dataset.active = String(d.active);
    recState.querySelector(".rec-t").textContent = !d.available ? "REC n/a" : d.active ? `REC ${d.duration}` : "not recording";
    recName.textContent = d.active ? d.name : "";
    recName.title = d.name || "";
    recSize.textContent = d.active ? d.size : "";
    recFree.textContent = d.free || "";
    recFree.dataset.tone = d.freeTone || "";
    recErr.textContent = d.error ? `recorder error: ${d.error}` : d.dropped ? `dropped ${d.dropped}` : "";
    start.hidden = !d.available || d.active;
    stop.hidden = !d.active;
    start.disabled = !open || !d.available;
    stop.disabled = !open;
    const co = opts.callouts && opts.callouts();
    if (co) {
      const as = co.audioState();
      const cfg = co.settings();
      const muted = !cfg.master || cfg.volume <= 0;
      spk.dataset.muted = String(muted);
      spk.dataset.audio = as;
      spk.dataset.tone = muted ? "" : as === "on" ? "ok" : "warn";
      spk.textContent = muted ? "🔇" : as === "on" ? "🔊" : "🔈";
      spk.title = muted ? "callouts muted (settings: Callouts tab)" : as === "on" ? `callouts audio on, volume ${Math.round(cfg.volume * 100)} %` : "callouts audio is not enabled: text notifications only (click to open settings)";
    }
    const cs = ctx.connState();
    conn.dataset.tone = cs === "open" ? "ok" : "bad";
    conn.querySelector(".conn-t").textContent = cs === "open" ? "core" : "core offline";
    conn.title = cs === "open" ? "connected to the core" : "disconnected, retrying";
  }
  return { el, render, setLayoutView };
}
