// Pure logic of the radio power block of the link panel (docs/gui.md). Display only: the ground station never
// sets or changes the power; it shows what the Router commands (LINK_CONFIG) and what each module reports as applied
// (LINK_STATS tx_power). The legal limit of the model's radio is 10 dBm (10 mW).
export const POWER_LIMIT_DBM = 10;

/**
 * cmd: commanded power of LINK_CONFIG (number | undefined), router/ship: applied tx_power of the Router's and the
 * ship radio node's LINK_STATS (number | undefined). Returns { warnings: [{tone, text}] }.
 */
export function powerReport({ cmd, router, ship }) {
  const warnings = [];
  const num = (v) => typeof v === "number" && Number.isFinite(v);
  const over = [
    [cmd, "commanded by the Router"],
    [router, "applied by the Router"],
    [ship, "applied by the ship radio node"],
  ].filter(([v]) => num(v) && v > POWER_LIMIT_DBM);
  for (const [v, what] of over) warnings.push({ tone: "bad", text: `${v} dBm ${what}: above the ${POWER_LIMIT_DBM} dBm (10 mW) legal limit` });
  if (num(cmd) && num(ship) && cmd !== ship) {
    warnings.push({ tone: "warn", text: `power differs: Router commands ${cmd} dBm, ship radio node applies ${ship} dBm` });
  }
  return { warnings, differs: num(cmd) && num(ship) && cmd !== ship, over: over.length > 0 };
}

/** Tone of one power value: bad above the limit. */
export const powerTone = (v) => (typeof v === "number" && v > POWER_LIMIT_DBM ? "bad" : "");
