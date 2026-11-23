// Mission profile names (the SB switch selects the profile; the vehicle reports VehicleStatus.mission_profile and
// CmdApplied.profile). Display only; 255 means "none" and has no name.
export const PROFILE_NAMES = { 0: "hover test", 1: "waypoint test", 2: "flight" };

/** Name of a profile number, "" for 255 (none), unknown numbers and non-numbers. */
export const profileName = (n) => (typeof n === "number" ? PROFILE_NAMES[n] ?? "" : "");

/** "2 (flight)", "7" for an unnamed number, "" for 255 / not a number. */
export function profileText(n) {
  if (typeof n !== "number" || n === 255) return "";
  const name = profileName(n);
  return name ? `${n} (${name})` : String(n);
}
