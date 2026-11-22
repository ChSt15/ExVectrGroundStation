// Vehicle group: GNSS, compact. Latitude, longitude, absolute altitude, NED velocity, satellites, fix type and the
// receiver's accuracy estimates (Gps, from the radio's AIR_GNSS plus AIR_GNSS_INFO or from USB GNSS), and one line
// with the altitude cross-check of the estimated altitude against the GNSS altitude (gnss/gnss_logic.js). Display
// only: the cross-check compares two reported values and sends nothing. The home position is not shown here.
import { registerPanel } from "./registry.js";
import { Bindings, h, section, tiles } from "../ui.js";
import { describe, fmtNum } from "../fmt.js";
import { ALTCHECK_DEFAULTS, AltCheck, deg, fixName, nwuToNed } from "../gnss/gnss_logic.js";

const G = "Gps";
const finite = (x) => typeof x === "number" && Number.isFinite(x);
const num = (field) => (e) => (e.f[field] === undefined ? undefined : e.f[field]);

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
  consumes: [G, "HomePosition", "Position", "VehicleStatus"],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const cfg = () => ({ ...ALTCHECK_DEFAULTS, ...((ctx.callouts && ctx.callouts() && ctx.callouts().settings().altcheck) || {}) });
    const check = new AltCheck(cfg);
    let ccText = "—";
    const lookup = (view) => (t, src) => ctx.store.get(view, t, src);

    const pos = section(el, "");
    const ll = (field) => b.cell({ type: G, kind: "num", unit: "°", digits: 7, derive: (e) => (e.f[field] === undefined ? undefined : deg(e.f[field])) }, { showSrc: false });
    pos.append(h("div", { class: "row gnss-ll", "data-key": "gnss-lat" }, h("span", { class: "lbl", text: "Lat / Lon" }), ll("lat"), ll("lon")));
    b.row(pos, "Altitude (absolute)", { type: G, field: "alt", unit: "m", digits: 1 }, { key: "gnss-alt", title: "receiver altitude, u-blox: height above the WGS-84 ellipsoid, not above sea level" });

    b.vecTable(el, ["N", "E", "D"], [{ label: "Velocity NED", type: G, key: "gnss-vel-ned", unit: "m/s", vec: (e) => (e && e.f && e.f.vel !== undefined ? nwuToNed(e.f.vel) : undefined) }]);

    const t = tiles(el, "t3 gnss-tiles");
    b.tile(t, "Satellites", { type: G, kind: "num", digits: 0, derive: num("num_sats") }, { key: "gnss-sats" });
    b.tile(t, "Fix", { type: G, kind: "text", derive: (e) => (e.f.fix_type === undefined ? undefined : fixName(e.f.fix_type)) }, { key: "gnss-fix" });
    b.tile(t, "Alt check", { type: G, kind: "text", derive: () => ccText }, { key: "gnss-cc-line" });
    const a = tiles(el, "t3 gnss-tiles");
    b.tile(a, "Acc H [m]", { type: G, kind: "num", digits: 2, derive: num("pos_acc") }, { key: "gnss-acc-h", title: "horizontal accuracy estimate (1 sigma), the receiver's own" });
    b.tile(a, "Acc V [m]", { type: G, kind: "num", digits: 2, derive: num("alt_acc") }, { key: "gnss-acc-v", title: "vertical accuracy estimate (1 sigma), the receiver's own" });
    b.tile(a, "Acc speed [m/s]", { type: G, kind: "num", digits: 2, derive: num("vel_acc") }, { key: "gnss-acc-s", title: "speed accuracy estimate (USB GNSS only)" });

    const render = () => {
      const v = ctx.view();
      const lk = lookup(v);
      const last = check.update({ now: ctx.store.now(v), position: lk("Position"), gps: lk(G), home: lk("HomePosition"), status: lk("VehicleStatus") });
      ccText = last.state === "over" && finite(last.diff) ? `${fmtNum(Math.abs(last.diff), 1)} m, warning` : STATE_TEXT[last.state];
      b.update();
      const cc = t.querySelector("[data-key=gnss-cc-line]");
      if (cc) cc.dataset.tone = STATE_TONE[last.state];
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
