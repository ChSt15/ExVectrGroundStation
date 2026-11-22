// Callout controller: store -> engine -> suppression -> notifications and speech queue. Owns the settings (persisted
// in localStorage, guarded) and the audio state. Display-only: nothing here sends anything to the vehicle.
import { CalloutEngine } from "./engine.js";
import { CalloutQueue, Suppressor } from "./queue.js";
import { LogWatcher } from "./logwatch.js";
import { CATEGORIES, DEFAULTS, mergeSettings } from "./settings.js";

export const CALLOUT_CONSUMES = ["VehicleStatus", "MissionState", "Position", "Gps", "HomePosition"];
export const LS_SETTINGS = "exgs.callouts";
export const LS_AUDIO = "exgs.callouts.audio";

export const SAMPLES = {
  countdown: { id: "test.countdown", text: "3", severity: "info", priority: 5 },
  phase: { id: "test.phase", text: "Ascent", severity: "info", priority: 3 },
  altitude: { id: "test.altitude", text: "Altitude 50", severity: "info", priority: 4 },
  failure: { id: "test.failure", text: "Position failure", severity: "failure", priority: 0 },
  link: { id: "test.link", text: "Telemetry lost", severity: "warning", priority: 1 },
  state: { id: "test.state", text: "Armed", severity: "warning", priority: 2 },
  deviation: { id: "test.deviation", text: "Position deviation", severity: "warning", priority: 1 },
  altcheck: { id: "test.altcheck", text: "Altitude mismatch", severity: "warning", priority: 1 },
};

export function createCallouts({ store, getView, getCoreStatus, ls, speaker, notifier, defaults = null, clock = () => performance.now() / 1000 }) {
  let base = mergeSettings(DEFAULTS, defaults);
  let settings = base;
  try {
    const raw = ls.get(LS_SETTINGS);
    if (raw) {
      const saved = JSON.parse(raw);
      delete saved.failure; // not editable in the GUI; an old saved copy (repeats 3) must not override the default (announce once)
      settings = mergeSettings(base, saved);
    }
  } catch (e) {
    settings = base;
  }
  const getSettings = () => settings;
  const engine = new CalloutEngine(getSettings);
  const queue = new CalloutQueue({ speaker, clock });
  const suppressor = new Suppressor(getSettings, clock);
  const listeners = new Set();
  let audioChoice = ls.get(LS_AUDIO) === "1";
  let activated = false;
  let lastView = null;

  // vehicle log lines -> toasts (own master/categories/audio under settings.log; off in replay unless speakInReplay)
  let logView = "live";
  const logAllowed = () => settings.master && settings.log.enabled && (logView !== "replay" || settings.speakInReplay);
  const logwatch = new LogWatcher({
    clock,
    emit(c, repeat) {
      if (!logAllowed() || !settings.log.categories[c.category]) return;
      const key = `log:${c.title}`;
      if (repeat && !notifier.toasts.some((x) => x.key === key)) return; // inside the 10 s window and already gone: stay quiet
      notifier.show({ id: `log.${c.category}`, category: "log", text: c.title, severity: c.severity, key, sticky: c.sticky });
      if (!repeat && settings.log.audio[c.category] && audioState() === "on" && settings.volume > 0) speaker.beep(settings.volume);
    },
  });

  const changed = () => listeners.forEach((f) => f());

  function audioState() {
    if (!speaker.canSound) return "unsupported";
    if (!audioChoice) return "off";
    return activated ? "on" : "blocked";
  }

  function persist() {
    try {
      ls.set(LS_SETTINGS, JSON.stringify(settings));
    } catch (e) {
      /* storage unavailable */
    }
  }

  function deliver(ev, force = false) {
    if (!force && (!settings.master || !settings.categories[ev.category])) return false;
    if (!force && !suppressor.accept(ev)) return false;
    notifier.show(ev);
    if (audioState() === "on" && settings.volume > 0) queue.push(ev);
    return true;
  }

  function enableAudio() {
    audioChoice = true;
    ls.set(LS_AUDIO, "1");
    speaker.unlock();
    activated = speaker.canSound;
    changed();
  }

  function disableAudio() {
    audioChoice = false;
    ls.set(LS_AUDIO, "0");
    queue.cancelAll();
    changed();
  }

  // a remembered choice becomes active with the first user gesture on the page (browser autoplay rule)
  if (typeof document !== "undefined") {
    const gesture = () => {
      if (audioChoice && !activated) enableAudio();
    };
    for (const t of ["pointerdown", "keydown"]) document.addEventListener(t, gesture, { capture: true });
  }

  function latest(view, type) {
    return store.get(view, type);
  }

  function tick() {
    const view = getView();
    if (view !== logView) {
      logView = view;
      logwatch.reset();
    }
    logwatch.tick();
    if (view !== lastView) {
      if (lastView !== null) {
        queue.cancelAll();
        notifier.clearAll();
      }
      if (view === "live") engine.reset();
      else engine.resync();
      lastView = view;
    }
    const now = store.now(view);
    if (now === null) return;
    const cs = getCoreStatus();
    const r = engine.evaluate({
      view,
      now,
      status: latest(view, "VehicleStatus"),
      mission: latest(view, "MissionState"),
      position: latest(view, "Position"),
      gps: latest(view, "Gps"),
      home: latest(view, "HomePosition"),
      sources: view === "live" && cs ? cs.sources : [],
    });
    if (r.resynced) queue.cancelAll();
    if (r.clears.length) notifier.clearKeys(r.clears);
    if (view === "replay" && !settings.speakInReplay) {
      // keep the baselines moving (done above) but say and show nothing
    } else {
      for (const ev of r.events) deliver(ev);
    }
    queue.tick();
  }

  return {
    tick,
    settings: getSettings,
    defaults: () => base,
    /** Replace settings by applying `fn(copy)`; the result is validated by mergeSettings and persisted. */
    update(fn) {
      const draft = JSON.parse(JSON.stringify(settings));
      fn(draft);
      settings = mergeSettings(base, draft);
      persist();
      changed();
    },
    reset() {
      settings = base;
      try {
        ls.set(LS_SETTINGS, "");
      } catch (e) {
        /* ignore */
      }
      changed();
    },
    onChange(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    audioState,
    enableAudio,
    disableAudio,
    /** A replay seek (snapshot in the replay view): re-baseline and silence. */
    onSeek() {
      engine.resync();
      queue.cancelAll();
    },
    /** TEST button: speaks and shows a sample of the category (the click is the user gesture that enables audio). */
    test(category) {
      const s = SAMPLES[category];
      if (!s || !CATEGORIES.includes(category)) return;
      if (!activated) enableAudio();
      suppressor.clear();
      deliver({ ...s, category }, true);
    },
    testVolume() {
      if (!activated) enableAudio();
      notifier.show({ id: "test.volume", category: "state", text: "Volume test", severity: "info", priority: 2 });
      if (audioState() === "on") {
        speaker.beep(settings.volume);
        queue.push({ id: "test.volume", category: "state", text: "Volume test", severity: "info", priority: 2, maxAge: 10 });
      }
    },
    engine,
    queue,
    /** Vehicle log watcher: `onLog(packet)` for each ConsoleText packet of the active view. */
    log: {
      onLog: (pkt) => pkt?.f && typeof pkt.f.text === "string" && logwatch.feed(pkt.f.text),
      /** The calibration problem chip {text, detail} or null (not shown in replay unless the callouts are enabled there). */
      chip: () => (logAllowed() ? logwatch.chip() : null),
      /** Calibration state for the Failures panel rows: {state, detail}. */
      calibState: () => (logAllowed() ? logwatch.calibState() : { state: "unknown", detail: {} }),
      unread: () => logwatch.unread(),
      markRead: () => logwatch.markRead(),
      watcher: logwatch,
    },
  };
}
