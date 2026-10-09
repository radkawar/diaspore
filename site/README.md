# Diaspore site

The project site: a playground, a REPL and a debugger, running the Go runtime
compiled to WebAssembly. Static files, no framework, no CDN.

## Requirements

Go 1.26+ and uv. Installing packages in the browser needs access to PyPI.

## Usage

From the repository root:

```sh
make serve
```

Then open http://localhost:8000. `make site` builds without serving.

## Development

- `index.html`, `playground.html`, `repl.html`, `debugger.html`: the pages.
- `style.css`, `js/common.js`: shared styles, theme and navigation.
- `js/wasm-worker.js`: runs the WebAssembly runtime off the main thread.
- `js/packages.js`: installs pure-Python wheels from PyPI.
- `examples/`: playground programs.
- `build.py`: generates the stdlib overlay and the asset manifest.

The WebAssembly bridge lives in `runtimes/go/cmd/diaspore-wasm`. Pushes to
`main` deploy the site with `.github/workflows/pages.yml`.
