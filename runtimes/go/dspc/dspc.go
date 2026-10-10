// Package dspc embeds dspc, the self-hosted Diaspore compiler, and runs it on
// the Go VM. It lets Go hosts compile Python source to bundles with no Python
// installation.
//
// Standard-library imports link to the content-addressed compiled store shipped
// by interp. CompileOptions.SelfContained instead copies reachable library
// modules into the result. Neither mode needs standard-library source files.
//
// Run `make generate` from the repository root to produce the embedded compiler
// bundle and interp's compiled standard-library store before building.
package dspc

import (
	"bytes"
	"compress/gzip"
	_ "embed" // needed for the go:embed directive below
	"fmt"
	"io"
	"path"
	"path/filepath"
	"strings"

	"github.com/radkawar/diaspore/runtimes/go/interp"
)

//go:embed dspc.dsp.json.gz
var bundleGz []byte

// maxDepth is the recursion limit for compiler runs; the recursive-descent
// parser needs more than the default 1000 frames on deeply nested input.
const maxDepth = 3000

// Bundle decodes and returns the embedded compiler bundle. Every call returns
// a fresh copy, so callers may hand it to interp.New without sharing state.
func Bundle() (map[string]any, error) {
	data, err := gunzip(bundleGz)
	if err != nil {
		return nil, fmt.Errorf("dspc: embedded bundle is corrupt: %w", err)
	}
	bundle, err := interp.LoadBundle(data)
	if err != nil {
		return nil, fmt.Errorf("dspc: embedded bundle is corrupt: %w", err)
	}
	return bundle, nil
}

func gunzip(data []byte) ([]byte, error) {
	zr, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	defer zr.Close()
	return io.ReadAll(zr)
}

// Result is the outcome of one compiler run.
type Result struct {
	// Bundle is the compiled bundle as JSON text (empty on failure).
	Bundle string
	// Stderr holds compiler diagnostics.
	Stderr string
	// Code is the compiler's exit code; 0 means success.
	Code int
}

// CompileOptions controls source lookup and standard-library linking.
type CompileOptions struct {
	// Debug includes source lines in emitted modules for tracebacks.
	Debug bool
	// SearchPaths are checked after the entry directory, in declaration order.
	SearchPaths []string
	// SelfContained embeds reachable library modules instead of a store reference.
	SelfContained bool
	// NoStdlib disables standard-library resolution and emits no store reference.
	NoStdlib bool
	// Sources replaces the host filesystem when non-nil. Paths are rooted at /.
	// CompileSource always uses an overlay and adds its explicit entry source.
	Sources map[string]string
}

// CompileFile compiles the entry and its source dependencies. With no Sources
// overlay, the compiler may read only the entry directory and SearchPaths.
// Standard-library imports link to interp's shipped store by default.
func CompileFile(filename string, config CompileOptions, opts ...interp.Option) (Result, error) {
	argv, readPaths := compileArgs(filename, config, config.Sources != nil)
	if config.Sources != nil {
		opts = append([]interp.Option{interp.WithFSOverlay(sourceOverlay(config.Sources))}, opts...)
	}
	return run(argv, readPaths, opts)
}

// CompileSource compiles an in-memory entry without host filesystem access.
// Imports use config.Sources and the shipped compiled standard library. The
// explicit source takes precedence over the same entry in config.Sources.
func CompileSource(filename, source string, config CompileOptions, opts ...interp.Option) (Result, error) {
	overlay := sourceOverlay(config.Sources)
	overlay[virtualPath(filename)] = source
	argv, readPaths := compileArgs(filename, config, true)
	return run(argv, readPaths, append([]interp.Option{interp.WithFSOverlay(overlay)}, opts...))
}

func virtualPath(filename string) string {
	return path.Clean("/" + filename)
}

func sourceOverlay(sources map[string]string) map[string]string {
	overlay := make(map[string]string, len(sources)+1)
	for filename, source := range sources {
		overlay[virtualPath(filename)] = source
	}
	return overlay
}

func compileArgs(filename string, config CompileOptions, virtual bool) ([]string, []string) {
	dir := filepath.Dir(filename)
	if virtual {
		filename = virtualPath(filename)
		dir = path.Dir(filename)
	}
	argv := []string{"dspc", filename}
	readPaths := []string{dir, filename}
	for _, searchPath := range config.SearchPaths {
		if virtual {
			searchPath = virtualPath(searchPath)
		}
		argv = append(argv, "--path", searchPath)
		readPaths = append(readPaths, searchPath)
	}
	if config.Debug {
		argv = append(argv, "--debug")
	}
	if config.SelfContained {
		argv = append(argv, "--self-contained")
	}
	if config.NoStdlib {
		argv = append(argv, "--no-stdlib")
	}
	return argv, readPaths
}

// CompileSnippet compiles source for exec(), eval() and compile() and returns
// the statement list of the resulting module. It matches the signature
// expected by interp.WithSnippetCompiler.
func CompileSnippet(source, mode string) ([]any, error) {
	r, err := CompileSource("/snippet/__snippet__.py", source, CompileOptions{NoStdlib: true})
	if err != nil {
		return nil, err
	}
	if r.Code != 0 {
		msg := strings.TrimSpace(r.Stderr)
		msg = strings.TrimPrefix(msg, "diaspore: compile error: ")
		if i := strings.LastIndex(msg, "\n"); i >= 0 {
			msg = msg[i+1:]
		}
		if msg == "" {
			msg = "invalid syntax"
		}
		return nil, fmt.Errorf("%s", msg)
	}
	bundle, err := interp.LoadBundle([]byte(strings.TrimSpace(r.Bundle)))
	if err != nil {
		return nil, fmt.Errorf("dspc: compiler produced invalid JSON: %w", err)
	}
	main, _ := bundle["main"].(string)
	mods, _ := bundle["modules"].(map[string]any)
	mod, _ := mods[main].(map[string]any)
	body, _ := mod["body"].([]any)
	return body, nil
}

func run(argv, readPaths []string, extra []interp.Option) (Result, error) {
	compiler, err := Bundle()
	if err != nil {
		return Result{}, err
	}
	var stdout, stderr bytes.Buffer
	opts := []interp.Option{
		interp.WithArgs(argv),
		interp.WithCaps("fs"),
		interp.WithPolicy(map[string]map[string][]string{
			"fs": {"read": readPaths, "write": {}},
		}),
		interp.WithMaxDepth(maxDepth),
		interp.WithStdout(&stdout),
		interp.WithStderr(&stderr),
	}
	vm := interp.New(compiler, append(opts, extra...)...)
	defer vm.Close()
	code := vm.Run()
	vm.Close()
	return Result{Bundle: stdout.String(), Stderr: stderr.String(), Code: code}, nil
}
