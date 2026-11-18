# GUI layout, panels, 3D view (design section 5)

Implemented: the shell and the data panels (T7) and the 3D view (T8). The virtual radio (T9) is not built yet and has no placeholder. Spec: [ground-station-design.md](ground-station-design.md) section 5.

Plain ES modules in `web/`, served by the core (`GET /`, `/static/...`). No build step, no CDN, works offline (Three.js 0.170.0 is vendored in `web/vendor/three/` and resolved by the import map in `index.html`). Checked in a real headless Chromium by `tests/integration/test_gui.py`.

## Files

| Path | Role |
|---|---|
| `web/index.html` | Page skeleton, import map, stylesheet. |
| `web/app/main.js` | Shell: connections, store wiring, layout, panel mounting, 10 Hz tick. |
| `web/app/ws.js` | WebSocket client: hello, request/reply by id, reconnect with backoff 0.5, 1, 2, 4, 5 s. |
| `web/app/store.js` | Client store per view: latest packet per (type, src) with `iv` and `stale_after`, view clock. |
| `web/app/fmt.js` | Pure value formatting and freshness (`describe`). Tested with `node --test`. |
| `web/app/ui.js` | DOM helpers and `Bindings` (declarative value rows). Data only enters the DOM via `textContent`. |
| `web/app/layout.js` | Layout validation, presets, subscription derivation. Pure. |
| `web/app/header.js` | Header bar. |
| `web/app/view3d/` | 3D view: `frames.js`, `model_config.js`, `trail.js` (pure, unit tested), `model.js` and `scene.js` (Three.js). Loaded lazily by the `view3d` panel. |
| `web/vendor/three/` | Vendored Three.js 0.170.0 (`three.module.min.js`, OrbitControls, GLTFLoader, BufferGeometryUtils) and its MIT `LICENSE`. |
| `web/app/panels/*.js` | One file per panel, `registry.js`, `index.js` (imports all panels). |
| `web/app/style.css` | Light and dark theme (follows the system), responsive. |
| `config/layout.default.json` | Presets `default`, `bench`, `flight`, `phone`. Served at `/config/layout.json`. |
| `web/test/` | `node:test` unit tests (`fmt`, `store`, `layout`, backoff) and the browser shims. |

## Behaviour

- **Connections.** The shell opens one WebSocket (mode `latest`, 20 Hz) for all panels with mode `latest`, and a second one (mode `all`) only if a mounted panel has mode `all` (the console). Subscription types are the union of the mounted panels' `consumes`; `"*"` (inspector) subscribes to everything. Only the active view is subscribed. After a reconnect the core sends a `snapshot` and the store is rebuilt.
- **View clock.** Ages are computed against the view's clock from the core (`clock` in `status` and `packets`), extrapolated with the browser's monotonic timer between messages, never the browser's wall clock. In the replay view the clock is the replay position: frozen while paused or ended, running at `speed` while playing.
- **Freshness per value (`fmt.describe`).**
  - `fresh`: normal text.
  - `stale`: older than the entry's `stale_after` (schema nominal rate if missing, else 2 s). Dimmed and struck through with an age badge. The last value stays visible.
  - `missing`: no packet of that type in this view. Shown as `—`.
  - `not supplied`: the packet arrived but the field was not in it (reduced radio set). Shown as `n/s` in italics.
  - Every value carries a small source tag (`usb`, `link`, `fake`). For values from several sources the freshest source of the type is shown. The inspector lists every (type, source).
  - Derived values (roll/pitch/yaw, tilt, vector to target, distance) use the oldest of their inputs for freshness and join the source tags.
  - Each panel header shows `stale <age>` or `no data` for the worst of its values.
- **Vehicle-reported values only.** Panels never show anything inferred from commands that were sent. Derived values are display-only arithmetic on received data and are labelled as such.

## Layout and presets

`layout.json` (loaded from `/config/layout.json`, path set by `[gui] layout`):

```json
{ "default": "default",
  "presets": { "<name>": {
      "label": "Default",
      "columns": "minmax(300px, 1fr) minmax(300px, 1fr)",
      "areas": ["left right", "bottom bottom"],
      "panels": [ { "panel": "modes", "area": "left", "collapsed": false } ] } } }
```

`columns` is a CSS `grid-template-columns`, `areas` are `grid-template-areas` rows. Panels that share an area are stacked in list order inside it, so each area is its own column of panels and panels in different columns do not share row heights. The header has a preset selector. The choice is remembered in `localStorage` (`exgs.preset`), and so is each panel's collapsed state per preset (click the arrow or the title). Storage access is wrapped in try/catch: without it everything works, nothing is remembered. A narrow screen (up to 600 px) starts with `phone` (one column) when no preset was chosen before. An invalid or missing layout file falls back to one column with every registered panel (problems are logged to the browser console). Unknown panel ids and unknown areas are reported and skipped.

## Panels

| Id | Consumes | Notes |
|---|---|---|
| `modes` | `VehicleState` | Vehicle mode (large, colour-coded), ARMED, SIM. The design's "active control source" is not shown: no packet field carries it yet. |
| `mission` | `MissionState` | Mode, phase, index, mission time as T-/T+. |
| `failures` | `VehicleState` | Each flag as reported now plus "last set N ago" from packets received in this view; title shows all clear or ACTIVE. |
| `position` | `Position`, `Attitude`, `Gps` | Position, velocity, accuracies (NWU), quaternion, roll/pitch/yaw (ZYX, display only), tilt from vertical, angular rate, GPS lat/lon in degrees. |
| `guidance` | `MissionState`, `Position` | Target position and velocity, vector and distance to the target (display only). |
| `actuators` | `VehicleState`, `FlapState`, `TvcState` | TVC thrust and torque, vanes, motors, enable flags, flap table (actual and commanded, deg). |
| `sensors` | `VehicleState`, `Attitude`, `Position`, `Gps` | Per-sensor state, accuracies, ground distance. |
| `link` | `LinkStats`, `RadioLinkStats`, `status` | One block per source (state, device, rates, error counters) plus the radio link block (RSSI, LQ, SNR, rates, antenna). |
| `recording` | `status`, `logs.list` | Start (optional label), Stop, note entry, file, duration, size, free disk, recorder error, log list with Open (opens the log, switches to REPLAY). Recording is manual only. |
| `transport` | `status` | Visible in the REPLAY view only: play/pause, scrubber (seeks throttled to 10/s while dragging), time `mm:ss / mm:ss`, speed 0.25 to 8, jump +-5 s, keyboard space and arrow keys. |
| `view3d` | `Attitude`, `Position`, `MissionState`, `FlapState`, `VehicleState` | Ship, flaps, trail, setpoint, thrust arrow, ground grid, N/W/Up gizmo. See the 3D view section. |
| `console` | `ConsoleText` (mode `all`), `event` | Vehicle text and GS events, filter, pause, clear, 500 lines. |
| `inspector` | `*` | All (type, source) pairs with age and a field table from the schema. |

The header shows LIVE/REPLAY, per-source rate (`LinkStats.rx_frames_s`), radio link quality, REC state and duration, connection state and the preset selector.

## 3D view

Panel `view3d` (first in the middle column of `default`, first in `phone`). It consumes `Attitude`, `Position` (ship pose, trail), `MissionState` (setpoint), `FlapState` and `VehicleState` (`tvc_thrust`). Everything comes from the store, so it works the same live and in replay.

**Frames** (`view3d/frames.js`, tested in `web/test/frames.test.js`):

- World: NWU (X North, Y West, Z Up). Three.js is Y-up. All vehicle content sits under one `worldRoot` group with `rotation.x = -90 deg`, which maps NWU `(x, y, z)` to Three.js `(x, z, -y)`: North is +X, West is -Z (East +Z), Up is +Y. Inside `worldRoot` positions are NWU metres and the attitude is the vehicle quaternion.
- Quaternion: the vehicle `q = [w, x, y, z]` (body to world) becomes `quaternion.set(x, y, z, w)` after normalising. A norm that deviates by more than 1 % shows "invalid attitude" (ship drawn as a ghost).
- Body frame: right-handed, +Z the long axis with the nose toward +Z, the belly faces -X (South with the identity attitude), the back +X. The flaps TL and BL are on the +Y side, TR and BR on -Y. Flap angle 0 is fully extended along +-Y, increasing angle folds the flap toward +X (the back), 90 deg is folded flat. Source: workspace `docs/system-architecture.md`, "Vehicle geometry and body frame". The ship carries a dark strip on the belly as an orientation cue.

**Scene.** Ground grid at z = 0 (1 m cells, 10 m major lines, +-50 m), axes gizmo at the origin with N, W and Up labels, a 5 m compass ring with N/W/S/E, the ship, the trail (last `trail_window_s`, default 60 s, fading with age), the setpoint (ghost sphere with crosshair at `MissionState.sp_pos`, a dashed line from the ship, an arrow for `sp_vel` of 1 m per m/s), and a thrust arrow along `VehicleState.tvc_thrust` (body frame) ending at the tail, scaled by `thrust_arrow.m_per_n` up to `max_m`. Optional body axes (checkbox). Cameras (OrbitControls): Follow (orbits the ship), Observer (orbits the origin), Top (North up, zoom only).

**Data rules.** The ship needs both attitude and position, and is not drawn without them ("no attitude and position data"). If either is stale it is drawn semi-transparent with a "stale <age>" label, never extrapolated. With no `FlapState` in the view all flaps show `default_flap_angle_deg` (45 deg) and the overlay and the actuators panel say "flap data: none (default 45 deg)". Stale flap data keeps the last angles with a "flaps stale" label. The flaps jump to the reported angle, no smoothing. The trail is loaded with `get_history` on connect, on a view change and on every snapshot (so after a replay seek), then extended with live `Position` packets from the source that is currently preferred; a sample older than the last one resets it.

**Model config** (`config/model.<name>.json`, set by `[gui] model`, served at `/config/model.json`, files in `models/` at `/models/`). The placeholder is a procedural ship with the real dimensions (body 13 cm diameter, 74 cm high, origin at the centre of mass 37 cm from the bottom, parabolic nose of 8 cm; top flaps 7 x 14 cm with the bottom edge 52 cm from the bottom, bottom flaps 8 x 21 cm starting 1 cm above the bottom, both with an 8 cm chamfer).

```json
{ "name": "placeholder",
  "body": { "source": { "primitive": "starship-placeholder", "height_m": 0.74, "radius_m": 0.065,
                        "nose_length_m": 0.08, "origin_from_bottom_m": 0.37 },
            "model_to_body": { "rotation_deg_xyz": [0, 0, 0], "translation_m": [0, 0, 0], "scale": 1.0 } },
  "flaps": [ { "id": "tl", "index": 0,
               "source": { "primitive": "flap", "width_m": 0.07, "height_m": 0.14, "thickness_m": 0.004,
                           "chamfer_m": 0.08, "chamfer_corner": "out_top" },
               "model_to_body": { "rotation_deg_xyz": [0, 0, 90], "translation_m": [0, 0.065, 0.22] },
               "pivot_body_m": [0, 0.065, 0.22], "axis_body": [0, 0, 1], "sign": -1, "model_angle_deg": 0 } ],
  "default_flap_angle_deg": 45,
  "thrust_arrow": { "m_per_n": 0.02, "max_m": 0.6, "origin_body_m": [0, 0, -0.37] },
  "trail_window_s": 60 }
```

- `source`: `{ "primitive": name, ...parameters }` (`starship-placeholder`, `flap`) or `{ "gltf": "models/ship.glb", "node": null }`. With `node` set, that node of the file is used (its own transform in the file is ignored) so the body and the flaps can be nodes of one file or separate files. Both go through the same loader and the same transforms.
- `model_to_body` (body and, as an extension of the design, each flap): rotation as Euler XYZ in degrees, translation in metres, uniform scale, mapping the model's own frame into the body frame **at the modelled angle**. glTF is Y-up, so a glTF ship with its long axis along Y needs `[90, 0, 0]` (glTF Y goes to body Z, glTF Z to body -Y). The primitives are built directly in the body frame.
- Flaps: `pivot_body_m` and `axis_body` (body frame, unit vector) define the hinge. The pivot group rotates by `sign * (angle - model_angle_deg)` about the axis, `angle` in the vehicle convention and `model_angle_deg` the angle at which the flap is modelled in its file. `index` selects the element of `FlapState.angle` (0 tl, 1 tr, 2 bl, 3 br).
- Placeholder flap plate: hinge on the model's Z axis, the plate extends toward +X, `chamfer_m` is the length of a 45 deg cut at the outer corner `out_top` (toward the nose, top flaps) or `out_bottom` (toward the tail, bottom flaps). The architecture note gives only "8 cm chamfer"; the corner and the 45 deg cut are assumptions of the placeholder and change by config only. The pivot is at the flap's vertical centre on the body surface at +-Y (z = +0.22 m top, -0.255 m bottom; the bottom flaps span z = -0.36 to -0.15 m by the stated 21 cm height).

**Swapping in a real glTF model** (config change only, no code):

1. Put the file in `models/` (for example `models/starship.glb`) and copy `config/model.placeholder.json` to `config/model.starship.json`.
2. Body: `"source": { "gltf": "models/starship.glb", "node": "hull" }` and `model_to_body` so that the model's nose points to body +Z, the belly to -X and the origin sits at the centre of mass (glTF Y-up long axis: `rotation_deg_xyz: [90, 0, 0]` plus a translation if the file's origin is elsewhere).
3. Flaps: either `{ "gltf": ..., "node": "flap_tl" }` per flap or separate files. Set `pivot_body_m` to the hinge in the body frame, `axis_body` and `sign` so that increasing angle folds toward +X, and `model_angle_deg` to the angle the flap has in the file (0 if modelled extended). Check with the checkbox "body axes" and by watching the flaps move.
4. Set `[gui] model = "config/model.starship.json"` in the core config and reload the page. A config that fails validation, or a model that fails to load, shows "3D view unavailable: ..." in the panel, and the rest of the GUI keeps working. Run `.venv/bin/pytest tests/integration/test_view3d.py` to re-check the loader with `models/test-box.gltf`.

**Debug hook** for end-to-end tests: `window.exgs.view3d.debug()` returns the ship pose and visibility, nose, belly and origin in Three.js coordinates, per-flap angle and outer-edge position in the body frame, flap source (`data`, `default`, `stale`), setpoint and thrust visibility, trail point count, overlay text and camera mode. `window.exgs.view3d.scene3d` gives the Scene3D object.

## Adding a panel

1. Create `web/app/panels/<name>.js` and call `registerPanel` (`id` unique and matching `[a-z_]+`, `title`, `consumes`, optional `mode: "all"`, optional `views: ["replay"]`, `create(el, ctx)`).
2. Add `import "./<name>.js";` to `web/app/panels/index.js`.
3. Add `{ "panel": "<id>", "area": "<area>" }` to the presets in `config/layout.default.json` (or your own layout file).

`ctx`: `store`, `schema`, `api` (`api.request(op, payload)` returns a promise of `data` or rejects with `{code, message}`), `fmt`, `h`, `Bindings`, `view()` (`"live"` or `"replay"`), `status()` (last `status` message), `now()` (view time), `switchView(v)`, `setTitle(text)`.

The object returned by `create` may have: `onPacket(pkt)` (per received packet of a consumed type in the current view; also called for the items of a snapshot), `onReset(view)` (snapshot replaced the store), `onTick(now)` (10 Hz), `onViewChange(view)`, `onStatus(status)`, `onEvent(evt)`, `onReady()` (after every connect), `freshness()` returning `{state, age}` for the header badge, `destroy()`.

Most panels only need `Bindings`:

```js
const b = new ctx.Bindings(ctx);
b.row(el, "Phase", { type: "MissionState", field: "mission_phase", kind: "enum" });
b.row(el, "Position", { type: "Position", field: "pos", kind: "vec", unit: "m" });
return { onTick: () => b.update(), onViewChange: () => b.update(), freshness: () => b.freshness() };
```

Value spec: `type` (or `types` for derived values), `field`, `index`, `src`, `unit`, `digits`, `scale`, `kind` (`num`, `vec`, `enum`, `bool`, `time`, `text`), `labels` (bool), `format(value)`, `derive(entry or entries)`. The cell options `tone(description)` (colour name) and `class` are the optional last argument of `row` and `cell`.

## Testing

```bash
.venv/bin/python tools/run_js_tests.py     # node --test web/test if node exists, else the same files in headless Chromium
.venv/bin/pytest tests/integration/test_gui.py
.venv/bin/python tools/browser_check.py http://127.0.0.1:8765/ --wait 3 --width 390 --shot /tmp/gui.png   # screenshot a running core
```

`browser_check.py` drives Chromium over the DevTools protocol with no extra dependencies (aiohttp only). The browser tests are skipped when no Chromium or Chrome is installed.

## Known gaps

- Replay: the controls are wired to `replay.*` and `status.replay`, but were exercised only against the "not available" answer because the Replayer (T4) is not merged in this branch. Scrubber markers for `GsEvent`, `Note` and mode changes (design 5.7) need index data the API does not expose yet.
- Not built: virtual radio (T9).
- 3D view: the placeholder's chamfer corner and 45 deg cut are assumptions (see above). The glTF path is tested with a generated box file only, not with a real ship model. Nothing is drawn for the vane angles.
