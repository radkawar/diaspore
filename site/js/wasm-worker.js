const version = new URL(import.meta.url).search;
(async () => {
  try {
    await import(new URL(`../wasm_exec.js${version}`, import.meta.url).href);
    const go = new Go();
    const response = await fetch(new URL(`../diaspore.wasm${version}`, import.meta.url));
    if (!response.ok) throw new Error(`Runtime download failed (${response.status})`);
    const {instance} = await WebAssembly.instantiate(await response.arrayBuffer(), go.importObject);
    go.run(instance).catch(error => postMessage({error: error.message}));
    if (![self.diasporeCompile, self.diasporeRun, self.diasporeRepl, self.diasporeReplReset].every(fn => typeof fn === 'function')) {
      throw new Error('The browser runtime is out of date. Rebuild the site and reload.');
    }
    postMessage({ready: true});
  } catch (error) {
    postMessage({error: `${error.message}. Build the site with make site.`});
  }
})();
onmessage = ({data: {id, method, args}}) => {
  try {
    const methods = {compile: self.diasporeCompile, run: self.diasporeRun, repl: self.diasporeRepl, reset: self.diasporeReplReset, trace: self.diasporeTrace};
    if (!methods[method]) throw new Error('This runtime does not support the requested action. Reload to update it.');
    postMessage({id, result: methods[method](...args)});
  } catch (error) {
    postMessage({id, error: error.message});
  }
};
