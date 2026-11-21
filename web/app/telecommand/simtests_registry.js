// Registry of 'Simulation testing' sections. A section is one file that calls registerSimSection(); the page
// (panels/simtests.js) mounts every registered section in order. Add a simulation test = add a section here.
// def: {id, title, order?, create(el, ctx) -> {onReady, onTelecommands, onStatus, onTick, onViewChange, destroy}}
const sections = new Map();

export function registerSimSection(def) {
  if (!def || !def.id || typeof def.create !== "function") throw new Error("registerSimSection: id and create() required");
  if (sections.has(def.id)) throw new Error(`sim test section ${def.id} registered twice`);
  sections.set(def.id, { title: def.id, order: 100, ...def });
}

export const simSections = () => [...sections.values()].sort((a, b) => a.order - b.order);
