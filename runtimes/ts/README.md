# diaspore-ts

Diaspore's TypeScript sandbox runtime executes JSON, gzip, and DSPB bundles
with no runtime npm dependencies. The npm package includes compiled JavaScript
and the self-hosted compiler. See the
[language specification](https://github.com/radkawar/diaspore/blob/main/docs/spec.md).

## Requirements

Node >= 22 for the npm package. Running TypeScript sources directly requires
Node >= 23.6.

## Usage

Install the package, then use its CLI:

```sh
npm install diaspore
npx diaspore-ts build entry.py -o entry.dsp.json
npx diaspore-ts run entry.dsp.json --max-steps 1000000
npx diaspore-ts run entry.dsp.json --durable --snapshot run.flow.json
npx diaspore-ts resume entry.dsp.json run.flow.json --send '{"approved": true}'
```

Builds accept repeatable `--path DIR` options to search additional module
directories in order, for example
`build entry.py --path lib --path vendor -o entry.dsp.json`. The embedded
compiler receives read access to these directories.

The CLI grants no filesystem, network, or process capabilities by default.
Durable suspension exits with code 7.

## Embedding

Compile a module that imports `host` with
`uv run diaspore build plugin.py --binding host -o plugin.dsp.json`. For a
`price(order)` function that calls `host.lookup(order["sku"])`:

```ts
import { readFileSync } from "node:fs";
import { Interp, loadBundle } from "diaspore";

const bundle = loadBundle(readFileSync("plugin.dsp.json"));
const vm = new Interp(bundle, {
  bindings: { host: { lookup: (sku) => sku === "book" ? 12 : 0 } },
  maxSteps: 1000000,
});
if (vm.run() !== 0) throw new Error("plugin initialization failed");
console.log(vm.call("plugin", "price", { sku: "book" }));
```

Bindings are synchronous. Host errors become catchable sandbox `RuntimeError`
exceptions; uncaught sandbox calls throw `DiasporeError`. Lists and string-keyed
dictionaries cross the boundary as arrays and plain objects. Safe integral
numbers become sandbox integers; large integers use `bigint`. Null and undefined
become `None`. Durable mode journals successful binding results and reuses them
during replay.

## Differences

- `net` and `tasks` functions raise `RuntimeError`.
- Random sequences use a runtime-specific algorithm.
- Deep embedded calls may require Node's `--stack-size=6000`; the CLI sets this
  automatically.

## Development

From a repository checkout, generate the compiler bundle with `make generate`
(requires Python and uv), then run:

```sh
npm install --prefix runtimes/ts && npm run check --prefix runtimes/ts
npm pack ./runtimes/ts --pack-destination runtimes/ts
uv run pytest -q tests/test_corpus.py -k ts_vm
node runtimes/ts/src/cli.ts selftest-build
```

Packing compiles the sources to `dist/` and includes the generated compiler
bundle and license. It fails if the compiler bundle is missing. The self-test
compares compiler output with the Python compiler and checks DSPB encoding
and decoding.
