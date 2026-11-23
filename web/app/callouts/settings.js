// Callout settings: built-in defaults, merge of a defaults file and the user's saved values, volume scaling.
// Pure functions (unit tested in web/test/callouts_settings.test.js). Display-only feature: nothing here is sent to
// the vehicle.

export const CATEGORIES = ["countdown", "phase", "altitude", "failure", "link", "state", "deviation", "altcheck"];
export const CATEGORY_LABELS = {
  countdown: "Countdown",
  phase: "Mission phases",
  altitude: "Altitude",
  failure: "Failures",
  link: "Link / telemetry",
  state: "State (armed, motors, zeroing, sim)",
  deviation: "Position deviation",
  altcheck: "GNSS altitude cross-check",
};
import { LOG_CATEGORIES } from "./log_patterns.js";

export const VOICE_MODES = ["recorded", "browser", "beeps"];
export const VOICE_MODE_LABELS = { recorded: "Recorded (default)", browser: "Browser voice", beeps: "Beeps only" };
export const STYLE_NAMES = ["normal", "warning", "failure"];
export const BEEP_BASE_GAIN = 0.35;
export const DEFAULT_VOLUME = 0.8;

export const DEFAULTS = {
  master: true,
  volume: DEFAULT_VOLUME, // master volume 0..1, scales every style volume and the beep
  speakInReplay: false,
  voiceMode: "recorded", // "recorded" clips (web/audio) | "browser" speechSynthesis | "beeps" only
  voice: "", // browser voiceURI, "" = browser default
  categories: { countdown: true, phase: true, altitude: true, failure: true, link: true, state: true, deviation: false, altcheck: false },
  altitude: { levels: [25, 50, 100, 150, 200], hysteresis_m: 3 },
  // Freefall callouts: levels = metres above the flip start, which the vehicle reports (MissionState.flip_start_altitude).
  freefall: { levels: [5, 10, 15, 25, 50] },
  deviation: { limit_m: 5, hold_s: 2, hysteresis_m: 1 },
  altcheck: { limit_m: 5, hold_s: 1, hysteresis_m: 1 }, // GNSS vs estimated altitude (the GNSS panel uses the same limit)
  failure: { repeat_s: 5, repeats: 0 },
  // Ground distance (lidar, valid below 1 m) spoken while the mission is Running: first at once, then every repeat_s.
  ground_distance: { enabled: true, repeat_s: 1.0 },
  telemetry: { timeout_s: 3 },
  stale_s: 3,
  suppress: { normal_s: 2, failure_s: 8 },
  phaseNames: {
    Hover: "Hover", Ascent: "Ascent", Descent: "Belly flop", Deceleration: "Deceleration", RTH: "Return to home",
    Landing: "Landing", Completed: "Landed",
  },
  styles: {
    normal: { rate: 1.0, pitch: 1.0, volume: 0.9 },
    warning: { rate: 1.15, pitch: 0.9, volume: 1.0 },
    failure: { rate: 1.3, pitch: 0.7, volume: 1.0 },
  },
};

/** Vehicle log notifications (logwatch.js): master, which categories raise toasts, which also beep. */
DEFAULTS.log = {
  enabled: true,
  categories: Object.fromEntries(LOG_CATEGORIES.map((c) => [c, true])),
  audio: Object.fromEntries(LOG_CATEGORIES.map((c) => [c, c === "abort"])), // audio on only for critical
};

const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
const isObj = (x) => x && typeof x === "object" && !Array.isArray(x);
const num = (x, lo, hi, dflt) => (typeof x === "number" && Number.isFinite(x) ? clamp(x, lo, hi) : dflt);

/** Altitude list: finite positive numbers, unique, ascending. Falls back to `dflt` when nothing usable is left. */
export function cleanLevels(list, dflt) {
  if (!Array.isArray(list)) return dflt;
  const out = [...new Set(list.map(Number).filter((x) => Number.isFinite(x) && x > 0 && x < 100000))].sort((a, b) => a - b);
  return out.length ? out : dflt;
}

/**
 * Merge `over` (defaults file or saved settings, untrusted) onto the complete settings `base`. Only known keys of the
 * right type are taken, numbers are clamped, unknown keys are dropped. Returns a new object; `base` is not modified.
 */
export function mergeSettings(base, over) {
  const out = JSON.parse(JSON.stringify(base));
  if (!isObj(over)) return out;
  if (typeof over.master === "boolean") out.master = over.master;
  if (typeof over.speakInReplay === "boolean") out.speakInReplay = over.speakInReplay;
  if (VOICE_MODES.includes(over.voiceMode)) out.voiceMode = over.voiceMode;
  if (typeof over.voice === "string") out.voice = over.voice;
  out.volume = num(over.volume, 0, 1, out.volume);
  out.stale_s = num(over.stale_s, 0.5, 60, out.stale_s);
  if (isObj(over.categories)) for (const c of CATEGORIES) if (typeof over.categories[c] === "boolean") out.categories[c] = over.categories[c];
  if (isObj(over.log)) {
    if (typeof over.log.enabled === "boolean") out.log.enabled = over.log.enabled;
    for (const k of ["categories", "audio"]) {
      if (!isObj(over.log[k])) continue;
      for (const c of LOG_CATEGORIES) if (typeof over.log[k][c] === "boolean") out.log[k][c] = over.log[k][c];
    }
  }
  if (isObj(over.altitude)) {
    out.altitude.levels = cleanLevels(over.altitude.levels, out.altitude.levels);
    out.altitude.hysteresis_m = num(over.altitude.hysteresis_m, 0, 50, out.altitude.hysteresis_m);
  }
  if (isObj(over.freefall)) {
    out.freefall.levels = cleanLevels(over.freefall.levels, out.freefall.levels);
  }
  if (isObj(over.deviation)) {
    out.deviation.limit_m = num(over.deviation.limit_m, 0.1, 10000, out.deviation.limit_m);
    out.deviation.hold_s = num(over.deviation.hold_s, 0, 60, out.deviation.hold_s);
    out.deviation.hysteresis_m = num(over.deviation.hysteresis_m, 0, 1000, out.deviation.hysteresis_m);
  }
  if (isObj(over.altcheck)) {
    out.altcheck.limit_m = num(over.altcheck.limit_m, 0.1, 10000, out.altcheck.limit_m);
    out.altcheck.hold_s = num(over.altcheck.hold_s, 0, 60, out.altcheck.hold_s);
    out.altcheck.hysteresis_m = num(over.altcheck.hysteresis_m, 0, 1000, out.altcheck.hysteresis_m);
  }
  if (isObj(over.failure)) {
    out.failure.repeat_s = num(over.failure.repeat_s, 1, 600, out.failure.repeat_s);
    out.failure.repeats = Math.round(num(over.failure.repeats, 0, 20, out.failure.repeats));
  }
  if (isObj(over.ground_distance)) {
    if (typeof over.ground_distance.enabled === "boolean") out.ground_distance.enabled = over.ground_distance.enabled;
    out.ground_distance.repeat_s = num(over.ground_distance.repeat_s, 0.3, 5, out.ground_distance.repeat_s);
  }
  if (isObj(over.telemetry)) out.telemetry.timeout_s = num(over.telemetry.timeout_s, 0.5, 60, out.telemetry.timeout_s);
  if (isObj(over.suppress)) {
    out.suppress.normal_s = num(over.suppress.normal_s, 0, 60, out.suppress.normal_s);
    out.suppress.failure_s = num(over.suppress.failure_s, 0, 600, out.suppress.failure_s);
  }
  if (isObj(over.phaseNames)) for (const [k, v] of Object.entries(over.phaseNames)) if (typeof v === "string" && v.length < 60 && /^\w+$/.test(k)) out.phaseNames[k] = v;
  if (isObj(over.styles)) {
    for (const s of STYLE_NAMES) {
      const o = over.styles[s];
      if (!isObj(o)) continue;
      out.styles[s].rate = num(o.rate, 0.5, 2, out.styles[s].rate);
      out.styles[s].pitch = num(o.pitch, 0, 2, out.styles[s].pitch);
      out.styles[s].volume = num(o.volume, 0, 1, out.styles[s].volume);
    }
  }
  return out;
}

/** speechSynthesis volume: the style's own volume times the master volume, clamped to 0..1. */
export function effectiveVolume(styleVolume, master) {
  const s = typeof styleVolume === "number" && Number.isFinite(styleVolume) ? styleVolume : 1;
  const m = typeof master === "number" && Number.isFinite(master) ? master : 1;
  return clamp(s * m, 0, 1);
}

/** WebAudio gain of the alert beep at a master volume (0 gives silence). */
export function beepGain(master) {
  return clamp(typeof master === "number" && Number.isFinite(master) ? master : 0, 0, 1) * BEEP_BASE_GAIN;
}

/** Style of a severity: info and ok use "normal". */
export const styleOf = (severity) => (severity === "failure" ? "failure" : severity === "warning" ? "warning" : "normal");
