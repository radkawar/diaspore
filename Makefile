.DEFAULT_GOAL := help
.PHONY: help sync generate build test test-py test-go test-ts test-lua fuzz lint \
	fmt schema site serve bench clean

GO_DIR     := runtimes/go
TS_DIR     := runtimes/ts
LUA_DIR    := runtimes/lua
BUILD      := build
GO_BIN     := $(BUILD)/diaspore-go
DSPC_GO    := $(GO_DIR)/dspc/dspc.dsp.json.gz
DSPC_TS    := $(TS_DIR)/dspc.dsp.json.gz
STDLIB_GO  := $(GO_DIR)/dspc/stdlib.json.gz
STDLIB_TS  := $(TS_DIR)/stdlib
GO_SRC     := $(shell find $(GO_DIR) -name '*.go' -not -name '*_test.go')
PY_SRC     := $(shell find src/diaspore -name '*.py')
STDLIB_SRC := $(shell find src/diaspore/stdlib -name '*.py')

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

sync: ## Install the Python workspace
	uv sync

$(DSPC_GO): selfhost/dspc.py $(PY_SRC)
	uv run diaspore build selfhost/dspc.py -o $@

$(DSPC_TS): $(DSPC_GO)
	cp $< $@

$(STDLIB_GO): $(STDLIB_SRC)
	uv run python -c 'from diaspore.compiler import write_stdlib_overlay; write_stdlib_overlay("$@")'

$(TS_DIR)/.stdlib-stamp: $(STDLIB_SRC)
	rm -rf $(STDLIB_TS)
	cd src/diaspore && find stdlib -name '*.py' -exec install -D -m 644 {} $(CURDIR)/$(TS_DIR)/{} \;
	touch $@

generate: $(DSPC_GO) $(DSPC_TS) $(STDLIB_GO) $(TS_DIR)/.stdlib-stamp ## Generate the compiler and stdlib embedded by the Go and TS runtimes

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
	uv run ruff check .
	cd $(GO_DIR) && go vet ./... && test -z "$$(gofmt -l .)"
	cd $(GO_DIR) && go run honnef.co/go/tools/cmd/staticcheck@latest ./...
	cd $(TS_DIR) && npm ci --no-audit --no-fund --silent && npm run -s check

fmt: ## Format Go sources
	cd $(GO_DIR) && gofmt -w .

schema: ## Export the IR JSON Schema to build/ir.schema.json
	@mkdir -p $(BUILD)
	uv run diaspore schema -o $(BUILD)/ir.schema.json

site: generate ## Build the site: wasm runtime, stdlib map, asset manifest
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
	rm -rf $(DSPC_GO) $(DSPC_TS) $(STDLIB_GO) $(STDLIB_TS) $(TS_DIR)/.stdlib-stamp site/diaspore.wasm site/wasm_exec.js site/stdlib.json site/build.json site/ir.schema.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
