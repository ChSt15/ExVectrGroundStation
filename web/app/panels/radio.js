import { registerPanel } from "./registry.js";
import { createRadioWidget } from "../radio/radio_widget.js";

// Virtual radio (design 5.6, task T9). SAFETY-RELEVANT: see radio/radio_widget.js and docs/radio.md.
registerPanel({
  id: "radio",
  title: "Virtual radio",
  inputs: true,
  consumes: ["RcState"], // the recorded RcState is what the widget shows in the replay view
  create(el, ctx) {
    const inst = createRadioWidget(el, ctx);
    (window.exgs ??= {}).radio = inst;
    return inst;
  },
});
