import { registerPanel } from "./registry.js";
import { h } from "../ui.js";
import { fmtClock } from "../fmt.js";

const SPEEDS = [0.25, 0.5, 1, 2, 4, 8];
const SEEK_THROTTLE_MS = 100; // at most 10 seeks per second while dragging

registerPanel({
  id: "transport",
  title: "Replay transport",
  consumes: [],
  views: ["replay"],
  create(el, ctx) {
    const play = h("button", { class: "play", text: "▶", title: "play / pause (space)", onclick: () => toggle() });
    const back = h("button", { text: "−5 s", title: "back 5 s (left arrow)", onclick: () => jump(-5) });
    const fwd = h("button", { text: "+5 s", title: "forward 5 s (right arrow)", onclick: () => jump(5) });
    const slider = h("input", { type: "range", min: "0", max: "1000", value: "0", step: "1", class: "scrub grow" });
    const time = h("span", { class: "time", text: "--:-- / --:--" });
    const speed = h("select", { title: "speed", onchange: () => send("replay.speed", { x: Number(speed.value) }) }, SPEEDS.map((s) => h("option", { value: String(s), text: `${s}×` })));
    speed.value = "1";
    const msg = h("div", { class: "msg" });
    el.append(h("div", { class: "toolbar" }, play, back, slider, time, fwd, speed), msg);

    let dragging = false;
    let lastSeek = 0;
    let pendingTimer = null;

    const st = () => ctx.status()?.replay || { state: "closed" };
    const span = () => {
      const r = st();
      return r.t_start != null && r.t_end != null ? r.t_end - r.t_start : 0;
    };

    async function send(op, payload = {}) {
      try {
        await ctx.api.request(op, payload);
        msg.textContent = "";
      } catch (e) {
        msg.textContent = `${op}: ${e.message}`;
      }
    }
    function toggle() {
      const r = st();
      if (r.state === "closed" || r.state === "loading") return;
      send(r.state === "playing" ? "replay.pause" : "replay.play");
    }
    function jump(dt) {
      const r = st();
      if (r.t == null) return;
      send("replay.seek", { t: Math.min(r.t_end, Math.max(r.t_start, r.t + dt)) });
    }
    function seekFromSlider() {
      const r = st();
      if (r.t_start == null) return;
      const t = r.t_start + (Number(slider.value) / 1000) * span();
      const now = performance.now();
      clearTimeout(pendingTimer);
      if (now - lastSeek >= SEEK_THROTTLE_MS) {
        lastSeek = now;
        send("replay.seek", { t });
      } else {
        pendingTimer = setTimeout(() => {
          lastSeek = performance.now();
          send("replay.seek", { t });
        }, SEEK_THROTTLE_MS - (now - lastSeek));
      }
    }
    slider.addEventListener("pointerdown", () => (dragging = true));
    slider.addEventListener("input", seekFromSlider);
    const endDrag = () => (dragging = false);
    slider.addEventListener("pointerup", endDrag);
    slider.addEventListener("change", endDrag);

    function onKey(e) {
      if (ctx.view() !== "replay") return;
      const tag = (e.target && e.target.tagName) || "";
      if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(tag) && !(tag === "INPUT" && e.target.type === "range")) return;
      if (e.key === " ") {
        e.preventDefault();
        toggle();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        jump(-5);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        jump(5);
      }
    }
    document.addEventListener("keydown", onKey);

    function render() {
      const r = st();
      const open = r.state !== "closed" && r.state !== "loading" && r.t_start != null;
      for (const c of [play, back, fwd, slider, speed]) c.disabled = !open;
      play.textContent = r.state === "playing" ? "❚❚" : "▶";
      if (r.state === "closed") msg.textContent = "No log open. Open one from the Recording & replay panel.";
      else if (r.state === "loading") msg.textContent = "Loading log (indexing)...";
      else if (msg.textContent.startsWith("No log") || msg.textContent.startsWith("Loading")) msg.textContent = "";
      if (!open) {
        time.textContent = "--:-- / --:--";
        return;
      }
      const t = r.t - r.t_start;
      if (!dragging) slider.value = String(span() > 0 ? Math.round((t / span()) * 1000) : 0);
      time.textContent = `${fmtClock(t)} / ${fmtClock(span())}${r.state === "ended" ? " (end)" : ""}`;
      if (document.activeElement !== speed) speed.value = String(r.speed);
    }
    return {
      onStatus: render,
      onTick: render,
      onViewChange: render,
      destroy: () => document.removeEventListener("keydown", onKey),
    };
  },
});
