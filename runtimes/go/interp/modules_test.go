package interp_test

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestAddModulesPreservesState(t *testing.T) {
	decode := func(source string) map[string]any {
		t.Helper()
		var bundle map[string]any
		if err := json.Unmarshal([]byte(source), &bundle); err != nil {
			t.Fatal(err)
		}
		return bundle
	}
	initial := decode(`{"diaspore":1,"main":"session","modules":{"session":{"body":[{"op":"assign","targets":[{"op":"name","id":"x"}],"v":{"op":"const","t":"int","v":"42"}}]}}}`)
	var out bytes.Buffer
	vm := interp.New(initial, interp.WithStdout(&out), interp.WithStderr(&out))
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d: %s", code, out.String())
	}
	addition := decode(`{"diaspore":1,"main":"plugin","modules":{"session":{"body":[]},"plugin":{"body":[{"op":"importfrom","module":"session","names":[{"n":"x","alias":"x"}]},{"op":"expr","v":{"op":"call","f":{"op":"name","id":"print"},"args":[{"op":"name","id":"x"}]}}]}}}`)
	if err := vm.AddModules(addition); err != nil {
		t.Fatal(err)
	}
	if _, err := vm.Call("plugin", "missing"); err == nil {
		t.Fatal("expected missing function error after module initialization")
	}
	if got := strings.TrimSpace(out.String()); got != "42" {
		t.Fatalf("module output = %q, want 42", got)
	}
	if err := vm.AddModules(map[string]any{}); err == nil {
		t.Fatal("accepted invalid bundle")
	}
	durable := interp.New(initial, interp.WithDurable())
	if err := durable.AddModules(addition); err == nil {
		t.Fatal("accepted module additions in durable mode")
	}
}
