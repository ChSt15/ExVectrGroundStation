// Vehicle log line patterns (ConsoleText from the vehicle's LOG_TEXT stream). DATA FILE: one entry per event, a new
// pattern is one line. Display only: nothing here is sent to the vehicle. Pure functions, unit tested in
// web/test/log_patterns.test.js against real lines quoted from the flight software.
//
// Line format (wire profile, src/exvectr_gs/wire/exvectrnet_1/mapping.py):  "[node <id> <LEVEL>] <text>"
// Levels: DEBUG, INFO, WARNING, ERROR. The flight software prints with Core::printM (INFO), Core::printW (WARNING),
// LOG_MSG (INFO). Texts below are the format strings of flight-software/src/main.cpp and src/calibration/*.

export const VEHICLE_NODE = 1; // pattern matching only for the vehicle; other nodes (Router, ship radio) only WARNING/ERROR

/** Notification categories of the log watcher (settings.log.categories); order = order in the settings page. */
export const LOG_CATEGORIES = ["warning", "error", "calibration", "refused", "abort", "mission"];
export const LOG_CATEGORY_LABELS = {
  warning: "Vehicle warnings (all WARNING lines)",
  error: "Vehicle errors (all ERROR lines)",
  calibration: "Calibration events and problems",
  refused: "Refused commands (Arm, MissionBegin, calibration)",
  abort: "Countdown abort (critical)",
  mission: "Mission abort telecommand and mode messages",
};

/** Severity names are the Notifier's: info | warning | failure (failure = red; "critical" entries use it, sticky). */

const SENSOR_STATUS =
  "ok|not calibrated since power-up|last run failed|not found in memory|default \\(never calibrated\\)|parameters out of range|still-window check failed|no still-window data";
const VALIDITY_RE = new RegExp(`gyro: (${SENSOR_STATUS}), accel: (${SENSOR_STATUS}), mag: (${SENSOR_STATUS})`);

/**
 * Calibration validity from the text of the vehicle's repeated warning, or of any line that carries
 * Calibration::describe(): "gyro: ok, accel: parameters out of range, mag: ok".
 * Returns {bad: ["accel"], detail: {gyro, accel, mag}} or null when the text has no such part.
 */
export function calibrationProblem(text) {
  const m = VALIDITY_RE.exec(text);
  if (!m) return null;
  const detail = { gyro: m[1], accel: m[2], mag: m[3] };
  return { bad: Object.keys(detail).filter((k) => detail[k] !== "ok"), detail };
}

const lc = (s) => s.charAt(0).toLowerCase() + s.slice(1);
const reasonOf = (s) => {
  const p = calibrationProblem(s);
  return p && p.bad.length ? p.bad.join(", ") : null;
};

/**
 * Entry: {re, category, severity, title(match, message), levels?, calib?: "problem"|"ok"}. First match wins.
 * `levels` limits the entry to those line levels (default: any). `calib` feeds the persistent calibration chip.
 */
export const PATTERNS = [
  // --- critical: countdown abort. main.cpp: Core::printW("MISSION ABORTED before liftoff: calibration invalid (%s). Reset and stop.\n", reason)
  { re: /^MISSION ABORTED before liftoff: (.*?)\.? Reset and stop\./, category: "abort", severity: "failure", sticky: true, calib: "problem", title: (m) => `Countdown ABORTED: calibration invalid (${reasonOf(m[1]) || m[1]})` },
  // --- calibration problem, repeated every 10 s. main.cpp: "WARNING calibration invalid, Arm and MissionBegin are refused (repeats every 10 s): %s"
  { re: /^WARNING calibration invalid, Arm and MissionBegin are refused.*?: (.*)$/, category: "calibration", severity: "warning", calib: "problem", title: (m) => `Calibration invalid: ${reasonOf(m[1]) || m[1]}` },
  // --- refusals. "Arm refused: calibration invalid (%s)", "Arm refused: estimator zeroing active", "Mission begin refused: %s"
  { re: /^Arm refused: (.*)$/, category: "refused", severity: "warning", calib: "problem", title: (m) => `Arm refused: ${m[1]}` },
  { re: /^Mission begin refused: (.*)$/, category: "refused", severity: "warning", calib: "problem", title: (m) => `MissionBegin refused: ${m[1]}` },
  // "Calibration %s refused: %s" and "Calibration start|apply|save refused: ..."
  { re: /^Calibration (.+?) refused: (.*)$/, category: "refused", severity: "warning", title: (m) => `Calibration ${m[1]} refused: ${m[2]}` },
  { re: /^Calibration stop ignored: (.*)$/, category: "refused", severity: "warning", title: (m) => `Calibration stop ignored: ${m[1]}` },
  { re: /^ZeroEstimators refused: (.*)$/, category: "refused", severity: "warning", title: (m) => `ZeroEstimators refused: ${m[1]}` },
  // --- mission: "Mission abort telecommand: %s (alt %.2f m, speed %.2f m/s)"
  { re: /^Mission abort telecommand: (\S+)(?: \((.*)\))?/, category: "mission", severity: "warning", title: (m) => `Mission abort: ${m[1]}${m[2] ? ` (${m[2]})` : ""}` },
  { re: /^Estimator zeroing aborted by (.*)$/, category: "mission", severity: "info", title: (m) => `Estimator zeroing aborted by ${m[1]}` },
  // --- calibration session (CalibrationService::logf adds the "Calibration: " prefix in calibration_task.hpp)
  // "Accel: pose %u captured, move to the next one"   (the periodic "Accel: %u poses captured (still|moving)" is progress, not an event)
  { re: /^Calibration: Accel: pose (\d+) captured/, category: "calibration", severity: "info", title: (m) => `Accel pose ${m[1]} captured` },
  { re: /^Calibration: Accel: all (\d+) poses stored/, category: "calibration", severity: "info", title: (m) => `Accel: all ${m[1]} poses stored, stop and fit` },
  // "Accel fit %s: %u poses..." / "Mag fit %s: ..." with %s = PASS | WARN | FAIL_RESIDUAL | FAIL_COVERAGE | ...
  { re: /^Calibration: (Accel|Mag) fit (\w+)/, category: "calibration", severity: "info", title: (m) => `${m[1]} fit ${m[2]}`, level: (m) => (m[2] === "PASS" ? "info" : m[2] === "WARN" ? "warning" : "failure") },
  // "Accel VERIFY %s:", "Mag VERIFY %s:", "Gyro VERIFY %s:" with PASS | FAIL
  { re: /^Calibration: (Accel|Mag|Gyro) VERIFY (\w+)/, category: "calibration", severity: "info", title: (m) => `${m[1]} verify ${m[2]}`, level: (m) => (m[2] === "PASS" ? "info" : "failure") },
  { re: /^Calibration: (Accel|Mag) candidate rejected/, category: "calibration", severity: "warning", title: (m) => `${m[1]} candidate rejected` },
  // "Gyro calibration %s: %s, bias ..." with %s = result | FAILED
  { re: /^Calibration: Gyro calibration (result|FAILED): (\w+)/, category: "calibration", severity: "info", title: (m) => `Gyro calibration ${m[1] === "FAILED" ? "FAILED" : "result"}: ${m[2]}`, level: (m) => (m[1] === "FAILED" ? "failure" : "info") },
  // main.cpp countdown gyro run: "Calibration: gyro bias run PASS, bias ..." / "gyro bias run FAILED (%s, ..." (WARNING) / "... PASS discarded"
  { re: /^Calibration: gyro bias run (PASS discarded|PASS|FAILED)/, category: "calibration", severity: "info", title: (m) => `Gyro bias run ${m[1]}`, level: (m) => (m[1] === "PASS" ? "info" : "failure") },
  { re: /^Calibration: (\w+) calibration applied \(RAM only until Save\)/, category: "calibration", severity: "info", title: (m) => `${m[1]} calibration applied (RAM only until Save)` },
  { re: /^Calibration: saved to EEPROM/, category: "calibration", severity: "info", title: () => "Calibration saved to EEPROM" },
  { re: /^Calibration: EEPROM save FAILED/, category: "calibration", severity: "failure", title: () => "Calibration EEPROM save FAILED" },
  { re: /^Calibration: all three calibrations checked/, category: "calibration", severity: "info", calib: "ok", title: () => "Calibrations checked, countdown continues" },
  { re: /^Calibration invalid \((.*)\) but the countdown is no longer running/, category: "calibration", severity: "warning", calib: "problem", title: (m) => `Calibration invalid: ${reasonOf(m[1]) || m[1]}` },
  { re: /^Vehicle disarmed: calibration invalid \((.*)\)/, category: "abort", severity: "failure", sticky: true, calib: "problem", title: (m) => `Vehicle DISARMED: calibration invalid (${reasonOf(m[1]) || m[1]})` },
  // any other "... refused" line (Sim wind/gust/fault refused ...): LOG_MSG, level INFO
  { re: /\brefused\b/, category: "refused", severity: "warning", title: (m, t) => t },
];

const LINE_RE = /^\[node (\d+) (\w+)\] ([\s\S]*)$/;

/** Split "[node 1 WARNING] text" into {node, level, text}; null when the line is not in that format. */
export function parseLine(raw) {
  const m = LINE_RE.exec(String(raw).replace(/\s+$/, ""));
  return m ? { node: Number(m[1]), level: m[2].toUpperCase(), text: m[3] } : null;
}

const SEV_OF_LEVEL = { WARNING: "warning", ERROR: "failure" };
const MAX_TITLE = 140;
const clip = (s) => (s.length > MAX_TITLE ? `${s.slice(0, MAX_TITLE - 1)}…` : s);

/**
 * Classify one line. Returns {level, node, text, category, severity, title, sticky, calib, isProblemLevel} or null for
 * a line that raises nothing (INFO without pattern, DEBUG, other nodes below WARNING, unparsable).
 * `isProblemLevel` is true for WARNING/ERROR (counted in the Console unread badge, any node).
 */
export function classify(raw) {
  const p = parseLine(raw);
  if (!p) return null;
  const problem = p.level === "WARNING" || p.level === "ERROR";
  const vehicle = p.node === VEHICLE_NODE;
  if (vehicle) {
    for (const e of PATTERNS) {
      if (e.levels && !e.levels.includes(p.level)) continue;
      const m = e.re.exec(p.text);
      if (!m) continue;
      const severity = (e.level && e.level(m)) || e.severity;
      return { ...p, category: e.category, severity, title: clip(e.title(m, p.text)), sticky: !!e.sticky, calib: e.calib || null, isProblemLevel: problem };
    }
  }
  if (!problem) return null;
  const isErr = p.level === "ERROR";
  const title = `${vehicle ? "" : `node ${p.node}: `}${p.text.replace(/^WARNING\s+/, "")}`;
  return { ...p, category: isErr ? "error" : "warning", severity: SEV_OF_LEVEL[p.level], title: clip(title), sticky: false, calib: null, isProblemLevel: true };
}

/** Chip text of a calibration problem state: "Calibration invalid: accel". */
export function chipText(problem) {
  return `Calibration invalid: ${problem.bad.length ? problem.bad.join(", ") : "see Console"}`;
}
