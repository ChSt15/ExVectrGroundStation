import test from "node:test";
import assert from "node:assert/strict";
import { buildIndex, composeText, numberClips } from "../app/callouts/clips.js";
import { ClipPlayer } from "../app/callouts/clipplayer.js";
import { Speaker } from "../app/callouts/speech.js";
import { DEFAULTS, mergeSettings } from "../app/callouts/settings.js";

const PHRASES = {
  altitude: "Altitude", "failure-bit": "Failure bit", "t-minus": "T minus", minus: "Minus", point: "Point", meters: "Meters",
  "meters-per-second": "Meters per second", seconds: "Seconds", percent: "Percent", thousand: "Thousand",
  "position-failure": "Position failure", "return-to-home": "Return to home",
};
const manifest = { clips: {} };
for (const [id, text] of Object.entries(PHRASES)) manifest.clips[id] = { file: `${id}.ogg`, text, duration: 0.5 };
for (let n = 0; n <= 99; n++) manifest.clips[`num-${n}`] = { file: `n${n}.ogg`, text: String(n), duration: 0.4 };
for (let n = 100; n < 1000; n += 100) manifest.clips[`num-${n}`] = { file: `n${n}.ogg`, text: `${n / 100} hundred`, duration: 0.5 };
const index = buildIndex(manifest);

test("numberClips: integers, negatives, decimals, hundreds, thousands", () => {
  assert.deepEqual(numberClips("12"), ["num-12"]);
  assert.deepEqual(numberClips("12.5"), ["num-12", "point", "num-5"]);
  assert.deepEqual(numberClips("-3"), ["minus", "num-3"]);
  assert.deepEqual(numberClips("0.0"), ["num-0", "point", "num-0"]);
  assert.deepEqual(numberClips("100"), ["num-100"]);
  assert.deepEqual(numberClips("150"), ["num-100", "num-50"]);
  assert.deepEqual(numberClips("205"), ["num-200", "num-5"]);
  assert.deepEqual(numberClips("1234"), ["num-1", "thousand", "num-200", "num-34"]);
  assert.deepEqual(numberClips("2005"), ["num-2", "thousand", "num-5"]);
  assert.deepEqual(numberClips("007"), ["num-7"]);
  assert.deepEqual(numberClips("123456"), ["num-1", "num-2", "num-3", "num-4", "num-5", "num-6"]);
  assert.equal(numberClips("abc"), null);
  assert.equal(numberClips("1e3"), null);
});

test("composeText: engine phrases, altitude, countdown, units, failure bits", () => {
  assert.deepEqual(composeText("Position failure", index), ["position-failure"]);
  assert.deepEqual(composeText("Return to home", index), ["return-to-home"]);
  assert.deepEqual(composeText("Altitude 50", index), ["altitude", "num-50"]);
  assert.deepEqual(composeText("Altitude 150", index), ["altitude", "num-100", "num-50"]);
  assert.deepEqual(composeText("3", index), ["num-3"]);
  assert.deepEqual(composeText("T minus 5", index), ["t-minus", "num-5"]);
  assert.deepEqual(composeText("Failure bit 7", index), ["failure-bit", "num-7"]);
  assert.deepEqual(composeText("12.5 m", index), ["num-12", "point", "num-5", "meters"]);
  assert.deepEqual(composeText("-3 m/s", index), ["minus", "num-3", "meters-per-second"]);
  assert.deepEqual(composeText("40 %", index), ["num-40", "percent"]);
  assert.deepEqual(composeText("Meters per second", index), ["meters-per-second"]);
  assert.equal(composeText("Banana", index), null);
  assert.equal(composeText("", index), null);
  assert.equal(composeText("m", index), null); // a unit alone is not speech
});

// ---- Speaker with clips: fake WebAudio
function fakeCtx(log) {
  return class AC {
    constructor() { this.currentTime = 1; this.destination = {}; this.state = "running"; }
    decodeAudioData(buf) { return Promise.resolve({ duration: buf.dur }); }
    createGain() { const g = { gain: { value: 0, setValueAtTime() {} }, connect() {} }; log.push(["gain", g]); return g; }
    createBufferSource() {
      const s = { start: (t) => log.push(["start", t]), stop: () => log.push(["stop"]), connect() {} };
      log.push(["src", s]);
      return s;
    }
    createOscillator() { log.push(["beep"]); return { frequency: {}, connect() {}, start() {}, stop() {} }; }
  };
}
const fakeFetch = (manifestOk = true) => async (url) => {
  if (url.endsWith("manifest.json")) return { ok: manifestOk, json: async () => manifest };
  return { ok: true, arrayBuffer: async () => ({ dur: 0.5 }) };
};

test("ClipPlayer + Speaker: recorded clips chained with the gap, volume applied, cancel stops", async () => {
  const log = [];
  const AC = fakeCtx(log);
  const clips = new ClipPlayer({ baseUrl: "/x/", fetchFn: fakeFetch() });
  const c = mergeSettings(DEFAULTS, { volume: 0.5 });
  const sp = new Speaker({ getSettings: () => c, synth: null, Utterance: null, AudioCtx: AC, clips });
  assert.equal(sp.canSound, true);
  sp.unlock();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(clips.state, "ready");
  let done = 0;
  sp.speak({ text: "Altitude 150", severity: "info" }, () => done++);
  const starts = log.filter((x) => x[0] === "start").map((x) => x[1]);
  assert.equal(starts.length, 3);
  assert.ok(Math.abs(starts[1] - starts[0] - 0.56) < 1e-9); // 0.5 s clip + 60 ms gap
  assert.ok(Math.abs(log.find((x) => x[0] === "gain")[1].gain.value - 0.9 * 0.5) < 1e-9);
  const last = log.filter((x) => x[0] === "src").at(-1)[1];
  last.onended();
  assert.equal(done, 1);
  sp.speak({ text: "Altitude 50", severity: "info" }, () => done++);
  sp.cancel();
  assert.ok(log.some((x) => x[0] === "stop"));
  const s2 = log.filter((x) => x[0] === "src").at(-1)[1];
  s2.onended && s2.onended(); // a cancelled playback must not report done
  assert.equal(done, 1);
});

test("beep and voice are concurrent: a failure beep does not delay the words, a beep-only item does not hold the queue", async () => {
  const log = [];
  const AC = fakeCtx(log);
  const clips = new ClipPlayer({ baseUrl: "/x/", fetchFn: fakeFetch() });
  let c = mergeSettings(DEFAULTS, {});
  const sp = new Speaker({ getSettings: () => c, synth: null, Utterance: null, AudioCtx: AC, clips });
  sp.unlock();
  await new Promise((r) => setTimeout(r, 20));
  // failure: the beep oscillators and the first clip start in the same synchronous call (same audio time)
  sp.speak({ text: "Altitude 50", severity: "failure" }, () => {});
  const kinds = log.filter((x) => x[0] === "beep" || x[0] === "start").map((x) => x[0]);
  assert.ok(kinds.includes("beep") && kinds.includes("start"), "beep and clip start immediately");
  assert.ok(log.find((x) => x[0] === "start")[1] < 1.1); // ctx.currentTime (1) plus the player lead, no 0.3 s delay
  sp.cancel();
  // a direct beep (log notification, motor beep) while a clip plays leaves the clip untouched
  log.length = 0;
  sp.speak({ text: "Altitude 50", severity: "info" }, () => {});
  const stops = () => log.filter((x) => x[0] === "stop").length;
  const before = stops();
  assert.equal(sp.beep(0.8), true);
  assert.equal(stops(), before);
  assert.equal(log.filter((x) => x[0] === "beep").length >= 2, true);
  sp.cancel();
  // beeps-only item completes at once (does not hold the queue for the beep length)
  c = mergeSettings(DEFAULTS, { voiceMode: "beeps" });
  const t0 = Date.now();
  await new Promise((r) => sp.speak({ text: "Altitude 50", severity: "info" }, r));
  assert.ok(Date.now() - t0 < 100);
});

test("Speaker fallback: no clip for the text -> usable browser speech, else a beep", async () => {
  const log = [];
  const AC = fakeCtx(log);
  const spoken = [];
  const synth = { getVoices: () => [{ voiceURI: "v", name: "V", lang: "en" }], speak: (u) => { if (u.text) spoken.push(u.text); setTimeout(() => u.onend(), 0); }, cancel() {} };
  class U { constructor(t) { this.text = t; } }
  const clips = new ClipPlayer({ baseUrl: "/x/", fetchFn: fakeFetch() });
  const c = mergeSettings(DEFAULTS, {});
  const sp = new Speaker({ getSettings: () => c, synth, Utterance: U, AudioCtx: AC, clips });
  sp.unlock();
  await new Promise((r) => setTimeout(r, 20));
  await new Promise((r) => sp.speak({ text: "Banana", severity: "info" }, r));
  assert.deepEqual(spoken, ["Banana"]);
  // voice without any system voices (Flatpak Chromium): beep instead
  const sp2 = new Speaker({ getSettings: () => c, synth: { getVoices: () => [], speak() { throw new Error("no"); }, cancel() {} }, Utterance: U, AudioCtx: AC, clips });
  sp2.unlock();
  const n = log.filter((x) => x[0] === "beep").length;
  await new Promise((r) => sp2.speak({ text: "Banana", severity: "info" }, r));
  assert.ok(log.filter((x) => x[0] === "beep").length > n);
});

test("voice mode: browser skips the clips, beeps only never speaks", async () => {
  const log = [];
  const AC = fakeCtx(log);
  const spoken = [];
  const synth = { getVoices: () => [{ voiceURI: "v", name: "V", lang: "en" }], speak: (u) => { if (u.text) spoken.push(u.text); setTimeout(() => u.onend(), 0); }, cancel() {} };
  class U { constructor(t) { this.text = t; } }
  const clips = new ClipPlayer({ baseUrl: "/x/", fetchFn: fakeFetch() });
  let c = mergeSettings(DEFAULTS, { voiceMode: "browser" });
  const sp = new Speaker({ getSettings: () => c, synth, Utterance: U, AudioCtx: AC, clips });
  sp.unlock();
  await new Promise((r) => setTimeout(r, 20));
  await new Promise((r) => sp.speak({ text: "Altitude 50", severity: "info" }, r));
  assert.deepEqual(spoken, ["Altitude 50"]);
  assert.equal(log.filter((x) => x[0] === "start").length, 0);
  c = mergeSettings(DEFAULTS, { voiceMode: "beeps" });
  await new Promise((r) => sp.speak({ text: "Altitude 50", severity: "info" }, r));
  assert.deepEqual(spoken, ["Altitude 50"]);
  assert.equal(mergeSettings(DEFAULTS, { voiceMode: "bogus" }).voiceMode, "recorded");
});

test("ClipPlayer: a missing manifest leaves it failed and plan() null", async () => {
  const clips = new ClipPlayer({ baseUrl: "/x/", fetchFn: fakeFetch(false) });
  await clips.load(new (fakeCtx([]))());
  assert.equal(clips.state, "failed");
  assert.equal(clips.plan("Altitude 50"), null);
});
