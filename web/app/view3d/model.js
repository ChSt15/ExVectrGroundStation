// Config-driven ship model (design 5.5). Builds the body and the flaps from the model config, with `primitive`
// (procedural placeholder) or `gltf` sources through the same path. Body frame: +Z nose, belly -X, +Y left.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { flapCant, flapOutline, flapRotationRad, normalizeTransform } from "./model_config.js";

const DEG2RAD = Math.PI / 180;

/** Apply a `model_to_body` style transform (Euler XYZ degrees, metres, uniform scale) to an object. */
export function applyTransform(obj, t) {
  const n = normalizeTransform(t);
  obj.rotation.set(...n.rotation_deg_xyz.map((d) => d * DEG2RAD), "XYZ");
  obj.position.set(...n.translation_m);
  obj.scale.setScalar(n.scale);
}

const COLORS = { body: 0xc9ced6, belly: 0x2b2f36, flap: 0xe0662f, edge: 0x20242a };

// ---- procedural primitives, built directly in the body frame (Z long axis) ----

function starshipPlaceholder(src) {
  const R = src.radius_m, H = src.height_m, nose = src.nose_length_m ?? 0.08, z0 = -(src.origin_from_bottom_m ?? H / 2);
  const zc = z0 + H - nose; // top of the cylinder
  const profile = [new THREE.Vector2(0, z0), new THREE.Vector2(R, z0), new THREE.Vector2(R, zc)];
  for (let i = 1; i <= 8; i++) {
    const s = i / 8; // parabolic nose
    profile.push(new THREE.Vector2(R * (1 - s * s), zc + nose * s));
  }
  const g = new THREE.Group();
  const lathe = (pts, phiStart, phiLen) => {
    const geo = new THREE.LatheGeometry(pts, 48, phiStart, phiLen);
    geo.rotateX(Math.PI / 2); // lathe axis Y -> body Z; lathe angle phi -> body (x, y) = (r sin phi, -r cos phi)
    return geo;
  };
  g.add(new THREE.Mesh(lathe(profile, 0, Math.PI * 2), new THREE.MeshStandardMaterial({ color: COLORS.body, metalness: 0.45, roughness: 0.5 })));
  // dark heat-shield strip on the belly (-X), a visible orientation cue
  const strip = [new THREE.Vector2(R * 1.004, z0 + 0.01), new THREE.Vector2(R * 1.004, zc)];
  const half = 0.75;
  g.add(new THREE.Mesh(lathe(strip, -Math.PI / 2 - half, 2 * half), new THREE.MeshStandardMaterial({ color: COLORS.belly, roughness: 0.8, side: THREE.DoubleSide })));
  return g;
}

/** Flap plate in the model frame: hinge line on Z at the origin, plate extends toward +X, thin along Y. */
function flapPlate(src) {
  const pts = flapOutline(src);
  const shape = new THREE.Shape(pts.map(([x, y]) => new THREE.Vector2(x, y)));
  const t = src.thickness_m ?? 0.004;
  const geo = new THREE.ExtrudeGeometry(shape, { depth: t, bevelEnabled: false });
  geo.translate(0, 0, -t / 2);
  geo.rotateX(Math.PI / 2); // shape y -> model z, extrusion -> model y
  const g = new THREE.Group();
  const tip = new THREE.Object3D(); // outer edge at the flap centre height, used by the debug hook
  tip.name = "tip";
  tip.position.set(src.width_m, 0, 0);
  g.add(tip);
  g.add(new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: COLORS.flap, metalness: 0.2, roughness: 0.55, side: THREE.DoubleSide })));
  g.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: COLORS.edge })));
  return g;
}

export const PRIMITIVES = { "starship-placeholder": starshipPlaceholder, flap: flapPlate };

const gltfLoader = new GLTFLoader();

/** Load a source ({primitive} or {gltf, node}) into an Object3D in its own model frame. */
export async function loadSource(src) {
  if (src.primitive) {
    const make = PRIMITIVES[src.primitive];
    if (!make) throw new Error(`unknown primitive ${src.primitive}`);
    return make(src);
  }
  if (src.gltf) {
    const url = src.gltf.startsWith("/") ? src.gltf : `/${src.gltf}`;
    const gltf = await gltfLoader.loadAsync(url);
    let obj = gltf.scene;
    if (src.node) {
      obj = gltf.scene.getObjectByName(src.node);
      if (!obj) throw new Error(`node ${src.node} not found in ${src.gltf}`);
      obj = obj.clone();
      obj.position.set(0, 0, 0); // the node is placed by model_to_body, not by its place in the file
      obj.quaternion.identity();
      obj.scale.set(1, 1, 1);
    }
    return obj;
  }
  throw new Error("source needs primitive or gltf");
}

/**
 * Build the ship. Returns { group, flaps: Map(id -> {cfg, pivot}), setFlapAngles(rad[4]), setGhost(bool) }.
 * `group` is in the body frame: set its position (NWU, inside worldRoot) and quaternion from the vehicle attitude.
 */
export async function buildShip(cfg) {
  const group = new THREE.Group();
  group.name = "ship";
  const body = await loadSource(cfg.body.source);
  applyTransform(body, cfg.body.model_to_body);
  group.add(body);

  const flaps = new Map();
  for (const f of cfg.flaps) {
    const model = await loadSource(f.source);
    applyTransform(model, f.model_to_body); // flap model frame -> body frame at the modelled angle
    const offset = new THREE.Group();
    offset.position.set(...f.pivot_body_m.map((v) => -v));
    offset.add(model);
    const pivot = new THREE.Group();
    pivot.name = `flap-${f.id}`;
    const cant = flapCant(f); // fixed inward tilt about the lower hinge point; the hinge rotates with it
    pivot.position.set(...f.pivot_body_m.map((v, i) => v - cant.point[i]));
    pivot.add(offset);
    const cantGroup = new THREE.Group();
    cantGroup.name = `flap-cant-${f.id}`;
    cantGroup.position.set(...cant.point);
    cantGroup.quaternion.setFromAxisAngle(new THREE.Vector3(1, 0, 0), cant.angleRad);
    cantGroup.add(pivot);
    group.add(cantGroup);
    flaps.set(f.id, { cfg: f, pivot, angleRad: null });
  }

  const axisQuat = new THREE.Quaternion();
  const setFlapAngles = (anglesRad) => {
    for (const fl of flaps.values()) {
      const a = anglesRad[fl.cfg.index];
      if (!Number.isFinite(a)) continue;
      fl.angleRad = a;
      const axis = new THREE.Vector3(...fl.cfg.axis_body).normalize();
      fl.pivot.quaternion.copy(axisQuat.setFromAxisAngle(axis, flapRotationRad(fl.cfg, a)));
    }
  };

  const ghostState = new WeakMap();
  const setGhost = (on) => {
    group.traverse((o) => {
      for (const m of [].concat(o.material || [])) {
        if (!ghostState.has(m)) ghostState.set(m, { transparent: m.transparent, opacity: m.opacity, depthWrite: m.depthWrite });
        const s = ghostState.get(m);
        m.transparent = on || s.transparent;
        m.opacity = on ? 0.3 : s.opacity;
        m.depthWrite = on ? false : s.depthWrite;
        m.needsUpdate = true;
      }
    });
  };

  return { group, flaps, setFlapAngles, setGhost };
}
