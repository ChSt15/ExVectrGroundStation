// Small DOM helpers and the value-binding helper used by the data panels.
// Data (including vehicle console text) is only ever put into the DOM with textContent.
import { describe, fmtAge, worstState } from "./fmt.js";

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === undefined || c === null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

/** Fill a value cell from a description made by fmt.describe(). */
export function renderValue(cell, d, showSrc = true) {
  cell.v.textContent = d.text;
  cell.unit.textContent = d.unit ? ` ${d.unit}` : "";
  cell.src.textContent = showSrc && d.src ? d.src : "";
  cell.age.textContent = d.state === "stale" ? fmtAge(d.age) : "";
  cell.el.dataset.state = d.state;
  cell.el.title = d.title || "";
  if (cell.tone) cell.el.dataset.tone = cell.tone(d) || "";
}

export function makeCell(extraClass = "") {
  const v = h("span", { class: "v" });
  const unit = h("span", { class: "unit" });
  const src = h("span", { class: "src" });
  const age = h("span", { class: "age" });
  const el = h("span", { class: `val ${extraClass}`.trim() }, v, unit, src, age);
  return { el, v, unit, src, age };
}

/**
 * Declarative bindings: panel.val(parent, label, spec, cellOpts) creates a labelled row whose value is
 * refreshed by update(). Staleness, units and source tags come from fmt.describe via ctx.
 */
export class Bindings {
  constructor(ctx) {
    this.ctx = ctx;
    this.items = [];
  }

  /** Bare cell without a label (for tables). tone(d) may return a CSS tone name. */
  cell(spec, opts = {}) {
    const c = makeCell(opts.class);
    c.tone = opts.tone;
    this.items.push({ cell: c, spec, showSrc: opts.showSrc !== false });
    return c.el;
  }

  /** Stat tile: small caps label over a large value with its unit (and the age when stale). */
  tile(parent, label, spec, opts = {}) {
    const cell = this.cell(spec, { showSrc: false, ...opts, class: `big ${opts.class || ""}`.trim() });
    const t = h("div", { class: `tile ${opts.tileClass || ""}`.trim(), "data-key": opts.key || null }, h("div", { class: "tile-l", text: label }), cell);
    parent.append(t);
    return cell;
  }

  /**
   * Table of vectors: one row per quantity, one column per axis, the unit in the last column. `rows`:
   * [{ label, type | types, field | vec(entry or entries) -> array, digits, scale, tone }]. Cells show no age
   * (the panel badge does); stale rows are dimmed.
   */
  vecTable(parent, cols, rows) {
    const table = h("table", { class: "vt" }, h("thead", {}, h("tr", {}, h("th", { text: "" }), ...cols.map((c) => h("th", { text: c })), h("th", { class: "u", text: "" }))));
    const body = h("tbody");
    for (const r of rows) {
      const pick = r.vec || ((e) => (e && e.f ? e.f[r.field] : undefined));
      const tr = h("tr", { "data-row": r.key || null }, h("th", { text: r.label }));
      cols.forEach((_, i) => {
        const spec = {
          type: r.type,
          types: r.types,
          kind: "num",
          digits: r.digits ?? 2,
          scale: r.scale ?? 1,
          derive: (e) => {
            const v = pick(e);
            if (v === undefined) return undefined;
            if (!Array.isArray(v)) return null;
            return v[i] === undefined ? null : v[i];
          },
        };
        tr.append(h("td", {}, this.cell(spec, { showSrc: false, class: "noage", tone: r.tone })));
      });
      tr.append(h("td", { class: "u", text: r.unit || "" }));
      body.append(tr);
    }
    table.append(body);
    parent.append(table);
    return table;
  }

  /** Label + value row appended to `parent`. */
  row(parent, label, spec, opts = {}) {
    const cell = this.cell(spec, opts);
    const row = h("div", { class: "row", "data-key": opts.key || null }, h("span", { class: "lbl", text: label }), cell);
    parent.append(row);
    return cell;
  }

  describeSpec(spec) {
    const { store, view } = this.ctx;
    const now = store.now(view());
    return describe(
      spec,
      (type, src) => store.get(view(), type, src),
      now === null ? Infinity : now,
      (t) => store.schema.get(t),
    );
  }

  update() {
    for (const it of this.items) renderValue(it.cell, this.describeSpec(it.spec), it.showSrc);
  }

  /** Worst freshness over all bound values and the largest age among stale ones, for the panel badge. */
  freshness() {
    const ds = this.items.map((it) => this.describeSpec(it.spec));
    const state = worstState(ds.map((d) => d.state).filter((s) => s !== "not supplied"));
    const ages = ds.filter((d) => d.state === "stale").map((d) => d.age);
    return { state, age: ages.length ? Math.max(...ages) : null };
  }
}

/** Row of stat tiles. */
export function tiles(parent, cls = "") {
  const t = h("div", { class: `tiles ${cls}`.trim() });
  parent.append(t);
  return t;
}

/** Panel section with a small heading. */
export function section(parent, title) {
  const s = h("div", { class: "sect" }, title ? h("div", { class: "sect-title", text: title }) : null);
  parent.append(s);
  return s;
}
