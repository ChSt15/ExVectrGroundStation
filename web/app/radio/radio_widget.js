// Virtual radio widget (design 5.6). SAFETY-RELEVANT UI: this is the GUI's virtual Pocket. Since the GS-control cutover
// the core's command engine is the only sender of vehicle commands and runs without any client; the widget only feeds it
// the virtual switches while this client holds the lease and the Pocket is not live (the Pocket wins). Rules kept here:
//  - Clicks only send requests. Every control shows the state the core reports in its `rc` push messages
//    (the panel's state), never an optimistic local state; what the VEHICLE applied is in the Command tab.
//  - Controls are operable only while this client holds the lease and the core reports "virtual" with the Pocket not live.
//  - Heartbeat every 300 ms while holding; closing the tab ends the lease at once (WebSocket close).
//  - A momentary control sends 1 every 100 ms while pressed and 0 on release, cancel, blur or hiding.
//  - A control with `lock` in the radio config is a padlock until held for 1 s, then operable until the
//    core relocks it (reported in `locks`). The GUI knows nothing about what a control means.
import { h } from "../ui.js";
import { stickConfig, stickState } from "./sticks.js";
import { createSticks } from "./sticks_widget.js";
import { commandPathRows } from "../status/command_path.js";
import { renderCommandPath } from "../status/command_path_view.js";
import { pickupHints } from "../command/command_logic.js";
import {
  HEARTBEAT_MS, MomentaryHold, HoldTimer, SLIDER_INTERVAL_MS, Throttle, controlEnabled, cyclePosition, describeRadio,
  needsReleasePrompt, nextToggle, showPadlock, sliderFromPointer, stateFromChannels, switchPosFromFraction, unlockRemaining,
} from "./radio_logic.js";

const VIEW_W = 200;
const VIEW_H = 140;

function padlockIcon() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "22");
  svg.setAttribute("height", "22");
  svg.setAttribute("aria-hidden", "true");
  const body = document.createElementNS(ns, "rect");
  for (const [k, v] of Object.entries({ x: 5, y: 11, width: 14, height: 10, rx: 2, fill: "currentColor" })) body.setAttribute(k, v);
  const arc = document.createElementNS(ns, "path");
  for (const [k, v] of Object.entries({ d: "M8 11V8a4 4 0 0 1 8 0v3", fill: "none", stroke: "currentColor", "stroke-width": 2 })) arc.setAttribute(k, v);
  svg.append(arc, body);
  return svg;
}

/** In-page confirmation (a native confirm() blocks automation and cannot be styled). Resolves true or false. */
export function confirmDialog(message, okLabel, cancelLabel) {
  return new Promise((resolve) => {
    const done = (v) => {
      document.removeEventListener("keydown", onKey, true);
      box.remove();
      resolve(v);
    };
    const onKey = (e) => {
      if (e.key === "Escape") done(false);
    };
    const cancel = h("button", { class: "rc-cancel", text: cancelLabel, onclick: () => done(false) });
    const ok = h("button", { class: "danger rc-ok", text: okLabel, onclick: () => done(true) });
    const box = h("div", { class: "rc-modal", role: "dialog", "aria-modal": "true" }, h("div", { class: "rc-dialog" }, h("p", { text: message }), h("div", { class: "toolbar" }, cancel, ok)));
    document.addEventListener("keydown", onKey, true);
    document.body.append(box);
    cancel.focus();
  });
}

export function createRadioWidget(el, ctx) {
  let cfg = null;
  let cfgError = "radio config not loaded";
  let rcAt = 0; // performance.now() when the last rc message arrived
  let shown = {}; // last displayed state per control id (the core's report), used to derive the next click
  const ctl = new Map(); // control id -> { spec, root, update(state, enabled, lock, us) }
  const disposers = [];

  const role = h("span", { class: "rc-role", "data-role": "none" });
  const banner = h("div", { class: "rc-banner", role: "status" });
  const warn = h("div", { class: "rc-warning", role: "alert", hidden: "" });
  const stage = h("div", { class: "rc-stage", style: `aspect-ratio: ${VIEW_W} / ${VIEW_H}` });
  const img = h("img", { class: "rc-bg", src: "/static/app/radio/pocket.svg", alt: "", draggable: "false" });
  const screen = h("div", { class: "rc-screen" });
  stage.append(img, screen);
  const take = h("button", { class: "rc-take danger", text: "Use virtual switches", onclick: () => act("rc.acquire", { kind: "connection" }) });
  const release = h("button", { class: "rc-release", text: "Release virtual switches", onclick: () => act("rc.release") });
  const msg = h("div", { class: "msg rc-msg" });
  const foot = h("div", { class: "hint", text: "Controls show the virtual panel state the core reports. What the vehicle applied: Command tab. SD = motors armed / disarmed." });
  const hintBox = h("div", { class: "rc-hints", "data-testid": "radio-hints", role: "status", hidden: "" });
  const lockNote = h("div", { class: "lock-note", "data-testid": "radio-lock", hidden: "" });
  const stickBox = h("div", { class: "rc-sticks" });
  const pathBox = h("div", { class: "cp-box", "data-testid": "radio-path" });
  el.append(role, banner, warn, hintBox, lockNote, h("div", { class: "toolbar" }, take, release), msg, stage, pathBox, stickBox, foot);
  // Stick display (read-only): the Pocket's sticks in slave, what the core sends in master (the channel defaults:
  // no stick sending exists), the recorded RcState in replay.
  const sticks = createSticks(stickBox);
  let stickCfg = stickConfig(null);

  // ------------------------------------------------------------ requests (never change what is displayed)

  async function act(op, payload = {}) {
    try {
      await ctx.api.request(op, payload, 5000);
      msg.textContent = "";
      return true;
    } catch (e) {
      msg.textContent = `${op}: ${e.message}`;
      return false;
    }
  }
  const setControl = (id, value) => act("rc.set", { values: { [id]: value } });

  const rc = () => ctx.rc();
  const isHolder = () => ctx.api.state === "open" && !!rc() && !!rc().holder_is_you && !!rc().lease;
  const current = () =>
    describeRadio({ apiState: ctx.api.state, view: ctx.view(), rc: rc(), canControl: ctx.api.welcome && ctx.api.welcome.can_control, cfg });

  // ------------------------------------------------------------ controls (built from the radio config)

  const momentaries = [];
  const holds = [];
  const throttles = [];

  function buildToggle(spec) {
    const knob = h("span", { class: "knob" });
    const b = h("button", { class: "rc-switch", role: "switch", "aria-label": spec.label || spec.id }, knob);
    b.addEventListener("click", () => setControl(spec.id, nextToggle(shown[spec.id] ?? 0)));
    return { root: b, set: (pos, on) => { b.dataset.pos = String(pos); b.setAttribute("aria-checked", String(Number(pos) > 0)); b.disabled = !on; } };
  }

  function buildSwitch3(spec) {
    const n = spec.values_us.length;
    const box = h("div", { class: "rc-switch3", role: "radiogroup", tabindex: "0", "aria-label": spec.label || spec.id }, h("span", { class: "knob" }));
    box.addEventListener("click", (e) => {
      const r = box.getBoundingClientRect();
      setControl(spec.id, switchPosFromFraction((e.clientY - r.top) / r.height, n));
    });
    box.addEventListener("keydown", (e) => {
      if (e.key === "ArrowUp") setControl(spec.id, Math.min(n - 1, (shown[spec.id] ?? 0) + 1));
      else if (e.key === "ArrowDown") setControl(spec.id, Math.max(0, (shown[spec.id] ?? 0) - 1));
      else if (e.key === " " || e.key === "Enter") setControl(spec.id, cyclePosition(shown[spec.id] ?? 0, n));
      else return;
      e.preventDefault();
    });
    box.dataset.n = String(n);
    return { root: box, set: (pos, on) => { box.dataset.pos = String(pos); box.dataset.disabled = String(!on); box.tabIndex = on ? 0 : -1; } };
  }

  function buildMomentary(spec) {
    const hold = new MomentaryHold((v) => setControl(spec.id, v));
    momentaries.push(hold);
    const b = h("button", { class: "rc-mom", "aria-label": spec.label || spec.id }, "hold");
    b.addEventListener("pointerdown", (e) => {
      if (b.disabled) return;
      try {
        b.setPointerCapture(e.pointerId);
      } catch (err) {
        /* synthetic events without a capturable pointer */
      }
      hold.press();
    });
    for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) b.addEventListener(ev, () => hold.release());
    b.addEventListener("keydown", (e) => {
      if ((e.key === " " || e.key === "Enter") && !e.repeat && !b.disabled) hold.press();
    });
    b.addEventListener("keyup", (e) => {
      if (e.key === " " || e.key === "Enter") hold.release();
    });
    b.addEventListener("blur", () => hold.release());
    return { root: b, set: (pos, on) => { b.dataset.pos = String(pos); b.disabled = !on; if (!on) hold.release(); } };
  }

  function buildSlider(spec) {
    const input = h("input", { class: "rc-slider", type: "range", min: "0", max: "1000", step: "1", value: "0", "aria-label": spec.label || spec.id });
    const th = new Throttle(SLIDER_INTERVAL_MS, (v) => setControl(spec.id, v));
    throttles.push(th);
    let dragging = false;
    let lastInput = -Infinity;
    input.addEventListener("pointerdown", () => { dragging = true; });
    const end = () => { dragging = false; th.flush(); };
    input.addEventListener("pointerup", end);
    input.addEventListener("pointercancel", end);
    input.addEventListener("input", () => { lastInput = performance.now(); th.push(Number(input.value) / 1000); });
    input.addEventListener("change", () => th.flush());
    return {
      root: input,
      // While the thumb is being dragged it follows the pointer; it snaps to the core's report on release.
      set: (pos, on) => { input.disabled = !on; if (!dragging && performance.now() - lastInput > 400) input.value = String(Math.round(Number(pos) * 1000)); input.dataset.pos = Number(pos).toFixed(3); if (!on) th.cancel(); },
      readout: (pos) => Number(pos).toFixed(2),
    };
  }

  /** Padlock for a control with `lock` in the config: hold 1 s to unlock (the core does the unlocking). */
  function buildPadlock(spec) {
    const bar = h("span", { class: "bar" });
    const btn = h("button", { class: "rc-padlock", "aria-label": `${spec.label || spec.id} is locked: hold 1 s to unlock`, title: "hold 1 s to unlock" }, padlockIcon(), bar);
    const info = h("span", { class: "rc-lockinfo" });
    let anim = null;
    const stopAnim = () => {
      if (anim !== null) clearInterval(anim);
      anim = null;
      bar.style.width = "0";
    };
    const timer = new HoldTimer(() => {
      stopAnim();
      act("rc.unlock", { control: spec.id });
    });
    holds.push({ cancel: () => { timer.cancel(); stopAnim(); } });
    const start = () => {
      if (btn.disabled || timer.holding) return;
      timer.start();
      anim = setInterval(() => { bar.style.width = `${Math.round(timer.progress() * 100)}%`; }, 40);
    };
    const stop = () => { timer.cancel(); stopAnim(); };
    btn.addEventListener("pointerdown", (e) => {
      try {
        btn.setPointerCapture(e.pointerId);
      } catch (err) {
        /* see above */
      }
      start();
    });
    for (const ev of ["pointerup", "pointercancel", "lostpointercapture", "pointerleave", "blur"]) btn.addEventListener(ev, stop);
    btn.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { start(); e.preventDefault(); } });
    btn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") stop(); });
    const relock = h("button", { class: "rc-relock", text: "Lock", title: "lock again (the control returns to its default = disarmed)", onclick: () => act("rc.lock", { control: spec.id }) });
    return { btn, info, relock, stop };
  }

  function buildControl(spec) {
    const kind = { toggle2: buildToggle, switch3: buildSwitch3, momentary: buildMomentary, slider: buildSlider }[spec.kind];
    const inner = kind(spec);
    const us = h("span", { class: "rc-us" });
    const meaning = h("span", { class: "rc-meaning", text: spec.meaning || "" });
    const root = h("div", { class: `rc-ctl rc-k-${spec.kind}`, "data-id": spec.id, "data-kind": spec.kind, style: `left:${(spec.svg.x / VIEW_W) * 100}%; top:${(spec.svg.y / VIEW_H) * 100}%` });
    root.append(h("span", { class: "rc-label", text: spec.label || spec.id, title: spec.meaning || "" }));
    let lockUi = null;
    if (spec.lock) {
      lockUi = buildPadlock(spec);
      root.append(lockUi.btn, inner.root, lockUi.info, lockUi.relock);
    } else {
      root.append(inner.root);
    }
    root.append(us, meaning);
    const readout = inner.readout;
    return {
      spec,
      root,
      stop: () => lockUi && lockUi.stop(),
      update(pos, enabled, lock, usVal, banner, unlockLeft, lockedWhileOperating) {
        inner.set(pos, enabled);
        us.textContent = usVal == null ? "" : readout ? `${readout(pos)} · ${usVal} µs` : `${usVal} µs`;
        root.dataset.enabled = String(enabled);
        if (lockUi) {
          root.dataset.lock = lock ? (lock.locked ? "locked" : "unlocked") : "unknown";
          lockUi.btn.hidden = !lockedWhileOperating;
          inner.root.hidden = lockedWhileOperating;
          lockUi.btn.disabled = !banner.interactive;
          const open = banner.interactive && lock && !lock.locked;
          lockUi.relock.hidden = !open;
          lockUi.info.textContent = lockedWhileOperating ? "hold 1 s" : open ? (lock.armed ? "unlocked · armed" : "unlocked · disarmed") : lock && lock.locked ? "locked" : "";
        }
      },
    };
  }

  function build(radioCfg) {
    cfg = radioCfg;
    stickCfg = stickConfig(radioCfg);
    cfgError = null;
    stage.querySelectorAll(".rc-ctl").forEach((n) => n.remove());
    ctl.clear();
    for (const spec of cfg.controls || []) {
      if (!spec.svg || typeof spec.svg.x !== "number") continue;
      const c = buildControl(spec);
      ctl.set(spec.id, c);
      stage.append(c.root);
    }
  }

  async function loadConfig() {
    try {
      build(await ctx.api.request("config.get", { name: "radio" }));
    } catch (e) {
      cfg = null;
      cfgError = e.code === "not_found" ? "No radio config on this core." : `radio config: ${e.message}`;
    }
    render();
  }

  // ------------------------------------------------------------ render: always from what the core reported

  function replayState() {
    const it = ctx.store.get("replay", "RcState");
    return it ? { state: stateFromChannels(cfg.controls, it.f.ch_us), ch: it.f.ch_us, item: it } : null;
  }

  function render() {
    const b = current();
    banner.dataset.tone = b.tone;
    take.disabled = !b.canTake;
    take.hidden = b.canRelease;
    release.hidden = !b.canRelease;
    const view = ctx.view();
    const r = rc();
    let state = {};
    let ch = [];
    let locks = {};
    let sub = "";
    let screenText = "";
    lockNote.hidden = !(view === "replay" || ctx.api.state !== "open");
    lockNote.textContent = view === "replay" ? "replay: inputs locked" : "no core connection: inputs locked";
    if (!cfg) {
      banner.textContent = cfgError || "radio config not loaded";
      banner.dataset.tone = "dim";
      take.disabled = true;
      screen.textContent = "";
      sticks.update(null, { caption: "sticks: radio config not loaded" });
      return;
    }
    if (ctx.api.state !== "open") {
      screenText = "no link";
    } else if (view === "replay") {
      const rs = replayState();
      if (rs) {
        state = rs.state;
        ch = rs.ch;
        sub = ` · ${rs.item.src} · origin ${rs.item.f.origin}`;
        screenText = "REPLAY";
      } else {
        sub = " · no RcState in this log";
        screenText = "REPLAY";
      }
    } else if (r) {
      state = r.controls || {};
      ch = r.channels_us || [];
      locks = r.locks || {};
      if (r.mode === "pocket") {
        const now = ctx.store.now("live");
        sub = now != null && r.t != null ? ` · ${Math.max(0, now - r.t).toFixed(1)} s ago` : "";
        screenText = "HANDSET";
      } else if (r.mode === "virtual") {
        screenText = r.command && r.command.input === "virtual" ? "VIRTUAL" : "PANEL";
      } else {
        screenText = "IDLE";
      }
    }
    banner.textContent = b.text + sub;
    renderCommandPath(pathBox, commandPathRows(ctx.store.get(view, "VehicleStatus")));
    renderSticks(view, r, ch);
    role.textContent = b.roleText;
    role.dataset.role = b.role;
    warn.textContent = b.warning || "";
    warn.hidden = !b.warning;
    const hints = view === "live" && ctx.api.state === "open" && r && r.command ? pickupHints(r.command.hints) : [];
    hintBox.textContent = hints.length ? `Waiting for the input: ${hints.join("; ")}` : "";
    hintBox.hidden = !hints.length;
    screen.textContent = screenText;
    shown = { ...state };
    const nowMs = performance.now();
    for (const [id, c] of ctl) {
      const spec = c.spec;
      const lock = locks[id];
      const pos = state[id] ?? spec.default ?? 0;
      const left = unlockRemaining(lock, rcAt, nowMs);
      const usVal = ch.length >= spec.channel ? ch[spec.channel - 1] : null;
      c.update(pos, controlEnabled(b, spec, lock), lock, usVal, b, left, showPadlock(b, spec.lock ? lock : undefined));
    }
    if (!b.interactive) for (const m of momentaries) m.release();
    if (!b.interactive) for (const hd of holds) hd.cancel();
  }

  function renderSticks(view, r, ch) {
    if (ctx.api.state !== "open") return sticks.update(null, { caption: "sticks: no link" });
    if (view === "replay") {
      const it = ctx.store.get("replay", "RcState");
      return sticks.update(it ? stickState(stickCfg, it.f.ch_us) : null, { caption: it ? `Sticks · recorded (${it.f.origin ?? "?"})` : "Sticks · no RcState in this log" });
    }
    if (!r) return sticks.update(null, { caption: "sticks: RC not available" });
    if (r.mode === "pocket") {
      const now = ctx.store.now("live");
      const age = now != null && r.t != null ? Math.max(0, now - r.t) : null;
      return sticks.update(stickState(stickCfg, ch), { caption: `Sticks · Pocket (received)${age !== null ? ` · ${age.toFixed(1)} s ago` : ""}`, stale: age !== null && age > 0.5 });
    }
    return sticks.update(stickState(stickCfg, ch), { caption: "Sticks · display only (the ground station sends no stick values)" });
  }

  // ------------------------------------------------------------ heartbeat, safety nets

  const hb = setInterval(() => {
    if (isHolder()) ctx.api.request("rc.heartbeat", {}, 2000).catch(() => {});
  }, HEARTBEAT_MS);
  disposers.push(() => clearInterval(hb));

  const releaseMomentaries = () => {
    for (const m of momentaries) m.release();
    for (const t of throttles) t.flush();
  };
  const onBlur = () => releaseMomentaries();
  const onHidden = () => {
    if (document.visibilityState === "hidden") releaseMomentaries();
  };
  window.addEventListener("blur", onBlur);
  document.addEventListener("visibilitychange", onHidden);
  disposers.push(() => window.removeEventListener("blur", onBlur), () => document.removeEventListener("visibilitychange", onHidden));

  // Switching to the replay view while holding control asks first (design 5.6); the view guard is the only
  // path, so the header toggle and the recording panel are both covered.
  const removeGuard = ctx.addViewGuard(async (next) => {
    if (!needsReleasePrompt(next, rc())) return true;
    const yes = await confirmDialog("Release control and switch to replay? Sending stops and every control returns to its default.", "Release and switch", "Stay live");
    if (!yes) return false;
    try {
      await ctx.api.request("rc.release", {}, 5000);
    } catch (e) {
      if (e.code !== "no_lease") {
        msg.textContent = `rc.release: ${e.message}; staying in the live view`;
        return false;
      }
    }
    return true;
  });
  disposers.push(removeGuard);

  const inst = {
    /** Dock tab marker: amber while a Pocket loss is reported, red when the vehicle latched another session. */
    alert() {
      const r = rc();
      if (ctx.api.state !== "open" || !r || !r.command) return "";
      if (r.command.master === "not_master" || r.command.multi_master) return "bad";
      return r.command.pocket === "lost" ? "warn" : "";
    },
    onRc() {
      rcAt = performance.now();
      render();
    },
    onStatus: () => render(),
    onTick: () => render(),
    onPacket: () => {
      if (ctx.view() === "replay") render();
    },
    onReset: () => render(),
    onViewChange(view) {
      // Defence in depth: a client never holds control while it shows replay data.
      if (view === "replay" && isHolder()) act("rc.release");
      render();
    },
    onReady() {
      loadConfig();
    },
    destroy() {
      // The panel going away (layout change) must not leave a lease whose heartbeat has stopped: release it.
      releaseMomentaries();
      if (isHolder()) ctx.api.request("rc.release", {}, 2000).catch(() => {});
      for (const d of disposers) d();
      for (const c of ctl.values()) c.stop();
    },
    /** Debug hook for end-to-end tests. */
    debug() {
      const out = { role: role.textContent, warning: warn.hidden ? "" : warn.textContent, banner: banner.textContent, tone: banner.dataset.tone, take: { hidden: take.hidden, disabled: take.disabled }, release: { hidden: release.hidden }, controls: {} };
      for (const [id, c] of ctl) {
        const padlock = c.root.querySelector(".rc-padlock");
        out.controls[id] = { pos: shown[id], enabled: c.root.dataset.enabled === "true", lock: c.root.dataset.lock || null, padlock: !!padlock && !padlock.hidden, us: c.root.querySelector(".rc-us").textContent };
      }
      return out;
    },
  };
  if (ctx.api.state === "open") loadConfig();
  return inst;
}
