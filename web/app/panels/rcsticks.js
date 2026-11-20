// Compact RC panel (Flight view): the RC state the core reports (master/slave/idle, as the radio panel) and the two
// sticks. DISPLAY ONLY: no control here; the full radio is in the Operations dock.
import { registerPanel } from "./registry.js";
import { h } from "../ui.js";
import { describeRadio } from "../radio/radio_logic.js";
import { stickConfig, stickState } from "../radio/sticks.js";
import { createSticks } from "../radio/sticks_widget.js";

registerPanel({
  id: "rcsticks",
  title: "RC input",
  consumes: ["RcState"],
  create(el, ctx) {
    let cfg = null;
    const banner = h("div", { class: "rc-banner small", role: "status" });
    const sticks = createSticks(el, { compact: true });
    el.prepend(banner);
    const open = h("button", { class: "linkbtn", text: "Open radio", onclick: () => window.exgs?.dock?.open("radio") });
    el.append(h("div", { class: "toolbar end" }, open));
    async function load() {
      try {
        cfg = await ctx.api.request("config.get", { name: "radio" });
      } catch (e) {
        cfg = null;
      }
      render();
    }
    function render() {
      const r = ctx.rc();
      const view = ctx.view();
      const b = describeRadio({ apiState: ctx.api.state, view, rc: r, canControl: ctx.api.welcome && ctx.api.welcome.can_control, cfg });
      banner.textContent = b.text;
      banner.dataset.tone = b.tone;
      const sc = stickConfig(cfg);
      if (ctx.api.state !== "open") return sticks.update(null, { caption: "" });
      if (view === "replay") {
        const it = ctx.store.get("replay", "RcState");
        return sticks.update(it ? stickState(sc, it.f.ch_us) : null, { caption: it ? "recorded" : "no RcState in this log" });
      }
      if (!r) return sticks.update(null, { caption: "RC not available" });
      const now = ctx.store.now("live");
      const age = r.mode === "slave" && now != null && r.t != null ? Math.max(0, now - r.t) : null;
      sticks.update(stickState(sc, r.channels_us), {
        caption: r.mode === "slave" ? `Pocket sticks${age !== null ? ` · ${age.toFixed(1)} s ago` : ""}` : r.sending ? "sent (defaults, no stick sending)" : "not sending",
        stale: age !== null && age > 0.5,
      });
    }
    if (ctx.api.state === "open") load();
    return { onReady: load, onRc: render, onTick: render, onViewChange: render };
  },
});
