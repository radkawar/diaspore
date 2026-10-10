"""Browser worker identity checks and compile option forwarding."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from .conftest import ROOT


def _node(script: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    result = subprocess.run(
        [node, "--experimental-vm-modules", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_browser_compile_options_and_package_overlay() -> None:
    _node(r"""
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createContext, SourceTextModule} from 'node:vm';
const id = 'sha256:' + 'a'.repeat(64);
let worker;
class Worker {
  constructor(url) { worker = this; this.url = url; }
  postMessage(request) {
    this.request = request;
    this.onmessage({data: {id: request.id, result: {code: 0}}});
  }
}
const context = createContext({Worker, URL, URLSearchParams,
  diasporeBuild: 'build-version', diasporeStdlib: id});
const module = new SourceTextModule(readFileSync('site/js/runtime.js', 'utf8'), {
  context, initializeImportMeta(meta) { meta.url = 'https://site.test/js/runtime.js'; },
});
await module.link(() => { throw Error('Unexpected import'); });
await module.evaluate();
assert.equal(worker.url.searchParams.get('stdlib'), id);
assert.equal(worker.url.searchParams.get('v'), 'build-version');
worker.onmessage({data: {ready: true}});
const files = {'/site-packages/copy.py': 'value = 42\n'};
await module.namespace.compile('import copy', files);
assert.equal(worker.request.method, 'compile');
assert.equal(worker.request.args[0], 'import copy');
assert.deepEqual(JSON.parse(worker.request.args[1]), files);
assert.deepEqual(JSON.parse(worker.request.args[2]), {self_contained: false});
await module.namespace.compile('import copy', files, {self_contained: true});
assert.deepEqual(JSON.parse(worker.request.args[2]), {self_contained: true});
""")


def test_browser_worker_rejects_missing_or_mismatched_store_identity() -> None:
    _node(r"""
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createContext, SourceTextModule} from 'node:vm';
const source = readFileSync('site/js/wasm-worker.js', 'utf8');
const installed = 'sha256:' + 'a'.repeat(64);
async function boot(expected) {
  let finish;
  const message = new Promise(resolve => { finish = resolve; });
  const downloads = [];
  const context = createContext({URL, URLSearchParams, onmessage: null,
    postMessage: finish,
    fetch: async url => {
      downloads.push(url.pathname);
      return {ok: true, arrayBuffer: async () => new ArrayBuffer(0)};
    },
    WebAssembly: {instantiate: async () => ({instance: {}})},
    Go: class {
      run() {
        for (const name of ['Compile', 'Run', 'Repl', 'ReplReset', 'Trace']) {
          context['diaspore' + name] = () => ({code: 0});
        }
        context.diasporeStdlibId = () => installed;
        return new Promise(() => {});
      }
    },
  });
  context.self = context;
  const module = new SourceTextModule(source, {context,
    initializeImportMeta(meta) {
      const url = new URL('https://site.test/js/wasm-worker.js?v=test');
      if (expected !== null) url.searchParams.set('stdlib', expected);
      meta.url = url.href;
    },
    importModuleDynamically: async specifier => {
      assert.equal(new URL(specifier).pathname, '/wasm_exec.js');
      const dependency = new SourceTextModule('', {context});
      await dependency.link(() => {});
      await dependency.evaluate();
      return dependency;
    },
  });
  await module.link(() => { throw Error('Unexpected import'); });
  await module.evaluate();
  const result = await message;
  assert.deepEqual(downloads, ['/diaspore.wasm']);
  return result;
}
assert.equal((await boot(installed)).ready, true);
const required = 'sha256:' + 'b'.repeat(64);
const mismatch = await boot(required);
assert.equal(mismatch.ready, undefined);
assert.match(mismatch.error, /Stdlib mismatch/);
assert.ok(mismatch.error.includes(required));
assert.ok(mismatch.error.includes(installed));
const missing = await boot(null);
assert.equal(missing.ready, undefined);
assert.match(missing.error, /missing a valid stdlib identity/);
""")
