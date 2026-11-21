// Display smoothing of the 3D view's target position. DISPLAY ONLY: the smoothed point is drawn in the 3D view and
// is never used for a value shown as "reported" (panels and plots keep the raw MissionState values).
//
// Between MissionState packets the shown position advances as sp_pos + sp_vel * (view time since the packet), at most
// MAX_EXTRAP_S, then it holds. When a new packet arrives the shown position is not snapped: the difference to the
// previous shown position decays exponentially (time constant BLEND_TAU_S). No extrapolation when sp_vel is missing
// or not finite. Time is the view clock (seconds), so replay seeks and pauses behave.

export const MAX_EXTRAP_S = 0.5;
export const BLEND_TAU_S = 0.15;
const OFFSET_EPS = 1e-3; // m: below this the blend counts as finished

const vec3 = (v) => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number" && Number.isFinite(x));

export class TargetSmoother {
  constructor({ maxExtrapS = MAX_EXTRAP_S, tauS = BLEND_TAU_S } = {}) {
    this.maxExtrapS = maxExtrapS;
    this.tauS = tauS;
    this.reset();
  }

  reset() {
    this.pos = null; // raw sp_pos of the newest packet
    this.vel = null; // raw sp_vel or null (not usable)
    this.t = null; // packet time on the view clock
    this.off = [0, 0, 0]; // blend offset at offT
    this.offT = 0;
  }

  /** Extrapolated raw position at view time `now` (no blending), or null. */
  _raw(now) {
    if (!this.pos) return null;
    if (!this.vel) return this.pos.slice();
    const dt = Math.min(this.maxExtrapS, Math.max(0, now - this.t));
    return this.pos.map((p, i) => p + this.vel[i] * dt);
  }

  _decay(now) {
    return Math.exp(-Math.max(0, now - this.offT) / this.tauS);
  }

  /**
   * Feed the newest MissionState values (t = packet time on the view clock). Call every refresh; a repeated packet
   * (same t) is ignored. Invalid pos clears the state. A packet time going backwards (replay seek) resets.
   */
  update(now, pos, vel, t) {
    if (!vec3(pos) || !Number.isFinite(t)) return this.reset();
    if (this.t !== null && (t < this.t || now < this.offT)) this.reset();
    if (this.t === t && this.pos) return;
    const prev = this.pos ? this.at(now) : null;
    this.pos = pos.slice();
    this.vel = vec3(vel) ? vel.slice() : null;
    this.t = t;
    this.offT = now;
    this.off = [0, 0, 0];
    if (prev) {
      const raw = this._raw(now);
      this.off = prev.map((p, i) => p - raw[i]);
    }
  }

  /** Position to draw at view time `now`, or null without a target. */
  at(now) {
    const raw = this._raw(now);
    if (!raw) return null;
    const f = this._decay(now);
    return raw.map((r, i) => r + this.off[i] * f);
  }

  /** True when the drawn position no longer changes with time (hold reached or no velocity, blend finished). */
  settled(now) {
    if (!this.pos) return true;
    const moving = this.vel && now - this.t < this.maxExtrapS && this.vel.some((v) => v !== 0);
    const blending = Math.hypot(...this.off) * this._decay(now) > OFFSET_EPS;
    return !moving && !blending;
  }
}
