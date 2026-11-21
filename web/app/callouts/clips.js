// Recorded callout clips (docs/callouts.md "Recorded voice"): composition of a callout text into a list of clip ids,
// and the WebAudio player. Display/audio only. The composition is pure and unit tested (web/test/callouts_clips.test.js).

export const VOICE_ID = "en_GB-northern_english_male-medium";
export const CLIP_GAP_S = 0.06; // silence between chained clips
const MAX_NUM = 99;
const UNITS = { m: "meters", "m/s": "meters per second", s: "seconds", deg: "degrees", "°": "degrees", "%": "percent" };

const norm = (w) => w.toLowerCase().replace(/[.,;:!?]+$/, "");

/**
 * Clip ids that speak the number string `tok` ("12.5", "-3", "100", "0.0"), or null when it cannot be composed.
 * 0..99 and 100..900 are single clips, 1000..99999 compose as thousands + hundreds + rest, more is read digit by digit.
 */
export function numberClips(tok) {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(tok);
  if (!m) return null;
  const out = [];
  if (m[1]) out.push("minus");
  const int = m[2].replace(/^0+(?=\d)/, "");
  const n = Number(int);
  const digits = (s) => [...s].map((d) => `num-${d}`);
  if (int.length > 5) out.push(...digits(int));
  else {
    const rest = (r) => {
      if (r <= MAX_NUM) return [`num-${r}`];
      const h = Math.floor(r / 100) * 100;
      return r % 100 ? [`num-${h}`, `num-${r % 100}`] : [`num-${h}`];
    };
    if (n >= 1000) {
      const k = Math.floor(n / 1000);
      out.push(...rest(k), "thousand");
      if (n % 1000) out.push(...(n % 1000 <= MAX_NUM ? [`num-${n % 1000}`] : rest(n % 1000)));
    } else out.push(...rest(n));
  }
  if (m[3]) out.push("point", ...digits(m[3].slice(0, 3)));
  return out;
}

/**
 * Index of a manifest: spoken phrase (normalised) -> clip id for the phrase clips (not the num-N ones), the longest
 * phrase in words, and the set of all ids. `manifest` = web/audio/<voice>/manifest.json.
 */
export function buildIndex(manifest) {
  const phrases = new Map();
  const ids = new Set();
  let maxWords = 1;
  for (const [id, c] of Object.entries(manifest.clips || {})) {
    ids.add(id);
    if (id.startsWith("num-")) continue;
    const words = c.text.split(/\s+/).map(norm);
    phrases.set(words.join(" "), id);
    maxWords = Math.max(maxWords, words.length);
  }
  return { phrases, ids, maxWords };
}

/**
 * Compose a callout text: a whole-phrase clip, or words/phrases/numbers/units in order ("Altitude 50", "T minus 5",
 * "Failure bit 7", "12.5 m"). Returns the clip id list, or null when any part has no clip (the caller falls back to
 * browser speech or the beep).
 */
export function composeText(text, index) {
  const words = String(text).trim().split(/\s+/).filter(Boolean);
  if (!words.length) return null;
  const out = [];
  for (let i = 0; i < words.length; ) {
    let hit = null;
    for (let n = Math.min(index.maxWords, words.length - i); n >= 1 && !hit; n--) {
      const id = index.phrases.get(words.slice(i, i + n).map(norm).join(" "));
      if (id) hit = { id, n };
    }
    if (hit) {
      out.push(hit.id);
      i += hit.n;
      continue;
    }
    const w = words[i].replace(/[,;:!?]+$/, "");
    const num = numberClips(w);
    const unit = UNITS[w.toLowerCase()];
    const ids = num || (unit && out.length ? [index.phrases.get(unit)] : null);
    if (!ids || !ids.every((id) => id && index.ids.has(id))) return null;
    out.push(...ids);
    i += 1;
  }
  return out;
}
