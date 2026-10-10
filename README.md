# Diaspore

Diaspore compiles a large subset of Python into a portable JSON bundle for
Python, Go, TypeScript and Lua runtimes. Bundles have no ambient filesystem or
network authority and support fuel limits. Python, Go and TypeScript can pause
execution into a JSON snapshot and resume it on another compatible runtime.

[Playground](https://radkawar.github.io/diaspore) |
[Specification](https://github.com/radkawar/diaspore/blob/main/docs/spec.md) | [Security model](https://github.com/radkawar/diaspore/blob/main/docs/security.md) |
[Examples](https://github.com/radkawar/diaspore/blob/main/examples) | [Releases](https://github.com/radkawar/diaspore/releases)

```sh
diaspore build app.py -o app.dsp.json     # compile once
diaspore run app.dsp.json                 # Python
diaspore-go run app.dsp.json              # Go
npx diaspore-ts run app.dsp.json          # TypeScript
lua5.4 runtimes/lua/cli.lua run app.dsp.json  # Lua
```

By default, bundles contain program code and a content hash identifying the
standard library shipped by the runtime. The prebuilt `stdlib.dsps` (`DSPS02`)
store loads only its compact index initially; standalone module DSPB payloads
are checked, decoded and cached on demand. Python, Go and TypeScript compress
each module with raw DEFLATE and a shared preset dictionary from the index;
Lua ships raw payloads. Compression does not change the library's content hash.
Imports initialize each machine's library modules only when needed. A mismatched
library is rejected before execution.
For an artifact that carries its own reachable library modules:

```sh
diaspore build app.py --self-contained -o app.dsp.json
```

`compile_bundle(..., self_contained=True)` provides the same option to Python
embedders. Go, TypeScript and Lua compilers expose the corresponding option.

`make generate` produces matching stores for all four runtimes, including the
Python package; runtime startup does not compile installed library sources.
Index and per-module hashes detect corrupted artifacts, not malicious replacement
of a distribution. See [docs/dspb.md](docs/dspb.md) for the binary format.

## Features

- JSON bundles can be inspected, signed, and stored independently of their host.
- Network, file system and task spawning are denied unless
  granted. `--max-steps` caps CPU; exhausting it is an uncatchable trap.
- Hosts expose functions as an importable module and call into
  the sandbox. Python, Go, TypeScript and Lua hosts share one model.
- `flow.suspend()` pauses a program into a snapshot on the Python,
  Go and TypeScript runtimes. Resume it later in another process or runtime.
- The compiler is written in the subset it compiles. Go and TypeScript embed
  its compiled bundle; Lua loads it from the runtime installation. These
  runtimes and the [browser playground](https://radkawar.github.io/diaspore)
  build Python without Python installed.
- Supported corpus programs are compared byte for byte
  with CPython, alongside differential fuzzing. Runtime-specific exclusions are
  documented in the specification and runtime READMEs.

## Runtimes

| Runtime | Install | Notes |
| --- | --- | --- |
| diaspore-py | `uv tool install diaspore` | Reference VM, compiler and CLI |
| diaspore-go | [Release binaries](https://github.com/radkawar/diaspore/releases); library: `go get github.com/radkawar/diaspore/runtimes/go` | Native binary and WebAssembly |
| diaspore-ts | [`npm install diaspore`](https://www.npmjs.com/package/diaspore) | Node 22+, no runtime dependencies |
| diaspore-lua | [Release tarball](https://github.com/radkawar/diaspore/releases) | Pure Lua 5.4; arbitrary-precision integers, no tasks or network |

Sources: [src/diaspore](https://github.com/radkawar/diaspore/blob/main/src/diaspore), [runtimes/go](https://github.com/radkawar/diaspore/blob/main/runtimes/go),
[runtimes/ts](https://github.com/radkawar/diaspore/blob/main/runtimes/ts), [runtimes/lua](https://github.com/radkawar/diaspore/blob/main/runtimes/lua).

## Getting started

Install the compiler and reference runtime with
[uv](https://docs.astral.sh/uv/):

```sh
uv tool install diaspore
diaspore build main.py -o main.dsp.json
diaspore run main.dsp.json
```

Run the same bundle from Node, or embed it:

```sh
npm install diaspore
npx diaspore-ts run main.dsp.json
```

Source builds require a POSIX host, GNU Make 4.3+, and Go 1.26+; Node 23.6+
and Lua 5.4 are needed only for their runtimes.

```sh
git clone https://github.com/radkawar/diaspore && cd diaspore
uv sync
make build    # embedded compiler + build/diaspore-go
```

Then try the [examples](https://github.com/radkawar/diaspore/blob/main/examples), starting with
[embedding](https://github.com/radkawar/diaspore/blob/main/examples/embedding): one sandboxed plugin hosted from all four
languages.

## Embedding

```python
from diaspore import Machine, compile_bundle

bundle = compile_bundle("plugin.py", extra_modules={"host"})
vm = Machine(bundle, bindings={"host": {"lookup": lookup}}, max_steps=1_000_000)
vm.run()
price = vm.call("plugin", "price", order)
```

The same pattern in [Go](https://github.com/radkawar/diaspore/blob/main/runtimes/go#embedding),
[TypeScript](https://github.com/radkawar/diaspore/blob/main/runtimes/ts#embedding) and [Lua](https://github.com/radkawar/diaspore/blob/main/runtimes/lua#embedding).

## Language

Functions, closures, classes with multiple inheritance, generators,
`async`/`await`, exceptions including `except*`, `match`, comprehensions,
f-strings, arbitrary-precision integers, multi-module packages, and a portable
standard library (`json`, `math`, `re`, `time`, `random`, `collections`,
`itertools`, `functools` and more). Unsupported syntax is a compile error with a
`file:line`, never a silent runtime failure. The exact surface is in
[docs/spec.md](https://github.com/radkawar/diaspore/blob/main/docs/spec.md).

## Repository

```
src/diaspore/   compiler, reference VM, CLI, standard library
selfhost/       dspc, the self-hosted compiler
runtimes/       go, ts and lua runtimes
examples/       runnable examples for every runtime
site/           playground and project site
docs/           specification, security model, binary format
tests/          conformance corpus and test suites
```

## Development

```sh
make test    # every suite on every runtime
make lint    # ruff, go vet, staticcheck, gofmt, tsc
make fuzz    # differential fuzzing against CPython
make serve   # build and serve the site on localhost:8000
make vendor  # restore pinned CPython standard-library sources
make cpython # CPython regression scoreboard and pass-count ratchet
```

Run `make help` for the full list.

The API detector derives its cases from package source. The self-suite executes
the package's pure tests inside the sandbox:

```sh
uv run --python 3.12 pytest -q tests/test_api_parity.py
uv run --python 3.12 pytest -q -rs tests/test_self_suite.py
```

`make cpython` records per-module outcomes in `build/cpython-regressions/` and
compares pass counts with `tests/cpython_baseline.json`. Compilation failures,
import failures, skips, and unexecuted tests are distinct from passing tests.
See `tools/stdlib_manifest.json` for the exact vendored module inventory.

## License

[MIT](https://github.com/radkawar/diaspore/blob/main/LICENSE)

The bundled pure-Python standard library includes unmodified files from
CPython v3.12.12, distributed under the Python Software Foundation license and
the notices in [src/diaspore/cpython/LICENSE](src/diaspore/cpython/LICENSE).
Source distributions retain unmodified sources and their manifest in
`src/diaspore/cpython`; Diaspore's maintained C-layer adapters and overlays live
in `src/diaspore/stdlib`. Runtime packages ship precompiled code and notices.
Compilers search the maintained root before the upstream root.
Unicode property and identifier tables include data distributed under the
[Unicode License](src/diaspore/cpython/UNICODE_LICENSE).
