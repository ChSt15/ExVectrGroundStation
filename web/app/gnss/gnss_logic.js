// GNSS panel logic (docs/gui.md "GNSS panel"): the altitude cross-check and a few formatting helpers.
// Pure and display-only: it compares two values the vehicle reported and says whether they disagree. It never
// changes anything on the vehicle and is never the only warning.

export const ALTCHECK_DEFAULTS = { limit_m: 5, hold_s: 1, hysteresis_m: 1 };
/** The vehicle counts as "at the start" for the reference while its estimated altitude is within this of zero. */
export const GROUND_WINDOW_M = 1.0;
/** The ground reference is the mean of the first GNSS altitudes while on the ground, frozen after at most this many. */
export const REF_MAX_SAMPLES = 100;
/** A u-blox fix type below this has no 3-D position (3 = 3-D fix). Unknown fix type: judged by the altitude alone. */
export const MIN_FIX_TYPE = 3;
export const FIX_NAMES = ["no fix", "dead reckoning", "2D", "3D", "GNSS + DR", "time only"];

const finite = (x) => typeof x === "number" && Number.isFinite(x);
const R2D = 180 / Math.PI;

export const fixName = (t) => (finite(t) ? FIX_NAMES[t] ?? `fix ${t}` : "—");

/** GNSS has a usable altitude: a finite value and, when the fix type is known, a 3-D fix. */
export function gnssUsable(f) {
  if (!f || !finite(f.alt)) return false;
  return !finite(f.fix_type) || f.fix_type >= MIN_FIX_TYPE;
}

/**
 * GNSS velocity as [N, W, U] m/s. The decoded `Gps.vel` is already NWU (the system frame, SI), so this only validates:
 * null when not a finite 3-vector. East motion is negative W, descent is negative U. No NED anywhere in the GUI.
 */
export function gnssVelNwu(v) {
  if (!Array.isArray(v) || v.length < 3 || !v.slice(0, 3).every(finite)) return null;
  return [v[0], v[1], v[2]];
}

export function groundSpeed(v) {
  return Array.isArray(v) && finite(v[0]) && finite(v[1]) ? Math.hypot(v[0], v[1]) : null;
}

export const deg = (rad) => (finite(rad) ? rad * R2D : null);

/**
 * Altitude cross-check: the vehicle's estimated altitude (Position.pos[2], relative to its origin) against the GNSS
 * altitude relative to the start. Reference of the GNSS altitude, in this order:
 *  1. the home altitude of a HOME_POSITION packet (ALT_VALID): the altitude the estimator uses for its origin;
 *  2. the mean of the first valid GNSS altitudes while the estimate is within GROUND_WINDOW_M of zero (on the pad),
 *     frozen when the vehicle leaves the ground or after REF_MAX_SAMPLES samples. Connecting in flight gives no
 *     reference ("no reference"): nothing is invented.
 * A new VEHICLE_STATUS.home_epoch drops the reference taken from GNSS (the vehicle's origin changed).
 * difference = estimated minus GNSS relative altitude. "mismatch" needs |difference| > limit_m for hold_s seconds
 * (cleared below limit_m - hysteresis_m).
 */
export class AltCheck {
  /** @param {() => object} getCfg { limit_m, hold_s, hysteresis_m, stale_s } read on every update */
  constructor(getCfg = () => ({})) {
    this.getCfg = getCfg;
    this.reset();
  }

  reset() {
    this.ref = null; // { value, source: "home" | "gnss", n }
    this.frozen = false;
    this.lastGpsT = null;
    this.epoch = undefined;
    this.over = null; // view time the difference went over the limit
    this.mismatch = false;
    this.lastNow = null;
  }

  /**
   * @param {object} inp { now, position, gps, home, status }: store entries (or undefined), `now` in view seconds
   * @returns {{state, est, gnssAbs, gnssRel, diff, ref, refSource, mismatch, limit}} state is one of
   *   "nodata" | "stale" | "nofix" | "noref" | "ok" | "mismatch" ("over" = over the limit, not yet for hold_s)
   */
  update(inp) {
    const c = { ...ALTCHECK_DEFAULTS, stale_s: 3, ...this.getCfg() };
    const now = inp.now;
    if (!finite(now)) return this._out("nodata", c);
    if (this.lastNow !== null && now < this.lastNow - 0.01) this.reset(); // replay seek
    this.lastNow = now;

    const st = inp.status && inp.status.f;
    const epoch = st && finite(st.home_epoch) ? st.home_epoch : undefined;
    if (epoch !== undefined) {
      if (this.epoch !== undefined && epoch !== this.epoch) this._dropRef(); // the vehicle's origin changed
      this.epoch = epoch;
    }

    const fresh = (e) => !!e && !!e.f && now - e.t <= c.stale_s;
    const pos = inp.position && inp.position.f;
    const est = pos && Array.isArray(pos.pos) && finite(pos.pos[2]) ? pos.pos[2] : null;
    const g = inp.gps && inp.gps.f;
    const home = inp.home && inp.home.f;

    // a HOME_POSITION copy of another epoch than the status reports is stale: not used
    const homeOk = !!home && home.alt_valid === true && finite(home.alt) && (epoch === undefined || home.epoch === epoch);
    if (homeOk) {
      if (!this.ref || this.ref.source !== "home" || this.ref.value !== home.alt) this.ref = { value: home.alt, source: "home", n: 1 };
      this.frozen = true;
    } else if (this.ref && this.ref.source === "home") this._dropRef();

    if (!g || !inp.position) return this._out("nodata", c, { est });
    if (!fresh(inp.gps) || !fresh(inp.position)) return this._out("stale", c, { est, gnssAbs: finite(g.alt) ? g.alt : null });
    if (!gnssUsable(g)) return this._out("nofix", c, { est });

    // reference from the first GNSS samples while on the pad
    if (!this.frozen && est !== null && inp.gps.t !== this.lastGpsT) {
      if (Math.abs(est) <= GROUND_WINDOW_M) {
        const n = this.ref ? this.ref.n : 0;
        this.ref = { value: this.ref ? (this.ref.value * n + g.alt) / (n + 1) : g.alt, source: "gnss", n: n + 1 };
        if (this.ref.n >= REF_MAX_SAMPLES) this.frozen = true;
      } else if (this.ref) this.frozen = true; // left the ground: the reference stays
    }
    this.lastGpsT = inp.gps.t;

    if (!this.ref || est === null) return this._out("noref", c, { est, gnssAbs: g.alt });
    const gnssRel = g.alt - this.ref.value;
    const diff = est - gnssRel;
    const a = Math.abs(diff);
    if (a > c.limit_m) {
      if (this.over === null) this.over = now;
      if (now - this.over >= c.hold_s) this.mismatch = true;
    } else if (a < c.limit_m - c.hysteresis_m) {
      this.over = null;
      this.mismatch = false;
    } else if (!this.mismatch) {
      this.over = null;
    }
    const state = this.mismatch ? "mismatch" : this.over !== null ? "over" : "ok";
    return this._out(state, c, { est, gnssAbs: g.alt, gnssRel, diff });
  }

  _dropRef() {
    this.ref = null;
    this.frozen = false;
    this.over = null;
    this.mismatch = false;
  }

  _out(state, c, v = {}) {
    if (state === "nodata" || state === "stale" || state === "nofix") {
      // not judged: a warning needs live, usable data; the timer restarts when data is back
      this.over = null;
      this.mismatch = false;
    }
    return {
      state,
      est: v.est ?? null,
      gnssAbs: v.gnssAbs ?? null,
      gnssRel: v.gnssRel ?? null,
      diff: v.diff ?? null,
      ref: this.ref ? this.ref.value : null,
      refSource: this.ref ? this.ref.source : null,
      mismatch: state === "mismatch",
      limit: c.limit_m,
    };
  }
}

/**
 * Graph helper (plot/catalogue.js): GNSS altitude relative to the start and the estimated altitude at one Gps sample.
 * Stateless apart from the memo `last._gnssRef` that the SignalFeeder's `last` object carries (cleared with it):
 * reference = the altitude of the latest HOME_POSITION (ALT_VALID), else the mean of the first GNSS altitudes seen
 * while the estimate was within GROUND_WINDOW_M of zero (frozen when the vehicle leaves the ground). No reference: rel
 * is undefined. `f`: Gps fields, `last`: latest fields per type.
 */
export function plotGnssAltitude(f, last) {
  if (!gnssUsable(f)) return { rel: undefined, est: undefined };
  const p = last.Position;
  const est = p && Array.isArray(p.pos) && finite(p.pos[2]) ? p.pos[2] : undefined;
  const home = last.HomePosition;
  let ref;
  if (home && home.alt_valid === true && finite(home.alt)) ref = home.alt;
  else {
    const m = last._gnssRef || (last._gnssRef = { sum: 0, n: 0, frozen: false });
    if (!m.frozen && est !== undefined) {
      if (Math.abs(est) <= GROUND_WINDOW_M) {
        m.sum += f.alt;
        m.n += 1;
        if (m.n >= REF_MAX_SAMPLES) m.frozen = true;
      } else if (m.n > 0) m.frozen = true;
    }
    ref = m.n ? m.sum / m.n : undefined;
  }
  return { rel: ref === undefined ? undefined : f.alt - ref, est };
}
