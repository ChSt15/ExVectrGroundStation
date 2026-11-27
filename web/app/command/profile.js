// Mission profile names (the SB switch selects the profile; the vehicle reports VehicleStatus.mission_profile,
// VehicleStatus.profile_selected and CmdApplied.profile). Display only; 255 means "none" and has no name.
export const PROFILE_NAMES = { 0: "hover test", 1: "waypoint test", 2: "flight" };

/** Name of a profile number, "" for 255 (none), unknown numbers and non-numbers. */
export const profileName = (n) => (typeof n === "number" ? PROFILE_NAMES[n] ?? "" : "");

/** "2 (flight)", "7" for an unnamed number, "" for 255 / not a number. */
export function profileText(n) {
  if (typeof n !== "number" || n === 255) return "";
  const name = profileName(n);
  return name ? `${n} (${name})` : String(n);
}

const ACTIVE_MODES = new Set(["Initialisation", "Startup", "Running"]); // go countdown and the mission itself

/**
 * The status-bar MISSION indicator (display only). `f` = VehicleStatus fields, `applied` = CmdApplied.profile (the SB position
 * the vehicle sees), `sent` = the profile the GS engine sends. Running (vehicle mission_profile set and mode active) wins over
 * the selection. The vehicle-confirmed selection is `applied` (CmdApplied.profile, USB only), else
 * VehicleStatus.profile_selected (255 or missing = none; on the air set, so it also works over the radio), else `sent`. Returns { text, tag: "running"|"selected"|"", tone: "flight"|"test"|"none", title }.
 */
export function missionIndicator(f, applied, sent) {
  const num = (n) => (typeof n === "number" && n !== 255 ? n : null);
  const mp = num(f && f.mission_profile);
  const running = mp !== null && ACTIVE_MODES.has(f.mission_mode);
  const apc = num(applied), aps = num(f && f.profile_selected), se = num(sent);
  const ap = apc !== null ? apc : aps; // the vehicle's own view of the selection
  const sel = ap !== null ? ap : se;
  const p = running ? mp : sel;
  if (p === null) return { text: "none", tag: "", tone: "none", title: "No mission selected or reported yet (no CmdApplied, no VEHICLE_STATUS profile_selected, no command engine report)" };
  const name = profileName(p) || `profile ${p}`;
  const parts = [running ? `Running mission: the vehicle's latched profile ${p} (VehicleStatus.mission_profile).` : `Selected by the SB switch: profile ${p}, ${apc !== null ? "as the vehicle applies it (CmdApplied.profile)" : ap !== null ? "as the vehicle reports it (VehicleStatus.profile_selected, from VEHICLE_STATUS)" : "as the ground station sends it (no CmdApplied yet)"}.`];
  if (ap !== null && se !== null && ap !== se) parts.push(`Mismatch: the vehicle ${apc !== null ? "applies" : "reports (VEHICLE_STATUS)"} profile ${ap}, the ground station sends ${se} (SB pickup lag).`);
  return { text: name, tag: running ? "running" : "selected", tone: p === 2 ? "flight" : "test", title: parts.join(" ") };
}
