// WebAudio player of the recorded clips: loads manifest.json and decodes every clip once (cached AudioBuffers), then
// plays a clip list gap-free by scheduling AudioBufferSourceNodes back to back. The context, fetch and base URL are
// injectable for tests.
import { buildIndex, CLIP_GAP_S, composeText } from "./clips.js";

export class ClipPlayer {
  constructor({ baseUrl, fetchFn } = {}) {
    this.baseUrl = baseUrl;
    this.fetchFn = fetchFn || ((...a) => globalThis.fetch(...a));
    this.buffers = new Map();
    this.index = null;
    this.state = "idle"; // idle | loading | ready | failed
    this.sources = [];
    this.gen = 0;
  }

  get ready() {
    return this.state === "ready";
  }

  /** Fetch and decode everything (once). Never throws; `state` becomes ready or failed (then callouts fall back). */
  async load(ctx) {
    if (this.state !== "idle" || !ctx || !ctx.decodeAudioData) return;
    this.state = "loading";
    try {
      const mr = await this.fetchFn(`${this.baseUrl}manifest.json`);
      if (!mr.ok) throw new Error(`manifest ${mr.status}`);
      const manifest = await mr.json();
      const index = buildIndex(manifest);
      await Promise.all(Object.entries(manifest.clips).map(async ([id, c]) => {
        const r = await this.fetchFn(this.baseUrl + c.file);
        if (!r.ok) throw new Error(`${c.file} ${r.status}`);
        this.buffers.set(id, await ctx.decodeAudioData(await r.arrayBuffer()));
      }));
      this.index = index;
      this.state = "ready";
    } catch (e) {
      this.state = "failed";
    }
  }

  /** Clip ids for a callout text, or null (not loaded, or a part has no clip). */
  plan(text) {
    return this.ready ? composeText(text, this.index) : null;
  }

  /** Play clip ids at `gain` (0..1); `done` is called once at the end, or when cancelled/failed. */
  play(ctx, ids, gain, done) {
    const gen = ++this.gen;
    try {
      const g = ctx.createGain();
      g.gain.value = gain;
      g.connect(ctx.destination);
      let t = ctx.currentTime + 0.03;
      let last = null;
      this.sources = ids.map((id) => {
        const buf = this.buffers.get(id);
        const s = ctx.createBufferSource();
        s.buffer = buf;
        s.connect(g);
        s.start(t);
        t += buf.duration + CLIP_GAP_S;
        last = s;
        return s;
      });
      last.onended = () => gen === this.gen && done();
      return true;
    } catch (e) {
      return false;
    }
  }

  stop() {
    this.gen += 1;
    for (const s of this.sources) {
      try {
        s.stop();
      } catch (e) {
        /* already ended */
      }
    }
    this.sources = [];
  }
}
