import test from "node:test";
import assert from "node:assert/strict";
import {
  DANGER_ENABLE_MS, DANGER_TEXT, DangerGate, HoldTimer, REQUEST_HOLD_MS, actionKey, describeRequest, groupActions, requestFor,
  sendState,
} from "../app/requests/requests_logic.js";

function fakeTimers() {
  let now = 0;
  let id = 0;
  const jobs = new Map();
  return {
    now: () => now,
    setTimeout: (fn, ms) => (jobs.set(++id, { fn, at: now + ms }), id),
    clearTimeout: (i) => jobs.delete(i),
    advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...jobs.entries()].filter(([, j]) => j.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        now = due[1].at;
        jobs.delete(due[0]);
        due[1].fn();
      }
      now = end;
    },
  };
}

const WL = [
  { action: "CALIB_APPLY", target: 1, label: "Apply", group: "Calibration", confirm: "hold", route: "usb" },
  { action: "SAVE_CONFIG", target: 1, label: "Save", group: "Vehicle", confirm: "hold", route: "usb" },
  { action: "CALIB_GYRO_START", target: 1, label: "Gyro", group: "Calibration", confirm: "hold", route: "usb" },
  { action: "LINK_UPLOAD_MODE", target: 2, label: "TX", group: "Link modules", confirm: "danger", route: "link" },
  { action: "LINK_UPLOAD_MODE", target: 3, label: "RX", group: "Link modules", confirm: "danger", route: null },
];

test("constants: hold 1 s, danger button disabled for 2 s, the plain warning text", () => {
  assert.equal(REQUEST_HOLD_MS, 1000);
  assert.equal(DANGER_ENABLE_MS, 2000);
  assert.match(DANGER_TEXT, /stops the radio link of the TX\/RX module/);
  assert.match(DANGER_TEXT, /firmware-upload mode/);
  assert.match(DANGER_TEXT, /always obeys, even in flight\.$/);
});

test("groupActions keeps the core's order and groups by group", () => {
  const g = groupActions(WL);
  assert.deepEqual(g.map((x) => x.group), ["Calibration", "Vehicle", "Link modules"]);
  assert.deepEqual(g[0].items.map((a) => a.action), ["CALIB_APPLY", "CALIB_GYRO_START"]);
  assert.deepEqual(groupActions(null), []);
  assert.equal(actionKey(WL[3]), "LINK_UPLOAD_MODE@2");
  assert.notEqual(actionKey(WL[3]), actionKey(WL[4]));
});

test("requestFor finds in-flight first, then the newest finished, per action and target", () => {
  const reqs = {
    inflight: [{ action: "LINK_UPLOAD_MODE", target: 3, state: "sending", req_id: 9 }],
    recent: [
      { action: "LINK_UPLOAD_MODE", target: 2, state: "acked", req_id: 8 },
      { action: "LINK_UPLOAD_MODE", target: 2, state: "timeout", req_id: 4 },
    ],
  };
  assert.equal(requestFor(reqs, WL[3]).req_id, 8);
  assert.equal(requestFor(reqs, WL[4]).req_id, 9);
  assert.equal(requestFor(reqs, WL[0]), null);
  assert.equal(requestFor(null, WL[0]), null);
});

test("describeRequest shows only what the core reports: ACK status exactly, timeout, or sending", () => {
  assert.deepEqual(describeRequest(null), { tone: "dim", text: "" });
  const base = { req_id: 7, attempts: 2, max_attempts: 5, source: "usb", detail: 0 };
  assert.deepEqual(describeRequest({ ...base, state: "sending" }), { tone: "warn", text: "sending, attempt 2/5 via usb (id 7)" });
  assert.equal(describeRequest({ ...base, state: "acked", status_name: "DONE" }).tone, "ok");
  assert.equal(describeRequest({ ...base, state: "acked", status_name: "DONE" }).text, "ACK DONE (id 7)");
  const rej = describeRequest({ ...base, state: "acked", status_name: "REJECTED_STATE", detail: 3 });
  assert.deepEqual(rej, { tone: "bad", text: "ACK REJECTED_STATE (detail 3) (id 7)" });
  assert.equal(describeRequest({ ...base, state: "acked", status_name: "#200" }).tone, "bad"); // unknown is never success
  const to = describeRequest({ ...base, attempts: 5, state: "timeout" });
  assert.equal(to.tone, "bad");
  assert.match(to.text, /^TIMEOUT: no ACK after 5 attempts/);
  assert.equal(describeRequest({ ...base, state: "sent-ok" }).tone, "dim"); // an unknown state is not shown as success
});

test("sendState: replay, no connection, view-only host, in flight and no route all disable the button", () => {
  const ok = { apiState: "open", view: "live", canControl: true, inflight: false, route: "usb" };
  assert.deepEqual(sendState(ok), { enabled: true, reason: "" });
  assert.equal(sendState({ ...ok, view: "replay" }).enabled, false);
  assert.match(sendState({ ...ok, view: "replay" }).reason, /nothing is ever sent/);
  assert.equal(sendState({ ...ok, apiState: "closed" }).enabled, false);
  assert.equal(sendState({ ...ok, canControl: false }).enabled, false);
  assert.equal(sendState({ ...ok, inflight: true }).enabled, false);
  assert.equal(sendState({ ...ok, route: null }).enabled, false);
});

test("hold-to-confirm: sends only after 1 s of continuous holding, releasing early sends nothing", () => {
  const t = fakeTimers();
  const sent = [];
  const hold = new HoldTimer(() => sent.push("send"), t, REQUEST_HOLD_MS);
  hold.start();
  t.advance(999);
  assert.deepEqual(sent, []);
  assert.ok(hold.holding && hold.progress() > 0.99);
  hold.cancel();
  t.advance(5000);
  assert.deepEqual(sent, []);
  hold.start();
  t.advance(1000);
  assert.deepEqual(sent, ["send"]);
  assert.equal(hold.holding, false);
  hold.start();
  hold.start(); // a repeated pointerdown does not restart or double-fire
  t.advance(1000);
  assert.equal(sent.length, 2);
});

test("DangerGate: the confirm button becomes ready 2 s after the modal opened, closing resets it", () => {
  const t = fakeTimers();
  const gate = new DangerGate(t);
  assert.equal(gate.ready(), false);
  assert.equal(gate.remainingMs(), 2000);
  gate.open();
  t.advance(1999);
  assert.equal(gate.ready(), false);
  assert.equal(gate.remainingMs(), 1);
  t.advance(1);
  assert.equal(gate.ready(), true);
  gate.close();
  assert.equal(gate.ready(), false);
  gate.open();
  assert.equal(gate.ready(), false); // reopening starts the 2 s again
});
