import test from "node:test";
import assert from "node:assert/strict";
import { MissionEvents, deriveEvents, eventsToCsv, filterRows, applyCutoff, currentIndex, fmtEventTime } from "../app/status/mission_events.js";

const near = (a, b, e = 1e-9) => assert.ok(Math.abs(a - b) < e, `${a} vs ${b}`);
const ms = (t, tm, phase, extra = {}) => ({ type: "MissionState", src: "usb", t, f: { t_mission: tm, mission_phase: phase, mission_mode: "Running", mission_index: 0, ...extra } });
const vs = (t, f, src = "usb") => ({ type: "VehicleStatus", src, t, f });
const con = (t, text, src = "usb") => ({ type: "ConsoleText", src, t, f: { text } });
const gs = (t, kind, detail) => ({ type: "GsEvent", src: "gs", t, f: { kind, detail } });
const texts = (r) => r.events.map((e) => e.text);

/** A whole mission at 2 Hz: countdown -5..0 (Idle), Hover 0..4, Ascent 0..4 (the step clock restarts), Completed. */
function mission(t0, extra = []) {
  const out = [];
  for (let i = 0; i <= 10; i++) out.push(ms(t0 - 5 + i * 0.5, -5 + i * 0.5, "Idle"));
  for (let i = 1; i <= 8; i++) out.push(ms(t0 + i * 0.5, i * 0.5, "Hover"));
  for (let i = 0; i <= 8; i++) out.push(ms(t0 + 4.5 + i * 0.5, i * 0.5, "Ascent"));
  out.push(ms(t0 + 9, 4.5, "Completed"));
  out.push(ms(t0 + 9.5, null, "Idle", { mission_mode: "Finished" }));
  return [...out, ...extra];
}

test("one mission: countdown start, T0, phase changes, end, all in mission time", () => {
  const r = deriveEvents(mission(1000));
  assert.deepEqual(texts(r), [
    "Mission countdown started",
    "Mission begin (T0)",
    "Mission phase: Idle → Hover",
    "Mission phase: Hover → Ascent",
    "Mission phase: Ascent → Completed",
    "Mission end: Completed",
    "Mission phase: Completed → Idle",
    "Mission mode: Running → Finished",
  ]);
  const by = Object.fromEntries(r.events.map((e) => [e.text, e]));
  near(by["Mission countdown started"].tm, -5);
  near(by["Mission begin (T0)"].tm, 0);
  near(by["Mission phase: Hover → Ascent"].tm, 4.5);
  near(by["Mission phase: Ascent → Completed"].tm, 9); // step clock restarted at Ascent, mission time did not
  assert.equal(r.rows[0].kind, "header");
  assert.match(r.rows[0].text, /^Mission 1, begun at \d\d:\d\d:\d\d$/);
  assert.equal(r.rows.filter((x) => x.kind === "header").length, 1);
  assert.equal(by["Mission begin (T0)"].wall, 1000);
});

test("two missions in one recording give two sections with their own T0", () => {
  const r = deriveEvents([...mission(1000), ...mission(5000)]);
  const heads = r.rows.filter((x) => x.kind === "header");
  assert.equal(heads.length, 2);
  assert.match(heads[1].text, /^Mission 2, begun at/);
  const begins = r.events.filter((e) => e.text === "Mission begin (T0)");
  assert.equal(begins.length, 2);
  near(begins[1].tm, 0);
  assert.equal(begins[1].section, 1);
  // header of mission 2 sits right before its countdown event, after all of mission 1
  const i = r.rows.indexOf(heads[1]);
  assert.equal(r.rows[i + 1].text, "Mission countdown started");
  assert.ok(r.rows.slice(0, i).every((x) => x.t < 5000));
  near(r.rows[i + 1].tm, -5);
});

test("events before the first mission have no mission time, later ones keep counting after the end", () => {
  const r = deriveEvents([con(900, "[node 1 WARNING] Something odd"), ...mission(1000), con(1020, "[node 1 WARNING] after the end")]);
  assert.equal(r.events[0].tm, null);
  assert.equal(r.events[0].section, -1);
  assert.equal(r.rows[0].kind, "event"); // no header before the first mission
  const last = r.events.at(-1);
  assert.equal(last.text, "after the end");
  near(last.tm, 20);
});

test("vehicle status: mode, armed, motors, simulation, failsafe and failure bits, deduped over two sources", () => {
  const base = { mode: "Ready", armed: false, motors_enabled: false, simulation: false, failsafe: false, failure_bits: 0 };
  const r = deriveEvents([
    vs(10, base),
    vs(10.2, base, "link"),
    vs(11, { ...base, mode: "Running", armed: true }),
    vs(11.4, { ...base, mode: "Running", armed: true }, "link"), // the same change from the Router: one event
    vs(12, { ...base, mode: "Running", armed: true, motors_enabled: true, simulation: true }),
    vs(13, { ...base, mode: "Running", armed: true, motors_enabled: true, simulation: true, failsafe: true, failure_bits: 1 << 14 }),
    vs(14, { ...base, mode: "Failure", armed: false, motors_enabled: false, simulation: true, failsafe: true, failure_bits: 1 << 14 }),
    vs(15, { ...base, mode: "Ready", armed: false, simulation: false, failsafe: false, failure_bits: 0 }),
  ]);
  assert.deepEqual(texts(r), [
    "Vehicle mode: Ready (state at start of data)",
    "Vehicle mode: Ready → Running",
    "Armed",
    "Motors enabled (gate open)",
    "Simulation on",
    "Failsafe set",
    "Failure set: GS failsafe",
    "Vehicle mode: Running → Failure",
    "Disarmed",
    "Motors disabled (gate closed)",
    "Vehicle mode: Failure → Ready",
    "Simulation off",
    "Failsafe cleared",
    "Failure cleared: GS failsafe",
  ]);
  assert.equal(r.events.find((e) => e.text === "Failsafe set").tone, "bad");
  assert.ok(r.events.every((e) => e.type === "VehicleStatus" && e.cat === "vehicle"));
});

test("repeated identical states make no events, order is by time", () => {
  const s = { mode: "Ready", armed: true, failure_bits: 0 };
  const pk = [];
  for (let i = 0; i < 50; i++) pk.push(vs(100 + i, s));
  pk.push(vs(90, { mode: "Ready", armed: false })); // fed unsorted: feedAll sorts
  const r = deriveEvents(pk);
  assert.deepEqual(texts(r), ["Vehicle mode: Ready (state at start of data)", "Armed"]);
  assert.ok(r.events[0].t < r.events[1].t);
  // the same through the incremental path
  const live = new MissionEvents();
  [...pk].sort((a, b) => a.t - b.t).forEach((p) => live.feed(p));
  assert.deepEqual(texts(live.build()), texts(r));
});

test("armed at the start of the data is reported, disarmed baseline is not", () => {
  const r = deriveEvents([vs(1, { mode: "Running", armed: true, motors_enabled: false, failsafe: false })]);
  assert.deepEqual(texts(r), ["Vehicle mode: Running (state at start of data)", "Armed (state at start of data)"]);
});

test("commands: GS CmdState changes, vehicle applied, GO refused, flags", () => {
  const cs = (t, f) => ({ type: "CmdState", src: "gs", t, f: { session: 0xabc, go: 0, profile: 2, vehicle_mode: "REAL", motors: 0, reset_button: 0, failsafe: 0, pocket_live: true, input_virtual: false, ...f } });
  const r = deriveEvents([
    cs(1, {}),
    cs(2, { motors: 1 }),
    cs(3, { motors: 1, go: 1 }),
    cs(3.04, { motors: 1, go: 1 }),
    cs(4, { motors: 1, go: 1, failsafe: 1 }),
    cs(5, { motors: 0, go: 0, failsafe: 0, vehicle_mode: "SIMULATION", pocket_live: false }),
    { type: "CmdApplied", src: "usb", t: 3.1, f: { go: 0, motors: 1 } },
    { type: "CmdApplied", src: "usb", t: 3.3, f: { go: 1, motors: 1 } },
    vs(3.2, { go_refusal: 0 }),
    vs(6, { go_refusal: 1, ctrl_flags: 1 << 1 }),
    gs(7, "link_warning", "ABORT sent by the ground station"),
  ]);
  const cmd = r.events.filter((e) => e.cat === "command").map((e) => e.text);
  assert.deepEqual(cmd, [
    "Command session 0xabc started (state at start of data)",
    "Command mission profile 2 (SB) (state at start of data)",
    "Command motors armed (SD)",
    "Command GO (SA)",
    "Vehicle applied motors enabled (SD) (state at start of data)",
    "Vehicle applied GO (SA)",
    "Command GS FAILSAFE requested",
    "Command go off (SA)",
    "Command mode SIMULATION (SC)",
    "Command motors disarmed (SD)",
    "Command GS failsafe request cleared",
    "Pocket stream lost",
    "Go refused: vehicle not Ready (or calibration invalid)",
    "ABORT sent by the ground station",
  ]);
  assert.ok(texts(r).includes("Vehicle flag set: ABORT latched (state at start of data)"));
  assert.equal(r.events.find((e) => e.text === "Vehicle flag set: ABORT latched (state at start of data)").cat, "vehicle");
  assert.equal(r.events.filter((e) => e.text === "Command GO (SA)").length, 1);
});

test("GS events: link and request lines; console patterns; WARNING lines; repeats folded", () => {
  const r = deriveEvents([
    gs(1, "source_up", "usb: connected /dev/ttyACM0"),
    gs(2, "request_ack", "REQUEST_ACK CALIB_GYRO_START id 7: DONE detail 0 (update)"),
    gs(3, "source_down", "usb: disconnected: closed"),
    gs(4, "request_retry", "x"),
    con(5, "[node 1 INFO] Started bellyflop kick, target q 0.1 0.2 0.3 0.4"),
    con(6, "[node 1 INFO] Bellyflop kick finished (predicted): remaining 1.0 deg, pitch rate 2.0 deg/s, 0.300 s"),
    con(7, "[node 1 INFO] RTH: descent starts (alt), vehicle 1.20 m from the return point"),
    con(8, "[node 1 WARNING] Landing declared at 0.12 m altitude (cue tof)"),
    con(9, "[node 2 WARNING] Router link lost"),
    con(10, "[node 1 INFO] boring info line"),
    con(11, "[node 1 WARNING] WARNING calibration invalid, Arm and MissionBegin are refused (repeats every 10 s): gyro: not found in memory, accel: ok, mag: ok"),
    con(21, "[node 1 WARNING] WARNING calibration invalid, Arm and MissionBegin are refused (repeats every 10 s): gyro: not found in memory, accel: ok, mag: ok"),
    con(21.2, "[node 1 WARNING] WARNING calibration invalid, Arm and MissionBegin are refused (repeats every 10 s): gyro: not found in memory, accel: ok, mag: ok", "link"),
    { type: "Note", src: "gs", t: 30, f: { text: "wind picking up" } },
    gs(31, "rec_start", "manual"),
  ]);
  const t = texts(r);
  assert.ok(t.includes("Source up: usb: connected /dev/ttyACM0"));
  assert.ok(t.includes("Source down: usb: disconnected: closed"));
  assert.ok(t.includes("REQUEST_ACK CALIB_GYRO_START id 7: DONE detail 0 (update)"));
  assert.ok(!t.includes("x"));
  assert.ok(t.includes("Bellyflop kick started"));
  assert.ok(t.includes("Bellyflop kick finished (predicted)"));
  assert.ok(t.some((x) => x.startsWith("RTH descent starts")));
  assert.ok(t.some((x) => x.startsWith("Landing declared at 0.12 m")));
  assert.ok(t.includes("node 2: Router link lost"));
  assert.ok(!t.some((x) => x.includes("boring")));
  const calib = r.events.filter((e) => e.text.startsWith("Calibration invalid"));
  assert.equal(calib.length, 1);
  assert.equal(calib[0].count, 3);
  assert.ok(t.includes("Note: wind picking up"));
  assert.ok(t.includes("Recording started (manual)"));
  assert.equal(r.events.find((e) => e.text === "Source down: usb: disconnected: closed").cat, "link");
  assert.equal(r.events.find((e) => e.text.startsWith("Landing declared")).tone, "warn");
  assert.ok(r.events.every((e) => ["mission", "vehicle", "command", "link", "log"].includes(e.cat)));
});

test("the whole recording at once equals the stream fed live, and a gap ends the mission", () => {
  const pk = [...mission(1000), con(1010, "[node 1 WARNING] hello"), ...mission(2000)].sort((a, b) => a.t - b.t);
  const all = deriveEvents(pk);
  const live = new MissionEvents();
  for (const p of pk) live.feed(p);
  const a = live.build();
  assert.deepEqual(a.events.map((e) => [e.t, e.tm, e.text]), all.events.map((e) => [e.t, e.tm, e.text]));
  assert.equal(a.rows.length, all.rows.length);
  assert.ok(live.lastT >= 2009);
});

test("countdown in progress: T- stamps use the projected T0 and later events get exact times", () => {
  const live = new MissionEvents();
  for (let i = 0; i <= 6; i++) live.feed(ms(1000 + i * 0.5, -10 + i * 0.5, "Idle"));
  live.feed(con(1003, "[node 1 WARNING] mid countdown"));
  let r = live.build();
  near(r.events.at(-1).tm, -7); // T0 projected at 1010
  assert.equal(r.events.some((e) => e.text === "Mission begin (T0)"), false);
  for (let i = 7; i <= 22; i++) live.feed(ms(1000 + i * 0.5, -10 + i * 0.5, i < 21 ? "Idle" : "Hover"));
  r = live.build();
  assert.ok(r.events.some((e) => e.text === "Mission begin (T0)"));
  near(r.events.find((e) => e.text === "mid countdown").tm, -7);
});

test("CSV, filter, current index, time format", () => {
  const r = deriveEvents(mission(1000, [con(1002, '[node 1 WARNING] =HYPERLINK("x"), "q"')]));
  const csv = eventsToCsv(r.events);
  const lines = csv.trim().split("\r\n");
  assert.equal(lines[0], "mission_time_s,mission_time,wall_time_utc,category,text,mission,source_type");
  assert.equal(lines.length, r.events.length + 1);
  assert.match(lines[1], /^-5\.000,T-5\.0,1970-01-01T00:16:35\.000Z,mission,Mission countdown started,1,MissionState$/);
  assert.ok(lines.some((l) => l.includes(`"'=HYPERLINK(""x""), ""q"""`)), "formula prefix and quoting");
  const shown = filterRows(r.rows, { mission: false });
  assert.ok(shown.every((x) => x.kind === "header" || x.cat !== "mission"));
  assert.equal(shown.filter((x) => x.kind === "event").length, 1);
  assert.equal(currentIndex(r.rows, 900), -1);
  const i = currentIndex(r.rows, 1000);
  assert.equal(r.rows[i].text, "Mission begin (T0)");
  assert.equal(r.rows[currentIndex(r.rows, 99999)].kind, "event");
  assert.equal(fmtEventTime(null), "—");
  assert.equal(fmtEventTime(0), "T0");
  assert.equal(fmtEventTime(-5.04), "T-5.0");
  assert.equal(fmtEventTime(12.34), "T+12.3");
});

test("clear cutoff hides events up to it, keeps the mission header of later events", () => {
  const r = deriveEvents(mission(1000, [con(1001, "[node 1 WARNING] a"), con(1003, "[node 1 WARNING] b")]));
  const evs = r.rows.filter((x) => x.kind === "event");
  assert.deepEqual(applyCutoff(r.rows, null), { rows: r.rows, hidden: 0 });
  const all = applyCutoff(r.rows, 0);
  assert.equal(all.hidden, 0);
  assert.equal(all.rows.length, r.rows.length);
  const cut = applyCutoff(r.rows, 1002);
  assert.equal(cut.hidden, evs.filter((x) => x.t <= 1002).length);
  const kept = cut.rows.filter((x) => x.kind === "event");
  assert.ok(kept.length > 0 && kept.every((x) => x.t > 1002));
  assert.equal(cut.rows[0].kind, "header", "section header kept for the remaining events");
  assert.equal(kept[0].tm, evs.find((x) => x.t === kept[0].t).tm, "mission time untouched");
  const none = applyCutoff(r.rows, 1e9);
  assert.equal(none.rows.length, 0, "an empty section loses its header");
  assert.equal(none.hidden, evs.length);
});

test("a restart without Completed gives a new section 'Mission 2' with its own mission time and an end reason", () => {
  const ms = (t, tm, phase, mode) => ({ type: "MissionState", src: "usb", t, f: { t_mission: tm, mission_phase: phase, mission_mode: mode } });
  const pk = [];
  for (let i = 0; i < 8; i++) pk.push(ms(1000 + i, -3 + i, "Hover", "Startup"));
  for (let i = 0; i < 20; i++) pk.push(ms(1008 + i, i, "Ascent", "Running"));
  pk.push(ms(1028, 20, "Ascent", "Finished"));
  pk.push(ms(1029, -4, "Hover", "Initialisation"));
  pk.push(ms(1032, -1, "Hover", "Startup"));
  pk.push(ms(1034, 1, "Hover", "Running"));
  const r = deriveEvents(pk);
  const heads = r.rows.filter((x) => x.kind === "header");
  assert.equal(heads.length, 2);
  assert.match(heads[1].text, /^Mission 2, begun at/);
  assert.equal(r.missions[0].endReason, "restarted");
  assert.ok(r.events.some((e) => e.text === "Mission end: a new mission started"));
  const begins = r.events.filter((e) => e.text === "Mission begin (T0)");
  assert.equal(begins.length, 2);
  near(begins[1].tm, 0);
  assert.equal(begins[1].section, 1);
});
