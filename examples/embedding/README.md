# Embedded pricing policy

Four hosts load the same sandboxed Python policy and expose only customer
lookups, with a 100,000-step budget.

## Source

- `plugin.py`: pricing and risk functions that import the `host` binding.
- `python/main.py`: Python host using `Machine`.
- `go/main.go`: Go host using `interp.New`; `go/go.mod` links the local runtime.
- `ts/main.ts`: TypeScript host using `Interp`.
- `lua/main.lua`: Lua host using `diaspore.new`.

## Build

Run all commands from the repository root after `make build`.

```sh
uv run diaspore build examples/embedding/plugin.py --binding host -o build/plugin.dsp.json
```

## Run

Each host loads the bundle, registers bindings, runs module initialization, then
calls `price` and `risk_score` with the same orders.

```sh
uv run python examples/embedding/python/main.py build/plugin.dsp.json
(cd examples/embedding/go && go run . ../../../build/plugin.dsp.json)
node examples/embedding/ts/main.ts build/plugin.dsp.json
lua5.4 examples/embedding/lua/main.lua build/plugin.dsp.json
```

## Output

Each host prints:

```text
acme: total=749.25 risk=25
startup: total=124.95 risk=5
anon: total=10.00 risk=30
```
