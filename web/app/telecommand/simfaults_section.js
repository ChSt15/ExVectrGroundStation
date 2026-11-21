// 'Servo faults' section of the Simulation testing page (docs/telecommands.md). TEMPORARY, SAFETY-RELEVANT UI: one
// telecommand frame per button, after a 1 s hold, once, no retry, no ACK. Rules in simtests_logic.js.
import { h } from "../ui.js";
import { holdButton } from "./hold_button.js";
import { registerSimSection } from "./simtests_registry.js";
import {
  DEFAULT_SLOW_FACTOR, DEFAULT_STUCK_DEG, FAULT_TYPES, FLAPS, FLAP_NAMES, LAST_FLIGHT_PRESET, clearCommand,
  describeFaultSent, simTestState, slowCommand, stuckCommand,
} from "./simtests_logic.js";

export function createSimFaultsSection(el, ctx) {
  let cmds = [];
  let listError = "whitelist not loaded";
  let push = null;
  let busy = false;
  let flap = "TL";
  const msg = h("div", { class: "rq-msg", role: "status" });
  const sentEl = h("div", { class: "sw-sent", "data-testid": "sf-sent" });
  const rad = h("span", { class: "rq-target", "data-testid": "sf-rad" });
  const flapBtns = FLAPS.map((f) => h("button", {
    type: "button", class: "sf-flap", "data-flap": f, "aria-pressed": "false", title: `flap ${FLAP_NAMES[f]}`, text: f,
    onclick: () => { flap = f; render(); },
  }));
  const grid = h("div", { class: "sf-grid", role: "group", "aria-label": "flap" },
    flapBtns[0], flapBtns[1], h("div", { class: "sf-body", text: "nose up" }), flapBtns[2], flapBtns[3]);
  const modeName = `sfm${Math.random().toString(36).slice(2, 7)}`;
  const mkRadio = (value, label, checked) => h("label", { class: "sf-radio" },
    h("input", { type: "radio", name: modeName, value, checked, "data-testid": `sf-mode-${value}` }), label);
  const modeNow = mkRadio("now", "stuck now", false);
  const modeArm = mkRadio("arm", "stuck when the descent begins", true);
  const angle = h("input", { type: "number", class: "tc-param sf-deg", step: "any", min: "0", max: "90", value: String(DEFAULT_STUCK_DEG), "aria-label": "stuck angle in degrees" });
  const atCur = h("input", { type: "checkbox", class: "sf-cur", "aria-label": "stick at the current angle" });
  const factor = h("input", { type: "number", class: "tc-param sf-factor", step: "any", value: String(DEFAULT_SLOW_FACTOR), "aria-label": "servo rate factor" });
  const armed = () => modeArm.querySelector("input").checked;
  const form = () => ({ flap, armed: armed(), atCurrent: atCur.checked, deg: angle.value });

  const applyStuck = holdButton("Apply stuck fault (hold 1 s)", "hold 1 s to send one frame", () => send(stuckCommand(cmds, form()), "Stuck fault"));
  const applySlow = holdButton("Apply slow servo (hold 1 s)", "hold 1 s to send one frame", () => send(slowCommand(cmds, { flap, factor: factor.value }), "Slow servo"));
  const clear = holdButton("Clear all faults (hold 1 s)", "hold 1 s to send one frame", () => send(clearCommand(cmds), "Clear"));
  const preset = h("button", {
    type: "button", class: "rq-btn sf-preset", "data-testid": "sf-preset",
    title: "fills the fields only; sending still needs Apply and the 1 s hold",
    text: "Replay last flight failure: top-left flap stuck at 42 deg when the descent begins",
    onclick: () => {
      const p = LAST_FLIGHT_PRESET;
      flap = p.flap; angle.value = String(p.deg); atCur.checked = p.atCurrent;
      (p.armed ? modeArm : modeNow).querySelector("input").checked = true;
      render();
    },
  });
  const body = h("div", { class: "rq-body" });
  el.append(
    h("div", { class: "tc-warn", text: "SIMULATION ONLY. One frame per button after a 1 s hold; no ACK, no retry. The vehicle refuses faults outside simulation mode and writes a log line (see the Console) when a fault is set, armed or cleared. A fault armed for the descent takes effect when the descent/bellyflop mission begins." }),
    body, msg, sentEl,
  );

  async function send(c, what) {
    if (!c || busy) return;
    busy = true;
    msg.textContent = "";
    render();
    try {
      await ctx.api.request("telecommand.send", { name: c.name, confirm: c.name, param_float: c.value }, 5000);
    } catch (e) {
      msg.textContent = `${what} refused: ${e.message}`;
    }
    busy = false;
    render();
  }

  async function load() {
    try {
      const all = (await ctx.api.request("telecommand.list", {}, 5000)).commands;
      cmds = all.filter((c) => FAULT_TYPES.includes(c.type));
      listError = cmds.length ? "" : "the telecommand whitelist has no simulation fault entries (config [telecommands], group 'Simulation tests')";
    } catch (e) {
      listError = `telecommand.list: ${e.message}`;
    }
    build();
    render();
  }

  function build() {
    body.replaceChildren();
    if (listError) { body.append(h("div", { class: "rq-err", text: listError })); return; }
    for (const i of [angle, atCur, factor]) i.oninput = render;
    for (const r of [modeNow, modeArm]) r.querySelector("input").onchange = render;
    body.append(
      h("div", { class: "sw-row" }, h("span", { text: "Flap:" }), grid),
      h("div", { class: "sw-row" }, modeNow, modeArm),
      h("div", { class: "sw-row" }, h("label", { text: "Stuck angle (degrees, 0..90) " }, angle), h("label", { class: "sf-radio" }, atCur, "at current angle"), h("span", { text: "vehicle value:" }), rad),
      h("div", { class: "sw-row" }, applyStuck.btn, preset),
      h("div", { class: "sw-row" }, h("label", { text: "Servo rate factor (0.05..1) " }, factor), applySlow.btn),
      h("div", { class: "sw-row" }, clear.btn),
    );
  }

  function render() {
    const canControl = !!(ctx.api.welcome && ctx.api.welcome.can_control);
    const st = ctx.status();
    const route = st ? st.telecommand_route || null : null;
    const entry = ctx.store.get(ctx.view(), "VehicleStatus");
    const base = { apiState: ctx.api.state, view: ctx.view(), canControl, route, simulation: entry && entry.f ? entry.f.simulation : null, busy };
    const stk = stuckCommand(cmds, form());
    const slw = slowCommand(cmds, { flap, factor: factor.value });
    const on = simTestState({ ...base, inputsOk: true }).enabled;
    for (const f of flapBtns) f.setAttribute("aria-pressed", String(f.dataset.flap === flap));
    for (const f of flapBtns) f.disabled = !on;
    for (const i of [angle, atCur, factor, modeNow.querySelector("input"), modeArm.querySelector("input")]) i.disabled = !on;
    angle.disabled = !on || atCur.checked;
    preset.disabled = !on;
    const set = (b, c) => {
      const s = simTestState({ ...base, inputsOk: !!c });
      b.btn.disabled = !s.enabled; b.btn.dataset.why = s.reason;
      if (!s.enabled) b.stop();
    };
    set(applyStuck, stk); set(applySlow, slw); set(clear, clearCommand(cmds));
    rad.textContent = atCur.checked ? "-1.0 (current angle)" : stk ? `${stk.value.toFixed(4)} rad` : "-";
    const d = describeFaultSent(push, ctx.store.now("live"));
    sentEl.replaceChildren(h("div", { class: "rq-state", "data-tone": d.tone, text: d.text }));
  }

  return {
    onReady: load,
    onTelecommands(m) { push = m; render(); },
    onStatus: render,
    onTick: render,
    onViewChange: render,
    destroy() { for (const b of [applyStuck, applySlow, clear]) b.stop(); },
    debug: () => ({ cmds: cmds.map((c) => c.name), push, listError }),
  };
}

registerSimSection({ id: "faults", title: "Servo faults", order: 20, create: createSimFaultsSection });
