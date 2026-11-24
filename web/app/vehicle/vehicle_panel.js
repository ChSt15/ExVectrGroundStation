// 'Vehicle' panel (docs/requests.md): the vehicle settings that are requests with a result of their own. Today the stop
// altitude (SET_STOP_ALTITUDE) and the mission parameters (SET_MISSION_PARAM). SAFETY-RELEVANT UI: the core enforces the whitelist, control hosts, rate limit and
// confirmation; the state shown comes from the vehicle's REQUEST_ACK and the echoed MissionState only.
import { h } from "../ui.js";
import { reportedSimulation, sendState } from "../requests/requests_logic.js";
import { NOTE, ROWS, TARGET, apogeeWarning, changedRows, describeRow, findById, parseValue, rowAction, runSequence, summarize, validationText, vehicleText, vehicleValue } from "./missionparams_logic.js";

export function createVehiclePanel(el, ctx) {
  let reqs = null; // last `requests` push
  const msg = h("div", { class: "rq-msg", role: "status" });
  const sim = h("div", { class: "tc-sim", "data-testid": "tc-sim" });
  el.append(
    h("div", { class: "tc-warn", "data-testid": "tc-radio-note", text: "Safety commands are given with the Pocket switches, or the virtual switches when the Pocket is not connected (Virtual radio panel): SA go/abort, SB profile, SC real/simulation, SD motors armed/disarmed, SE short press = reset system state, SE hold = zero estimators. The ground station's command engine turns them into the command state it sends continuously." }),
    sim, msg,
  );

  // One table: the six SET_MISSION_PARAM parameters and the stop altitude (SET_STOP_ALTITUDE) as the 7th row. ONE Send
  // sends every changed field, one request at a time, each waiting for its ACK (docs/requests.md). State shown comes
  // from the vehicle's REQUEST_ACK and its MISSION_PARAMS / MissionState readback only.
  const nowS = () => performance.now() / 1000;
  const inputs = {};
  const dirty = {};
  const cells = {};
  const results = {}; // key -> {text, tone, reqId, final, acked, ackedAt, value}
  let running = false;
  let epoch = 0; // bumped by a view change: stops a running sequence
  const summary = h("div", { class: "rq-msg", role: "status", "data-testid": "mp-summary" });
  const sendBtn = h("button", { class: "rq-btn", "data-testid": "mp-send" }, "Send");
  const resetLink = h("a", { href: "#", class: "hint", "data-testid": "mp-reset", text: "Reset to defaults" });
  const apogeeWarn = h("div", { class: "tc-warn", "data-testid": "mp-apogee-warning" });
  const host = h("section", { class: "rq-group", "data-testid": "mission-params" }, h("h4", { text: "Mission parameters" }),
    h("div", { class: "hint", text: NOTE }));
  let group = null;
  for (const row of ROWS) {
    if (row.group !== group) {
      group = row.group;
      host.append(h("h5", { text: group }));
    }
    const inp = h("input", { type: "number", class: "tc-param tc-float", value: String(row.def), min: String(row.min), step: String(row.step), "data-testid": `mp-input-${row.key}`, "aria-label": `${row.label} in ${row.unit}` });
    if (Number.isFinite(row.max)) inp.max = String(row.max);
    inputs[row.key] = inp;
    cells[row.key] = { veh: h("span", { class: "mono", "data-testid": `mp-vehicle-${row.key}` }), state: h("span", { class: "rq-state", "data-testid": `mp-state-${row.key}` }), bad: h("span", { class: "hint", "data-testid": `mp-invalid-${row.key}` }) };
    inp.addEventListener("input", () => {
      dirty[row.key] = true;
      delete results[row.key];
      render();
    });
    host.append(h("div", { class: "rq-row tc-row" }, h("span", { text: `${row.label} (${row.unit})` }), inp, cells[row.key].veh, cells[row.key].state, cells[row.key].bad));
    if (row.key === "apogee_altitude") host.append(apogeeWarn);
  }
  resetLink.addEventListener("click", (ev) => {
    ev.preventDefault();
    if (running) return;
    for (const row of ROWS) {
      inputs[row.key].value = String(row.def);
      dirty[row.key] = true;
      delete results[row.key];
    }
    render();
  });
  sendBtn.addEventListener("click", async () => {
    if (sendBtn.disabled || running) return;
    const { changed } = changedRows(texts(), vehicleValues(), dirty);
    if (!changed.length) return;
    running = true;
    const mine = epoch;
    summary.textContent = "";
    msg.textContent = "";
    for (const c of changed) delete results[c.row.key];
    render();
    const outcomes = await runSequence(changed, {
      send: (row, value) => ctx.api.request("request.send", { action: rowAction(row), target: TARGET, confirm: rowAction(row), ...(row.id === null ? {} : { arg_i: row.id }), arg_f: value }, 5000),
      poll: (id) => findById(reqs, id),
      sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
      now: () => performance.now(),
      cancelled: () => epoch !== mine,
      onUpdate: (key, r) => {
        const item = changed.find((c) => c.row.key === key);
        results[key] = { ...(results[key] || {}), ...r, value: item.value, ackedAt: r.acked ? nowS() : null };
        render();
      },
    });
    running = false;
    if (epoch === mine && outcomes.length) summary.textContent = summarize(outcomes);
    render();
  });
  const texts = () => Object.fromEntries(ROWS.map((r) => [r.key, inputs[r.key].value]));
  const vehicleValues = () => {
    const p = ctx.store.get(ctx.view(), "MissionParams");
    const m = ctx.store.get(ctx.view(), "MissionState");
    return Object.fromEntries(ROWS.map((r) => [r.key, vehicleValue(r, p && p.f, m && m.f)]));
  };
  el.append(host, h("div", { class: "rq-row" }, sendBtn, resetLink), summary);

  async function load() {
    try {
      reqs = await ctx.api.request("request.status", {}, 5000);
    } catch (e) {
      msg.textContent = `request.status: ${e.message}`;
    }
    render();
  }

  function render() {
    const canControl = !!(ctx.api.welcome && ctx.api.welcome.can_control);
    if (ctx.view() !== "live") {
      epoch++;
      running = false;
    }
    const veh = vehicleValues();
    const route = ctx.status()?.request_routes?.[String(TARGET)] || null;
    for (const row of ROWS) {
      const inp = inputs[row.key];
      const c = cells[row.key];
      const pv = parseValue(inp.value, row);
      if (dirty[row.key] && pv !== null && veh[row.key] !== null && Math.abs(veh[row.key] - pv) <= 0.0005) dirty[row.key] = false; // readback arrived: follow the vehicle again
      // an untouched field follows the vehicle's value (the default while unknown)
      if (!dirty[row.key] && document.activeElement !== inp) {
        const want = veh[row.key] !== null ? String(+veh[row.key].toFixed(3)) : String(row.def);
        if (inp.value !== want) inp.value = want;
      }
      c.veh.textContent = vehicleText(veh[row.key]);
      const bad = parseValue(inp.value, row) === null;
      c.bad.textContent = bad ? validationText(inp.value, row) : "";
      inp.setCustomValidity(bad ? validationText(inp.value, row) : "");
      inp.disabled = running;
      const r = results[row.key];
      if (!r) {
        c.state.textContent = "";
        c.state.dataset.tone = "info";
        continue;
      }
      let d = r;
      if (row.action === "SET_STOP_ALTITUDE" && r.final && r.acked) {
        d = describeRow(row, findById(reqs, r.reqId) || { state: "acked", status_name: "DONE" }, r.value, veh[row.key], r.ackedAt === null ? null : nowS() - r.ackedAt);
      }
      c.state.textContent = d.text;
      c.state.dataset.tone = d.tone;
    }
    apogeeWarn.textContent = apogeeWarning(inputs.apogee_altitude.value);
    const { changed, invalid } = changedRows(texts(), veh, dirty);
    const s = sendState({ apiState: ctx.api.state, view: ctx.view(), canControl, inflight: running, route, argOk: invalid.length === 0 });
    sendBtn.disabled = !s.enabled || changed.length === 0;
    sendBtn.dataset.why = s.reason || (changed.length === 0 ? "nothing changed" : "");
    sendBtn.textContent = changed.length ? `Send (${changed.length})` : "Send";
    const rep = reportedSimulation(ctx.store.get(ctx.view(), "VehicleStatus"));
    sim.textContent = rep.text;
    sim.dataset.tone = rep.tone;
  }

  return {
    onReady: load,
    onRequests(m) {
      reqs = m;
      render();
    },
    onStatus: render,
    onTick: render,
    onViewChange: render,
    destroy() {},
    debug: () => ({ reqs }),
  };
}
