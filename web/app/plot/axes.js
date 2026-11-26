// Axis assignment and ranges of the tuning graphs: every series sits on the left or the right y axis (series.axis
// "L" | "R", default L; saved graphs without it keep working). No DOM, unit tested in web/test/axes.test.js. Display only.
import { paddedRange, valueRange } from "./series.js";

export const sideOf = (x) => (x && x.axis === "R" ? "R" : "L");
export const hasRight = (series) => series.some((x) => sideOf(x) === "R");
export const seriesOn = (series, side) => series.filter((x) => sideOf(x) === side);

/** y range of one axis: a valid manual [lo, hi] wins, else the padded range of the decimated value lists of ITS series. */
export function axisRange(manual, lists) {
  if (manual && Number.isFinite(manual[0]) && Number.isFinite(manual[1]) && manual[1] > manual[0]) return manual;
  return paddedRange(valueRange(lists));
}

/** Manual range from the two input boxes of an axis (strings or numbers), null when auto or invalid. */
export function manualRange(auto, lo, hi) {
  if (auto) return null;
  const a = Number(lo);
  const b = Number(hi);
  return Number.isFinite(a) && Number.isFinite(b) && b > a ? [a, b] : null;
}

/** Plot-area pads (px) for a graph with or without a right axis (the labelled axes need more room). */
export function padsFor(dual) {
  return dual ? { l: 64, r: 64 } : { l: 50, r: 10 };
}
