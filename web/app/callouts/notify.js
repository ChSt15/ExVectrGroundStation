// Notifications: toast stack (top right, below the header and status area, left of the Operations dock) and the
// history list (last 100). Every callout is shown, also when audio is off or blocked. Failures that are `sticky`
// stay until the failure clears or the user dismisses them.
import { h } from "../ui.js";

export const MAX_TOASTS = 5;
export const HISTORY_MAX = 100;
export const AUTO_DISMISS_MS = 5000;
export const FAILURE_DISMISS_MS = 10000;

export const toneOf = (severity) => ({ failure: "bad", warning: "warn", ok: "ok" })[severity] || "info";

/** Pure history helper: newest first, bounded. */
export function pushHistory(list, entry, max = HISTORY_MAX) {
  const out = [entry, ...list];
  if (out.length > max) out.length = max;
  return out;
}

export class Notifier {
  constructor({ root = document.body, now = () => new Date() } = {}) {
    this.now = now;
    this.history = [];
    this.toasts = []; // {el, key, sticky, timer, count, ev}
    this.listeners = new Set();
    this.bar = h("div", { class: "toast-bar", "data-testid": "audio-bar", hidden: true }); // "Enable audio" (filled by the controller)
    this.stack = h("div", { class: "toast-stack" });
    this.el = h("div", { class: "toasts", "data-testid": "toasts", "aria-label": "callout notifications" }, this.bar, this.stack);
    root.append(this.el);
    this.reposition();
    window.addEventListener("resize", () => this.reposition());
  }

  onHistory(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /**
   * Keep the stack clear of the status area (below #top) and of the Operations dock (left of it). Called on every
   * change and on resize; the dock width changes with its mode.
   */
  reposition() {
    const top = document.getElementById("top");
    const dock = document.getElementById("dock");
    const topPx = top ? Math.ceil(top.getBoundingClientRect().bottom) + 8 : 8;
    let right = 12;
    if (dock && !dock.hidden) {
      const r = dock.getBoundingClientRect();
      if (r.width > 0) right = Math.max(12, Math.ceil(window.innerWidth - r.left) + 8);
    }
    this.el.style.top = `${topPx}px`;
    this.el.style.right = `${right}px`;
  }

  /** Show an event as a toast and add it to the history. */
  show(ev) {
    const t = this.now();
    this.history = pushHistory(this.history, { t: t.getTime(), text: ev.text, severity: ev.severity, category: ev.category, id: ev.id });
    this.listeners.forEach((f) => f(this.history));
    const existing = ev.key ? this.toasts.find((x) => x.key === ev.key && x.ev.text === ev.text) : null;
    if (existing) {
      existing.count += 1;
      existing.badge.textContent = `×${existing.count}`;
      existing.badge.hidden = false;
      return;
    }
    const badge = h("span", { class: "toast-n", hidden: true });
    const close = h("button", { class: "toast-x", title: "dismiss", "aria-label": "dismiss notification" }, "✕");
    const el = h("div", { class: "toast", "data-tone": toneOf(ev.severity), "data-category": ev.category, "data-sticky": String(!!ev.sticky), role: ev.severity === "failure" ? "alert" : "status" },
      h("span", { class: "toast-t", text: ev.text }), badge, close);
    const item = { el, key: ev.key, sticky: !!ev.sticky, count: 1, badge, ev, timer: null };
    close.addEventListener("click", () => this.remove(item));
    if (!item.sticky) item.timer = setTimeout(() => this.remove(item), ev.severity === "failure" ? FAILURE_DISMISS_MS : AUTO_DISMISS_MS);
    this.toasts.push(item);
    this.stack.append(el);
    while (this.toasts.length > MAX_TOASTS) this.remove(this.toasts.find((x) => !x.sticky) || this.toasts[0]);
    this.reposition();
  }

  remove(item) {
    clearTimeout(item.timer);
    item.el.remove();
    this.toasts = this.toasts.filter((x) => x !== item);
  }

  /** A failure (or other keyed condition) ended: drop its toasts. */
  clearKeys(keys) {
    for (const x of [...this.toasts]) if (x.key && keys.includes(x.key)) this.remove(x);
  }

  clearAll() {
    for (const x of [...this.toasts]) this.remove(x);
  }
}
