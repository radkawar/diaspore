// Package dspc embeds dspc, the self-hosted Diaspore compiler, and runs it on
// the Go VM. It lets Go hosts compile Python source to bundles with no Python
// installation.
//
// The package also embeds Diaspore's pure-Python standard library modules
// (collections, itertools, functools, ...). The compiler sees them as
// read-only files under /stdlib, searched after the entry directory and any
// caller-supplied search paths, matching the Python CLI.
//
// Both embedded files are build artifacts: run `make generate` from the
// repository root to produce dspc.dsp.json.gz and stdlib.json.gz before
// building this package.
package dspc

import (
	"bytes"
	"compress/gzip"
	_ "embed" // needed for the go:embed directive below
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"sync"

	"github.com/radkawar/diaspore/runtimes/go/interp"
)

//go:embed dspc.dsp.json.gz
var bundleGz []byte

//go:embed stdlib.json.gz
var stdlibGz []byte

// stdlibRoot is the virtual directory the embedded standard library lives in.
const stdlibRoot = "/stdlib"

var stdlib = sync.OnceValues(func() (map[string]string, error) {
	data, err := gunzip(stdlibGz)
	if err != nil {
		return nil, fmt.Errorf("dspc: embedded stdlib is corrupt: %w", err)
	}
	var files map[string]string
	if err := json.Unmarshal(data, &files); err != nil {
		return nil, fmt.Errorf("dspc: embedded stdlib is corrupt: %w", err)
	}
	return files, nil
})

// Stdlib returns the embedded standard library as a map from virtual path
// (under /stdlib) to module source. The map is shared; callers must not
// modify it.
func Stdlib() (map[string]string, error) { return stdlib() }

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
	var bundle map[string]any
	if err := json.Unmarshal(data, &bundle); err != nil {
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

// CompileFile compiles the entry module at path and every module it imports.
// Search paths are checked in order after the entry's directory, then the
// embedded standard library. The compiler may read only files under those
// directories.
func CompileFile(path string, debug bool, searchPaths []string, opts ...interp.Option) (Result, error) {
	dir := "."
	if i := strings.LastIndexAny(path, `/\`); i >= 0 {
		dir = path[:i]
	}
	argv := []string{"dspc", path}
	readPaths := []string{dir, path}
	for _, searchPath := range searchPaths {
		argv = append(argv, "--path", searchPath)
		readPaths = append(readPaths, searchPath)
	}
	argv = append(argv, "--stdlib", stdlibRoot)
	readPaths = append(readPaths, stdlibRoot)
	if debug {
		argv = append(argv, "--debug")
	}
	files, err := stdlib()
	if err != nil {
		return Result{}, err
	}
	return run(argv, readPaths, append([]interp.Option{interp.WithVirtualFiles(files)}, opts...))
}

// CompileSource compiles a single in-memory module. The source is presented
// to the compiler as the file at path; no real file system access is granted.
// Imports resolve against the embedded standard library.
func CompileSource(path, source string, opts ...interp.Option) (Result, error) {
	dir := "/"
	if i := strings.LastIndexByte(path, '/'); i > 0 {
		dir = path[:i]
	}
	files, err := stdlib()
	if err != nil {
		return Result{}, err
	}
	overlay := make(map[string]string, len(files)+1)
	for name, src := range files {
		overlay[name] = src
	}
	overlay[path] = source
	return run([]string{"dspc", "--stdlib", stdlibRoot, path}, []string{dir, stdlibRoot},
		append([]interp.Option{interp.WithFSOverlay(overlay)}, opts...))
}

// CompileSnippet compiles source for exec(), eval() and compile() and returns
// the statement list of the resulting module. It matches the signature
// expected by interp.WithSnippetCompiler.
func CompileSnippet(source, mode string) ([]any, error) {
	r, err := CompileSource("/snippet/__snippet__.py", source)
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
	var bundle map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(r.Bundle)), &bundle); err != nil {
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
	code := interp.New(compiler, append(opts, extra...)...).Run()
	return Result{Bundle: stdout.String(), Stderr: stderr.String(), Code: code}, nil
}
