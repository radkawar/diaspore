package interp_test

import (
	"bytes"
	"strings"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestTracebackQualifiedExceptionNames(t *testing.T) {
	cases := []struct{ name, source, terminal string }{
		{"builtin", `assert ValueError.__module__ == 'builtins'
assert ValueError.__qualname__ == 'ValueError'
assert ExceptionGroup.__module__ == 'builtins'
assert ExceptionGroup.__qualname__ == 'ExceptionGroup'
raise ValueError('invalid value')
`, "ValueError: invalid value"},
		{"main_module", `class Problem(Exception):
    pass
raise Problem('main failure')
`, "Problem: main failure"},
		{"nested_qualified_name", `class Outer:
    class Problem(Exception):
        pass
raise Outer.Problem('nested failure')
`, "Outer.Problem: nested failure"},
		{"explicit_module_and_name", `class Problem(Exception):
    __module__ = 'example.errors'
    __qualname__ = 'Container.Failure'
assert Problem.__name__ == 'Problem'
raise Problem('custom failure')
`, "example.errors.Container.Failure: custom failure"},
		{"empty_message", `class Problem(Exception):
    __module__ = 'example.errors'
raise Problem()
`, "example.errors.Problem"},
		{"empty_module", `class Problem(Exception):
    __module__ = ''
raise Problem('empty module')
`, ".Problem: empty module"},
		{"invalid_module", `class Problem(Exception):
    __module__ = None
raise Problem('unknown module')
`, "<unknown>.Problem: unknown module"},
		{"struct_exception_identity", `import struct
assert struct.error.__name__ == 'error'
assert struct.error.__module__ == 'struct'
assert issubclass(struct.error, Exception)
raise struct.error('invalid format')
`, "struct.error: invalid format"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			compiled, err := dspc.CompileSource("/src/main.py", test.source, dspc.CompileOptions{})
			if err != nil || compiled.Code != 0 {
				t.Fatalf("compile: %v %s", err, compiled.Stderr)
			}
			bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
			if err != nil {
				t.Fatal(err)
			}
			var stderr bytes.Buffer
			vm := interp.New(bundle, interp.WithStderr(&stderr))
			defer vm.Close()
			if code := vm.Run(); code != 1 {
				t.Fatalf("Run() = %d; want 1: %s", code, stderr.String())
			}
			output := strings.TrimSuffix(stderr.String(), "\n")
			terminal := output[strings.LastIndexByte(output, '\n')+1:]
			if terminal != test.terminal {
				t.Fatalf("terminal exception = %q; want %q\n%s", terminal, test.terminal, output)
			}
		})
	}
}
