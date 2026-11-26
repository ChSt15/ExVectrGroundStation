import test from "node:test";
import assert from "node:assert/strict";
import { Trail, isNewMission, lastMissionStart, pathUpToNow, entirePath, cleanTrailSettings } from "../app/view3d/trail.js";

test("trail: a sample is added when the ship moved or 100 ms passed, else skipped", () => {
  const tr = new Trail(60);
  assert.equal(tr.add(0, [0, 0, 0]), "added");
  assert.equal(tr.add(0.01, [0.01, 0, 0]), "skipped"); // 1 cm, 10 ms
  assert.equal(tr.add(0.02, [0.1, 0, 0]), "added"); // moved 9 cm
  assert.equal(tr.add(0.05, [0.1, 0, 0]), "skipped"); // standing, 30 ms
  assert.equal(tr.add(0.13, [0.1, 0, 0]), "added"); // standing, 110 ms
  assert.equal(tr.pts.length, 3);
  assert.equal(tr.add(NaN, [0, 0, 0]), "skipped");
});

test("trail: there is no time limit, old points stay for an hour", () => {
  const tr = new Trail(60);
  for (let t = 0; t <= 3600; t += 0.1) tr.add(t, [t * 0.5, 0, 0]);
  assert.equal(tr.pts[0].t, 0);
  assert.ok(tr.pts.length > 35000);
});

test("trail: a sample older than the last one (replay seek back) resets it", () => {
  const tr = new Trail(60);
  tr.add(100, [1, 1, 1]);
  tr.add(101, [2, 2, 2]);
  assert.equal(tr.add(50, [0, 0, 0]), "reset");
  assert.equal(tr.pts.length, 1);
});

test("trail: load skips null entries, sorts by time and thins like live samples", () => {
  const tr = new Trail(60);
  tr.load({ t: [3, 1, 2], pos: [[3, 0, 0], [1, 0, 0], null] });
  assert.deepEqual(tr.pts.map((p) => p.t), [1, 3]);
  const dense = new Trail(60);
  const t = [], pos = [];
  for (let i = 0; i < 1000; i++) { t.push(i * 0.001); pos.push([0, 0, 0]); } // 1 kHz, standing
  dense.load({ t, pos });
  assert.ok(dense.pts.length <= 11);
  dense.load({ t, pos }, 0.5);
  assert.ok(dense.pts[0].t >= 0.5);
});

test("trail: the memory cap decimates the OLD part, keeps the first point and the recent points", () => {
  const tr = new Trail(60, { maxPoints: 1000 });
  for (let i = 0; i < 5000; i++) tr.add(i, [i, 0, 0]);
  assert.ok(tr.pts.length <= 1000 && tr.pts.length > 500);
  assert.equal(tr.pts[0].t, 0);
  assert.equal(tr.pts[tr.pts.length - 1].t, 4999);
  // the last 100 samples are all still there (full resolution), the oldest are thinned
  const recent = tr.pts.filter((p) => p.t >= 4900).length;
  assert.equal(recent, 100);
  const gaps = tr.pts.slice(1).map((p, i) => p.t - tr.pts[i].t);
  assert.ok(gaps[0] > 1 && gaps[gaps.length - 1] === 1);
  assert.ok(gaps.every((g) => g > 0)); // still strictly time ordered
});

test("trail: version changes on every change, fades by age and 0 for the future", () => {
  const tr = new Trail(10);
  const v0 = tr.version;
  tr.add(0, [0, 0, 0]);
  tr.add(5, [1, 0, 0]);
  tr.add(20, [2, 0, 0]);
  assert.ok(tr.version > v0);
  assert.deepEqual(tr.fades(10), [0, 0.5, 0]);
  tr.truncateAfter(5);
  assert.equal(tr.pts.length, 2);
  tr.clear();
  assert.equal(tr.pts.length, 0);
});

const M = (mode, tMission) => ({ mode, tMission });

test("new mission: leaving Idle or Finished for Initialisation, Startup or Running, or time back into a countdown", () => {
  assert.equal(isNewMission(M("Idle", 0), M("Initialisation", -10)), true);
  assert.equal(isNewMission(M("Idle", 0), M("Running", 0)), true);
  assert.equal(isNewMission(M("Finished", 200), M("Startup", -10)), true);
  assert.equal(isNewMission(M("Running", 50), M("Running", -9)), true); // clock back into a countdown
  assert.equal(isNewMission(M("Startup", -3), M("Running", 0)), false); // same mission continues
  assert.equal(isNewMission(M("Running", 5), M("Running", 6)), false);
  assert.equal(isNewMission(M("Running", 5), M("Finished", 6)), false);
  assert.equal(isNewMission(undefined, M("Running", 0)), false); // first observation
  assert.equal(isNewMission(M("Idle", null), M("Idle", null)), false);
});

const mission = {
  t: [0, 10, 20, 30, 40, 50, 60],
  mission_mode: ["Idle", "Initialisation", "Running", "Finished", "Idle", "Startup", "Running"],
  t_mission: [0, -10, 0, 60, 0, -10, 0],
};
const pos = { t: [], pos: [] };
for (let t = 0; t <= 70; t += 1) { pos.t.push(t); pos.pos.push([t, 0, 0]); }

test("lastMissionStart: the last new mission at or before now", () => {
  assert.equal(lastMissionStart(mission, 5), null);
  assert.equal(lastMissionStart(mission, 15), 10);
  assert.equal(lastMissionStart(mission, 45), 10);
  assert.equal(lastMissionStart(mission, 55), 50);
  assert.equal(lastMissionStart({}, 55), null);
});

test("replay up to now: from the recording start or the current mission, shortens when seeking back", () => {
  const tr = new Trail(60);
  let r = pathUpToNow(pos, mission, 45, tr);
  assert.equal(r.missionStart, 10);
  assert.equal(tr.pts[0].t, 10);
  assert.equal(tr.pts[tr.pts.length - 1].t, 45);
  r = pathUpToNow(pos, mission, 55, tr);
  assert.equal(tr.pts[0].t, 50); // a new mission cleared the path
  r = pathUpToNow(pos, mission, 5, tr);
  assert.equal(r.missionStart, null);
  assert.equal(tr.pts[0].t, 0); // from the start of the recording
  assert.equal(tr.pts[tr.pts.length - 1].t, 5);
  pathUpToNow(pos, { t: [] }, 30, tr); // no mission data: from the start
  assert.equal(tr.pts.length, 31);
});

test("replay entire path: everything, independent of the position, future points not highlighted", () => {
  const tr = new Trail(10);
  entirePath(pos, tr);
  assert.equal(tr.pts.length, 71);
  assert.equal(tr.pts[70].t, 70);
  const f = tr.fades(35);
  assert.equal(f[70], 0);
  assert.equal(f[35], 1);
  assert.equal(f[20], 0);
});

test("trail settings: unknown values fall back to path up to now", () => {
  assert.deepEqual(cleanTrailSettings({ replayMode: "entire" }), { replayMode: "entire" });
  assert.deepEqual(cleanTrailSettings({ replayMode: "x" }), { replayMode: "upto" });
  assert.deepEqual(cleanTrailSettings(null), { replayMode: "upto" });
});
