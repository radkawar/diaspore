# diaspore-ts

Diaspore's TypeScript sandbox runtime executes JSON, gzip, and DSPB bundles
with no runtime npm dependencies. The npm package includes compiled JavaScript
and the self-hosted compiler with a content-addressed compiled standard library. See the
[language specification](https://github.com/radkawar/diaspore/blob/main/docs/spec.md).

The pinned CPython `json` package uses the private native `_json` accelerator;
its public Python API remains upstream code. Python strings preserve explicit
surrogate code points separately from non-BMP Unicode scalars, including in
JSON and DSPB bundle transport.

User-defined classes keep slot storage separate from optional instance
dictionaries, including inherited and private slots. Default object/set
reduction uses that real layout and honors state and constructor hooks.
Float metadata, hexadecimal conversions, and finite numeric hashing use
IEEE 754 binary64, including nearest-even rounding at normal and subnormal
boundaries. Native audit hooks are interpreter-local; `sys.audit` delivers
events synchronously and preserves hook exceptions and registration vetoes.
Native exception hooks render sandbox tracebacks, exception chains and notes
through the current `sys.stderr`. Uncaught exceptions honor `sys.excepthook`;
`sys.__excepthook__` retains the default hook. Built-in exception state is
stored separately from user instance dictionaries. Traceback source lines use
bundled debug metadata, never ambient host source files.

Native `math` provides integer combinatorics and binary64 operations using
Python numeric conversion protocols; integer loops and iterable reductions
consume the interpreter's fuel budget.
`time.perf_counter()` uses the same monotonic clock as `time.monotonic()`,
including the shared virtual clock in deterministic mode.
Private incremental hash engines accelerate MD5, SHA-1, and SHA-2 compression;
the shipped Python `hashlib` providers still own their public APIs.

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

Builds link the shipped standard library by default and record its exact content
ID. Imports execute lazily, with program modules taking precedence. A mismatched
runtime store is rejected before execution. Use `--self-contained` to embed
reachable standard modules and omit the reference; bundles without a reference
never silently fall back to the shipped store. Durable snapshots retain and
check the same reference when resumed.
`--no-stdlib` disables standard-library resolution and omits the reference.

The CLI grants no filesystem, network, or process capabilities by default.
Durable suspension exits with code 7.

## Embedding

The exported `compileFile("entry.py")` API also links by default. Pass
`{ selfContained: true }` to embed reachable standard modules, or
`{ stdlib: false }` to disable standard-library resolution. Embedders can supply
a validated compiled `StdlibStore` through `Interp`'s `stdlibStore` option;
the bundle must request that exact store ID to resolve its modules.

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
try {
  if (vm.run() !== 0) throw new Error("plugin initialization failed");
  console.log(vm.call("plugin", "price", { sku: "book" }));
} finally {
  vm.close();
}
```

Bindings are synchronous. Host errors become catchable sandbox `RuntimeError`
exceptions; uncaught sandbox calls throw `DiasporeError`. Lists and string-keyed
dictionaries cross the boundary as arrays and plain objects. Safe integral
numbers become sandbox integers; large integers use `bigint`. Null and undefined
become `None`. Durable mode journals successful binding results and reuses them
during replay.

## Differences

- `tasks` is unavailable; importing it raises `ModuleNotFoundError`.
  Portable `_thread` locks support uncontended single-thread use.
  `net` functions raise `RuntimeError`.
- Regex LOCALE/TEMPLATE/DEBUG, conditional groups, named Unicode escapes, and
  variable-width or backreference-dependent lookbehind are unsupported.
- Memoryview export cleanup follows host garbage collection unless explicitly
  released; it does not promise CPython reference-counting timing.
- `isidentifier`, `isprintable`, `isdecimal`, and string repr use Unicode 15 data.
  Other character predicates and case conversion follow the host's Unicode
  version. `isdigit` recognizes decimal digits only, not superscript digits.
- NaN values are JavaScript numbers, so their hashes do not preserve CPython's
  distinct per-object NaN identity.
- `sys.int_info` describes V8's pointer-sized BigInt digits, not CPython's
  30-bit digits. Decimal integer conversions have no configured digit limit.
  `sys.hash_info` reports the runtime's unseeded, sign-masked FNV-1a string-key
  hashing rather than CPython's randomized SipHash; numeric hashing uses
  the signed 64-bit, $2^{61}-1$ modulus model.
- Native `math` omits hyperbolic functions, `cbrt`, `exp2`, `erf`, `erfc`, and
  `isqrt`. Elementary transcendental rounding follows the host JavaScript
  math implementation.
- Introspection describes sandbox execution, not host JavaScript or CPython
  bytecode. Shared provider restrictions are in spec sections 11.10 and 12.
- Native `open` supports UTF-8 text only; upstream `io` uses the portable
  codec providers and capability-checked private file descriptors.
- Deep embedded calls may require Node's `--stack-size=6000`; the CLI sets this
  automatically.

## Development

From a repository checkout, generate the compiler bundle and stdlib store with
`make generate` (see [source-build requirements](../../README.md#getting-started)),
then run:

```sh
npm install --prefix runtimes/ts && npm run check --prefix runtimes/ts
npm pack ./runtimes/ts --pack-destination runtimes/ts
uv run --python 3.12 pytest -q tests/test_corpus.py -k ts_vm
node runtimes/ts/src/cli.ts selftest-build
```

Packing compiles the sources to `dist/` and includes the generated compiler,
compiled standard-library store, and MIT/PSF/Unicode licenses and source manifest.
It fails if required generated artifacts are missing. The self-test compares
compiler output with the Python compiler and checks DSPB encoding and decoding.
