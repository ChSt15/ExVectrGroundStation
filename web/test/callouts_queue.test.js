import test from "node:test";
import assert from "node:assert/strict";
import { CalloutQueue, Suppressor } from "../app/callouts/queue.js";
import { Speaker } from "../app/callouts/speech.js";
import { DEFAULTS, mergeSettings } from "../app/callouts/settings.js";
import { pushHistory } from "../app/callouts/notify.js";

const ev = (text, priority, extra = {}) => ({ id: text, category: "state", text, severity: priority === 0 ? "failure" : "info", priority, ...extra });

function rig() {
  const log = [];
  let t = 0;
  const speaker = { cur: null, speak(e, done) { log.push(["say", e.text]); this.cur = done; }, cancel() { log.push(["cancel"]); this.cur = null; } };
  const q = new CalloutQueue({ speaker, clock: () => t });
  return { q, log, speaker, advance: (dt) => (t += dt), finish: () => { const d = speaker.cur; speaker.cur = null; d && d(); } };
}

test("queue speaks by priority: failures > warnings > state > mission > altitude > countdown", () => {
  const r = rig();
  r.q.push(ev("blocker", 3));
  r.q.push(ev("countdown", 5));
  r.q.push(ev("altitude", 4));
  r.q.push(ev("state", 2));
  r.q.push(ev("warning", 1));
  const said = [];
  for (let i = 0; i < 6; i++) r.finish();
  assert.deepEqual(r.log.filter((x) => x[0] === "say").map((x) => x[1]), ["blocker", "warning", "state", "altitude", "countdown"]);
  void said;
});

test("a failure cancels the speech in progress and drops lower-priority pending items", () => {
  const r = rig();
  r.q.push(ev("altitude", 4));
  r.q.push(ev("mission", 3));
  r.q.push(ev("Sensor failure", 0));
  assert.deepEqual(r.log, [["say", "altitude"], ["cancel"], ["say", "Sensor failure"]]);
  r.finish();
  assert.deepEqual(r.log.length, 3); // nothing else pending
  // a second failure does not cancel the first failure but waits
  r.q.push(ev("A failure", 0));
  r.q.push(ev("B failure", 0));
  assert.deepEqual(r.log.slice(3), [["say", "A failure"]]);
  r.finish();
  assert.deepEqual(r.log.slice(4), [["say", "B failure"]]);
});

test("a cancelled utterance's late end event does not advance the queue", () => {
  const r = rig();
  const lateDone = [];
  r.speaker.speak = function (e, done) { r.log.push(["say", e.text]); lateDone.push(done); };
  r.q.push(ev("one", 4));
  r.q.push(ev("fail", 0));
  r.q.push(ev("two", 0));
  lateDone[0](); // end of the cancelled one
  assert.deepEqual(r.log.filter((x) => x[0] === "say").map((x) => x[1]), ["one", "fail"]);
});

test("pending items older than their max age are dropped (late countdown numbers are wrong)", () => {
  const r = rig();
  r.q.push(ev("busy", 3));
  r.q.push(ev("3", 5, { maxAge: 1.5 }));
  r.advance(2);
  r.finish();
  assert.deepEqual(r.log.filter((x) => x[0] === "say").map((x) => x[1]), ["busy"]);
});

test("identical text inside the window is dropped (2 s normal, 8 s failures); repeats pass", () => {
  let t = 0;
  const c = mergeSettings(DEFAULTS, {});
  const s = new Suppressor(() => c, () => t);
  const n = { text: "Altitude 50", severity: "info" };
  assert.equal(s.accept(n), true);
  t = 1.9;
  assert.equal(s.accept(n), false);
  t = 4;
  assert.equal(s.accept(n), true);
  const f = { text: "Position failure", severity: "failure" };
  assert.equal(s.accept(f), true);
  t = 11;
  assert.equal(s.accept(f), false);
  assert.equal(s.accept({ ...f, repeat: true }), true);
  t = 25;
  assert.equal(s.accept(f), true);
});

test("speaker: style by severity, master volume applied immediately, beep before failures, fake synth", () => {
  const spoken = [];
  const beeps = [];
  const synth = { getVoices: () => [{ voiceURI: "v1", name: "V", lang: "en" }], speak: (u) => { spoken.push(u); setTimeout(() => u.onend && u.onend(), 0); }, cancel() {} };
  class U { constructor(t) { this.text = t; } }
  class AC { constructor() { this.currentTime = 0; this.destination = {}; this.state = "running"; }
    createOscillator() { beeps.push("osc"); return { frequency: {}, connect() {}, start() {}, stop() {} }; }
    createGain() { return { gain: { setValueAtTime: (v) => beeps.push(v) }, connect() {} }; } }
  let c = mergeSettings(DEFAULTS, { voice: "v1" });
  const sp = new Speaker({ getSettings: () => c, synth, Utterance: U, AudioCtx: AC });
  assert.equal(sp.voices().length, 1);
  return new Promise((resolve) => {
    sp.speak({ text: "Altitude 50", severity: "info" }, () => {
      const u = spoken[0];
      assert.deepEqual([u.rate, u.pitch], [1.0, 1.0]);
      assert.ok(Math.abs(u.volume - 0.9 * 0.8) < 1e-9);
      assert.equal(u.voice.voiceURI, "v1");
      assert.equal(beeps.length, 0);
      c = mergeSettings(c, { volume: 0.5 }); // next callout uses the new volume
      sp.speak({ text: "Sensor failure", severity: "failure" }, () => {
        const f = spoken[1];
        assert.deepEqual([f.rate, f.pitch], [1.3, 0.7]);
        assert.ok(Math.abs(f.volume - 0.5) < 1e-9);
        assert.ok(beeps.length >= 2); // two tones before the words
        resolve();
      });
    });
  });
});

test("speaker without speechSynthesis: no voices, speak completes, nothing throws", () => {
  const sp = new Speaker({ getSettings: () => DEFAULTS, synth: null, Utterance: null, AudioCtx: null });
  assert.equal(sp.supported, false);
  assert.deepEqual(sp.voices(), []);
  assert.equal(sp.beep(1), false);
  return new Promise((resolve) => sp.speak({ text: "x", severity: "info" }, resolve));
});

test("history is newest first and bounded to 100", () => {
  let h = [];
  for (let i = 0; i < 130; i++) h = pushHistory(h, { text: String(i) });
  assert.equal(h.length, 100);
  assert.equal(h[0].text, "129");
});
