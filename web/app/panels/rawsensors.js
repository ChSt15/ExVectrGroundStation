import { registerPanel } from "./registry.js";
import { h, makeCell, renderValue } from "../ui.js";
import { describe, fmtAge, fmtNum, isStale, ageOf } from "../fmt.js";
import {
  CALIBRATED_PLACEHOLDER, CALIBRATED_ROWS, QUANTITIES, Series, computeCalibrated, decodeSource, magnitude, sparkValue,
  vehicleUsbConnected,
} from "../sensors/rawsensors_logic.js";

const NS = "http://www.w3.org/2000/svg";
const W = 90;
const H = 22;

// Raw sensor panel: ImuRaw (uncalibrated, as the vehicle sends it) next to the calibrated values computed HERE from
// the vehicle's CALIBRATION packet with the ship's formulas. Display only. Needs the vehicle's USB debug set.
registerPanel({
  id: "rawsensors",
  title: "Raw and calibrated sensors",
  consumes: ["ImuRaw", "Calibration"],
  create(el, ctx) {
    const note = h("div", { class: "hint", "data-testid": "raw-note" });
    const calInfo = h("div", { class: "raw-cal-info", "data-testid": "raw-cal-info" });
    const head = h("div", { class: "raw-head" }, h("strong", { text: "raw (uncalibrated)" }), " ", h("span", { class: "dim", text: "from the vehicle's USB debug set, SI units as sent; calibrated = M (raw - offset) from the CALIBRATION packet, computed here, display only" }));
    const table = h("table", { class: "grid raw" });
    table.append(
      h("thead", {}, h("tr", {}, h("th", { text: "" }), h("th", { text: "raw (uncalibrated)" }), h("th", { text: "|raw|" }), h("th", { text: "calibrated (computed here with the ship's formulas)" }), h("th", { text: "|calibrated|" }), h("th", { text: "last 10 s (raw)" }))),
    );
    const tbody = h("tbody");
    table.append(tbody);
    el.append(head, note, calInfo, table);

    const mkSvg = () => {
      const poly = document.createElementNS(NS, "polyline");
      poly.setAttribute("fill", "none");
      poly.setAttribute("stroke", "currentColor");
      poly.setAttribute("stroke-width", "1.2");
      const svg = document.createElementNS(NS, "svg");
      svg.setAttribute("width", String(W));
      svg.setAttribute("height", String(H));
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      svg.setAttribute("class", "spark");
      svg.append(poly);
      return { svg, poly };
    };

    const rows = QUANTITIES.map((q) => {
      const val = makeCell();
      const mag = makeCell();
      const { svg, poly } = mkSvg();
      const calRow = CALIBRATED_ROWS[q.key];
      const calCell = h("td", { class: "raw-cal dim", "data-testid": `raw-cal-${q.key}`, text: calRow ? CALIBRATED_PLACEHOLDER : "" });
      const calMag = h("td", { class: "raw-calmag", "data-testid": `raw-calmag-${q.key}` });
      tbody.append(h("tr", { "data-q": q.key }, h("th", { text: q.label }), h("td", {}, val.el), h("td", {}, q.magnitude ? mag.el : ""), calCell, calMag, h("td", {}, svg)));
      return { q, val, mag, poly, svg, series: new Series(), calRow, calCell, calMag };
    });
    // omega_est row: calibrated gyro minus the EKF bias (equals POSE omega up to timing)
    const omegaCell = h("td", { class: "raw-cal dim", "data-testid": "raw-cal-omega_est", text: CALIBRATED_PLACEHOLDER });
    tbody.append(h("tr", { "data-q": "omega_est" }, h("th", { text: "Angular rate estimate (gyro cal - EKF bias)" }), h("td"), h("td"), omegaCell, h("td"), h("td")));

    const spec = (q) => ({ type: "ImuRaw", field: q.field, kind: q.vec ? "vec" : "num", scale: q.scale, digits: q.digits, unit: q.unit });
    const lookup = (t, s) => ctx.store.get(ctx.view(), t, s);
    const schemaOf = (t) => ctx.store.schema.get(t);
    const fmtVec = (v, scale, digits) => v.map((x) => fmtNum(scale * x, digits)).join("  ");

    function render() {
      const now = ctx.store.now(ctx.view());
      const nowN = now === null ? Infinity : now;
      const usb = ctx.view() === "replay" || vehicleUsbConnected(ctx.status());
      note.textContent = usb ? "" : "No vehicle USB source is connected: raw sensor values need the vehicle's USB debug set.";

      const imuE = lookup("ImuRaw");
      const calE = lookup("Calibration");
      const calStale = !!calE && isStale(calE, nowN, schemaOf("Calibration"));
      const imuStale = !!imuE && isStale(imuE, nowN, schemaOf("ImuRaw"));
      const res = computeCalibrated(imuE && imuE.f, calE && calE.f);
      if (!calE) {
        calInfo.textContent = "CALIBRATION: none received (the vehicle sends it every 2 s on USB)";
      } else {
        const f = calE.f;
        calInfo.textContent = `CALIBRATION ${calStale ? "STALE " : ""}age ${fmtAge(ageOf(calE, nowN))}  |  source: gyro ${decodeSource(f.gyro_source)}; accel ${decodeSource(f.accel_source)}; mag ${decodeSource(f.mag_source)}${f.raw_frame ? `  |  raw_frame ${f.raw_frame}: not computed` : ""}`;
      }
      calInfo.dataset.state = !calE ? "missing" : calStale ? "stale" : "fresh";

      for (const r of rows) {
        const d = describe(spec(r.q), lookup, nowN, schemaOf);
        renderValue(r.val, d, false);
        if (r.q.magnitude) {
          const m = imuE && imuE.f ? magnitude(imuE.f[r.q.field]) : undefined;
          const md = m === undefined ? { ...d, text: d.state === "missing" ? "—" : "n/s", unit: "" } : { ...d, text: (m * r.q.scale).toFixed(r.q.digits), unit: r.q.unit, value: m };
          renderValue(r.mag, md, false);
        }
        if (r.calRow) {
          const v = res[r.calRow.key];
          const stale = calStale || imuStale;
          if (!calE) {
            r.calCell.textContent = CALIBRATED_PLACEHOLDER;
            r.calMag.textContent = "";
          } else if (!v) {
            r.calCell.textContent = "n/a (input missing or NaN)";
            r.calMag.textContent = "";
          } else {
            r.calCell.textContent = `${fmtVec(v, r.calRow.scale, r.calRow.digits)} ${r.calRow.unit}`;
            r.calMag.textContent = r.q.magnitude ? `${fmtNum(r.calRow.scale * magnitude(v), r.calRow.digits)} ${r.calRow.unit}` : "";
          }
          r.calCell.dataset.state = !calE ? "missing" : stale ? "stale" : "fresh";
          r.calCell.classList.toggle("dim", !calE);
        }
        r.poly.setAttribute("points", r.series.path(W, H));
        r.svg.dataset.state = d.state;
      }
      const om = res.omega_est;
      omegaCell.textContent = !calE ? CALIBRATED_PLACEHOLDER : om ? `${fmtVec(om, 1, 4)} rad/s` : "n/a (input missing or NaN)";
      omegaCell.dataset.state = !calE ? "missing" : calStale || imuStale ? "stale" : "fresh";
      omegaCell.classList.toggle("dim", !calE);
    }

    return {
      onPacket(pkt) {
        if (pkt.type !== "ImuRaw") return;
        for (const r of rows) r.series.add(pkt.t, sparkValue(r.q, pkt.f[r.q.field]));
      },
      onReset() {
        for (const r of rows) r.series.clear();
      },
      onViewChange() {
        for (const r of rows) r.series.clear();
        render();
      },
      onTick: render,
      onStatus: render,
      freshness: () => {
        const d = describe({ type: "ImuRaw", field: "gyro", kind: "vec" }, lookup, ctx.store.now(ctx.view()) ?? Infinity, schemaOf);
        return { state: d.state, age: d.state === "stale" ? d.age : null };
      },
    };
  },
});
