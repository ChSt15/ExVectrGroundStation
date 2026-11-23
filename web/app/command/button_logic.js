// Pure logic of the GS command buttons (header ABORT / FAILSAFE and the Command panel). SAFETY-RELEVANT UI rules:
//  - Nothing is displayed from a click: states come from the core's `rc` push (`command.out`, `hints`, `input`).
//  - ABORT and FAILSAFE act in the core (`engine.abort`, `engine.failsafe`), so they work with any input and with no
//    lease, and still act if this tab closes right after the hold. They are held for HOLD_MS, no dialog.
//  - The panel buttons drive the VIRTUAL panel (rc.set), so the engine's pickup rule applies unchanged.
export const HOLD_MS = 500; // ABORT / FAILSAFE hold time
export const ARM_HOLD_MS = 1000; // arming the motors keeps the 1 s padlock-like hold of the virtual SD guard
export const SE_REPEAT_MS = 100; // SE=1 refresh while a momentary button is held (the core lapses it after 300 ms)
export const SE_PRESS_S = 0.3; // hold_s of the short reset press

/** Can the safety buttons act? {ok, why}: only the core / link being down (or a view-only client) disables them. */
export function safetyGuard({ open, rc, canControl }) {
  if (!open) return { ok: false, why: "core offline" };
  if (!rc || !rc.command) return { ok: false, why: "no command engine (replay or no core engine)" };
  if (canControl === false) return { ok: false, why: "view only (not a control host)" };
  return { ok: true, why: "" };
}

/** Header FAILSAFE / ABORT button view from the core's report. */
export function headerButtons({ open, rc, canControl }) {
  const g = safetyGuard({ open, rc, canControl });
  const latched = !!(g.ok && rc.command.failsafe_latched);
  const none = g.ok ? "" : g.why;
  return {
    abort: { label: "ABORT", disabled: !g.ok, title: g.ok ? "hold 500 ms: go off at once" : `ABORT unavailable: ${none}` },
    failsafe: {
      label: latched ? "Clear failsafe request" : "FAILSAFE",
      state: latched ? "requested" : "idle",
      banner: latched ? "FAILSAFE REQUESTED" : "",
      disabled: !g.ok,
      title: g.ok ? (latched ? "hold 500 ms to clear the failsafe request" : "hold 500 ms: request failsafe (motors off, go off)") : `FAILSAFE unavailable: ${none}`,
      on: !latched, // the value `engine.failsafe` is called with when the hold completes
    },
  };
}

/** The Command panel: which input is active and what each button can do. */
export function panelState({ open, rc, canControl }) {
  const g = safetyGuard({ open, rc, canControl });
  const cmd = g.ok || (open && rc && rc.command) ? rc.command : null;
  const out = (cmd && cmd.out) || {};
  const hints = (cmd && cmd.hints) || {};
  const holder = !!(rc && rc.holder_is_you && rc.lease);
  const other = !!(rc && rc.lease && !rc.holder_is_you);
  const pocket = !!cmd && cmd.input === "pocket";
  let input = "none";
  let note = "";
  if (!open) note = "core offline";
  else if (!cmd) note = "no command engine";
  else if (canControl === false) note = "view only (not a control host)";
  else if (pocket) { input = "pocket"; note = "Pocket in control"; }
  else if (other) { input = cmd.input; note = `virtual switches held by ${rc.lease.holder}`; }
  else if (holder) { input = "virtual"; note = "Virtual input: the buttons drive the virtual switches"; }
  else note = "No input: a button takes the virtual switches";
  const usable = !!cmd && open && canControl !== false && !pocket && !other;
  const armed = out.motors === 1;
  const go = out.go === 1;
  const sim = out.vehicle_mode === 1;
  const b = (disabled, why = "") => ({ disabled: !usable || disabled, why: usable ? why : note });
  return {
    input, note, pocket, holder, usable, out,
    state: { go: go ? "GO" : "NOT GO", motors: armed ? "ARMED" : "DISARMED", mode: sim ? "SIMULATION" : "REAL", reset: out.reset_button === 1 ? "PRESSED" : "released" },
    buttons: {
      begin: b(go || !!hints.sa, hints.sa ? "go is waiting: press Abort first so the switch is seen off" : go ? "go is already on" : ""),
      abort: { disabled: !(open && cmd && canControl !== false), why: open && cmd ? "" : note },
      motors: b(!armed && !!hints.sd, !armed && hints.sd ? "motors are waiting: disarm first so the switch is seen off" : ""),
      real: b(!sim, ""),
      sim: b(sim, ""),
      reset: b(!!hints.se, hints.se ? "SE is waiting: release first" : ""),
      zero: b(!!hints.se, hints.se ? "SE is waiting: release first" : ""),
    },
    motorsAction: armed ? "disarm" : "arm",
  };
}

/** The request sequences of the panel buttons: [{op, ...params}], run in order. `holder`: this client holds the lease. */
export function buttonOps(name, st) {
  const acquire = st.holder ? [] : [{ op: "rc.acquire", kind: "connection" }];
  switch (name) {
    case "begin": return [...acquire, { op: "rc.set", values: { SA: 1 } }];
    case "abort": return [{ op: "engine.abort" }, ...(st.holder ? [{ op: "rc.set", values: { SA: 0 } }] : [])];
    case "arm": return [...acquire, { op: "rc.unlock", control: "SD" }, { op: "rc.set", values: { SD: 1 } }];
    case "disarm": return [...acquire, { op: "rc.set", values: { SD: 0 } }];
    case "real": return [...acquire, { op: "rc.set", values: { SC: 0 } }];
    case "sim": return [...acquire, { op: "rc.set", values: { SC: 1 } }];
    case "reset": return [...acquire, { op: "rc.set", values: { SE: 1 }, hold_s: SE_PRESS_S }];
    default: throw new Error(`unknown button ${name}`);
  }
}
