import test from "node:test";
import assert from "node:assert/strict";
import { dockState, fallbackLayout, normalizeLayout, pickTab, pickView, subscriptionTypes, viewPanels } from "../app/layout.js";
import { nextBackoff } from "../app/ws.js";

async function loadJson(url) {
  if (typeof process !== "undefined" && process.versions && process.versions.node) {
    const fs = await import("node:fs");
    return JSON.parse(fs.readFileSync(url, "utf8"));
  }
  return (await fetch(url)).json();
}

const IDS = ["a", "b", "c", "radio", "console"];
const good = {
  default: "one",
  dock: { tabs: [{ panel: "radio", label: "Radio" }, { panel: "console" }] },
  views: {
    one: { label: "One", columns: "1fr 2fr", groups: [{ id: "vehicle", title: "Vehicle", panels: [{ panel: "a" }, { panel: "b", collapsed: true, options: { compact: true } }] }, { id: "mission", title: "Mission", panels: [{ panel: "c" }] }] },
    two: { dock: "rail", groups: [{ id: "x", panels: [{ panel: "c" }] }] },
  },
};

test("valid layout passes unchanged in substance", () => {
  const { layout, problems } = normalizeLayout(good, IDS);
  assert.deepEqual(problems, []);
  assert.equal(layout.default, "one");
  assert.deepEqual(layout.dock.tabs.map((t) => [t.panel, t.label]), [["radio", "Radio"], ["console", "console"]]);
  const one = layout.views.one;
  assert.deepEqual(one.groups.map((g) => g.title), ["Vehicle", "Mission"]);
  assert.deepEqual(viewPanels(one).map((e) => e.panel), ["a", "b", "c"]);
  assert.equal(one.groups[0].panels[1].collapsed, true);
  assert.deepEqual(one.groups[0].panels[1].options, { compact: true });
  assert.equal(one.dock, "open");
  assert.equal(layout.views.two.dock, "rail");
  assert.equal(layout.views.two.columns, "repeat(1, minmax(0, 1fr))");
  assert.equal(layout.views.two.label, "two");
});

test("unknown panels, duplicates and dock panels inside groups are reported and dropped", () => {
  const raw = {
    dock: { tabs: [{ panel: "radio" }, { panel: "radio" }, { panel: "zzz" }] },
    views: { v: { groups: [{ id: "g", panels: [{ panel: "a" }, { panel: "a" }, { panel: "radio" }, { panel: "nope" }] }, { id: "empty", panels: [] }] } },
  };
  const { layout, problems } = normalizeLayout(raw, IDS);
  assert.equal(problems.length, 6);
  assert.deepEqual(layout.dock.tabs.map((t) => t.panel), ["radio"]);
  assert.deepEqual(viewPanels(layout.views.v).map((e) => e.panel), ["a"]);
  assert.equal(layout.views.v.groups.length, 1);
});

test("empty or broken layout falls back to one view with every non-dock panel", () => {
  for (const raw of [{}, null, { views: { bad: { groups: [] } } }, { views: { "Bad Id": { groups: [{ panels: [{ panel: "a" }] }] } } }]) {
    const { layout, problems } = normalizeLayout(raw, IDS);
    assert.deepEqual(layout, fallbackLayout(IDS));
    assert.ok(problems.includes("using fallback layout"));
  }
  const { layout } = normalizeLayout({ dock: { tabs: [{ panel: "radio" }] } }, IDS);
  assert.deepEqual(viewPanels(layout.views.bench).map((e) => e.panel), ["a", "b", "c", "console"]);
  assert.deepEqual(layout.dock.tabs.map((t) => t.panel), ["radio"]);
});

test("view, dock state and tab choice: remembered if still valid, else the defaults", () => {
  const { layout } = normalizeLayout(good, IDS);
  assert.equal(pickView(layout, "two"), "two");
  assert.equal(pickView(layout, "phone"), "one"); // removed presets fall back to the default view
  assert.equal(pickView(layout, null), "one");
  assert.equal(dockState(layout.views.one, null), "open");
  assert.equal(dockState(layout.views.one, "rail"), "rail");
  assert.equal(dockState(layout.views.two, "bogus"), "rail");
  assert.equal(pickTab(layout, "console"), "console");
  assert.equal(pickTab(layout, "gone"), "radio");
});

test("the shipped layout has the bench, flight and tuning views and passes without problems", async () => {
  const raw = await loadJson(new URL("../../config/layout.default.json", import.meta.url));
  const ids = [...new Set([...raw.dock.tabs.map((t) => t.panel), ...Object.values(raw.views).flatMap((v) => v.groups.flatMap((g) => g.panels.map((p) => p.panel))), "transport"])];
  const { layout, problems } = normalizeLayout(raw, ids);
  assert.deepEqual(problems, []);
  assert.deepEqual(Object.keys(layout.views), ["bench", "flight", "tuning"]);
  assert.equal(layout.views.tuning.dock, "rail");
  assert.equal(layout.default, "bench");
  for (const v of ["bench", "flight"]) assert.deepEqual(layout.views[v].groups.map((g) => g.title), ["Vehicle", "Guidance", "Mission"]);
  assert.equal(layout.views.bench.dock, "open");
  assert.equal(layout.views.flight.dock, "rail");
  for (const t of ["radio", "requests", "telecommands", "link", "recording", "console", "inspector", "rawsensors"]) assert.ok(layout.dock.tabs.some((x) => x.panel === t), t);
});

test("subscription types are derived from the mounted panels per mode", () => {
  const panels = [{ consumes: ["B", "A"] }, { consumes: ["ConsoleText"], mode: "all" }, { consumes: ["A"] }];
  assert.deepEqual(subscriptionTypes(panels, "latest"), ["A", "B"]);
  assert.deepEqual(subscriptionTypes(panels, "all"), ["ConsoleText"]);
  assert.equal(subscriptionTypes([...panels, { consumes: ["*"] }], "latest"), "*");
  assert.deepEqual(subscriptionTypes([{ consumes: [] }], "all"), []);
});

test("reconnect backoff doubles from 0.5 s up to 5 s", () => {
  let b = 0;
  const seq = [];
  for (let i = 0; i < 6; i++) seq.push((b = nextBackoff(b)));
  assert.deepEqual(seq, [0.5, 1, 2, 4, 5, 5]);
});
