# Diaspore site

The project site: a playground, a REPL and a debugger, running the Go runtime
compiled to WebAssembly. Static files, no framework, no CDN.

## Requirements

The [source-build requirements](../README.md#getting-started) apply.
Building the site and installing packages in the browser need access to PyPI.

## Usage

From the repository root:

```sh
make serve
```

Then open http://localhost:8000. `make site` builds without serving.

The runtime embeds a precompiled stdlib store. The playground, REPL and debugger
link to that store by default; stdlib modules execute only when imported. The site
manifest and runtime must have the same stdlib identity or startup fails.
Installed package sources remain separate and take precedence over the store.

In the playground, select **Self-contained** before Run to embed reachable stdlib
modules instead of a `stdlib` content reference. Download saves the displayed
bundle as `main.dsp.json`, including any JSON edits. Linked bundles require the
same stdlib identity on the destination runtime; self-contained bundles do not.
The JS `compile(source, files, {self_contained: true})` API in `js/runtime.js`
provides the same option.

The "With packages" group includes "Durable workflow", which installs Diaspore
and runs `examples/durable.py`. The build pins the newest published pure-Python
wheel no newer than the checkout's package version; an unavailable release
cannot become a broken demo link. The build fails if no eligible wheel exists.

## Development

- `index.html`, `playground.html`, `repl.html`, `debugger.html`: the pages.
- `style.css`, `js/common.js`: shared styles, theme and navigation.
- `js/wasm-worker.js`: runs the WebAssembly runtime off the main thread.
- `js/packages.js`: installs pure-Python wheels from PyPI.
- `examples/`: playground programs.
- `build.py`: generates the asset manifest, stdlib identity and published demo version.

The WebAssembly bridge lives in `runtimes/go/cmd/diaspore-wasm`. Pushes to
`main` deploy the site with `.github/workflows/pages.yml`; the release workflow
deploys it again after publishing to PyPI so the demo can use the new version.
