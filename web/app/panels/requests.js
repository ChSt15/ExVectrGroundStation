import { registerPanel } from "./registry.js";
import { createRequestsPanel } from "../requests/requests_panel.js";

// Whitelisted one-shot requests (docs/requests.md). SAFETY-RELEVANT: see requests/requests_panel.js.
registerPanel({
  id: "requests",
  title: "Requests",
  consumes: [],
  inputs: true, // locked in the replay view: the panel disables every button and says why (nothing is ever sent)
  create(el, ctx) {
    const inst = createRequestsPanel(el, ctx);
    (window.exgs ??= {}).requests = inst;
    return inst;
  },
});
