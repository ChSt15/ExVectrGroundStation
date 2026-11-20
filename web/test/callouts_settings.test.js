import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULTS, beepGain, cleanLevels, effectiveVolume, mergeSettings, styleOf } from "../app/callouts/settings.js";
import { FAILURE_TEXT } from "../app/callouts/engine.js";
import { FAILURE_BITS } from "../app/status/status_logic.js";

test("volume scaling: style volume x master, clamped to 0..1", () => {
  assert.equal(effectiveVolume(0.9, 0.8), 0.9 * 0.8);
  assert.equal(effectiveVolume(1.0, 1.0), 1);
  assert.equal(effectiveVolume(1.0, 0), 0);
  assert.equal(effectiveVolume(1.0, 2), 1); // never above 1.0
  assert.equal(effectiveVolume(3, 1), 1);
  assert.equal(effectiveVolume(-1, 1), 0);
  assert.equal(effectiveVolume(NaN, 0.5), 0.5);
  assert.equal(beepGain(0), 0);
  assert.ok(beepGain(0.8) > beepGain(0.4) && beepGain(1) <= 1);
  assert.equal(beepGain(5), beepGain(1));
});

test("defaults: master volume 80 %, deviation disabled, styles as specified", () => {
  assert.equal(DEFAULTS.volume, 0.8);
  assert.equal(DEFAULTS.categories.deviation, false);
  assert.equal(DEFAULTS.speakInReplay, false);
  assert.deepEqual(DEFAULTS.styles.failure, { rate: 1.3, pitch: 0.7, volume: 1.0 });
  assert.deepEqual(DEFAULTS.altitude.levels, [25, 50, 100, 150, 200]);
  assert.equal(styleOf("ok"), "normal");
  assert.equal(styleOf("info"), "normal");
  assert.equal(styleOf("warning"), "warning");
  assert.equal(styleOf("failure"), "failure");
});

test("merge takes known keys of the right type only, clamps, and does not modify the base", () => {
  const before = JSON.stringify(DEFAULTS);
  const m = mergeSettings(DEFAULTS, {
    volume: 7, master: "yes", bogus: 1, categories: { altitude: false, nope: true, failure: "x" },
    altitude: { levels: [200, "50", -3, 50, "x", 10], hysteresis_m: 2 }, styles: { failure: { rate: 9, pitch: 0.5 } },
    deviation: { limit_m: 8 }, voice: "abc",
  });
  assert.equal(JSON.stringify(DEFAULTS), before);
  assert.equal(m.volume, 1);
  assert.equal(m.master, true);
  assert.equal(m.bogus, undefined);
  assert.equal(m.categories.altitude, false);
  assert.equal(m.categories.failure, true);
  assert.equal(m.categories.nope, undefined);
  assert.deepEqual(m.altitude.levels, [10, 50, 200]);
  assert.equal(m.altitude.hysteresis_m, 2);
  assert.deepEqual(m.styles.failure, { rate: 2, pitch: 0.5, volume: 1.0 });
  assert.equal(m.deviation.limit_m, 8);
  assert.equal(m.voice, "abc");
  // file defaults first, then user values on top
  const file = mergeSettings(DEFAULTS, { volume: 0.5, categories: { deviation: true } });
  const user = mergeSettings(file, { volume: 0.3 });
  assert.equal(user.volume, 0.3);
  assert.equal(user.categories.deviation, true);
  assert.deepEqual(mergeSettings(DEFAULTS, "junk"), DEFAULTS);
  assert.deepEqual(cleanLevels([], [1]), [1]);
});

test("spoken failure names cover every failure bit of the status logic", () => {
  assert.equal(FAILURE_TEXT.length, FAILURE_BITS.length);
});
