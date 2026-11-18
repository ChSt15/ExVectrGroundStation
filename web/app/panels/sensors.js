import { registerPanel } from "./registry.js";
import { Bindings, section } from "../ui.js";

const SENSORS = [
  ["sensor_gnss", "GNSS"],
  ["sensor_imu", "IMU"],
  ["sensor_baro", "Barometer"],
  ["sensor_mag", "Magnetometer"],
  ["sensor_att_kf", "Attitude filter"],
  ["sensor_pos_kf", "Position filter"],
];
const TONE = { Ready: "ok", Init: "info", Calib: "info", Failure: "bad" };

registerPanel({
  id: "sensors",
  title: "Sensors & estimator",
  consumes: ["VehicleState", "Attitude", "Position", "Gps"],
  create(el, ctx) {
    const b = new Bindings(ctx);
    const s1 = section(el, "Sensor state");
    for (const [f, label] of SENSORS) {
      b.row(s1, label, { type: "VehicleState", field: f, kind: "enum" }, { tone: (d) => (d.state === "fresh" ? TONE[d.text] || "" : "") });
    }
    const s2 = section(el, "Estimator accuracy");
    b.row(s2, "Attitude tilt accuracy", { type: "Attitude", field: "tilt_acc", unit: "rad", digits: 4 });
    b.row(s2, "Attitude north accuracy", { type: "Attitude", field: "north_acc", unit: "rad", digits: 4 });
    b.row(s2, "Position h / v accuracy", { type: "Position", kind: "vec", digits: 3, derive: (e) => (e.f.h_acc === undefined && e.f.v_acc === undefined ? undefined : [e.f.h_acc ?? null, e.f.v_acc ?? null]) });
    b.row(s2, "GPS accuracy (pos, alt, vel)", { type: "Gps", kind: "vec", digits: 2, derive: (e) => (e.f.pos_acc === undefined ? undefined : [e.f.pos_acc, e.f.alt_acc ?? null, e.f.vel_acc ?? null]) });
    const s3 = section(el, "Ground distance");
    b.row(s3, "Ground distance", { type: "VehicleState", field: "ground_dist", unit: "m" });
    b.row(s3, "Covariance", { type: "VehicleState", field: "ground_dist_cov", digits: 4 });
    return { onTick: () => b.update(), onViewChange: () => b.update(), freshness: () => b.freshness() };
  },
});
