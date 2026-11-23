// Requests panel (docs/requests.md). SAFETY-RELEVANT UI: the only way the GUI makes the core send a REQUEST.
// See requests_logic.js for the rules. The core enforces the whitelist, control hosts, rate limit and the
// confirmation arguments again; this file only provides the deliberate gestures.
import { h } from "../ui.js";
import {
  DANGER_ENABLE_MS, DANGER_TEXT, DangerGate, dangerText, HoldTimer, REQUEST_HOLD_MS, actionKey, describeRequest, groupActions,
  requestFor, sendState, parseArgF, parseArgI, homeText,
} from "./requests_logic.js";

/** Modal for a `danger` action. Resolves true only when the operator presses the confirm button, which stays disabled for 2 s. */
export function dangerDialog(a, gate = new DangerGate()) {
  return new Promise((resolve) => {
    const done = (v) => {
      clearInterval(tick);
      gate.close();
      document.removeEventListener("keydown", onKey, true);
      box.remove();
      resolve(v);
    };
    const onKey = (e) => {
      if (e.key === "Escape") done(false);
    };
    const cancel = h("button", { class: "rc-cancel", text: "Cancel", onclick: () => done(false) });
    const ok = h("button", { class: "danger rc-ok", disabled: true, onclick: () => gate.ready() && done(true) });
    const refresh = () => {
      const ready = gate.ready();
      ok.disabled = !ready;
      ok.textContent = ready ? `Send ${a.label}` : `Send ${a.label} (${Math.ceil(gate.remainingMs() / 1000)})`;
    };
    const box = h(
      "div",
      { class: "rc-modal", role: "dialog", "aria-modal": "true", "data-testid": "danger-modal" },
      h("div", { class: "rc-dialog" }, h("h3", { text: `${a.label} (node ${a.target})` }), h("p", { class: "rq-danger-text", text: dangerText(a) }), h("div", { class: "toolbar" }, cancel, ok)),
    );
    gate.open();
    const tick = setInterval(refresh, 100);
    refresh();
    document.addEventListener("keydown", onKey, true);
    document.body.append(box);
    cancel.focus();
  });
}

export function createRequestsPanel(el, ctx) {
  let actions = [];
  let listError = "whitelist not loaded";
  let reqs = null; // last `requests` push
  const rows = new Map(); // action key -> { a, btn, bar, state, stop }
  const msg = h("div", { class: "rq-msg", role: "status" });
  const body = h("div", { class: "rq-body" });
  const home = h("div", { class: "rq-home mono", "data-testid": "rq-home" });
  el.append(h("div", { class: "rq-note", text: "One-shot requests. Hold a button for 1 s to send. The state shown comes from the vehicle's acknowledgements only." }), home, body, msg);

  async function load() {
    try {
      actions = (await ctx.api.request("request.list", {}, 5000)).actions.filter((a) => a.action !== "SET_STOP_ALTITUDE" && a.group !== "Simulation"); // those have their own controls (Vehicle panel, Simulation testing page)
      listError = "";
      reqs = await ctx.api.request("request.status", {}, 5000);
    } catch (e) {
      listError = `request.list: ${e.message}`;
    }
    build();
    render();
  }

  async function send(a, understand, argF, argI) {
    try {
      await ctx.api.request("request.send", { action: a.action, target: a.target, confirm: a.action, understand_link_stops: !!understand, arg_i: argI ?? a.arg_i ?? 0, ...(argF === null || argF === undefined ? {} : { arg_f: argF }) }, 5000);
      msg.textContent = "";
    } catch (e) {
      msg.textContent = `${a.action}: ${e.message}`;
    }
  }

  async function confirmed(a, argF, argI) {
    if (a.confirm === "danger") {
      if (!(await dangerDialog(a))) return;
      return send(a, true, argF, argI);
    }
    return send(a, false, argF, argI);
  }

  function build() {
    body.replaceChildren();
    rows.clear();
    if (listError) body.append(h("div", { class: "rq-err", text: listError }));
    else if (!actions.length) body.append(h("div", { class: "rq-err", text: "The request whitelist is empty (config [requests])." }));
    for (const g of groupActions(actions)) {
      const sec = h("section", { class: "rq-group" }, h("h4", { text: g.group }));
      for (const a of g.items) {
        const bar = h("span", { class: "bar" });
        const btn = h("button", { class: `rq-btn${a.confirm === "danger" ? " danger" : ""}`, "data-action": a.action, "data-target": String(a.target), "data-confirm": a.confirm, title: `hold ${REQUEST_HOLD_MS / 1000} s to send` }, h("span", { class: "rq-label", text: a.label }), bar);
        const finput = a.arg_f ? h("input", { type: "number", class: "tc-param tc-float", value: String(a.arg_f.default), min: String(a.arg_f.min), max: String(a.arg_f.max), step: "any", title: a.arg_f.name || "argument", "data-testid": "rq-arg", "aria-label": `${a.action} ${a.arg_f.name || "argument"}` }) : null;
        if (finput) finput.addEventListener("input", render);
        const iinput = a.arg_i_range ? h("input", { type: "number", class: "tc-param", value: String(a.arg_i), min: String(a.arg_i_range.min), max: String(a.arg_i_range.max), step: "1", title: a.arg_i_range.name || "integer argument", "aria-label": `${a.action} ${a.arg_i_range.name || "integer argument"}` }) : null;
        if (iinput) iinput.addEventListener("input", render);
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
          confirmed(a, finput ? parseArgF(finput.value, a.arg_f) : null, iinput ? parseArgI(iinput.value, a.arg_i_range) : null);
        }, undefined, REQUEST_HOLD_MS);
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
        sec.append(h("div", { class: "rq-row", "data-key": actionKey(a) }, btn, iinput, finput, h("span", { class: "rq-target", text: `${a.target_name} (node ${a.target})` }), route, state));
        rows.set(actionKey(a), { a, btn, finput, iinput, route, state, stop });
      }
      body.append(sec);
    }
  }

  const routeOf = (a) => {
    const st = ctx.status();
    return st && st.request_routes ? st.request_routes[String(a.target)] || null : null;
  };

  function renderHome() {
    const hd = homeText(ctx.store.get(ctx.view(), "HomePosition"));
    home.textContent = hd.text;
    home.dataset.tone = hd.tone;
  }

  function render() {
    renderHome();
    const canControl = !!(ctx.api.welcome && ctx.api.welcome.can_control);
    for (const r of rows.values()) {
      const cur = requestFor(reqs, r.a);
      const inflight = !!cur && cur.state === "sending";
      const route = routeOf(r.a);
      const s = sendState({ apiState: ctx.api.state, view: ctx.view(), canControl, inflight, route, argOk: (!r.finput || parseArgF(r.finput.value, r.a.arg_f) !== null) && (!r.iinput || parseArgI(r.iinput.value, r.a.arg_i_range) !== null) });
      r.btn.disabled = !s.enabled;
      if (!s.enabled) r.stop();
      r.btn.dataset.why = s.reason;
      const d = describeRequest(cur);
      r.state.textContent = d.text;
      r.state.dataset.tone = d.tone;
      r.route.textContent = route ? `via ${route}` : "no route";
    }
  }

  return {
    onReady: load,
    onRequests(m) {
      reqs = m;
      render();
    },
    onStatus: () => render(),
    onTick: renderHome,
    onViewChange: () => render(),
    onRc() {},
    destroy() { for (const r of rows.values()) r.stop(); },
    debug: () => ({ rows: [...rows.keys()], reqs, listError }),
  };
}
