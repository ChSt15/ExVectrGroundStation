// Pure logic of the two link directions (docs/gui.md "Link directions"). Display only.
//
// The radio link has two directions and two module stat packets, never to be mixed up:
//   Downlink (ship -> GS):  TxModuleStats = LINK_STATS of the Router (node 2, in the Pocket). Its rx_lq / rssi / snr
//                           describe what the Router receives, i.e. the ship's signal.
//   Uplink (GS -> ship):    RxModuleStats = LINK_STATS of the ship radio node (node 3, forwarded by the Router). Its
//                           rx_lq / rssi / snr describe what the ship receives, i.e. the ground station's signal.
// A missing direction is "no data"; the other direction's value is never shown in its place.
import { ageOf, staleAfter } from "../fmt.js";

export const DIRECTIONS = [
  { id: "down", type: "TxModuleStats", title: "Downlink (ship -> GS)", short: "DL", node: "measured by the Router (node 2), TxModuleStats" },
  { id: "up", type: "RxModuleStats", title: "Uplink (GS -> ship)", short: "UL", node: "measured by the ship radio node (node 3), RxModuleStats" },
];
export const DIRECTION_BY_ID = new Map(DIRECTIONS.map((d) => [d.id, d]));

/** Tone of one link quality value in percent: bad below 50, warn below 80. Never applied to stale or missing data. */
export function lqTone(v) {
  return typeof v !== "number" || !Number.isFinite(v) ? "" : v < 50 ? "bad" : v < 80 ? "warn" : "ok";
}

/**
 * State of one direction from its store entry (undefined = never received). Returns
 * { state: "nodata" | "stale" | "fresh", age, badge, tone, f }. `f` holds the fields only when an entry exists.
 */
export function directionState(dir, entry, now, schemaType) {
  if (!entry) return { dir, state: "nodata", age: null, badge: "no data", tone: "", f: {} };
  const age = ageOf(entry, now);
  const stale = age > staleAfter(entry, schemaType);
  const f = entry.f || {};
  return {
    dir,
    state: stale ? "stale" : "fresh",
    age,
    badge: stale ? `STALE ${age.toFixed(age < 10 ? 1 : 0)} s` : "live",
    tone: stale ? "" : lqTone(f.rx_lq),
    f,
  };
}

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);

/** Text of a number with a unit, or "n/s" when the field is not supplied. */
export function fmtField(v, unit = "", digits = 0) {
  const n = num(v);
  return n === undefined ? "n/s" : `${n.toFixed(digits)}${unit ? ` ${unit}` : ""}`;
}

/** Short chip text of one direction for the header: "DL 40%", "DL stale 40%" or "DL no data". */
export function chipText(st) {
  const lq = num(st.f.rx_lq);
  if (st.state === "nodata") return `${st.dir.short} no data`;
  const v = lq === undefined ? "n/s" : `${lq}%`;
  return st.state === "stale" ? `${st.dir.short} stale ${v}` : `${st.dir.short} ${v}`;
}

/** Title (tooltip) of the header chip of one direction. */
export function chipTitle(st) {
  const base = `${st.dir.title}: link quality ${st.dir.node}`;
  if (st.state === "nodata") return `${base}. No data received.`;
  const r = num(st.f.rssi);
  return `${base}. RSSI ${r === undefined ? "n/s" : `${r} dBm`}. ${st.state === "stale" ? `STALE, last update ${st.age.toFixed(1)} s ago` : "live"}`;
}

/** Counters of a direction as one text, for the panel: "expired 0, dropped 0". */
export function counterText(f) {
  return `expired ${fmtField(f.frames_expired)}, dropped ${fmtField(f.frames_dropped)}`;
}

/** Antenna text: "rx 0 / tx 1". */
export function antennaText(f) {
  return num(f.rx_antenna) === undefined && num(f.tx_antenna) === undefined ? "n/s" : `rx ${fmtField(f.rx_antenna)} / tx ${fmtField(f.tx_antenna)}`;
}

/** Tone of an expired / dropped counter: any non-zero value is a warning (cumulative since the module started). */
export const counterTone = (v) => (typeof v === "number" && v > 0 ? "warn" : "");
