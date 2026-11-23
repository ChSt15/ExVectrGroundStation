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
