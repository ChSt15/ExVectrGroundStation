// 'Vehicle' panel (docs/requests.md): the vehicle settings that are requests with a result of their own. Today the stop
// altitude (SET_STOP_ALTITUDE). SAFETY-RELEVANT UI: the core enforces the whitelist, control hosts, rate limit and
// confirmation; the state shown comes from the vehicle's REQUEST_ACK and the echoed MissionState only.
import { h } from "../ui.js";
import { reportedSimulation, sendState } from "../requests/requests_logic.js";
import { ACCEPT_NOTE, ACTION, DEFAULT_M, MIN_M, TARGET, describeSetting, echoText, findRequest, parseStopAltitude, sendErrorText } from "./stopalt_logic.js";

export function createVehiclePanel(el, ctx) {
  let reqs = null; // last `requests` push
  const msg = h("div", { class: "rq-msg", role: "status" });
  const sim = h("div", { class: "tc-sim", "data-testid": "tc-sim" });
  el.append(
    h("div", { class: "tc-warn", "data-testid": "tc-radio-note", text: "Safety commands are given with the Pocket switches, or the virtual switches when the Pocket is not connected (Virtual radio panel): SA go/abort, SB profile, SC real/simulation, SD motors armed/disarmed, SE short press = reset system state, SE hold = zero estimators. The ground station's command engine turns them into the command state it sends continuously." }),
    sim, msg,
  );

  // Stop altitude = the whitelisted REQUEST SET_STOP_ALTITUDE (docs/requests.md). The state comes from the vehicle's
  // REQUEST_ACK (the core retries with the same req_id); the echoed MissionState.stop_altitude is the cross-check.
  const saInput = h("input", { type: "number", class: "tc-param tc-float", value: String(DEFAULT_M), min: String(MIN_M), step: "0.5", "data-testid": "stopalt-input", "aria-label": "stop altitude in metres" });
  const saBtn = h("button", { class: "rq-btn", "data-testid": "stopalt-set" }, "Set");
  const saEcho = h("span", { class: "mono", "data-testid": "stopalt-echo" });
  const saState = h("span", { class: "rq-state", "data-testid": "stopalt-state" });
  const nowS = () => performance.now() / 1000;
  let sa = null; // { target, reqId (null until the send call is answered), ackedAt }
  saBtn.addEventListener("click", async () => {
    const v = parseStopAltitude(saInput.value);
    if (v === null || saBtn.disabled) return;
    const mine = { target: v, reqId: null, ackedAt: null };
    sa = mine;
    msg.textContent = "";
    render();
    try {
      const r = await ctx.api.request("request.send", { action: ACTION, target: TARGET, confirm: ACTION, arg_f: v }, 5000);
      mine.reqId = r.req_id;
    } catch (e) {
      if (sa === mine) sa = null;
      msg.textContent = sendErrorText(e);
    }
    render();
  });
  saInput.addEventListener("input", render);
  el.append(h("section", { class: "rq-group", "data-testid": "stopalt" },
    h("h4", { text: "Vehicle settings" }),
    h("div", { class: "rq-row tc-row" }, h("span", { text: "Stop altitude (m, at least 1)" }), saInput, saBtn, saEcho, saState),
    h("div", { class: "hint", text: `Altitude where the vehicle ends up stopped (default 20 m). Sent as a request: the vehicle acknowledges it, and the request is repeated until it does (up to 5 s). ${ACCEPT_NOTE}` }),
  ));

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
    const entry = ctx.store.get(ctx.view(), "MissionState");
    const echo = entry && entry.f ? entry.f.stop_altitude : null;
    if (ctx.view() !== "live") sa = null;
    const route = ctx.status()?.request_routes?.[String(TARGET)] || null;
    const s = sendState({ apiState: ctx.api.state, view: ctx.view(), canControl, inflight: false, route, argOk: parseStopAltitude(saInput.value) !== null });
    saBtn.disabled = !s.enabled;
    saBtn.dataset.why = s.reason;
    saEcho.textContent = echoText(echo);
    const rep = reportedSimulation(ctx.store.get(ctx.view(), "VehicleStatus"));
    sim.textContent = rep.text;
    sim.dataset.tone = rep.tone;
    if (!sa) {
      saState.textContent = "";
      saState.dataset.tone = "info";
      return;
    }
    const r = findRequest(reqs, sa.reqId);
    if (r && r.state === "acked" && r.status_name === "DONE" && sa.ackedAt === null) sa.ackedAt = nowS();
    const d = describeSetting(sa.reqId === null ? null : r || { state: "sending", attempts: 1, max_attempts: 5 }, sa.target, echo, sa.ackedAt === null ? null : nowS() - sa.ackedAt);
    saState.textContent = d.text;
    saState.dataset.tone = d.tone;
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
