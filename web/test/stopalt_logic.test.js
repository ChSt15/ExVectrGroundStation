import test from "node:test";
import assert from "node:assert/strict";
import { StopAltitudeSetter, parseStopAltitude, echoText, MAX_RETRIES } from "../app/telecommand/stopalt_logic.js";

test("parse: finite metres >= 1, no maximum", () => {
  assert.equal(parseStopAltitude("20"), 20);
  assert.equal(parseStopAltitude("5.5"), 5.5);
  assert.equal(parseStopAltitude("100000"), 100000);
  for (const t of ["0.9", "-5", "", "abc", "NaN", "Infinity"]) assert.equal(parseStopAltitude(t), null);
});

test("echo text", () => {
  assert.equal(echoText(20), "vehicle: 20.0 m");
  assert.equal(echoText(NaN), "vehicle: unknown");
  assert.equal(echoText(null), "vehicle: unknown");
});

test("pending until the echo matches within 0.05 m, then set; no more sends", () => {
  const s = new StopAltitudeSetter();
  s.request(30, 0);
  assert.deepEqual(s.status(), { text: "pending (1/6)", tone: "warn" });
  assert.equal(s.tick(20, 0.5).send, false);
  assert.equal(s.tick(30.2, 1).send, false);
  assert.equal(s.state, "pending");
  assert.equal(s.tick(30.04, 1.5).send, false);
  assert.deepEqual(s.status(), { text: "set", tone: "ok" });
  assert.equal(s.tick(30, 10).send, false);
});

test("retries every 2 s, 5 times, then not confirmed once", () => {
  const s = new StopAltitudeSetter();
  s.request(30, 0);
  let sent = 0;
  let notes = [];
  for (let t = 0.5; t <= 20; t += 0.5) {
    const r = s.tick(20, t);
    if (r.send) sent += 1;
    if (r.notify) notes.push(r.notify);
  }
  assert.equal(sent, MAX_RETRIES);
  assert.equal(notes.length, 1);
  assert.match(notes[0], /not confirmed/);
  assert.deepEqual(s.status(), { text: "not confirmed", tone: "bad" });
});

test("retry spacing and echo NaN keeps pending; a new request restarts; cancel idles", () => {
  const s = new StopAltitudeSetter();
  s.request(25, 10);
  assert.equal(s.tick(NaN, 11.9).send, false);
  assert.equal(s.tick(NaN, 12).send, true);
  assert.equal(s.tick(NaN, 13.9).send, false);
  s.request(40, 14);
  assert.equal(s.sends, 1);
  assert.equal(s.target, 40);
  s.cancel();
  assert.equal(s.status().text, "");
  assert.equal(s.tick(40, 30).send, false);
});
