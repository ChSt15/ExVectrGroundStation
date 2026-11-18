// Panel registry (design 5.3). A panel is one file in this folder that calls registerPanel(),
// plus an entry in the layout JSON.
const panels = new Map();

export function registerPanel(def) {
  if (!def || !def.id || typeof def.create !== "function") throw new Error("registerPanel: id and create() required");
  if (panels.has(def.id)) throw new Error(`panel ${def.id} registered twice`);
  panels.set(def.id, {
    title: def.id,
    consumes: [],
    mode: "latest",
    views: null, // null = both views; ["replay"] = only shown in the replay view
    ...def,
  });
}

export const getPanel = (id) => panels.get(id);
export const allPanels = () => [...panels.values()];
