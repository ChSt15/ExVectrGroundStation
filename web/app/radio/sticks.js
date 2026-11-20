// Pure stick display mapping (radio panel and the compact RC panel). DISPLAY ONLY: the ground station sends no stick
// values; it shows the channels it receives from the Pocket (slave), sends (master: the channel defaults) or replays.
// The channel assignment comes from the radio config's optional [sticks] table (docs/radio.md).

export const DEFAULT_STICKS = { mode: 2, roll: 1, pitch: 2, throttle: 3, yaw: 4 };
export const US_CENTER = 1500;
export const US_LOW = 1000;
export const US_HIGH = 2000;

/** Stick config from the raw radio config (missing keys take the defaults). */
export function stickConfig(radioCfg) {
  const s = (radioCfg && radioCfg.sticks) || {};
  const out = { ...DEFAULT_STICKS };
  for (const k of Object.keys(DEFAULT_STICKS)) if (Number.isInteger(s[k])) out[k] = s[k];
  return out;
}

/** -1..1 for a centred axis (1000 us = -1, 1500 = 0, 2000 = +1); undefined for a missing value. */
export function normCentered(us) {
  if (typeof us !== "number" || !Number.isFinite(us)) return undefined;
  return Math.max(-1, Math.min(1, (us - US_CENTER) / (US_HIGH - US_CENTER)));
}

/** 0..1 for the throttle (1000 us = 0 low, 2000 = 1 full). */
export function normThrottle(us) {
  if (typeof us !== "number" || !Number.isFinite(us)) return undefined;
  return Math.max(0, Math.min(1, (us - US_LOW) / (US_HIGH - US_LOW)));
}

/** Throttle shown on a -1..1 gimbal axis (low = bottom). */
export const throttleAxis = (t) => (t === undefined ? undefined : t * 2 - 1);

/**
 * Both gimbals from a channel vector (1-based channels in the config). Mode 2: left = yaw (x) / throttle (y),
 * right = roll (x) / pitch (y). Mode 1: left = yaw / pitch, right = roll / throttle. Up and right are positive.
 * Returns { left: {x, y, xLabel, yLabel, xUs, yUs}, right: {...}, throttle (0..1), values: {roll, pitch, yaw, throttle} }.
 */
export function stickState(cfg, ch) {
  const c = cfg || DEFAULT_STICKS;
  const us = (n) => (Array.isArray(ch) && n >= 1 && n <= ch.length && typeof ch[n - 1] === "number" ? ch[n - 1] : undefined);
  const roll = normCentered(us(c.roll));
  const pitch = normCentered(us(c.pitch));
  const yaw = normCentered(us(c.yaw));
  const throttle = normThrottle(us(c.throttle));
  const axis = (name, v) => ({ name, v: name === "throttle" ? throttleAxis(v) : v, us: us(c[name]), ch: c[name] });
  const vals = { roll, pitch, yaw, throttle };
  const [lx, ly, rx, ry] = c.mode === 1 ? ["yaw", "pitch", "roll", "throttle"] : ["yaw", "throttle", "roll", "pitch"];
  const gimbal = (xn, yn) => {
    const x = axis(xn, vals[xn]);
    const y = axis(yn, vals[yn]);
    return { x: x.v, y: y.v, xLabel: xn, yLabel: yn, xUs: x.us, yUs: y.us, xCh: x.ch, yCh: y.ch };
  };
  return { left: gimbal(lx, ly), right: gimbal(rx, ry), throttle, values: vals };
}
