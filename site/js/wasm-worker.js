const params = new URL(import.meta.url).searchParams;
const version = `?${new URLSearchParams({v: params.get('v')})}`;
const expectedStdlib = params.get('stdlib');
(async () => {
  try {
    const [, response] = await Promise.all([
      import(new URL(`../wasm_exec.js${version}`, import.meta.url).href),
      fetch(new URL(`../diaspore.wasm${version}`, import.meta.url)),
    ]);
    const go = new Go();
    if (!response.ok) throw new Error(`Runtime download failed (${response.status})`);
    const {instance} = await WebAssembly.instantiate(await response.arrayBuffer(), go.importObject);
    go.run(instance).catch(error => postMessage({error: error.message}));
    if (![self.diasporeCompile, self.diasporeRun, self.diasporeRepl, self.diasporeReplReset, self.diasporeTrace, self.diasporeStdlibId].every(fn => typeof fn === 'function')) {
      throw new Error('The browser runtime is out of date. Rebuild the site and reload.');
    }
    if (!/^sha256:[0-9a-f]{64}$/.test(expectedStdlib || '')) {
      throw new Error('Site manifest is missing a valid stdlib identity. Rebuild the site and reload');
    }
    const runtimeStdlib = self.diasporeStdlibId();
    if (runtimeStdlib !== expectedStdlib) {
      throw new Error(`Stdlib mismatch: site requires ${expectedStdlib}, runtime provides ${runtimeStdlib}. Rebuild the site and reload`);
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
