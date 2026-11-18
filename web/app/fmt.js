// Value formatting with units and freshness (design 5.3). Pure functions, no DOM: tested with node --test.
//
// Freshness states of a displayed value:
//   fresh         received within stale_after of the view clock
//   stale         last value is older than stale_after (shown dimmed, with its age)
//   missing       nothing of this packet type received in this view
//   not supplied  the packet arrived but the field was not in it (this link does not carry it)

export const DEFAULT_STALE_S = 2.0;
const RAD2DEG = 180 / Math.PI;

export function fmtNum(v, digits = 2) {
  if (v === null || v === undefined) return "—";
  if (typeof v !== "number") return String(v);
  if (Number.isNaN(v)) return "NaN";
  if (!Number.isFinite(v)) return v > 0 ? "inf" : "-inf";
  const s = v.toFixed(digits);
  return /^-0(\.0*)?$/.test(s) ? s.slice(1) : s; // no "-0.00"
}

export function fmtAge(s) {
  if (s === null || s === undefined || Number.isNaN(s)) return "";
  if (s < 0) s = 0;
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 120) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

/** mm:ss (or h:mm:ss) from a duration in seconds. */
export function fmtClock(s) {
  if (s === null || s === undefined || Number.isNaN(s)) return "--:--";
  const neg = s < 0;
  const t = Math.floor(Math.abs(s));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = t % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return `${neg ? "-" : ""}${h > 0 ? `${h}:${mm}` : mm}:${ss}`;
}

/** Mission time as T-/T+ seconds. */
export function fmtMissionTime(s) {
  if (s === null || s === undefined || Number.isNaN(s)) return "—";
  return `T${s < 0 ? "−" : "+"}${Math.abs(s).toFixed(2)} s`;
}

export function fmtBytes(n) {
  if (n === null || n === undefined) return "—";
  const u = ["B", "kB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < u.length - 1) {
    n /= 1000;
    i++;
  }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
}

/** Local date and time from epoch seconds (display only). */
export function fmtDateTime(epochS) {
  if (epochS === null || epochS === undefined) return "—";
  const d = new Date(epochS * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// ---------------------------------------------------------------- derived values (display only)

/** ZYX (yaw-pitch-roll) angles in radians from q = [w,x,y,z] (body -> world). */
export function quatToRpy(q) {
  if (!Array.isArray(q) || q.length !== 4 || q.some((v) => typeof v !== "number")) return undefined;
  const [w, x, y, z] = q;
  const roll = Math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y));
  const sp = Math.max(-1, Math.min(1, 2 * (w * y - z * x)));
  const pitch = Math.asin(sp);
  const yaw = Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z));
  return [roll, pitch, yaw];
}

/** Angle in radians between the body +Z axis and the world +Z axis (vertical). */
export function tiltFromVertical(q) {
  if (!Array.isArray(q) || q.length !== 4 || q.some((v) => typeof v !== "number")) return undefined;
  const [, x, y] = q;
  return Math.acos(Math.max(-1, Math.min(1, 1 - 2 * (x * x + y * y))));
}

export const rad2deg = (v) => (typeof v === "number" ? v * RAD2DEG : v);

// ---------------------------------------------------------------- freshness

/** Stale threshold in seconds for a store entry, falling back to the schema's nominal rate. */
export function staleAfter(entry, schemaType) {
  if (entry && typeof entry.stale_after === "number") return entry.stale_after;
  const hz = schemaType && schemaType.nominal_rate_hz;
  if (typeof hz === "number" && hz > 0) return Math.max(0.5, 3 / hz);
  return DEFAULT_STALE_S;
}

/** Age in seconds of a store entry at view time `now`. */
export function ageOf(entry, now) {
  return Math.max(0, now - entry.t);
}

export function isStale(entry, now, schemaType) {
  return ageOf(entry, now) > staleAfter(entry, schemaType);
}

/** True when the field value is a non-finite float that the core sent as null with an `nf` marker. */
function nfMarker(entry, field, index) {
  const nf = entry.nf && entry.nf[field];
  if (nf === undefined) return undefined;
  if (Array.isArray(nf)) return index === undefined ? nf.find((m) => m) : nf[index];
  return nf;
}

const NF_TEXT = { nan: "NaN", inf: "inf", "-inf": "-inf" };

/**
 * Describe one value for display.
 *
 * spec: { type | types[], field, index, src, unit, digits, scale, kind, format, derive, labels }
 *   kind: "num" (default), "vec" (array shown as "a  b  c"), "text", "bool", "enum", "time"
 *   derive(entries) -> value | undefined, replaces reading `field` (display-only derived values)
 *   format(value) -> string, replaces the default formatting
 * lookup(type, src) -> entry | undefined;  schemaOf(type) -> schema type | undefined
 * Returns { state, text, unit, age, src, value, title }.
 */
export function describe(spec, lookup, now, schemaOf = () => undefined) {
  const types = spec.types || [spec.type];
  const entries = types.map((t) => lookup(t, spec.src));
  if (entries.some((e) => !e)) {
    return { state: "missing", text: "—", unit: "", age: null, src: "", value: undefined, title: "no data received" };
  }
  let age = 0;
  let stale = false;
  for (let i = 0; i < entries.length; i++) {
    const a = ageOf(entries[i], now);
    age = Math.max(age, a);
    if (a > staleAfter(entries[i], schemaOf(types[i]))) stale = true;
  }
  const src = [...new Set(entries.map((e) => e.src))].join("+");

  let value;
  let marker;
  if (spec.derive) {
    value = spec.derive(entries.length === 1 ? entries[0] : entries);
  } else {
    const raw = entries[0].f ? entries[0].f[spec.field] : undefined;
    marker = nfMarker(entries[0], spec.field, spec.index);
    value = spec.index !== undefined && Array.isArray(raw) ? raw[spec.index] : raw;
    if (value === undefined && marker === undefined) {
      return {
        state: "not supplied",
        text: "n/s",
        unit: "",
        age,
        src,
        value: undefined,
        title: `${spec.field} not supplied by ${src}`,
      };
    }
  }
  if (value === undefined) {
    return { state: "not supplied", text: "n/s", unit: "", age, src, value, title: "not available" };
  }

  const state = stale ? "stale" : "fresh";
  let text;
  if (value === null) {
    text = NF_TEXT[marker] || "—";
  } else if (spec.format) {
    text = spec.format(value);
  } else {
    text = formatByKind(value, spec, entries[0], marker);
  }
  const unit = spec.unit && value !== null && spec.kind !== "text" && spec.kind !== "enum" && spec.kind !== "bool" ? spec.unit : "";
  return { state, text, unit, age, src, value, title: `${src}, ${fmtAge(age)} old` };
}

function formatByKind(value, spec, entry, marker) {
  const digits = spec.digits ?? 2;
  const scale = spec.scale ?? 1;
  switch (spec.kind || "num") {
    case "vec": {
      if (!Array.isArray(value)) return String(value);
      const nf = entry.nf && entry.nf[spec.field];
      return value
        .map((v, i) => (v === null || v === undefined ? (Array.isArray(nf) && nf[i] && NF_TEXT[nf[i]]) || "—" : fmtNum(scale * v, digits)))
        .join("  ");
    }
    case "bool":
      return value ? (spec.labels ? spec.labels[1] : "yes") : spec.labels ? spec.labels[0] : "no";
    case "time":
      return fmtMissionTime(value);
    case "enum":
    case "text":
      return String(value);
    default:
      return fmtNum(typeof value === "number" ? scale * value : value, digits);
  }
}

/** Worst freshness of several descriptions, for panel-level badges. */
export function worstState(states) {
  for (const s of ["missing", "stale", "not supplied", "fresh"]) if (states.includes(s)) return s;
  return "missing";
}
