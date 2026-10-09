// Package interp_test exercises the public embedding API.
package interp_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/interp"
)

// appBundle is `diaspore build --binding host` output for:
//
//	import host
//
//	def double(x):
//	    return x * 2
//
//	print("host says", host.greet("diaspore"))
const appBundle = `{"diaspore":1,"main":"app","modules":{"app":{"name":"app","file":"app.py","body":[
{"op":"import","module":"host","alias":"host","ln":1},
{"op":"func","name":"double","gen":false,
 "params":{"args":["x"],"defaults":[],"vararg":null,"kwonly":[],"kwdefaults":[],"kwarg":null},
 "locals":["x"],"gdecl":[],"ndecl":[],
 "body":[{"op":"return","ln":4,"v":{"op":"binop","kind":"mul",
   "l":{"op":"name","id":"x"},"r":{"op":"const","t":"int","v":"2"}}}],"ln":3},
{"op":"expr","v":{"op":"call","f":{"op":"name","id":"print"},
 "args":[{"op":"const","t":"str","v":"host says"},
   {"op":"call","f":{"op":"attr","o":{"op":"name","id":"host"},"n":"greet"},
    "args":[{"op":"const","t":"str","v":"diaspore"}],"kw":[]}],"kw":[]},"ln":6}]}}}`

func decodeBundle(t *testing.T) map[string]any {
	t.Helper()
	var bundle map[string]any
	if err := json.Unmarshal([]byte(appBundle), &bundle); err != nil {
		t.Fatalf("bundle JSON: %v", err)
	}
	return bundle
}

// TestEmbeddingRunAndCall checks host bindings and calls into a loaded module.
func TestEmbeddingRunAndCall(t *testing.T) {
	var out bytes.Buffer
	vm := interp.New(decodeBundle(t),
		interp.WithBindings("host", map[string]interp.NativeFunc{
			"greet": func(args []interp.Value) (interp.Value, error) {
				return "hi " + args[0].(string), nil
			},
		}),
		interp.WithMaxSteps(1_000_000),
		interp.WithArgs([]string{"app"}),
		interp.WithStdout(&out),
		interp.WithStderr(&out),
	)
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d, want 0; output: %s", code, out.String())
	}
	if got, want := out.String(), "host says hi diaspore\n"; got != want {
		t.Fatalf("stdout = %q, want %q", got, want)
	}
	v, err := vm.Call("app", "double", int64(21))
	if err != nil {
		t.Fatalf("Call: %v", err)
	}
	if v != int64(42) {
		t.Fatalf("Call result = %v, want 42", v)
	}
}

// TestEmbeddingDiasporeError checks that sandbox exceptions reach the host as Raised errors.
func TestEmbeddingDiasporeError(t *testing.T) {
	var out bytes.Buffer
	vm := interp.New(decodeBundle(t),
		interp.WithBindings("host", map[string]interp.NativeFunc{
			"greet": func(args []interp.Value) (interp.Value, error) { return interp.None, nil },
		}),
		interp.WithStdout(&out), interp.WithStderr(&out),
	)
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d", code)
	}
	_, err := vm.Call("app", "double", interp.None)
	var raised *interp.Raised
	if !errors.As(err, &raised) {
		t.Fatalf("Call error = %v, want *interp.Raised", err)
	}
	if want := "TypeError: unsupported operand type(s) for *: 'NoneType' and 'int'"; err.Error() != want {
		t.Fatalf("error = %q, want %q", err.Error(), want)
	}
	out.Reset()
	if code := vm.ReportError(err); code != 1 {
		t.Fatalf("ReportError() = %d, want 1", code)
	}
	if !strings.Contains(out.String(), "Traceback (most recent call last):") ||
		!strings.Contains(out.String(), err.Error()) {
		t.Fatalf("ReportError output = %q, want traceback and exception", out.String())
	}
}
