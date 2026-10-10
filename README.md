# Diaspore

Write Python. Ship JSON. Run it anywhere, sandboxed.

Diaspore compiles a large subset of Python into a portable JSON bundle and runs
it with identical output on four runtimes: Python, Go, TypeScript and Lua. A
bundle has no ambient authority, runs on a fuel budget, and can pause itself
into a JSON snapshot that any other runtime can resume.

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

## Features

- **Portable.** One JSON artifact: diffable, signable, storable in a database
  column, validated before it runs.
- **Sandboxed.** Network, file system and task spawning are denied unless
  granted. `--max-steps` caps CPU; exhausting it is an uncatchable trap.
- **Embeddable.** Hosts expose functions as an importable module and call into
  the sandbox. Python, Go, TypeScript and Lua hosts share one model.
- **Durable.** `flow.suspend()` pauses a program into a snapshot. Resume it
  later, in another process, on another runtime.
- **Self-hosted.** The compiler is written in the subset it compiles. The Go and
  TypeScript runtimes embed it, so they build Python without Python, and so does
  the [browser playground](https://radkawar.github.io/diaspore).
- **Conformant.** Every corpus program must match CPython byte for byte on every
  runtime, backed by differential fuzzing.

## Runtimes

| Runtime | Install | Notes |
| --- | --- | --- |
| diaspore-py | `uv tool install diaspore` | Reference VM, compiler and CLI |
| diaspore-go | [Release binaries](https://github.com/radkawar/diaspore/releases); library: `go get github.com/radkawar/diaspore/runtimes/go` | Near CPython speed; also compiles to WebAssembly |
| diaspore-ts | [`npm install diaspore`](https://www.npmjs.com/package/diaspore) | Node 22+, no runtime dependencies |
| diaspore-lua | [Release tarball](https://github.com/radkawar/diaspore/releases) | Pure Lua 5.4; no tasks, regex or big integers |

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

To build everything from source you need Go 1.26+; Node 23.6+ and Lua 5.4 are
needed only for their runtimes.

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
```

Run `make help` for the full list.

## License

[MIT](https://github.com/radkawar/diaspore/blob/main/LICENSE)
