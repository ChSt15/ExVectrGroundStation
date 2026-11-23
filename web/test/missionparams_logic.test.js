import test from "node:test";
import assert from "node:assert/strict";
import { ROWS, GAP_MS, apogeeWarning, changedRows, defaultInputs, describeRow, parseValue, runSequence, summarize, validationText, vehicleText, vehicleValue } from "../app/vehicle/missionparams_logic.js";

const row = (k) => ROWS.find((r) => r.key === k);
const vehicleAll = (over = {}) => ({ ...Object.fromEntries(ROWS.map((r) => [r.key, r.def])), ...over });

test("rows: ids 1..6 and the stop altitude as the 7th", () => {
  assert.deepEqual(ROWS.slice(0, 6).map((r) => r.id), [1, 2, 3, 4, 5, 6]);
  assert.equal(ROWS[6].action, "SET_STOP_ALTITUDE");
  assert.deepEqual(ROWS.map((r) => r.group), ["Flight (profile 2)", "Flight (profile 2)", "Hover test (profile 0)", "Waypoint test (profile 1)", "Waypoint test (profile 1)", "Waypoint test (profile 1)", "Vehicle settings"]);
});

test("parse and validation text follow the allowed range", () => {
  assert.equal(parseValue("7", row("ascent_speed")), 7);
  for (const t of ["0.4", "10.1", "", "x", "NaN", "-1"]) assert.equal(parseValue(t, row("ascent_speed")), null);
  assert.equal(parseValue("0", row("hover_hold_s")), 0);
  assert.equal(parseValue("601", row("hover_hold_s")), null);
  assert.equal(validationText("11", row("ascent_speed")), "allowed: 0.5 to 10 m/s");
  assert.equal(validationText("1", row("apogee_altitude")), "allowed: 2 to 1000 m");
  assert.equal(validationText("0.5", row("stop_altitude")), "allowed: 1 or more m");
});

test("changed-field detection: only inputs that differ from the vehicle value", () => {
  const veh = vehicleAll();
  assert.deepEqual(changedRows(defaultInputs(), veh).changed, []);
  const inputs = { ...defaultInputs(), ascent_speed: "5", stop_altitude: "35" };
  const { changed, invalid } = changedRows(inputs, veh);
  assert.deepEqual(changed.map((c) => [c.row.key, c.value]), [["ascent_speed", 5], ["stop_altitude", 35]]);
  assert.deepEqual(invalid, []);
  // f32 readback noise is not a change
  assert.deepEqual(changedRows(defaultInputs(), vehicleAll({ ascent_speed: 7.0000001 })).changed, []);
  // an invalid input is reported and never sent
  const bad = changedRows({ ...defaultInputs(), apogee_altitude: "1" }, veh);
  assert.deepEqual(bad.invalid, ["apogee_altitude"]);
  assert.deepEqual(bad.changed, []);
  // vehicle value unknown: only edited fields count
  const unk = vehicleAll({ hover_hold_s: null, p1_hold_1_s: null });
  assert.deepEqual(changedRows(defaultInputs(), unk, {}).changed, []);
  assert.deepEqual(changedRows(defaultInputs(), unk, { hover_hold_s: true }).changed.map((c) => c.row.key), ["hover_hold_s"]);
});

test("reset to defaults and vehicle text", () => {
  assert.equal(defaultInputs().apogee_altitude, "120");
  assert.equal(defaultInputs().stop_altitude, "20");
  assert.equal(vehicleText(7), "vehicle: 7.0");
  assert.equal(vehicleText(null), "vehicle: unknown");
  assert.equal(vehicleValue(row("ascent_speed"), { ascent_speed: 6 }, { stop_altitude: 30 }), 6);
  assert.equal(vehicleValue(row("stop_altitude"), { ascent_speed: 6 }, { stop_altitude: 30 }), 30);
  assert.equal(vehicleValue(row("ascent_speed"), { ascent_speed: null }, null), null);
});

test("apogee warning below 90 m", () => {
  assert.equal(apogeeWarning("89.9"), "below 90 m: no bellyflop, the flight ends in RTH");
  assert.equal(apogeeWarning("2"), "below 90 m: no bellyflop, the flight ends in RTH");
  assert.equal(apogeeWarning("90"), "");
  assert.equal(apogeeWarning("120"), "");
  assert.equal(apogeeWarning(""), "");
});

test("row result wording", () => {
  const r = (extra) => ({ state: "acked", attempts: 1, max_attempts: 5, detail: 0, status_name: "DONE", ...extra });
  const row1 = row("ascent_speed");
  assert.deepEqual(describeRow(row1, r(), 5, null), { text: "set", tone: "ok" });
  assert.equal(describeRow(row1, r({ status_name: "REJECTED_STATE", detail: 1 }), 5, null).text, "refused: motors armed or in flight");
  assert.match(describeRow(row1, r({ status_name: "REJECTED_STATE", detail: 3 }), 5, null).text, /^refused: session mismatch/);
  assert.equal(describeRow(row1, r({ status_name: "REJECTED_INVALID", detail: 1 }), 5, null).text, "refused: value out of range");
  assert.equal(describeRow(row1, { state: "timeout" }, 5, null).text, "no answer from the vehicle");
  // the stop altitude keeps the echo cross-check
  assert.equal(describeRow(row("stop_altitude"), r(), 35, 35).text, "set");
  assert.equal(describeRow(row("stop_altitude"), r(), 35, 20).text, "acknowledged, waiting for the echo");
});

test("summary line", () => {
  assert.equal(summarize(["ok", "ok", "ok"]), "all 3 parameters set");
  assert.equal(summarize(["ok"]), "1 parameter set");
  assert.equal(summarize(["ok", "refused", "timeout", "error"]), "1 of 4 set, 1 refused, 1 without an answer, 1 not sent");
});

// A fake core + vehicle: answers each request after `delay` ms of virtual time.
function rig(answers, delay = 300) {
  let clock = 0;
  const reqs = new Map();
  const log = [];
  let id = 100;
  return {
    log,
    sleep: async (ms) => { clock += ms; },
    now: () => clock,
    send: async (r, value) => {
      const req_id = ++id;
      const a = answers[r.key] ?? { status_name: "DONE", detail: 0 };
      if (a.throw) throw new Error(a.throw);
      log.push({ at: clock, key: r.key, value, req_id, arg_i: r.id });
      reqs.set(req_id, { sent: clock, a });
      return { req_id };
    },
    poll: (rid) => {
      const q = reqs.get(rid);
      if (!q || q.a.never || clock - q.sent < delay) return { state: "sending", attempts: 1, max_attempts: 5 };
      return { state: "acked", attempts: 1, max_attempts: 5, ...q.a };
    },
    clock: () => clock,
  };
}

test("paced sequence: one at a time, new req_id each, 550 ms gaps, continues after a refusal", async () => {
  const rg = rig({ hover_hold_s: { status_name: "REJECTED_INVALID", detail: 1 } });
  const res = {};
  const items = [["ascent_speed", 5], ["hover_hold_s", 700], ["p1_hold_2_s", 20]].map(([k, v]) => ({ row: row(k), value: v }));
  const outcomes = await runSequence(items, { ...rg, onUpdate: (k, r) => { res[k] = r; } });
  assert.deepEqual(outcomes, ["ok", "refused", "ok"]);
  assert.equal(new Set(rg.log.map((l) => l.req_id)).size, 3);
  assert.deepEqual(rg.log.map((l) => l.arg_i), [1, 3, 5]);
  assert.ok(rg.log[1].at - rg.log[0].at >= 300 + GAP_MS && rg.log[2].at - rg.log[1].at >= 300 + GAP_MS);
  assert.equal(res.ascent_speed.text, "set");
  assert.equal(res.hover_hold_s.text, "refused: value out of range");
  assert.equal(res.hover_hold_s.tone, "bad");
  assert.equal(res.p1_hold_2_s.final, true);
});

test("sequence: no answer within 7 s, core refusal of the send, cancel", async () => {
  const rg = rig({ ascent_speed: { never: true }, apogee_altitude: { throw: "rate limit" } });
  const res = {};
  const items = ["ascent_speed", "apogee_altitude", "hover_hold_s"].map((k) => ({ row: row(k), value: 5 }));
  const outcomes = await runSequence(items, { ...rg, onUpdate: (k, r) => { res[k] = r; } });
  assert.deepEqual(outcomes, ["timeout", "error", "ok"]);
  assert.equal(res.ascent_speed.text, "no answer from the vehicle");
  assert.equal(res.apogee_altitude.text, "not sent: rate limit");
  assert.ok(rg.clock() >= 7000);
  let n = 0;
  const rg2 = rig({});
  const o2 = await runSequence(items, { ...rg2, onUpdate: () => {}, cancelled: () => n++ >= 1 });
  assert.equal(o2.length, 1);
});
