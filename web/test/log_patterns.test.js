import test from "node:test";
import assert from "node:assert/strict";
import { calibrationProblem, chipText, classify, parseLine } from "../app/callouts/log_patterns.js";
import { DEFAULTS, mergeSettings } from "../app/callouts/settings.js";

const W = (t, node = 1) => `[node ${node} WARNING] ${t}`;
const I = (t, node = 1) => `[node ${node} INFO] ${t}`;
const WHY = "gyro: ok, accel: parameters out of range, mag: ok";

// Real texts: format strings of flight-software/src/main.cpp and src/calibration/calibration_service.cpp, with arguments filled in.
// Live sample (usb source): "[node 1 INFO] Calibration: Accel: 2 poses captured (moving)"
test("parseLine splits node, level and text", () => {
  assert.deepEqual(parseLine("[node 1 INFO] Calibration: Accel: 2 poses captured (moving)"), { node: 1, level: "INFO", text: "Calibration: Accel: 2 poses captured (moving)" });
  assert.equal(parseLine("not a vehicle line"), null);
  assert.equal(parseLine("[fake] t=1.0s mode=Idle"), null);
});

test("calibrationProblem parses Calibration::describe() text for every status wording", () => {
  assert.deepEqual(calibrationProblem(WHY).bad, ["accel"]);
  assert.deepEqual(calibrationProblem("gyro: not calibrated since power-up, accel: default (never calibrated), mag: ok").bad, ["gyro", "accel"]);
  assert.deepEqual(calibrationProblem("gyro: ok, accel: ok, mag: still-window check failed").bad, ["mag"]);
  assert.deepEqual(calibrationProblem("gyro: ok, accel: ok, mag: ok").bad, []);
  assert.equal(calibrationProblem("something else"), null);
  assert.equal(chipText(calibrationProblem(WHY)), "Calibration invalid: accel");
});

test("the repeated calibration warning (main.cpp, every 10 s)", () => {
  const c = classify(W(`WARNING calibration invalid, Arm and MissionBegin are refused (repeats every 10 s): ${WHY}`));
  assert.equal(c.category, "calibration");
  assert.equal(c.severity, "warning");
  assert.equal(c.title, "Calibration invalid: accel");
  assert.equal(c.calib, "problem");
});

test("Arm and MissionBegin refusals carry the reason", () => {
  let c = classify(W(`Arm refused: calibration invalid (${WHY})`));
  assert.equal(c.category, "refused");
  assert.equal(c.title, `Arm refused: calibration invalid (${WHY})`);
  assert.equal(c.calib, "problem");
  c = classify(I("Arm refused: estimator zeroing active"));
  assert.equal(c.title, "Arm refused: estimator zeroing active");
  c = classify(W(`Mission begin refused: calibration invalid (${WHY})`));
  assert.equal(c.title, `MissionBegin refused: calibration invalid (${WHY})`);
  c = classify(W("Mission begin refused: countdown too short (-5 s), the gyro calibration needs the Initialisation phase"));
  assert.match(c.title, /^MissionBegin refused: countdown too short/);
  c = classify(W("Calibration apply refused: no passing candidate for Accel (state Idle, result NONE)"));
  assert.equal(c.category, "refused");
  assert.match(c.title, /^Calibration apply refused: no passing candidate/);
  c = classify(W("Calibration start refused: another calibration is running, stop or discard it first"));
  assert.equal(c.category, "refused");
  c = classify(I("Sim wind speed refused (not simulation)"));
  assert.equal(c.category, "refused"); // generic "refused" pattern on an INFO line
});

test("countdown abort is critical (failure severity, sticky)", () => {
  const c = classify(W(`MISSION ABORTED before liftoff: calibration invalid (${WHY}). Reset and stop.`));
  assert.equal(c.category, "abort");
  assert.equal(c.severity, "failure");
  assert.equal(c.sticky, true);
  assert.equal(c.title, "Countdown ABORTED: calibration invalid (accel)");
});

test("mission abort telecommand and zeroing messages", () => {
  const c = classify(I("Mission abort telecommand: ResetAndStop (alt 0.00 m, speed 0.00 m/s)"));
  assert.equal(c.category, "mission");
  assert.equal(c.title, "Mission abort: ResetAndStop (alt 0.00 m, speed 0.00 m/s)");
  assert.equal(classify(I("Estimator zeroing aborted by calibration start")).category, "mission");
});

test("calibration session events", () => {
  const t = (line) => classify(I(line))?.title;
  assert.equal(t("Calibration: Accel: pose 3 captured, move to the next one"), "Accel pose 3 captured");
  assert.equal(t("Calibration: Accel: all 12 poses stored, stop and fit"), "Accel: all 12 poses stored, stop and fit");
  assert.equal(t("Calibration: Accel fit PASS: 12 poses, residual rms 0.0100 max 0.0200 m/s^2, offset 0.1 0.1 0.1 m/s^2, scale 0.99..1.01, checks 0x000"), "Accel fit PASS");
  assert.equal(classify(I("Calibration: Accel fit WARN: 9 poses")).severity, "warning");
  assert.equal(classify(I("Calibration: Mag fit FAIL_COVERAGE: 100 stored, coverage 40 %")).severity, "failure");
  assert.equal(t("Calibration: Gyro VERIFY PASS: run PASS, still bias differs from the active one by 0.010 deg/s (limit 0.50)"), "Gyro verify PASS");
  assert.equal(t("Calibration: Gyro calibration result: PASS, bias 0.1 0.2 0.3 deg/s, noise 0.01 0.01 0.01 deg/s -> review"), "Gyro calibration result: PASS");
  assert.equal(classify(I("Calibration: Gyro calibration FAILED: FAIL_NOISE, bias 0 0 0")).severity, "failure");
  assert.equal(t("Calibration: gyro bias run PASS, bias 0.100 0.200 0.300 deg/s (was 0.1 0.2 0.3), noise 0.01 0.01 0.01 deg/s, IMU 30.0 C"), "Gyro bias run PASS");
  assert.equal(classify(W("Calibration: gyro bias run FAILED (noise too high, 3 restarts, checks 0x4): gyro calibration invalid until a run passes")).severity, "failure");
  assert.equal(t("Calibration: Accel calibration applied (RAM only until Save); the estimator zero is invalid, the automatic zero runs next: keep the vehicle still"), "Accel calibration applied (RAM only until Save)");
  assert.equal(t("Calibration: saved to EEPROM and verified by read-back"), "Calibration saved to EEPROM");
  assert.equal(classify(W("Calibration: EEPROM save FAILED (read-back differs), the values stay in RAM only")).severity, "failure");
  assert.equal(classify(I("Calibration: all three calibrations checked at the end of the gyro run, countdown continues")).calib, "ok");
});

test("periodic progress and ordinary INFO lines raise nothing", () => {
  assert.equal(classify(I("Calibration: Accel: 2 poses captured (moving)")), null);
  assert.equal(classify(I("Calibration: Gyro: 40 % of the still window")), null);
  assert.equal(classify(I("Calibration: gyro bias run started (window 6.0 s, keep the vehicle still)")), null);
  assert.equal(classify("[node 1 DEBUG] anything"), null);
});

test("WARNING and ERROR always notify; other nodes only at WARNING/ERROR", () => {
  let c = classify(W("Something odd happened"));
  assert.equal(c.category, "warning");
  assert.equal(c.severity, "warning");
  assert.equal(c.title, "Something odd happened");
  c = classify("[node 1 ERROR] Flash write failed");
  assert.equal(c.category, "error");
  assert.equal(c.severity, "failure");
  assert.equal(classify(I("Arm refused: estimator zeroing active", 0)), null); // Router/other node INFO: ignored even if it matches
  assert.equal(classify(I("Calibration: Accel fit PASS", 2)), null);
  assert.equal(classify(W("Radio retry", 2)).title, "node 2: Radio retry");
  assert.equal(classify(W("Radio retry", 2)).isProblemLevel, true);
});

test("log settings merge: defaults all categories on, audio only for the abort", () => {
  assert.equal(DEFAULTS.log.enabled, true);
  assert.deepEqual(Object.entries(DEFAULTS.log.categories).filter(([, v]) => !v), []);
  assert.deepEqual(Object.entries(DEFAULTS.log.audio).filter(([, v]) => v).map(([k]) => k), ["abort"]);
  const m = mergeSettings(DEFAULTS, { log: { enabled: false, categories: { refused: false, nope: true, error: "x" }, audio: { warning: true } } });
  assert.equal(m.log.enabled, false);
  assert.equal(m.log.categories.refused, false);
  assert.equal(m.log.categories.error, true);
  assert.equal(m.log.categories.nope, undefined);
  assert.equal(m.log.audio.warning, true);
  assert.equal(DEFAULTS.log.audio.warning, false); // base not modified
});
