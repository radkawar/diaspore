package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativePerformanceCounter(t *testing.T) {
	cases := []struct {
		name, source string
		options      []interp.Option
	}{
		{name: "real_monotonic_clock", source: `import time
assert time.perf_counter.__name__ == 'perf_counter'
before = time.monotonic()
start = time.perf_counter()
after = time.monotonic()
assert isinstance(start, float)
assert before <= start <= after
time.sleep(0.02)
assert time.perf_counter() - start >= 0.02
for args, kw, message in (
    ((1,), {}, 'time.perf_counter() takes no arguments (1 given)'),
    ((), {'x': 1}, 'time.perf_counter() takes no keyword arguments'),
):
    try:
        time.perf_counter(*args, **kw)
    except TypeError as exc:
        assert str(exc) == message
    else:
        raise AssertionError('performance clock accepted arguments')
`},
		{name: "explicit_deterministic_clock", options: []interp.Option{interp.WithDeterministic()}, source: `import time
assert time.monotonic() == 0.001
assert time.perf_counter() == 0.002
time.sleep(0.25)
assert time.perf_counter() == 0.253
`},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			bundle := compilePerformanceCounterBundle(t, test.source)
			var stderr bytes.Buffer
			options := append(test.options, interp.WithStderr(&stderr))
			vm := interp.New(bundle, options...)
			defer vm.Close()
			if code := vm.Run(); code != 0 {
				t.Fatalf("Run() = %d: %s", code, stderr.String())
			}
		})
	}
}

func TestPerformanceCounterDurableJournal(t *testing.T) {
	bundle := compilePerformanceCounterBundle(t, `import time
import flow
start = time.perf_counter()
resumed = flow.suspend(start)
assert resumed == start
assert time.perf_counter() >= start
`)
	var stderr bytes.Buffer
	vm := interp.New(bundle, interp.WithDurable(), interp.WithStderr(&stderr))
	outcome := vm.RunDurable()
	vm.Close()
	if !outcome.Suspended {
		t.Fatalf("expected clock suspension, got exit %d: %s", outcome.Code, stderr.String())
	}
	if _, ok := outcome.Payload.(float64); !ok {
		t.Fatalf("performance clock returned %T", outcome.Payload)
	}
	if !bytes.Contains(outcome.Snapshot, []byte(`"perf_counter"`)) {
		t.Fatal("performance clock missing from durable journal")
	}
	resumed := interp.New(bundle, interp.WithDurable(), interp.WithStderr(&stderr))
	defer resumed.Close()
	result, err := resumed.Resume(outcome.Snapshot, outcome.Payload)
	if err != nil {
		t.Fatal(err)
	}
	if result.Suspended || result.Code != 0 {
		t.Fatalf("resume failed with exit %d: %s", result.Code, stderr.String())
	}
}

func compilePerformanceCounterBundle(t *testing.T, source string) map[string]any {
	t.Helper()
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{NoStdlib: true})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	return bundle
}
