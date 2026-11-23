// Pure logic of the 'Stop altitude' setting (docs/telecommands.md). Display and command only, nothing sends it
// automatically. SetStopAltitude (telecommand id 19, float = metres >= 1, no maximum) has NO acknowledgement: the confirmation is
// the vehicle's echoed MissionState.stop_altitude. A request is sent at once, then repeated every RETRY_S until the
// echo matches (within TOLERANCE_M), at most MAX_RETRIES repeats; then it is "not confirmed".
export const COMMAND = "SetStopAltitude";
export const MIN_M = 1;
export const MAX_M = Infinity; // no maximum, the value only has to be finite
export const DEFAULT_M = 20;
export const TOLERANCE_M = 0.05;
export const RETRY_S = 2;
export const MAX_RETRIES = 5;
export const ACCEPT_NOTE = "The vehicle accepts it only on the ground with the motors armed gate closed (motors disarmed); otherwise the value stays and the request ends as 'not confirmed'.";

const finite = (x) => typeof x === "number" && Number.isFinite(x);

/** The field text as metres within [min, max], else null. */
export function parseStopAltitude(text, min = MIN_M, max = MAX_M) {
  const t = String(text).trim();
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const v = Number(t);
  return v >= min && v <= max ? v : null;
}

/** 'vehicle: 20.0 m' or 'vehicle: unknown' from the echoed value. */
export const echoText = (echo) => (finite(echo) ? `vehicle: ${echo.toFixed(1)} m` : "vehicle: unknown");

export class StopAltitudeSetter {
  constructor() {
    this.target = null;
    this.state = "idle"; // idle | pending | set | failed
    this.sends = 0;
    this.lastSend = 0;
  }

  /** Start a request; the caller sends it at once when this returns true (the first frame is counted). */
  request(value, now) {
    this.target = value;
    this.state = "pending";
    this.sends = 1;
    this.lastSend = now;
  }

  cancel() {
    this.state = "idle";
    this.target = null;
  }

  /**
   * Advance with the current echo and time (s). Returns { send: bool, notify: string|null }; `state` is updated.
   * `send` = transmit the telecommand again now. `notify` is set once when the request ends as not confirmed.
   */
  tick(echo, now) {
    const r = { send: false, notify: null };
    if (this.state !== "pending") return r;
    if (finite(echo) && Math.abs(echo - this.target) <= TOLERANCE_M) {
      this.state = "set";
      return r;
    }
    if (now - this.lastSend < RETRY_S) return r;
    if (this.sends < 1 + MAX_RETRIES) {
      this.sends += 1;
      this.lastSend = now;
      r.send = true;
    } else {
      this.state = "failed";
      r.notify = `Stop altitude ${this.target.toFixed(1)} m not confirmed by the vehicle (it accepts the setting only on the ground with the motors disarmed)`;
    }
    return r;
  }

  /** Label and tone for the status chip. */
  status() {
    if (this.state === "set") return { text: "set", tone: "ok" };
    if (this.state === "pending") return { text: `pending (${this.sends}/${1 + MAX_RETRIES})`, tone: "warn" };
    if (this.state === "failed") return { text: "not confirmed", tone: "bad" };
    return { text: "", tone: "info" };
  }
}
