import { registerPanel } from "./registry.js";
import { h } from "../ui.js";
import { fmtAge, fmtNum, staleAfter } from "../fmt.js";

function fieldText(entry, fd) {
  const v = entry.f ? entry.f[fd.name] : undefined;
  const nf = entry.nf && entry.nf[fd.name];
  if (v === undefined && nf === undefined) return "not supplied";
  const one = (x, i) => {
    if (x === null) return (Array.isArray(nf) ? nf[i] : nf) || "null";
    if (typeof x === "number") return fd.kind === "float" || fd.kind === "float_array" || fd.kind === "time_ns" ? fmtNum(x, 5) : String(x);
    return String(x);
  };
  const text = Array.isArray(v) ? v.map(one).join("  ") : one(v, 0);
  return fd.unit && fd.unit !== "-" ? `${text} ${fd.unit}` : text;
}

registerPanel({
  id: "inspector",
  title: "Inspector",
  consumes: ["*"],
  create(el, ctx) {
    let selected = null; // "type|src"
    const list = h("div", { class: "inspect-list" });
    const detail = h("div", { class: "inspect-detail" });
    el.append(list, detail);

    function render() {
      const view = ctx.view();
      const now = ctx.store.now(view);
      const entries = ctx.store
        .all(view)
        .sort((a, b) => a.type.localeCompare(b.type) || a.src.localeCompare(b.src));
      const table = h("table", { class: "grid inspect" }, h("tr", {}, ...["type", "src", "age", ""].map((t) => h("th", { text: t }))));
      for (const e of entries) {
        const key = `${e.type}|${e.src}`;
        const age = now === null ? null : Math.max(0, now - e.t);
        const stale = age !== null && age > staleAfter(e, ctx.store.schema.get(e.type));
        table.append(
          h(
            "tr",
            { class: key === selected ? "sel" : "", onclick: () => ((selected = key), render()) },
            h("td", { text: e.type }),
            h("td", { text: e.src }),
            h("td", { text: age === null ? "" : fmtAge(age), "data-state": stale ? "stale" : "fresh" }),
            h("td", { text: stale ? "stale" : "" }),
          ),
        );
      }
      list.replaceChildren(entries.length ? table : h("div", { class: "hint", text: "No packets received in this view." }));
      const sel = entries.find((e) => `${e.type}|${e.src}` === selected);
      if (!sel) {
        detail.replaceChildren(h("div", { class: "hint", text: "Click a row to see all fields." }));
        return;
      }
      const def = ctx.store.schema.get(sel.type);
      const t = h("table", { class: "grid" });
      const names = def ? def.fields : Object.keys(sel.f || {}).map((name) => ({ name, kind: "?" }));
      for (const fd of names) {
        t.append(h("tr", {}, h("th", { text: fd.name, title: fd.desc || "" }), h("td", { text: fieldText(sel, fd) })));
      }
      for (const [name, text] of Object.entries(sel.raw || {})) t.append(h("tr", {}, h("th", { text: name }), h("td", { text: `${text} (raw)` })));
      detail.replaceChildren(h("div", { class: "sect-title", text: `${sel.type} from ${sel.src}` }), t);
    }
    return { onTick: render, onViewChange: render };
  },
});
