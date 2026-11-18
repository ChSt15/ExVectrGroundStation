import test from "node:test";
import assert from "node:assert/strict";
import {
  WORLD_ROOT_ROTATION_X, Q_ROOT, nwuToThree, threeToNwu, isValidQuat, vehicleQuatToThree, quatRotate,
  quatFromAxisAngle, attitudeToThree, bodyToWorld, bodyToThree, quatMul, quatConj,
} from "../app/view3d/frames.js";

const near = (a, b, eps = 1e-9) => assert.ok(a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) < eps), `${a} != ${b}`);
const ID = [1, 0, 0, 0];

// worldRoot.rotation.x = -90 deg applied to a vector, as Three.js does it (rotation about X)
const worldRoot = (v) => {
  const c = Math.cos(WORLD_ROOT_ROTATION_X), s = Math.sin(WORLD_ROOT_ROTATION_X);
  return [v[0], v[1] * c - v[2] * s, v[1] * s + v[2] * c];
};

test("worldRoot rotation maps North to +X, West to -Z (East +Z), Up to +Y", () => {
  near(worldRoot([1, 0, 0]), [1, 0, 0]); // North -> +X
  near(worldRoot([0, 1, 0]), [0, 0, -1]); // West -> -Z
  near(worldRoot([0, 0, 1]), [0, 1, 0]); // Up -> +Y
  for (const v of [[1, 2, 3], [-4, 0.5, 9]]) near(worldRoot(v), nwuToThree(v));
  near(threeToNwu(nwuToThree([1, 2, 3])), [1, 2, 3]);
});

test("a 90 degree yaw about NWU Z turns body +X from North to West", () => {
  const yaw90 = quatFromAxisAngle([0, 0, 1], Math.PI / 2);
  near(bodyToWorld(yaw90, [1, 0, 0]), [0, 1, 0]); // West in NWU
  near(bodyToThree(yaw90, [1, 0, 0]), [0, 0, -1]); // West = -Z in Three.js
});

test("identity attitude: body axes equal the NWU axes, nose Up and belly South", () => {
  near(bodyToWorld(ID, [0, 0, 1]), [0, 0, 1]); // nose (+Z body) -> Up
  near(bodyToThree(ID, [0, 0, 1]), [0, 1, 0]); // Up is Three.js +Y
  near(bodyToWorld(ID, [-1, 0, 0]), [-1, 0, 0]); // belly (-X body) -> South
  near(bodyToThree(ID, [-1, 0, 0]), [-1, 0, 0]); // South is Three.js -X
});

test("pitching the nose 90 degrees toward North puts the nose on North and the belly Up", () => {
  // rotation about Y (West) by +90 deg takes Z toward +X (right-hand rule: Z -> X)
  const q = quatFromAxisAngle([0, 1, 0], Math.PI / 2);
  near(bodyToWorld(q, [0, 0, 1]), [1, 0, 0]);
  near(bodyToWorld(q, [-1, 0, 0]), [0, 0, 1]); // belly (-X) now faces Up
});

test("quaternion order: vehicle [w,x,y,z] becomes Three.js (x,y,z,w) and is normalised", () => {
  assert.deepEqual(vehicleQuatToThree([1, 0, 0, 0]), { x: 0, y: 0, z: 0, w: 1 });
  const t = vehicleQuatToThree([2, 0, 0, 2]);
  near([t.x, t.y, t.z, t.w], [0, 0, Math.SQRT1_2, Math.SQRT1_2]);
  assert.equal(vehicleQuatToThree([0, 0, 0, 0]), null);
  assert.equal(vehicleQuatToThree([1, 2, 3]), null);
  assert.equal(vehicleQuatToThree([1, NaN, 0, 0]), null);
});

test("validity: unit length within 1 percent", () => {
  assert.ok(isValidQuat([1, 0, 0, 0]));
  assert.ok(isValidQuat([1.005, 0, 0, 0]));
  assert.ok(!isValidQuat([1.02, 0, 0, 0]));
  assert.ok(!isValidQuat([0, 0, 0, 0]));
  assert.ok(!isValidQuat([1, 0, 0]));
  assert.ok(!isValidQuat(undefined));
});

test("attitude outside worldRoot: q3 = qR q qR* rotates Three.js vectors like the NWU rotation", () => {
  const q = quatMul(quatFromAxisAngle([0, 0, 1], 0.7), quatFromAxisAngle([0, 1, 0], -0.4));
  const q3 = attitudeToThree(q);
  for (const v of [[1, 0, 0], [0, 1, 0], [0.2, -0.3, 0.9]]) {
    near(quatRotate(q3, nwuToThree(v)), nwuToThree(quatRotate(q, v)));
  }
  near(quatMul(Q_ROOT, quatConj(Q_ROOT)), ID);
});
