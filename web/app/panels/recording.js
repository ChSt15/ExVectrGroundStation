import { registerPanel } from "./registry.js";
import { h } from "../ui.js";
import { fmtBytes, fmtClock, fmtDateTime } from "../fmt.js";

registerPanel({
  id: "recording",
  title: "Recording & replay",
  consumes: [],
  create(el, ctx) {
    const msg = h("div", { class: "msg" });
    const label = h("input", { type: "text", placeholder: "label (optional)", maxlength: "60", class: "grow" });
    const start = h("button", { class: "danger", text: "Start", onclick: () => run("rec.start", label.value.trim() ? { label: label.value.trim() } : {}) });
    const stop = h("button", { text: "Stop", onclick: () => run("rec.stop") });
    const info = h("div", { class: "info" });
    const noteIn = h("input", { type: "text", placeholder: "note for the recording", class: "grow", maxlength: "200" });
    const noteBtn = h("button", {
      text: "Add note",
      onclick: async () => {
        const text = noteIn.value.trim();
        if (!text) return;
        if (await run("rec.note", { text })) noteIn.value = "";
      },
    });
    noteIn.addEventListener("keydown", (e) => {
      if (e.key === "Enter") noteBtn.click();
    });
    const list = h("div", { class: "loglist" });
    const refresh = h("button", { text: "Refresh list", onclick: () => loadLogs() });
    el.append(
      h("div", { class: "toolbar" }, label, start, stop),
      info,
      h("div", { class: "toolbar" }, noteIn, noteBtn),
      msg,
      h("div", { class: "sect-title" }, "Recordings (newest first) ", refresh),
      list,
    );

    async function run(op, payload = {}) {
      try {
        await ctx.api.request(op, payload);
        msg.textContent = "";
        if (op === "rec.stop" || op === "rec.start") setTimeout(loadLogs, 700);
        return true;
      } catch (e) {
        msg.textContent = e.code === "not_implemented" ? "Recording is not available in this core." : `${op}: ${e.message}`;
        return false;
      }
    }

    async function loadLogs() {
      try {
        const logs = await ctx.api.request("logs.list");
        renderLogs(logs);
      } catch (e) {
        list.replaceChildren(h("div", { class: "hint", text: e.code === "not_implemented" ? "Log library not available." : `logs.list: ${e.message}` }));
      }
    }

    function renderLogs(logs) {
      if (!logs.length) {
        list.replaceChildren(h("div", { class: "hint", text: "No recordings yet." }));
        return;
      }
      const table = h("table", { class: "grid logs" }, h("tr", {}, ...["name", "started", "length", "size", "sources", "sim", ""].map((t) => h("th", { text: t }))));
      for (const l of logs) {
        const open = h("button", { text: "Open", onclick: () => openLog(l.name) });
        table.append(
          h(
            "tr",
            { class: l.complete ? "" : "incomplete" },
            h("td", { text: l.name + (l.label ? ` (${l.label})` : "") + (l.complete ? "" : " [incomplete]") }),
            h("td", { text: fmtDateTime(l.started) }),
            h("td", { text: l.duration_s == null ? "—" : fmtClock(l.duration_s) }),
            h("td", { text: fmtBytes(l.size) }),
            h("td", { text: (l.sources || []).join(", ") }),
            h("td", { text: l.sim == null ? "—" : l.sim ? "sim" : "real" }),
            h("td", {}, open),
          ),
        );
      }
      list.replaceChildren(table);
    }

    async function openLog(name) {
      try {
        await ctx.api.request("replay.open", { name }, 60000);
        msg.textContent = "";
        ctx.switchView("replay");
      } catch (e) {
        msg.textContent = e.code === "not_implemented" ? "Replay is not available in this core." : `replay.open: ${e.message}`;
      }
    }

    function render() {
      const st = ctx.status();
      const rec = st && st.recorder;
      if (!rec || rec.available === false) {
        info.textContent = "Recorder not available in this core.";
        start.disabled = stop.disabled = noteBtn.disabled = true;
        return;
      }
      start.disabled = rec.active;
      stop.disabled = !rec.active;
      noteBtn.disabled = !rec.active;
      const dur = rec.active && rec.started && st.clock ? fmtClock(st.clock - rec.started) : "";
      info.replaceChildren(
        h("div", { class: rec.active ? "recording" : "" , text: rec.active ? `REC ${rec.name || ""}  ${dur}` : "Not recording (manual start only)" }),
        h("div", { text: `packets ${rec.packets ?? 0}   size ${fmtBytes(rec.bytes)}   free disk ${fmtBytes(rec.free_bytes)}` }),
        ...(rec.error ? [h("div", { class: "err", text: `recorder error: ${rec.error}` })] : []),
      );
    }
    loadLogs();
    return {
      onStatus: render,
      onTick: render,
      onReady: loadLogs,
    };
  },
});
