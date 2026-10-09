# diaspore-go

Diaspore's Go runtime executes portable bundles and embeds the self-hosted
Python subset compiler. See the
[language and runtime specification](../../docs/spec.md).

## Requirements

Go 1.26 or later. Building from source also needs Python 3.12 or later, uv, and
make to generate the embedded compiler; the resulting binary needs no Python.

## Usage

Download a binary from the
[releases page](https://github.com/radkawar/diaspore/releases), or build from
the repository root with `uv sync && make build`:

```sh
diaspore-go build app.py -o app.dsp.json
diaspore-go run app.dsp.json --max-steps 1000000 -- arg1
diaspore-go run app.dsp.json --durable --snapshot app.flow.json
diaspore-go resume app.dsp.json app.flow.json --send '"approved"'
```

The embedding library installs with
`go get github.com/radkawar/diaspore/runtimes/go@latest`. The `dspc` package
and the CLI embed a generated compiler bundle, so `go install` of the CLI does
not work; use a release binary or `make build`.

Repeat `--path DIR` to add import search directories, for example
`./build/diaspore-go build app.py --path lib --path vendor -o build/app.dsp.json`.
The entry's directory is searched first, then added directories in flag order.
The compiler has read-only access to these directories.

The loader accepts JSON, DSPB, and gzip-compressed bundles. `build` selects DSPB
for an output ending in `.dspb` and gzip for `.gz`. Network, filesystem, and
task creation capabilities are denied unless granted with `--allow-net`,
`--allow-fs`, and `--allow-spawn`. Durable suspension writes a snapshot and
exits with code 7.

## Embedding

Compile a plugin that imports `host` and defines `price(order)` with
`uv run diaspore build plugin.py --binding host -o build/plugin.dsp.json`. A Go
host can load it, supply bindings, and call its functions:

```go
package main

import (
    "fmt"
    "os"

    "github.com/radkawar/diaspore/runtimes/go/interp"
)

func main() {
    data, err := os.ReadFile("build/plugin.dsp.json")
    if err != nil { panic(err) }
    bundle, err := interp.LoadBundle(data)
    if err != nil { panic(err) }
    vm := interp.New(bundle,
        interp.WithBindings("host", map[string]interp.NativeFunc{
            "lookup": func(args []interp.Value) (interp.Value, error) {
                return int64(10), nil
            },
        }),
        interp.WithMaxSteps(1_000_000),
    )
    if code := vm.Run(); code != 0 { os.Exit(code) }
    order, err := interp.FromGo(map[string]any{"qty": 2})
    if err != nil { panic(err) }
    price, err := vm.Call("plugin", "price", order)
    if err != nil { panic(err) }
    text, err := vm.Str(price)
    if err != nil { panic(err) }
    fmt.Println(text)
}
```

Bindings and `Call` use `interp.Value`; `interp.FromGo` converts host
containers. The `dspc` package provides
`CompileFile(path, debug, searchPaths, opts...)`, `CompileSource`, and
`CompileSnippet`; pass `nil` for no extra search paths.

`vm.AddModules(bundle)` registers new modules without executing them or
replacing existing state; durable VMs reject additions. `vm.ReportError(err)`
writes errors returned by `Call` to the configured stderr, including exception
tracebacks, and returns the corresponding exit code.

## Differences

- `sys.platform` is `"diaspore-go"`.
- Embedded hosts must supply `interp.WithSnippetCompiler(dspc.CompileSnippet)`
  for `exec`, `eval`, and `compile` on source strings; CLI `run` supplies it.
- `build` does not accept `--binding`; use the Python CLI for binding modules.
- Unlike the Python CLI, loading does not perform JSON Schema validation;
  `--no-validate` has no effect.
- String `%` formatting is not implemented, and Unicode case conversion supports
  fewer multi-character mappings than CPython.

## Development

Run from the repository root:

```sh
make generate
(cd runtimes/go && go vet ./... && go test ./...)
uv run pytest -q tests/test_corpus.py -k go_vm
```
