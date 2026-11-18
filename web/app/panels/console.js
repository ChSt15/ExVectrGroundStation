import { registerPanel } from "./registry.js";
import { h } from "../ui.js";

const MAX_LINES = 500;

registerPanel({
  id: "console",
  title: "Console",
  consumes: ["ConsoleText"],
  mode: "all",
  create(el, ctx) {
    const lines = []; // {kind: "vehicle"|"gs", level, text, t}
    const show = { vehicle: true, gs: true };
    const out = h("div", { class: "console" });
    let paused = false;
    let dirty = false;
    const box = (kind, text) => {
      const cb = h("input", { type: "checkbox", checked: true, onchange: () => ((show[kind] = cb.checked), (dirty = true)) });
      return h("label", {}, cb, ` ${text}`);
    };
    const pause = h("input", { type: "checkbox", onchange: () => (paused = pause.checked) });
    const clear = h("button", { text: "Clear", onclick: () => ((lines.length = 0), (dirty = true)) });
    el.append(h("div", { class: "toolbar" }, box("vehicle", "vehicle"), box("gs", "GS events"), h("label", {}, pause, " pause"), clear), out);

    function add(l) {
      lines.push(l);
      if (lines.length > MAX_LINES) lines.shift();
      dirty = true;
    }
    function render() {
      if (!dirty || paused) return;
      dirty = false;
      const stick = out.scrollTop + out.clientHeight >= out.scrollHeight - 8;
      out.replaceChildren(
        ...lines
          .filter((l) => show[l.kind])
          .map((l) => h("div", { class: `ln ${l.kind} ${l.level || ""}` }, h("span", { class: "ts", text: l.ts }), h("span", { text: l.text }))),
      );
      if (stick) out.scrollTop = out.scrollHeight;
    }
    return {
      onPacket(pkt) {
        if (pkt.type !== "ConsoleText" || !pkt.f || pkt.f.text === undefined) return;
        add({ kind: "vehicle", text: pkt.f.text, ts: `${pkt.src} ` });
      },
      onEvent(ev) {
        add({ kind: "gs", level: ev.level, text: ev.text, ts: "GS " });
      },
      onReset(view) {
        if (view === "replay") {
          lines.length = 0;
          dirty = true;
        }
      },
      onTick: render,
      onViewChange: () => {
        lines.length = 0;
        dirty = true;
      },
    };
  },
});
