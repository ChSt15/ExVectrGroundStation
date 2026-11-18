# GUI layout, panels, 3D view (design section 5)

Implemented in task T7: the shell and the data panels. The 3D view (T8) and the virtual radio (T9) are not built yet and have no placeholders. Spec: [ground-station-design.md](ground-station-design.md) section 5.

Plain ES modules in `web/`, served by the core (`GET /`, `/static/...`). No build step, no CDN, works offline (the import map in `index.html` is empty until Three.js is vendored in T8). Checked in a real headless Chromium by `tests/integration/test_gui.py`.

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
| `console` | `ConsoleText` (mode `all`), `event` | Vehicle text and GS events, filter, pause, clear, 500 lines. |
| `inspector` | `*` | All (type, source) pairs with age and a field table from the schema. |

The header shows LIVE/REPLAY, per-source rate (`LinkStats.rx_frames_s`), radio link quality, REC state and duration, connection state and the preset selector.

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
- Not built: 3D view (T8), virtual radio (T9).
