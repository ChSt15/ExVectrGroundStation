import test from "node:test";
import assert from "node:assert/strict";
import { POWER_LIMIT_DBM, powerReport, powerTone } from "../app/link/link_logic.js";

test("limit is 10 dBm", () => assert.equal(POWER_LIMIT_DBM, 10));

test("equal power below the limit: no warning", () => {
  const r = powerReport({ cmd: 10, router: 10, ship: 10 });
  assert.deepEqual(r.warnings, []);
  assert.equal(r.differs || r.over, false);
});

test("commanded and applied power that differ are flagged", () => {
  const r = powerReport({ cmd: 10, router: 10, ship: 7 });
  assert.equal(r.differs, true);
  assert.match(r.warnings[0].text, /Router commands 10 dBm, ship radio node applies 7 dBm/);
  assert.equal(r.warnings[0].tone, "warn");
});

test("any end above 10 dBm is a loud legal-limit warning, each end named", () => {
  const r = powerReport({ cmd: 14, router: 10, ship: 20 });
  assert.equal(r.over, true);
  const bad = r.warnings.filter((w) => w.tone === "bad").map((w) => w.text);
  assert.equal(bad.length, 2);
  assert.match(bad[0], /14 dBm commanded by the Router: above the 10 dBm \(10 mW\) legal limit/);
  assert.match(bad[1], /20 dBm applied by the ship radio node/);
  assert.equal(powerReport({ cmd: 5, router: 11, ship: 5 }).over, true);
});

test("missing values are never compared or guessed", () => {
  assert.deepEqual(powerReport({ cmd: undefined, router: 10, ship: 7 }).warnings, []);
  assert.deepEqual(powerReport({}).warnings, []);
  assert.equal(powerTone(11), "bad");
  assert.equal(powerTone(10), "");
  assert.equal(powerTone(undefined), "");
});
