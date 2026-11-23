import test from "node:test";
import assert from "node:assert/strict";
import { describeSetting, echoText, findRequest, parseStopAltitude, ACTION } from "../app/telecommand/stopalt_logic.js";

test("parse: finite metres >= 1, no maximum", () => {
  assert.equal(parseStopAltitude("20"), 20);
  assert.equal(parseStopAltitude("5.5"), 5.5);
  assert.equal(parseStopAltitude("100000"), 100000);
  for (const t of ["0.9", "-5", "", "abc", "NaN", "Infinity"]) assert.equal(parseStopAltitude(t), null);
});

test("echo text", () => {
  assert.equal(echoText(20), "vehicle: 20.0 m");
  assert.equal(echoText(NaN), "vehicle: unknown");
  assert.equal(echoText(null), "vehicle: unknown");
});

const base = { req_id: 4, action: ACTION, attempts: 1, max_attempts: 5, detail: 0 };

test("findRequest matches by id and action, in flight first", () => {
  const a = { ...base, state: "sending" };
  const b = { ...base, req_id: 5, state: "acked", status_name: "DONE" };
  assert.equal(findRequest({ inflight: [a], recent: [b] }, 4), a);
  assert.equal(findRequest({ inflight: [], recent: [b] }, 5), b);
  assert.equal(findRequest({ inflight: [], recent: [{ ...b, action: "ZERO_ESTIMATORS" }] }, 5), null);
  assert.equal(findRequest(null, 4), null);
  assert.equal(findRequest({ inflight: [a] }, null), null);
});

test("no request state yet and sending", () => {
  assert.deepEqual(describeSetting(null, 30, 20), { text: "sending", tone: "warn" });
  assert.deepEqual(describeSetting({ ...base, state: "sending", attempts: 2 }, 30, 20), { text: "sending (2/5)", tone: "warn" });
});

test("set (green) needs ACK DONE and a matching echo within 0.05 m", () => {
  const done = { ...base, state: "acked", status_name: "DONE" };
  assert.deepEqual(describeSetting(done, 30, 30.04), { text: "set", tone: "ok" });
  assert.deepEqual(describeSetting(done, 30, 20, 1), { text: "acknowledged, waiting for the echo", tone: "warn" });
  const late = describeSetting(done, 30, 20, 9);
  assert.equal(late.tone, "bad");
  assert.match(late.text, /acknowledged, but the vehicle reports 20\.0 m/);
  assert.equal(describeSetting(done, 30, NaN, 1).tone, "warn");
});

test("each refusal pair is shown in plain words, never as set", () => {
  const rej = (status_name, detail) => describeSetting({ ...base, state: "acked", status_name, detail }, 30, 30);
  assert.deepEqual(rej("REJECTED_STATE", 1), { text: "refused: motors armed or in flight", tone: "bad" });
  assert.match(rej("REJECTED_STATE", 3).text, /^refused: session mismatch, restart the GS or wait 1 s/);
  assert.deepEqual(rej("REJECTED_INVALID", 1), { text: "refused: value out of range", tone: "bad" });
  assert.equal(rej("REJECTED_INVALID", 2).text, "refused: wrong target");
  assert.match(rej("REJECTED_INVALID", 3).text, /unknown action/);
  assert.equal(rej("FAILED", 0).tone, "bad");
});

test("timeout and abort are failures", () => {
  assert.deepEqual(describeSetting({ ...base, state: "timeout" }, 30, 20), { text: "no answer from the vehicle", tone: "bad" });
  assert.match(describeSetting({ ...base, state: "aborted", abort_reason: "x" }, 30, 20).text, /^aborted: x/);
});
