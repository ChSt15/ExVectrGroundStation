import test from "node:test";
import assert from "node:assert/strict";
import { profileName, profileText } from "../app/command/profile.js";
import { switchText } from "../app/command/command_logic.js";

test("profile names", () => {
  assert.equal(profileName(0), "hover test");
  assert.equal(profileName(1), "waypoint test");
  assert.equal(profileName(2), "flight");
  assert.equal(profileName(255), "");
  assert.equal(profileName(undefined), "");
  assert.equal(profileText(2), "2 (flight)");
  assert.equal(profileText(255), "");
  assert.equal(profileText(7), "7");
  assert.equal(switchText("profile", 1), "profile 1 (waypoint test)");
});

import { missionIndicator } from "../app/command/profile.js";

test("mission indicator", () => {
  const sel = missionIndicator({ mission_profile: 255, mission_mode: "Idle" }, 1, 1);
  assert.deepEqual([sel.text, sel.tag, sel.tone], ["waypoint test", "selected", "test"]);
  const run = missionIndicator({ mission_profile: 2, mission_mode: "Running" }, 1, 1);
  assert.deepEqual([run.text, run.tag, run.tone], ["flight", "running", "flight"]);
  assert.equal(missionIndicator({ mission_profile: 0, mission_mode: "Startup" }, 2, 2).tag, "running");
  assert.equal(missionIndicator({ mission_profile: 0, mission_mode: "Finished" }, 2, 2).text, "flight"); // finished: back to selection
  const fb = missionIndicator({ mission_profile: 255 }, undefined, 0);
  assert.deepEqual([fb.text, fb.tag], ["hover test", "selected"]);
  assert.match(fb.title, /ground station sends/);
  const none = missionIndicator({ mission_profile: 255 }, 255, undefined);
  assert.deepEqual([none.text, none.tone], ["none", "none"]);
  assert.match(missionIndicator({}, 2, 0).title, /Mismatch: the vehicle applies profile 2, the ground station sends 0/);
  assert.ok(!/Mismatch/.test(missionIndicator({}, 2, 2).title));
});
