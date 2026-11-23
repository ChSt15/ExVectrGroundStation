// Operations dock tab "Command" (replaces "RC applied"). Two sources, labelled apart:
//  - what the ground station's command engine SENDS (the `rc` push, `command`: input, Pocket, pickup hints, session)
//  - what the VEHICLE applied (CMD_APPLIED, USB only): the CMD_STATE fields as decoded, safe-seen bits, ages, rejects.
// Display only. Nothing here sends anything, and the sent state is never shown as the applied state.
import { registerPanel } from "./registry.js";
import { Bindings, h, section } from "../ui.js";
import {
  SWITCHES, SAFE_SEEN, REJECT_LABELS, ageText, engineRows, flagsText, safeSeenList, session32Text, switchText, switchTone,
} from "../command/command_logic.js";

const T = "CmdApplied";

registerPanel({
  id: "command",
  title: "Command",
  consumes: [T],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const sent = section(el, "Sent by the ground station (command engine)");
    const sentBox = h("div", { class: "cp-box", "data-testid": "command-engine" });
    sent.append(sentBox);
    const sw = section(el, "Applied by the vehicle (CMD_APPLIED, last accepted CMD_STATE)");
    for (const s of SWITCHES) {
      const val = b.cell(
        { type: T, kind: "text", derive: (e) => (e.f[s.id] === undefined ? undefined : `${e.f[s.id]}: ${switchText(s.id, e.f[s.id])}`) },
        { showSrc: false, tone: (d) => switchTone(s.id, Number((d.text || "").split(":")[0])) },
      );
      sw.append(h("div", { class: "row", "data-key": `cmd-${s.id}` }, h("span", { class: "lbl", text: `${s.label} (${s.meaning})` }), val));
    }
    b.row(sw, "Input flags", { type: T, kind: "text", derive: (e) => (e.f.flags === undefined ? undefined : flagsText(e.f.flags)) }, { key: "cmd-flags", showSrc: false });
    b.row(sw, "Latched session", { type: T, kind: "text", derive: (e) => (e.f.session === undefined ? undefined : session32Text(e.f.session)) }, { key: "cmd-session", showSrc: false });
    const ss = section(el, "Safe value seen");
    for (let i = 0; i < SAFE_SEEN.length; i++) {
      b.row(ss, SAFE_SEEN[i], { type: T, kind: "text", derive: (e) => (e.f.safe_seen === undefined ? undefined : safeSeenList(e.f.safe_seen)[i].seen ? "seen" : "not seen") }, { key: `cmd-seen-${i}`, showSrc: false });
    }
    const ag = section(el, "Age of the last accepted CMD_STATE");
    b.row(ag, "Either link", { type: T, kind: "text", derive: (e) => (e.f.age === undefined ? undefined : ageText(e.f.age)) }, { key: "cmd-age", showSrc: false });
    b.row(ag, "USB", { type: T, kind: "text", derive: (e) => (e.f.usb_age === undefined ? undefined : ageText(e.f.usb_age)) }, { key: "cmd-usb-age", showSrc: false });
    b.row(ag, "UART (radio)", { type: T, kind: "text", derive: (e) => (e.f.uart_age === undefined ? undefined : ageText(e.f.uart_age)) }, { key: "cmd-uart-age", showSrc: false });
    const rj = section(el, "Rejected CMD_STATE frames (wrapping counters)");
    REJECT_LABELS.forEach((label, i) => {
      b.row(rj, label, { type: T, kind: "num", digits: 0, derive: (e) => (Array.isArray(e.f.rejects) ? e.f.rejects[i] : undefined) }, { key: `cmd-rej-${i}`, showSrc: false });
    });
    function renderEngine() {
      const rc = ctx.rc?.();
      const rows = engineRows(rc && rc.command);
      sentBox.replaceChildren(
        ...(rows.length
          ? rows.map((r) => h("div", { class: "cp-row", "data-key": r.key, "data-tone": r.tone }, h("span", { class: "cp-l", text: r.label }), h("span", { class: "cp-v", text: r.text })))
          : [h("div", { class: "cp-row", "data-tone": "dim", text: "No command engine report (no core connection, or replay)." })]),
      );
    }
    renderEngine();
    return { onTick: () => { b.update(); renderEngine(); } };
  },
});
