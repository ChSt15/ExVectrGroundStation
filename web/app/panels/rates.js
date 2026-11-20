import { registerPanel } from "./registry.js";
import { Bindings, h, section } from "../ui.js";
import { PATH_HINT, describeRates } from "../rates/rates_logic.js";

registerPanel({
  id: "rates",
  title: "Data rates",
  consumes: ["RadioLinkStats"],
  create(el, ctx) {
    const note = h("div", { class: "hint", "data-testid": "rates-note" });
    const bar = h("div", { class: "rt-bar", "data-testid": "air-bar", role: "meter", "aria-valuemin": "0", "aria-valuemax": "100" }, h("div", { class: "rt-fill" }), h("span", { class: "rt-bar-t" }));
    const airSec = section(el, "Air path utilisation");
    airSec.append(bar);
    const rb = new Bindings(ctx);
    rb.row(airSec, "Router LINK_STATS: air packets up / down", { type: "RadioLinkStats", kind: "vec", unit: "Hz", digits: 1, derive: (e) => (e.f.up_hz === undefined && e.f.down_hz === undefined ? undefined : [e.f.up_hz ?? null, e.f.down_hz ?? null]) });
    rb.row(airSec, "Router link quality", { type: "RadioLinkStats", field: "lq_pct", unit: "%", digits: 0 });
    const tbl = section(el, "Streams");
    const head = h("tr", {}, ...["Type", "Path", "Source", "Hz (2 s)", "Hz (10 s)", "Nominal Hz", "Bytes/s", "Age"].map((t) => h("th", { text: t })));
    const body = h("tbody");
    tbl.append(h("table", { class: "rt-table", "data-testid": "rates-table" }, h("thead", {}, head), body), note);
    let last = null;

    function render() {
      const data = ctx.rates();
      if (data === last && body.childElementCount) return;
      last = data;
      const d = describeRates(data);
      const air = d.air;
      bar.dataset.tone = air.tone;
      bar.dataset.known = String(air.known);
      bar.querySelector(".rt-fill").style.width = `${Math.min(100, air.frac * 100)}%`;
      bar.querySelector(".rt-bar-t").textContent = air.known ? air.long : "no air payload bytes measured (replay has no byte counts, or no radio source)";
      bar.setAttribute("aria-valuenow", String(Math.round(air.frac * 100)));
      const rows = [];
      let path = null;
      const flush = () => {
        const p = d.paths.find((x) => x.path === path);
        if (!p) return;
        rows.push(
          h("tr", { class: "total", "data-testid": "rates-total", "data-path": p.path, title: `${p.streams} streams; payload ${p.payloadText}` },
            h("td", { text: "total" }), h("td", { text: p.path }), h("td", { text: "" }), h("td", { class: "num", text: p.rateText }), h("td"), h("td"),
            h("td", { class: "num", text: p.bytesText }), h("td")),
        );
      };
      for (const r of d.rows) {
        if (r.path !== path) {
          if (path !== null) flush();
          path = r.path;
        }
        rows.push(
          h("tr", { "data-testid": "rates-row", "data-type": r.type, "data-path": r.path, "data-source": r.source, "data-state": r.state, "data-tone": r.tone, title: r.title || PATH_HINT[r.path] || "" },
            h("td", { text: r.type }), h("td", { class: "path", text: r.path }), h("td", { text: r.node === null || r.node === undefined ? r.source : `${r.source} (node ${r.node})` }),
            h("td", { class: "num", text: r.rateText }), h("td", { class: "num", text: r.rate10Text }), h("td", { class: "num", text: r.nominalText }),
            h("td", { class: "num", text: r.bytesText }), h("td", { class: "num", text: r.ageText })),
        );
      }
      if (path !== null) flush();
      body.replaceChildren(...rows);
      note.textContent = d.rows.length
        ? d.measure === "packets"
          ? "Replay: packets per second in replay time, computed from the recording (a log holds no wire frames, so no byte counts; one wire frame can give several packet types). Greyed: not seen recently."
          : "Frames per second and wire bytes per second (framing, CRC and header included) measured on receive; averaged over 2 s and 10 s. Greyed: not seen recently. Orange: well below the schema's nominal rate (rate_air on the air path, rate_usb elsewhere)."
        : "Nothing received yet.";
    }
    return { onTick: () => { render(); rb.update(); }, onRates: render, onViewChange: render, freshness: () => rb.freshness() };
  },
});
