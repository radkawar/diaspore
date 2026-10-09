//go:build js && wasm

package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"syscall/js"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

const traceEventLimit = 20_000

type browserTraceEvent struct {
	interp.TraceEvent
	Stdout string `json:"stdout,omitempty"`
	Stderr string `json:"stderr,omitempty"`
}

func diasporeTrace(this js.Value, args []js.Value) (out any) {
	defer func() {
		if r := recover(); r != nil {
			out = result("", fmt.Sprintf("diaspore: internal error: %v\n", r), 70, 0)
		}
	}()
	if len(args) == 0 || args[0].Type() != js.TypeString {
		return result("", "diaspore: diasporeTrace requires a bundle JSON string\n", 2, 0)
	}
	bundle, err := interp.LoadBundle([]byte(args[0].String()))
	if err != nil {
		return result("", "diaspore: "+err.Error()+"\n", 2, 0)
	}
	opts := runOpts{}
	if len(args) > 1 && args[1].Type() == js.TypeString && args[1].String() != "" {
		if err := json.Unmarshal([]byte(args[1].String()), &opts); err != nil {
			return result("", "diaspore: invalid opts JSON: "+err.Error()+"\n", 2, 0)
		}
	}
	if opts.MaxSteps <= 0 || opts.MaxSteps > 1_000_000 {
		opts.MaxSteps = 1_000_000
	}
	if opts.MaxDepth <= 0 || opts.MaxDepth > 200 {
		opts.MaxDepth = 200
	}
	var stdout, stderr bytes.Buffer
	events := make([]browserTraceEvent, 0, 256)
	outAt, errAt := 0, 0
	truncated := false
	tracer := func(event interp.TraceEvent) {
		if len(events) >= traceEventLimit {
			truncated = true
			return
		}
		events = append(events, browserTraceEvent{event, string(stdout.Bytes()[outAt:]), string(stderr.Bytes()[errAt:])})
		outAt, errAt = stdout.Len(), stderr.Len()
	}
	vm := interp.New(bundle, interp.WithStdout(&stdout), interp.WithStderr(&stderr),
		interp.WithArgs(opts.Args), interp.WithMaxSteps(opts.MaxSteps), interp.WithMaxDepth(opts.MaxDepth),
		interp.WithSnippetCompiler(dspc.CompileSnippet), interp.WithTracer(tracer))
	code := vm.Run()
	res := result(stdout.String(), stderr.String(), code, vm.Steps())
	// JSON parsing converts the nested typed snapshots to JavaScript objects.
	data, err := json.Marshal(events)
	if err != nil {
		return result("", err.Error()+"\n", 70, vm.Steps())
	}
	res["events"] = js.Global().Get("JSON").Call("parse", string(data))
	res["truncated"] = truncated
	return res
}

func init() {
	js.Global().Set("diasporeTrace", js.FuncOf(diasporeTrace))
}
