# PyPI packages

Two pure-Python packages are compiled into one bundle that runs without a Python
installation on Go, TypeScript, and Lua.

## Source

- `app.py`: encodes and decodes Morse with `morse3`, then groups connected
  stations with `unionfind`.

## Build

From the repository root after `make build`; package installation needs network
access.

```sh
uv pip install --target build/vendor --no-deps morse3==0.47 unionfind==0.2.1
uv run diaspore build examples/packages/app.py --path build/vendor -o build/packages.dsp.json
```

`--path DIR` adds a module search directory and can be repeated. Package sources
are bundled at build time, not installed in the sandbox. The
[browser playground](https://radkawar.github.io/diaspore/playground.html) can
install the same packages.

## Run

Each runtime loads the same bundle; none needs access to `build/vendor`.

```sh
uv run diaspore run build/packages.dsp.json
build/diaspore-go run build/packages.dsp.json
node runtimes/ts/src/cli.ts run build/packages.dsp.json
lua5.4 runtimes/lua/cli.lua run build/packages.dsp.json
```

## Output

Each runtime prints:

```text
morse: .... . .-.. .-.. ---   .-- --- .-. .-.. -..
decoded: hello world
stations: [[0, 1, 2], [3, 4]]
```
