// Frames and conventions of the 3D view (design 5.5). Pure math, no Three.js import, unit tested.
//
// Vehicle world frame: NWU, right-handed (X North, Y West, Z Up). Three.js: right-handed, Y up.
// All vehicle content lives under one `worldRoot` group with rotation.x = WORLD_ROOT_ROTATION_X (-90 deg).
// That maps NWU (x, y, z) to Three.js (x, z, -y): Three.js +X = North, +Y = Up, +Z = East (West = -Z).
// Inside worldRoot, positions are set directly in NWU metres and attitudes directly from the vehicle quaternion.
//
// Body frame (user decision, system-architecture.md): right-handed, +Z the long axis with the nose toward +Z,
// the belly faces -X, the back +X. With the identity quaternion the body axes equal the NWU axes, so the
// nose points Up and the belly points South.

export const WORLD_ROOT_ROTATION_X = -Math.PI / 2;

/** Vehicle quaternions must be unit length; a deviation above this (1 %) is shown as "invalid attitude". */
export const QUAT_NORM_TOLERANCE = 0.01;

/** Position NWU -> Three.js world, for objects placed OUTSIDE worldRoot. Inside worldRoot use NWU directly. */
export const nwuToThree = ([x, y, z]) => [x, z, -y];

/** Inverse of nwuToThree. */
export const threeToNwu = ([x, y, z]) => [x, -z, y];

export const quatNorm = (q) => Math.hypot(q[0], q[1], q[2], q[3]);

/** True when q is an array of 4 finite numbers with norm within 1 % of 1. */
export function isValidQuat(q) {
  return Array.isArray(q) && q.length === 4 && q.every(Number.isFinite) && Math.abs(quatNorm(q) - 1) <= QUAT_NORM_TOLERANCE;
}

/** Vehicle q = [w, x, y, z] -> Three.js component order {x, y, z, w}, normalised. Returns null if unusable. */
export function vehicleQuatToThree(q) {
  if (!Array.isArray(q) || q.length !== 4 || !q.every(Number.isFinite)) return null;
  const n = quatNorm(q);
  if (n === 0) return null;
  return { x: q[1] / n, y: q[2] / n, z: q[3] / n, w: q[0] / n };
}

/** Hamilton product a*b of quaternions [w, x, y, z]. */
export function quatMul(a, b) {
  return [
    a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
    a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
    a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
    a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
  ];
}

export const quatConj = (q) => [q[0], -q[1], -q[2], -q[3]];

/** Rotate vector v by the unit quaternion q = [w, x, y, z]. */
export function quatRotate(q, v) {
  const r = quatMul(quatMul(q, [0, v[0], v[1], v[2]]), quatConj(q));
  return [r[1], r[2], r[3]];
}

/** Quaternion [w, x, y, z] for a rotation of `angle` rad about the unit `axis`. */
export function quatFromAxisAngle(axis, angle) {
  const n = Math.hypot(...axis) || 1;
  const s = Math.sin(angle / 2) / n;
  return [Math.cos(angle / 2), axis[0] * s, axis[1] * s, axis[2] * s];
}

/** q_R of the design: the rotation of worldRoot, [w, x, y, z]. */
export const Q_ROOT = quatFromAxisAngle([1, 0, 0], WORLD_ROOT_ROTATION_X);

/** Attitude for objects placed outside worldRoot: q3 = q_R (x) q (x) q_R*, [w, x, y, z]. */
export const attitudeToThree = (q) => quatMul(quatMul(Q_ROOT, q), quatConj(Q_ROOT));

/** Body-frame vector -> NWU world vector for attitude q (body -> world). */
export const bodyToWorld = (q, v) => quatRotate(q, v);

/** Body-frame vector -> Three.js world vector (through worldRoot) for attitude q. */
export const bodyToThree = (q, v) => nwuToThree(quatRotate(q, v));

/** Usable attitude setpoint: 4 finite numbers, norm within 1 % of 1 (None / zero / NaN from the core never passes). */
export const isUsableSetpointQuat = isValidQuat;

/**
 * Angle in degrees of the shortest-arc rotation between two attitudes [w, x, y, z] (q and -q are the same attitude).
 * Null if either is unusable. Display only.
 */
export function attitudeErrorDeg(q, sp) {
  if (!isValidQuat(q) || !isValidQuat(sp)) return null;
  const nq = quatNorm(q), ns = quatNorm(sp);
  const dot = Math.abs((q[0] * sp[0] + q[1] * sp[1] + q[2] * sp[2] + q[3] * sp[3]) / (nq * ns));
  return (2 * Math.acos(Math.min(1, dot)) * 180) / Math.PI;
}

/**
 * Thrust vector in the body frame from the ACTUATORS values the vehicle reports: `tvc` = [atan2(Tx, Tz), atan2(Ty, Tz)]
 * (rad, telemetry_mapping.cpp `tvcDeflection`) and `thrust` = |T| (N). Display only. null when unusable.
 */
export function tvcThrustVector(tvc, thrust) {
  if (!Array.isArray(tvc) || tvc.length < 2 || !Number.isFinite(thrust) || thrust <= 0) return null;
  const tx = Math.tan(tvc[0]);
  const ty = Math.tan(tvc[1]);
  if (!Number.isFinite(tx) || !Number.isFinite(ty)) return null;
  const k = thrust / Math.sqrt(tx * tx + ty * ty + 1);
  return [tx * k, ty * k, k];
}
