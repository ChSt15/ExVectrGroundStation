// The Three.js scene of the 3D view: world (grid, N/W/Up gizmo, compass), ship, trail, setpoint, thrust arrow,
// camera modes. Everything vehicle-related lives under `worldRoot` (rotation.x = -90 deg), so positions are NWU
// metres and the attitude is the vehicle quaternion (see frames.js). No data handling here: the panel feeds it.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { WORLD_ROOT_ROTATION_X, vehicleQuatToThree } from "./frames.js";
import { buildShip } from "./model.js";
import { getDisplay, pixelRatio } from "../display_settings.js";

const GRID_HALF_M = 50;
const AXIS_COLORS = { n: 0xe5534b, w: 0x3fb950, up: 0x58a6ff };
export const CAMERA_MODES = ["follow", "observer", "top"];

const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

/** Parse a CSS colour (#rgb[a], rgb()) to a THREE.Color. */
function parseColor(s, fallback = "#808080") {
  const c = new THREE.Color();
  try {
    c.set(s || fallback);
  } catch (e) {
    c.set(fallback);
  }
  return c;
}

function labelSprite(text, color) {
  const cv = document.createElement("canvas");
  cv.width = cv.height = 128;
  const g = cv.getContext("2d");
  g.font = "bold 72px system-ui, sans-serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillStyle = color;
  g.fillText(text, 64, 68);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  s.renderOrder = 10;
  s.userData.label = true;
  return s;
}

export class Scene3D {
  constructor(container, model) {
    this.container = container;
    this.cfg = model;
    this.mode = "follow";
    this.dirty = true;
    this.labels = [];
    this.stale = {};
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.applyPixelRatio();
    this.renderer.setClearColor(0x000000, 0);
    container.append(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.02, 500);
    this.root = new THREE.Group();
    this.root.name = "worldRoot";
    this.root.rotation.x = WORLD_ROOT_ROTATION_X;
    this.scene.add(this.root);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 2.2);
    sun.position.set(3, 8, 5);
    this.scene.add(sun);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.addEventListener("change", () => (this.dirty = true));
    this.controls.minDistance = 0.3;
    this.controls.maxDistance = 200;
    this.shipPosThree = new THREE.Vector3();
    this.buildStatic();
    this.buildTrail();
    this.buildSetpoint();
    this.setCameraMode("follow");
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(container);
    this.mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    this._onTheme = () => this.applyTheme();
    this.mq?.addEventListener?.("change", this._onTheme);
    window.addEventListener("exgs-theme", this._onTheme); // the GUI's own dark / light switch
    this._onDisplay = () => this.resize(); // UI scale / 3D resolution changed
    window.addEventListener("exgs-display", this._onDisplay);
    this.watchDpr();
    this.resize();
  }

  async buildShip() {
    this.ship = await buildShip(this.cfg);
    this.root.add(this.ship.group);
    this.bodyAxes = new THREE.AxesHelper(0.25);
    this.bodyAxes.visible = false;
    this.ship.group.add(this.bodyAxes);
    this.buildTargetAttitude();
    const ta = this.cfg.thrust_arrow || {};
    this.thrustCfg = { m_per_n: ta.m_per_n ?? 0.02, max_m: ta.max_m ?? 0.6, origin: ta.origin_body_m ?? [0, 0, -0.4] };
    this.thrust = new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), new THREE.Vector3(), 0.1, 0xd29922, 0.04, 0.025);
    this.thrust.visible = false;
    this.ship.group.add(this.thrust);
    this.ship.group.visible = false; // until the first attitude/position arrives
    this.dirty = true;
  }

  // ------------------------------------------------------------------ static world
  buildStatic() {
    if (this.staticGroup) {
      this.root.remove(this.staticGroup);
      this.staticGroup.traverse((o) => {
        o.geometry?.dispose();
        o.material?.map?.dispose();
        o.material?.dispose();
      });
    }
    this.labels = this.labels.filter((l) => l.userData.keep);
    const g = (this.staticGroup = new THREE.Group());
    const line = parseColor(css("--line", "#888"));
    const dim = css("--dim", "#888");
    // grid on the ground plane z = 0 (NWU): 1 m cells, 10 m major lines
    const minor = [], major = [];
    for (let i = -GRID_HALF_M; i <= GRID_HALF_M; i++) {
      const t = i % 10 === 0 ? major : minor;
      t.push(i, -GRID_HALF_M, 0, i, GRID_HALF_M, 0, -GRID_HALF_M, i, 0, GRID_HALF_M, i, 0);
    }
    const mk = (arr, color, opacity) => {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(arr, 3));
      return new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
    };
    g.add(mk(minor, line, 0.35), mk(major, line, 0.9));
    // axes gizmo at the origin: North +X, West +Y, Up +Z
    const L = 1.5;
    for (const [dir, col, text] of [[[1, 0, 0], AXIS_COLORS.n, "N"], [[0, 1, 0], AXIS_COLORS.w, "W"], [[0, 0, 1], AXIS_COLORS.up, "Up"]]) {
      g.add(new THREE.ArrowHelper(new THREE.Vector3(...dir), new THREE.Vector3(), L, col, 0.2, 0.1));
      const s = labelSprite(text, `#${col.toString(16).padStart(6, "0")}`);
      s.position.set(dir[0] * (L + 0.25), dir[1] * (L + 0.25), dir[2] * (L + 0.25));
      s.userData.size = 0.035;
      g.add(s);
      this.labels.push(s);
    }
    // compass rose: ring with N/W/S/E marks
    const ringR = 5;
    const ring = [];
    for (let i = 0; i <= 96; i++) ring.push(Math.cos((i / 96) * 2 * Math.PI) * ringR, Math.sin((i / 96) * 2 * Math.PI) * ringR, 0);
    const rg = new THREE.BufferGeometry();
    rg.setAttribute("position", new THREE.Float32BufferAttribute(ring, 3));
    g.add(new THREE.Line(rg, new THREE.LineBasicMaterial({ color: parseColor(dim) })));
    for (const [t, x, y] of [["N", 1, 0], ["W", 0, 1], ["S", -1, 0], ["E", 0, -1]]) {
      const s = labelSprite(t, dim);
      s.position.set(x * (ringR + 0.6), y * (ringR + 0.6), 0);
      s.userData.size = 0.03;
      g.add(s);
      this.labels.push(s);
    }
    this.root.add(g);
    this.dirty = true;
  }

  applyTheme() {
    this.buildStatic();
    this.trailBg = parseColor(css("--panel", "#1a2027"));
    this.dirty = true;
  }

  // ------------------------------------------------------------------ trail
  buildTrail(max = 4000) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(max * 3), 3));
    geo.setAttribute("color", new THREE.BufferAttribute(new Float32Array(max * 3), 3));
    geo.setDrawRange(0, 0);
    this.trailLine = new THREE.Line(geo, new THREE.LineBasicMaterial({ vertexColors: true }));
    this.trailLine.frustumCulled = false;
    this.trailMax = max;
    this.trailBg = parseColor(css("--panel", "#1a2027"));
    this.root.add(this.trailLine);
  }

  /** points: [{t, p:[x,y,z]}], fades: 0..1 per point (1 newest). */
  setTrail(points, fades) {
    const n = Math.min(points.length, this.trailMax);
    const pos = this.trailLine.geometry.attributes.position, col = this.trailLine.geometry.attributes.color;
    const fg = parseColor(css("--info", "#58a6ff"));
    const bg = this.trailBg;
    for (let i = 0; i < n; i++) {
      pos.setXYZ(i, ...points[i].p);
      const f = 0.2 + 0.8 * fades[i]; // never fully invisible
      col.setXYZ(i, bg.r + (fg.r - bg.r) * f, bg.g + (fg.g - bg.g) * f, bg.b + (fg.b - bg.b) * f);
    }
    pos.needsUpdate = col.needsUpdate = true;
    this.trailLine.geometry.setDrawRange(0, n);
    this.trailCount = n;
    this.dirty = true;
  }

  // ------------------------------------------------------------------ setpoint
  buildSetpoint() {
    const g = (this.setpoint = new THREE.Group());
    const color = 0xbc6bff;
    const mat = new THREE.LineBasicMaterial({ color, transparent: true });
    g.add(new THREE.Mesh(new THREE.SphereGeometry(0.06, 16, 12), new THREE.MeshBasicMaterial({ color, wireframe: true, transparent: true, opacity: 0.55 })));
    const c = 0.15;
    const cross = new THREE.BufferGeometry();
    cross.setAttribute("position", new THREE.Float32BufferAttribute([-c, 0, 0, c, 0, 0, 0, -c, 0, 0, c, 0, 0, 0, -c, 0, 0, c], 3));
    g.add(new THREE.LineSegments(cross, mat));
    this.velArrow = new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(), 0.5, color, 0.1, 0.06);
    this.velArrow.visible = false;
    g.add(this.velArrow);
    g.visible = false;
    this.root.add(g);
    this.spLine = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineDashedMaterial({ color, dashSize: 0.1, gapSize: 0.07, transparent: true }),
    );
    this.spLine.visible = false;
    this.spLine.frustumCulled = false;
    this.root.add(this.spLine);
    this.spMats = [mat, g.children[0].material, this.spLine.material];
  }

  /** pos: NWU [x,y,z] or null; vel: NWU [x,y,z] or null; stale: bool. Needs the ship position for the dashed line. */
  setSetpoint(pos, vel, stale, shipPos) {
    const ok = Array.isArray(pos) && pos.length === 3 && pos.every(Number.isFinite);
    this.setpoint.visible = ok;
    this.spLine.visible = ok && !!shipPos;
    if (!ok) return;
    this.setpoint.position.set(...pos);
    for (const m of this.spMats) m.opacity = stale ? 0.3 : m === this.spMats[1] ? 0.55 : 1;
    if (shipPos) {
      const a = this.spLine.geometry.attributes.position;
      a.setXYZ(0, ...shipPos);
      a.setXYZ(1, ...pos);
      a.needsUpdate = true;
      this.spLine.computeLineDistances();
    }
    const sp = Array.isArray(vel) && vel.length === 3 && vel.every(Number.isFinite) ? Math.hypot(...vel) : 0;
    this.velArrow.visible = sp > 1e-3;
    if (this.velArrow.visible) {
      this.velArrow.setDirection(new THREE.Vector3(...vel).normalize());
      this.velArrow.setLength(Math.min(Math.max(sp, 0.3), 3), 0.12, 0.07); // 1 m of arrow per m/s, 0.3..3 m
    }
    this.dirty = true;
  }

  // ------------------------------------------------------------------ ship
  /** pos NWU or null; q vehicle [w,x,y,z] or null (not drawn without both). */
  setShip(pos, q, ghost) {
    if (!this.ship) return;
    const qt = q && vehicleQuatToThree(q);
    const ok = Array.isArray(pos) && pos.every(Number.isFinite) && qt;
    this.ship.group.visible = !!ok;
    if (!ok) return;
    this.ship.group.position.set(...pos);
    this.ship.group.quaternion.set(qt.x, qt.y, qt.z, qt.w); // Three.js order x, y, z, w
    if (ghost !== this._ghost) {
      this.ship.setGhost(ghost);
      this._ghost = ghost;
    }
    this.shipPosThree.set(pos[0], pos[2], -pos[1]);
    this.followShip();
    this.dirty = true;
  }

  setThrust(vecBody, ghost) {
    if (!this.thrust) return;
    const ok = Array.isArray(vecBody) && vecBody.length === 3 && vecBody.every(Number.isFinite);
    const mag = ok ? Math.hypot(...vecBody) : 0;
    const len = Math.min(mag * this.thrustCfg.m_per_n, this.thrustCfg.max_m);
    this.thrust.visible = ok && len >= 0.01;
    if (!this.thrust.visible) return;
    const dir = new THREE.Vector3(...vecBody).normalize();
    // the arrow ends at the nozzle end of the ship and points along the thrust vector (body frame)
    this.thrust.position.set(...this.thrustCfg.origin).addScaledVector(dir, -len);
    this.thrust.setDirection(dir);
    this.thrust.setLength(len, Math.min(0.06, len * 0.5), 0.03);
    this.thrust.line.material.opacity = this.thrust.cone.material.opacity = ghost ? 0.3 : 1;
    this.thrust.line.material.transparent = this.thrust.cone.material.transparent = true;
    this.dirty = true;
  }

  /**
   * Target attitude: the wanted body axes drawn as dashed, semi-transparent "ghost" axes at the ship position, in the
   * same colours as the real body axes (AxesHelper: X red, Y green, Z blue). Placed under worldRoot with the vehicle
   * quaternion exactly like the ship group, so they coincide with the real axes when the vehicle tracks perfectly.
   */
  buildTargetAttitude() {
    const L = 0.35;
    const g = (this.attGhost = new THREE.Group());
    this.attGhostMats = [];
    for (const [dir, col] of [[[L, 0, 0], 0xff0000], [[0, L, 0], 0x00ff00], [[0, 0, L], 0x0000ff]]) {
      const mat = new THREE.LineDashedMaterial({ color: col, dashSize: 0.03, gapSize: 0.02, transparent: true, opacity: 0.6, depthTest: false });
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(...dir)]), mat);
      line.computeLineDistances();
      line.renderOrder = 2;
      g.add(line);
      this.attGhostMats.push(mat);
    }
    g.visible = false;
    this.root.add(g);
    this.attGhostEnabled = true;
  }

  /** pos NWU [x,y,z] (ship position) or null, sp vehicle quaternion [w,x,y,z] or null (hidden), shown: the toggle. */
  setTargetAttitude(pos, sp) {
    if (!this.attGhost) return;
    const qt = sp && vehicleQuatToThree(sp);
    const ok = this.attGhostEnabled && qt && Array.isArray(pos) && pos.every(Number.isFinite);
    this.attGhost.visible = !!ok;
    if (ok) {
      this.attGhost.position.set(...pos);
      this.attGhost.quaternion.set(qt.x, qt.y, qt.z, qt.w);
    }
    this.dirty = true;
  }

  setTargetAttitudeEnabled(on) {
    this.attGhostEnabled = on;
    if (!on && this.attGhost) this.attGhost.visible = false;
    this.dirty = true;
  }

  setBodyAxes(on) {
    if (this.bodyAxes) this.bodyAxes.visible = on;
    this.dirty = true;
  }

  // ------------------------------------------------------------------ camera
  setCameraMode(mode) {
    if (!CAMERA_MODES.includes(mode)) return;
    this.mode = mode;
    const c = this.controls, cam = this.camera;
    const target = mode === "observer" ? new THREE.Vector3() : this.shipPosThree.clone();
    c.target.copy(target);
    c.enableRotate = mode !== "top";
    cam.up.set(mode === "top" ? 1 : 0, mode === "top" ? 0 : 1, 0); // top: North up on the screen
    if (mode === "follow") cam.position.copy(target).add(new THREE.Vector3(-1.3, 0.9, 1.6));
    else if (mode === "observer") cam.position.set(-9, 5, 11);
    else cam.position.copy(target).add(new THREE.Vector3(0, 6, 0));
    c.update();
    this.dirty = true;
  }

  followShip() {
    if (this.mode === "observer") return;
    const delta = this.shipPosThree.clone().sub(this.controls.target);
    if (delta.lengthSq() === 0) return;
    this.controls.target.add(delta);
    this.camera.position.add(delta);
  }

  // ------------------------------------------------------------------ rendering
  /** Renderer pixel ratio from devicePixelRatio, the GUI's UI scale and the 3D resolution setting (capped). */
  applyPixelRatio() {
    const d = getDisplay();
    const r = pixelRatio(window.devicePixelRatio, d.ui, d.res);
    if (r !== this._pr) {
      this._pr = r;
      this.renderer.setPixelRatio(r);
    }
  }

  /** Re-apply when devicePixelRatio changes (window moved to another screen, browser zoom). */
  watchDpr() {
    this._dprMq?.removeEventListener?.("change", this._onDpr);
    if (!window.matchMedia) return;
    this._dprMq = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
    this._onDpr = () => {
      this.resize();
      this.watchDpr();
    };
    this._dprMq.addEventListener?.("change", this._onDpr);
  }

  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    this.applyPixelRatio();
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  /** Render if something changed. Call every animation frame. */
  frame() {
    if (!this.dirty || !this.container.clientWidth) return;
    this.dirty = false;
    this.controls.update();
    for (const l of this.labels) {
      const s = l.userData.size * this.camera.position.distanceTo(l.getWorldPosition(this._tmp || (this._tmp = new THREE.Vector3())));
      l.scale.set(Math.max(s, 0.05), Math.max(s, 0.05), 1);
    }
    this.renderer.render(this.scene, this.camera);
  }

  /** World position (Three.js coordinates) of a body-frame point of the ship, for tests and the debug hook. */
  bodyPointToThree(p) {
    this.root.updateMatrixWorld(true);
    return new THREE.Vector3(...p).applyMatrix4(this.ship.group.matrixWorld).toArray();
  }

  dispose() {
    this.ro.disconnect();
    this.mq?.removeEventListener?.("change", this._onTheme);
    window.removeEventListener("exgs-theme", this._onTheme);
    window.removeEventListener("exgs-display", this._onDisplay);
    this._dprMq?.removeEventListener?.("change", this._onDpr);
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
