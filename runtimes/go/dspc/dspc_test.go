package dspc_test

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestCompileFileSearchPathPolicy(t *testing.T) {
	root := t.TempDir()
	entryDir := filepath.Join(root, "app")
	searchDir := filepath.Join(root, "lib")
	outsideDir := filepath.Join(root, "outside")
	for _, dir := range []string{entryDir, searchDir, outsideDir} {
		if err := os.Mkdir(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	entry := filepath.Join(entryDir, "main.py")
	if err := os.WriteFile(entry, []byte("import external\nprint(external.value)\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	external := filepath.Join(outsideDir, "external.py")
	if err := os.WriteFile(external, []byte("value = 42\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(external, filepath.Join(searchDir, "external.py")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	for _, tc := range []struct {
		name  string
		paths []string
		ok    bool
	}{
		{"unrequested", nil, false},
		{"symlink escape", []string{searchDir}, false},
		{"explicit grant", []string{searchDir, outsideDir}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			result, err := dspc.CompileFile(entry, false, tc.paths)
			if err != nil {
				t.Fatal(err)
			}
			if result.Code != 0 {
				t.Fatalf("compile: exit %d: %s", result.Code, result.Stderr)
			}
			bundle, err := interp.LoadBundle([]byte(result.Bundle))
			if err != nil {
				t.Fatal(err)
			}
			modules := bundle["modules"].(map[string]any)
			if _, included := modules["external"]; included != tc.ok {
				t.Fatalf("external included %t, want %t", included, tc.ok)
			}
			var output bytes.Buffer
			code := interp.New(bundle, interp.WithStdout(&output), interp.WithStderr(&output)).Run()
			if (code == 0) != tc.ok {
				t.Fatalf("run: exit %d, want success %t: %s", code, tc.ok, output.String())
			}
			if tc.ok && output.String() != "42\n" {
				t.Fatalf("output %q, want 42", output.String())
			}
		})
	}
}
