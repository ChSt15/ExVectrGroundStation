// WebSocket client with automatic reconnect (exponential backoff 0.5-5 s) and request/reply by id.

export const BACKOFF_MIN_S = 0.5;
export const BACKOFF_MAX_S = 5;

export function nextBackoff(prev) {
  return prev ? Math.min(BACKOFF_MAX_S, prev * 2) : BACKOFF_MIN_S;
}

export class Api {
  /**
   * opts: { url, name, onMessage(msg), onState(state), onReady() }
   * state: "connecting" | "open" | "closed"; onReady runs after `welcome` on every (re)connect.
   */
  constructor(opts) {
    this.opts = opts;
    this.ws = null;
    this.state = "closed";
    this.nextId = 1;
    this.pending = new Map();
    this.backoff = 0;
    this.timer = null;
    this.stopped = false;
    this.welcome = null;
  }

  start() {
    this.stopped = false;
    this._connect();
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    if (this.ws) this.ws.close();
  }

  _setState(s) {
    this.state = s;
    if (this.opts.onState) this.opts.onState(s);
  }

  _connect() {
    this._setState("connecting");
    let ws;
    try {
      ws = new WebSocket(this.opts.url);
    } catch (e) {
      this._scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.backoff = 0;
      this._setState("open");
      this._send({ op: "hello", client: "gui", name: this.opts.name || "gui", protocol: 1 });
    };
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      if (msg.op === "result" && this.pending.has(msg.id)) {
        const p = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        clearTimeout(p.timer);
        if (msg.ok) p.resolve(msg.data);
        else p.reject(Object.assign(new Error(msg.error?.message || "error"), { code: msg.error?.code }));
        return;
      }
      if (msg.op === "welcome") {
        this.welcome = msg;
        if (this.opts.onMessage) this.opts.onMessage(msg);
        if (this.opts.onReady) this.opts.onReady();
        return;
      }
      if (this.opts.onMessage) this.opts.onMessage(msg);
    };
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      this._failPending("connection closed");
      this._setState("closed");
      this._scheduleReconnect();
    };
    ws.onerror = () => {}; // onclose follows
  }

  _scheduleReconnect() {
    if (this.stopped) return;
    this.backoff = nextBackoff(this.backoff);
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this._connect(), this.backoff * 1000);
  }

  _failPending(why) {
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(Object.assign(new Error(why), { code: "closed" }));
    }
    this.pending.clear();
  }

  _send(obj) {
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  /** Send a request and resolve with `data` of the result, or reject with {code, message}. */
  request(op, payload = {}, timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
      const id = this.nextId++;
      if (!this._send({ op, id, ...payload })) {
        reject(Object.assign(new Error("not connected"), { code: "closed" }));
        return;
      }
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(Object.assign(new Error(`${op} timed out`), { code: "timeout" }));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
    });
  }
}
