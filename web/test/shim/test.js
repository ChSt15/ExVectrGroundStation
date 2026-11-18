// Minimal stand-in for node:test, used only when the tests run in a browser (tools/run_js_tests.py)
// because no node binary is installed. Supports test(name, fn) with sync or async fn.
const queue = [];
export function test(name, fn) {
  queue.push({ name, fn });
}
export default test;
export async function runAll() {
  const results = [];
  for (const { name, fn } of queue) {
    try {
      await fn();
      results.push({ name, ok: true });
    } catch (e) {
      results.push({ name, ok: false, error: String((e && e.stack) || e) });
    }
  }
  return results;
}
