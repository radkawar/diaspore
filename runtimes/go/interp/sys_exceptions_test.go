package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestSysExceptionHooks(t *testing.T) {
	cases := []struct{ name, source string }{
		{"native_module_mutation", `import sys
assert sys.excepthook is sys.__excepthook__
assert sys.unraisablehook is sys.__unraisablehook__
original = sys.unraisablehook
replacement = lambda record: None
sys.unraisablehook = replacement
assert sys.__dict__['unraisablehook'] is replacement
assert sys.__unraisablehook__ is original
del sys.unraisablehook
assert not hasattr(sys, 'unraisablehook')
sys.__dict__['unraisablehook'] = original
assert sys.unraisablehook is original
sys.lifecycle_probe = 42
assert sys.__dict__['lifecycle_probe'] == 42
del sys.lifecycle_probe
try:
    del sys.lifecycle_probe
except AttributeError:
    pass
else:
    raise AssertionError('deleting absent module attribute succeeded')
`},
		{"default_hook_output", `import sys
class Sink:
    def __init__(self):
        self.parts = []
    def write(self, text):
        self.parts.append(text)
        return len(text)
    def text(self):
        return ''.join(self.parts)
class Problem(Exception):
    __module__ = 'example.errors'
    __qualname__ = 'Problem'
saved = sys.stderr
sink = Sink()
sys.stderr = sink
try:
    assert sys.excepthook(ValueError, Problem('message'), None) is None
    assert sink.text() == 'example.errors.Problem: message\n'
    sink.parts.clear()
    try:
        raise Problem('with traceback')
    except Problem as error:
        info = sys.exc_info()
        assert sys.__excepthook__(*info) is None
    text = sink.text()
    assert text.startswith('Traceback (most recent call last):\n')
    assert 'line ' in text and 'in <module>' in text
    assert text.endswith('example.errors.Problem: with traceback\n')
    sink.parts.clear()
    assert sys.excepthook(None, 42, None) is None
    assert sink.text() == 'TypeError: print_exception(): Exception expected for value, int found\n'
finally:
    sys.stderr = saved
`},
		{"atexit_custom_hooks", `import atexit
import sys
atexit._clear()
seen = []
def fail():
    raise ValueError('callback failure')
def hook(details):
    seen.append((details.exc_type, str(details.exc_value), details.object is fail))
original = sys.unraisablehook
sys.unraisablehook = hook
try:
    atexit.register(fail)
    assert atexit._run_exitfuncs() is None
finally:
    sys.unraisablehook = original
assert seen == [(ValueError, 'callback failure', True)]
assert atexit._ncallbacks() == 0
`},
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
			if code := vm.Run(); code != 0 {
				t.Fatalf("Run() = %d: %s", code, stderr.String())
			}
		})
	}
}
