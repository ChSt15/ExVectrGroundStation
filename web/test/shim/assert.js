// Minimal stand-in for node:assert/strict (browser test runner only).
function fail(msg, a, b) {
  throw new Error(`${msg || "assertion failed"}${b !== undefined ? ` (${JSON.stringify(a)} vs ${JSON.stringify(b)})` : ""}`);
}
const same = (a, b) => {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => same(a[k], b[k]));
};
const assert = (v, m) => (v ? undefined : fail(m || "expected truthy"));
assert.ok = assert;
assert.equal = assert.strictEqual = (a, b, m) => (Object.is(a, b) ? undefined : fail(m || "not equal", a, b));
assert.notEqual = assert.notStrictEqual = (a, b, m) => (!Object.is(a, b) ? undefined : fail(m || "equal", a, b));
assert.deepEqual = assert.deepStrictEqual = (a, b, m) => (same(a, b) ? undefined : fail(m || "not deep equal", a, b));
assert.match = (s, re, m) => (re.test(s) ? undefined : fail(m || "no match", s, String(re)));
assert.throws = (fn, m) => {
  try {
    fn();
  } catch (e) {
    return;
  }
  fail(m || "expected throw");
};
export default assert;
