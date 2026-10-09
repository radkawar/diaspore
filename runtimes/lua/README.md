# diaspore-lua

The Lua runtime executes Diaspore JSON bundles without external Lua
dependencies. See the [language specification](../../docs/spec.md) for sandbox
semantics.

## Requirements

- Lua 5.4
- Python 3.12+ and uv to compile bundles and run the test suite

## Usage

```sh
uv run diaspore build examples/benchmark/bench.py -o build/bench.dsp.json
lua5.4 runtimes/lua/cli.lua run build/bench.dsp.json
```

The CLI accepts `--max-steps`, `--max-depth`, `--deterministic`, and capability
flags such as `--allow-fs read:/data`. Arguments after `--` reach `sys.argv`.
The CLI can be invoked by absolute path from any working directory.

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
```

Options also include `max_depth`, `argv`, `caps`, `policy`, and `deterministic`.
Output callbacks receive strings immediately; omitted callbacks use Lua's
standard streams. `run()` returns an exit code; `call()` propagates errors. Host
errors become sandbox `RuntimeError` exceptions. Bindings accept positional
arguments, and bundled modules take precedence over bindings.

Booleans, numbers, strings, lists, and dictionaries cross the boundary as Lua
values. Dense arrays (including `{}`) become lists; other tables become
dictionaries. Use `diaspore.dict({})` to mark an empty dictionary. Sandbox
tuples return as arrays. `nil` represents `None` at the top level;
`diaspore.none` preserves `None` inside tables. Other sandbox objects cannot
cross this boundary.

## Differences

- No tasks, regular expressions, network I/O, or runtime
  `compile`/`exec`/`eval`.
- Integers use signed 64-bit arithmetic, with wrapping overflow; larger literals
  become floats. There is no arbitrary-precision integer support.
- Bundles must be plain JSON. Gzip, DSPB, and durable flows are unsupported.
- Unicode case conversion covers ASCII and Latin-1; other Unicode predicates are
  approximate. `globals()`, `locals()`, and `vars()` return snapshots.
- `time.sleep()` uses a CPU-clock busy wait. Random sequences are
  runtime-specific.

## Development

```sh
uv run pytest -q tests/test_corpus.py -k lua_vm
```
