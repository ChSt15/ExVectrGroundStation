import test from "node:test";
import assert from "node:assert/strict";
import { ageText, rejectList, safeSeenList, switchText, switchTone } from "../app/rcapplied/rcapplied_logic.js";
import { describeVehicleStatus } from "../app/status/status_logic.js";

test("switch meanings and invalid", () => {
  assert.equal(switchText("sa", 1), "go");
  assert.equal(switchText("sa", 0), "abort (not go)");
  assert.equal(switchText("sb", 2), "type 2");
  assert.equal(switchText("sb", 255), "invalid");
  assert.equal(switchText("sc", 1), "real");
  assert.equal(switchText("sc", 2), "reserved");
  assert.equal(switchText("sd", 1), "enabled");
  assert.equal(switchText("se", 1), "pressed");
  assert.equal(switchText("sd", undefined), "n/s");
  assert.equal(switchTone(255), "warn");
  assert.equal(switchTone(1), "");
});

test("safe-seen bits, ages, rejects", () => {
  assert.deepEqual(safeSeenList(0b0101).map((x) => x.seen), [true, false, true, false]);
  assert.equal(ageText(0.04), "0.04 s");
  assert.equal(ageText(null), "never");
  assert.equal(ageText(NaN), "never");
  assert.deepEqual(rejectList([1, 2, 3, 4]).map((x) => x.count), [1, 2, 3, 4]);
  assert.equal(rejectList([1, 2, 3, 4])[3].label, "SOURCE_OK clear");
  assert.equal(rejectList(undefined)[0].count, null);
});

test("status bar control source shows latched and fresh sources", () => {
  const e = (f) => ({ type: "VehicleStatus", src: "usb", t: 100, iv: 0.1, stale_after: 1, f });
  const byKey = (d) => Object.fromEntries(d.items.map((i) => [i.key, i]));
  const d = byKey(describeVehicleStatus(e({ ctrl_source: "RADIO", ctrl_flags: (1 << 7) | (1 << 9), rc_age: 0.1 }), 100.2));
  assert.equal(d.ctrl_source.text, "RADIO (latched)");
  assert.match(d.ctrl_source.title, /radio/);
  const n = byKey(describeVehicleStatus(e({ ctrl_source: "NONE", ctrl_flags: 0 }), 100.2));
  assert.equal(n.ctrl_source.text, "NONE");
});
