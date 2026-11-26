// Replay view of the tuning graphs (docs/gui.md "Tuning view", "Replay"): the graphs show the whole recording, the
// replay position is a bar. No DOM, unit tested in web/test/replayview.test.js. Display only.

/** [tStart, tEnd] of the open log from the replay status (seconds), or null while no log is ready. */
export function logRange(rs) {
  if (!rs || rs.state === "closed" || rs.state === "loading") return null;
  if (!Number.isFinite(rs.t_start) || !Number.isFinite(rs.t_end)) return null;
  return [rs.t_start, Math.max(rs.t_end, rs.t_start + 1e-3)];
}

/** Key of the loaded recording: the whole-log data is loaded once per key, not per seek. */
export const logKey = (rs) => (logRange(rs) ? `${rs.name}|${rs.t_start}|${rs.t_end}` : null);

/**
 * Visible time window in replay. `frozen` ([a, b], after a zoom / pan) keeps its span and is clamped into the log;
 * windowS = Infinity ("all", the default) is the whole recording; a finite preset is centred on the replay position
 * `pos` and kept inside the log.
 */
export function replayWindow({ range, pos, windowS, frozen }) {
  const [a, b] = range;
  const total = b - a;
  if (frozen) {
    const span = Math.min(frozen[1] - frozen[0], total);
    const lo = Math.max(a, Math.min(frozen[0], b - span));
    return [lo, lo + span];
  }
  if (!Number.isFinite(windowS) || windowS >= total) return [a, b];
  const p = Number.isFinite(pos) ? pos : a;
  const lo = Math.max(a, Math.min(p - windowS / 2, b - windowS));
  return [lo, lo + windowS];
}

/** Replay position (seconds) for a seek to time t, clamped into the log. */
export const clampToLog = (t, range) => Math.max(range[0], Math.min(range[1], t));

/** True when pixel x is within `tol` px of the replay bar (grab it to scrub). */
export const nearBar = (x, barX, tol = 6) => Number.isFinite(barX) && Math.abs(x - barX) <= tol;

/** Decimated points allowed per signal when the whole log is loaded: at most ~2 per pixel column of a wide screen. */
export const REPLAY_MAX_POINTS = 20000;
