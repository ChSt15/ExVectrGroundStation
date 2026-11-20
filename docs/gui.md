# GUI: structure, views, panels, 3D view (design section 5)

Redesigned on `feature/gui-redesign` (user request 2026-11-20): views by usage instead of devices, the main groups Vehicle, Guidance and Mission left to right with the 3D view in the centre, one Operations dock for everything that is setup or diagnostics, a data-source banner and recording controls on every page, and a new dark theme with a high-contrast light option. Spec: [ground-station-design.md](ground-station-design.md) section 5. Screenshots (fake source): `docs/img/bench-1920.png`, `bench-1366.png`, `flight-1920.png`, `flight-1366.png`.

Plain ES modules in `web/`, served by the core (`GET /`, `/static/...`). No build step, no CDN, works offline (Three.js 0.170.0 is vendored in `web/vendor/three/` and resolved by the import map in `index.html`). Checked in a real headless Chromium by `tests/integration/test_gui.py` and the other `test_*_gui.py` / `*_widget.py` tests.

## Page structure

From top to bottom, identical in every view and in live and replay:

1. **Data-source banner** (`status/source_banner.js`, logic `status/source_logic.js`, unit tested in `web/test/source_logic.test.js`). One full-width coloured strip:
   - **LIVE · REAL VEHICLE** (green) when the freshest `VehicleStatus.simulation` is false;
   - **LIVE · SIMULATION** (violet) when it is true;
   - **LIVE · REAL OR SIMULATION UNKNOWN** or **LIVE · last reported REAL/SIMULATION · stale** (amber) when no `VehicleStatus` arrived, the flag is not supplied or the packet is stale (never assumed);
   - **REPLAY** (blue, striped) with the log name, the position `mm:ss / mm:ss`, playing/paused/ended, and **recorded: REAL / SIMULATION / unknown** from the replayed `VehicleStatus.simulation`; the replay transport (play/pause, scrubber, ±5 s, speed, keyboard space and arrows) sits inside the banner;
   - **NO CORE CONNECTION** (red) while the WebSocket is down.
   In replay and without a connection the banner shows **🔒 replay: inputs locked** (or the offline reason). The status bar no longer repeats the simulation cell: the banner is the one place for it.
2. **Header** (`header.js`): the layout views **Bench / Flight** (Alt+1, Alt+2; remembered in `localStorage` `exgs.view`), the data view **LIVE / REPLAY**, the **RC chip** (the RC mode the core reports: `RC SLAVE · Pocket`, `RC MASTER · SENDING` in amber, `RC IDLE`; click opens the radio tab), the radio link quality, the **recorder block** and the core connection and theme switch (dark default, light high contrast, `exgs.theme`).
   - Recorder block (status from `status.recorder`, logic `describeRecorder`): idle shows `not recording`, the free disk space (amber below 5 GB, red below 1 GB) and **● Record** (starts a recording at once, manual only). Recording shows a pulsing red **REC mm:ss**, the log name, its size, free space and a red **■ Stop** button (no confirmation). Recorder errors and dropped packets are shown in red. Labels, notes and the log list stay in the dock tab Recordings.
3. **Mission status bar** (`status/status_bar.js`, unchanged logic, see below). New: a red **TX POWER** line when any transmit power (LinkConfig, TxModuleStats, RxModuleStats) is above 10 dBm, so the warning is visible whichever dock tab is open (stale values stay listed and are marked).
4. **Workspace**: the view's groups (`#panels`) and the Operations dock (`#dock`) on the right.

## Views and groups

| View | Groups (left to right) | Dock | Purpose |
|---|---|---|---|
| **Bench** (default) | Vehicle: attitude, position & velocity, actuators, sensors & estimator, failures · Guidance: 3D view (large), target vs current · Mission: mission | open | everything, for bench work and setup |
| **Flight** | Vehicle: attitude, position, actuators (compact) · Guidance: 3D view (fills the column), errors · Mission: mission (compact), RC input (sticks) | side rail | only what matters in flight, large numbers; fits 1366x768 without scrolling (tested) |

The tuning view is not built yet (see "Tuning view: remaining work"). The old `default` and `phone` presets and the device idea are gone; a narrow window (below 1100 px) stacks the groups in one column and the page scrolls (no horizontal scroll at 390 px, tested).

**Why this arrangement.** The reading order is Vehicle → Guidance → Mission. The 3D view shows ship pose, target, trail, flaps and thrust together, which is above all the guidance picture (where the vehicle is against where it should be), so it heads the centre column and is the largest element; the numeric target-vs-current table sits right under it. Vehicle state (the inputs to guidance) is on the left, the mission (what guidance follows) on the right. Inside each group the most important values come first as big tiles with units (attitude angles, altitude and speeds, thrust and TVC; distance and errors to target; phase and mission time), then tables, then details and hints. Safety-relevant states (REAL/SIMULATION, replay, armed, motors, failsafe, failures, RC master, transmit power) are in the banner, header and status bar, never only inside a panel; panels do not repeat them (the old `modes` panel is removed).

**Operations dock.** Tabs: Radio, Requests, Commands (temp), Link & power, Recordings, Raw sensors, Console, Inspector (`role=tablist`, arrow keys). In Bench it is a column (440 px, 380 px below 1500 px), collapsible to the rail with ⟩ (remembered per view, `exgs.dock.<view>`). In the rail (Flight) the tabs are vertical labels; a click opens the dock as an overlay over the groups (✕ or Escape closes it). A tab shows a marker dot: amber while the core reports RC master, red with "Pocket also active", red/amber for a transmit power problem. **Dock panels are mounted once and survive a view change**, so switching Bench ↔ Flight does not destroy the radio widget and does not release a held RC lease (before the redesign a preset change destroyed the panel and released the lease; see the report: user decision). In replay, and without a connection, the input tabs (radio, requests, commands) show the line "replay: inputs locked: nothing can be sent from here" above the panel; the panels are now shown disabled in replay (each button disabled with its reason) instead of being hidden. Their guards are unchanged (hold to confirm, danger modal, locks, rate limits, the core's own refusal).

## Layout file

`config/layout.default.json` (served at `/config/layout.json`, path set by `[gui] layout`), validated by `web/app/layout.js` (`normalizeLayout`, unit tested):

```json
{ "default": "bench",
  "dock": { "title": "Operations", "tabs": [ { "panel": "radio", "label": "Radio" } ] },
  "views": { "bench": {
      "label": "Bench", "hint": "tooltip text",
      "columns": "minmax(250px, 1fr) minmax(320px, 1.55fr) minmax(220px, 0.85fr)",
      "dock": "open",
      "groups": [ { "id": "vehicle", "title": "Vehicle",
                    "panels": [ { "panel": "attitude", "collapsed": false, "options": { "compact": true } } ] } ] } } }
```

- `columns` is a CSS `grid-template-columns` for the groups (one column per group, in order). `dock` is `open` or `rail`.
- `options` are passed to the panel as `ctx.options`: `compact` (attitude, position, actuators, guidance, mission: fewer rows), `height` for `view3d` (`large`, `fill`).
- A panel is either a dock tab or in the groups, never both; unknown panels, duplicates and empty groups are reported in the browser console and dropped; with no valid view the fallback is one group with every panel. A remembered view that no longer exists (for example `phone`) falls back to the default.
- Collapsed state per view: `exgs.collapsed.<view>`. All storage access is wrapped in try/catch.

## Visual design

Tokens in `style.css` (`:root[data-theme=dark|light]`): surfaces, lines, text, the meaning colours (green ok, amber warn, red fault/danger, blue info, violet simulation) and the banner colours; graphs will use the eight validated categorical series colours `--s1..--s8`. Type scale 11/12/13/15 px, tiles 21 px (28 px in Flight), hero 26 px (34 px in Flight); numbers in a monospace font with tabular figures. Number formats: angles 1 decimal in degrees, rates deg/s 1 decimal, positions and velocities 2 decimals (m, m/s), σ 2 decimals; units are always shown (tiles next to the value, tables in a unit column). Stale values are dimmed and struck through with their age (tiles hide the age, the panel badge shows it), missing values `—`, not supplied `n/s`.

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

## Panels

| Id | Group | Consumes | Notes |
|---|---|---|---|
| `attitude` | Vehicle | `Attitude` | Roll, pitch, yaw, tilt tiles (ZYX of q, display only), body rates (deg/s); full: quaternion, accuracies. |
| `position` | Vehicle | `Position`, `Gps` | Altitude, vertical and horizontal speed tiles; N/W/U table of position and velocity; full: accuracies, GPS. |
| `actuators` | Vehicle | `Actuators`, `FlapState`, `TvcState`, `VehicleState` | Thrust and TVC tiles (`Actuators`, the vehicle's commanded output), flap table command / angle (/ FlapState command if supplied); TvcState section only where a link carries it. |
| `sensors` | Vehicle | `VehicleStatus`, `Estimator`, `Attitude`, `Actuators`, `VehicleState` | Sensor states decoded from `VehicleStatus.sensor_state` (chips), estimator σ (attitude about N/W/U in deg, velocity, position), attitude accuracies, ground distance. |
| `failures` | Vehicle | `VehicleStatus` | Each failure bit and failsafe as reported, with "last set N ago" from this view (keeps short failures visible). |
| `view3d` | Guidance | `Attitude`, `Position`, `MissionState`, `FlapState`, `VehicleState` | See "3D view" (look and flap fix unchanged). |
| `guidance` | Guidance | `MissionState`, `Position` | Distance, altitude error and speed error tiles; table target / current / error (target − current, display only). No attitude target exists in the telemetry. |
| `mission` | Mission | `MissionState` | Phase (hero), mission time T±, mission mode, current target point, index, phase changes seen in this view. Upcoming points are not in the telemetry. |
| `rcsticks` | Mission (Flight) | `RcState`, `rc` push | Compact RC: the radio banner text and the two sticks (display only), "Open radio". |
| `radio` | dock | `RcState`, `rc` push | Virtual radio, see below; now also the two sticks. Safety-relevant. |
| `requests` | dock | `requests` push, `status` | See "Requests panel". Safety-relevant. Disabled (not hidden) in replay. |
| `telecommands` | dock | `telecommands` push, `status`, `VehicleStatus` | TEMPORARY, see [telecommands.md](telecommands.md). Safety-relevant. Disabled (not hidden) in replay. |
| `link` | dock | `LinkStats`, `RadioLinkStats`, `LinkConfig`, `TxModuleStats`, `RxModuleStats`, `status` | Sources, the Router's radio link, transmit power block. |
| `recording` | dock | `status`, `logs.list` | Start with a label, notes, log list with Open (switches to REPLAY). Quick start/stop is in the header. |
| `rawsensors`, `console`, `inspector` | dock | | Unchanged. |
| `transport` | banner | `status` | Replay transport, mounted inside the data-source banner, visible in REPLAY only. |

## 3D view

Panel `view3d` (head of the Guidance group in Bench and Flight). It consumes `Attitude`, `Position` (ship pose, trail), `MissionState` (setpoint), `FlapState` and `VehicleState` (`tvc_thrust`). Everything comes from the store, so it works the same live and in replay.

**Frames** (`view3d/frames.js`, tested in `web/test/frames.test.js`):

- World: NWU (X North, Y West, Z Up). Three.js is Y-up. All vehicle content sits under one `worldRoot` group with `rotation.x = -90 deg`, which maps NWU `(x, y, z)` to Three.js `(x, z, -y)`: North is +X, West is -Z (East +Z), Up is +Y. Inside `worldRoot` positions are NWU metres and the attitude is the vehicle quaternion.
- Quaternion: the vehicle `q = [w, x, y, z]` (body to world) becomes `quaternion.set(x, y, z, w)` after normalising. A norm that deviates by more than 1 % shows "invalid attitude" (ship drawn as a ghost).
- Body frame: right-handed, +Z the long axis with the nose toward +Z, the belly faces -X (South with the identity attitude), the back +X. The flaps TL and BL are on the +Y side, TR and BR on -Y. Flap angle 0 is fully extended along +-Y, increasing angle folds the flap toward +X (the back), 90 deg is folded flat. Source: workspace `docs/system-architecture.md`, "Vehicle geometry and body frame". The ship carries a dark strip on the belly as an orientation cue.

**Scene.** Ground grid at z = 0 (1 m cells, 10 m major lines, +-50 m), axes gizmo at the origin with N, W and Up labels, a 5 m compass ring with N/W/S/E, the ship, the trail (last `trail_window_s`, default 60 s, fading with age), the setpoint (ghost sphere with crosshair at `MissionState.sp_pos`, a dashed line from the ship, an arrow for `sp_vel` of 1 m per m/s), and a thrust arrow along `VehicleState.tvc_thrust` (body frame) ending at the tail, scaled by `thrust_arrow.m_per_n` up to `max_m`. Optional body axes (checkbox). Cameras (OrbitControls): Follow (orbits the ship), Observer (orbits the origin), Top (North up, zoom only).

**Data rules.** The ship needs both attitude and position, and is not drawn without them ("no attitude and position data"). If either is stale it is drawn semi-transparent with a "stale <age>" label, never extrapolated. With no `FlapState` in the view all flaps show `default_flap_angle_deg` (45 deg) and the overlay and the actuators panel say "flap data: none (default 45 deg)". Stale flap data keeps the last angles with a "flaps stale" label. The flaps jump to the reported angle, no smoothing. The trail is loaded with `get_history` on connect, on a view change and on every snapshot (so after a replay seek), then extended with live `Position` packets from the source that is currently preferred; a sample older than the last one resets it.

**Model config** (`config/model.<name>.json`, set by `[gui] model`, served at `/config/model.json`, files in `models/` at `/models/`). The placeholder is a procedural ship with the real dimensions (body 13 cm diameter, 74 cm high, origin at the centre of mass 37 cm from the bottom, parabolic nose of 8 cm; top flaps 7 x 14 cm with the bottom edge 52 cm from the bottom, bottom flaps 8 x 21 cm starting 1 cm above the bottom, both with an 8 cm chamfer, on the top (nose side) of every flap).

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
- Placeholder flap plate: hinge on the model's Z axis, the plate extends toward +X, `chamfer_m` is the length of a 45 deg cut at the outer corner named by the orientation parameter `chamfer_corner`: `out_top` (toward the nose, +Z) or `out_bottom` (toward the tail). **All four flaps use `out_top`** (user decision 2026-11-20: the bottom flaps were drawn upside down with the chamfer at the bottom); a flap that is modelled the other way up is fixed by this one parameter in its config entry, never by rotating it. The architecture note gives only "8 cm chamfer"; the 45 deg cut is an assumption of the placeholder and, like the corner, changes by config only. The pivot is at the flap's vertical centre on the body surface at +-Y (z = +0.22 m top, -0.255 m bottom; the bottom flaps span z = -0.36 to -0.15 m by the stated 21 cm height).

**Swapping in a real glTF model** (config change only, no code):

1. Put the file in `models/` (for example `models/starship.glb`) and copy `config/model.placeholder.json` to `config/model.starship.json`.
2. Body: `"source": { "gltf": "models/starship.glb", "node": "hull" }` and `model_to_body` so that the model's nose points to body +Z, the belly to -X and the origin sits at the centre of mass (glTF Y-up long axis: `rotation_deg_xyz: [90, 0, 0]` plus a translation if the file's origin is elsewhere).
3. Flaps: either `{ "gltf": ..., "node": "flap_tl" }` per flap or separate files. Set `pivot_body_m` to the hinge in the body frame, `axis_body` and `sign` so that increasing angle folds toward +X, and `model_angle_deg` to the angle the flap has in the file (0 if modelled extended). Check with the checkbox "body axes" and by watching the flaps move.
4. Set `[gui] model = "config/model.starship.json"` in the core config and reload the page. A config that fails validation, or a model that fails to load, shows "3D view unavailable: ..." in the panel, and the rest of the GUI keeps working. Run `.venv/bin/pytest tests/integration/test_view3d.py` to re-check the loader with `models/test-box.gltf`.

**Debug hook** for end-to-end tests: `window.exgs.view3d.debug()` returns the ship pose and visibility, nose, belly and origin in Three.js coordinates, per-flap angle and outer-edge position in the body frame, flap source (`data`, `default`, `stale`), setpoint and thrust visibility, trail point count, overlay text and camera mode. `window.exgs.view3d.scene3d` gives the Scene3D object.

## Virtual radio

Panel `radio` (`web/app/radio/`; first tab of the Operations dock). **Safety-relevant**: it is the only GUI path that makes the core send RC state. Rules, all enforced in `radio_logic.js` / `radio_widget.js` and covered by the tests:

- **Always the core's state.** Every control, the µs readout and the banner come from the `rc` push messages (the state the core is sending, labelled "sent"; the vehicle-applied state is "not reported"). A click sends a request (`rc.set`) and changes nothing locally; the control moves when the core reports it. The one exception is the slider thumb while it is being dragged: it follows the pointer and snaps to the core's report afterwards.
- **Config driven.** The controls, their kinds, positions on the handset (`svg = {x, y}` in the 200 x 140 viewBox of `pocket.svg`), labels and `meaning` tooltips come from `config.get radio`. The widget does not know what a control means. The handset drawing is decorative; the controls are HTML elements placed over it.
- **Banners** (from `rc.mode`, `holder_is_you`, connection and view): a state indicator chip `MASTER` (amber) / `SLAVE` (blue) / `IDLE`, and the banner: amber `MASTER · sending to <sources> · <rate> Hz`, grey `NOT SENDING`, blue `SLAVE · showing the Pocket · read-only` (with the age of the handset packet), `MASTER (<holder>)` for another client's lease, a warning when a lease is held but no RC-capable source is connected, red `DISCONNECTED`, and violet `REPLAY`. While master and `pocket_active`, a separate red warning banner: "Pocket also active: the radio rejects ground-station controls" (display only; it does not change what is sent). The button is "Switch to master" (`rc.acquire`) and, for the holder, "Release (back to slave)". The state is always the one the core reports (`rc.mode`)
- **Operable only while this client holds the lease and the core reports `master`.** Otherwise all controls are read-only: idle, slave (handset positions mapped to the nearest configured position, sliders continuous), someone else master, disconnected, replay. "Switch to master" is enabled only when the core said this client may control (`welcome.can_control`), the view is live and nobody holds the lease; it is **also available while the Pocket is active** (slave): master is the user's choice; a denial (`no control source is connected`, another holder) is shown as the core's message.
- **Heartbeat** `rc.heartbeat` every 300 ms while this client holds the lease. Closing the tab or losing the connection ends the lease at once (WebSocket close), a stalled page within 1 s. After a reconnect the client does **not** take control again by itself. A layout view change does not destroy the dock panel, so it does not release a held lease; reloading or closing the page does (WebSocket close).
- **Controls.** `toggle2`: click flips. `switch3`: click the top, middle or bottom zone, or arrow keys / space. `momentary` (SE): `SE=1` on pointerdown and every 100 ms while held, `SE=0` on pointerup, pointercancel, lost pointer capture, window blur and when the page is hidden (the core also releases it 300 ms after the last refresh). `slider` (S1): at most 20 updates per second, the newest value always follows.
- **Padlock.** A control with `lock` in the radio config (SD) is shown as a padlock while the core reports it locked: hold it for 1 s (progress bar) to send `rc.unlock`; releasing early cancels. While unlocked the switch is operable and shows `unlocked · <seconds>` counted down from the core's `remaining_s`, with a Relock button (`rc.lock`). When the core relocks it (timeout, lease end) the padlock returns on the next push.
- **Replay.** In the replay view the widget shows the recorded `RcState` (freshest of any source) read-only under the REPLAY banner. Switching to replay while this client holds control asks "Release control and switch to replay?" (in-page dialog, `Release and switch` / `Stay live`); the release is sent before the view changes, and if it fails the view stays live. As a second line of defence a client that finds itself holding the lease in the replay view releases it.
- **Sticks (display only).** Under the handset two gimbal boxes show the stick channels with a dot, the axis values (−1..1, throttle 0..100 %) and the pulse widths: in slave the Pocket's sticks as received (stale after 0.5 s, greyed), in master what the core sends (the channel defaults: sticks neutral, throttle low; there is no stick sending), in replay the recorded `RcState`. The channel assignment comes from the radio config's `[sticks]` table (`radio/sticks.js`, unit tested in `web/test/sticks.test.js`; docs/radio.md). The same widget is in the Flight view's `rcsticks` panel.
- **Debug hook** `window.exgs.radio.debug()`: banner text and tone, button states, and per control the displayed position, enabled, lock state, padlock visibility and the µs text.

## Adding a panel

1. Create `web/app/panels/<name>.js` and call `registerPanel` (`id` unique and matching `[a-z_]+`, `title`, `consumes`, optional `mode: "all"`, optional `views: ["replay"]`, optional `inputs: true` for a panel that can make the core send something (it gets the dock lock line), `create(el, ctx)`).
2. Add `import "./<name>.js";` to `web/app/panels/index.js`.
3. Put it into a group of a view (or into `dock.tabs`) in `config/layout.default.json`. Read `ctx.options` for per-view variants (`compact`).
4. Use the helpers in `ui.js`: `Bindings.tile()` for big numbers, `Bindings.vecTable()` for N/W/U tables, `Bindings.row()` for details.

### Mission status bar

`web/app/status/status_logic.js` (pure, `web/test/status_logic.test.js`) and `status_bar.js`; mounted in `#statusbar` under the sticky header, so it is visible in every view and in live and replay (REPLAY shows the replayed status with a "REPLAY" prefix and an amber edge). **Only vehicle-reported values**, from the freshest `VehicleStatus` (the same packet the schema calls VEHICLE_STATUS): nothing is computed from what the ground station sent, nothing is assumed, and a flag the wire profile did not supply is shown as `n/s`, never as false.

| Cell | Source field | Display |
|---|---|---|
| Sensors | `simulation` | large coloured badge: **SIMULATION** (purple) or **REAL SENSORS** (green); `SIM? (not supplied)` if the field is absent |
| Mode | `mode` | name as reported, tone by mode |
| Mission | `mission_mode` / `mission_phase` | names |
| Armed | `armed` | `ARMED` (amber) or `disarmed` |
| Motors | `motors_enabled` | large: **MOTORS ENABLED** (red, pulsing) or `motors off` |
| Failsafe | `failsafe` | `FAILSAFE` (red) or `no failsafe` |
| Failures | `failure_bits` | raw number and hex always, plus decoded names (bit 0 sensor, 1 attitude, 2 position, 3 position out of bounds, 4 attitude out of bounds, 5 radio connection loss, from the flight software's field mapping; unknown bits as `bit n`) |
| Ground station | `gs_present` | `GS present` / `GS absent` |
| Control source | `ctrl_source` | name as reported |
| RC age | `rc_age` | seconds, or `none (NaN)` |
| Last request | `last_req_id`, `last_req_status` | `id n: <status name>` (`none (id 0)`), red for a status other than DONE and IN_PROGRESS |
| Sensor state (raw) | `sensor_state` | raw hex; the tooltip decodes the six 2-bit fields (Init, Calib, Ready, Failure) |

Below the cells: one **link health** chip per source (connection state from `status`, packets per second from its `LinkStats`; a connected source that has been silent shows `silent <age>` in the stale colour). A red banner appears above the bar for a fresh `NetworkStatus.incompatible` (schema hash or network version mismatch, with the detail text) and disappears by itself when it clears or when that source goes quiet. **Stale data** (the `VehicleStatus` older than its stale limit) greys the whole line out and the label reads `STALE <age>`; with no `VehicleStatus` in the view it reads `no VehicleStatus received`.

### Transmit power (link panel)

Block in the `link` panel (`web/app/link/link_logic.js`, `web/test/link_logic.test.js`). Shows **Router power commanded** (`LinkConfig.tx_power_dbm`, the Router's `LINK_CONFIG` to the ship radio node), **Router power applied** and **Ship radio node power applied** (`TxModuleStats.tx_power` and `RxModuleStats.tx_power`, from each module's `LINK_STATS`). The block is outlined and a warning appears when **commanded and ship-applied power differ**, and a loud red warning when **any value is above 10 dBm** (the legal limit of 10 mW), naming which end. The ground station never sets the power. The Router drops `LINK_CONFIG` from USB, so on a normal bench the commanded value may never arrive: the block then says "LINK_CONFIG not received" and compares nothing.

### Raw and calibrated sensor panel

`web/app/panels/rawsensors.js`, `web/app/sensors/rawsensors_logic.js` (`web/test/rawsensors_logic.test.js`). Panel `rawsensors`, "Raw and calibrated sensors". Consumes `ImuRaw` and `Calibration` (the vehicle's `CALIBRATION` packet, port 0x93, USB only, every 2 s). Needs the vehicle's USB debug set: with no connected `vehicle_usb` source in the live view the panel says so.

- **Raw (uncalibrated):** `ImuRaw` gyro (rad/s), accelerometer (m/s²), magnetometer (shown in µT, the packet is in T), pressure (Pa) and temperature (K), live, with the update age (stale values are struck through with their age, as everywhere), the magnitude of accel and mag, and a sparkline of the last 10 s (magnitude for vectors). The column header says **raw (uncalibrated)**.
- **Calibrated (computed here with the ship's formulas):** the formulas of flight-software `docs/project/hardware-and-drivers.md` section 8a, computed in the browser from the latest `ImuRaw` and `Calibration`, SI units: `gyro_cal = gyro_matrix * (gyro - gyro_offset)`, `accel_cal = accel_matrix * (accel - accel_offset)`, `mag_cal = mag_matrix * (mag - mag_offset)`, `omega_est = gyro_cal - gyro_bias_est`; matrices are row-major, `M[r][c] = matrix[3r + c]`. Calibrated values and magnitudes (|accel cal| should be about 9.81 m/s² when the vehicle is still) are shown next to the raw ones; the magnetometer in µT (the vehicle normalises |mag cal| to about 5 µT, which is not the physical field strength). A component that is NaN on the wire gives "n/a", never a guessed number; a `raw_frame` other than 0 (SENSOR) is not computed. **Display only:** nothing computed here is sent, stored, used for a decision or fed back anywhere.
- **Calibration state:** a line shows the age of the `CALIBRATION` packet (marked STALE when it is older than its limit, and the calibrated column is then dimmed) and the source flags per sensor, decoded: calib EEPROM, transform EEPROM, offset override (hard-coded), RAM only (not saved), memory cleared (defaults), or identity defaults when no bit is set. Until a `CALIBRATION` packet has been received the calibrated column shows `waiting for CALIBRATION packet` (and the state line `none received`).
- **Tests:** the formula is checked against the `imu_raw` and `calibration` cases of the Packets test vectors (`web/test/data/calibration_vectors.json` is a copy, a Python test fails when it drifts from `lib/ExVectrPackets/test/vectors/packets.json`).

`Estimator.att_std` changed meaning on the vehicle (packet version 2 here): it is the 1 sigma of small rotations about the **WORLD axes [North, West, Up]**, no longer a per-quaternion-axis value. The packet labels are `north, west, up` and the inspector shows them that way.

### Commands (temporary) panel

TEMPORARY, safety-relevant: see [telecommands.md](telecommands.md) (hold 1 s, one frame, no ACK, the simulation state shown is only what the vehicle reports). Logic `web/app/telecommand/telecommand_logic.js`, panel `telecommand_panel.js`.

### Requests panel

Panel `requests` (`web/app/requests/requests_logic.js`, `requests_panel.js`; a dock tab, `inputs: true`: shown disabled with the lock line in the replay view). **Safety-relevant**: with the radio widget it is the only GUI path that makes the core send anything ([requests.md](requests.md)). Rules, enforced in the logic module and covered by `web/test/requests_logic.test.js` and `tests/integration/test_requests_widget.py`:

- **From the core's whitelist.** Rows come from `request.list` (group, label, target, confirm level, `route`); the GUI knows nothing about what an action means and has no other way to name an action.
- **Hold to send.** Each row's button needs a continuous hold of **1 s** (progress bar, same pattern as the radio padlock); a click, a short hold, a blur or a pointer leaving sends nothing. The panel never sends by itself.
- **`danger` actions** (`LINK_UPLOAD_MODE`, `LINK_REBOOT`) additionally open a modal after the hold, showing the row's own `warning` (for `LINK_REBOOT` on the ship radio node: a reboot in flight interrupts RC for seconds), else "This acts on a link module (the Router or the ship radio node): its radio link stops or restarts. The module always obeys, even in flight." Its send button is **disabled for 2 s** (countdown in the label); Cancel and Escape close it without sending; only the button sends `request.send` with `understand_link_stops: true`.
- **State from the core only:** each row shows the latest `requests` push for its (action, target): `sending, attempt n/5 via <source>`, `ACK <status as reported> (detail d)`, or `TIMEOUT`. `DONE` and `IN_PROGRESS` are green, any other status (and an unknown one) red, never an assumed success. The button is disabled while that request is in flight.
- **Disabled** in the replay view, without a connection, on a view-only host (`welcome.can_control` false), while in flight, and when no source can reach the target (`status.request_routes`); the core still refuses everything itself and its error text is shown under the list.

`ctx`: `options` (the layout entry's options), `store`, `schema`, `api` (`api.request(op, payload)` returns a promise of `data` or rejects with `{code, message}`), `fmt`, `h`, `Bindings`, `view()` (`"live"` or `"replay"`), `status()` (last `status` message), `rc()` (last `rc` push, `null` while disconnected), `now()` (view time), `switchView(v)` (asynchronous: view guards run first), `addViewGuard(async (nextView) => bool)` (returns a remover; a guard can veto a view switch, the radio uses it for the release prompt), `setTitle(text)`.

The object returned by `create` may have: `onPacket(pkt)` (per received packet of a consumed type in the current view; also called for the items of a snapshot), `onReset(view)` (snapshot replaced the store), `onTick(now)` (10 Hz), `onViewChange(view)`, `onStatus(status)`, `onEvent(evt)`, `onRc(rc)` (every `rc` push, `null` on disconnect), `onRequests(msg)` (every `requests` push), `onTelecommands(msg)` (every `telecommands` push, TEMPORARY), `onReady()` (after every connect), `freshness()` returning `{state, age}` for the header badge, `alert()` returning `""`, `"warn"` or `"bad"` for the dock tab marker, `destroy()`. Debug hooks of the shell: `window.exgs.layout` (`view()`, `set(id)`), `window.exgs.dock` (`open(tab)`, `mode()`, `tab()`), `window.exgs.source()` (banner state).

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
.venv/bin/python tools/browser_check.py http://127.0.0.1:8765/ --wait 3 --width 1366 --height 768 --shot gui.png   # screenshot a running core
```

`browser_check.py` drives Chromium over the DevTools protocol with no extra dependencies (aiohttp only). The browser tests are skipped when no Chromium or Chrome is installed.

## Tuning view: remaining work

Not built in this branch (scope cut by the main session 2026-11-20). Done and tested so far:

- Core: `get_history` with `buffer: "plot"` (docs/api.md): live from `CoreApp.plot_history` (`PLOT_TYPES` in `core/history.py`, up to 200 Hz, the last 130 s, so a page reload keeps two minutes), in replay read from the open log (at most 600 s per request). The FakeSource now also emits `VehicleStatus` (option `simulation`), `Actuators`, `ImuRaw`, `Estimator`, `TaskTiming` and `Calibration`.
- Pure modules with unit tests (`web/test/plot.test.js`): `web/app/plot/series.js` (ring buffer, min/max decimation per pixel column: 100 Hz for 2 min on 600 px is at most 1200 points, measured well below 20 ms per graph; gaps; nice ticks; window; CSV) and `web/app/plot/catalogue.js` (signal catalogue of everything the vehicle sends, derived target − current and calibrated signals, 16 ready-made graphs with one unit each, feeder, history conversion).

Left to do:

1. A canvas plot component (`web/app/plot/plot.js`: axes, decimated lines, shared cursor, drag to pan, wheel to zoom, PNG export). A draft exists outside the repo and was not reviewed or browser-tested.
2. The `tuning` panel (graph workspace): toolbar (window 10 s / 30 s / 2 min / all, pause and scroll-back, add / reset graphs, CSV of all graphs), graph cards (signal picker, auto / manual y, legend with the value at the cursor, CSV, PNG), history load on connect / view change / seek, subscription mode `all`, replay cursor = replay position.
3. The `tuning` view in `config/layout.default.json` (one group, dock rail), browser tests (live and replay, performance with 100 Hz), screenshots.
4. Signals available for graphs (from the vehicle's telemetry): attitude (roll/pitch/yaw/tilt from q), body rates (`Attitude.omega`), position and velocity vs `MissionState.sp_pos` / `sp_vel` and their errors, `Actuators` flaps / TVC / thrust, `FlapState` angles, `TvcState`, raw / calibrated / bias-corrected gyro, raw / calibrated accelerometer and magnetometer (`ImuRaw` + `Calibration`), pressure, `Estimator` σ (attitude N/W/U, velocity, position), `TaskTiming` avg and max.

**Missing telemetry for tuning** (to be requested from the flight software; nothing is invented here): attitude setpoint (quaternion or angles) and attitude error; angular-rate setpoint; controller terms per axis (P, I, D contributions, integrator states, output saturation flags); commanded vs achieved thrust and torque (it is not documented whether `Actuators` is command or feedback); flap commands (`FlapState.cmd` is not sent by the vehicle); acceleration setpoint; estimator innovations / residuals (GNSS, baro, magnetometer) and covariance diagonals beyond att/vel/pos σ (gyro and accel bias σ); estimated linear acceleration; barometric altitude; task names and valid `max` values for `TaskTiming` (all NaN on the bench), loop period and jitter; battery voltage and current; motor RPM / ESC feedback; the mission plan (list of upcoming points, time to the next phase).

## Known gaps

- Replay: the controls are wired to `replay.*` and `status.replay`, but were exercised only against the "not available" answer because the Replayer (T4) is not merged in this branch. Scrubber markers for `GsEvent`, `Note` and mode changes (design 5.7) need index data the API does not expose yet.
- 3D view: the placeholder's chamfer corner and 45 deg cut are assumptions (see above). The glTF path is tested with a generated box file only, not with a real ship model. Nothing is drawn for the vane angles.
