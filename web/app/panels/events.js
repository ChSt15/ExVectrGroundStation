// Mission events: a timestamp list of what happened when (docs/gui.md "Mission events"), stamped with the continuous
// mission time and the wall time. Derived by status/mission_events.js from vehicle-reported packets and GS events.
// Replay: the whole recording is read once when the log is opened (core op get_events), the list shows everything at
// once, the replay position is highlighted, later events are dimmed, a click seeks. Live: the list grows; a page reload
// restores it from the core's buffer. Display only: nothing is sent to the vehicle (replay.seek moves the viewer).
import { registerPanel } from "./registry.js";
import { h } from "../ui.js";
import { MissionEvents, CATEGORIES, CATEGORY_LABELS, filterRows, applyCutoff, currentIndex, eventsToCsv, fmtEventTime, fmtWall } from "../status/mission_events.js";

const ROW_H = 22;
const OVERSCAN = 8;
const LS_CATS = "exgs.events.cats";
const LS_FOLLOW = "exgs.events.follow";
const LS_CLEAR = "exgs.events.clear_t"; // live clear cutoff (epoch s), per browser
const LS_AUTOCLEAR = "exgs.events.autoclear";
const LIVE_REFRESH_MS = 500;
const ICONS = { mission: "▲", vehicle: "■", command: "▶", link: "◆", log: "●" };

registerPanel({
  id: "events",
  title: "Mission events",
  consumes: ["MissionState", "VehicleStatus", "CmdState", "CmdApplied", "ConsoleText", "GsEvent", "Note"],
  mode: "all",
  create(el, ctx) {
    const me = new MissionEvents();
    let shown = {};
    try {
      shown = JSON.parse(ctx.ls.get(LS_CATS) || "{}") || {};
    } catch (e) {
      shown = {};
    }
    let follow = ctx.ls.get(LS_FOLLOW) !== "0";
    const cl = Number(ctx.ls.get(LS_CLEAR));
    let cutoff = ctx.ls.get(LS_CLEAR) !== null && Number.isFinite(cl) && cl > 0 ? cl : null;
    let autoClear = ctx.ls.get(LS_AUTOCLEAR) === "1";
    let showCleared = false; // session only
    let clearedN = 0;
    let missionCount = null;
    let model = { rows: [], events: [], missions: [] };
    let rows = []; // filtered
    let dirty = false;
    let lastBuild = 0;
    let loadedKey = null;
    let loading = false;
    let queue = [];
    let gen = 0;
    let note = "";
    let lastCur = -2;
    let stick = true;

    const status = h("span", { class: "ev-status dim", "data-testid": "ev-status" });
    const boxes = CATEGORIES.map((c) => {
      const cb = h("input", { type: "checkbox", checked: shown[c] !== false, "data-testid": `ev-cat-${c}` });
      cb.addEventListener("change", () => {
        shown[c] = cb.checked;
        ctx.ls.set(LS_CATS, JSON.stringify(shown));
        refilter();
      });
      return h("label", { class: `ev-cat ev-${c}`, title: CATEGORY_LABELS[c] }, cb, ` ${ICONS[c]} ${CATEGORY_LABELS[c]}`);
    });
    const followCb = h("input", { type: "checkbox", checked: follow, "data-testid": "ev-follow" });
    followCb.addEventListener("change", () => {
      follow = followCb.checked;
      ctx.ls.set(LS_FOLLOW, follow ? "1" : "0");
      lastCur = -2;
    });
    const clearBtn = h("button", { text: "Clear", "data-testid": "ev-clear", onclick: () => clearNow() });
    const showLink = h("a", { href: "#", class: "ev-showcleared", "data-testid": "ev-showcleared", hidden: true });
    showLink.addEventListener("click", (e) => {
      e.preventDefault();
      showCleared = !showCleared;
      refilter();
    });
    const autoCb = h("input", { type: "checkbox", checked: autoClear, "data-testid": "ev-autoclear" });
    autoCb.addEventListener("change", () => {
      autoClear = autoCb.checked;
      ctx.ls.set(LS_AUTOCLEAR, autoClear ? "1" : "0");
    });
    const exportBtn = h("button", { text: "Export CSV", "data-testid": "ev-export", title: "Events matching the category filter: mission time, wall time, category, text", onclick: () => exportCsv() });
    const list = h("div", { class: "evlist", "data-testid": "ev-list", tabindex: "0" });
    const spacer = h("div", { class: "evspacer" });
    list.append(spacer);
    el.append(h("div", { class: "toolbar ev-bar" }, ...boxes, h("label", {}, followCb, " follow"), clearBtn, h("label", { title: "Clear the list when a new mission begins (live)" }, autoCb, " clear at new mission"), showLink, exportBtn, status), list);

    function exportCsv() {
      const evs = rows.filter((r) => r.kind === "event");
      const blob = new Blob([eventsToCsv(evs)], { type: "text/csv" });
      const a = h("a", { href: URL.createObjectURL(blob), download: `mission-events-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.csv` });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    }

    function setCutoff(t) {
      cutoff = t;
      showCleared = false;
      if (t === null) ctx.ls.set(LS_CLEAR, "");
      else ctx.ls.set(LS_CLEAR, String(t));
    }

    /** Live only: hide everything up to the newest event now (replay: the button is disabled, the log is not changed). */
    function clearNow() {
      if (ctx.view() !== "live" || !model.events.length) return;
      setCutoff(model.events[model.events.length - 1].t);
      refilter();
    }

    function refilter() {
      const live = ctx.view() === "live";
      const cut = applyCutoff(filterRows(model.rows, shown), live && !showCleared ? cutoff : null);
      rows = cut.rows;
      clearedN = live && cutoff !== null ? applyCutoff(filterRows(model.rows, shown), cutoff).hidden : 0;
      clearBtn.disabled = !live;
      clearBtn.title = live ? "Hide all events up to now (display and CSV only; Show cleared restores them)" : "Replay: the log is not changed, so there is nothing to clear";
      showLink.hidden = !(live && clearedN > 0);
      showLink.textContent = showCleared ? `Hide cleared (${clearedN})` : `Show cleared (${clearedN})`;
      lastCur = -2;
      spacer.style.height = `${rows.length * ROW_H}px`;
      renderWindow(true);
      updateStatus();
    }

    function rebuild() {
      model = me.build();
      const mc = model.missions.length;
      if (ctx.view() === "live" && autoClear && missionCount !== null && mc > missionCount) setCutoff(model.missions[mc - 1].start - 1e-6);
      missionCount = ctx.view() === "live" ? mc : null;
      lastBuild = performance.now();
      dirty = false;
      const nearBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 2 * ROW_H;
      stick = nearBottom;
      refilter();
      if (ctx.view() === "live" && stick) list.scrollTop = list.scrollHeight;
    }

    function updateStatus() {
      const n = model.events.length;
      const m = model.missions.length;
      status.textContent = loading ? "loading…" : `${n} event${n === 1 ? "" : "s"}${m ? `, ${m} mission${m === 1 ? "" : "s"}` : ""}${rows.length < model.rows.length ? ` (${model.rows.length - rows.length} hidden)` : ""}${note ? ` · ${note}` : ""}`;
    }

    const pos = () => (ctx.view() === "replay" ? ctx.now() : null);
    function renderWindow(force) {
      const top = list.scrollTop;
      const first = Math.max(0, Math.floor(top / ROW_H) - OVERSCAN);
      const last = Math.min(rows.length, Math.ceil((top + (list.clientHeight || 300)) / ROW_H) + OVERSCAN);
      const p = pos();
      const cur = p === null ? -1 : currentIndex(rows, p);
      const frag = [];
      if (!rows.length) {
        frag.push(h("div", { class: "evempty dim", text: loading ? "loading…" : ctx.view() === "replay" ? "no log open, or it holds no events" : "no events yet" }));
      }
      for (let i = first; i < last; i++) {
        const r = rows[i];
        const style = `top:${i * ROW_H}px`;
        if (r.kind === "header") {
          frag.push(h("div", { class: "evrow evhead", style, "data-testid": "ev-head", title: r.text }, r.text));
          continue;
        }
        const state = p === null ? "" : i === cur ? "current" : r.t <= p ? "past" : "upcoming";
        const wall = new Date(r.t * 1000);
        const row = h(
          "div",
          { class: `evrow ev-${r.cat}`, style, "data-testid": "ev-row", "data-cat": r.cat, "data-tone": r.tone, "data-state": state, "data-t": String(r.t), "data-type": r.type, title: `${wall.toLocaleString()}  ·  ${r.type}${r.count > 1 ? `  ·  repeated ${r.count} times` : ""}` },
          h("span", { class: "evtm mono", text: fmtEventTime(r.tm) }),
          h("span", { class: "evwall mono dim", text: fmtWall(r.t) }),
          h("span", { class: `evic ev-${r.cat}`, text: ICONS[r.cat] }),
          h("span", { class: "evtx", text: r.count > 1 ? `${r.text} ×${r.count}` : r.text }),
        );
        if (p !== null) row.addEventListener("click", () => seek(r.t));
        frag.push(row);
      }
      spacer.replaceChildren(...frag);
      spacer.style.height = `${rows.length * ROW_H}px`;
      if (p !== null && follow && cur !== lastCur && cur >= 0 && (force || cur !== lastCur)) {
        lastCur = cur;
        const y = cur * ROW_H;
        if (y < list.scrollTop + ROW_H || y > list.scrollTop + list.clientHeight - 2 * ROW_H) {
          list.scrollTop = Math.max(0, y - list.clientHeight / 2);
          requestAnimationFrame(() => renderWindow(false));
        }
      }
    }
    list.addEventListener("scroll", () => renderWindow(false));

    function seek(t) {
      ctx.api.request("replay.seek", { t }).catch(() => {});
    }

    async function load(key) {
      const my = ++gen;
      const view = ctx.view();
      loading = true;
      queue = [];
      missionCount = null; // a reload must not auto-clear
      updateStatus();
      try {
        const res = await ctx.api.request("get_events", { view }, 60000);
        if (my !== gen) return;
        me.reset();
        me.feedAll(res.packets || []);
        const upto = me.lastT;
        for (const it of queue) if (it.t > upto) me.feed(it);
        note = res.truncated ? "list cut at the core's limit" : "";
        loadedKey = key;
      } catch (e) {
        if (my === gen) note = `events: ${e.message}`;
      } finally {
        if (my === gen) {
          loading = false;
          queue = [];
          rebuild();
        }
      }
    }

    function viewKey() {
      if (ctx.view() === "live") return "live";
      const rs = ctx.status()?.replay;
      return rs && rs.name && rs.state !== "closed" && rs.state !== "loading" ? `replay|${rs.name}|${rs.t_end}` : null;
    }

    return {
      onPacket(it) {
        if (ctx.view() !== "live") return; // replay: the whole log was read at once
        if (loading) queue.push(it);
        else {
          me.feed(it);
          dirty = true;
        }
      },
      onReset(view) {
        if (view === "live") loadedKey = null; // reconnect: reload from the core's buffer
      },
      onViewChange() {
        loadedKey = null;
        gen++;
        loading = false;
        me.reset();
        rebuild();
      },
      onTick() {
        if (ctx.api.state === "open" && !loading) {
          const key = viewKey();
          if (key !== loadedKey) {
            if (key === null) {
              me.reset();
              loadedKey = null;
              note = "";
              rebuild();
            } else void load(key);
            return;
          }
        }
        if (dirty && performance.now() - lastBuild > LIVE_REFRESH_MS) rebuild();
        else if (ctx.view() === "replay") renderWindow(false);
      },
    };
  },
});
