const worker = new Worker(new URL(`./wasm-worker.js?v=${globalThis.diasporeBuild}`, import.meta.url), {type: 'module'});
let sequence = 0;
const pending = new Map();
let resolveReady, rejectReady;
export const ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
worker.onmessage = ({data}) => {
  if (data.ready) { resolveReady(); return; }
  if (data.error && data.id === undefined) { rejectReady(new Error(data.error)); return; }
  const request = pending.get(data.id);
  if (!request) return;
  pending.delete(data.id);
  if (data.error) request.reject(new Error(data.error));
  else request.resolve(data.result);
};
worker.onerror = event => {
  const error = new Error(event.message || 'WebAssembly worker failed');
  rejectReady(error);
  for (const request of pending.values()) request.reject(error);
  pending.clear();
};
export async function invoke(method, ...args) {
  await ready;
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, {resolve, reject});
    worker.postMessage({id, method, args});
  });
}
