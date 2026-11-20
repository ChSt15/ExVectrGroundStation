import { registerPanel } from "./registry.js";
import { Bindings, h, section } from "../ui.js";
import { fmtAge } from "../fmt.js";
import { POWER_LIMIT_DBM, describeSource, powerReport, powerTone } from "../link/link_logic.js";
import { confirmDialog } from "../radio/radio_widget.js";

const STATE_TONE = { connected: "ok", searching: "warn", error: "bad", disabled: "" };

registerPanel({
  id: "link",
  title: "Radio link",
  consumes: ["VehicleStatus", "LinkStats", "RadioLinkStats", "LinkConfig", "TxModuleStats", "RxModuleStats"],
  create(el, ctx) {
    let b = new Bindings(ctx);
    let key = "";
    let blocks = [];
    const srcBox = h("div", { class: "sources" });
    // Source switches (testing feature, SAFETY-RELEVANT: disabling is a true disconnect in the core: the port is
    // closed, nothing is received or sent through it, in-flight requests are aborted). The state shown is the core's.
    const swBox = h("div", { class: "src-switches", "data-testid": "source-switches" });
    const swMsg = h("div", { class: "msg" });
    const swSect = section(el, "Sources (on / off)");
    swSect.append(swBox, swMsg, h("div", { class: "hint", text: "Off closes the port: nothing is received or sent through that source (no RC, requests, commands or heartbeat). Not kept over a core restart." }));
    let swKey = "";
    const swRows = new Map();
    async function setEnabled(s, on) {
      if (!on) {
        const ok = await confirmDialog(`Disconnect source ${s.id}? Its port is closed: nothing is received from or sent to it (RC state, requests and commands through it stop) until you switch it on again.`, `Disconnect ${s.id}`, "Cancel");
        if (!ok) return;
      }
      try {
        await ctx.api.request("sources.enable", { source: s.id, enabled: on }, 10000);
        swMsg.textContent = "";
      } catch (e) {
        swMsg.textContent = `sources.enable: ${e.message}`;
      }
    }
    function renderSwitches(sources) {
      const k = sources.map((s) => s.id).join(",");
      if (k !== swKey) {
        swKey = k;
        swRows.clear();
        swBox.replaceChildren();
        for (const s of sources) {
          const sw = h("button", { class: "src-sw", role: "switch", "data-source": s.id, "aria-label": `source ${s.id} on or off` }, h("span", { class: "knob" }));
          const state = h("span", { class: "src-state" });
          const rate = b2.cell({ type: "LinkStats", src: s.id, field: "rx_frames_s", unit: "frames/s", digits: 0 }, { showSrc: false, class: "noage" });
          swBox.append(h("div", { class: "src-row", "data-source": s.id }, sw, h("b", { text: s.id }), h("span", { class: "dim", text: s.kind }), state, rate));
          sw.addEventListener("click", () => {
            const cur = (ctx.status()?.sources || []).find((x) => x.id === s.id);
            if (cur) setEnabled(cur, !cur.enabled);
          });
          swRows.set(s.id, { sw, state });
        }
      }
      const opts = { view: ctx.view(), connState: ctx.api.state, canControl: !!(ctx.api.welcome && ctx.api.welcome.can_control) };
      for (const s of sources) {
        const r = swRows.get(s.id);
        const d = describeSource(s, opts);
        r.sw.setAttribute("aria-checked", String(d.on));
        r.sw.disabled = !d.usable;
        r.sw.title = d.usable ? (d.on ? "switch off (disconnect)" : "switch on (connect)") : d.why;
        r.state.textContent = d.state;
        r.state.dataset.tone = d.tone;
      }
      b2.update();
    }
    const b2 = new Bindings(ctx);
    const radio = section(el, "Radio (Router)");
    const rb = new Bindings(ctx);
    const R = "RadioLinkStats";
    rb.row(radio, "RSSI (ant 1 / 2)", { type: R, field: "rssi_dbm", kind: "vec", unit: "dBm", digits: 0 });
    rb.row(radio, "Link quality", { type: R, field: "lq_pct", unit: "%", digits: 0 });
    rb.row(radio, "SNR", { type: R, field: "snr_db", unit: "dB", digits: 0 });
    rb.row(radio, "Up / down rate", { type: R, kind: "vec", unit: "Hz", digits: 1, derive: (e) => (e.f.up_hz === undefined && e.f.down_hz === undefined ? undefined : [e.f.up_hz ?? null, e.f.down_hz ?? null]) });
    rb.row(radio, "Active antenna", { type: R, field: "antenna", digits: 0 });
    rb.row(radio, "Ship radio node heard on vehicle UART", { type: "VehicleStatus", field: "uart_peer_seen", kind: "text", format: (v) => (v === true ? "seen" : v === false ? "lost" : "n/s") }, { tone: (d) => (d.state === "fresh" && d.value === false ? "warn" : "") });
    const power = section(el, `Transmit power (legal limit ${POWER_LIMIT_DBM} dBm = 10 mW)`);
    const pb = new Bindings(ctx);
    const warnBox = h("div", { class: "pw-warnings", role: "alert" });
    pb.row(power, "Router power commanded (LINK_CONFIG)", { type: "LinkConfig", field: "tx_power_dbm", unit: "dBm", digits: 0 }, { tone: (d) => powerTone(d.value) });
    const cfgNote = h("div", { class: "hint", "data-testid": "pw-cfg-note" });
    pb.row(power, "Router power applied (its LINK_STATS)", { type: "TxModuleStats", field: "tx_power", unit: "dBm", digits: 0 }, { tone: (d) => powerTone(d.value) });
    pb.row(power, "Ship radio node power applied (its LINK_STATS)", { type: "RxModuleStats", field: "tx_power", unit: "dBm", digits: 0 }, { tone: (d) => powerTone(d.value) });
    power.append(cfgNote, warnBox);
    el.append(srcBox, radio);

    function rebuild(sources) {
      b = new Bindings(ctx);
      blocks = [];
      srcBox.replaceChildren();
      for (const s of sources) {
        const sec = section(srcBox, `Source ${s.id} (${s.kind})`);
        const L = (field, extra = {}) => ({ type: "LinkStats", src: s.id, field, ...extra });
        const stateEl = h("span", { class: "val" });
        const head = h("div", { class: "row" }, h("span", { class: "lbl", text: "State" }), stateEl);
        sec.append(head);
        blocks.push({ s, stateEl, id: s.id });
        b.row(sec, "Receive rate (serial hop)", L("rx_frames_s", { unit: "frames/s", digits: 0 }));
        b.row(sec, "Receive data rate", L("rx_bytes_s", { unit: "B/s", digits: 0 }));
        b.row(sec, "Transmit rate (serial hop)", L("tx_frames_s", { unit: "frames/s", digits: 0 }));
        b.row(sec, "CRC errors", L("crc_errors", { digits: 0 }));
        b.row(sec, "Decode errors", L("decode_errors", { digits: 0 }));
        b.row(sec, "Reconnects", L("reconnects", { digits: 0 }));
      }
    }

    function render() {
      const status = ctx.status();
      const sources = status ? status.sources : [];
      renderSwitches(sources);
      const k = sources.map((s) => `${s.id}/${s.kind}`).join(",");
      if (k !== key) {
        key = k;
        rebuild(sources);
      }
      const now = status ? status.clock : null;
      for (const blk of blocks) {
        const s = sources.find((x) => x.id === blk.id);
        if (!s) continue;
        const since = s.since && now ? ` for ${fmtAge(now - s.since)}` : "";
        blk.stateEl.textContent = `${s.state}${s.state === "connected" ? since : ""}${s.device ? `  ${s.device}` : ""}${s.last_error ? `  (${s.last_error})` : ""}`;
        blk.stateEl.dataset.tone = STATE_TONE[s.state] || "";
        blk.stateEl.dataset.state = "fresh";
      }
      b.update();
      rb.update();
      pb.update();
      const val = (type) => {
        const d = pb.describeSpec({ type, field: type === "LinkConfig" ? "tx_power_dbm" : "tx_power" });
        return d.state === "fresh" ? d.value : undefined;
      };
      const rep = powerReport({ cmd: val("LinkConfig"), router: val("TxModuleStats"), ship: val("RxModuleStats") });
      warnBox.replaceChildren(...rep.warnings.map((w) => h("div", { class: "pw-warn", "data-tone": w.tone, text: w.text })));
      power.dataset.alarm = rep.over ? "bad" : rep.differs ? "warn" : "";
      alarm = power.dataset.alarm;
      cfgNote.textContent = pb.describeSpec({ type: "LinkConfig", field: "tx_power_dbm" }).state === "missing" ? "LINK_CONFIG not received: the Router sends it to the ship radio node and drops it from USB, so it is shown only when a source carries it." : "";
    }
    let alarm = "";
    return {
      alert: () => alarm, // dock tab marker
      onTick: render,
      onStatus: render,
      onViewChange: render,
      freshness: () => rb.freshness(),
    };
  },
});
