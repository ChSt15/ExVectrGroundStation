// Mission event list (docs/gui.md "Mission events"): WHAT HAPPENED WHEN, stamped with the continuous mission time (T-
// before T0, T+ after, ./mission_clock.js) and the wall time. Pure, no DOM, unit tested in web/test/mission_events.test.js.
// Display only: derived from vehicle-reported packets and from the GS's own events, nothing is sent.
//
// One stateful deriver, the same code for a whole recording (feedAll, replay) and for the live stream (feed per packet):
//   const ev = new MissionEvents(); ev.feedAll(packets) | ev.feed(pkt); ev.build() -> { rows, events, missions }
// Packets are `{type, src, t, f}` as the API sends them (t = epoch seconds, enums as names). The deriver only looks at a
// VALUE CHANGE of a field, so a stream thinned to its changes (core/event_history.py) and the full stream give the
// same events. Two sources reporting the same change (USB and Router) give ONE event (same field and value within
// DEDUP_S). The first value seen is a silent baseline, except the states worth knowing at the start of the data
// (armed, motors enabled, simulation, failsafe, set failure bits, the vehicle mode: "(state at start of data)").
//
// Event: { t, tm, wall, cat, tone, text, type, count, section }
//   t = wall = epoch seconds; tm = mission time (s) or null before the first mission started; cat = mission | vehicle |
//   command | link | log; tone = info | ok | warn | bad; type = source packet type (debugging); count = repeats folded
//   into it (console and GS lines identical to the previous one within REPEAT_S); section = mission index or -1.
// Mission events (countdown start, T0, end) come from the mission clock, not from the packets directly.
import { MissionClock } from "./mission_clock.js";
import { FAILURE_BIT_NAMES, CTRL_FLAGS, GO_REFUSALS, DEGRADED_BITS } from "./status_logic.js";
import { classify, parseLine, VEHICLE_NODE } from "../callouts/log_patterns.js";
import { fmtT } from "../plot/missionclock.js";

export const CATEGORIES = ["mission", "vehicle", "command", "link", "log"];
export const CATEGORY_LABELS = { mission: "Mission", vehicle: "Vehicle", command: "Command", link: "Link", log: "Log" };
export const DEDUP_S = 3; // the same change from another source inside this window is the same event
export const REPEAT_S = 60; // an identical console / GS line inside this window is folded into the previous event
export const MAX_EVENTS = 20000;
const SAMPLE_GAP_S = 0.9; // MissionState samples handed to the mission clock: at most about one per second

const num = (v) => typeof v === "number" && Number.isFinite(v);
const startNote = " (state at start of data)";

/** Console lines of the flight software worth a line of their own (first match wins, before the notification patterns). */
export const EVENT_LINES = [
  { re: /^Started bellyflop kick/, text: () => "Bellyflop kick started" },
  { re: /^Bellyflop kick finished \((.*?)\)/, text: (m) => `Bellyflop kick finished (${m[1]})` },
  { re: /^RTH: descent starts/, text: (m, t) => t.replace(/^RTH: /, "RTH ") },
  { re: /^(WARNING: )?LANDING declared at/i, text: (m, t) => t.replace(/^WARNING: /, ""), tone: (m) => (m[1] ? "warn" : undefined) },
  { re: /^Landing declared at/, text: (m, t) => t },
];

/** Same-value helper tables: [field, text(value, prev), tone(value), reportAtStart(value)] for booleans of VehicleStatus. */
const VS_BOOLS = [
  ["armed", (v) => (v ? "Armed" : "Disarmed"), (v) => (v ? "warn" : "info"), (v) => v === true, "vehicle"],
  ["motors_enabled", (v) => (v ? "Motors enabled (gate open)" : "Motors disabled (gate closed)"), (v) => (v ? "warn" : "info"), (v) => v === true, "vehicle"],
  ["simulation", (v) => (v ? "Simulation on" : "Simulation off"), () => "info", (v) => v === true, "vehicle"],
  ["failsafe", (v) => (v ? "Failsafe set" : "Failsafe cleared"), (v) => (v ? "bad" : "ok"), (v) => v === true, "vehicle"],
  ["ready_to_arm", (v) => (v ? "Ready to arm" : "No longer ready to arm"), (v) => (v ? "ok" : "warn"), () => false, "vehicle"],
  ["gs_present", (v) => (v ? "Vehicle hears the ground station" : "Vehicle lost the ground station"), (v) => (v ? "ok" : "warn"), () => false, "link"],
];

/** Command fields (CmdState = what the GS sends, CmdApplied = what the vehicle applied): [field, text(value), tone(value), atStart(value)]. */
const cmdFields = (who, applied) => [
  ["go", (v) => `${who} ${v ? "GO (SA)" : "go off (SA)"}`, (v) => (v ? "warn" : "info"), (v) => v === 1],
  ["profile", (v) => `${who} mission profile ${v} (SB)`, () => "info", (v) => v !== 0],
  ["vehicle_mode", (v) => `${who} ${applied ? "vehicle mode" : "mode"} ${v === 0 || v === "REAL" ? "REAL" : "SIMULATION"} (SC)`, () => "info", (v) => v !== 0 && v !== "REAL"],
  ["motors", (v) => `${who} motors ${v ? (applied ? "enabled" : "armed") : applied ? "disabled" : "disarmed"} (SD)`, (v) => (v ? "warn" : "info"), (v) => v === 1],
  ["reset_button", (v) => `${who} SE ${v ? "pressed" : "released"}`, () => "info", (v) => v === 1],
  ["failsafe", (v) => `${who} ${v ? "GS FAILSAFE requested" : "GS failsafe request cleared"}`, (v) => (v ? "bad" : "ok"), (v) => v === 1],
];
const CMD_SENT = cmdFields("Command", false);
const CMD_APPLIED = cmdFields("Vehicle applied", true);

const hex = (n) => `0x${(n >>> 0).toString(16)}`;

export class MissionEvents {
  constructor(capacity = MAX_EVENTS) {
    this.cap = capacity;
    this.reset();
  }

  reset() {
    this.clock = new MissionClock();
    this.ev = [];
    this.seq = 0;
    this.last = new Map(); // `${src}|${field}` -> last value of that source
    this.glast = new Map(); // field -> {v, t} of the last emitted change
    this.lastText = new Map(); // `${type}|${text}` -> event, for the repeat folding
    this.lastSample = -Infinity;
    this.lastPhase = undefined;
    this.lastMode = undefined;
    this.maxT = -Infinity;
  }

  /** Newest packet time fed (live reloads drop queued packets up to it). */
  get lastT() {
    return this.maxT;
  }

  /** Sort by time (stable) and feed all of them. */
  feedAll(packets) {
    const sorted = [...packets].sort((a, b) => a.t - b.t);
    for (const p of sorted) this.feed(p);
    return this;
  }

  /** One packet; unknown types are ignored. */
  feed(pkt) {
    const f = pkt.f || {};
    const t = pkt.t;
    if (!num(t)) return;
    this.maxT = Math.max(this.maxT, t);
    switch (pkt.type) {
      case "MissionState":
        return this._missionState(pkt, f);
      case "VehicleStatus":
        return this._vehicleStatus(pkt, f);
      case "CmdState":
        return this._cmd(pkt, f, CMD_SENT, "");
      case "CmdApplied":
        return this._cmd(pkt, f, CMD_APPLIED, "applied.");
      case "GsEvent":
        return this._gsEvent(pkt, f);
      case "Note":
        if (typeof f.text === "string" && f.text) this._add(pkt.t, "log", "info", `Note: ${f.text}`, "Note", true);
        return;
      case "ConsoleText":
        return this._console(pkt, f);
      default:
    }
  }

  // ---------------------------------------------------------------- change tracking

  /**
   * A value change of `field` from source `src`: returns {prev, atStart} when it is news, else null. `atStart` = first
   * value seen from any source (reported only when `reportFirst(value)`, else a silent baseline).
   */
  _change(field, src, v, t, reportFirst = () => false) {
    if (v === undefined || v === null) return null;
    const k = `${src}|${field}`;
    const prev = this.last.get(k);
    this.last.set(k, v);
    if (prev === v) return null;
    const g = this.glast.get(field);
    if (prev === undefined) {
      if (g || !reportFirst(v)) {
        if (!g) this.glast.set(field, { v, t, quiet: true });
        return null;
      }
      this.glast.set(field, { v, t });
      return { prev: undefined, atStart: true };
    }
    if (g && g.v === v && t - g.t < DEDUP_S) return null; // the same change already reported by another source
    this.glast.set(field, { v, t });
    return { prev, atStart: false };
  }

  _add(t, cat, tone, text, type, fold = false) {
    if (fold) {
      const key = `${type}|${text}`;
      const old = this.lastText.get(key);
      if (old && t - old.last < REPEAT_S) {
        old.ev.count++;
        old.last = t;
        return;
      }
      const ev = { t, tm: null, wall: t, cat, tone, text, type, count: 1, section: -1, seq: this.seq++ };
      this.lastText.set(key, { ev, last: t });
      this._push(ev);
      return;
    }
    this._push({ t, tm: null, wall: t, cat, tone, text, type, count: 1, section: -1, seq: this.seq++ });
  }

  _push(ev) {
    this.ev.push(ev);
    if (this.ev.length > this.cap) {
      this.ev.splice(0, this.ev.length - this.cap);
      if (this.lastText.size > 4000) this.lastText.clear();
    }
  }

  // ---------------------------------------------------------------- packet types

  _missionState(pkt, f) {
    const t = pkt.t;
    const tm = f.t_mission;
    if (t - this.lastSample >= SAMPLE_GAP_S || f.mission_phase !== this.lastPhase || f.mission_mode !== this.lastMode) {
      if (this.clock.add(t, num(tm) ? tm : null, f.mission_phase, f.mission_mode, f.mission_elapsed)) this.lastSample = t;
      this.lastPhase = f.mission_phase;
      this.lastMode = f.mission_mode;
    }
    const type = "MissionState";
    const phase = this._change("mission_phase", pkt.src, f.mission_phase, t);
    if (phase) this._add(t, "mission", "info", `Mission phase: ${phase.prev} → ${f.mission_phase}`, type);
    const mode = this._change("mission_mode", pkt.src, f.mission_mode, t);
    if (mode) this._add(t, "mission", "info", `Mission mode: ${mode.prev} → ${f.mission_mode}`, type);
    const idx = this._change("mission_index", pkt.src, num(f.mission_index) ? f.mission_index : undefined, t);
    if (idx) this._add(t, "mission", "info", `Mission index ${idx.prev} → ${f.mission_index}`, type);
    const wp = this._change("waypoint", pkt.src, num(f.waypoint) ? f.waypoint : undefined, t);
    if (wp) this._add(t, "mission", "info", `Waypoint ${wp.prev} → ${f.waypoint}`, type);
  }

  _vehicleStatus(pkt, f) {
    const { t, src } = pkt;
    const type = "VehicleStatus";
    const mode = this._change("mode", src, f.mode, t, () => true);
    if (mode) this._add(t, "vehicle", f.mode === "Failure" ? "bad" : "info", mode.atStart ? `Vehicle mode: ${f.mode}${startNote}` : `Vehicle mode: ${mode.prev} → ${f.mode}`, type);
    for (const [field, text, tone, atStart, cat] of VS_BOOLS) {
      const v = typeof f[field] === "boolean" ? f[field] : undefined;
      const c = this._change(field, src, v, t, atStart);
      if (c) this._add(t, cat, tone(v), text(v) + (c.atStart ? startNote : ""), type);
    }
    if (num(f.failure_bits)) {
      for (let i = 0; i < 32; i++) {
        const on = (f.failure_bits & (1 << i)) !== 0;
        const c = this._change(`failure.${i}`, src, on, t, (v) => v === true);
        if (c) this._add(t, "vehicle", on ? "bad" : "ok", `Failure ${on ? "set" : "cleared"}: ${FAILURE_BIT_NAMES[i] ?? `bit ${i}`}${c.atStart ? startNote : ""}`, type);
      }
    }
    if (num(f.ctrl_flags)) {
      CTRL_FLAGS.forEach(([name, tone], i) => {
        if (tone !== "warn" && tone !== "bad") return; // freshness and bookkeeping bits are not events
        const on = (f.ctrl_flags & (1 << i)) !== 0;
        const c = this._change(`ctrlflag.${i}`, src, on, t, (v) => v === true);
        if (c) this._add(t, "vehicle", on ? tone : "ok", `Vehicle flag ${on ? "set" : "cleared"}: ${name}${c.atStart ? startNote : ""}`, type);
      });
    }
    if (num(f.degraded_bits)) {
      DEGRADED_BITS.forEach((name, i) => {
        const on = (f.degraded_bits & (1 << i)) !== 0;
        const c = this._change(`degraded.${i}`, src, on, t, (v) => v === true);
        if (c) this._add(t, i === 0 ? "vehicle" : "link", on ? "warn" : "ok", `${on ? "Degraded" : "Degraded cleared"}: ${name}` + (c.atStart ? startNote : ""), type);
      });
    }
    const gr = this._change("go_refusal", src, num(f.go_refusal) ? f.go_refusal : undefined, t);
    if (gr && f.go_refusal !== 0) this._add(t, "command", "warn", `Go refused: ${GO_REFUSALS[f.go_refusal] ?? `reason ${f.go_refusal}`}`, type);
    const cs = this._change("ctrl_source", src, f.ctrl_source, t);
    if (cs) this._add(t, "command", "info", `Control source: ${cs.prev} → ${f.ctrl_source}`, type);
    const mp = this._change("mission_profile", src, num(f.mission_profile) ? f.mission_profile : undefined, t);
    if (mp && f.mission_profile !== 255) this._add(t, "command", "info", `Mission profile ${f.mission_profile} latched at go (SB)`, type);
  }

  _cmd(pkt, f, table, prefix) {
    const { t, src } = pkt;
    const type = pkt.type;
    if (!prefix) {
      const s = this._change("cmd.session", src, num(f.session) ? f.session : undefined, t, () => true);
      if (s) this._add(t, "command", "info", `Command session ${hex(f.session)} started${s.atStart ? startNote : ""}`, type);
    }
    for (const [field, text, tone, atStart] of table) {
      const v = f[field];
      const c = this._change(prefix + field, src, v, t, atStart);
      if (c) this._add(t, "command", tone(v), text(v) + (c.atStart ? startNote : ""), type);
    }
    if (!prefix) {
      const pl = this._change("cmd.pocket_live", src, typeof f.pocket_live === "boolean" ? f.pocket_live : undefined, t);
      if (pl) this._add(t, "command", pl.prev === true && f.pocket_live === false ? "warn" : "info", f.pocket_live ? "Pocket stream live" : "Pocket stream lost", type);
      const iv = this._change("cmd.input_virtual", src, typeof f.input_virtual === "boolean" ? f.input_virtual : undefined, t);
      if (iv) this._add(t, "command", "info", f.input_virtual ? "Command input: virtual panel" : "Command input: Pocket", type);
    }
  }

  _gsEvent(pkt, f) {
    const kind = String(f.kind ?? "");
    const d = typeof f.detail === "string" ? f.detail : "";
    const t = pkt.t;
    const add = (cat, tone, text) => this._add(t, cat, tone, text, "GsEvent", true);
    switch (kind) {
      case "source_up":
        return add("link", "ok", `Source up: ${d}`);
      case "source_down":
        return add("link", "warn", `Source down: ${d}`);
      case "link_warning":
        return add(/abort|failsafe/i.test(d) ? "command" : "link", "warn", d || "Link warning");
      case "lease_acquired":
      case "lease_released":
      case "lease_expired":
        return add("command", kind === "lease_expired" ? "warn" : "info", `Control ${kind.replace("lease_", "lease ")}: ${d}`);
      case "request_sent":
      case "request_ack":
      case "telecommand_sent":
        return add("command", "info", d || kind);
      case "request_timeout":
        return add("command", "warn", d || "Request timed out");
      case "rec_start":
        return add("log", "info", `Recording started${d ? ` (${d})` : ""}`);
      case "rec_stop":
        return add("log", "info", `Recording stopped${d ? ` (${d})` : ""}`);
      default: // request_retry, replay_note, unknown kinds: not events
    }
  }

  _console(pkt, f) {
    if (typeof f.text !== "string") return;
    const raw = f.text;
    const p = parseLine(raw);
    if (p && p.node === VEHICLE_NODE) {
      for (const e of EVENT_LINES) {
        const m = e.re.exec(p.text);
        if (!m) continue;
        const tone = (e.tone && e.tone(m)) || (p.level === "WARNING" ? "warn" : "info");
        return this._add(pkt.t, "log", tone, e.text(m, p.text), "ConsoleText", true);
      }
    }
    const c = classify(raw);
    if (!c) return;
    const tone = c.severity === "failure" ? "bad" : c.severity === "warning" ? "warn" : "info";
    this._add(pkt.t, "log", tone, c.title, "ConsoleText", true);
  }

  // ---------------------------------------------------------------- output

  /** Events of the missions from the clock (countdown start, T0, end) as plain events. */
  _missionEvents() {
    const out = [];
    this.clock.missions.forEach((m, i) => {
      const mk = (t, text, tone, seq) => ({ t, tm: null, wall: t, cat: "mission", tone, text, type: "MissionState", count: 1, section: -1, seq: seq === 2 ? 1e12 + i : -1000 + seq + i * 10 });
      out.push(mk(m.start, m.partial ? `Mission already running when the data begins${m.t0Known ? "" : " (T0 not known)"}` : "Mission countdown started", "info", 0));
      if (m.t0Known && !m.partial) out.push(mk(m.t0, "Mission begin (T0)", "ok", 1));
      if (m.end !== null) {
        const why = { completed: "Mission end: Completed", reset: "Mission end: reset to Idle", "clock lost": "Mission end: mission clock lost (no MissionState)", restarted: "Mission end: a new mission started", held: "Mission end: mission time stopped (final value held)" }[m.endReason] || "Mission end";
        out.push(mk(m.end, why, m.endReason === "completed" ? "ok" : "warn", 2));
      }
    });
    return out;
  }

  /**
   * The display model. `events` sorted by time with `tm` and `section` set; `rows` = events with a header row
   * ({kind: "header", t, n, text, mission}) before each mission's first row. Mission time is null before the first
   * mission started; after a mission's end it keeps counting from that mission's T0 until the next one starts.
   */
  build() {
    const missions = this.clock.missions;
    const all = [...this._missionEvents(), ...this.ev].sort((a, b) => a.t - b.t || a.seq - b.seq);
    const rows = [];
    let sec = -1;
    let k = 0;
    for (const e of all) {
      while (k < missions.length && missions[k].start <= e.t) {
        sec = k++;
        const m = missions[sec];
        rows.push({ kind: "header", t: m.start, n: sec + 1, mission: m, text: `Mission ${sec + 1}, begun at ${fmtWall(m.start)}${m.partial ? " (already running at start of data)" : ""}` });
      }
      e.section = sec;
      e.tm = sec >= 0 ? this.clock.timeIn(missions[sec], e.t) : null;
      rows.push({ kind: "event", ...e });
    }
    return { rows, events: all, missions };
  }
}

/** Events of a whole packet list in one go (a recording). */
export function deriveEvents(packets) {
  return new MissionEvents().feedAll(packets).build();
}

// ---------------------------------------------------------------- formatting and export
const p2 = (n) => String(n).padStart(2, "0");

/** Local wall clock HH:MM:SS from epoch seconds. */
export function fmtWall(t) {
  if (!num(t)) return "—";
  const d = new Date(t * 1000);
  return `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
}

/** Mission time as T-12.3 / T+4.0 / T0 (null: no mission yet). */
export function fmtEventTime(tm) {
  return num(tm) ? fmtT(tm, 1) : "—";
}

const csvCell = (s) => {
  let v = String(s ?? "");
  if (/^[=+@]/.test(v) || /^-(?![\d.])/.test(v)) v = `'${v}`; // spreadsheet formula injection from console text
  return /[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
};

/** CSV of event rows: mission time (s and as shown), wall time (ISO, UTC), category, text, mission number, source packet. */
export function eventsToCsv(events) {
  const head = ["mission_time_s", "mission_time", "wall_time_utc", "category", "text", "mission", "source_type"];
  const lines = [head.join(",")];
  for (const e of events) {
    lines.push(
      [num(e.tm) ? e.tm.toFixed(3) : "", fmtEventTime(e.tm), new Date(e.t * 1000).toISOString(), e.cat, e.count > 1 ? `${e.text} (x${e.count})` : e.text, e.section >= 0 ? e.section + 1 : "", e.type]
        .map(csvCell)
        .join(","),
    );
  }
  return `${lines.join("\r\n")}\r\n`;
}

/** Rows shown for a category set (headers stay). */
export function filterRows(rows, shown) {
  return rows.filter((r) => r.kind === "header" || shown[r.cat] !== false);
}

/**
 * Clear cutoff (display only): drops event rows with t <= cutoff. A mission header stays when a later event of that
 * mission remains (so the section still names its mission), and goes when the whole section is hidden. The derivation
 * is untouched, so mission time and dedupe stay right. cutoff null/undefined: nothing hidden.
 * Returns {rows, hidden} (hidden = number of event rows dropped).
 */
export function applyCutoff(rows, cutoff) {
  if (cutoff === null || cutoff === undefined || !Number.isFinite(cutoff)) return { rows, hidden: 0 };
  const out = [];
  let head = null;
  let hidden = 0;
  for (const r of rows) {
    if (r.kind === "header") {
      head = r;
    } else if (r.t > cutoff) {
      if (head) out.push(head);
      head = null;
      out.push(r);
    } else hidden++;
  }
  return { rows: out, hidden };
}

/** Index of the last event row at or before `t` (the current replay position), -1 if none. */
export function currentIndex(rows, t) {
  let lo = 0;
  let hi = rows.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (rows[mid].t <= t) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  while (ans >= 0 && rows[ans].kind === "header") ans--;
  return ans;
}
