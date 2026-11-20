import test from "node:test";
import assert from "node:assert/strict";
import { LOCK_OFFLINE, LOCK_REPLAY, describeRecorder, describeSource, simulationFlag } from "../app/status/source_logic.js";

const VS = { nominal_rate_hz: 5 };
const entry = (sim, t = 100, extra = {}) => ({ type: "VehicleStatus", src: "usb", t, f: sim === undefined ? {} : { simulation: sim }, ...extra });

test("simulation flag: missing, not supplied, stale, fresh", () => {
  assert.equal(simulationFlag(undefined, 100, VS).sim, null);
  assert.match(simulationFlag(entry(undefined), 100, VS).why, /not supplied by usb/);
  const fresh = simulationFlag(entry(true), 100.1, VS);
  assert.equal(fresh.sim, true);
  assert.equal(fresh.stale, false);
  assert.equal(simulationFlag(entry(false), 105, VS).stale, true);
});

test("live banner: REAL, SIMULATION, unknown and stale are distinct and never assumed", () => {
  const live = (sim, now = 100.1) => describeSource({ connState: "open", view: "live", live: simulationFlag(sim === "none" ? undefined : entry(sim), now, VS) });
  assert.equal(live(false).kind, "real");
  assert.equal(live(false).label, "LIVE · REAL VEHICLE");
  assert.equal(live(true).kind, "sim");
  assert.equal(live(true).inputsLocked, false);
  const none = live("none");
  assert.equal(none.kind, "unknown");
  assert.match(none.details[0], /no VehicleStatus/);
  assert.equal(live(undefined).kind, "unknown");
  const stale = live(false, 110);
  assert.equal(stale.kind, "unknown");
  assert.equal(stale.label, "LIVE · last reported REAL");
  assert.match(stale.details[0], /stale/);
});

test("replay banner: log name, position, recorded real or simulation, inputs locked", () => {
  const replay = { state: "paused", name: "2026-11-20_flight.exlog", t: 1065, t_start: 1000, t_end: 1300, speed: 1 };
  const d = describeSource({ connState: "open", view: "replay", replay, recorded: simulationFlag(entry(true, 1065), 1065, VS) });
  assert.equal(d.kind, "replay");
  assert.deepEqual(d.details, ["2026-11-20_flight.exlog", "01:05 / 05:00", "paused"]);
  assert.equal(d.recorded, "SIMULATION");
  assert.equal(d.inputsLocked, true);
  assert.equal(d.lockReason, LOCK_REPLAY);
  const real = describeSource({ connState: "open", view: "replay", replay: { ...replay, state: "playing", speed: 2 }, recorded: simulationFlag(entry(false, 1065), 1065, VS) });
  assert.equal(real.recorded, "REAL");
  assert.equal(real.details[2], "playing 2×");
  assert.equal(describeSource({ connState: "open", view: "replay", replay, recorded: simulationFlag(undefined, 1, VS) }).recorded, "unknown");
  const closed = describeSource({ connState: "open", view: "replay", replay: { state: "closed" } });
  assert.deepEqual(closed.details, ["no log open"]);
  assert.equal(closed.recorded, null);
  assert.equal(closed.inputsLocked, true);
});

test("no core connection locks inputs in every view", () => {
  for (const view of ["live", "replay"]) {
    const d = describeSource({ connState: "closed", view, live: simulationFlag(entry(false), 100, VS) });
    assert.equal(d.kind, "offline");
    assert.equal(d.lockReason, LOCK_OFFLINE);
  }
});

test("recorder block: idle, recording with duration and size, low disk, error", () => {
  assert.equal(describeRecorder(undefined).available, false);
  assert.equal(describeRecorder({ available: false }).available, false);
  const idle = describeRecorder({ active: false, free_bytes: 800e9 }, 10);
  assert.equal(idle.active, false);
  assert.equal(idle.free, "800.0 GB free");
  assert.equal(idle.freeTone, "");
  const rec = describeRecorder({ active: true, name: "a.exlog", started: 100, bytes: 2.5e6, free_bytes: 3e9 }, 165);
  assert.equal(rec.duration, "01:05");
  assert.equal(rec.size, "2.5 MB");
  assert.equal(rec.freeTone, "warn");
  assert.equal(rec.tone, "rec");
  assert.equal(describeRecorder({ active: true, free_bytes: 5e8, error: "disk full" }, 1).tone, "bad");
  assert.equal(describeRecorder({ active: false, free_bytes: 5e8 }, 1).freeTone, "bad");
});
