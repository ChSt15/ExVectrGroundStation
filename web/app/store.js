// Client store (design 5.1): per view, the latest packet per (type, src) with iv and stale_after,
// plus the view clock. Panels read from here.

export class Store {
  constructor(perfNow = () => performance.now() / 1000) {
    this.perfNow = perfNow;
    this.views = { live: this._newView(), replay: this._newView() };
    this.schema = new Map(); // type name -> schema type
    this.listeners = new Set(); // fn(kind, view, payload)
  }

  _newView() {
    return { items: new Map(), clock: null, perfAtClock: 0, rate: 1, priority: [] };
  }

  setSchema(types) {
    this.schema = new Map(types.map((t) => [t.name, t]));
  }

  /** Subscribe to store changes: fn("snapshot"|"packet", view, item?). Returns an unsubscribe function. */
  onChange(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  _emit(kind, view, item) {
    for (const fn of this.listeners) fn(kind, view, item);
  }

  /** Replace the store for a view (snapshot message). */
  applySnapshot(view, msg) {
    const v = this.views[view];
    if (!v) return;
    v.items = new Map();
    if (Array.isArray(msg.priority)) v.priority = msg.priority;
    this.setClock(view, msg.clock);
    for (const it of msg.items || []) v.items.set(`${it.type}|${it.src}`, it);
    this._emit("snapshot", view);
    for (const it of msg.items || []) this._emit("packet", view, it);
  }

  /** Merge a `packets` message. */
  applyPackets(view, msg) {
    const v = this.views[view];
    if (!v) return;
    this.setClock(view, msg.clock);
    for (const it of msg.items || []) {
      const key = `${it.type}|${it.src}`;
      const old = v.items.get(key);
      // `all` mode packets carry no iv/stale_after: keep the last known values
      if (old && it.iv === undefined) it.iv = old.iv;
      if (old && it.stale_after === undefined) it.stale_after = old.stale_after;
      v.items.set(key, it);
      this._emit("packet", view, it);
    }
  }

  /** Record the view clock from a message (seconds, core time). `rate`: clock seconds per wall second. */
  setClock(view, clock, rate) {
    const v = this.views[view];
    if (!v || typeof clock !== "number") return;
    v.clock = clock;
    v.perfAtClock = this.perfNow();
    if (rate !== undefined) v.rate = rate;
  }

  setRate(view, rate) {
    const v = this.views[view];
    if (!v) return;
    // re-anchor so the clock does not jump when the rate changes
    if (v.clock !== null) {
      const n = this.perfNow();
      v.clock += (n - v.perfAtClock) * v.rate;
      v.perfAtClock = n;
    }
    v.rate = rate;
  }

  /** Current view time in core seconds, extrapolated from the last message (never the browser's wall clock). */
  now(view) {
    const v = this.views[view];
    if (!v || v.clock === null) return null;
    return v.clock + (this.perfNow() - v.perfAtClock) * v.rate;
  }

  /**
   * Entry for (type, src). Without src: the type merged field by field over all sources (as the core's
   * `StateStore.get_merged`): each field comes from the highest-priority source whose packet is fresh and carries it,
   * else from the newest packet that carries it. The result is the preferred source's entry with `f`/`nf` filled in;
   * `fs[field] = {src, t, stale_after}` gives each field's own source and time (ages are per field).
   * With one source it is that source's entry, unchanged.
   */
  get(view, type, src) {
    const v = this.views[view];
    if (!v) return undefined;
    if (src) return v.items.get(`${type}|${src}`);
    const cands = [];
    for (const it of v.items.values()) if (it.type === type) cands.push(it);
    if (cands.length < 2) return cands[0];
    const now = this.now(view);
    const rank = (it) => {
      const i = v.priority.indexOf(it.src);
      return i < 0 ? v.priority.length : i;
    };
    cands.sort((a, b) => rank(a) - rank(b) || (a.src < b.src ? -1 : 1));
    const isFresh = (it) => now === null || now - it.t <= (typeof it.stale_after === "number" ? it.stale_after : 0.5);
    const fresh = cands.filter(isFresh);
    const newest = [...cands].sort((a, b) => b.t - a.t);
    const base = fresh[0] || newest[0];
    const names = new Set();
    for (const it of cands) for (const k of Object.keys(it.f || {})) names.add(k);
    const f = {};
    const nf = {};
    const fs = {};
    let merged = false;
    for (const k of names) {
      const has = (it) => it.f && it.f[k] !== undefined && it.f[k] !== null;
      const pick = fresh.find(has) || newest.find(has) || cands.find((it) => it.f && k in it.f);
      f[k] = pick.f[k];
      if (pick.nf && pick.nf[k] !== undefined) nf[k] = pick.nf[k];
      if (pick !== base) {
        merged = true;
        fs[k] = { src: pick.src, t: pick.t, stale_after: pick.stale_after };
      }
    }
    if (!merged) return { ...base, srcs: cands.map((it) => it.src) };
    const out = { ...base, f, srcs: cands.map((it) => it.src), fs };
    if (Object.keys(nf).length) out.nf = nf;
    else delete out.nf;
    return out;
  }

  /** All entries of a type (one per source). */
  all(view, type) {
    const v = this.views[view];
    return v ? [...v.items.values()].filter((it) => !type || it.type === type) : [];
  }

  clear(view) {
    const v = this.views[view];
    if (v) v.items = new Map(); // the clock is kept: it is refreshed by the next message anyway
  }
}
