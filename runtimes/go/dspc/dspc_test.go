package dspc_test

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
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
			result, err := dspc.CompileFile(entry, dspc.CompileOptions{SearchPaths: tc.paths})
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

func compileSourceBundle(t *testing.T, filename, source string, config dspc.CompileOptions) map[string]any {
	t.Helper()
	result, err := dspc.CompileSource(filename, source, config)
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
	return bundle
}

func runCompiledBundle(t *testing.T, bundle map[string]any) (int, string) {
	t.Helper()
	var output bytes.Buffer
	vm := interp.New(bundle, interp.WithStdout(&output), interp.WithStderr(&output))
	defer vm.Close()
	code := vm.Run()
	return code, output.String()
}

func TestCompileSourceStdlibModes(t *testing.T) {
	id, err := interp.StdlibID()
	if err != nil {
		t.Fatal(err)
	}
	for _, selfContained := range []bool{false, true} {
		name := "linked"
		if selfContained {
			name = "self-contained"
		}
		t.Run(name, func(t *testing.T) {
			bundle := compileSourceBundle(t, "/src/main.py",
				"import json\nprint(json.dumps([1, 2], separators=(',', ':')))\n",
				dspc.CompileOptions{SelfContained: selfContained})
			modules := bundle["modules"].(map[string]any)
			if selfContained {
				if _, linked := bundle["stdlib"]; linked {
					t.Fatal("self-contained bundle has a stdlib reference")
				}
				if _, included := modules["json"]; !included {
					t.Fatal("self-contained bundle omitted its imported json module")
				}
			} else {
				if bundle["stdlib"] != id {
					t.Fatalf("stdlib reference = %v, want %s", bundle["stdlib"], id)
				}
				if len(modules) != 1 {
					t.Fatalf("linked bundle contains %d modules, want only the entry", len(modules))
				}
			}
			if code, output := runCompiledBundle(t, bundle); code != 0 || output != "[1,2]\n" {
				t.Fatalf("run: exit %d, output %q", code, output)
			}
		})
	}
}

func TestCompileSourceOverlayAndStdlibShadowing(t *testing.T) {
	sources := map[string]string{
		"src/main.py":   "raise RuntimeError('overlay entry must not win')\n",
		"src/json.py":   "value = 'caller json'\n",
		"vendor/mod.py": "value = 42\n",
	}
	for _, selfContained := range []bool{false, true} {
		bundle := compileSourceBundle(t, "src/main.py",
			"import json\nimport mod\nprint(json.value, mod.value)\n",
			dspc.CompileOptions{Sources: sources, SearchPaths: []string{"vendor"}, SelfContained: selfContained})
		if code, output := runCompiledBundle(t, bundle); code != 0 || output != "caller json 42\n" {
			t.Fatalf("run (self-contained=%t): exit %d, output %q", selfContained, code, output)
		}
	}
	if sources["src/main.py"] != "raise RuntimeError('overlay entry must not win')\n" {
		t.Fatal("CompileSource mutated its caller's source map")
	}
}

func TestCompileSourceDoesNotReadHostFiles(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "hidden.py"), []byte("value = 'host'\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	bundle := compileSourceBundle(t, filepath.Join(root, "main.py"), "import hidden\n", dspc.CompileOptions{})
	if _, included := bundle["modules"].(map[string]any)["hidden"]; included {
		t.Fatal("in-memory compilation read an ambient host file")
	}
	if code, output := runCompiledBundle(t, bundle); code == 0 || !strings.Contains(output, "hidden") {
		t.Fatalf("run unexpectedly resolved a host module: exit %d, output %q", code, output)
	}
}

func TestCompileFileSourceOverlay(t *testing.T) {
	result, err := dspc.CompileFile("virtual/main.py", dspc.CompileOptions{Sources: map[string]string{
		"virtual/main.py":    "import sibling\nprint(sibling.value)\n",
		"virtual/sibling.py": "value = 17\n",
	}})
	if err != nil || result.Code != 0 {
		t.Fatalf("overlay compile: %v, exit %d: %s", err, result.Code, result.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(result.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	if code, output := runCompiledBundle(t, bundle); code != 0 || output != "17\n" {
		t.Fatalf("overlay run: exit %d, output %q", code, output)
	}
}

func TestCompileWithoutStdlib(t *testing.T) {
	bundle := compileSourceBundle(t, "/src/main.py", "print(17)\n", dspc.CompileOptions{NoStdlib: true})
	if _, linked := bundle["stdlib"]; linked {
		t.Fatal("NoStdlib emitted a standard-library reference")
	}
	if len(bundle["modules"].(map[string]any)) != 1 {
		t.Fatal("NoStdlib embedded implicit standard-library modules")
	}
	if code, output := runCompiledBundle(t, bundle); code != 0 || output != "17\n" {
		t.Fatalf("standalone run: exit %d, output %q", code, output)
	}
	bundle = compileSourceBundle(t, "/src/main.py", "import json\n", dspc.CompileOptions{NoStdlib: true})
	if _, included := bundle["modules"].(map[string]any)["json"]; included {
		t.Fatal("NoStdlib resolved a standard-library source module")
	}
	if code, output := runCompiledBundle(t, bundle); code == 0 || !strings.Contains(output, "json") {
		t.Fatalf("standalone bundle silently resolved the runtime store: exit %d, output %q", code, output)
	}
}
