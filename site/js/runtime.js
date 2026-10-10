const workerURL = new URL('./wasm-worker.js', import.meta.url);
workerURL.search = new URLSearchParams({v: globalThis.diasporeBuild, stdlib: globalThis.diasporeStdlib});
const worker = new Worker(workerURL, {type: 'module'});
let sequence = 0;
const pending = new Map();
let resolveReady, rejectReady;
export const ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
export let isReady = false;
worker.onmessage = ({data}) => {
  if (data.ready) { isReady = true; resolveReady(); return; }
  if (data.error && data.id === undefined) {
    isReady = false;
    const error = new Error(data.error);
    rejectReady(error);
    for (const request of pending.values()) request.reject(error);
    pending.clear();
    return;
  }
  const request = pending.get(data.id);
  if (!request) return;
  pending.delete(data.id);
  if (data.error) request.reject(new Error(data.error));
  else request.resolve(data.result);
};
worker.onerror = event => {
  isReady = false;
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

/** Compile user/package sources; self_contained embeds reachable stdlib modules. */
export function compile(source, files = {}, {self_contained = false} = {}) {
  return invoke('compile', source, JSON.stringify(files), JSON.stringify({self_contained}));
}
