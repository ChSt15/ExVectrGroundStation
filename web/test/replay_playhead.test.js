import test from "node:test";
import assert from "node:assert/strict";
import { CORR_S, Playhead, createCoalescer, createDebounce } from "../app/status/replay_playhead.js";

const rs = (t, state = "playing", speed = 1) => ({ t, state, speed, t_start: 0, t_end: 1000 });
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} vs ${b}`);

test("no position before the first push, then extrapolates with the speed", () => {
  const p = new Playhead();
  assert.equal(p.position(0), null);
  p.push(rs(10, "playing", 2), 1000);
  near(p.position(1000), 10);
  near(p.position(1250), 10.5);
  near(p.position(2000), 12);
});

test("paused and ended do not advance and snap to the pushed position", () => {
  const p = new Playhead();
  p.push(rs(10), 0);
  p.push(rs(10.6, "paused"), 500);
  near(p.position(500), 10.6);
  near(p.position(5000), 10.6);
  p.push(rs(20, "playing"), 6000);
  p.push(rs(1000, "ended"), 7000);
  near(p.position(9000), 1000);
});

test("a push close to the extrapolation corrects gently and never goes backwards", () => {
  const p = new Playhead();
  p.push(rs(0), 0);
  // 500 ms later the core says 0.4 (display is 0.5, ahead by 0.1)
  p.push(rs(0.4), 500);
  let prev = p.position(500);
  near(prev, 0.5);
  for (let t = 510; t <= 1500; t += 10) {
    const v = p.position(t);
    assert.ok(v >= prev - 1e-9, `backwards at ${t}`);
    prev = v;
  }
  // after the correction time the display follows the pushed track (0.4 + elapsed)
  near(p.position(500 + CORR_S * 1000 + 500), 0.4 + CORR_S + 0.5, 0.06);
});

test("a late display is caught up without a jump", () => {
  const p = new Playhead();
  p.push(rs(0), 0);
  p.push(rs(0.7), 500); // display 0.5, core 0.7
  const a = p.position(500);
  const b = p.position(510);
  assert.ok(b - a < 0.05, "no jump");
  near(p.position(500 + CORR_S * 1000), 0.7 + 0.5, 0.02 + 0.0); // caught up after the correction time
});

test("a big error, a state change or a speed change snap or re-anchor", () => {
  const p = new Playhead();
  p.push(rs(0), 0);
  p.push(rs(50), 500); // a jump (seek by someone else): snap
  near(p.position(500), 50);
  p.push(rs(50.5, "playing", 4), 1000); // speed change: new rate from here
  near(p.position(2000), 50.5 + 4, 0.8);
  near(p.rate, 4);
});

test("the position stays inside the log", () => {
  const p = new Playhead();
  p.push(rs(999), 0);
  near(p.position(5000), 1000);
});

test("a user seek shows at once, is held while dragging and ignores pushes shortly after", () => {
  const p = new Playhead();
  p.push(rs(10), 0);
  p.set(300, false, 100); // drag step
  near(p.position(600), 300); // no extrapolation while dragging
  assert.equal(p.isDragging(600), true);
  assert.equal(p.push(rs(11), 700), false); // ignored during the drag
  near(p.position(700), 300);
  p.set(320, true, 800); // final
  assert.equal(p.isDragging(800), false);
  near(p.position(1000), 320.2); // plays on from the final position
  assert.equal(p.push(rs(10.5), 1000), false); // within 300 ms after the seek
  assert.equal(p.push(rs(320.45), 1200), true); // later pushes are taken, gently
  near(p.position(1200), 320.4, 0.02);
});

test("a drag that never ends expires on its own", () => {
  const p = new Playhead();
  p.push(rs(10), 0);
  p.set(100, false, 0);
  assert.equal(p.isDragging(900), true);
  assert.equal(p.isDragging(1100), false);
  assert.equal(p.push(rs(10), 1200), true);
});

test("a push without a position clears it", () => {
  const p = new Playhead();
  p.push(rs(10), 0);
  p.push({ state: "closed", t: null }, 10);
  assert.equal(p.position(20), null);
});

function fakeTimers() {
  let now = 0;
  const q = new Map();
  let id = 0;
  return {
    t: {
      now: () => now,
      set: (fn, ms) => (q.set(++id, { fn, at: now + ms }), id),
      clear: (i) => q.delete(i),
    },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        const next = [...q.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        now = next[1].at;
        q.delete(next[0]);
        next[1].fn();
      }
      now = end;
    },
    pending: () => q.size,
  };
}

test("coalescer: at most one send per interval, the last value is never lost, final sends at once", () => {
  const f = fakeTimers();
  const sent = [];
  const c = createCoalescer((v) => sent.push(v), 70, f.t);
  f.advance(1000);
  c.call(1); // first call goes at once
  c.call(2);
  c.call(3);
  assert.deepEqual(sent, [1]);
  f.advance(69);
  assert.deepEqual(sent, [1]);
  f.advance(2);
  assert.deepEqual(sent, [1, 3]); // the newest only
  c.call(4);
  c.call(5, true); // final: at once, cancels the pending one
  assert.deepEqual(sent, [1, 3, 5]);
  f.advance(500);
  assert.deepEqual(sent, [1, 3, 5]);
  assert.equal(f.pending(), 0);
});

test("coalescer: 60 calls in a second send about one per 70 ms", () => {
  const f = fakeTimers();
  const sent = [];
  const c = createCoalescer((v) => sent.push(v), 70, f.t);
  for (let i = 0; i < 60; i++) {
    c.call(i);
    f.advance(1000 / 60);
  }
  f.advance(200);
  assert.ok(sent.length <= 17 && sent.length >= 10, String(sent.length));
  assert.equal(sent[sent.length - 1], 59);
});

test("debounce: runs once after the quiet time", () => {
  const f = fakeTimers();
  let n = 0;
  const d = createDebounce(() => n++, 250, f.t);
  d.call();
  f.advance(200);
  d.call();
  f.advance(200);
  assert.equal(n, 0);
  f.advance(60);
  assert.equal(n, 1);
  d.call();
  d.cancel();
  f.advance(1000);
  assert.equal(n, 1);
});
