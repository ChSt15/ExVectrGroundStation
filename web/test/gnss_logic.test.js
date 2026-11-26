import test from "node:test";
import assert from "node:assert/strict";
import { ALTCHECK_DEFAULTS, AltCheck, fixName, gnssUsable, groundSpeed, gnssVelNwu, gnssNwuFromHome, homeLine, plotGnssAltitude } from "../app/gnss/gnss_logic.js";
import { CalloutEngine } from "../app/callouts/engine.js";
import { DEFAULTS, mergeSettings } from "../app/callouts/settings.js";
import { SIGNAL_BY_ID, SignalFeeder } from "../app/plot/catalogue.js";

const gps = (alt, t, extra = {}) => ({ type: "Gps", src: "radio", t, f: { alt, fix_type: 3, ...extra } });
const pos = (z, t) => ({ type: "Position", src: "radio", t, f: { pos: [0, 0, z], vel: [0, 0, 0] } });
const home = (alt, epoch = 1, extra = {}) => ({ type: "HomePosition", src: "radio", t: 1, f: { alt_valid: true, alt, epoch, ...extra } });
const status = (epoch, t = 1) => ({ type: "VehicleStatus", src: "usb", t, f: { home_epoch: epoch } });

test("helpers: NWU velocity, ground speed, fix names, usable altitude", () => {
  assert.deepEqual(gnssVelNwu([1, 2, 3]), [1, 2, 3]);
  assert.deepEqual(gnssVelNwu([0, -5, -2]), [0, -5, -2]); // moving east: W negative; descending: U negative
  assert.equal(gnssVelNwu([1, null, 3]), null);
  assert.equal(gnssVelNwu(undefined), null);
  assert.equal(groundSpeed([3, 4, 9]), 5);
  assert.equal(groundSpeed(undefined), null);
  assert.equal(fixName(3), "3D");
  assert.equal(fixName(undefined), "—");
  assert.equal(gnssUsable({ alt: 100, fix_type: 3 }), true);
  assert.equal(gnssUsable({ alt: 100 }), true); // fix type not known (info not received yet): the altitude decides
  assert.equal(gnssUsable({ alt: 100, fix_type: 2 }), false);
  assert.equal(gnssUsable({ fix_type: 3 }), false);
  assert.equal(gnssUsable(undefined), false);
});

test("reference from the first GNSS altitudes on the pad, difference and threshold with hold time", () => {
  const c = new AltCheck(() => ({}));
  assert.deepEqual([ALTCHECK_DEFAULTS.limit_m, ALTCHECK_DEFAULTS.hold_s], [5, 1]);
  let r = c.update({ now: 10, position: pos(0, 10), gps: gps(300, 10) });
  assert.equal(r.state, "ok");
  assert.equal(r.ref, 300);
  assert.equal(r.refSource, "gnss");
  // flying: estimate 40 m, GNSS 338 m (38 m above the start): difference +2 m
  r = c.update({ now: 11, position: pos(40, 11), gps: gps(338, 11) });
  assert.equal(r.state, "ok");
  assert.equal(r.gnssRel, 38);
  assert.equal(r.diff, 2);
  assert.equal(r.ref, 300); // frozen once off the pad: the mean stops
  // 8 m apart: over the limit, but not for 1 s yet
  r = c.update({ now: 12, position: pos(50, 12), gps: gps(342, 12) });
  assert.equal(r.state, "over");
  assert.equal(r.mismatch, false);
  r = c.update({ now: 12.5, position: pos(50, 12.5), gps: gps(342, 12.5) });
  assert.equal(r.state, "over");
  r = c.update({ now: 13.1, position: pos(50, 13.1), gps: gps(342, 13.1) });
  assert.equal(r.state, "mismatch");
  assert.equal(r.mismatch, true);
  assert.equal(r.diff, 8);
  // back inside the limit but within the hysteresis band (limit - 1 m): stays in mismatch
  r = c.update({ now: 14, position: pos(50, 14), gps: gps(346, 14) });
  assert.equal(r.diff, 4);
  assert.equal(r.state, "mismatch");
  r = c.update({ now: 15, position: pos(50, 15), gps: gps(349, 15) });
  assert.equal(r.state, "ok");
  assert.equal(r.mismatch, false);
});

test("a short excursion below hold_s never warns", () => {
  const c = new AltCheck(() => ({ hold_s: 1 }));
  c.update({ now: 1, position: pos(0, 1), gps: gps(100, 1) });
  c.update({ now: 2, position: pos(30, 2), gps: gps(130, 2) });
  assert.equal(c.update({ now: 3, position: pos(40, 3), gps: gps(130, 3) }).state, "over"); // 10 m
  assert.equal(c.update({ now: 3.5, position: pos(31, 3.5), gps: gps(130.5, 3.5) }).state, "ok");
  assert.equal(c.update({ now: 4.6, position: pos(31, 4.6), gps: gps(130.5, 4.6) }).state, "ok");
});

test("the threshold and hold time are configurable", () => {
  const c = new AltCheck(() => ({ limit_m: 1, hold_s: 0, hysteresis_m: 0.2 }));
  c.update({ now: 1, position: pos(0, 1), gps: gps(100, 1) });
  c.update({ now: 2, position: pos(30, 2), gps: gps(130, 2) });
  const r = c.update({ now: 3, position: pos(32, 3), gps: gps(130, 3) }); // 2 m > 1 m, hold 0
  assert.equal(r.state, "mismatch");
  assert.equal(r.limit, 1);
});

test("home altitude is the reference when known, a stale home copy (other epoch) is not used", () => {
  const c = new AltCheck(() => ({}));
  let r = c.update({ now: 5, position: pos(10, 5), gps: gps(512, 5), home: home(500, 2), status: status(2) });
  assert.equal(r.refSource, "home");
  assert.equal(r.gnssRel, 12);
  assert.equal(r.diff, -2);
  // the status reports epoch 3, the copy says 2: stale, not used, and there is no pad reference (estimate is 10 m)
  r = c.update({ now: 6, position: pos(10, 6), gps: gps(512, 6), home: home(500, 2), status: status(3, 6) });
  assert.equal(r.state, "noref");
  assert.equal(r.refSource, null);
});

test("connecting in flight gives no reference instead of a guessed one", () => {
  const c = new AltCheck(() => ({}));
  const r = c.update({ now: 1, position: pos(80, 1), gps: gps(380, 1) });
  assert.equal(r.state, "noref");
  assert.equal(r.gnssRel, null);
  assert.equal(r.gnssAbs, 380);
});

test("a new home epoch drops the start reference taken from GNSS", () => {
  const c = new AltCheck(() => ({}));
  c.update({ now: 1, position: pos(0, 1), gps: gps(100, 1), status: status(1) });
  assert.equal(c.update({ now: 2, position: pos(0, 2), gps: gps(100, 2), status: status(1, 2) }).ref, 100);
  const r = c.update({ now: 3, position: pos(0, 3), gps: gps(130, 3), status: status(2, 3) }); // origin moved: new reference
  assert.equal(r.ref, 130);
});

test("missing, stale and no-fix GNSS is not judged and clears a mismatch", () => {
  const c = new AltCheck(() => ({ hold_s: 0 }));
  assert.equal(c.update({ now: 1, position: pos(0, 1) }).state, "nodata");
  assert.equal(c.update({ now: 1, gps: gps(100, 1) }).state, "nodata");
  c.update({ now: 1, position: pos(0, 1), gps: gps(100, 1) });
  c.update({ now: 2, position: pos(30, 2), gps: gps(100, 2) });
  assert.equal(c.update({ now: 3, position: pos(30, 3), gps: gps(100, 3) }).state, "mismatch");
  // GNSS stops: 3.5 s without data
  const stale = c.update({ now: 6.5, position: pos(30, 6.5), gps: gps(100, 3) });
  assert.equal(stale.state, "stale");
  assert.equal(stale.mismatch, false);
  const nofix = c.update({ now: 7, position: pos(30, 7), gps: { type: "Gps", t: 7, f: { fix_type: 1 } } });
  assert.equal(nofix.state, "nofix");
  // data back: the difference timer starts again (hold 0 fires at once)
  assert.equal(c.update({ now: 8, position: pos(30, 8), gps: gps(100, 8) }).state, "mismatch");
});

test("a time jump backwards (replay seek) resets the check", () => {
  const c = new AltCheck(() => ({}));
  c.update({ now: 100, position: pos(0, 100), gps: gps(100, 100) });
  assert.equal(c.ref.value, 100);
  const r = c.update({ now: 10, position: pos(0, 10), gps: gps(250, 10) });
  assert.equal(r.ref, 250);
});

test("graph signals: GNSS altitude, relative altitude and the difference", () => {
  const fd = new SignalFeeder(() => ({ vals: [], push(t, v) { this.vals.push(v); }, clear() { this.vals = []; } }));
  fd.push("Position", 1, { pos: [0, 0, 0] });
  fd.push("Gps", 1, { alt: 200, fix_type: 3 });
  fd.push("Position", 2, { pos: [0, 0, 30] });
  fd.push("Gps", 2, { alt: 234, fix_type: 3 });
  assert.deepEqual(fd.ring("gnss.alt").vals, [200, 234]);
  assert.deepEqual(fd.ring("gnss.alt_rel").vals, [0, 34]);
  assert.deepEqual(fd.ring("gnss.alt_diff").vals, [0, -4]);
  // no fix: nothing plotted
  fd.push("Gps", 3, { fix_type: 1 });
  assert.equal(fd.ring("gnss.alt_rel").vals.length, 2);
  // a HOME_POSITION altitude takes over as the reference
  fd.push("HomePosition", 3, { alt_valid: true, alt: 190, epoch: 1 });
  fd.push("Gps", 4, { alt: 234, fix_type: 3 });
  assert.equal(fd.ring("gnss.alt_rel").vals[2], 44);
  assert.ok(SIGNAL_BY_ID.has("gnss.alt_diff"));
  assert.deepEqual(plotGnssAltitude({ fix_type: 3 }, {}), { rel: undefined, est: undefined });
});

test("callout engine: 'Altitude mismatch' once per excursion, off through the category switch, clears with the mismatch", () => {
  const settings = mergeSettings(DEFAULTS, { altcheck: { limit_m: 5, hold_s: 1, hysteresis_m: 1 } });
  assert.equal(settings.categories.altcheck, false); // optional: audio off by default
  const eng = new CalloutEngine(() => settings);
  const run = (now, z, alt) => eng.evaluate({ view: "live", now, status: undefined, mission: undefined, position: pos(z, now), gps: gps(alt, now), home: undefined, sources: [] });
  run(1, 0, 100);
  run(2, 20, 120);
  let r = run(3, 30, 120);
  assert.equal(r.events.filter((e) => e.id === "altcheck.mismatch").length, 0); // over for less than hold_s
  r = run(4.2, 30, 120);
  const ev = r.events.find((e) => e.id === "altcheck.mismatch");
  assert.ok(ev);
  assert.equal(ev.text, "Altitude mismatch");
  assert.equal(ev.category, "altcheck");
  assert.equal(run(5, 30, 120).events.filter((e) => e.id === "altcheck.mismatch").length, 0); // once
  r = run(6, 30, 129.5);
  assert.ok(r.clears.includes("altcheck"));
  // clamped, validated settings
  assert.equal(mergeSettings(DEFAULTS, { altcheck: { limit_m: -3, hold_s: "x" } }).altcheck.limit_m, 0.1);
  assert.equal(mergeSettings(DEFAULTS, { altcheck: { limit_m: 8 } }).altcheck.limit_m, 8);
});

const RAD = Math.PI / 180;
const H0 = { latlon_valid: true, alt_valid: true, lat: 51.5 * RAD, lon: 10 * RAD, alt: 100, epoch: 1 };

test("GNSS metres relative to home: NWU signs, scale at 51.5 deg, altitude", () => {
  const at = (dlat, dlon, alt = 100) => gnssNwuFromHome({ lat: H0.lat + dlat * RAD, lon: H0.lon + dlon * RAD, alt, fix_type: 3 }, H0);
  assert.deepEqual(at(0, 0), [0, -0, 0]);
  const [n, w, u] = at(1e-4, 0, 112.5);
  assert.ok(Math.abs(n - 11.15) < 0.05 && w === -0 && u === 12.5, `${n} ${w} ${u}`); // north is positive N
  const [n2, w2] = at(0, 1e-4); // east is negative W: 1e-4 deg of longitude is 6.93 m at 51.5 deg
  assert.ok(n2 === 0 && Math.abs(w2 + 6.93) < 0.05, `${n2} ${w2}`);
  const [, w3] = at(0, -1e-4); // west is positive W
  assert.ok(Math.abs(w3 - 6.93) < 0.05);
  assert.ok(Math.abs(at(-1e-4, 0)[0] + 11.15) < 0.05); // south is negative N
  const [, , down] = at(0, 0, 90);
  assert.equal(down, -10);
});

test("GNSS metres relative to home: nothing is invented without a home or a fix", () => {
  const g = { lat: H0.lat, lon: H0.lon, alt: 100, fix_type: 3 };
  assert.equal(gnssNwuFromHome(g, undefined), null);
  assert.equal(gnssNwuFromHome(g, { ...H0, latlon_valid: false }), null);
  assert.equal(gnssNwuFromHome(g, { ...H0, lat: null }), null);
  assert.equal(gnssNwuFromHome({ ...g, lat: null }, H0), null);
  assert.equal(gnssNwuFromHome({ ...g, fix_type: 2 }, H0), null);
  assert.deepEqual(gnssNwuFromHome({ lat: g.lat, lon: g.lon, alt: 100 }, H0), [0, -0, 0]); // fix type not known yet
  const noAlt = gnssNwuFromHome(g, { ...H0, alt_valid: false });
  assert.deepEqual(noAlt.slice(0, 2), [0, -0]);
  assert.ok(Number.isNaN(noAlt[2])); // horizontal known, no vertical reference
  assert.ok(Number.isNaN(gnssNwuFromHome({ ...g, alt: null }, H0)[2]));
});

test("home line shows the reported values and says so when no home was received", () => {
  assert.match(homeLine(undefined).text, /no home position/);
  const l = homeLine({ ...H0, simulation: true });
  assert.match(l.text, /51\.5000000°, 10\.0000000°, 100\.0 m \(simulation\), epoch 1/);
  assert.match(homeLine({ ...H0, latlon_valid: false, alt_valid: false }).text, /no lat\/lon, no altitude/);
});

test("tuning signals: GNSS N/W/U relative to the latest HomePosition, none without a home", () => {
  const f = new SignalFeeder(() => ({ push() {}, clear() {} }));
  const out = {};
  const sigs = ["gnss.home.N", "gnss.home.W", "gnss.home.U", "gnss.home.h"].map((id) => SIGNAL_BY_ID.get(id));
  assert.ok(sigs.every(Boolean));
  const g = { lat: H0.lat + 1e-4 * RAD, lon: H0.lon + 1e-4 * RAD, alt: 105, fix_type: 3 };
  for (const x of sigs) assert.equal(x.extract(g, f.last), undefined); // no home yet
  f.push("HomePosition", 1, H0);
  for (const x of sigs) out[x.id] = x.extract(g, f.last);
  assert.ok(Math.abs(out["gnss.home.N"] - 11.15) < 0.05);
  assert.ok(Math.abs(out["gnss.home.W"] + 6.93) < 0.05);
  assert.equal(out["gnss.home.U"], 5);
  assert.ok(Math.abs(out["gnss.home.h"] - Math.hypot(11.15, 6.93)) < 0.1);
});
