import test from "node:test";
import assert from "node:assert/strict";
import {
  HoldTimer, MomentaryHold, POCKET_WARNING, Throttle, controlEnabled, cyclePosition, describeRadio, needsReleasePrompt,
  nextToggle, positionFromUs, showPadlock, sliderFromPointer, stateFromChannels, switchPosFromFraction, unlockRemaining,
} from "../app/radio/radio_logic.js";

/** Fake clock with timeouts and intervals, advanced explicitly. */
function fakeTimers() {
  let now = 0;
  let id = 0;
  const jobs = new Map();
  const t = {
    now: () => now,
    setTimeout: (fn, ms) => (jobs.set(++id, { fn, at: now + ms, every: null }), id),
    clearTimeout: (i) => jobs.delete(i),
    setInterval: (fn, ms) => (jobs.set(++id, { fn, at: now + ms, every: ms }), id),
    clearInterval: (i) => jobs.delete(i),
    advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...jobs.entries()].filter(([, j]) => j.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        const [i, j] = due;
        now = j.at;
        if (j.every) j.at += j.every;
        else jobs.delete(i);
        j.fn();
      }
      now = end;
    },
    pending: () => jobs.size,
  };
  return t;
}

const CONTROLS = [
  { id: "SA", kind: "toggle2", channel: 5, values_us: [1000, 2000] },
  { id: "SB", kind: "switch3", channel: 6, values_us: [1000, 1500, 2000] },
  { id: "SD", kind: "toggle2", channel: 8, values_us: [1000, 2000], lock: true, lock_timeout_s: 60 },
  { id: "S1", kind: "slider", channel: 10, range_us: [1000, 2000] },
];

test("positions map back from microseconds like the core does", () => {
  assert.equal(positionFromUs(CONTROLS[1], 1400), 1);
  assert.equal(positionFromUs(CONTROLS[1], 1900), 2);
  assert.equal(positionFromUs(CONTROLS[3], 1250), 0.25);
  assert.equal(positionFromUs(CONTROLS[3], 5000), 1);
  const ch = new Array(16).fill(1500);
  ch[4] = 2000; ch[5] = 1500; ch[7] = 2000; ch[9] = 1250;
  assert.deepEqual(stateFromChannels(CONTROLS, ch), { SA: 1, SB: 1, SD: 1, S1: 0.25 });
  assert.deepEqual(stateFromChannels(CONTROLS, [1500]), {}, "channels beyond the vector are not shown");
});

test("toggle, switch zones, cycle and slider helpers", () => {
  assert.equal(nextToggle(0), 1);
  assert.equal(nextToggle(1), 0);
  assert.equal(switchPosFromFraction(0.0, 3), 2, "top zone is the highest position");
  assert.equal(switchPosFromFraction(0.5, 3), 1);
  assert.equal(switchPosFromFraction(0.99, 3), 0);
  assert.equal(switchPosFromFraction(5, 3), 0, "clamped");
  assert.equal(cyclePosition(2, 3), 0);
  assert.equal(sliderFromPointer(100, 200, 150), 0.25);
  assert.equal(sliderFromPointer(100, 200, 0), 0);
  assert.equal(sliderFromPointer(100, 0, 150), 0);
});

const rc = (over = {}) => ({ mode: "idle", lease: null, holder_is_you: false, target: null, ...over });
const base = { apiState: "open", view: "live", canControl: true, cfg: { send_rate_hz: 25 } };

test("banners follow the mode the core reports", () => {
  let b = describeRadio({ ...base, rc: rc() });
  assert.equal(b.tone, "idle");
  assert.equal(b.role, "idle");
  assert.ok(b.canTake && !b.interactive && !b.canRelease);
  b = describeRadio({ ...base, rc: rc({ mode: "master", target: "usb", lease: { holder: "gui" }, holder_is_you: true }) });
  assert.equal(b.tone, "controlling");
  assert.equal(b.roleText, "MASTER");
  assert.match(b.text, /MASTER · sending to usb · 25 Hz/);
  assert.ok(b.interactive && b.canRelease && !b.canTake && b.warning === null);
  b = describeRadio({ ...base, rc: rc({ mode: "master", target: "usb", lease: { holder: "exgs" } }) });
  assert.match(b.text, /MASTER \(exgs\)/);
  assert.ok(!b.interactive && !b.canRelease && !b.canTake);
  b = describeRadio({ ...base, rc: rc({ mode: "slave", pocket_active: true }) });
  assert.ok(b.tone === "mirror" && b.role === "slave" && b.roleText === "SLAVE" && !b.interactive && b.canTake && !b.canRelease);
  assert.equal(b.warning, null, "no warning in slave");
  b = describeRadio({ ...base, canControl: false, rc: rc({ mode: "slave" }) });
  assert.ok(!b.canTake, "a view-only client cannot switch to master");
  b = describeRadio({ ...base, rc: rc({ lease: { holder: "gui" }, holder_is_you: true }) });
  assert.ok(b.tone === "warn" && !b.interactive && b.canRelease, "lease without an RC-capable source: not sending");
});

test("master with the Pocket also active shows the warning and stays operable", () => {
  let b = describeRadio({ ...base, rc: rc({ mode: "master", pocket_active: true, target: "usb, link", lease: { holder: "gui" }, holder_is_you: true }) });
  assert.equal(b.warning, POCKET_WARNING);
  assert.match(b.warning, /Pocket also active: the radio rejects ground-station controls/);
  assert.ok(b.interactive && b.canRelease, "master is allowed while the Pocket is active");
  b = describeRadio({ ...base, rc: rc({ mode: "master", pocket_active: true, lease: { holder: "exgs" } }) });
  assert.equal(b.warning, POCKET_WARNING, "shown to every client");
  b = describeRadio({ ...base, rc: rc({ mode: "master", pocket_active: false, lease: { holder: "gui" }, holder_is_you: true }) });
  assert.equal(b.warning, null);
});

test("read-only situations: disconnected, replay, view-only client, no RC", () => {
  let b = describeRadio({ ...base, apiState: "closed", rc: rc({ mode: "master", holder_is_you: true, lease: {} }) });
  assert.ok(b.tone === "bad" && !b.interactive && !b.canTake && !b.canRelease);
  b = describeRadio({ ...base, view: "replay", rc: rc({ mode: "master", holder_is_you: true, lease: {} }) });
  assert.ok(b.tone === "replay" && !b.interactive && !b.canTake);
  b = describeRadio({ ...base, canControl: false, rc: rc() });
  assert.ok(!b.canTake, "a client off the control hosts cannot take control");
  b = describeRadio({ ...base, rc: null });
  assert.ok(!b.interactive && !b.canTake);
});

test("locked controls: padlock and enablement", () => {
  const live = describeRadio({ ...base, rc: rc({ mode: "master", holder_is_you: true, lease: {}, target: "usb" }) });
  const locked = { locked: true, remaining_s: null };
  const open = { locked: false, remaining_s: 42 };
  assert.ok(showPadlock(live, locked) && !showPadlock(live, open));
  assert.ok(!controlEnabled(live, CONTROLS[2], locked), "SD is not operable while locked");
  assert.ok(controlEnabled(live, CONTROLS[2], open));
  assert.ok(controlEnabled(live, CONTROLS[0], undefined), "unlockable controls are enabled without lock info");
  const idle = describeRadio({ ...base, rc: rc() });
  assert.ok(!showPadlock(idle, locked) && !controlEnabled(idle, CONTROLS[0], undefined));
  assert.equal(unlockRemaining(open, 1000, 3500), 39.5);
  assert.equal(unlockRemaining(open, 0, 99999), 0);
  assert.equal(unlockRemaining(locked, 0, 0), null);
});

test("switching to replay while holding the lease needs the release prompt", () => {
  assert.ok(needsReleasePrompt("replay", rc({ holder_is_you: true, lease: { id: "x" } })));
  assert.ok(!needsReleasePrompt("replay", rc()));
  assert.ok(!needsReleasePrompt("replay", rc({ lease: { id: "x" } })), "someone else's lease is not ours to release");
  assert.ok(!needsReleasePrompt("live", rc({ holder_is_you: true, lease: { id: "x" } })));
});

test("slider updates are throttled to the interval and the newest value is always sent", () => {
  const t = fakeTimers();
  const sent = [];
  const th = new Throttle(50, (v) => sent.push(v), t);
  th.push(0.1); // at once
  t.advance(10); th.push(0.2); t.advance(10); th.push(0.3); t.advance(10); th.push(0.4);
  assert.deepEqual(sent, [0.1]);
  t.advance(25); // 55 ms after the first send: the trailing send carries the newest value
  assert.deepEqual(sent, [0.1, 0.4]);
  t.advance(200); th.push(0.9); // idle for a while: immediate again
  assert.deepEqual(sent, [0.1, 0.4, 0.9]);
  // a drag at 1 kHz for one second yields at most 20 updates (+ the first)
  const n0 = sent.length;
  for (let i = 0; i < 1000; i++) { th.push(i / 1000); t.advance(1); }
  t.advance(100);
  assert.ok(sent.length - n0 <= 21, `sent ${sent.length - n0}`);
  assert.equal(sent[sent.length - 1], 0.999, "the last value is not lost");
  t.advance(200); th.push(0.5); th.push(0.6); th.cancel(); t.advance(200);
  assert.equal(sent[sent.length - 1], 0.5, "cancel drops a pending value");
});

test("momentary press repeats every 100 ms and releases once", () => {
  const t = fakeTimers();
  const sent = [];
  const m = new MomentaryHold((v) => sent.push(v), t);
  assert.equal(m.release(), false, "releasing an unpressed control sends nothing");
  m.press();
  m.press(); // a second pointerdown does nothing
  t.advance(350);
  assert.deepEqual(sent, [1, 1, 1, 1], "initial + 3 repeats in 350 ms");
  assert.ok(m.pressed);
  assert.equal(m.release(), true);
  assert.deepEqual(sent, [1, 1, 1, 1, 0]);
  t.advance(1000);
  assert.equal(sent.length, 5, "nothing is sent after the release");
  assert.equal(t.pending(), 0);
  assert.equal(m.release(), false);
});

test("the padlock needs a continuous 1 s hold", () => {
  const t = fakeTimers();
  let done = 0;
  const hold = new HoldTimer(() => done++, t);
  hold.start();
  t.advance(999);
  assert.equal(done, 0);
  assert.ok(hold.progress() > 0.99);
  hold.cancel(); // released early
  t.advance(500);
  assert.equal(done, 0);
  assert.equal(hold.progress(), 0);
  hold.start();
  t.advance(1000);
  assert.equal(done, 1);
  assert.ok(!hold.holding);
  t.advance(5000);
  assert.equal(done, 1, "fires once per hold");
});
