// RC applied panel (T9b-1): pure helpers. Display only: the vehicle's own decode of the RC_STATE switches (RC_APPLIED,
// USB only), the safe-seen bits, the age of both control sources and the reject counters. Nothing here sends anything.
export const INVALID = 255;

/** Switch rows: id, label, meaning per decoded value. sb/sc 255 = invalid; sa/sd/se report 0 for invalid. */
export const SWITCHES = [
  { id: "sa", label: "SA", meaning: "go / abort", values: ["abort (not go)", "go"] },
  { id: "sb", label: "SB", meaning: "mission type", values: ["type 0", "type 1", "type 2"] },
  { id: "sc", label: "SC", meaning: "simulation / real", values: ["simulation", "real", "reserved"] },
  { id: "sd", label: "SD", meaning: "motor enable", values: ["disabled", "enabled"] },
  { id: "se", label: "SE", meaning: "reset / zero", values: ["released", "pressed"] },
];

/** Safe-seen bits (a set bit: the safe value was seen, an edge may be reported). */
export const SAFE_SEEN = ["GO", "SC real", "SD enable", "SE press"];
export const REJECT_LABELS = ["bad length", "unknown source", "stale sequence", "SOURCE_OK clear"];

export function switchText(id, v) {
  const sw = SWITCHES.find((s) => s.id === id);
  if (!sw || typeof v !== "number") return "n/s";
  if (v === INVALID) return "invalid";
  return sw.values[v] ?? `unknown (${v})`;
}

/** Tone of a switch value: invalid is amber, everything else neutral (no value is an alarm by itself). */
export const switchTone = (v) => (v === INVALID ? "warn" : "");

export function safeSeenList(bits) {
  if (typeof bits !== "number") return [];
  return SAFE_SEEN.map((name, i) => ({ name, seen: !!(bits & (1 << i)) }));
}

export function ageText(a) {
  return typeof a === "number" && Number.isFinite(a) ? `${a.toFixed(2)} s` : "never";
}

export function rejectList(r) {
  return REJECT_LABELS.map((label, i) => ({ label, count: Array.isArray(r) && typeof r[i] === "number" ? r[i] : null }));
}
