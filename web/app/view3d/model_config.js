// Model configuration logic of the 3D view (design 5.5). Pure, no Three.js import, unit tested.
// The config file is config/model.<name>.json (served at /config/model.json), see docs/gui.md.

export const DEFAULT_FLAP_DEG = 45;
const RAD2DEG = 180 / Math.PI;
const DEG2RAD = Math.PI / 180;

const isVec = (v, n) => Array.isArray(v) && v.length === n && v.every(Number.isFinite);

/** Check a model config; returns a list of problem strings (empty when usable). */
export function validateModel(cfg) {
  const p = [];
  if (!cfg || typeof cfg !== "object") return ["model config is not an object"];
  const src = cfg.body?.source;
  if (!src || (typeof src.primitive !== "string" && typeof src.gltf !== "string")) p.push("body.source needs `primitive` or `gltf`");
  if (!Array.isArray(cfg.flaps)) p.push("flaps must be a list");
  const ids = new Set();
  for (const [i, f] of (cfg.flaps || []).entries()) {
    const where = `flaps[${i}]`;
    if (typeof f.id !== "string" || ids.has(f.id)) p.push(`${where}: id missing or duplicate`);
    ids.add(f.id);
    if (!Number.isInteger(f.index) || f.index < 0 || f.index > 3) p.push(`${where}: index must be 0..3 (element of FlapState.angle)`);
    if (!isVec(f.pivot_body_m, 3)) p.push(`${where}: pivot_body_m must be 3 numbers`);
    if (!isVec(f.axis_body, 3) || Math.hypot(...f.axis_body) === 0) p.push(`${where}: axis_body must be a non-zero 3-vector`);
    if (f.sign !== 1 && f.sign !== -1) p.push(`${where}: sign must be 1 or -1`);
    if (!Number.isFinite(f.model_angle_deg)) p.push(`${where}: model_angle_deg must be a number`);
    if (!f.source || (typeof f.source.primitive !== "string" && typeof f.source.gltf !== "string")) p.push(`${where}: source needs \`primitive\` or \`gltf\``);
  }
  return p;
}

/** Placement of a model in the body frame: rotation as Euler XYZ degrees, translation in metres, uniform scale. */
export function normalizeTransform(t) {
  return {
    rotation_deg_xyz: isVec(t?.rotation_deg_xyz, 3) ? t.rotation_deg_xyz : [0, 0, 0],
    translation_m: isVec(t?.translation_m, 3) ? t.translation_m : [0, 0, 0],
    scale: Number.isFinite(t?.scale) ? t.scale : 1,
  };
}

/**
 * Rotation of a flap pivot group in radians: sign * (angle - model_angle).
 * `angle` follows the vehicle convention (0 = fully extended, 90 deg = folded flat), `model_angle_deg` is the angle
 * at which the flap is modelled in its file.
 */
export const flapRotationRad = (flap, angleRad) => flap.sign * (angleRad - flap.model_angle_deg * DEG2RAD);

/**
 * Fixed inward tilt of a flap ("cant"): the whole flap, hinge line included, is rotated about the body X axis through
 * its lower hinge point (pivot minus half the flap length along the axis), so the lower point stays and the upper end
 * moves toward the body axis. `cant_deg` is positive inward; the signed rotation about +X is cant for a flap on the
 * +Y side and -cant on the -Y side. The flap deflection angle applies on top of it. Returns { point, angleRad }.
 */
export function flapCant(flap) {
  const [px, py, pz] = flap.pivot_body_m;
  const half = (flap.source?.height_m ?? 0) / 2;
  const cant = (flap.cant_deg ?? 0) * DEG2RAD;
  return { point: [px, py, pz - half], angleRad: py >= 0 ? cant : -cant };
}

/**
 * Flap angles to display, radians per FlapState element (tl, tr, bl, br).
 * `entry` is the store entry of FlapState (or undefined). Without usable data every flap shows the default angle.
 * Returns { angles: number[4], source: "data" | "default" }.
 */
export function resolveFlapAngles(cfg, entry) {
  const def = (Number.isFinite(cfg?.default_flap_angle_deg) ? cfg.default_flap_angle_deg : DEFAULT_FLAP_DEG) * DEG2RAD;
  const a = entry?.f?.angle;
  const out = [def, def, def, def];
  if (!Array.isArray(a) || !a.some(Number.isFinite)) return { angles: out, source: "default" };
  for (let i = 0; i < 4; i++) if (Number.isFinite(a[i])) out[i] = a[i];
  return { angles: out, source: "data" };
}

export const radToDeg = (r) => r * RAD2DEG;

/**
 * Outline of a chamfered flap plate in the plane (out, z). The hinge line is out = 0, the plate is centred on z = 0
 * and extends to out = width. `chamfer_m` is the length of the diagonal cut (45 degrees), at the outer corner that
 * `chamfer_corner` names: "out_top" (toward the nose) or "out_bottom" (toward the tail).
 */
export function flapOutline({ width_m, height_m, chamfer_m = 0, chamfer_corner = "out_top" }) {
  const w = width_m, hh = height_m / 2;
  const c = Math.min(chamfer_m / Math.SQRT2, w, height_m);
  if (c <= 0) return [[0, -hh], [w, -hh], [w, hh], [0, hh]];
  return chamfer_corner === "out_bottom"
    ? [[0, -hh], [w - c, -hh], [w, -hh + c], [w, hh], [0, hh]]
    : [[0, -hh], [w, -hh], [w, hh - c], [w - c, hh], [0, hh]];
}

/** Area of a polygon [[x, y], ...] (shoelace). */
export function polygonArea(pts) {
  let s = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length];
    s += x1 * y2 - x2 * y1;
  }
  return Math.abs(s) / 2;
}
