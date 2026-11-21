// Callouts settings page (Operations dock): master switch, volume, categories with TEST buttons, altitude list,
// deviation limit, voice styles, voice, speak-in-replay, history. docs/callouts.md. Display-only feature.
import { registerPanel } from "./registry.js";
import { h, section } from "../ui.js";
import { CATEGORIES, CATEGORY_LABELS, STYLE_NAMES, VOICE_MODES, VOICE_MODE_LABELS } from "../callouts/settings.js";
import { toneOf } from "../callouts/notify.js";

const STYLE_HELP = {
  normal: "mission, altitude, countdown",
  warning: "deviation, telemetry and link",
  failure: "failures, failsafe, motors enabled (with a beep)",
};
const NO_VOICES = "no speech voices available: install speech-dispatcher / a voice, text notifications still shown";
const hhmmss = (ms) => new Date(ms).toTimeString().slice(0, 8);

registerPanel({
  id: "callouts",
  title: "Callouts",
  consumes: [],
  create(el, ctx) {
    const co = () => ctx.callouts();
    const set = (fn) => co().update(fn);
    const num = (testid, step, min, max, on) => {
      const i = h("input", { type: "number", step, min, max, "data-testid": testid, class: "co-num" });
      i.addEventListener("change", () => {
        const v = Number(i.value);
        if (Number.isFinite(v)) on(v);
      });
      return i;
    };

    // ---- audio and volume
    const audioState = h("span", { class: "pill", "data-testid": "audio-state" });
    const audioBtn = h("button", { "data-testid": "audio-enable" });
    audioBtn.addEventListener("click", () => (co().audioState() === "on" ? co().disableAudio() : co().enableAudio()));
    const audioNote = h("div", { class: "hint", "data-testid": "voices-note" });
    const mode = h("select", { "data-testid": "co-voice-mode", "aria-label": "voice" }, ...VOICE_MODES.map((k) => h("option", { value: k, text: VOICE_MODE_LABELS[k] })));
    mode.addEventListener("change", () => set((s) => (s.voiceMode = mode.value)));
    const a = section(el, "Audio");
    a.append(
      h("div", { class: "co-row" }, audioState, audioBtn),
      h("div", { class: "co-row" }, h("span", { text: "Voice" }), mode),
      h("div", { class: "hint", text: "Recorded: pre-recorded clips, work without any system voice. Browser voice: the browser's speech synthesis. Beeps only: a short beep per callout." }),
      audioNote,
    );

    const master = h("input", { type: "checkbox", "data-testid": "co-master" });
    master.addEventListener("change", () => set((s) => (s.master = master.checked)));
    const vol = h("input", { type: "range", min: "0", max: "100", step: "1", "data-testid": "co-volume", "aria-label": "master volume" });
    const volPct = h("span", { class: "co-pct", "data-testid": "co-volume-pct" });
    vol.addEventListener("input", () => set((s) => (s.volume = Number(vol.value) / 100))); // applied immediately
    const volTest = h("button", { "data-testid": "co-volume-test", text: "Test volume" });
    volTest.addEventListener("click", () => co().testVolume());
    const m = section(el, "Master");
    m.append(
      h("label", { class: "co-row" }, master, " Callouts on (off: no speech and no notifications)"),
      h("div", { class: "co-row" }, h("span", { text: "Volume" }), vol, volPct, volTest),
      h("div", { class: "hint", text: "The master volume scales every voice style's volume and the alert beep; it is applied to the next callout." }),
    );

    // ---- categories
    const catBoxes = {};
    const c = section(el, "Categories");
    for (const k of CATEGORIES) {
      const box = h("input", { type: "checkbox", "data-testid": `co-cat-${k}` });
      box.addEventListener("change", () => set((s) => (s.categories[k] = box.checked)));
      const test = h("button", { text: "Test", "data-testid": `co-test-${k}`, title: `speak and show a sample of ${CATEGORY_LABELS[k]}` });
      test.addEventListener("click", () => co().test(k));
      catBoxes[k] = box;
      c.append(h("div", { class: "co-row" }, h("label", { class: "co-grow" }, box, " ", CATEGORY_LABELS[k]), test));
    }
    c.append(h("div", { class: "hint", text: "Position deviation is off by default: see docs/callouts.md (the setpoint is the guidance reference, not verified as a hard target)." }));

    // ---- thresholds
    const altInput = h("input", { type: "text", class: "co-list", "data-testid": "co-altitudes", "aria-label": "altitude list in metres" });
    altInput.addEventListener("change", () => set((s) => (s.altitude.levels = altInput.value.split(/[\s,;]+/).filter(Boolean).map(Number))));
    const hyst = num("co-hyst", "0.5", "0", "50", (v) => set((s) => (s.altitude.hysteresis_m = v)));
    const devLimit = num("co-dev-limit", "0.5", "0.1", "10000", (v) => set((s) => (s.deviation.limit_m = v)));
    const altLimit = num("co-alt-limit", "0.5", "0.1", "10000", (v) => set((s) => (s.altcheck.limit_m = v)));
    const altHold = num("co-alt-hold", "0.5", "0", "60", (v) => set((s) => (s.altcheck.hold_s = v)));
    const replay = h("input", { type: "checkbox", "data-testid": "co-replay" });
    replay.addEventListener("change", () => set((s) => (s.speakInReplay = replay.checked)));
    const t = section(el, "Thresholds");
    t.append(
      h("div", { class: "co-row" }, h("span", { text: "Altitudes (m)" }), altInput),
      h("div", { class: "co-row" }, h("span", { text: "Hysteresis (m)" }), hyst, h("span", { class: "dim", text: "no repeat near a threshold" })),
      h("div", { class: "co-row" }, h("span", { text: "Deviation limit (m)" }), devLimit),
      h("div", { class: "co-row" }, h("span", { text: "GNSS altitude check limit (m)" }), altLimit, h("span", { text: "for (s)" }), altHold, h("span", { class: "dim", text: "also the GNSS panel's warning" })),
      h("label", { class: "co-row" }, replay, " Speak and notify in replay (callouts follow the recorded timeline, never a seek)"),
    );

    // ---- styles and voice
    const styleInputs = {};
    const st = section(el, "Voice styles");
    for (const n of STYLE_NAMES) {
      const row = h("div", { class: "co-row co-style", "data-style": n }, h("b", { text: n }));
      styleInputs[n] = {};
      for (const [f, step, max] of [["rate", "0.05", "2"], ["pitch", "0.05", "2"], ["volume", "0.05", "1"]]) {
        const i = num(`co-style-${n}-${f}`, step, f === "rate" ? "0.5" : "0", max, (v) => set((s) => (s.styles[n][f] = v)));
        styleInputs[n][f] = i;
        row.append(h("label", {}, f, " ", i));
      }
      st.append(row, h("div", { class: "hint", text: STYLE_HELP[n] }));
    }
    st.append(h("div", { class: "hint", text: "The Web Speech API has rate, pitch, volume and voice only (no emotion, no SSML): urgency is emulated with these styles and the beep." }));
    const voice = h("select", { "data-testid": "co-voice", "aria-label": "browser speech voice" });
    voice.addEventListener("change", () => set((s) => (s.voice = voice.value)));
    st.append(h("div", { class: "co-row" }, h("span", { text: "Browser voice" }), voice), h("div", { class: "hint", text: "Used by the Browser voice setting, and as the fallback of Recorded for phrases without a clip. Rate and pitch of the styles apply to browser speech only; recorded clips use the volume." }));

    // ---- reset and history
    const reset = h("button", { "data-testid": "co-reset", text: "Reset to defaults" });
    reset.addEventListener("click", () => co().reset());
    const histList = h("ol", { class: "co-history", "data-testid": "co-history" });
    const hs = section(el, "Notification history (last 100)");
    hs.append(histList);
    el.append(h("div", { class: "co-row" }, reset));
    let lastHist = null;

    function renderHistory() {
      const hist = ctx.notifier().history;
      if (hist === lastHist) return;
      lastHist = hist;
      histList.replaceChildren(...(hist.length ? hist.map((e) => h("li", { "data-tone": toneOf(e.severity) }, h("span", { class: "mono", text: hhmmss(e.t) }), " ", e.text)) : [h("li", { class: "dim", text: "none yet" })]));
    }

    function renderVoices() {
      const list = ctx.speaker().voices();
      const cur = co().settings().voice;
      voice.replaceChildren(h("option", { value: "", text: "browser default" }), ...list.map((v) => h("option", { value: v.uri, text: `${v.name} (${v.lang})` })));
      voice.value = list.some((v) => v.uri === cur) ? cur : "";
      voice.disabled = !list.length;
      renderAudio();
    }

    function renderAudio() {
      const s = co().audioState();
      const sp = ctx.speaker();
      audioState.dataset.tone = s === "on" ? "ok" : "bad";
      audioState.textContent = { on: "audio on", off: "audio off (click Enable audio)", blocked: "audio blocked: click anywhere or Enable audio", unsupported: "no audio output available in this browser" }[s];
      audioBtn.textContent = s === "on" ? "Disable audio" : "Enable audio";
      audioBtn.disabled = s === "unsupported";
      const m = co().settings().voiceMode;
      const clips = sp.clips;
      audioNote.textContent = m === "beeps" ? ""
        : m === "recorded" ? (clips && clips.state === "failed" ? "recorded clips could not be loaded: using browser speech or beeps." : clips && clips.state === "loading" ? "loading recorded clips ..." : "")
        : !sp.supported ? "speechSynthesis is not available: beeps instead, text notifications are still shown." : !sp.voices().length ? NO_VOICES : "";
    }

    function render() {
      const s = co().settings();
      const put = (i, v) => {
        if (document.activeElement !== i) i.value = String(v);
      };
      master.checked = s.master;
      mode.value = s.voiceMode;
      put(vol, Math.round(s.volume * 100));
      volPct.textContent = `${Math.round(s.volume * 100)} %`;
      for (const k of CATEGORIES) catBoxes[k].checked = s.categories[k];
      put(altInput, s.altitude.levels.join(", "));
      put(hyst, s.altitude.hysteresis_m);
      put(devLimit, s.deviation.limit_m);
      put(altLimit, s.altcheck.limit_m);
      put(altHold, s.altcheck.hold_s);
      replay.checked = s.speakInReplay;
      for (const n of STYLE_NAMES) for (const f of Object.keys(styleInputs[n])) put(styleInputs[n][f], s.styles[n][f]);
      voice.value = [...voice.options].some((o) => o.value === s.voice) ? s.voice : "";
      renderAudio();
    }

    co().onChange(render);
    ctx.speaker().onVoices(renderVoices);
    renderVoices();
    render();
    renderHistory();
    return { onTick: renderHistory, onViewChange: render };
  },
});
