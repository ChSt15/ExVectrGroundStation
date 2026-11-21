// 'Servo faults' section of the Simulation testing page (docs/telecommands.md). TEMPORARY, SAFETY-RELEVANT UI: one
// telecommand frame per button, after a 1 s hold, once, no retry, no ACK. Rules in simtests_logic.js.
import { h } from "../ui.js";
import { holdButton } from "./hold_button.js";
import { registerSimSection } from "./simtests_registry.js";
import {
  DEFAULT_SLOW_FACTOR, DEFAULT_STUCK_DEG, FAULT_TYPES, FLAPS, FLAP_NAMES, clearCommand,
  describeFaultSent, forcedFlaps, simTestState, slowCommand, stuckCommand,
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
  const angle = h("input", { type: "number", class: "tc-param sf-deg", step: "any", min: "0", max: "90", value: String(DEFAULT_STUCK_DEG), "aria-label": "forced angle in degrees" });
  const atCur = h("input", { type: "checkbox", class: "sf-cur", "aria-label": "hold the current angle" });
  const factor = h("input", { type: "number", class: "tc-param sf-factor", step: "any", value: String(DEFAULT_SLOW_FACTOR), "aria-label": "servo rate factor" });
  const track = {};
  const forcedEl = h("div", { class: "sf-forced", "data-testid": "sf-forced" });
  const form = () => ({ flap, atCurrent: atCur.checked, deg: angle.value });

  const applyStuck = holdButton("Force flap (hold 1 s)", "hold 1 s to send one frame", () => send(stuckCommand(cmds, form()), "Force flap"));
  const applySlow = holdButton("Apply slow servo (hold 1 s)", "hold 1 s to send one frame", () => send(slowCommand(cmds, { flap, factor: factor.value }), "Slow servo"));
  const clear = holdButton("Release all faults (hold 1 s)", "hold 1 s to send one frame", () => send(clearCommand(cmds), "Release"));
  const body = h("div", { class: "rq-body" });
  el.append(
    h("div", { class: "tc-warn", text: "SIMULATION ONLY. One frame per button after a 1 s hold; no ACK, no retry. The vehicle refuses faults outside simulation mode and writes a log line (see the Console) when a fault is set or released. Force flap sets the chosen flap's setting to the angle at once." }),
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
    body.append(
      h("div", { class: "sw-row" }, h("span", { text: "Flap:" }), grid),
      h("div", { class: "sw-row" }, h("label", { text: "Forced angle (degrees, 0..90) " }, angle), h("label", { class: "sf-radio" }, atCur, "hold the current angle"), h("span", { text: "vehicle value:" }), rad),
      h("div", { class: "sw-row" }, applyStuck.btn),
      h("div", { class: "sw-row" }, h("label", { text: "Servo rate factor (0.05..1) " }, factor), applySlow.btn),
      h("div", { class: "sw-row" }, clear.btn),
      forcedEl,
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
    for (const i of [angle, atCur, factor]) i.disabled = !on;
    angle.disabled = !on || atCur.checked;
    const set = (b, c) => {
      const s = simTestState({ ...base, inputsOk: !!c });
      b.btn.disabled = !s.enabled; b.btn.dataset.why = s.reason;
      if (!s.enabled) b.stop();
    };
    set(applyStuck, stk); set(applySlow, slw); set(clear, clearCommand(cmds));
    rad.textContent = atCur.checked ? "-1.0 (hold current angle)" : stk ? `${stk.value.toFixed(4)} rad` : "-";
    const fe = ctx.store.get(ctx.view(), "FlapState");
    const fh = forcedFlaps(track, fe && fe.f ? fe.f : null, ctx.store.now(ctx.view()));
    forcedEl.replaceChildren(h("div", { class: "rq-state", "data-tone": fh.forced.some(Boolean) ? "warn" : "dim", text: "Flaps reported by the vehicle: " + FLAPS.map((n, i) => `${n} ${fh.angle[i] === null ? "-" : fh.angle[i].toFixed(1) + " deg"}${fh.forced[i] ? " (forced?)" : ""}`).join(", ") }));
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
