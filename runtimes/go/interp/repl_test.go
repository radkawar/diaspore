package interp_test

import (
	"bytes"
	"strings"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestInteractiveCode(t *testing.T) {
	cases := []struct {
		name   string
		source string
		output string
		code   int
	}{
		{"expression", `x = 2
exec(compile("x * 21", "<stdin>", "single"))
exec(compile("None", "<stdin>", "single"))
exec(compile("print(x)", "<stdin>", "single"))`, "42\n2\n", 0},
		{"compound", `exec(compile("for x in range(2):\n    x\n", "<stdin>", "single"))`, "0\n1\n", 0},
		{"function", `exec(compile("def f():\n    42\n\nf()", "<stdin>", "single"))`, "", 0},
		{"traceback", `exec(compile("1 / 0", "<stdin>", "single"))`, "", 1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			compiled, err := dspc.CompileSource("/src/main.py", tc.source)
			if err != nil || compiled.Code != 0 {
				t.Fatalf("compile: %v %s", err, compiled.Stderr)
			}
			bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
			if err != nil {
				t.Fatal(err)
			}
			var stdout, stderr bytes.Buffer
			vm := interp.New(bundle, interp.WithSnippetCompiler(dspc.CompileSnippet),
				interp.WithStdout(&stdout), interp.WithStderr(&stderr))
			if code := vm.Run(); code != tc.code {
				t.Fatalf("Run() = %d, want %d: %s", code, tc.code, stderr.String())
			}
			if stdout.String() != tc.output {
				t.Fatalf("stdout = %q, want %q", stdout.String(), tc.output)
			}
			if tc.name == "traceback" {
				for _, fragment := range []string{`File "<stdin>", line 1`, "1 / 0", "ZeroDivisionError"} {
					if !strings.Contains(stderr.String(), fragment) {
						t.Fatalf("traceback lacks %q: %s", fragment, stderr.String())
					}
				}
			}
		})
	}
}
