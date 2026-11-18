import { registerPanel } from "./registry.js";
import { Bindings, h, section } from "../ui.js";
import { fmtAge } from "../fmt.js";

const STATE_TONE = { connected: "ok", searching: "warn", error: "bad", disabled: "" };

registerPanel({
  id: "link",
  title: "Radio link",
  consumes: ["LinkStats", "RadioLinkStats"],
  create(el, ctx) {
    let b = new Bindings(ctx);
    let key = "";
    let blocks = [];
    const srcBox = h("div", { class: "sources" });
    const radio = section(el, "Radio (TX module)");
    const rb = new Bindings(ctx);
    const R = "RadioLinkStats";
    rb.row(radio, "RSSI (ant 1 / 2)", { type: R, field: "rssi_dbm", kind: "vec", unit: "dBm", digits: 0 });
    rb.row(radio, "Link quality", { type: R, field: "lq_pct", unit: "%", digits: 0 });
    rb.row(radio, "SNR", { type: R, field: "snr_db", unit: "dB", digits: 0 });
    rb.row(radio, "Up / down rate", { type: R, kind: "vec", unit: "Hz", digits: 1, derive: (e) => (e.f.up_hz === undefined && e.f.down_hz === undefined ? undefined : [e.f.up_hz ?? null, e.f.down_hz ?? null]) });
    rb.row(radio, "Active antenna", { type: R, field: "antenna", digits: 0 });
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
        b.row(sec, "Receive rate", L("rx_frames_s", { unit: "pkt/s", digits: 0 }));
        b.row(sec, "Receive data rate", L("rx_bytes_s", { unit: "B/s", digits: 0 }));
        b.row(sec, "Transmit rate", L("tx_frames_s", { unit: "pkt/s", digits: 0 }));
        b.row(sec, "CRC errors", L("crc_errors", { digits: 0 }));
        b.row(sec, "Decode errors", L("decode_errors", { digits: 0 }));
        b.row(sec, "Reconnects", L("reconnects", { digits: 0 }));
      }
    }

    function render() {
      const status = ctx.status();
      const sources = status ? status.sources : [];
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
    }
    return {
      onTick: render,
      onStatus: render,
      onViewChange: render,
      freshness: () => rb.freshness(),
    };
  },
});
