// 'Sim wind' panel (docs/telecommands.md). TEMPORARY, SAFETY-RELEVANT UI: sends the whitelisted simulation wind
// telecommands through `telecommand.send`, one frame each, 150 ms apart, after one 1 s hold per group. Rules in
// simwind_logic.js; the core enforces whitelist, control hosts, group rate limit and confirmation again.
import { h } from "../ui.js";
import {
  DIRECTION_MODES, HoldTimer, TELECOMMAND_HOLD_MS, WIND_NAMES, applySequence, clearSequence, degToVehicleRad,
  describeSent, lastSent, parseNumber, reportedSimulation, runSequence, windState,
} from "./simwind_logic.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function createSimWindPanel(el, ctx) {
  let cmds = {}; // name -> whitelist row
  let listError = "whitelist not loaded";
  let push = null;
  let busy = false;
  const msg = h("div", { class: "rq-msg", role: "status" });
  const sim = h("div", { class: "tc-sim", "data-testid": "sw-sim" });
  const body = h("div", { class: "rq-body" });
  const conv = h("span", { class: "rq-target", "data-testid": "sw-rad" });
  const stateEl = h("div", { class: "sw-sent", "data-testid": "sw-sent" });
  const mkInput = (cls, step) => h("input", { type: "number", class: `tc-param ${cls}`, step, "aria-label": cls });
  const speed = mkInput("sw-speed", "any");
  const dir = mkInput("sw-dir", "any");
  const gust = mkInput("sw-gust", "any");
  const period = mkInput("sw-period", "any");
  const seed = mkInput("sw-seed", "1");
  const mode = h("select", { class: "sw-mode", "aria-label": "direction convention" },
    h("option", { value: "from", text: DIRECTION_MODES.from }), h("option", { value: "toward", text: DIRECTION_MODES.toward }));
  const field = (label, input, extra) => h("div", { class: "rq-row sw-row" }, h("label", { text: label }, input), extra || "");
  el.append(
    h("div", { class: "tc-warn", text: "TEMPORARY, SIMULATION ONLY. Apply sends 5 telecommands (one frame each, 150 ms apart) after a 1 s hold. No ACK, no retry; the vehicle refuses them outside simulation mode and clamps out-of-range values. Wind and gusts reset when simulation mode switches." }),
    sim, body, msg, stateEl,
  );

  function holdButton(label, title, onDone) {
    const bar = h("span", { class: "bar" });
    const btn = h("button", { class: "rq-btn", title }, h("span", { class: "rq-label", text: label }), bar);
    let anim = null;
    const stopAnim = () => { if (anim !== null) clearInterval(anim); anim = null; bar.style.width = "0"; };
    const timer = new HoldTimer(() => { stopAnim(); onDone(); }, undefined, TELECOMMAND_HOLD_MS);
    const stop = () => { timer.cancel(); stopAnim(); };
    const start = () => {
      if (btn.disabled || timer.holding) return;
      timer.start();
      anim = setInterval(() => { bar.style.width = `${Math.round(timer.progress() * 100)}%`; }, 40);
    };
    btn.addEventListener("pointerdown", (e) => {
      try { btn.setPointerCapture(e.pointerId); } catch (err) { /* synthetic pointer ids have no capture */ }
      start();
    });
    for (const ev of ["pointerup", "pointercancel", "lostpointercapture", "pointerleave", "blur"]) btn.addEventListener(ev, stop);
    btn.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { start(); e.preventDefault(); } });
    btn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") stop(); });
    return { btn, stop };
  }

  const applyB = holdButton("Apply wind (hold 1 s)", "hold 1 s to send 5 frames, once each", () => run(applySequence(values()), "Apply"));
  const clearB = holdButton("Clear wind (hold 1 s)", "hold 1 s to send wind speed 0 and gust 0", () => run(clearSequence(), "Clear"));

  /** Parsed field values (null when invalid) with the limits of the core's whitelist. */
  function values() {
    const lim = (n) => [cmds[n]?.param_float_min ?? 0, cmds[n]?.param_float_max ?? 0];
    const deg = parseNumber(dir.value, -720, 720);
    return {
      speed: parseNumber(speed.value, ...lim("SimWindSpeed")),
      directionRad: deg === null ? null : degToVehicleRad(deg, mode.value),
      gust: parseNumber(gust.value, ...lim("SimGustStrength")),
      period: parseNumber(period.value, ...lim("SimGustPeriod")),
      seed: (() => { const v = parseNumber(seed.value, ...lim("SimGustSeed")); return v !== null && Number.isInteger(v) ? v : null; })(),
    };
  }

  async function run(steps, what) {
    if (!steps || busy) return;
    busy = true;
    msg.textContent = "";
    render();
    const res = await runSequence(steps, (s) => ctx.api.request("telecommand.send", { name: s.name, confirm: s.name, param_float: s.value }, 5000), sleep);
    busy = false;
    if (res.error) msg.textContent = `${what}: ${res.error.step.name} refused after ${res.sent} sent: ${res.error.message}`;
    render();
  }

  async function load() {
    try {
      const all = (await ctx.api.request("telecommand.list", {}, 5000)).commands;
      cmds = Object.fromEntries(all.filter((c) => c.rate_group === "simwind").map((c) => [c.name, c]));
      listError = WIND_NAMES.every((n) => cmds[n]) ? "" : "the telecommand whitelist has no simulation wind entries (config [telecommands])";
    } catch (e) {
      listError = `telecommand.list: ${e.message}`;
    }
    build();
    render();
  }

  function build() {
    body.replaceChildren();
    if (listError) {
      body.append(h("div", { class: "rq-err", text: listError }));
      return;
    }
    const d = (n) => String(cmds[n].param_float);
    if (!speed.value) { speed.value = d("SimWindSpeed"); dir.value = "0"; gust.value = d("SimGustStrength"); period.value = d("SimGustPeriod"); seed.value = d("SimGustSeed"); }
    for (const i of [speed, dir, gust, period, seed, mode]) i.oninput = render;
    mode.onchange = render;
    const rng = (n) => `${cmds[n].param_float_min}..${cmds[n].param_float_max}`;
    body.append(
      field(`Wind speed (m/s, ${rng("SimWindSpeed")})`, speed),
      field("Direction (degrees)", dir, mode),
      h("div", { class: "rq-row sw-row" }, h("span", { text: "Vehicle value (air moves toward, CCW from N toward W):" }), conv),
      field(`Gust strength (m/s, ${rng("SimGustStrength")})`, gust),
      field(`Gust period (s, ${rng("SimGustPeriod")})`, period),
      field(`Gust seed (integer, ${rng("SimGustSeed")})`, seed),
      h("div", { class: "rq-row sw-row" }, applyB.btn, clearB.btn),
    );
  }

  function render() {
    const canControl = !!(ctx.api.welcome && ctx.api.welcome.can_control);
    const st = ctx.status();
    const route = st ? st.telecommand_route || null : null;
    const entry = ctx.store.get(ctx.view(), "VehicleStatus");
    const v = values();
    const inputsOk = applySequence(v) !== null;
    conv.textContent = v.directionRad === null ? "-" : `${v.directionRad.toFixed(4)} rad`;
    const base = { apiState: ctx.api.state, view: ctx.view(), canControl, route, simulation: entry && entry.f ? entry.f.simulation : null, busy };
    const sa = windState({ ...base, inputsOk });
    const sc = windState({ ...base, inputsOk: true });
    applyB.btn.disabled = !sa.enabled; applyB.btn.dataset.why = sa.reason;
    clearB.btn.disabled = !sc.enabled; clearB.btn.dataset.why = sc.reason;
    if (!sa.enabled) applyB.stop();
    if (!sc.enabled) clearB.stop();
    const rep = reportedSimulation(entry);
    sim.textContent = rep.text;
    sim.dataset.tone = rep.tone;
    const now = ctx.store.now("live");
    stateEl.replaceChildren(...WIND_NAMES.map((n) => {
      const d = describeSent(lastSent(push, n), now);
      return h("div", { class: "rq-state", "data-tone": d.tone, "data-name": n, text: `${cmds[n]?.label || n}: ${d.text || "not sent"}` });
    }));
  }

  return {
    onReady: load,
    onTelecommands(m) { push = m; render(); },
    onStatus: render,
    onTick: render,
    onViewChange: render,
    destroy() { applyB.stop(); clearB.stop(); },
    debug: () => ({ cmds: Object.keys(cmds), push, listError }),
  };
}
