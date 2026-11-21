// Display smoothing of the 3D view's target position. DISPLAY ONLY: the smoothed point is drawn in the 3D view and
// is never used for a value shown as "reported" (panels and plots keep the raw MissionState values).
//
// A critically damped second-order follower (time constant TAU_S) of the reported sp_pos, with feed-forward velocity:
// a continuous motion is tracked without lag, a guidance step (new waypoint, phase change) glides instead of teleporting.
//   x'' = w^2 (r - x) + 2 w (vr - x'),  w = 1/tau,  r = P + v * min(dt, cap),  vr = v while dt < cap, else 0
// P is the newest sp_pos packet and dt the view time since it. The velocity v is taken from sp_vel when it agrees with
// the finite difference of the reported positions, else from the finite difference: the vehicle's sp_vel is not the
// derivative of sp_pos in every phase (it is 0 while sp_pos moves in Deceleration and Landing, tools/analyse_target_smoothing.py).
// An implied speed above STEP_SPEED_MPS between two packets is a guidance step: it gives no velocity (sp_vel is used).
// The extrapolation never goes beyond one packet interval plus a margin (cap), so a 5 Hz source (AIR_MISSION over the
// Router) is bridged and a lost packet is not extrapolated for long. Time is the view clock (seconds), so replay seeks
// and pauses behave.

export const TAU_S = 0.25;
export const MAX_EXTRAP_S = 0.5;
export const EXTRAP_FACTOR = 1.25; // cap = factor * packet interval + margin
export const EXTRAP_MARGIN_S = 0.05;
export const STEP_SPEED_MPS = 30;
const SETTLE_EPS = 1e-3; // m and m/s
const MAX_DT_S = 1.0; // a longer gap between packets gives no finite difference

const vec3 = (v) => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number" && Number.isFinite(x));
const norm = (a) => Math.hypot(a[0], a[1], a[2]);

export class TargetSmoother {
  constructor({ tauS = TAU_S } = {}) {
    this.tauS = tauS;
    this.reset();
  }

  reset() {
    this.pos = null; // raw sp_pos of the newest packet
    this.v = [0, 0, 0]; // velocity used for the extrapolation
    this.t = null; // packet time (view clock)
    this.interval = null; // smoothed packet interval
    this.x = null; // drawn position
    this.xv = [0, 0, 0];
    this.tNow = 0; // time up to which the follower is integrated
  }

  _cap() {
    return this.interval === null ? MAX_EXTRAP_S : Math.min(MAX_EXTRAP_S, EXTRAP_FACTOR * this.interval + EXTRAP_MARGIN_S);
  }

  /** [r, vr] at view time t. */
  _target(t) {
    const cap = this._cap();
    const dt = Math.min(cap, Math.max(0, t - this.t));
    const r = this.pos.map((p, i) => p + this.v[i] * dt);
    return [r, t - this.t < cap ? this.v : [0, 0, 0]];
  }

  _advance(now) {
    if (!this.pos || now <= this.tNow) return;
    const w = 1 / this.tauS;
    const h = Math.max(0.005, (now - this.tNow) / 200); // bounded work after a long pause, stable for w*h < 2
    while (this.tNow < now - 1e-12) {
      const s = Math.min(h, now - this.tNow);
      this.tNow += s;
      const [r, vr] = this._target(this.tNow);
      for (let i = 0; i < 3; i++) {
        this.xv[i] += (w * w * (r[i] - this.x[i]) + 2 * w * (vr[i] - this.xv[i])) * s;
        this.x[i] += this.xv[i] * s;
      }
    }
  }

  /**
   * Feed the newest MissionState values (t = packet time on the view clock). Call every refresh; a repeated packet
   * (same t) is ignored. Invalid pos clears the state. A packet time going backwards (replay seek) resets.
   */
  update(now, pos, vel, t) {
    if (!vec3(pos) || !Number.isFinite(t) || !Number.isFinite(now)) return this.reset();
    if (this.t !== null && (t < this.t || now < this.tNow)) this.reset();
    if (this.t === t && this.pos) return;
    const sp = vec3(vel) ? vel.slice() : [0, 0, 0];
    if (!this.pos) {
      this.x = pos.slice();
      this.xv = [0, 0, 0];
      this.tNow = now;
      this.v = sp;
    } else {
      this._advance(now);
      const dt = t - this.t;
      let v = sp;
      if (dt > 0.005 && dt < MAX_DT_S) {
        const fd = pos.map((p, i) => (p - this.pos[i]) / dt);
        if (norm(fd) <= STEP_SPEED_MPS) {
          const agree = norm(fd.map((f, i) => f - sp[i])) < 0.3 + 0.1 * norm(sp);
          v = vec3(vel) && agree ? sp : fd;
          this.interval = this.interval === null ? dt : 0.7 * this.interval + 0.3 * dt;
        }
      }
      this.v = v;
    }
    this.pos = pos.slice();
    this.t = t;
  }

  /** Position to draw at view time `now`, or null without a target. */
  at(now) {
    if (!this.pos) return null;
    this._advance(now);
    return this.x.slice();
  }

  /** True when the drawn position no longer changes with time (arrived at the held target, no extrapolation left). */
  settled(now) {
    if (!this.pos) return true;
    this._advance(now);
    const [r, vr] = this._target(now);
    return norm(vr) === 0 && norm(this.xv) < SETTLE_EPS && Math.hypot(r[0] - this.x[0], r[1] - this.x[1], r[2] - this.x[2]) < SETTLE_EPS;
  }
}
