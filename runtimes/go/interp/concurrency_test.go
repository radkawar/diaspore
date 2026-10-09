package interp_test

import (
	"bytes"
	"encoding/json"
	"runtime"
	"testing"
	"time"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func compileBundle(t *testing.T, path string) map[string]any {
	t.Helper()
	result, err := dspc.CompileFile(path, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result.Code != 0 {
		t.Fatalf("compile %s: exit %d: %s", path, result.Code, result.Stderr)
	}
	var bundle map[string]any
	if err := json.Unmarshal([]byte(result.Bundle), &bundle); err != nil {
		t.Fatal(err)
	}
	return bundle
}

// TestTasksStress runs 8 tasks bumping a lock-protected counter plus a
// 4-producer channel fan-in; deterministic output, and race-clean under
// `go test -race`.
func TestTasksStress(t *testing.T) {
	bundle := compileBundle(t, "testdata/tasks_stress.py")
	for i := 0; i < 5; i++ {
		var out bytes.Buffer
		vm := interp.New(bundle,
			interp.WithCaps("spawn"),
			interp.WithStdout(&out), interp.WithStderr(&out))
		if code := vm.Run(); code != 0 {
			t.Fatalf("run %d: exit %d, output %q", i, code, out.String())
		}
		if got, want := out.String(), "2000 304900\n"; got != want {
			t.Fatalf("run %d: output %q, want %q", i, got, want)
		}
	}
}

// TestAbandonedGeneratorsDoNotLeak abandons many started generators and
// checks the goroutine count returns to baseline once their finalizers run.
func TestAbandonedGeneratorsDoNotLeak(t *testing.T) {
	bundle := compileBundle(t, "testdata/genleak.py")
	runtime.GC()
	baseline := runtime.NumGoroutine()
	var out bytes.Buffer
	vm := interp.New(bundle, interp.WithArgs([]string{"genleak", "300"}),
		interp.WithStdout(&out), interp.WithStderr(&out))
	if code := vm.Run(); code != 0 {
		t.Fatalf("exit %d, output %q", code, out.String())
	}
	if got, want := out.String(), "300\n"; got != want {
		t.Fatalf("output %q, want %q", got, want)
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		runtime.GC()
		time.Sleep(10 * time.Millisecond)
		if runtime.NumGoroutine() <= baseline+3 {
			return
		}
	}
	t.Fatalf("goroutines leaked: baseline %d, now %d", baseline, runtime.NumGoroutine())
}
