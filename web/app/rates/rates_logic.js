// Pure logic of the "Data rates" panel and the air figure of the status bar (docs/gui.md). Display only: the numbers
// are what the core measured on receive (`get_rates`, docs/api.md), nothing is inferred from what was sent.
export const STALE_MIN_S = 3; // a stream not seen for this long (or 3 nominal periods) is greyed
export const LOW_FRACTION = 0.7; // below this share of the nominal rate the row gets a warning colour
export const UTIL_WARN = 0.8;

export const fmtHz = (x) => (typeof x !== "number" || !Number.isFinite(x) ? "—" : x >= 10 ? x.toFixed(0) : x >= 1 ? x.toFixed(1) : x.toFixed(2));
export function fmtBytes(x) {
  if (typeof x !== "number" || !Number.isFinite(x)) return "n/a";
  return x >= 10000 ? `${(x / 1000).toFixed(1)} kB/s` : `${Math.round(x)} B/s`;
}
const fmtAgeS = (a) => (a < 10 ? `${a.toFixed(1)} s` : a < 600 ? `${a.toFixed(0)} s` : `${(a / 60).toFixed(0)} min`);

/** Rate used to compare with the nominal one: slow streams (< 2 Hz) are judged on the 10 s window. */
export function judgedRate(row) {
  const r = row.rate_hz || [];
  return row.nominal_hz && row.nominal_hz < 2 ? (r[1] ?? r[0] ?? 0) : (r[0] ?? 0);
}

export function describeRow(row) {
  const nominal = row.nominal_hz || null;
  const staleAfter = Math.max(STALE_MIN_S, nominal ? 3 / nominal : 0);
  const stale = row.age_s > staleAfter || !((row.rate_hz && row.rate_hz[0]) || (row.rate_hz && row.rate_hz[1]));
  const jr = judgedRate(row);
  const low = !stale && nominal !== null && jr < LOW_FRACTION * nominal;
  return {
    key: `${row.source}/${row.type}/${row.node ?? "-"}/${row.path}`,
    type: row.type,
    path: row.path,
    source: row.source,
    node: row.node,
    rateText: fmtHz(row.rate_hz && row.rate_hz[0]),
    rate10Text: fmtHz(row.rate_hz && row.rate_hz[1]),
    nominalText: nominal === null ? "—" : fmtHz(nominal),
    bytesText: row.bytes_s ? fmtBytes(row.bytes_s[0]) : "n/a",
    ageText: fmtAgeS(row.age_s),
    state: stale ? "stale" : "fresh",
    tone: low ? "warn" : "",
    low,
    title: low ? `${fmtHz(jr)} Hz is below the nominal ${fmtHz(nominal)} Hz` : "",
  };
}

/** Air path utilisation: payload B/s against the capacity (data.air). */
export function describeAir(data) {
  const air = data && data.air;
  const pay = air && air.payload_s;
  if (!air || !pay) return { known: false, text: "air: n/a", frac: 0, tone: "", payload: null, capacity: air ? air.capacity_bytes_s : null };
  const frac = air.utilisation ? air.utilisation[0] : 0;
  return {
    known: true,
    payload: pay[0],
    capacity: air.capacity_bytes_s,
    frac,
    tone: frac > 1 ? "bad" : frac > UTIL_WARN ? "warn" : "",
    text: `air: ${fmtBytes(pay[0])}`,
    long: `${fmtBytes(pay[0])} of payload from the vehicle side against about ${fmtBytes(air.capacity_bytes_s)} air capacity per direction (${(frac * 100).toFixed(0)} %)`,
  };
}

export function describeRates(data) {
  if (!data) return { rows: [], paths: [], air: describeAir(null), measure: "frames" };
  return {
    rows: (data.streams || []).map(describeRow),
    paths: (data.paths || []).map((p) => ({
      path: p.path,
      streams: p.streams,
      rateText: fmtHz(p.rate_hz[0]),
      bytesText: p.bytes_s ? fmtBytes(p.bytes_s[0]) : "n/a",
      payloadText: p.payload_s ? fmtBytes(p.payload_s[0]) : "n/a",
    })),
    air: describeAir(data),
    measure: data.measure || "frames",
  };
}

export const PATH_HINT = {
  air: "crossed the radio link (node 1 or 3, received through the Router)",
  "usb direct": "from the vehicle on its own USB",
  "router local": "made by the Router itself, or the Pocket's RC_STATE copy: never on the air",
  simulated: "test double or a recording without frame information",
  unknown: "origin node not known",
};
