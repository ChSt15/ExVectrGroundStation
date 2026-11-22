// Operations dock tab "RC applied" (T9b-1). Display only: what the vehicle decoded from RC_STATE (RC_APPLIED, USB only).
import { registerPanel } from "./registry.js";
import { Bindings, h, section } from "../ui.js";
import { SWITCHES, SAFE_SEEN, REJECT_LABELS, ageText, safeSeenList, switchText, switchTone } from "../rcapplied/rcapplied_logic.js";

const T = "RcApplied";

registerPanel({
  id: "rcapplied",
  title: "RC applied",
  consumes: [T],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const sw = section(el, "Switches as decoded by the vehicle");
    for (const s of SWITCHES) {
      const val = b.cell({ type: T, kind: "text", derive: (e) => (e.f[s.id] === undefined ? undefined : (e.f[s.id] === 255 ? "invalid" : `${e.f[s.id]}: ${switchText(s.id, e.f[s.id])}`)) }, { showSrc: false, tone: (d) => (d.text === "invalid" ? switchTone(255) : "") });
      sw.append(h("div", { class: "row", "data-key": `rca-${s.id}` }, h("span", { class: "lbl", text: `${s.label} ${s.meaning}` }), val));
    }
    const ss = section(el, "Safe value seen");
    for (let i = 0; i < SAFE_SEEN.length; i++) {
      b.row(ss, SAFE_SEEN[i], { type: T, kind: "text", derive: (e) => (e.f.safe_seen === undefined ? undefined : safeSeenList(e.f.safe_seen)[i].seen ? "seen" : "not seen") }, { key: `rca-seen-${i}`, showSrc: false });
    }
    const ag = section(el, "Source age (last accepted RC_STATE)");
    b.row(ag, "Radio", { type: T, kind: "text", derive: (e) => (e.f.radio_age === undefined ? undefined : ageText(e.f.radio_age)) }, { key: "rca-radio-age", showSrc: false });
    b.row(ag, "Bench (USB)", { type: T, kind: "text", derive: (e) => (e.f.bench_age === undefined ? undefined : ageText(e.f.bench_age)) }, { key: "rca-bench-age", showSrc: false });
    const rj = section(el, "Rejected RC_STATE frames (wrapping counters)");
    REJECT_LABELS.forEach((label, i) => {
      b.row(rj, label, { type: T, kind: "num", digits: 0, derive: (e) => (Array.isArray(e.f.rejects) ? e.f.rejects[i] : undefined) }, { key: `rca-rej-${i}`, showSrc: false });
    });
    return { onTick: () => b.update() };
  },
});
