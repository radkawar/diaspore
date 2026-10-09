// Package dspc embeds dspc, the self-hosted Diaspore compiler, and runs it on
// the Go VM. It lets Go hosts compile Python source to bundles with no Python
// installation.
//
// The embedded bundle is a build artifact: run `make generate` from the
// repository root to produce dspc.dsp.json.gz before building this package.
package dspc

import (
	"bytes"
	"compress/gzip"
	_ "embed" // needed for the go:embed directive below
	"encoding/json"
	"fmt"
	"io"
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
	zr, err := gzip.NewReader(bytes.NewReader(bundleGz))
	if err != nil {
		return nil, fmt.Errorf("dspc: embedded bundle is corrupt: %w", err)
	}
	defer zr.Close()
	data, err := io.ReadAll(zr)
	if err != nil {
		return nil, fmt.Errorf("dspc: embedded bundle is corrupt: %w", err)
	}
	var bundle map[string]any
	if err := json.Unmarshal(data, &bundle); err != nil {
		return nil, fmt.Errorf("dspc: embedded bundle is corrupt: %w", err)
	}
	return bundle, nil
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
// Search paths are checked in order after the entry's directory. The compiler
// may read only files under those directories.
func CompileFile(path string, debug bool, searchPaths []string, opts ...interp.Option) (Result, error) {
	dir := "."
	if i := strings.LastIndexAny(path, `/\`); i >= 0 {
		dir = path[:i]
	}
	argv := make([]string, 2, 3+2*len(searchPaths))
	argv[0], argv[1] = "dspc", path
	readPaths := make([]string, 2, 2+len(searchPaths))
	readPaths[0], readPaths[1] = dir, path
	for _, searchPath := range searchPaths {
		argv = append(argv, "--path", searchPath)
		readPaths = append(readPaths, searchPath)
	}
	if debug {
		argv = append(argv, "--debug")
	}
	return run(argv, map[string][]string{"read": readPaths, "write": {}}, nil, opts)
}

// CompileSource compiles a single in-memory module. The source is presented
// to the compiler as the file at path; no real file system access is granted.
func CompileSource(path, source string, opts ...interp.Option) (Result, error) {
	dir := "/"
	if i := strings.LastIndexByte(path, '/'); i > 0 {
		dir = path[:i]
	}
	return run([]string{"dspc", path},
		map[string][]string{"read": {dir}, "write": {}},
		map[string]string{path: source}, opts)
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

func run(argv []string, fsPolicy map[string][]string, overlay map[string]string,
	extra []interp.Option) (Result, error) {
	compiler, err := Bundle()
	if err != nil {
		return Result{}, err
	}
	var stdout, stderr bytes.Buffer
	opts := []interp.Option{
		interp.WithArgs(argv),
		interp.WithCaps("fs"),
		interp.WithPolicy(map[string]map[string][]string{"fs": fsPolicy}),
		interp.WithMaxDepth(maxDepth),
		interp.WithStdout(&stdout),
		interp.WithStderr(&stderr),
	}
	if overlay != nil {
		opts = append(opts, interp.WithFSOverlay(overlay))
	}
	code := interp.New(compiler, append(opts, extra...)...).Run()
	return Result{Bundle: stdout.String(), Stderr: stderr.String(), Code: code}, nil
}
