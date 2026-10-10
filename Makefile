.DEFAULT_GOAL := help
.PHONY: help sync generate build test test-py test-go test-ts test-lua fuzz lint \
	fmt schema site serve bench clean vendor cpython

GO_DIR     := runtimes/go
TS_DIR     := runtimes/ts
LUA_DIR    := runtimes/lua
BUILD      := build
GO_BIN     := $(BUILD)/diaspore-go
DSPC_GO    := $(GO_DIR)/dspc/dspc.dsp.json.gz
DSPC_TS    := $(TS_DIR)/dspc.dsp.json.gz
DSPC_LUA   := $(LUA_DIR)/dspc.dsp.json
STDLIB_GO  := $(GO_DIR)/interp/stdlib.json.gz
STDLIB_TS  := $(TS_DIR)/stdlib.json.gz
STDLIB_LUA := $(LUA_DIR)/stdlib.json
GO_SRC     := $(shell find $(GO_DIR) -name '*.go' -not -name '*_test.go')
PY_SRC     := $(shell find src/diaspore -name '*.py')
STDLIB_SRC := $(shell find src/diaspore/stdlib src/diaspore/cpython -type f -not -path '*/__pycache__/*')

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

sync: ## Install the Python workspace
	uv sync

vendor: ## Restore pinned, unmodified CPython standard-library sources
	uv run python tools/vendor_stdlib.py

cpython: build ## Run CPython language and stdlib tests against the committed ratchet
	uv run --python 3.12 python tools/cpython_regressions.py --all --baseline tests/cpython_baseline.json $(CPYTHON_ARGS)

# A single process pairs every compiler reference with its immutable store snapshot.
$(DSPC_GO) $(DSPC_TS) $(DSPC_LUA) $(STDLIB_GO) $(STDLIB_TS) $(STDLIB_LUA) &: selfhost/dspc.py $(PY_SRC) $(STDLIB_SRC) tools/generate_runtime.py Makefile
	uv run python tools/generate_runtime.py

$(TS_DIR)/.stdlib-stamp: src/diaspore/cpython/LICENSE src/diaspore/cpython/UNICODE_LICENSE src/diaspore/cpython/stdlib_manifest.json Makefile
	rm -rf $(TS_DIR)/cpython
	mkdir -p $(TS_DIR)/cpython
	cp src/diaspore/cpython/LICENSE src/diaspore/cpython/UNICODE_LICENSE src/diaspore/cpython/stdlib_manifest.json $(TS_DIR)/cpython/
	touch $@

$(LUA_DIR)/.stdlib-stamp: $(TS_DIR)/.stdlib-stamp
	rm -rf $(LUA_DIR)/cpython
	cp -R $(TS_DIR)/cpython $(LUA_DIR)/cpython
	touch $@

generate: $(DSPC_GO) $(DSPC_TS) $(DSPC_LUA) $(STDLIB_GO) $(STDLIB_TS) $(STDLIB_LUA) $(TS_DIR)/.stdlib-stamp $(LUA_DIR)/.stdlib-stamp ## Generate linked compilers and compiled stdlib stores

$(GO_BIN): $(GO_SRC) $(GO_DIR)/go.mod $(DSPC_GO) $(STDLIB_GO)
	cd $(GO_DIR) && go build -o $(CURDIR)/$@ ./cmd/diaspore-go

build: generate $(GO_BIN) ## Generate artifacts and build the diaspore-go binary

test: build ## Run every test suite against every runtime
	uv run pytest -q
	cd $(GO_DIR) && go test ./...
	node $(TS_DIR)/src/cli.ts selftest-build

test-py: ## Python compiler and reference VM only
	uv run pytest -q -k "not go_vm and not ts_vm and not lua_vm"

test-go: build ## Go runtime: unit tests and conformance corpus
	cd $(GO_DIR) && go test ./...
	uv run pytest -q tests/test_corpus.py -k go_vm

test-ts: generate ## TypeScript runtime conformance corpus
	uv run pytest -q tests/test_corpus.py -k ts_vm

test-lua: ## Lua runtime conformance corpus
	uv run pytest -q tests/test_corpus.py -k lua_vm

fuzz: build ## Differential fuzzing against CPython (SEEDS=n to scale)
	DIASPORE_FUZZ_SEEDS=$(or $(SEEDS),300) uv run pytest tests/test_fuzz.py -q

lint: generate ## Lint and format-check all sources
	uv run python tools/lint_python.py
	cd $(GO_DIR) && go vet ./... && test -z "$$(gofmt -l .)"
	cd $(GO_DIR) && go run honnef.co/go/tools/cmd/staticcheck@latest ./...
	cd $(TS_DIR) && npm ci --no-audit --no-fund --silent && npm run -s check

fmt: ## Format Go sources
	cd $(GO_DIR) && gofmt -w .

schema: ## Export the IR JSON Schema to build/ir.schema.json
	@mkdir -p $(BUILD)
	uv run diaspore schema -o $(BUILD)/ir.schema.json

site: generate ## Build the site: wasm runtime, stdlib identity, asset manifest
	cd $(GO_DIR) && GOOS=js GOARCH=wasm go build -trimpath -ldflags="-s -w" \
		-o $(CURDIR)/site/diaspore.wasm ./cmd/diaspore-wasm
	cp "$$(go env GOROOT)/lib/wasm/wasm_exec.js" site/
	uv run python site/build.py

serve: site ## Serve the site on http://localhost:8000
	python3 -m http.server -d site 8000

bench: build ## Run the benchmark on CPython and every runtime
	@mkdir -p $(BUILD)
	uv run diaspore build examples/benchmark/bench.py -o $(BUILD)/bench.dsp.json
	@echo "CPython:"     && python3 examples/benchmark/bench.py
	@echo "diaspore-go:" && $(GO_BIN) run $(BUILD)/bench.dsp.json
	@echo "diaspore-ts:" && node $(TS_DIR)/src/cli.ts run $(BUILD)/bench.dsp.json
	@echo "diaspore-lua:" && lua5.4 $(LUA_DIR)/cli.lua run $(BUILD)/bench.dsp.json
	@echo "diaspore-py:" && uv run diaspore run $(BUILD)/bench.dsp.json

clean: ## Remove build output and generated artifacts
	rm -rf $(BUILD) .pytest_cache tests/fuzz_failures
	rm -rf $(DSPC_GO) $(DSPC_TS) $(DSPC_LUA) $(STDLIB_GO) $(STDLIB_TS) $(STDLIB_LUA) $(TS_DIR)/.stdlib-stamp $(TS_DIR)/cpython $(LUA_DIR)/.stdlib-stamp $(LUA_DIR)/cpython site/diaspore.wasm site/wasm_exec.js site/cpython site/build.json site/ir.schema.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
