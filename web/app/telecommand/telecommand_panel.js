// 'Commands (temporary)' panel (docs/telecommands.md). TEMPORARY, SAFETY-RELEVANT UI: the only way the GUI makes the
// core send a legacy telecommand. See telecommand_logic.js for the rules; the core enforces the whitelist, control
// hosts, rate limit and confirmation again.
import { h } from "../ui.js";
import { StopAltitudeSetter, ACCEPT_NOTE, COMMAND as STOP_CMD, DEFAULT_M, MAX_M, MIN_M, echoText, parseStopAltitude } from "./stopalt_logic.js";
import { HoldTimer, NO_ACK_TEXT, TELECOMMAND_HOLD_MS, describeSent, groupCommands, hasFloat, lastSent, parseFloatParam, parseParam, reportedSimulation, sendState } from "./telecommand_logic.js";

export function createTelecommandPanel(el, ctx) {
  let commands = [];
  let listError = "whitelist not loaded";
  let push = null; // last `telecommands` push
  const rows = new Map(); // name -> { c, btn, input, state, route, stop }
  const msg = h("div", { class: "rq-msg", role: "status" });
  const sim = h("div", { class: "tc-sim", "data-testid": "tc-sim" });
  const body = h("div", { class: "rq-body" });
  el.append(
    h("div", { class: "tc-warn", text: "TEMPORARY legacy telecommands (non-safety only), until the flight software's T9b-5. Hold a button for 1 s to send ONE frame. No ACK, no retry; the vehicle de-duplicates nothing." }),
    h("div", { class: "tc-warn", "data-testid": "tc-radio-note", text: "Safety commands are given with the Pocket switches, or the virtual switches when the Pocket is not connected (Virtual radio panel): SA go/abort, SB profile, SC real/simulation, SD motors armed/disarmed, SE short press = reset system state, SE hold = zero estimators. The ground station's command engine turns them into the command state it sends continuously. The vehicle ignores the old Arm, SimulationMode, MissionBegin, MissionSelect, MissionAbort and SystemReset telecommands." }),
    sim, body, msg,
  );

  // ---- Vehicle settings: stop altitude (the confirmation is the echoed MissionState.stop_altitude; docs/telecommands.md)
  const setter = new StopAltitudeSetter();
  const saInput = h("input", { type: "number", class: "tc-param tc-float", value: String(DEFAULT_M), min: String(MIN_M), max: String(MAX_M), step: "0.5", "data-testid": "stopalt-input", "aria-label": "stop altitude in metres" });
  const saBtn = h("button", { class: "rq-btn", "data-testid": "stopalt-set" }, "Set");
  const saEcho = h("span", { class: "mono", "data-testid": "stopalt-echo" });
  const saState = h("span", { class: "rq-state", "data-testid": "stopalt-state" });
  const nowS = () => performance.now() / 1000;
  const sendStop = async () => {
    try {
      await ctx.api.request("telecommand.send", { name: STOP_CMD, confirm: STOP_CMD, param_float: setter.target }, 5000);
      msg.textContent = "";
    } catch (e) {
      msg.textContent = `${STOP_CMD}: ${e.message}`;
    }
  };
  saBtn.addEventListener("click", () => {
    const v = parseStopAltitude(saInput.value);
    if (v === null || saBtn.disabled) return;
    setter.request(v, nowS());
    sendStop();
    render();
  });
  saInput.addEventListener("input", render);
  el.append(h("section", { class: "rq-group", "data-testid": "stopalt" },
    h("h4", { text: "Vehicle settings" }),
    h("div", { class: "rq-row tc-row" }, h("span", { text: "Stop altitude (m, 5 to 60)" }), saInput, saBtn, saEcho, saState),
    h("div", { class: "hint", text: `Altitude where the vehicle ends up stopped (default 20 m). Sent at once, repeated every 2 s up to 5 times until the vehicle echoes the value. ${ACCEPT_NOTE}` }),
  ));

  function renderStop(canControl, route) {
    const entry = ctx.store.get(ctx.view(), "MissionState");
    const echo = entry && entry.f ? entry.f.stop_altitude : null;
    if (ctx.view() !== "live") setter.cancel();
    const t = setter.tick(echo, nowS());
    if (t.send) sendStop();
    if (t.notify) {
      msg.textContent = t.notify;
      try {
        ctx.notifier().show({ id: "gs.stopalt", category: "state", text: t.notify, label: t.notify, severity: "warning", key: "gs.stopalt" });
      } catch (e) {
        /* the message line above still shows it */
      }
    }
    const s = sendState({ apiState: ctx.api.state, view: ctx.view(), canControl, route, paramOk: parseStopAltitude(saInput.value) !== null });
    saBtn.disabled = !s.enabled;
    saBtn.dataset.why = s.reason;
    saEcho.textContent = echoText(echo);
    const st = setter.status();
    saState.textContent = st.text;
    saState.dataset.tone = st.tone;
  }

  async function load() {
    try {
      // simulation entries (rate_group wind, group 'Simulation tests') live on the 'Simulation testing' page
      commands = (await ctx.api.request("telecommand.list", {}, 5000)).commands.filter((c) => !c.rate_group && c.group !== "Simulation tests" && c.name !== STOP_CMD);
      listError = "";
    } catch (e) {
      listError = `telecommand.list: ${e.message}`;
    }
    build();
    render();
  }

  async function send(c, param, paramFloat) {
    try {
      await ctx.api.request(
        "telecommand.send",
        { name: c.name, confirm: c.name, ...(param === null ? {} : { param }), ...(paramFloat === null ? {} : { param_float: paramFloat }) },
        5000,
      );
      msg.textContent = "";
    } catch (e) {
      msg.textContent = `${c.name}: ${e.message}`;
    }
  }

  function build() {
    body.replaceChildren();
    rows.clear();
    if (listError) body.append(h("div", { class: "rq-err", text: listError }));
    else if (!commands.length) body.append(h("div", { class: "rq-err", text: "The telecommand whitelist is empty (config [telecommands])." }));
    for (const g of groupCommands(commands)) {
      const sec = h("section", { class: "rq-group" }, h("h4", { text: g.group }));
      for (const c of g.items) {
        const bar = h("span", { class: "bar" });
        const btn = h("button", { class: "rq-btn", "data-name": c.name, "data-type": c.type, title: `hold ${TELECOMMAND_HOLD_MS / 1000} s to send ONE frame` }, h("span", { class: "rq-label", text: c.label }), bar);
        const ranged = c.param_min !== null && c.param_min !== undefined;
        const input = ranged ? h("input", { type: "number", class: "tc-param", value: String(c.param_int), min: String(c.param_min), max: String(c.param_max), step: "1", "aria-label": `${c.name} parameter` }) : null;
        const fl = hasFloat(c);
        const finput = fl ? h("input", { type: "number", class: "tc-param tc-float", value: String(c.param_float), min: String(c.param_float_min), max: String(c.param_float_max), step: "any", title: c.param_float_name || "float parameter", "aria-label": `${c.name} ${c.param_float_name || "parameter"}` }) : null;
        const route = h("span", { class: "rq-route" });
        const state = h("span", { class: "rq-state" });
        let anim = null;
        const stopAnim = () => {
          if (anim !== null) clearInterval(anim);
          anim = null;
          bar.style.width = "0";
        };
        const timer = new HoldTimer(() => {
          stopAnim();
          send(c, input ? parseParam(input.value, c) : null, finput ? parseFloatParam(finput.value, c) : null);
        }, undefined, TELECOMMAND_HOLD_MS);
        const stop = () => { timer.cancel(); stopAnim(); };
        const start = () => {
          if (btn.disabled || timer.holding) return;
          timer.start();
          anim = setInterval(() => { bar.style.width = `${Math.round(timer.progress() * 100)}%`; }, 40);
        };
        btn.addEventListener("pointerdown", (e) => {
          try {
            btn.setPointerCapture(e.pointerId);
          } catch (err) {
            /* synthetic pointer ids have no capture */
          }
          start();
        });
        for (const ev of ["pointerup", "pointercancel", "lostpointercapture", "pointerleave", "blur"]) btn.addEventListener(ev, stop);
        btn.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { start(); e.preventDefault(); } });
        btn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") stop(); });
        if (input) input.addEventListener("input", render);
        if (finput) finput.addEventListener("input", render);
        sec.append(h("div", { class: "rq-row tc-row", "data-name": c.name }, btn, input, finput, h("span", { class: "rq-target", text: c.note || "" }), route, state));
        rows.set(c.name, { c, btn, input, finput, route, state, stop });
      }
      body.append(sec);
    }
  }

  function render() {
    const canControl = !!(ctx.api.welcome && ctx.api.welcome.can_control);
    const st = ctx.status();
    const route = st ? st.telecommand_route || null : null;
    const now = ctx.store.now("live");
    for (const r of rows.values()) {
      const paramOk = (!r.input || parseParam(r.input.value, r.c) !== null) && (!r.finput || parseFloatParam(r.finput.value, r.c) !== null);
      const s = sendState({ apiState: ctx.api.state, view: ctx.view(), canControl, route, paramOk });
      r.btn.disabled = !s.enabled;
      if (!s.enabled) r.stop();
      r.btn.dataset.why = s.reason;
      const d = describeSent(lastSent(push, r.c.name), now);
      r.state.textContent = d.text;
      r.state.dataset.tone = d.tone;
      r.route.textContent = route ? `via ${route}` : "no route";
    }
    const rep = reportedSimulation(ctx.store.get(ctx.view(), "VehicleStatus"));
    sim.textContent = rep.text;
    sim.dataset.tone = rep.tone;
    renderStop(canControl, route);
    el.title = NO_ACK_TEXT;
  }

  return {
    onReady: load,
    onTelecommands(m) {
      push = m;
      render();
    },
    onStatus: render,
    onTick: render,
    onViewChange: render,
    destroy() { for (const r of rows.values()) r.stop(); },
    debug: () => ({ rows: [...rows.keys()], push, listError }),
  };
}
