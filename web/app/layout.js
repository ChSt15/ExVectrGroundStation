// Layout handling (design 5.2): presets from layout.json, validation with a safe fallback. Pure functions.
//
// layout.json: { "default": "default",
//   "presets": { "<name>": { "label", "columns": "<grid-template-columns>", "areas": ["a b", "c c"],
//                            "panels": [ { "panel": "<id>", "area": "<area name>", "collapsed": false } ] } } }
// Panels that share an area are stacked in list order inside that area.

const AREA_RE = /^[A-Za-z][A-Za-z0-9_-]*$/;

/** Area names used by a preset's `areas` rows ("." cells excluded). */
export function areaNames(preset) {
  const names = new Set();
  for (const row of preset.areas || []) for (const n of String(row).split(/\s+/)) if (n && n !== ".") names.add(n);
  return [...names];
}

/** One column fallback with every registered panel, used when layout.json is missing or unusable. */
export function fallbackLayout(panelIds) {
  return {
    default: "default",
    presets: {
      default: {
        label: "Default",
        columns: "minmax(0, 1fr)",
        areas: ["main"],
        panels: panelIds.map((panel) => ({ panel, area: "main", collapsed: false })),
      },
    },
  };
}

/**
 * Validate a raw layout. Unknown panel ids and bad area names are dropped (reported in `problems`);
 * a preset without any valid panel is dropped; if nothing is left the fallback layout is returned.
 */
export function normalizeLayout(raw, panelIds) {
  const problems = [];
  const presets = {};
  const src = raw && typeof raw === "object" && raw.presets && typeof raw.presets === "object" ? raw.presets : {};
  for (const [name, p] of Object.entries(src)) {
    if (!p || typeof p !== "object" || !Array.isArray(p.areas) || !Array.isArray(p.panels)) {
      problems.push(`preset ${name}: needs areas and panels`);
      continue;
    }
    const areas = p.areas.map(String);
    const names = areaNames({ areas });
    if (!names.length || names.some((n) => !AREA_RE.test(n))) {
      problems.push(`preset ${name}: bad area names`);
      continue;
    }
    const panels = [];
    for (const e of p.panels) {
      if (!e || !panelIds.includes(e.panel)) {
        problems.push(`preset ${name}: unknown panel ${e && e.panel}`);
        continue;
      }
      const area = names.includes(e.area) ? e.area : names[0];
      if (area !== e.area) problems.push(`preset ${name}: panel ${e.panel} has unknown area ${e.area}, using ${area}`);
      panels.push({ panel: e.panel, area, collapsed: !!e.collapsed });
    }
    if (!panels.length) {
      problems.push(`preset ${name}: no valid panels`);
      continue;
    }
    presets[name] = { label: p.label || name, columns: p.columns || `repeat(${areas[0].split(/\s+/).length}, minmax(0, 1fr))`, areas, panels };
  }
  if (!Object.keys(presets).length) return { layout: fallbackLayout(panelIds), problems: [...problems, "using fallback layout"] };
  const def = presets[raw.default] ? raw.default : Object.keys(presets)[0];
  return { layout: { default: def, presets }, problems };
}

/** Preset to start with: remembered one if still valid, else phone preset on narrow screens, else the default. */
export function pickPreset(layout, remembered, width) {
  if (remembered && layout.presets[remembered]) return remembered;
  if (width <= 600 && layout.presets.phone) return "phone";
  return layout.default;
}

/** Panels grouped by area, in list order. */
export function stacks(preset) {
  const out = new Map();
  for (const n of areaNames(preset)) out.set(n, []);
  for (const e of preset.panels) out.get(e.area).push(e);
  return out;
}

/** Union of the subscription types needed by a set of panels in one mode; "*" wins. */
export function subscriptionTypes(panels, mode) {
  const types = new Set();
  for (const p of panels) {
    if ((p.mode || "latest") !== mode) continue;
    for (const t of p.consumes || []) types.add(t);
  }
  if (types.has("*")) return "*";
  return [...types].sort();
}
