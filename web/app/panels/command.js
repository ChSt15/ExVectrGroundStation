// Operations dock tab "Command" (replaces "RC applied"). Two sources, labelled apart:
//  - what the ground station's command engine SENDS (the `rc` push, `command`: input, Pocket, pickup hints, session)
//  - what the VEHICLE applied (CMD_APPLIED, USB only): the CMD_STATE fields as decoded, safe-seen bits, ages, rejects.
// The top section has the command BUTTONS: they drive the same virtual switches as the Radio tab (rc.set under the
// virtual-panel lease), so the engine's pickup rule and "the Pocket wins" apply unchanged. The shown state is what the
// engine sends (the `rc` push), never the click. ABORT calls the core's `engine.abort` (any input).
import { registerPanel } from "./registry.js";
import { Bindings, h, section } from "../ui.js";
import {
  SWITCHES, SAFE_SEEN, REJECT_LABELS, ageText, engineRows, flagsText, safeSeenList, session32Text, switchText, switchTone,
} from "../command/command_logic.js";

import { holdControl } from "../command/hold_ctl.js";
import { ARM_HOLD_MS, SE_REPEAT_MS, buttonOps, panelState } from "../command/button_logic.js";

const T = "CmdApplied";

registerPanel({
  id: "command",
  title: "Command",
  consumes: [T],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const cmdSec = section(el, "Command (the same virtual switches as the Radio tab)");
    const inputBox = h("div", { class: "cmd-input", "data-testid": "cmd-input" });
    const stateBox = h("div", { class: "cmd-state", "data-testid": "cmd-state" });
    const btnBox = h("div", { class: "cmd-btns" });
    const whyBox = h("div", { class: "cmd-why", "data-testid": "cmd-why" });
    cmdSec.append(inputBox, stateBox, btnBox, whyBox);
    let msg = "";
    const stateOf = () => panelState({ open: ctx.api.state === "open", rc: ctx.rc?.(), canControl: ctx.api.welcome && ctx.api.welcome.can_control });
    async function run(name, extra) {
      const st = stateOf();
      try {
        for (const { op, ...params } of buttonOps(name, st)) await ctx.api.request(op, params, 3000);
        msg = "";
      } catch (e) {
        msg = `${name}: ${e.message}`;
      }
    }
    const mk = (key, label, name, extraAttrs = {}) => {
      const b = h("button", { class: "cmd-b", "data-btn": key, text: label, ...extraAttrs });
      b.addEventListener("click", () => run(name));
      btnBox.append(b);
      return b;
    };
    const bBegin = mk("begin", "Mission begin", "begin");
    const bAbort = mk("abort", "Abort", "abort");
    const armCtl = holdControl({ label: "Arm motors (hold 1 s)", ms: ARM_HOLD_MS, cls: "cmd-b", testid: "cmd-arm", title: "hold 1 s to arm", onDone: () => run("arm") });
    armCtl.btn.dataset.btn = "motors";
    const disarmB = mk("disarm", "Disarm motors", "disarm");
    const bReal = mk("real", "Mode: real", "real", { "aria-pressed": "false" });
    const bSim = mk("sim", "Mode: simulation", "sim", { "aria-pressed": "false" });
    bReal.classList.add("cmd-mode");
    bSim.classList.add("cmd-mode");
    const bReset = mk("reset", "Reset system state", "reset");
    btnBox.insertBefore(armCtl.btn, disarmB);
    // Zero estimators: a press holds SE (refreshed every 100 ms) for as long as the button is held; the vehicle zeroes at >= 1 s
    const bZero = h("button", { class: "cmd-b", "data-btn": "zero", text: "Zero estimators (hold >= 1 s)", title: "SE held while pressed: the vehicle zeroes after 1 s" });
    let zeroTimer = null;
    const zeroStop = () => {
      if (zeroTimer === null) return;
      clearInterval(zeroTimer);
      zeroTimer = null;
      ctx.api.request("rc.set", { values: { SE: 0 } }, 3000).catch(() => {});
    };
    bZero.addEventListener("pointerdown", async (e) => {
      try { bZero.setPointerCapture(e.pointerId); } catch (err) { /* synthetic pointer */ }
      if (bZero.disabled || zeroTimer !== null) return;
      const st = stateOf();
      zeroTimer = setInterval(() => ctx.api.request("rc.set", { values: { SE: 1 }, hold_s: 0.3 }, 3000).catch(() => {}), SE_REPEAT_MS);
      try {
        if (!st.holder) await ctx.api.request("rc.acquire", { kind: "connection" }, 3000);
        await ctx.api.request("rc.set", { values: { SE: 1 }, hold_s: 0.3 }, 3000);
      } catch (err) { msg = `zero: ${err.message}`; }
    });
    for (const ev of ["pointerup", "pointercancel", "lostpointercapture", "pointerleave", "blur"]) bZero.addEventListener(ev, zeroStop);
    btnBox.append(bZero);
    const named = { begin: bBegin, abort: bAbort, motors: armCtl.btn, real: bReal, sim: bSim, reset: bReset, zero: bZero };
    function renderButtons() {
      const st = stateOf();
      inputBox.textContent = st.note;
      inputBox.dataset.input = st.input;
      const arm = st.motorsAction === "arm";
      armCtl.btn.hidden = !arm;
      disarmB.hidden = arm;
      const why = [];
      for (const [k, el0] of Object.entries(named)) {
        const s = st.buttons[k];
        if (k === "motors") { armCtl.setDisabled(s.disabled); disarmB.disabled = s.disabled; } else el0.disabled = s.disabled;
        if (s.disabled && s.why && !st.pocket) why.push(s.why);
      }
      stateBox.replaceChildren(
        ...[["Go", st.state.go], ["Motors", st.state.motors], ["Mode", st.state.mode], ["Reset", st.state.reset]].map(([l, v]) => h("span", { "data-key": `cmd-out-${l.toLowerCase()}`, text: `${l}: ${v}` })),
      );
      bReal.setAttribute("aria-pressed", String(st.state.mode === "REAL"));
      bSim.setAttribute("aria-pressed", String(st.state.mode === "SIMULATION"));
      whyBox.textContent = msg || [...new Set(why)].join(" · ");
    }
    const sent = section(el, "Sent by the ground station (command engine)");
    const sentBox = h("div", { class: "cp-box", "data-testid": "command-engine" });
    sent.append(sentBox);
    const sw = section(el, "Applied by the vehicle (CMD_APPLIED, last accepted CMD_STATE)");
    for (const s of SWITCHES) {
      const val = b.cell(
        { type: T, kind: "text", derive: (e) => (e.f[s.id] === undefined ? undefined : `${e.f[s.id]}: ${switchText(s.id, e.f[s.id])}`) },
        { showSrc: false, tone: (d) => switchTone(s.id, Number((d.text || "").split(":")[0])) },
      );
      sw.append(h("div", { class: "row", "data-key": `cmd-${s.id}` }, h("span", { class: "lbl", text: `${s.label} (${s.meaning})` }), val));
    }
    b.row(sw, "Input flags", { type: T, kind: "text", derive: (e) => (e.f.flags === undefined ? undefined : flagsText(e.f.flags)) }, { key: "cmd-flags", showSrc: false });
    b.row(sw, "Latched session", { type: T, kind: "text", derive: (e) => (e.f.session === undefined ? undefined : session32Text(e.f.session)) }, { key: "cmd-session", showSrc: false });
    const ss = section(el, "Safe value seen");
    for (let i = 0; i < SAFE_SEEN.length; i++) {
      b.row(ss, SAFE_SEEN[i], { type: T, kind: "text", derive: (e) => (e.f.safe_seen === undefined ? undefined : safeSeenList(e.f.safe_seen)[i].seen ? "seen" : "not seen") }, { key: `cmd-seen-${i}`, showSrc: false });
    }
    const ag = section(el, "Age of the last accepted CMD_STATE");
    b.row(ag, "Either link", { type: T, kind: "text", derive: (e) => (e.f.age === undefined ? undefined : ageText(e.f.age)) }, { key: "cmd-age", showSrc: false });
    b.row(ag, "USB", { type: T, kind: "text", derive: (e) => (e.f.usb_age === undefined ? undefined : ageText(e.f.usb_age)) }, { key: "cmd-usb-age", showSrc: false });
    b.row(ag, "UART (radio)", { type: T, kind: "text", derive: (e) => (e.f.uart_age === undefined ? undefined : ageText(e.f.uart_age)) }, { key: "cmd-uart-age", showSrc: false });
    const rj = section(el, "Rejected CMD_STATE frames (wrapping counters)");
    REJECT_LABELS.forEach((label, i) => {
      b.row(rj, label, { type: T, kind: "num", digits: 0, derive: (e) => (Array.isArray(e.f.rejects) ? e.f.rejects[i] : undefined) }, { key: `cmd-rej-${i}`, showSrc: false });
    });
    function renderEngine() {
      const rc = ctx.rc?.();
      const rows = engineRows(rc && rc.command);
      sentBox.replaceChildren(
        ...(rows.length
          ? rows.map((r) => h("div", { class: "cp-row", "data-key": r.key, "data-tone": r.tone }, h("span", { class: "cp-l", text: r.label }), h("span", { class: "cp-v", text: r.text })))
          : [h("div", { class: "cp-row", "data-tone": "dim", text: "No command engine report (no core connection, or replay)." })]),
      );
    }
    renderEngine();
    renderButtons();
    return { onTick: () => { b.update(); renderEngine(); renderButtons(); }, onRc: renderButtons, destroy: () => zeroStop() };
  },
});
