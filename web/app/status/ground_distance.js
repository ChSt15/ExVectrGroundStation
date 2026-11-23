// Ground distance tile logic (pure): the downward lidar reading Actuators.ground_distance in metres. NaN (sent as null
// with a NaN marker) means no valid reading: real sensor out of range or stale, simulated lidar only below 1 m.
export const GD_STALE_S = 3;

/** {text, unit, state} for the tile; state: ok | none (no reading) | stale | missing (no Actuators received). */
export function groundDistanceView(entry, now, staleS = GD_STALE_S) {
  if (!entry || !entry.f) return { text: "—", unit: "", state: "missing" };
  const v = entry.f.ground_distance;
  if (typeof v !== "number" || !Number.isFinite(v)) return { text: "no reading", unit: "", state: "none" };
  const stale = Number.isFinite(now) && now - entry.t > staleS;
  return { text: v.toFixed(2), unit: "m", state: stale ? "stale" : "ok" };
}
