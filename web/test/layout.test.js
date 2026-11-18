import test from "node:test";
import assert from "node:assert/strict";
import { areaNames, fallbackLayout, normalizeLayout, pickPreset, stacks, subscriptionTypes } from "../app/layout.js";
import { nextBackoff } from "../app/ws.js";

const IDS = ["a", "b", "c"];
const good = { default: "one", presets: { one: { areas: ["x y", "z z"], panels: [{ panel: "a", area: "x" }, { panel: "b", area: "y" }, { panel: "c", area: "x", collapsed: true }] } } };

test("valid layout passes unchanged in substance", () => {
  const { layout, problems } = normalizeLayout(good, IDS);
  assert.deepEqual(problems, []);
  assert.deepEqual(areaNames(layout.presets.one), ["x", "y", "z"]);
  const st = stacks(layout.presets.one);
  assert.deepEqual(st.get("x").map((e) => e.panel), ["a", "c"]);
  assert.equal(st.get("x")[1].collapsed, true);
});

test("unknown panels and areas are reported and repaired", () => {
  const raw = { presets: { p: { areas: ["x"], panels: [{ panel: "a", area: "nope" }, { panel: "zzz", area: "x" }] } } };
  const { layout, problems } = normalizeLayout(raw, IDS);
  assert.equal(problems.length, 2);
  assert.equal(layout.presets.p.panels.length, 1);
  assert.equal(layout.presets.p.panels[0].area, "x");
});

test("empty or broken layout falls back to one column with every panel", () => {
  for (const raw of [{}, null, { presets: { bad: { areas: [], panels: [] } } }]) {
    const { layout } = normalizeLayout(raw, IDS);
    assert.deepEqual(layout, fallbackLayout(IDS));
  }
});

test("preset choice: remembered, phone on narrow screens, default", () => {
  const layout = { default: "d", presets: { d: {}, phone: {}, bench: {} } };
  assert.equal(pickPreset(layout, "bench", 1500), "bench");
  assert.equal(pickPreset(layout, "gone", 1500), "d");
  assert.equal(pickPreset(layout, null, 390), "phone");
  assert.equal(pickPreset({ default: "d", presets: { d: {} } }, null, 390), "d");
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
