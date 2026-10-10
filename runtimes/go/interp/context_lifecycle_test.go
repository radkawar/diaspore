package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeTaskContextLifecycle(t *testing.T) {
	const source = `import _contextvars
import contextvars
import tasks

value = contextvars.ContextVar('value', default=None)
seen = []
saved = []
def worker(fail):
    ident = tasks.get_ident()
    value.set(ident)
    seen.append(ident)
    saved.append(contextvars.copy_context())
    if fail:
        raise ValueError('worker failed')
    return ident

ident = tasks.spawn(worker, False).join()
assert ident not in _contextvars._contexts
assert saved[0].get(value) == ident
try:
    tasks.spawn(worker, True).join()
except ValueError as error:
    assert str(error) == 'worker failed'
else:
    raise AssertionError('worker exception lost')
assert seen[1] not in _contextvars._contexts
assert saved[1].get(value) == seen[1]
assert value.get() is None
`
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	vm := interp.New(bundle, interp.WithCaps("spawn"), interp.WithStderr(&stderr),
		interp.WithSnippetCompiler(dspc.CompileSnippet))
	defer vm.Close()
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d: %s", code, stderr.String())
	}
}
