package interp_test

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func traceBundle(t *testing.T, source string) map[string]any {
	t.Helper()
	path := filepath.Join(t.TempDir(), "main.py")
	if err := os.WriteFile(path, []byte(source), 0600); err != nil {
		t.Fatal(err)
	}
	compiled, err := dspc.CompileFile(path, true, nil)
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	return bundle
}

func TestTracerFrames(t *testing.T) {
	bundle := traceBundle(t, `def factorial(n):
    if n <= 1:
        return 1
    smaller = factorial(n - 1)
    return n * smaller

def numbers():
    yield 2
    yield 3

answer = factorial(3)
for number in numbers():
    print(number)
try:
    raise ValueError("caught")
except ValueError:
    print(answer)
`)
	var events []interp.TraceEvent
	var stdout, stderr bytes.Buffer
	vm := interp.New(bundle, interp.WithStdout(&stdout), interp.WithStderr(&stderr),
		interp.WithTracer(func(event interp.TraceEvent) { events = append(events, event) }))
	if code := vm.Run(); code != 0 {
		t.Fatalf("exit %d: %s", code, stderr.String())
	}
	if stdout.String() != "2\n3\n6\n" {
		t.Fatalf("output: %q", stdout.String())
	}
	kinds := map[string]int{}
	var nested, resumed, answer bool
	for _, event := range events {
		kinds[event.Kind]++
		if event.Depth != len(event.Stack)-1 {
			t.Fatalf("depth: %+v", event)
		}
		if event.Kind == "statement" && (event.Line <= 0 || event.Module == "") {
			t.Fatalf("location: %+v", event)
		}
		if event.Function == "factorial" && event.Depth == 3 && event.Locals["n"] == "1" {
			nested = true
		}
		if event.Function == "numbers" && event.Kind == "call" && event.Depth == 1 {
			resumed = true
		}
		if event.Kind == "return" && event.Depth == 0 && event.Locals["answer"] == "6" {
			answer = true
		}
	}
	if !nested || !resumed || !answer {
		t.Fatalf("frame snapshots: nested=%v resumed=%v answer=%v", nested, resumed, answer)
	}
	if kinds["exception"] != 1 || kinds["yield"] != 2 || kinds["call"] != kinds["return"]+kinds["yield"] {
		t.Fatalf("events: %v", kinds)
	}
}

func TestTracerBoundedSnapshots(t *testing.T) {
	source := `class Quiet:
    def __repr__(self):
        print("unexpected repr")
        return "quiet"

obj = Quiet()
obj.value = 42
long_text = "x" * 10000
cycle = []
cycle.append(cycle)
`
	for i := range 40 {
		source += "local_" + strings.Repeat("x", i+1) + " = 1\n"
	}
	source += "print('done')\n"
	bundle := traceBundle(t, source)
	var stdout bytes.Buffer
	var truncated, attributes, cycle bool
	vm := interp.New(bundle, interp.WithStdout(&stdout), interp.WithTracer(func(event interp.TraceEvent) {
		if len(event.Locals) > 32 {
			t.Fatalf("too many locals: %d", len(event.Locals))
		}
		for _, value := range event.Locals {
			if len(value) > 160 {
				t.Fatalf("long repr: %d", len(value))
			}
		}
		truncated = truncated || event.LocalsTruncated
		attributes = attributes || strings.Contains(event.Locals["obj"], "42")
		cycle = cycle || strings.Contains(event.Locals["cycle"], "...")
	}))
	if code := vm.Run(); code != 0 {
		t.Fatalf("exit %d", code)
	}
	if stdout.String() != "done\n" {
		t.Fatalf("repr changed program output: %q", stdout.String())
	}
	if !truncated || !attributes || !cycle {
		t.Fatalf("bounded snapshots: truncated=%v attributes=%v cycle=%v", truncated, attributes, cycle)
	}
}
