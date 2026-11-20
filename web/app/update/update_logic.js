// Pure logic of the 'Software' panel (docs/updating.md). The update is not a vehicle uplink, but the restart drops RC
// leases and recordings, so the button is disabled with a reason while anything is live. The core checks the same
// guards itself (and again right before it exits); this only explains them early.
import { HoldTimer } from "../radio/radio_logic.js";

export { HoldTimer };
export const UPDATE_HOLD_MS = 1000;

/** Reasons the update must not be applied now (empty = allowed), from live values the GUI already has. */
export function liveGuards({ apiState, view, canControl, vehicle, recorder, rc, coreGuards }) {
  const why = [];
  if (apiState !== "open") why.push("no connection to the core");
  if (view !== "live") why.push("replay view: switch to LIVE");
  if (!canControl) why.push("this client is on a view-only host");
  const f = (vehicle && vehicle.f) || {};
  if (f.armed === true) why.push("the vehicle reports ARMED");
  if (f.motors_enabled === true) why.push("the vehicle reports MOTORS ENABLED");
  if (f.mission_mode === "Running" || f.mission_mode === 3) why.push("the vehicle reports mission mode RUNNING");
  if (recorder && recorder.active) why.push("a recording is active: stop it first");
  if (rc && rc.lease) why.push("an RC control lease is held: release it first");
  for (const g of coreGuards || []) if (!why.includes(g)) why.push(g); // the core's own list is authoritative
  return why;
}

/** The apply button: { enabled, reason }. `check` is the result of the last check (or null). */
export function applyState({ status, check, guards, restartPending }) {
  if (guards.length) return { enabled: false, reason: guards.join("; ") };
  if (!status || status.git === false) return { enabled: false, reason: "not a git checkout" };
  if (status.enabled === false) return { enabled: false, reason: "self-update is disabled in the config" };
  if (status.detached) return { enabled: false, reason: "detached HEAD (a rollback state): check out the branch first" };
  if (status.branch !== status.configured_branch && !status.allow_switch) {
    return { enabled: false, reason: `branch ${status.branch} is checked out, the config follows ${status.configured_branch}` };
  }
  if (status.dirty) return { enabled: false, reason: "local changes in the working tree" };
  if (restartPending) return { enabled: true, reason: "" };
  if (!check) return { enabled: false, reason: "check for updates first" };
  if (check.diverged || check.ahead) return { enabled: false, reason: "local commits exist: not a fast-forward" };
  if (!check.behind) return { enabled: false, reason: "already up to date" };
  return { enabled: true, reason: "" };
}

/** Header badge: { show, text }. `summary` is the core's `update` push. */
export function badgeState(summary) {
  if (!summary || summary.enabled === false) return { show: false, text: "" };
  if (summary.restart_pending) return { show: true, text: "restart pending", tone: "warn" };
  if (summary.behind > 0) return { show: true, text: `update: ${summary.behind} new`, tone: "info" };
  return { show: false, text: "" };
}

/** One-line description of the checkout. */
export function describeVersion(st) {
  if (!st) return "";
  if (st.git === false) return st.message || "not a git checkout";
  return `${st.sha} on ${st.branch || "(detached HEAD)"} · ${(st.date || "").slice(0, 10)} · ${st.subject}`;
}

/** Notes shown under the commit list. */
export function checkNotes(c) {
  const n = [];
  if (!c) return n;
  if (c.deps_changed) n.push("pyproject.toml changed: `pip install -e .` runs as part of the update");
  if (c.submodules_changed) n.push("the pinned library versions (submodules) change");
  if (c.diverged) n.push(`DIVERGED: ${c.ahead} local commit(s) are not on the remote; a fast-forward update refuses`);
  if (c.more_commits) n.push(`... and ${c.more_commits} more commit(s)`);
  return n;
}
