"""3D view tests: config and files (static), then a real headless browser against the core with the fake source
(skipped when no Chromium-based browser is installed). Tests write only to temp dirs."""

import json
import math
import sys
from pathlib import Path

import pytest

from tests.integration.test_api import make_app, running

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import browser_check  # noqa: E402

needs_browser = pytest.mark.skipif(browser_check.find_browser() is None, reason="no Chromium-based browser")

# Body frame, NWU world; metres. Dimensions from docs/system-architecture.md (Vehicle geometry).
R = 0.065
FLAPS = {  # id: (side along Y, hinge z, width, height)
    "tl": (+1, 0.23, 0.07, 0.14),
    "tr": (-1, 0.23, 0.07, 0.14),
    "bl": (+1, -0.255, 0.08, 0.21),
    "br": (-1, -0.255, 0.08, 0.21),
}
DEBUG = "JSON.stringify(window.exgs.view3d.debug())"


def test_placeholder_model_config_has_the_real_dimensions_and_conventions():
    cfg = json.loads((ROOT / "config" / "model.placeholder.json").read_text())
    src = cfg["body"]["source"]
    assert (src["height_m"], 2 * src["radius_m"], src["origin_from_bottom_m"]) == (0.74, 0.13, 0.37)
    assert cfg["default_flap_angle_deg"] == 45
    flaps = {f["id"]: f for f in cfg["flaps"]}
    assert [f["index"] for f in cfg["flaps"]] == [0, 1, 2, 3]
    for fid, (side, zc, w, h) in FLAPS.items():
        f = flaps[fid]
        assert f["pivot_body_m"] == [0, side * R, zc]
        assert f["axis_body"] == [0, 0, 1] and f["sign"] == -side and f["model_angle_deg"] == 0
        assert (f["source"]["width_m"], f["source"]["height_m"], f["source"]["chamfer_m"]) == (w, h, 0.08)
    d = cfg["dimensions_m"]
    assert (d["tube_height"], d["nose_height"], src["nose_length_m"]) == (0.54, 0.20, 0.20)
    assert math.isclose(d["tube_height"] + d["nose_height"], d["total_height"]) and d["total_height"] == src["height_m"]
    # top flaps: lower edge 53 cm from the bottom (z = +0.16); bottom flaps: lower edge 1 cm (z = -0.36)
    assert math.isclose(flaps["tl"]["pivot_body_m"][2] - 0.07, d["top_flap_lower_edge_from_bottom"] - 0.37)
    assert math.isclose(flaps["bl"]["pivot_body_m"][2] - 0.105, d["bottom_flap_lower_edge_from_bottom"] - 0.37)
    assert math.isclose(flaps["bl"]["pivot_body_m"][2], 0.115 - 0.37)
    assert d["top_flap_cant_deg"] == 10 and flaps["tl"]["cant_deg"] == flaps["tr"]["cant_deg"] == 10
    assert "cant_deg" not in flaps["bl"] and "cant_deg" not in flaps["br"]
    assert cfg["thrust_arrow"]["origin_body_m"] == [0, 0, -0.37]


def test_every_vendored_three_file_is_present_with_its_license():
    v = ROOT / "web" / "vendor" / "three"
    for rel in (
        "three.module.min.js",
        "LICENSE",
        "addons/controls/OrbitControls.js",
        "addons/loaders/GLTFLoader.js",
        "addons/utils/BufferGeometryUtils.js",
    ):
        assert (v / rel).is_file(), rel
    assert 'const t="170"' in (v / "three.module.min.js").read_text()[:400]
    index = (ROOT / "web" / "index.html").read_text()
    assert "/static/vendor/three/three.module.min.js" in index and "cdn" not in index


def test_test_gltf_is_in_step_with_its_generator():
    sys.path.insert(0, str(ROOT / "tools"))
    import make_test_gltf

    assert json.loads((ROOT / "models" / "test-box.gltf").read_text()) == make_test_gltf.build()


async def open_view(rig, b, wait=3.0):
    await b.goto(rig.url() + "/")
    await b.sleep(wait)
    return json.loads(await b.eval(DEBUG))


CANT_DEG = {"tl": 10, "tr": 10, "bl": 0, "br": 0}


def flap_tip_expected(side, zc, w, angle_rad, h=0.0, cant_deg=0.0):
    """Outer edge of the flap (centre height) in the body frame: 0 deg = out along +-Y, increasing angle folds toward
    +X. The cant tilts the flap inward about its lower hinge point (z = zc - h/2)."""
    phi = math.radians(cant_deg) * side
    y, z = side * w * math.cos(angle_rad), h / 2
    return [
        w * math.sin(angle_rad),
        side * R + y * math.cos(phi) - z * math.sin(phi),
        zc - h / 2 + y * math.sin(phi) + z * math.cos(phi),
    ]


@needs_browser
async def test_browser_view_frames_flaps_and_overlays_with_flap_data():
    async with running(make_app(speed=2.0)) as rig:
        async with browser_check.Browser(1500, 1000, 9351) as b:
            d = await open_view(rig, b)
            assert d["ready"] and d["ship"]["visible"] and not d["ship"]["ghost"], d
            assert d["world_root_rotation_x"] == pytest.approx(-math.pi / 2)
            # the fake attitude is within a few degrees of upright: nose Up (+Y in Three.js), belly South (-X)
            o, n, bel = d["ship"]["origin_three"], d["ship"]["nose_three"], d["ship"]["belly_three"]
            nose = [n[i] - o[i] for i in range(3)]
            belly = [bel[i] - o[i] for i in range(3)]
            assert nose[1] > 0.95 and belly[0] < -0.9, (nose, belly)
            # flaps follow the reported angles (checked against the geometry of the convention)
            assert d["flap_source"] == "data"
            for fid, (side, zc, w, h) in FLAPS.items():
                f = d["flaps"][fid]
                exp = flap_tip_expected(side, zc, w, math.radians(f["angle_deg"]), h, CANT_DEG[fid])
                assert f["tip_body"] == pytest.approx(exp, abs=2e-3), (fid, f, exp)
            assert d["setpoint"]["visible"] and d["trail_points"] > 0 and d["thrust_visible"]
            # the only overlay line is the attitude error readout (Running: the fake source supplies sp_q)
            assert d["overlay"].startswith("attitude error (setpoint vs estimate): "), d["overlay"]
            ta = d["target_attitude"]
            assert ta["visible"] and 0 < ta["error_deg"] < 15, ta
            # the toggle hides the ghost axes
            await b.eval("document.querySelectorAll('[data-panel=view3d] .toolbar input')[1].click()")
            await b.sleep(0.4)
            assert not json.loads(await b.eval(DEBUG))["target_attitude"]["visible"]
            # camera modes work
            for mode in ("Observer", "Top", "Follow"):
                await b.eval(
                    f"[...document.querySelectorAll('[data-panel=view3d] .toggle button')].find(x => x.textContent === '{mode}').click()"  # noqa: E501
                )
                await b.sleep(0.3)
                assert json.loads(await b.eval(DEBUG))["mode"] == mode.lower()
            # the canvas actually has pixels
            lit = await b.eval(
                "(() => { const c = document.querySelector('[data-panel=view3d] canvas');"
                "const t = document.createElement('canvas');"
                "t.width = c.width; t.height = c.height; const g = t.getContext('2d'); g.drawImage(c, 0, 0);"
                "const px = g.getImageData(0, 0, t.width, t.height).data; let n = 0;"
                "for (let i = 3; i < px.length; i += 4) if (px[i] > 0) n++; return n; })()"
            )
            assert lit > 1000
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_browser_flaps_default_to_45_degrees_without_flap_data():
    async with running(make_app(rates={"FlapState": 0})) as rig:
        async with browser_check.Browser(1500, 1000, 9352) as b:
            d = await open_view(rig, b)
            assert d["flap_source"] == "default"
            for fid, (side, zc, w, h) in FLAPS.items():
                assert d["flaps"][fid]["angle_deg"] == pytest.approx(45)
                assert d["flaps"][fid]["tip_body"] == pytest.approx(
                    flap_tip_expected(side, zc, w, math.pi / 4, h, CANT_DEG[fid]), abs=2e-3
                )
            assert "flap data: none (default 45" in d["overlay"]
            hint = await b.eval("document.querySelector('[data-panel=actuators] [data-testid=noflaps]')?.hidden")
            assert hint is False
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_browser_stale_attitude_draws_a_ghost_ship_and_stale_flaps_are_marked():
    faults = [
        {"kind": "dropout", "at": 1.0, "duration": 1000, "types": ["Attitude", "FlapState"]},
    ]
    async with running(make_app(faults=faults, speed=2.0)) as rig:
        async with browser_check.Browser(1500, 1000, 9353) as b:
            d = await open_view(rig, b, wait=7.0)
            assert d["ship"]["visible"] and d["ship"]["ghost"], d
            assert "stale" in d["overlay"]
            assert d["flap_source"] == "stale"
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


GLTF_TEST = """
(async () => {
  const m = await import('/static/app/view3d/model.js');
  const T = await import('three');
  const cfg = {
    body: { source: { gltf: 'models/test-box.gltf', node: 'hull' }, model_to_body: { rotation_deg_xyz: [90, 0, 0] } },
    flaps: [{ id: 'f', index: 0, source: { gltf: 'models/test-box.gltf', node: 'flap' },
      model_to_body: { rotation_deg_xyz: [0, 0, 0], translation_m: [0.05, 0, 0.1] },
      pivot_body_m: [0.05, 0, 0.1], axis_body: [0, 0, 1], sign: -1, model_angle_deg: 0 }],
  };
  const ship = await m.buildShip(cfg);
  const box = new T.Box3().setFromObject(ship.group.children[0]);
  const body = [box.max.x - box.min.x, box.max.y - box.min.y, box.max.z - box.min.z, box.min.x, box.min.y, box.min.z];
  const tip = () => { ship.group.updateMatrixWorld(true);
    const fb = new T.Box3().setFromObject(ship.flaps.get('f').pivot);
    return [fb.min.x, fb.max.x, fb.min.y, fb.max.y]; };
  ship.setFlapAngles([0]); const a0 = tip();
  ship.setFlapAngles([Math.PI / 2]); const a90 = tip();
  return JSON.stringify({ body, a0, a90 });
})()
"""


@needs_browser
async def test_browser_gltf_model_loads_through_the_same_path_with_known_axes():
    async with running(make_app()) as rig:
        async with browser_check.Browser(800, 600, 9354) as b:
            await b.goto(rig.url() + "/")
            await b.sleep(1.0)
            r = json.loads(await b.eval(GLTF_TEST, await_promise=True))
            sx, sy, sz, mnx, mny, mnz = r["body"]
            # glTF box 0.10 x 0.40 (Y long) x 0.20; model_to_body rotates 90 deg about X: long axis -> body Z,
            # glTF Z -> body -Y. The node's own translation in the file is ignored (centred on the origin).
            assert (sx, sy, sz) == pytest.approx((0.10, 0.20, 0.40), abs=1e-5)
            assert (mnx, mny, mnz) == pytest.approx((-0.05, -0.10, -0.20), abs=1e-5)
            # flap: hinge edge at x = 0.05, plate extends toward +X (0.06 wide) at angle 0 ...
            x0, x1, _, _ = r["a0"]
            assert (x0, x1) == pytest.approx((0.05, 0.11), abs=1e-5)
            # ... folded 90 deg (sign -1: about +Z by -90 deg) the plate turns from +X to -Y around its hinge
            x0, x1, y0, y1 = r["a90"]
            assert (x0, x1, y0, y1) == pytest.approx((-0.01, 0.11, -0.06, 0.0), abs=1e-5)
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


@needs_browser
async def test_browser_trail_is_rebuilt_from_history_after_a_replay_seek():
    from tests.integration.test_api import Client
    from tests.support_logs import T0_NS, make_log

    app = make_app()
    log = make_log(Path(app.library.directory), "dropouts", 45.0)
    t0 = T0_NS / 1e9
    async with running(app, sources=False) as rig:
        async with browser_check.Browser(1500, 1000, 9355) as b:
            await b.goto(rig.url() + "/")
            await b.sleep(1.0)
            await b.eval(
                "[...document.querySelectorAll('header.top .toggle button')].find(x => x.textContent === 'REPLAY').click()"  # noqa: E501
            )
            c = await Client.connect(rig)
            await c.ok("replay.open", name=log.name)

            async def trail_after_seek(t):
                await c.ok("replay.seek", t=t0 + t)
                await b.sleep(1.2)
                return json.loads(await b.eval(DEBUG))

            late = await trail_after_seek(30)
            assert late["ship"]["visible"] and late["trail_points"] > 100, late
            early = await trail_after_seek(5)
            assert early["trail_points"] < late["trail_points"] / 2, (early["trail_points"], late["trail_points"])
            again = await trail_after_seek(30)
            assert again["trail_points"] == pytest.approx(late["trail_points"], rel=0.1)
            assert not [m for m in b.console if "EXCEPTION" in m], b.console


def test_every_flap_of_the_placeholder_has_its_chamfer_on_the_top():
    """User decision 2026-11-20: the bottom flaps were drawn upside down. The chamfer is on the top (nose side) of every
    flap, set by `chamfer_corner = "out_top"` in the model config."""
    import json
    from pathlib import Path

    cfg = json.loads((Path(__file__).parents[2] / "config" / "model.placeholder.json").read_text())
    assert [f["id"] for f in cfg["flaps"]] == ["tl", "tr", "bl", "br"]
    assert {f["source"]["chamfer_corner"] for f in cfg["flaps"]} == {"out_top"}
