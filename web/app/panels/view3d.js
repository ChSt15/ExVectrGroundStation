// 3D view panel (design 5.5): ship, flaps, trail, setpoint, thrust arrow, ground grid, N/W/Up gizmo.
// Three.js is vendored under /static/vendor/three and loaded lazily, so a missing library only disables this panel.
import { registerPanel } from "./registry.js";
import { h } from "../ui.js";
import { describe, fmtAge } from "../fmt.js";
import { isValidQuat, isUsableSetpointQuat, attitudeErrorDeg, tvcThrustVector } from "../view3d/frames.js";
import { validateModel, resolveFlapAngles, radToDeg } from "../view3d/model_config.js";
import { Trail } from "../view3d/trail.js";
import { TargetSmoother } from "../view3d/target_smooth.js";

const CONSUMES = ["Attitude", "Position", "MissionState", "FlapState", "VehicleState", "Actuators"];
const MODES = [["follow", "Follow"], ["observer", "Observer"], ["top", "Top"]];

registerPanel({
  id: "view3d",
  title: "3D view",
  consumes: CONSUMES,
  create(el, ctx) {
    const canvasBox = h("div", { class: "v3d-canvas" });
    const overlay = h("div", { class: "v3d-overlay" });
    const buttons = new Map(MODES.map(([id, label]) => [id, h("button", { text: label, "aria-pressed": id === "follow" ? "true" : "false" })]));
    const axesBox = h("input", { type: "checkbox" });
    const ghostBox = h("input", { type: "checkbox", checked: true });
    ghostBox.checked = true;
    const toolbar = h("div", { class: "toolbar" }, h("span", { class: "toggle" }, ...buttons.values()), h("label", { class: "hint" }, axesBox, " body axes"), h("label", { class: "hint", title: "dashed axes: attitude setpoint of the vehicle" }, ghostBox, " target attitude"));
    const size = ctx.options && ctx.options.height ? ` v3d-${ctx.options.height}` : "";
    el.append(toolbar, h("div", { class: `v3d${size}` }, canvasBox, overlay));

    let scene = null, cfg = null, failed = null;
    let trail = new Trail(60);
    // Display-only smoothing of the target position (see view3d/target_smooth.js); panels keep the raw values.
    const target = new TargetSmoother();
    let lastShipPos = null, lastSpStale = false;
    let histGen = 0, pending = false;
    let state = { ship: "no data", flaps: "default", flapAngles: null };

    const store = ctx.store;
    const view = () => ctx.view();
    const lookup = (t, src) => store.get(view(), t, src);
    const desc = (type, field) => describe({ type, field }, lookup, ctx.now(), (t) => store.schema.get(t));

    function setOverlay(lines) {
      overlay.replaceChildren(...lines.map(([text, cls]) => h("div", { class: cls || "", text })));
    }

    async function init() {
      try {
        const r = await fetch("/config/model.json");
        if (!r.ok) throw new Error(`model config: HTTP ${r.status}`);
        cfg = await r.json();
        const problems = validateModel(cfg);
        if (problems.length) throw new Error(`model config invalid: ${problems.join("; ")}`);
        const { Scene3D } = await import("../view3d/scene.js");
        scene = new Scene3D(canvasBox, cfg);
        trail = new Trail(cfg.trail_window_s ?? 60);
        await scene.buildShip();
        buttons.forEach((b, id) => b.addEventListener("click", () => {
          scene.setCameraMode(id);
          buttons.forEach((bb, i) => bb.setAttribute("aria-pressed", String(i === id)));
        }));
        axesBox.addEventListener("change", () => scene.setBodyAxes(axesBox.checked));
        ghostBox.addEventListener("change", () => {
          scene.setTargetAttitudeEnabled(ghostBox.checked);
          refresh();
        });
        const loop = () => {
          if (!scene) return;
          if (!target.settled(ctx.now() ?? 0)) drawTarget();
          scene.frame();
          requestAnimationFrame(loop);
        };
        requestAnimationFrame(loop);
        loadHistory();
        refresh();
      } catch (e) {
        failed = e;
        scene?.dispose();
        scene = null;
        canvasBox.hidden = true;
        setOverlay([[`3D view unavailable: ${e.message}`, "warn"]]);
        console.error("view3d", e);
      }
    }

    // ---- trail from history (on connect, view change, snapshot/seek) plus live samples
    async function loadHistory() {
      const gen = ++histGen;
      const now = ctx.now();
      if (!scene || now === null) return;
      try {
        const res = await ctx.api.request("get_history", {
          view: view(),
          series: [{ type: "Position", fields: ["pos"] }],
          t0: now - trail.windowS,
          t1: now,
          max_points: 1500,
        });
        if (gen !== histGen) return;
        const s = res.series?.[0];
        const live = trail.pts.filter((p) => !s || !s.t.length || p.t > s.t[s.t.length - 1]);
        trail.load(s);
        for (const p of live) trail.add(p.t, p.p);
      } catch (e) {
        /* no history (not_implemented / disconnected): the trail starts from live samples */
      }
      refresh();
    }

    function drawTarget() {
      const now = ctx.now();
      scene.setSetpoint(now === null ? null : target.at(now), lastVel, lastSpStale, lastShipPos);
    }
    let lastVel = null;

    // ---- store -> scene
    function refresh() {
      pending = false;
      if (!scene) return;
      const now = ctx.now();
      const att = desc("Attitude", "q"), pos = desc("Position", "pos");
      const lines = [], extra = [];
      const usable = (d) => d.state === "fresh" || d.state === "stale";
      const q = usable(att) ? att.value : null, p = usable(pos) ? pos.value : null;
      const validQ = isValidQuat(q);
      const stale = att.state === "stale" || pos.state === "stale";
      if (!usable(att) || !usable(pos)) {
        scene.setShip(null, null, false);
        lines.push([`no ${!usable(att) && !usable(pos) ? "attitude and position" : !usable(att) ? "attitude" : "position"} data`, "warn"]);
      } else {
        scene.setShip(p, q, stale || !validQ);
        if (!validQ) lines.push(["invalid attitude (quaternion not unit length)", "bad"]);
        if (stale) lines.push([`stale ${fmtAge(Math.max(att.age ?? 0, pos.age ?? 0))}`, "warn"]);
      }
      // flaps: follow FlapState, default angle without it, last angles kept (marked) when stale
      const fe = lookup("FlapState");
      const fl = resolveFlapAngles(cfg, fe);
      scene.ship?.setFlapAngles(fl.angles);
      const fdesc = desc("FlapState", "angle");
      state.flaps = fl.source === "default" ? "default" : fdesc.state === "stale" ? "stale" : "data";
      state.flapAngles = fl.angles;
      if (state.flaps === "default") lines.push([`flap data: none (default ${Math.round(radToDeg(fl.angles[0]))}°)`, "dim"]);
      else if (state.flaps === "stale") lines.push([`flaps stale ${fmtAge(fdesc.age)}`, "warn"]);
      // setpoint (guidance) and thrust arrow
      const ms = desc("MissionState", "sp_pos"), mv = desc("MissionState", "sp_vel");
      // a stale or missing target is drawn as received (no extrapolation, no blending)
      const spEntry = lookup("MissionState");
      lastSpStale = ms.state === "stale";
      lastShipPos = usable(pos) ? p : null;
      lastVel = usable(mv) ? mv.value : null;
      if (usable(ms) && !lastSpStale && spEntry && now !== null) {
        target.update(now, ms.value, lastVel, spEntry.t);
        drawTarget();
      } else {
        target.reset();
        scene.setSetpoint(usable(ms) ? ms.value : null, lastVel, lastSpStale, lastShipPos);
      }
      // target attitude (display only, as received, no smoothing): ghost axes at the ship, hidden when none or stale
      const sq = desc("MissionState", "sp_q");
      const spq = sq.state === "fresh" && isUsableSetpointQuat(sq.value) ? sq.value : null;
      scene.setTargetAttitude(spq && lastShipPos ? lastShipPos : null, spq);
      const err = spq && validQ && !stale ? attitudeErrorDeg(q, spq) : null;
      if (err !== null) extra.push([`attitude error (setpoint vs estimate): ${err.toFixed(1)}°`, "dim"]);
      state.attErr = err;
      // thrust arrow: the legacy full vector when present (old recordings), else built from the ACTUATORS values
      // (deflection angles + magnitude) that the vehicle reports over exvectrnet-1
      const th = desc("VehicleState", "tvc_thrust");
      if (usable(th)) {
        scene.setThrust(th.value, stale || th.state === "stale");
      } else {
        const tv = desc("Actuators", "tvc");
        const tn = desc("Actuators", "thrust");
        const vec = usable(tv) && usable(tn) ? tvcThrustVector(tv.value, tn.value) : null;
        scene.setThrust(vec, stale || tv.state === "stale" || tn.state === "stale");
      }
      // trail
      if (now !== null) {
        trail.prune(now);
        scene.setTrail(trail.pts, trail.fades(now));
      }
      state.ship = lines.length ? lines.map((l) => l[0]).join("; ") : "ok";
      setOverlay([...lines, ...extra]);
    }
    const schedule = () => {
      if (!pending) {
        pending = true;
        requestAnimationFrame(refresh);
      }
    };

    init();

    const inst = {
      onPacket(pkt) {
        if (pkt.type === "Position") {
          const src = lookup("Position")?.src;
          if (!src || pkt.src === src) {
            const res = trail.add(pkt.t, pkt.f.pos);
            if (res === "reset") loadHistory();
          }
        }
        schedule();
      },
      onReset() {
        target.reset();
        trail.clear();
        loadHistory();
        schedule();
      },
      onViewChange() {
        target.reset();
        trail.clear();
        loadHistory();
        schedule();
      },
      onReady() {
        loadHistory();
      },
      onTick() {
        refresh();
      },
      freshness() {
        const d = desc("Position", "pos"), a = desc("Attitude", "q");
        const worst = [d, a].find((x) => x.state === "missing") || [d, a].find((x) => x.state === "stale") || d;
        return { state: worst.state, age: worst.state === "stale" ? worst.age : null };
      },
      destroy() {
        const s = scene;
        scene = null;
        s?.dispose();
      },
      /** The Scene3D (camera, scene graph) for end-to-end tests and development; null until ready. */
      get scene3d() {
        return scene;
      },
      /** State of the view for end-to-end tests (design 9, T8). */
      debug() {
        if (!scene) return { ready: false, error: failed?.message ?? null };
        const sh = scene.ship;
        const flaps = {};
        for (const [id, f] of sh.flaps) {
          const tipObj = f.pivot.getObjectByName("tip");
          let tip = null;
          if (tipObj) {
            scene.root.updateMatrixWorld(true);
            const w = tipObj.getWorldPosition(new tipObj.position.constructor());
            tip = sh.group.worldToLocal(w).toArray(); // body frame
          }
          flaps[id] = { angle_deg: f.angleRad === null ? null : radToDeg(f.angleRad), tip_body: tip };
        }
        return {
          ready: true,
          mode: scene.mode,
          ship: {
            visible: sh.group.visible,
            position_nwu: sh.group.position.toArray(),
            nose_three: scene.bodyPointToThree([0, 0, 1]),
            belly_three: scene.bodyPointToThree([-1, 0, 0]),
            origin_three: scene.bodyPointToThree([0, 0, 0]),
            ghost: !!scene._ghost,
          },
          flaps,
          flap_source: state.flaps,
          setpoint: { visible: scene.setpoint.visible, position_nwu: scene.setpoint.position.toArray(), velocity_arrow: scene.velArrow.visible },
          thrust_visible: scene.thrust.visible,
          trail_points: scene.trailCount ?? 0,
          status: state.ship,
          overlay: overlay.textContent,
          target_attitude: { visible: scene.attGhost.visible, error_deg: state.attErr ?? null },
          world_root_rotation_x: scene.root.rotation.x,
        };
      },
    };
    (window.exgs ??= {}).view3d = inst;
    return inst;
  },
});
