import test from "node:test";
import assert from "node:assert/strict";
import { NO_ACK_TEXT, TELECOMMAND_HOLD_MS, describeSent, groupCommands, lastSent, parseParam, reportedSimulation, sendState } from "../app/telecommand/telecommand_logic.js";

const WL = [
  { name: "Arm", group: "Vehicle", param_int: 165, param_min: null, param_max: null },
  { name: "SimulationMode", group: "Vehicle", param_int: 169, param_min: null, param_max: null },
  { name: "MissionBegin", group: "Mission", param_int: -10, param_min: -3600, param_max: 3600 },
];

test("hold is 1 s and the panel states there is no ACK", () => {
  assert.equal(TELECOMMAND_HOLD_MS, 1000);
  assert.match(NO_ACK_TEXT, /no acknowledgement and no retry/);
});

test("grouping keeps the core's order", () => {
  assert.deepEqual(groupCommands(WL).map((g) => [g.group, g.items.length]), [["Vehicle", 2], ["Mission", 1]]);
  assert.deepEqual(groupCommands(null), []);
});

test("sent text comes from the core's record: time ago, once, no result implied", () => {
  const push = { recent: [{ n: 3, name: "SimulationMode", source: "usb", param_int: 169, t: 100 }, { n: 1, name: "SimulationMode", source: "usb", param_int: 169, t: 50 }] };
  assert.equal(lastSent(push, "SimulationMode").n, 3);
  assert.equal(lastSent(push, "Arm"), null);
  const d = describeSent(lastSent(push, "SimulationMode"), 102.5);
  assert.equal(d.text, "#3 sent 2.5 s ago once via usb, param 169; no ACK exists");
  assert.ok(!/done|success|acked/i.test(d.text));
  assert.equal(describeSent(null, 5).text, "");
});

test("send is possible only live, connected, on a control host, with a route and a valid parameter", () => {
  const ok = { apiState: "open", view: "live", canControl: true, route: "usb" };
  assert.equal(sendState(ok).enabled, true);
  for (const [k, v] of [["view", "replay"], ["apiState", "closed"], ["canControl", false], ["route", null], ["paramOk", false]]) {
    const s = sendState({ ...ok, [k]: v });
    assert.equal(s.enabled, false, k);
    assert.ok(s.reason);
  }
});

test("parameter field: integers within the whitelist range only", () => {
  const mb = WL[2];
  assert.equal(parseParam("-30", mb), -30);
  assert.equal(parseParam("3601", mb), null);
  assert.equal(parseParam("1.5", mb), null);
  assert.equal(parseParam("", mb), null);
  assert.equal(parseParam("5", WL[0]), null); // a fixed-parameter entry takes none
});

test("the simulation state shown is only what the vehicle reports", () => {
  assert.equal(reportedSimulation(undefined).text, "no VehicleStatus received");
  assert.equal(reportedSimulation({ f: { simulation: true } }).text, "vehicle reports: SIMULATION");
  assert.equal(reportedSimulation({ f: { simulation: false } }).text, "vehicle reports: REAL SENSORS");
  assert.match(reportedSimulation({ f: {} }).text, /does not report/);
});
