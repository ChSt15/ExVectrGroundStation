// Command panel and command-source cell: pure helpers. Display only, nothing here sends anything.
//  - CMD_APPLIED (USB only): the CMD_STATE fields as the VEHICLE applies them, never what the ground station sent.
//  - The command engine's own report (`rc` push, `command`): input, Pocket, pickup hints, session, NOT MASTER.
//  - The status bar cell "Command source": GS, session (low 16 bits), MULTI_MASTER, USB / UART fresh, GS_FAILSAFE_REQ.
export const INVALID = 255;

/** Rows of CMD_APPLIED: id, label, meaning per value. */
export const SWITCHES = [
  { id: "go", label: "SA go", meaning: "go / abort", values: ["NOT_GO (abort)", "GO"] },
  { id: "profile", label: "SB profile", meaning: "mission profile", values: ["profile 0", "profile 1", "profile 2"] },
  { id: "vehicle_mode", label: "SC vehicle mode", meaning: "real / simulation", values: ["REAL", "SIMULATION"] },
  { id: "motors", label: "SD motors", meaning: "motors armed / disarmed", values: ["disarmed", "armed"] },
  { id: "reset_button", label: "SE reset", meaning: "reset / zero", values: ["released", "pressed"] },
  { id: "failsafe", label: "Failsafe request", meaning: "GS failsafe", values: ["none", "FAILSAFE requested"] },
];

/** Safe-seen bits (a set bit: the safe value was seen, an edge may be reported). */
export const SAFE_SEEN = ["GO", "SC real", "SD armed", "SE press"];
export const REJECT_LABELS = ["bad length or field", "source not node 0", "foreign session", "stale or duplicate"];
/** `flags` of CMD_APPLIED: information about the input, reported by the vehicle, never acted on. */
export const APPLIED_FLAGS = ["POCKET_LIVE", "INPUT_VIRTUAL"];

export function switchText(id, v) {
  const sw = SWITCHES.find((s) => s.id === id);
  if (!sw || typeof v !== "number") return "n/s";
  return sw.values[v] ?? `unknown (${v})`;
}

/** Tone of a value: a failsafe request is red, everything else neutral (no other value is an alarm by itself). */
export const switchTone = (id, v) => (id === "failsafe" && v === 1 ? "bad" : "");

export function safeSeenList(bits) {
  if (typeof bits !== "number") return [];
  return SAFE_SEEN.map((name, i) => ({ name, seen: !!(bits & (1 << i)) }));
}

export function flagsText(bits) {
  if (typeof bits !== "number") return "n/s";
  const on = APPLIED_FLAGS.filter((_, i) => bits & (1 << i));
  return on.length ? on.join(", ") : "none";
}

export function ageText(a) {
  return typeof a === "number" && Number.isFinite(a) ? `${a.toFixed(2)} s` : "never";
}

export function rejectList(r) {
  return REJECT_LABELS.map((label, i) => ({ label, count: Array.isArray(r) && typeof r[i] === "number" ? r[i] : null }));
}

/** 16-bit session as shown (four hex digits), "none" for 0 or missing. */
export const session16Text = (s) => (typeof s === "number" && s > 0 ? (s & 0xffff).toString(16).toUpperCase().padStart(4, "0") : "none");
/** Full 32-bit session as eight hex digits. */
export const session32Text = (s) => (typeof s === "number" && s > 0 ? (s >>> 0).toString(16).toUpperCase().padStart(8, "0") : "none");

// ctrl_flags bits used by the command source cell (schema numbers).
const USB_FRESH = 1 << 7;
const UART_FRESH = 1 << 8;
const MULTI_MASTER = 1 << 11;
const GS_FAILSAFE_REQ = 1 << 12;

/**
 * Status bar cell "Command source", from the vehicle-reported VEHICLE_STATUS fields (`f`) and, optionally, this core's
 * own session (low 16 bits, from the `rc` push). Vehicle-reported values only, except that the own session is compared:
 * a different latched session is NOT MASTER (display only: the engine keeps sending, the vehicle decides).
 * @returns {{text: string, tone: string, title: string}}
 */
export function commandSourceCell(f, own16 = null) {
  if (!f) return { text: "n/s", tone: "", title: "no VehicleStatus" };
  const src = f.ctrl_source;
  const cf = typeof f.ctrl_flags === "number" ? f.ctrl_flags : 0;
  const sess = typeof f.cmd_session === "number" ? f.cmd_session : null;
  const parts = [];
  let tone = "";
  const up = (t) => (t === "bad" || (t === "warn" && tone !== "bad") ? (tone = t) : tone);
  if (src === undefined) parts.push("n/s");
  else if (src === "GS") parts.push(`GS${sess ? ` #${session16Text(sess)}` : ""}`);
  else parts.push(String(src));
  if (sess && typeof own16 === "number" && own16 > 0 && (sess & 0xffff) !== (own16 & 0xffff)) {
    parts.push("NOT MASTER");
    up("bad");
  }
  if (cf & MULTI_MASTER) {
    parts.push("MULTI_MASTER");
    up("bad");
  }
  if (cf & GS_FAILSAFE_REQ) {
    parts.push("GS_FAILSAFE_REQ");
    up("bad");
  }
  if (src === "GS") {
    const links = [cf & USB_FRESH ? "USB" : null, cf & UART_FRESH ? "UART" : null].filter(Boolean);
    parts.push(links.length ? `${links.join("+")} fresh` : "no link fresh");
    if (!links.length) up("warn");
  }
  const title =
    `VEHICLE_STATUS ctrl_source ${src ?? "n/s"}; latched session (low 16 bits) ${session16Text(sess)}; this ground station ${session16Text(own16)}. ` +
    `USB_FRESH / UART_FRESH: an accepted or duplicate CMD_STATE of the latched session arrived on that link in the last 2 s. ` +
    `MULTI_MASTER: a foreign session was rejected in the last 2 s. NOT MASTER: the vehicle latched another session than this ground station's.`;
  return { text: parts.join(" · "), tone, title };
}

/** Text for the Pocket state of the command engine report. */
export const POCKET_TEXT = { absent: "not seen", live: "live", lost: "LOST" };
export const INPUT_TEXT = { pocket: "Pocket", virtual: "virtual panel", none: "none (output held)" };

/** Pickup hints (what the operator must do), from `command.hints`. */
export function pickupHints(h) {
  const out = [];
  if (!h) return out;
  if (h.sa) out.push("SA is up while go is off: flip it down first");
  if (h.sd) out.push("SD is up while motors are disarmed: flip it down first");
  if (h.se) out.push("SE is pressed: release it first");
  if (h.sc !== null && h.sc !== undefined) out.push(`SC: flip to ${h.sc === 0 ? "REAL (position 0)" : "SIMULATION (position 1 or 2)"} to take over`);
  return out;
}

/**
 * Rows of the engine report shown in the Command panel: what the ground station SENDS (labelled so), the input, the Pocket,
 * hints and the session. `cmd` = `rc.command` of the `rc` push, or null.
 * @returns {{key: string, label: string, text: string, tone: string}[]}
 */
export function engineRows(cmd) {
  if (!cmd) return [];
  const rows = [];
  const row = (key, label, text, tone = "") => rows.push({ key, label, text, tone });
  row("session", "Session (this core)", `${session32Text(cmd.session)} (low 16 bits ${session16Text(cmd.session16)})`);
  row("input", "Input", INPUT_TEXT[cmd.input] ?? String(cmd.input), cmd.input === "none" ? "warn" : "");
  row("pocket", "Pocket stream", POCKET_TEXT[cmd.pocket] ?? String(cmd.pocket), cmd.pocket === "lost" ? "warn" : "");
  row("sending", "Command stream", cmd.sending ? `sending to ${(cmd.links || []).join(", ")} (25 Hz)` : cmd.ready === false ? "starting (waiting for the vehicle mode)" : "NO COMMAND LINK CONNECTED", cmd.sending ? "" : "warn");
  const o = cmd.out || {};
  row("sent", "Sent state", `go ${o.go ? "GO" : "NOT_GO"}, profile ${o.profile}, ${o.vehicle_mode ? "SIMULATION" : "REAL"}, motors ${o.motors ? "armed" : "disarmed"}, SE ${o.reset_button ? "pressed" : "released"}, failsafe ${o.failsafe ? "REQUESTED" : "no"}`);
  if (cmd.master === "not_master") row("master", "Command source", "NOT MASTER: the vehicle latched another session", "bad");
  if (cmd.multi_master) row("multi", "Command source", "MULTI_MASTER: a foreign session was rejected", "bad");
  for (const [i, t] of pickupHints(cmd.hints).entries()) row(`hint-${i}`, "Waiting for", t, "warn");
  return rows;
}
