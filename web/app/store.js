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
    return { items: new Map(), clock: null, perfAtClock: 0, rate: 1 };
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

  /** Entry for (type, src). Without src: the freshest entry of that type. */
  get(view, type, src) {
    const v = this.views[view];
    if (!v) return undefined;
    if (src) return v.items.get(`${type}|${src}`);
    let best;
    for (const it of v.items.values()) if (it.type === type && (!best || it.t > best.t)) best = it;
    return best;
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
