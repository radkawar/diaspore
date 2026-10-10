# diaspore-go

Diaspore's Go runtime executes portable bundles and embeds the self-hosted
Python subset compiler. See the
[language and runtime specification](../../docs/spec.md).

## Requirements

Go 1.26 or later. Building from source also needs Python 3.12 or later, uv,
a POSIX host and GNU Make 4.3+ to generate the embedded compiler and compiled
stdlib store; the resulting binary needs neither Python nor stdlib source files.

## Usage

Download a binary from the
[releases page](https://github.com/radkawar/diaspore/releases), or build from
the repository root with `uv sync && make build`:

```sh
diaspore-go build app.py -o app.dsp.json
diaspore-go build app.py --self-contained -o portable.dspb
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

By default, bundles contain program modules and a `stdlib` content ID. The
runtime embeds `stdlib.dsps` (dictionary-only DSPS02), checks only its compact
index digest and the requested ID at startup, and leaves every payload untouched.
The index includes an optional preset DEFLATE dictionary of at most 32 KiB;
an empty dictionary supports ordinary raw DEFLATE. On first import or
compiler-bridge access the runtime checks that module's stored-byte digest,
decodes raw standalone DSPB01 or bounded raw DEFLATE, and validates its identity
and IR. Exact decoded lengths, stream termination, and trailing bytes are checked.
Installed DSPS01 stores and gzip store payloads are not supported; generated
stores must be rebuilt. No global string table is used.
Decoded immutable IR and load errors are cached across VMs. Only imported
modules are compiled into runtime functions and executed. Their globals and
import cache belong to each VM, not to the shared store. The content ID is the
canonical compiled-module hash, independent of the container encoding. Digests
detect distribution corruption; they are not signatures.
Program modules take precedence over linked stdlib modules, then native modules.
Use `--self-contained` to include reachable stdlib modules and omit the link.
A bundle without `stdlib` never implicitly imports modules from the store.
Durable snapshots also record the linked ID and reject mismatched resumes.

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
    defer vm.Close()
    if code := vm.Run(); code != 0 { vm.Close(); os.Exit(code) }
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
containers. The `dspc` package provides `CompileFile(path, config, opts...)`,
`CompileSource(path, source, config, opts...)`, and `CompileSnippet`.
Pass `dspc.CompileOptions{}` for linked defaults; its `Debug`, `SelfContained`,
`NoStdlib`, `SearchPaths`, and `Sources` fields control compilation.
`interp.StdlibID()` returns the installed store's content ID and any load error.
The private `_diaspore_stdlib` bridge exposes copied module IR to sandboxed
compilers and nested VMs without granting filesystem or other capabilities.

For host inspection, `Range.Bounds()` returns exact start, stop, and step
values, including arbitrary-precision integers. `Range.Len()` returns
`(int64, error)` and reports lengths that overflow Python's platform-size limit;
its inline `Start`, `Stop`, and `Step` fields describe only small-bound ranges.

`vm.AddModules(bundle)` registers new modules without executing them or
replacing existing state; durable VMs reject additions. `vm.ReportError(err)`
writes errors returned by `Call` to the configured stderr, including exception
tracebacks, and returns the corresponding exit code.

Call `vm.Close()` when the embedding session ends. It runs shutdown callbacks
for a completed program and releases private file descriptors; `Run` alone
keeps the machine available for subsequent `Call` operations.

Shared standard-library providers and native accelerators are described in
[spec section 11.10](../../docs/spec.md#1110-standard-library).
String translation supports both `str.maketrans` signatures and mapping-backed
`str.translate`, including deletion, expansion, and isolated surrogates.

The maintained `_hashcore` provider uses private `_diaspore_hash` engines for
incremental MD5, SHA-1, SHA-224, SHA-256, SHA-384, and SHA-512. Public `hashlib`
remains Python code. Native engines retain only compression state and a partial
block, support independent copies and non-destructive digests, and charge fuel
for input and finalization work.

## Differences

- `sys.platform` is `"diaspore-go"`. Filesystem encoding metadata reports UTF-8,
  with `surrogatepass` on Windows and `surrogateescape` elsewhere.
  `sys.warnoptions` starts as an independent empty list in each VM; host
  `PYTHONWARNINGS` settings are not applied.
  The installation prefixes (`prefix`, `exec_prefix`, and their `base_` forms)
  name the embedded `<stdlib>` namespace, never a host Python installation.
- `sys.audit` and `sys.addaudithook` dispatch explicitly raised audit events to
  per-VM hooks, including reentrant registration and exception vetoes. Native Go
  host operations do not emit CPython's complete automatic audit-event catalog.
- `time.perf_counter` uses the same real Go monotonic clock as `time.monotonic`.
  Durable runs journal its readings; explicit deterministic mode uses the
  configured logical clock instead.
- Embedded hosts must supply `interp.WithSnippetCompiler(dspc.CompileSnippet)`
  for `exec`, `eval`, and `compile` on source strings. CLI `run` and browser
  run, durable, REPL, and debug VMs supply it automatically.
- `build` does not accept `--binding`; use the Python CLI for binding modules.
- Unlike the Python CLI, loading does not perform JSON Schema validation;
  `--no-validate` has no effect.
- Unicode case conversion supports fewer multi-character mappings than CPython.
- NaNs are unboxed values: separately created NaNs compare identical with `is`
  and share a hash, unlike CPython's per-object NaN identity and hashing.
- Regex execution is fuel-metered and supports lookahead, fixed-width lookbehind,
  backreferences, atomic groups and conditional groups. Possessive repetition,
  `LOCALE`/`DEBUG`/`TEMPLATE`, Unicode name escapes, and non-ASCII group names
  remain unavailable. Inputs are strings or bytes, not arbitrary buffers.
  Flags are plain integers, and `groupindex` returns a detached dictionary.
  `re.error` (also exported as `re.PatternError`) is a distinct exception with
  message, pattern, position, line, and column metadata. `Match.regs` exposes
  cached, immutable group-span tuples.
  See spec section 11.8 for the portable API.
- Non-Linux character-device terminal detection is unavailable; ordinary files
  and WebAssembly virtual files are not terminals.
- Socket `fileno()` values are VM-owned registry IDs, not host descriptors.
  `select` currently requires Linux and accepts only this VM's sockets and
  listeners; other platforms raise `NotImplementedError`.
- Introspection describes sandbox IR, not CPython bytecode or host Go frames.
  `FunctionType` can reconstruct a function from sandbox code, globals, defaults,
  and closure cells. `CodeType` construction from CPython bytecode is unavailable;
  `code.replace` supports names, filenames, first-line metadata, and the
  iterable-coroutine flag, not structural bytecode changes. `co_positions()`
  iterates the execution compiler's statement locations; `f_lasti` and
  `tb_lasti` address those entries in two-byte units for traceback consumers.
  Source start lines come from debug IR. Unavailable end lines and columns
  are `None`; stripped bundles also report `None` for start lines. These
  addresses are not CPython bytecode offsets. Shared stdlib/provider
  restrictions are listed in spec sections 11.10 and 12.

`sys.float_info` describes Go's IEEE-754 binary64 values through an immutable,
tuple-compatible struct sequence, including the named precision/range fields,
constructor and reduction protocol. Its type is immutable and cannot be
subclassed. Native reverse iterators remain lazy: they honor `__reversed__` or the
sequence protocol, including changes to mutable sequences during iteration.

`sys.int_info` and `sys.hash_info` use the same immutable struct-sequence
protocol. Integer limbs follow Go `math/big`'s architecture-sized words, and
decimal conversions are unlimited (there is no integer-string digit-limit
getter or setter). Hash metadata reports the actual unsalted encoded-key
FNV-1a implementation with 63 usable bits, alongside the 64-bit numeric hash
width and modulus. It does not claim CPython's salted string-hash algorithm.

The entry module is registered under both its bundle name and `__main__`.
Native modules support namespace mutation, including replacement of
`sys.excepthook` and `sys.unraisablehook`; the corresponding double-underscore
attributes retain the original hooks. Exception displays use module-qualified
class names, and filesystem errors retain their `errno`, `strerror`, and filename
attributes. `SyntaxError`, `IndentationError`, and `TabError` expose native
message/location fields, including end positions, independently of their
instance dictionaries.
Canonical unbound native descriptors are identity-hashable dictionary keys.

Text tab expansion counts Unicode code points, and integer parsing accepts
Unicode decimal digits. Numeric hashing follows CPython's integer, finite
binary64, and infinity hash arithmetic. Unary and in-place operators honor
inherited special methods; in-place list/dictionary operations retain mutable
identities, including native subclasses. Set updates and difference updates accept
multiple iterables, retain changes preceding iteration errors, and reuse stored
hashes for native set arguments.
Bytes and bytearrays provide ASCII classification, non-overlapping substring
counts, and reverse searches with integer needles and indexed slice bounds.
Saved bytearray methods observe the receiver's current storage when invoked.

Native integer combinatorics and greatest-common-divisor/least-common-multiple
operations use arbitrary-precision integers. `math.fsum` uses compensated
partials, and `math.dist` uses a scaled, compensated Euclidean norm, including
subnormal inputs and IEEE-754 exceptional values.

Object state and reduction use sandbox classes and the vendored `copyreg`
helpers. Protocols below 2 delegate to `copyreg`; newer protocols honor
`__reduce__`, `__getnewargs__`, `__getnewargs_ex__`, and `__getstate__` overrides
and provide list/dict item iterators. Instance dictionaries retain identity;
declared slots use separate storage and omit unset values from default state.
These object protocols do not make opaque native resources picklable: unsupported
native values raise `TypeError`.

Instance attribute lookup and updates use string-key paths when no user-defined
dictionary keys require equality dispatch. Fixed-arity positional Python method
calls fill frame slots directly after normal descriptor lookup, preserving
argument evaluation order, dynamic code replacement, and dispatch fuel charges.
Direct exception classification avoids reflection allocations; wrapped host
errors retain Go's `errors.As` behavior.

Exceptions expose validated `__cause__`, `__context__`, `__suppress_context__`,
and `__traceback__` attributes plus `with_traceback`. Explicit `raise ... from`
and implicit handler/finally chaining preserve the exception objects, including
`from None` suppression and cycle prevention. The vendored `unittest` reporter
can record failures and continue running subsequent test cases.

## Development

Run from the repository root:

```sh
make generate
(cd runtimes/go && gofmt -w . && go build ./... && go vet ./... && go run honnef.co/go/tools/cmd/staticcheck@latest ./... && go test ./...)
uv run --python 3.12 pytest -q tests/test_corpus.py -k go_vm
uv run --python 3.12 pytest -q tests/test_bootstrap.py tests/test_self_suite.py -k go
```
