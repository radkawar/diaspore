# diaspore-lua

The Lua runtime executes Diaspore JSON bundles without external Lua
dependencies. See the [language specification](../../docs/spec.md) for sandbox
semantics.

## Requirements

- Lua 5.4
- Python 3.12, uv, a POSIX host and GNU Make 4.3+ to regenerate artifacts;
  Python and uv to run the CPython conformance tests
- `make generate` produces the trusted compiler JSON and compact `stdlib.dsps`.
  Lua builds and runtime `compile`, `exec`, and `eval` use these installed artifacts.

## Usage

```sh
uv run diaspore build examples/benchmark/bench.py -o build/bench.dsp.json
lua5.4 runtimes/lua/cli.lua run build/bench.dsp.json
lua5.4 runtimes/lua/cli.lua build examples/benchmark/bench.py -o build/portable.dsp.json --self-contained
```

The CLI accepts `--max-steps`, `--max-depth`, `--deterministic`, and capability
flags such as `--allow-fs read:/data`. Arguments after `--` reach `sys.argv`.
The CLI can be invoked by absolute path from any working directory.
Runtime compilation uses a nested, fuel-metered interpreter with read-only
in-memory source files. It does not grant the program filesystem access.
Vendored `json` and `abc` use private native `_json` and `_abc` providers;
their public Python implementations remain unchanged.

Builds link the installed `stdlib.dsps` by canonical compiled-code content id
unless `--self-contained` is requested. Startup reads and verifies only the
DSPS02 index, then checks the bundle's requested id. The index contains that id,
a length-prefixed preset dictionary, the module count, and sorted module records.
Import reads and verifies one raw DSPB01 payload before decoding its IR; decoded
modules are cached privately, with independent IR copies and globals in each VM.
Membership and file metadata queries do not decode modules. Shipped Lua payloads
use raw codec 0 and an empty dictionary. Caller-supplied raw containers may include
an unused dictionary of up to 32768 bytes, which is verified with the index and
skipped. Raw-DEFLATE codec 1 is rejected; Lua does not include an inflater.
Resolution prefers program modules, then the linked store, then native modules.
Bundles without a stdlib reference are standalone: missing modules never fall
back to the store. Installed stores have no whole-store JSON fallback.
The private `_diaspore_stdlib` bridge exposes installed code to the compiler, not
ambient filesystem access.

## Embedding

Compile a program that imports `host` with `diaspore build --binding host`. Set
`package.path` to the runtime directory, including `?/init.lua`:

```lua
package.path = "runtimes/lua/?.lua;runtimes/lua/?/init.lua;" .. package.path
local diaspore = require("diaspore")
local vm = diaspore.new(diaspore.load("plugin.dsp.json"), {
  bindings = { host = { lookup = function(key) return 12 end } },
  max_steps = 100000,
  stdout = function(text) io.stdout:write(text) end,
  stderr = function(text) io.stderr:write(text) end,
})
assert(vm:run() == 0)
print(vm:call("plugin", "price", { item = "book", quantity = 2 }))
vm:close()
```

Options also include `max_depth`, `argv`, `caps`, `policy`, and `deterministic`.
Output callbacks receive strings immediately; omitted callbacks use Lua's
standard streams. `run()` returns an exit code; `call()` propagates errors. Host
errors become sandbox `RuntimeError` exceptions. Bindings accept positional
arguments, and bundled modules take precedence over bindings.
Call `close()` once after the final host call to run imported threading shutdown
and `atexit` callbacks; the CLI does this automatically. Compiler and stdlib
artifacts load lazily from the runtime installation. Optional parsed
`compiler_bundle` and `stdlib_store` embedding options override these artifacts.
Parsed `stdlib_store` overrides still validate their full canonical content
hash and are isolated from subsequent caller mutation. Container index and
payload hashes detect corruption; they are not distribution signatures.

`diaspore.compile(source, opts)` and `diaspore.compile_file(entry, opts)` return
raw JSON-bundle tables. They link the installed stdlib by default; set
`self_contained = true` for a portable bundle. Source compilation accepts a
`filename`; file compilation accepts `search_paths` for local module roots.
The CLI equivalent is `build ENTRY -o OUTPUT`, with repeated `--path DIR`
options and optional `--self-contained`.

Booleans, numbers, strings, lists, and dictionaries cross the boundary as Lua
values. Dense arrays (including `{}`) become lists; other tables become
dictionaries. Use `diaspore.dict({})` to mark an empty dictionary. Sandbox
tuples return as arrays. `nil` represents `None` at the top level;
`diaspore.none` preserves `None` inside tables. Other sandbox objects cannot
cross this boundary.

Native methods follow subclass MRO and `super()` descriptor binding. Class
construction retains the `__prepare__` namespace and automatically wraps
`__new__`, `__init_subclass__`, and `__class_getitem__`; `property.__doc__`
is writable. `int.from_bytes` remains a classmethod, while `bytes`/`bytearray`
`maketrans` and `str.maketrans` remain static methods across subclasses.
Reversal honors `__reversed__` and live indexed fallback; dictionary construction
and updates accept mapping keys or streaming pairs, and in-place union preserves
subclass identity. Builtin subclasses with object-style `__str__` honor custom
`__repr__`; string subclasses retain their native string value.
Class representations honor inherited metaclass overrides; explicit
`type.__repr__` and delegation through `super()` retain the base type renderer.
Uncaught exceptions use the live `sys.excepthook`; `sys.__excepthook__` retains
the original renderer for sandbox tracebacks, exception chains and notes.
Caught exceptions preserve traceback propagation and cause/context links across
explicit and bare raises, handlers, `finally`, and context-manager exits.
Assignments to exception metadata validate the required exception, traceback,
and boolean values.
Shallow protected execution uses ordinary calls; deeper catches and generator
resumption use bounded coroutine segments to avoid Lua's limited C-call stack.
Resumed generators count toward the configured guest recursion limit.
Private function activations and argument carriers are reused, but closures,
public frames, and tracebacks retain their activation records. Frame objects
preserve identity and expose live caller links while generators are running.

## Differences

- No tasks or network I/O. Runtime compilation supports `exec` and `eval`;
  interactive `single` mode is unsupported.
- Filesystem operations use only Lua's standard library. `stat`, `lstat`,
  `fstat`, and `readlink` check capabilities, then raise `OSError` with `errno.ENOSYS` and
  `diaspore-lua cannot read file metadata`. Metadata is never synthesized.
  Directory creation, listing, descriptor metadata/truncation, and descriptor
  inheritance controls are unavailable. Private descriptor numbers support
  read/write/seek/close without exposing ambient host handles; unsupported
  atomic creation and special-file flags fail explicitly.
  Terminal detection is unavailable: `isatty` returns `False` when terminal
  status cannot be determined, including for invalid or closed descriptors.
- `os.getcwd()` reads `PWD`; filesystem policy checks are lexical and do not
  resolve symlinks. The private file-kind probe cannot reliably distinguish
  special files. Host filesystem overlays are unsupported; compiler source
  overlays are isolated read-only maps with no host fallback.
- Integers use arbitrary precision with a native signed 64-bit fast path.
  Integer byte conversions and unsigned memoryview reads preserve the full
  value. Host-sized counts and allocation lengths raise `OverflowError` when
  out of range.
  `sys.int_info` reports 24-bit limbs in 8-byte integer cells and no decimal
  conversion limit. `sys.hash_info` describes unseeded FNV-1a string/bytes-key
  hashing, not CPython's SipHash; numeric metadata follows the backend values
  documented in [spec section 12](../../docs/spec.md#12-deliberate-deviations-and-exclusions).
- User bundles must be plain JSON. Gzip bundles and durable flows are unsupported;
  DSPB01 decoding is used internally for compiled store modules only.
- Bytearrays support mutation and export resize locks. Memoryviews share storage,
  preserve strided aliases and read-only access, and support native scalar casts.
  Multidimensional views are explicitly unsupported.
- Regex Unicode categories and case matching, `str.isidentifier`,
  `str.isprintable`, and string repr use Unicode 15 data.
  Regex `LOCALE`, `DEBUG`, `TEMPLATE`,
  named Unicode escapes, and non-ASCII group identifiers are rejected. Other
  string case conversion covers ASCII and
  Latin-1; remaining string Unicode predicates are approximate.
- `globals()`, module dictionaries, and instance `vars()` are live dictionaries;
  optimized function `locals()` returns a snapshot. Class namespaces are live
  read-only views and `sys.modules` is a live dictionary.
- Native UTF-8, ASCII, and Latin-1 codecs support strict, ignore, replace,
  backslashreplace, and surrogateescape policies. Other codecs require their
  linked or bundled providers.
- `time.sleep()` uses a CPU-clock busy wait. `math.lgamma` uses a portable
  Lanczos approximation and can differ from the platform C library in the last
  bits. Entropy reads use `/dev/urandom`, fail if unavailable, and are denied in
  deterministic mode.

## Development

```sh
uv run --python 3.12 pytest -q tests/test_corpus.py tests/test_bootstrap.py tests/test_self_suite.py -k lua
uv run --python 3.12 pytest -q tests/test_lua_numbers.py tests/test_lua_numeric_methods.py
uv run --python 3.12 pytest -q tests/test_lua_store_perf.py
```

Metadata-dependent bootstrap and self-suite cases retain the explicit
`diaspore-lua cannot read file metadata` exclusion. The corpus still exercises
in-memory `compile`, `exec`, and `eval`, without filesystem capabilities.
