import assert from "node:assert/strict";
import { test } from "node:test";
import { applyState, badgeState, checkNotes, describeVersion, liveGuards } from "../app/update/update_logic.js";

const ok = { apiState: "open", view: "live", canControl: true, vehicle: { f: { armed: false, motors_enabled: false, mission_mode: "Idle" } }, recorder: { active: false }, rc: { lease: null }, coreGuards: [] };

test("no guard on a quiet live view", () => assert.deepEqual(liveGuards(ok), []));

test("each guard gives its reason", () => {
  const has = (patch, text) => assert.ok(liveGuards({ ...ok, ...patch }).some((w) => w.includes(text)), text);
  has({ apiState: "closed" }, "connection");
  has({ view: "replay" }, "replay");
  has({ canControl: false }, "view-only");
  has({ vehicle: { f: { armed: true } } }, "ARMED");
  has({ vehicle: { f: { motors_enabled: true } } }, "MOTORS");
  has({ vehicle: { f: { mission_mode: "Running" } } }, "RUNNING");
  has({ recorder: { active: true } }, "recording");
  has({ rc: { lease: { id: "x" } } }, "lease");
  has({ coreGuards: ["a request is in flight"] }, "request");
});

const st = { git: true, enabled: true, branch: "master", configured_branch: "master", dirty: false, detached: false };
const chk = { behind: 2, ahead: 0, diverged: false };

test("apply needs a check with new commits and a clean tree on the configured branch", () => {
  assert.equal(applyState({ status: st, check: chk, guards: [] }).enabled, true);
  assert.match(applyState({ status: st, check: null, guards: [] }).reason, /check for updates first/);
  assert.match(applyState({ status: st, check: { ...chk, behind: 0 }, guards: [] }).reason, /up to date/);
  assert.match(applyState({ status: { ...st, dirty: true }, check: chk, guards: [] }).reason, /local changes/);
  assert.match(applyState({ status: { ...st, branch: "x" }, check: chk, guards: [] }).reason, /branch x/);
  assert.equal(applyState({ status: { ...st, branch: "x", allow_switch: true }, check: chk, guards: [] }).enabled, true);
  assert.match(applyState({ status: st, check: { ...chk, ahead: 1, diverged: true }, guards: [] }).reason, /fast-forward/);
  assert.match(applyState({ status: { ...st, detached: true }, check: chk, guards: [] }).reason, /detached/);
  assert.match(applyState({ status: st, check: chk, guards: ["the vehicle reports ARMED"] }).reason, /ARMED/);
});

test("a pending restart can be applied without a new check", () => {
  assert.equal(applyState({ status: st, check: null, guards: [], restartPending: true }).enabled, true);
  assert.equal(applyState({ status: st, check: null, guards: ["recording"], restartPending: true }).enabled, false);
});

test("badge", () => {
  assert.equal(badgeState(null).show, false);
  assert.equal(badgeState({ enabled: true, behind: 0 }).show, false);
  assert.equal(badgeState({ enabled: true, behind: 3 }).text, "update: 3 new");
  assert.equal(badgeState({ enabled: true, behind: 0, restart_pending: true }).text, "restart pending");
  assert.equal(badgeState({ enabled: false, behind: 3 }).show, false);
});

test("describe and notes", () => {
  assert.match(describeVersion({ git: true, sha: "abc", branch: "master", date: "2026-11-20T10:00:00+01:00", subject: "s" }), /abc on master · 2026-11-20 · s/);
  assert.match(describeVersion({ git: false }), /git checkout/);
  assert.equal(checkNotes({ deps_changed: true, submodules_changed: true, diverged: false, more_commits: 4 }).length, 3);
});
