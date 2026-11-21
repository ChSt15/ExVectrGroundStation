// Speech adapter over the Web Speech API (speechSynthesis) plus a WebAudio alert beep. The synth, the utterance
// class and the AudioContext are injectable so that tests can use fakes. The Web Speech API has rate, pitch, volume
// and voice only: urgency is emulated with the styles of settings.js and the beep (no emotion, no SSML).
import { CLIP_GAP_S } from "./clips.js";
import { beepGain, effectiveVolume, styleOf } from "./settings.js";

export const BEEP_TONES = [{ hz: 1000, s: 0.11 }, { hz: 760, s: 0.11 }]; // short urgent two-tone beep
export const BEEP_TOTAL_S = 0.3;

export class Speaker {
  constructor({ getSettings, synth, Utterance, AudioCtx, clips = null } = {}) {
    this.cfg = getSettings;
    this.clips = clips; // ClipPlayer (recorded voice) or null
    this.synth = synth !== undefined ? synth : globalThis.speechSynthesis;
    this.Utterance = Utterance !== undefined ? Utterance : globalThis.SpeechSynthesisUtterance;
    this.AudioCtx = AudioCtx !== undefined ? AudioCtx : globalThis.AudioContext || globalThis.webkitAudioContext;
    this.ctx = null;
    this.timer = null;
    this.watchdog = null;
    this.voiceListeners = new Set();
    if (this.synth && "onvoiceschanged" in this.synth) this.synth.onvoiceschanged = () => this.voiceListeners.forEach((f) => f(this.voices()));
    else if (this.synth && this.synth.addEventListener) this.synth.addEventListener("voiceschanged", () => this.voiceListeners.forEach((f) => f(this.voices())));
  }

  /** speechSynthesis exists at all (voices may still be missing, see voices()). */
  get supported() {
    return !!(this.synth && this.Utterance);
  }

  /** Something can make sound: recorded clips and beeps need WebAudio only, browser speech needs speechSynthesis. */
  get canSound() {
    return this.supported || !!this.AudioCtx;
  }

  /** Browser speech is usable: speechSynthesis exists and has at least one voice (Flatpak Chromium has none). */
  get ttsUsable() {
    return this.supported && this.voices().length > 0;
  }

  /** [{uri, name, lang}] from getVoices(); [] when none are installed (or not loaded yet). */
  voices() {
    try {
      return ((this.synth && this.synth.getVoices && this.synth.getVoices()) || []).map((v) => ({ uri: v.voiceURI, name: v.name, lang: v.lang }));
    } catch (e) {
      return [];
    }
  }

  onVoices(fn) {
    this.voiceListeners.add(fn);
    return () => this.voiceListeners.delete(fn);
  }

  /** Call from a user gesture: resume the AudioContext and prime speechSynthesis. Returns true when speech is usable. */
  unlock() {
    try {
      if (this.AudioCtx && !this.ctx) this.ctx = new this.AudioCtx();
      if (this.ctx && this.ctx.state === "suspended") this.ctx.resume?.();
    } catch (e) {
      this.ctx = null;
    }
    if (this.clips && this.ctx) this.clips.load(this.ctx);
    if (!this.supported) return this.canSound;
    try {
      const u = new this.Utterance("");
      u.volume = 0;
      this.synth.speak(u);
    } catch (e) {
      return false;
    }
    return true;
  }

  /** Two-tone beep at the master volume. Returns false when it cannot play (no WebAudio, volume 0). */
  beep(master = this.cfg().volume) {
    const gain = beepGain(master);
    if (gain <= 0 || !this.AudioCtx) return false;
    try {
      if (!this.ctx) this.ctx = new this.AudioCtx();
      if (this.ctx.state === "suspended") this.ctx.resume?.();
      let t = this.ctx.currentTime;
      for (const tone of BEEP_TONES) {
        const o = this.ctx.createOscillator();
        const g = this.ctx.createGain();
        o.type = "square";
        o.frequency.value = tone.hz;
        g.gain.setValueAtTime(gain, t);
        g.gain.setValueAtTime(0, t + tone.s);
        o.connect(g);
        g.connect(this.ctx.destination);
        o.start(t);
        o.stop(t + tone.s + 0.01);
        t += tone.s;
      }
      return true;
    } catch (e) {
      return false;
    }
  }

  /** Speak one event with the style of its severity; `done` is called once when finished (or when it cannot speak). */
  speak(ev, done) {
    const c = this.cfg();
    const style = c.styles[styleOf(ev.severity)];
    const volume = effectiveVolume(style.volume, c.volume);
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      clearTimeout(this.watchdog);
      done();
    };
    if (volume <= 0) {
      setTimeout(finish, 0);
      return;
    }
    const mode = c.voiceMode || "recorded";
    const ids = mode === "recorded" && this.clips && this.ctx ? this.clips.plan(ev.text) : null;
    // recorded clip when there is one; else browser speech when it can speak; else the beep
    const way = mode === "beeps" ? "beep" : ids ? "clips" : this.ttsUsable ? "tts" : "beep";
    const say = () => {
      try {
        if (way === "clips") {
          if (this.ctx.state === "suspended") this.ctx.resume?.();
          const total = ids.reduce((t, id) => t + this.clips.buffers.get(id).duration + CLIP_GAP_S, 0);
          if (!this.clips.play(this.ctx, ids, volume, finish)) finish();
          else this.watchdog = setTimeout(finish, (total + 1) * 1000);
          return;
        }
        if (way === "beep") {
          // beeps only / nothing else available: a failure already beeped, the others get one short beep
          if (ev.severity !== "failure") this.beep(c.volume);
          setTimeout(finish, BEEP_TOTAL_S * 1000);
          return;
        }
        const u = new this.Utterance(ev.text);
        u.rate = style.rate;
        u.pitch = style.pitch;
        u.volume = volume;
        if (c.voice) {
          const v = (this.synth.getVoices() || []).find((x) => x.voiceURI === c.voice);
          if (v) u.voice = v;
        }
        u.onend = finish;
        u.onerror = finish;
        this.synth.speak(u);
        this.watchdog = setTimeout(finish, 3000 + ev.text.length * 150); // some browsers never fire `end`
      } catch (e) {
        finish();
      }
    };
    if (ev.severity === "failure" && this.beep(c.volume)) this.timer = setTimeout(say, BEEP_TOTAL_S * 1000);
    else say();
  }

  cancel() {
    clearTimeout(this.timer);
    clearTimeout(this.watchdog);
    try {
      this.clips && this.clips.stop();
    } catch (e) {
      /* nothing playing */
    }
    try {
      this.synth && this.synth.cancel();
    } catch (e) {
      /* nothing to cancel */
    }
  }
}
