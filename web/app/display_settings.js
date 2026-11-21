// Per-browser display settings: UI scale and 3D resolution (display only, nothing is sent to the vehicle).
// Pure helpers are unit tested in web/test/display_settings.test.js; the DOM part is applyUiScale().

export const UI_SCALES = ["auto", "0.75", "1", "1.25", "1.5", "2"];
export const RES_SCALES = ["auto", "1", "1.5", "2"];
export const UI_LABELS = { auto: "Auto", 0.75: "75 %", 1: "100 %", 1.25: "125 %", 1.5: "150 %", 2: "200 %" };
export const RES_LABELS = { auto: "Auto", 1: "1x", 1.5: "1.5x", 2: "2x" };
export const MAX_PIXEL_RATIO = 3;
export const MIN_PIXEL_RATIO = 0.5;
export const DEFAULT_DISPLAY = { ui: "auto", res: "auto" };
export const LS_KEY = "exgs.display";

const pick = (v, allowed, dflt) => (allowed.includes(String(v)) ? String(v) : dflt);

/** Validate untrusted (saved) settings: unknown values fall back to Auto. */
export function cleanDisplay(x) {
  const o = x && typeof x === "object" ? x : {};
  return { ui: pick(o.ui, UI_SCALES, "auto"), res: pick(o.res, RES_SCALES, "auto") };
}

/** CSS zoom factor of the whole GUI. Auto is 1: the browser's own zoom / device pixel ratio already apply. */
export function uiZoom(ui) {
  const z = Number(ui);
  return Number.isFinite(z) && z > 0 ? z : 1;
}

/**
 * Renderer pixel ratio. The canvas CSS size is in zoomed CSS pixels, so the device pixels per canvas pixel are
 * dpr * zoom; the 3D resolution multiplies that (Auto = 1x). Capped at MAX_PIXEL_RATIO (memory and fill rate).
 */
export function pixelRatio(dpr, ui, res) {
  const d = Number.isFinite(dpr) && dpr > 0 ? dpr : 1;
  const r = res === "auto" || res === undefined ? 1 : Number(res) || 1;
  return Math.min(MAX_PIXEL_RATIO, Math.max(MIN_PIXEL_RATIO, d * uiZoom(ui) * r));
}

export function loadDisplay(ls) {
  try {
    return cleanDisplay(JSON.parse(ls.get(LS_KEY)));
  } catch (e) {
    return { ...DEFAULT_DISPLAY };
  }
}

let current = { ...DEFAULT_DISPLAY };
export const getDisplay = () => current;

/** Set the current settings, persist them (ls = {get,set}, failures ignored), apply the UI zoom, notify the 3D view. */
export function setDisplay(patch, ls) {
  current = cleanDisplay({ ...current, ...patch });
  try {
    ls?.set(LS_KEY, JSON.stringify(current));
  } catch (e) {
    /* storage unavailable */
  }
  applyUiScale();
  window.dispatchEvent(new Event("exgs-display"));
  return current;
}

export function initDisplay(ls) {
  current = loadDisplay(ls);
  applyUiScale();
}

/** CSS zoom on <html> via the --ui-zoom variable (style.css compensates the viewport-sized elements). */
export function applyUiScale() {
  const root = document.documentElement;
  const z = uiZoom(current.ui);
  root.style.setProperty("--ui-zoom", String(z));
  root.dataset.uiZoom = String(z);
}
