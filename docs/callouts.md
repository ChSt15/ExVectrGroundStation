# Audio callouts and notifications

Branch `feature/callouts` (user decisions 2026-11-20). **Display only**: the GUI speaks and shows what the vehicle reports. Nothing is sent to the vehicle, no decision is made, nothing is inferred from what the ground station sent. Code: `web/app/callouts/` (`engine.js` rules, `queue.js` priority queue and suppression, `speech.js` Web Speech and WebAudio adapter, `notify.js` toasts and history, `settings.js` defaults, merge and volume maths, `controller.js` wiring), the settings page `web/app/panels/callouts.js` (Operations dock tab **Callouts**), defaults `config/callouts.default.json`.

> **Limitation, read first.** Callouts come from the *displayed* data. They can be late or missing when the link drops or the browser, the audio device or the voice fails. **Never rely on them as the only warning.** A silent GUI does not mean the vehicle is fine.

## Rules

The engine reads the freshest `VehicleStatus`, `MissionState` and `Position` of the current view each 100 ms tick. Events are `{id, category, text, severity, priority}`; ordering priority: failures (0) > warnings (1) > state (2) > mission (3) > altitude (4) > countdown (5).

| Category | Says | Details |
|---|---|---|
| countdown | `5` `4` `3` `2` `1`, `Mission start` | `MissionState.t_mission` crossing -5 s .. -1 s once each, `Mission start` at 0. If one step crosses several numbers only the last is spoken. A jump of more than 2 s or a reset backwards re-baselines without speaking. |
| countdown (mission begin) | `Mission beginning` | `mission_mode` leaves `Idle` for `Initialisation`, `Startup` (or `Running`): the user started the mission. First observation (connect) is silent. Priority mission, category switch `countdown`. Clip `mission-beginning`. |
| phase | phase names | Edge of `mission_phase` (MissionState, else VehicleStatus), **only while `mission_mode` is `Running`** (and `Finished`, for `Landed`). The vehicle reports `Hover` from Ready on and through Initialisation / Startup (countdown), so nothing is said for it before `Running`; on entering `Running` the current phase (normally `Hover`) is announced once. The first observation while already `Running` is silent, `Idle` is not announced. Names are configurable (`phaseNames`): Hover = `Hover`, Ascent = `Ascent`, Descent = `Descent` (was `Belly flop`; a saved `Belly flop` is migrated), Deceleration = `Deceleration`, RTH = `Return to home`, Landing = `Landing`, Completed = `Landed`. `Deceleration` (the upright) is the phase change Descent to Deceleration, spoken once by this row only. `Apogee` is detected by the engine (below). |
| phase (apogee) | `Apogee` | Engine-detected (the vehicle has no apogee phase): while `mission_phase` is `Ascent`, the mode `Running` and `Position.pos[2]` above 5 m, the mean of `Position.vel[2]` over 0.3 s goes from positive to <= 0. Once per flight, re-armed by the next `Mission beginning`. Category switch `phase`. Clip `apogee`. |
| altitude | `50` (the number only, no word "Altitude") | `Position.pos[2]` (NWU up, relative to the reset origin) crossing the list (default 25, 50, 100, 150, 200 m), rising and falling, once per crossing. Hysteresis (default 3 m): a level is said at threshold + 1.5 m and re-arms at threshold - 1.5 m, so noise does not repeat. Several levels in one step: only the last reached. Active only while the reported `mission_mode` is `Running` (not on the ground), the first fresh sample sets the baseline silently. |
| failure | `Sensor failure`, `Attitude failure`, `Position failure`, `Position out of bounds`, `Attitude out of bounds`, `Control source lost`, `GNSS lost`, `Magnetometer lost`, `Barometer failure`, `Gyro failure`, `Accelerometer failure`, `Attitude estimate failure`, `Position estimate failure`, `Motors armed watchdog failure`, `GS failsafe` (bit 14), `Failure bit n`, `Failsafe`, `Failure cleared` | One callout per set bit of `VehicleStatus.failure_bits` (names as `status_logic.js`), `Failsafe` when the flag rises, `Failure cleared` when the last failure ends or the failsafe flag falls. Announced once while it persists (the `failure.repeats` setting, default 0, can repeat it every `repeat_s` seconds). Existing failures at connect are announced. Failure toasts stay until cleared or dismissed. |
| link | `Telemetry lost`, `Telemetry restored`, `Router link lost`, `Router link restored` | Live view only. Telemetry lost: the newest `VehicleStatus` is older than 3 s (also when the core connection is down: the view clock keeps running). Router: the source of kind `link_module` leaves `connected` (a user-disabled or absent Router says nothing). |
| state | `Motors armed`, `Motors disarmed` (the vehicle ARMED flag), `Go refused`, `Simulation`, `Real sensors` | Edges of the vehicle flags. `Simulation` / `Real sensors` at the first status after connect and on every change. Removed 2026-11-23 (owner): `Motors enabled`, `Zeroing`, `Zero complete`, `Toggle SD off, then on` (the INTERLOCK_NEED_TOGGLE bit is still shown in the status bar). |
| deviation | `Position deviation` | `Position.pos` against `MissionState.sp_pos` (3-D distance) above `deviation.limit_m` (5 m) for `hold_s` (2 s), once per excursion, re-armed below limit minus `hysteresis_m` (1 m). **Disabled by default**: `sp_pos` is the guidance setpoint (it comes from `AIR_MISSION`/`MISSION`, present live with the 53a7020 pin), which leads the vehicle by design (it is the current setpoint, not the final target) and has not been checked against a real flight, so a deviation alarm could be false or late. Enable it per category once checked. |
| countdown (freefall) | `5` `4` `3` `2` `1` | Countdown to the **start of the uprighting (flip)**, replacing the old distance callouts (`N meters till upright`, 25/15/10/5 m). Time to go = (`Position.pos[2]` minus the vehicle-reported `MissionState.flip_start_altitude`) / descent speed. Descent speed = minus the mean of `Position.vel[2]` (NWU up) over the last 0.5 s (smoothing against jitter). The vehicle recomputes the flip start with speed, so the latest telemetry is used each tick. Active while the reported `mission_phase` is `Descent` (bellyflop and freefall) and the mode is `Running`. Levels `freefall.countdown_s` (seconds, default 1, 2, 3, 4, 5, editable in the settings page): each number once per descent when the time to go falls to or below it; levels already passed at the first sample (or armed within 0.2 s of it) are skipped silently, several in one step: only the lowest. Silent (nothing armed or consumed) while the flip start is NaN or missing (not in freefall, older sender), when `vel` is missing, or when the descent speed is below 0.5 m/s. `Begin` once, when the distance reaches 0 (then it replaces the number of that step) or the phase changes `Descent` to `Deceleration`, whichever comes first. A new `Descent` re-arms. Normal altitude callouts stay muted during `Descent`. Same category switch and priority as altitude; numbers expire after 1.5 s. The old setting `freefall.levels` (metres) is ignored when loading a saved or file configuration (migration: the default countdown applies). No `Begin` callout any more (removed 2026-11-23): the upright is announced by the `Deceleration` phase callout. |
| altcheck | `Altitude mismatch` | `Position.pos[2]` against the GNSS altitude relative to the start or home (`gnss/gnss_logic.js`, `docs/gui.md` "GNSS panel and altitude cross-check") differing by more than `altcheck.limit_m` (5 m) for `hold_s` (1 s), once per excursion, re-armed below limit minus `hysteresis_m` (1 m). Not judged without fresh data, a 3-D fix and a reference. **Disabled by default** (optional audio; the GNSS panel shows the state regardless). No recorded clip yet: falls back to the browser voice or beeps until the clip is generated. |

**Stale data.** An entry older than 3 s (`stale_s`) never triggers mission, altitude or deviation callouts, and the baselines are reset, so the first fresh sample afterwards is silent. Failure and state callouts use VehicleStatus only while it is fresh.

**Replay.** Callouts are **off in replay** (setting *Speak and notify in replay*, default off; the test buttons still work). When on, they follow the recorded timeline at normal speed. A seek (replay time jumping back, or forward by more than 2 s, or a new snapshot) re-baselines silently and cancels pending speech: nothing is said for what was skipped.

**Queue and suppression.** One utterance at a time, best priority first. A failure (priority 0) cancels the speech in progress and drops every lower-priority pending item. Pending items expire (countdown after 1.5 s, others 6 s) so a late number is never spoken. Identical text inside a window is dropped, also from the notifications: 2 s normal, 8 s failures (repeats of a persisting failure are exempt).

## Voice styles

These styles apply to the **Browser voice** mode (and the fallback). The Web Speech API has rate, pitch, volume and voice selection only, no emotion and no SSML, so urgency is emulated:

| Style | Used for | rate | pitch | volume |
|---|---|---|---|---|
| normal | mission, altitude, countdown, ok messages | 1.0 | 1.0 | 0.9 |
| warning | deviation, telemetry/link, motors armed | 1.15 | 0.9 | 1.0 |
| failure | failures, failsafe | 1.3 | 0.7 | 1.0 |

The failure style is preceded by a short two-tone beep (WebAudio). All styles are editable in the settings. **Master volume** (0 to 100 %, default 80 %): the utterance volume is `min(1, style volume x master)` and the beep gain is `0.35 x master`; changes apply to the next callout, and *Test volume* speaks `Volume test` with the beep at the current setting.

## Recorded voice (default)

Browser speech needs a system voice, and some devices have none (the Steam Frame's Flatpak Chromium). So callouts are **pre-recorded clips** by default, played with WebAudio: they work offline, without any TTS voice, and sound the same everywhere. The **Voice** setting in the Callouts tab (`voiceMode`, saved with the other callout settings) has three choices:

- **Recorded (default):** the callout text is composed into clips (`web/app/callouts/clips.js`). A text that has no clip falls back to browser speech when it can speak (speechSynthesis with at least one voice), otherwise to one short beep.
- **Browser voice:** the Web Speech API as before (styles, voice selection); a beep when no voice exists.
- **Beeps only:** one short two-tone beep per callout (a failure beeps before its words in the other modes too).

Composition: a text that equals a clip's spoken text is one clip. Otherwise it is split into words: known phrases (longest match first), numbers and units. Numbers: `0..99` and `100..900` are single clips, `1234` is one, thousand, two hundred, thirty-four; `12.5` is twelve, point, five; `-3` starts with minus; `0.0` is zero, point, zero; beyond 99999 digit by digit. Units after a number: `m` meters, `m/s` meters per second, `s` seconds, `deg` or the degree sign degrees, `%` percent. `Altitude 50` is "altitude", "fifty"; `T minus 5` works too. Clips are chained on the AudioContext clock with a 60 ms gap (`CLIP_GAP_S`), not one `<audio>` element per clip. Priorities, interrupts (a failure cancels the clips in progress), queue, max age, suppression and the master and style volume (`min(1, style volume x master)`) are the same as for speech; rate and pitch of the styles apply to browser speech only.

The clips are `web/audio/en_GB-northern_english_male-medium/<clip-id>.ogg` (Opus, mono, about 450 KiB for the 149 clips, loaded and decoded once after audio is enabled), served as `/static/audio/...`, with `manifest.json` (clip id to file, spoken text, duration, model, length scale). Voice: Piper `en_GB-northern_english_male`, quality medium, length scale 1.1 (slower than the default 1.0, for clarity; `LENGTH_SCALE` in the generator).

### Licence

The voice is trained on the OpenSLR 83 dataset (crowdsourced UK and Ireland English) and published in rhasspy/piper-voices under **CC-BY-SA 4.0 International** (its MODEL_CARD; finetuned from the lessac medium voice). Generated audio may be redistributed with credit, an indication of changes and the same licence. That is why the clips are committed with `web/audio/en_GB-northern_english_male-medium/LICENSE-VOICE.txt` (credit, source, licence link, length scale). The licence covers the audio only, not the ground-station code. Anyone shipping the GUI audio must keep that file with the clips.

### Regenerating and adding a phrase

```
pip install -e '.[voice]'            # piper-tts, optional, not a runtime dependency; ffmpeg for Opus
python tools/generate_voice_clips.py  # downloads the model once to ~/.cache/exvectr-gs/piper/ (network), renders missing clips
python tools/generate_voice_clips.py --force   # re-render everything (or after changing --length-scale)
```

It trims leading and trailing silence, writes the clips, `manifest.json` and `LICENSE-VOICE.txt`, and skips clips whose file and text are unchanged; a changed length scale or format re-renders all. Without ffmpeg the clips stay WAV (larger). To add a phrase the engine can say: add `"clip-id": "Spoken text"` to `web/audio/phrases.json`, run the generator, commit the new clip and manifest. `tests/unit/test_voice_clips.py` reads the engine, settings, default config and controller sources and fails when a fixed text has no clip, and checks manifest and files against the list.

## Audio permission

Browsers need a user gesture before sound. The notification area shows an **Enable audio** button (and the settings page has the same), the header speaker icon shows muted / audio on / audio not enabled. The choice is remembered in `localStorage` (`exgs.callouts.audio`); after a reload the state is **audio blocked** until the first click or key press anywhere on the page, which activates it. The test buttons enable audio as well. With the default recorded voice no system voice is needed. In Browser voice mode with no `speechSynthesis` or no voices (Linux without speech-dispatcher) callouts beep, notifications still work and the settings page says `no speech voices available: install speech-dispatcher / a voice, text notifications still shown`.

## Notifications

Every callout is shown as a toast (also with audio off or blocked), top right below the header and status area and left of the Operations dock (the stack re-positions itself with the dock width): info/mission blue, warning amber, failure red, ok green. Normal ones disappear after 5 s, failure ones stay until the failure clears or the user dismisses them, at most 5 are visible. The settings page lists the last 100 with time. Master off (checkbox, or volume 0 for speech only) silences: master off means no speech and no notifications.

## Vehicle log notifications

Important vehicle messages must not be buried in the Console. The vehicle reports e.g. "calibration invalid, Arm refused" only as log text (`LOG_TEXT` -> `ConsoleText`, `"[node 1 WARNING] <text>"`), so the GUI watches those lines (`web/app/callouts/logwatch.js`, patterns in the data file `log_patterns.js`) and raises toasts. Display only: nothing is sent to the vehicle, and the Console keeps every line.

- **Always:** every WARNING and ERROR line (any node, other nodes prefixed `node N:`).
- **By pattern (vehicle node 1 only; Router/ship INFO lines are ignored):** categories `calibration` (pose N captured, all poses stored, Accel/Mag fit PASS/WARN/FAIL, VERIFY, Gyro result/FAILED, gyro bias run, applied, saved, EEPROM save FAILED), `refused` (Arm / MissionBegin refused with the reason, Calibration start/apply/save refused, ZeroEstimators refused, any other "refused" line), `abort` (countdown abort and disarm because of invalid calibration: critical, red, stays until dismissed, beeps by default), `mission` (Mission abort telecommand, estimator zeroing aborted). Periodic progress lines ("Accel: 2 poses captured (moving)") raise nothing.
- **Dedupe:** identical text within 10 s shows one toast with a count (`x3`); after 10 s it shows again.
- **Calibration chip** (header, amber): "Calibration invalid: accel", parsed from the vehicle's repeated warning (`WARNING calibration invalid, ... (repeats every 10 s): gyro: ok, accel: parameters out of range, mag: ok`) and from refusal/abort lines carrying the same reason. It clears when that warning has not repeated for 25 s or the vehicle reports "all three calibrations checked". Click opens the Console. The repeated warning toasts only when the problem appears or changes.
- **Console badge:** the Console dock tab shows the number of new WARNING/ERROR lines since it was last open (cleared while the tab is open and the dock visible).
- **Settings** (Callouts tab, "Vehicle log notifications"): master switch, a toast switch and a beep switch per category (defaults: all toasts on, beep only for the countdown abort; the beep is the existing alert beep, no new clips). Off in replay unless "Speak and notify in replay" is on; the chip and badge follow the same rule (chip) or always count (badge). Master off (above) silences these too.
- **Adding a pattern:** one line in `log_patterns.js` (`re`, `category`, `severity`, `title`), plus a test with the real log line in `web/test/log_patterns.test.js`.

## Configuration

`GET /config/callouts.json` serves `[gui] callouts` of the core config (default `config/callouts.default.json`), like the layout. The file may set any key of the built-in defaults (`settings.js`, unknown keys and bad values are dropped). The user's settings from the settings page are saved in full in `localStorage` (`exgs.callouts`) and override the file; **Reset to defaults** clears them. A later change of the defaults file therefore does not reach a browser that already saved settings until it resets.

## Tests

`web/test/log_patterns.test.js` and `logwatch.test.js` (pattern table against real log lines, dedupe, chip stale clear, unread), `web/test/callouts_*.test.js` (engine rules, seek and stale suppression, queue and suppression, speaker with a fake synth, clip composition and playback with a fake AudioContext (`callouts_clips.test.js`), settings merge, volume scaling), `tests/integration/test_callouts_gui.py` (headless browser with a fake `speechSynthesis` and `AudioContext` against the pty fake vehicle and a replay, including the 1366x768 position check of the toasts), `tests/integration/test_api.py` (`/config/callouts.json`). Real speech is not testable headless: listen once on the real browser and voice (docs/callouts.md checklist in the branch report).

## Renamed phrases (2026-11-23): motors armed / disarmed

The owner's naming: SD is "motors armed / disarmed", never "interlock open". The callout **texts** in `web/audio/phrases.json` and the engine changed (`Motors armed`, `Motors disarmed`, `Motors enabled`, `Motors armed watchdog failure`, new `GS failsafe`); the **clip ids are unchanged** (`interlock-open`, `interlock-closed`, `motors-enabled`, `interlock-watchdog-failure`) and the recorded clips were regenerated on 2026-11-23 (same ids, new words), see the last section. The Pocket-stream-loss warning is a toast only (the core's event with a toast key), no voice.


## Beeps and voice at the same time (2026-11-23)

Beeps never go through the speech queue. They are WebAudio oscillators on the same AudioContext as the clips, so a beep and a clip (or browser speech) play together: a failure's two-tone beep starts in the same call as its words (it no longer delays them by 0.3 s), vehicle-log beeps and the volume test beep directly, and a beeps-only callout releases the queue at once. A beep never cancels or delays a spoken callout and speech never delays a beep (`cancel()` stops clips and browser speech, not beeps). Queue rules for speech (priority, expiry, suppression) are unchanged. Beep volume stays `0.35 x master`.

**Why `N meters till upright` was only a beep:** the phrase `till upright` had no recorded clip (it was in `phrases.json` but never rendered), so the composition of `50 meters till upright` failed, and on a PC without a system voice (no speechSynthesis voices) the last fallback is the beep. The same hit every other phrase without a clip (`Begin`, `Motors armed`, ...). The phrase is removed with the distance callouts, and the pending clips (`Begin`, the renamed motors texts, the failure names, `Go refused`, ...) are now generated and committed (`tools/generate_voice_clips.py`); the generator and `tests/unit/test_voice_clips.py` (`PENDING_IDS` empty) keep every engine phrase covered. A text without a clip still uses browser speech when a voice exists and the beep only when nothing can speak.


## Final callout list (2026-11-23, owner trim)

| Callout | Spoken | Source |
|---|---|---|
| Ascent altitudes | the number only (25, 50, 100, 150, 200) | altitude crossings |
| Motors armed / disarmed | `Motors armed`, `Motors disarmed` | ARMED flag |
| Mission begin | `Mission beginning`, then `5` .. `1`, `Mission start` at T0 | mode edge, `t_mission` |
| Phases | `Hover`, `Ascent`, `Descent` (renamed from `Belly flop`), `Deceleration` (replaces `Begin`), `Return to home`, `Landing`, `Landed` | `mission_phase`, once each |
| Apogee | `Apogee` (new) | engine detection, once per flight |
| Freefall countdown | `5` .. `1` before the upright | flip start distance / descent speed |
| Safety (unchanged) | failures and `Failure cleared`, `Failsafe`, `GS failsafe`, `Control source lost`, `Telemetry lost/restored`, `Router link lost/restored`, `Go refused`, `Altitude mismatch`, `Position deviation`, `Simulation` / `Real sensors` | as before |
| Removed | `Motors enabled`, `Begin`, `Toggle SD off, then on`, `Zeroing`, `Zero complete`, `Belly flop` | |
