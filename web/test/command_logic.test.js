import test from "node:test";
import assert from "node:assert/strict";
import {
  ageText, commandSourceCell, engineRows, flagsText, pickupHints, rejectList, safeSeenList, session16Text, switchText, switchTone,
} from "../app/command/command_logic.js";
import { describeVehicleStatus } from "../app/status/status_logic.js";

test("CMD_APPLIED field meanings", () => {
  assert.equal(switchText("go", 1), "GO");
  assert.equal(switchText("go", 0), "NOT_GO (abort)");
  assert.equal(switchText("profile", 2), "profile 2 (flight)");
  assert.equal(switchText("vehicle_mode", 0), "REAL");
  assert.equal(switchText("vehicle_mode", 1), "SIMULATION");
  assert.equal(switchText("motors", 1), "armed");
  assert.equal(switchText("motors", 0), "disarmed");
  assert.equal(switchText("reset_button", 1), "pressed");
  assert.equal(switchText("failsafe", 1), "FAILSAFE requested");
  assert.equal(switchText("motors", undefined), "n/s");
  assert.equal(switchTone("failsafe", 1), "bad");
  assert.equal(switchTone("go", 1), "");
});

test("safe-seen bits, ages, rejects, flags, session", () => {
  assert.deepEqual(safeSeenList(0b0101).map((x) => x.seen), [true, false, true, false]);
  assert.equal(safeSeenList(0b0100)[2].name, "SD armed");
  assert.equal(ageText(0.04), "0.04 s");
  assert.equal(ageText(null), "never");
  assert.equal(ageText(NaN), "never");
  assert.deepEqual(rejectList([1, 2, 3, 4]).map((x) => x.count), [1, 2, 3, 4]);
  assert.equal(rejectList([1, 2, 3, 4])[2].label, "foreign session");
  assert.equal(rejectList(undefined)[0].count, null);
  assert.equal(flagsText(3), "POCKET_LIVE, INPUT_VIRTUAL");
  assert.equal(flagsText(0), "none");
  assert.equal(session16Text(0xcafe1234), "1234");
  assert.equal(session16Text(0), "none");
});

const ctrl = (f) => ({ type: "VehicleStatus", src: "usb", t: 100, iv: 0.1, stale_after: 1, f });
const byKey = (d) => Object.fromEntries(d.items.map((i) => [i.key, i]));

test("command source cell: GS, session, fresh links, MULTI_MASTER, GS_FAILSAFE_REQ, NOT MASTER", () => {
  let c = commandSourceCell({ ctrl_source: "GS", cmd_session: 0x1234, ctrl_flags: (1 << 7) | (1 << 8) }, 0xaaaa1234);
  assert.equal(c.text, "GS #1234 · USB+UART fresh");
  assert.equal(c.tone, "");
  c = commandSourceCell({ ctrl_source: "GS", cmd_session: 0x1234, ctrl_flags: 1 << 8 }, 0xaaaa1234);
  assert.equal(c.text, "GS #1234 · UART fresh");
  c = commandSourceCell({ ctrl_source: "GS", cmd_session: 0x9999, ctrl_flags: 1 << 7 }, 0xaaaa1234);
  assert.match(c.text, /NOT MASTER/);
  assert.equal(c.tone, "bad");
  c = commandSourceCell({ ctrl_source: "GS", cmd_session: 0x1234, ctrl_flags: (1 << 7) | (1 << 11) | (1 << 12) }, null);
  assert.match(c.text, /MULTI_MASTER · GS_FAILSAFE_REQ/);
  assert.equal(c.tone, "bad");
  c = commandSourceCell({ ctrl_source: "GS", cmd_session: 0x1234, ctrl_flags: 0 }, null);
  assert.match(c.text, /no link fresh/);
  assert.equal(c.tone, "warn");
  c = commandSourceCell({ ctrl_source: "NONE", ctrl_flags: 0 }, 5);
  assert.equal(c.text, "NONE");
  assert.equal(commandSourceCell(null).text, "n/s");
});

test("status bar shows the command source and compares the own session", () => {
  const f = { ctrl_source: "GS", cmd_session: 0x1234, ctrl_flags: (1 << 7) | (1 << 9) };
  let d = byKey(describeVehicleStatus(ctrl(f), 100.2, undefined, null, 0x1234));
  assert.equal(d.ctrl_source.label, "Command source");
  assert.equal(d.ctrl_source.text, "GS #1234 · USB fresh");
  d = byKey(describeVehicleStatus(ctrl(f), 100.2, undefined, null, 0x4321));
  assert.match(d.ctrl_source.text, /NOT MASTER/);
  assert.equal(d.ctrl_source.tone, "bad");
});

test("engine rows label what is sent and show hints and warnings", () => {
  assert.deepEqual(engineRows(null), []);
  const rows = Object.fromEntries(
    engineRows({
      session: 0xcafe0001, session16: 1, input: "pocket", pocket: "live", sending: true, links: ["usb", "router"], master: "not_master", multi_master: true,
      out: { go: 0, profile: 2, vehicle_mode: 1, motors: 0, reset_button: 0, failsafe: 0 }, hints: { sa: true, sd: false, se: false, sc: 0 },
    }).map((r) => [r.key, r]),
  );
  assert.equal(rows.input.text, "Pocket");
  assert.equal(rows.sending.text, "sending to usb, router (25 Hz)");
  assert.match(rows.sent.text, /go NOT_GO, profile 2 \(flight\), SIMULATION, motors disarmed/);
  assert.equal(rows.master.tone, "bad");
  assert.equal(rows.multi.tone, "bad");
  assert.equal(rows["hint-0"].text, "SA is up while go is off: flip it down first");
  assert.match(rows["hint-1"].text, /SC: flip to REAL/);
  assert.equal(pickupHints({ sa: false, sd: true, se: true, sc: null }).length, 2);
  assert.equal(engineRows({ session: 1, session16: 1, input: "none", pocket: "lost", sending: false, ready: true, out: {}, hints: {} }).find((r) => r.key === "pocket").tone, "warn");
});
