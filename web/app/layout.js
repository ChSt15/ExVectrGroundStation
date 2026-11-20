// Layout handling (design 5.2, redesign 2026-11-20): views by usage, groups left to right, one Operations dock.
// Pure functions, unit tested in web/test/layout.test.js.
//
// layout.json (docs/gui.md "Layout file"):
// { "default": "bench",
//   "dock": { "title": "Operations", "tabs": [ { "panel": "radio", "label": "Radio" }, ... ] },
//   "views": { "<id>": { "label": "Bench", "hint": "...", "columns": "<grid-template-columns>",
//                        "dock": "open" | "rail",
//                        "groups": [ { "id": "vehicle", "title": "Vehicle",
//                                      "panels": [ { "panel": "<id>", "collapsed": false, "options": {} } ] } ] } } }
//
// Groups are columns, left to right in list order. Dock panels are mounted once and survive a view change; a panel
// is either in the dock or in the groups, never both.

const ID_RE = /^[a-z][a-z0-9_-]*$/;
export const DOCK_MODES = ["open", "rail"];

function cleanEntry(e, panelIds, where, problems) {
  if (!e || typeof e !== "object" || !panelIds.includes(e.panel)) {
    problems.push(`${where}: unknown panel ${e && e.panel}`);
    return null;
  }
  const options = e.options && typeof e.options === "object" && !Array.isArray(e.options) ? { ...e.options } : {};
  return { panel: e.panel, collapsed: !!e.collapsed, options };
}

/** One view, one group, every registered panel; used when layout.json is missing or unusable. */
export function fallbackLayout(panelIds) {
  return {
    default: "bench",
    dock: { title: "Operations", tabs: [] },
    views: {
      bench: {
        label: "Bench",
        hint: "fallback layout",
        columns: "minmax(0, 1fr)",
        dock: "rail",
        groups: [{ id: "all", title: "All panels", panels: panelIds.map((panel) => ({ panel, collapsed: false, options: {} })) }],
      },
    },
  };
}

/**
 * Validate a raw layout. Unknown panels, a panel listed twice and dock panels inside groups are dropped (reported in
 * `problems`); groups without panels and views without groups are dropped; with no view left the fallback is used.
 */
export function normalizeLayout(raw, panelIds) {
  const problems = [];
  const src = raw && typeof raw === "object" ? raw : {};
  const dockTabs = [];
  const dockIds = new Set();
  const rawTabs = src.dock && Array.isArray(src.dock.tabs) ? src.dock.tabs : [];
  for (const t of rawTabs) {
    const e = cleanEntry(t, panelIds, "dock", problems);
    if (!e) continue;
    if (dockIds.has(e.panel)) {
      problems.push(`dock: panel ${e.panel} listed twice`);
      continue;
    }
    dockIds.add(e.panel);
    dockTabs.push({ ...e, label: typeof t.label === "string" && t.label ? t.label : e.panel });
  }
  const views = {};
  const rawViews = src.views && typeof src.views === "object" ? src.views : {};
  for (const [id, v] of Object.entries(rawViews)) {
    if (!ID_RE.test(id) || !v || typeof v !== "object" || !Array.isArray(v.groups)) {
      problems.push(`view ${id}: needs an id [a-z0-9_-] and groups`);
      continue;
    }
    const used = new Set();
    const groups = [];
    for (const [gi, g] of v.groups.entries()) {
      if (!g || typeof g !== "object" || !Array.isArray(g.panels)) {
        problems.push(`view ${id}: group ${gi} needs panels`);
        continue;
      }
      const panels = [];
      for (const p of g.panels) {
        const e = cleanEntry(p, panelIds, `view ${id}`, problems);
        if (!e) continue;
        if (dockIds.has(e.panel)) {
          problems.push(`view ${id}: panel ${e.panel} is in the dock`);
          continue;
        }
        if (used.has(e.panel)) {
          problems.push(`view ${id}: panel ${e.panel} listed twice`);
          continue;
        }
        used.add(e.panel);
        panels.push(e);
      }
      if (!panels.length) {
        problems.push(`view ${id}: group ${g.id || gi} has no valid panels`);
        continue;
      }
      const gid = typeof g.id === "string" && ID_RE.test(g.id) ? g.id : `g${gi}`;
      groups.push({ id: gid, title: typeof g.title === "string" ? g.title : "", panels });
    }
    if (!groups.length) {
      problems.push(`view ${id}: no valid groups`);
      continue;
    }
    const dock = DOCK_MODES.includes(v.dock) ? v.dock : "open";
    views[id] = {
      label: typeof v.label === "string" && v.label ? v.label : id,
      hint: typeof v.hint === "string" ? v.hint : "",
      columns: typeof v.columns === "string" && v.columns ? v.columns : `repeat(${groups.length}, minmax(0, 1fr))`,
      dock,
      groups,
    };
  }
  if (!Object.keys(views).length) {
    const fb = fallbackLayout(panelIds.filter((p) => !dockIds.has(p)));
    fb.dock.tabs = dockTabs;
    return { layout: fb, problems: [...problems, "using fallback layout"] };
  }
  const def = views[src.default] ? src.default : Object.keys(views)[0];
  const title = src.dock && typeof src.dock.title === "string" ? src.dock.title : "Operations";
  return { layout: { default: def, dock: { title, tabs: dockTabs }, views }, problems };
}

/** View to start with: the remembered one if it still exists, else the default. */
export function pickView(layout, remembered) {
  return remembered && layout.views[remembered] ? remembered : layout.default;
}

/** Dock state of a view: a remembered choice of the user wins over the view's own default. */
export function dockState(view, remembered) {
  return DOCK_MODES.includes(remembered) ? remembered : view.dock;
}

/** Dock tab to show: the remembered one if it is still a tab, else the first. */
export function pickTab(layout, remembered) {
  const tabs = layout.dock.tabs;
  if (remembered && tabs.some((t) => t.panel === remembered)) return remembered;
  return tabs.length ? tabs[0].panel : null;
}

/** Entries of all panels a view mounts in its groups, in order. */
export function viewPanels(view) {
  return view.groups.flatMap((g) => g.panels);
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
