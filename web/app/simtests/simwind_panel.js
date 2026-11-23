// Wind section of the Simulation testing page (docs/requests.md). SAFETY-RELEVANT UI: sends the whitelisted SIM_WIND_*
// and SIM_GUST_* requests through `request.send`, one after the other, each after the vehicle's ACK, after one 1 s
// hold per group. Rules in simwind_logic.js; the core enforces whitelist, control hosts, rate limit and confirmation.
import { h } from "../ui.js";
import { holdButton } from "./hold_button.js";
import { describeRequest, latestOf } from "../requests/requests_logic.js";
import {
  DIRECTION_MODES, WIND_ACTIONS, applySequence, clearSequence, degToVehicleRad,
  parseNumber, reportedSimulation, runRequestSequence, windState,
} from "./simwind_logic.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function createSimWindPanel(el, ctx) {
  let cmds = {}; // action -> whitelist row
  let listError = "whitelist not loaded";
  let reqs = null; // last `requests` push
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
    h("div", { class: "tc-warn", text: "SIMULATION ONLY. Apply sends 5 requests after a 1 s hold, one after the other, each after the vehicle's acknowledgement; it stops at the first refusal. The vehicle refuses them outside simulation mode. Wind and gusts reset when simulation mode switches." }),
    sim, body, msg, stateEl,
  );

  const applyB = holdButton("Apply wind (hold 1 s)", "hold 1 s to send 5 requests, one after the other", () => run(applySequence(values()), "Apply"));
  const clearB = holdButton("Clear wind (hold 1 s)", "hold 1 s to send wind speed 0 and gust 0 as requests", () => run(clearSequence(), "Clear"));

  /** Parsed field values (null when invalid) with the limits of the core's whitelist. */
  function values() {
    const lim = (n) => [cmds[n]?.arg_f?.min ?? 0, cmds[n]?.arg_f?.max ?? 0];
    const deg = parseNumber(dir.value, -720, 720);
    return {
      speed: parseNumber(speed.value, ...lim("SIM_WIND_SPEED")),
      directionRad: deg === null ? null : degToVehicleRad(deg, mode.value),
      gust: parseNumber(gust.value, ...lim("SIM_GUST_STRENGTH")),
      period: parseNumber(period.value, ...lim("SIM_GUST_PERIOD")),
      seed: (() => { const r = cmds.SIM_GUST_SEED?.arg_i_range; const v = r ? parseNumber(seed.value, r.min, r.max) : null; return v !== null && Number.isInteger(v) ? v : null; })(),
    };
  }

  async function run(steps, what) {
    if (!steps || busy) return;
    busy = true;
    msg.textContent = "";
    render();
    const res = await runRequestSequence(
      steps.map((x) => ({ ...x, label: cmds[x.action]?.label })),
      {
        send: (x) => ctx.api.request("request.send", { action: x.action, target: 1, confirm: x.action, arg_i: x.arg_i ?? 0, ...(x.arg_f === undefined ? {} : { arg_f: x.arg_f }) }, 5000),
        status: () => ctx.api.request("request.status", {}, 5000),
        sleep,
      },
    );
    busy = false;
    msg.textContent = res.error ? `${what} stopped after ${res.done} done: ${res.error.text}` : "";
    render();
  }

  async function load() {
    try {
      const all = (await ctx.api.request("request.list", {}, 5000)).actions;
      cmds = Object.fromEntries(all.filter((c) => WIND_ACTIONS.includes(c.action)).map((c) => [c.action, c]));
      listError = WIND_ACTIONS.every((n) => cmds[n]) ? "" : "the request whitelist has no simulation wind entries (config [requests], group 'Simulation')";
      reqs = await ctx.api.request("request.status", {}, 5000);
    } catch (e) {
      listError = `request.list: ${e.message}`;
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
    const d = (n) => String(cmds[n].arg_f.default);
    if (!speed.value) { speed.value = d("SIM_WIND_SPEED"); dir.value = "0"; gust.value = d("SIM_GUST_STRENGTH"); period.value = d("SIM_GUST_PERIOD"); seed.value = String(cmds.SIM_GUST_SEED.arg_i); }
    for (const i of [speed, dir, gust, period, seed, mode]) i.oninput = render;
    mode.onchange = render;
    const rng = (n) => `${cmds[n].arg_f.min}..${cmds[n].arg_f.max}`;
    const seedRng = `${cmds.SIM_GUST_SEED.arg_i_range.min}..${cmds.SIM_GUST_SEED.arg_i_range.max}`;
    body.append(
      field(`Wind speed (m/s, ${rng("SIM_WIND_SPEED")})`, speed),
      field("Direction (degrees)", dir, mode),
      h("div", { class: "rq-row sw-row" }, h("span", { text: "Vehicle value (air moves toward, CCW from N toward W):" }), conv),
      field(`Gust strength (m/s, ${rng("SIM_GUST_STRENGTH")})`, gust),
      field(`Gust period (s, ${rng("SIM_GUST_PERIOD")})`, period),
      field(`Gust seed (integer, ${seedRng})`, seed),
      h("div", { class: "rq-row sw-row" }, applyB.btn, clearB.btn),
    );
  }

  function render() {
    const canControl = !!(ctx.api.welcome && ctx.api.welcome.can_control);
    const st = ctx.status();
    const route = st?.request_routes?.["1"] || null;
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
    stateEl.replaceChildren(...WIND_ACTIONS.map((n) => {
      const d = describeRequest(latestOf(reqs, [n]));
      return h("div", { class: "rq-state", "data-tone": d.tone, "data-name": n, text: `${cmds[n]?.label || n}: ${d.text || "not sent"}` });
    }));
  }

  return {
    onReady: load,
    onRequests(m) { reqs = m; render(); },
    onStatus: render,
    onTick: render,
    onViewChange: render,
    destroy() { applyB.stop(); clearB.stop(); },
    debug: () => ({ cmds: Object.keys(cmds), reqs, listError }),
  };
}
