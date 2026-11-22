// Vehicle group: GNSS. Position, velocity and fix quality as the vehicle reports them (Gps, from the radio's AIR_GNSS
// plus AIR_GNSS_INFO or from USB GNSS), the home position if one was received, and the altitude cross-check of the
// estimated altitude against the GNSS altitude (gnss/gnss_logic.js). Display only: the cross-check compares two
// reported values and sends nothing.
import { registerPanel } from "./registry.js";
import { Bindings, h, section, tiles } from "../ui.js";
import { describe, fmtAge, fmtNum } from "../fmt.js";
import { ALTCHECK_DEFAULTS, AltCheck, deg, fixName, groundSpeed, nwuToNed } from "../gnss/gnss_logic.js";

const G = "Gps";
const HOME = "HomePosition";
const POS = "Position";
const finite = (x) => typeof x === "number" && Number.isFinite(x);
const comp = (field, i) => (e) => (Array.isArray(e.f[field]) ? e.f[field][i] ?? null : e.f[field] === null ? null : undefined);

const STATE_TEXT = {
  ok: "OK",
  over: "difference over the limit",
  mismatch: "MISMATCH",
  nodata: "no GNSS or position data",
  stale: "stale data, not judged",
  nofix: "no GNSS fix, not judged",
  noref: "no reference yet",
};
const STATE_TONE = { ok: "ok", over: "warn", mismatch: "bad", nodata: "", stale: "", nofix: "", noref: "" };

registerPanel({
  id: "gnss",
  title: "GNSS",
  consumes: [G, HOME, POS, "VehicleStatus"],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const cfg = () => ({ ...ALTCHECK_DEFAULTS, ...((ctx.callouts && ctx.callouts() && ctx.callouts().settings().altcheck) || {}) });
    const check = new AltCheck(cfg);
    let last = null;
    let ccText = "—";
    const lookup = (view) => (t, src) => ctx.store.get(view, t, src);

    // relative altitude: GNSS altitude minus the reference (home altitude, else the start altitude)
    const relAlt = () => (last && last.ref !== null && last.gnssAbs !== null ? last.gnssAbs - last.ref : undefined);

    const compact = !!(ctx.options && ctx.options.compact);
    // compact: only the tiles and one cross-check line are shown; the full sections are built detached so the
    // update code stays the same (they are not in the DOM).
    const full = compact ? h("div") : el;

    const t = tiles(el, compact ? "t4 gnss-compact" : "t3");
    const relTile = b.tile(t, "Altitude", { type: G, kind: "num", unit: "m", digits: 1, derive: (e) => (finite(e.f.alt) ? (relAlt() ?? e.f.alt) : e.f.alt === undefined ? undefined : null) }, { key: "gnss-alt" });
    b.tile(t, "Ground speed", { type: G, kind: "num", unit: "m/s", digits: 2, derive: (e) => (e.f.vel === undefined ? undefined : groundSpeed(e.f.vel)) }, { key: "gnss-gs" });
    b.tile(t, "Fix", { type: G, kind: "text", derive: (e) => (e.f.fix_type === undefined ? undefined : fixName(e.f.fix_type)) }, { key: "gnss-fix" });
    if (compact) for (const tl of t.querySelectorAll(".tile")) tl.title = tl.querySelector(".tile-l").textContent;
    const altNote = h("div", { class: "hint", "data-testid": "gnss-alt-note" });
    // compact: the fourth tile is the one-line altitude cross-check status (ok / difference X m, warning / ...)
    const ccLine = compact ? b.tile(t, "Alt check", { type: G, kind: "text", derive: () => ccText }, { key: "gnss-cc-line" }) : null;
    if (!compact) el.append(altNote);

    const pos = section(full, "Position");
    b.row(pos, "Latitude", { type: G, kind: "num", unit: "°", digits: 7, derive: (e) => (e.f.lat === undefined ? undefined : deg(e.f.lat)) }, { key: "gnss-lat" });
    b.row(pos, "Longitude", { type: G, kind: "num", unit: "°", digits: 7, derive: (e) => (e.f.lon === undefined ? undefined : deg(e.f.lon)) });
    b.row(pos, "Altitude (GNSS, absolute)", { type: G, field: "alt", unit: "m", digits: 1 }, { title: "receiver altitude, u-blox: height above the WGS-84 ellipsoid, not above sea level" });

    b.vecTable(full, ["N", "W/E", "U/D"], [
      { label: "Vel NWU", type: G, field: "vel", unit: "m/s", key: "gnss-vel-nwu" },
      { label: "Vel NED", type: G, key: "gnss-vel-ned", unit: "m/s", vec: (e) => (e && e.f && e.f.vel !== undefined ? nwuToNed(e.f.vel) : undefined) },
    ]);
    full.append(h("div", { class: "hint", text: "NWU columns N, W, U; NED columns N, E, D." }));

    const info = section(full, "Fix quality (1 Hz)");
    b.row(info, "Satellites", { type: G, kind: "num", digits: 0, derive: (e) => (e.f.num_sats === undefined ? undefined : e.f.num_sats) }, { key: "gnss-sats" });
    b.row(info, "h_acc / v_acc", { type: G, kind: "vec", unit: "m", digits: 2, derive: (e) => (e.f.pos_acc === undefined && e.f.alt_acc === undefined ? undefined : [e.f.pos_acc ?? null, e.f.alt_acc ?? null]) }, { key: "gnss-acc", title: "horizontal / vertical accuracy estimate (1 sigma); the receiver's own estimate" });
    const infoAge = h("div", { class: "hint", "data-testid": "gnss-info-age" });
    info.append(infoAge);

    const cc = section(full, "Altitude cross-check");
    const mk = (label, key) => {
      const v = h("span", { class: "v", "data-testid": key, text: "—" });
      cc.append(h("div", { class: "row" }, h("span", { class: "lbl", text: label }), v));
      return v;
    };
    const vEst = mk("Estimated (Position, up)", "alt-est");
    const vGnss = mk("GNSS, relative to start", "alt-gnss");
    const vDiff = mk("Difference (est. - GNSS)", "alt-diff");
    const vState = h("span", { class: "pill", "data-testid": "alt-state", text: "—" });
    cc.append(h("div", { class: "row" }, h("span", { class: "lbl", text: "Cross-check" }), vState));
    const ccNote = h("div", { class: "hint", "data-testid": "alt-note" });
    cc.append(ccNote);

    const hs = section(full, "Home");
    const vHome = h("span", { class: "v", "data-testid": "home-pos", text: "no home position received" });
    hs.append(h("div", { class: "row" }, h("span", { class: "lbl", text: "Home position" }), vHome));
    const vEpoch = h("span", { class: "v", "data-testid": "home-epoch", text: "—" });
    hs.append(h("div", { class: "row" }, h("span", { class: "lbl", text: "Home epoch (status / copy)" }), vEpoch));

    const entries = () => {
      const v = ctx.view();
      const lk = lookup(v);
      return { now: ctx.store.now(v), position: lk(POS), gps: lk(G), home: lk(HOME), status: lk("VehicleStatus") };
    };

    const render = () => {
      const inp = entries();
      last = check.update(inp);
      if (compact) ccText = last.state === "over" && finite(last.diff) ? `${fmtNum(Math.abs(last.diff), 1)} m, warning` : STATE_TEXT[last.state];
      b.update();
      const u = (v, d = 1) => (finite(v) ? `${fmtNum(v, d)} m` : "—");
      vEst.textContent = u(last.est);
      vGnss.textContent = u(last.gnssRel);
      vDiff.textContent = finite(last.diff) ? `${last.diff > 0 ? "+" : ""}${fmtNum(last.diff, 1)} m` : "—";
      vState.textContent = STATE_TEXT[last.state];
      vState.dataset.tone = STATE_TONE[last.state];
      vState.dataset.check = last.state;
      vDiff.dataset.tone = STATE_TONE[last.state];
      const c = cfg();
      ccNote.textContent = `Warning above ${fmtNum(c.limit_m, 1)} m for ${fmtNum(c.hold_s, 1)} s (Callouts setting altcheck). Reference: ${last.refSource === "home" ? `home altitude ${u(last.ref)}` : last.refSource === "gnss" ? `GNSS altitude at the start ${u(last.ref)} (taken while the estimate is on the pad)` : "none yet"}.`;
      altNote.textContent = last.refSource ? `Altitude shown relative to ${last.refSource === "home" ? "the home altitude" : "the GNSS altitude at the start"} (absolute value in the row below).` : "Altitude shown absolute (no home or start reference yet).";
      const now = inp.now ?? Infinity;
      if (compact) {
        const gg = inp.gps && inp.gps.f;
        const ll = gg && finite(gg.lat) && finite(gg.lon) ? `${fmtNum(deg(gg.lat), 7)}°, ${fmtNum(deg(gg.lon), 7)}°` : "no position";
        const q = gg && gg.num_sats !== undefined ? `${gg.num_sats} satellites, h_acc ${finite(gg.pos_acc) ? fmtNum(gg.pos_acc, 2) + " m" : "?"}` : "no fix quality info";
        t.title = `${ll}; ${q}`;
        if (ccLine && ccLine.el) ccLine.el.dataset.tone = STATE_TONE[last.state];
        ccLine.dataset.tone = STATE_TONE[last.state];
      }
      const g = inp.gps && inp.gps.f;
      infoAge.textContent = g && finite(g.info_t) ? `info (AIR_GNSS_INFO) ${fmtAge(Math.max(0, now - g.info_t))} old` : g && g.fix_type !== undefined ? "info from USB GNSS" : "no fix quality info received";
      const home = inp.home && inp.home.f;
      if (home) {
        const ll = home.latlon_valid ? `${fmtNum(deg(home.lat), 7)}°, ${fmtNum(deg(home.lon), 7)}°` : "lat/lon not valid";
        const al = home.alt_valid && finite(home.alt) ? `${fmtNum(home.alt, 1)} m` : "alt not valid";
        vHome.textContent = `${ll}, ${al}${home.simulation ? " (simulation)" : ""}${home.zeroing ? " (zeroing)" : ""}`;
        vHome.title = `h_acc ${finite(home.h_acc) ? fmtNum(home.h_acc, 2) + " m" : "unknown"}, set at vehicle time ${home.set_time ? fmtNum(home.set_time / 1e9, 1) + " s" : "never"}`;
      } else {
        vHome.textContent = "no home position received";
        vHome.title = "";
      }
      const he = inp.status && inp.status.f ? inp.status.f.home_epoch : undefined;
      const hc = home ? home.epoch : undefined;
      vEpoch.textContent = `${he === undefined ? "n/s" : he} / ${hc === undefined ? "none" : hc}`;
      vEpoch.dataset.tone = he !== undefined && hc !== undefined && he !== hc ? "warn" : "";
      vEpoch.title = "VEHICLE_STATUS.home_epoch / HOME_POSITION.epoch: differing values mean the home copy is stale (the home reference changed since)";
    };

    const freshness = () => {
      const v = ctx.view();
      const d = describe({ type: G, field: "lat" }, lookup(v), ctx.store.now(v) ?? Infinity, (x) => ctx.store.schema.get(x));
      return { state: d.state === "not supplied" ? "fresh" : d.state, age: d.state === "stale" ? d.age : null };
    };
    return {
      onTick: render,
      onViewChange() {
        check.reset();
        render();
      },
      onReset: () => check.reset(),
      freshness,
    };
  },
});
