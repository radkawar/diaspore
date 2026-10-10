package main

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/interp"
)

var repoRoot = filepath.Join("..", "..", "..", "..")

func haveUV(t *testing.T) {
	t.Helper()
	if _, err := exec.LookPath("uv"); err != nil {
		t.Skip("uv not available")
	}
}

func decodeJSON(t *testing.T, data []byte) any {
	t.Helper()
	var v any
	if err := json.Unmarshal(data, &v); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	return v
}

// omitDefaults mirrors the native compiler's compact_bundle pass (spec 2.2)
// so verbose and compact emissions of the same bundle compare equal.
var omitDefaults = []struct {
	key     string
	isEmpty func(v any) bool
}{
	{"orelse", isEmptyList}, {"finalbody", isEmptyList}, {"handlers", isEmptyList},
	{"kw", isEmptyList}, {"args", isEmptyList}, {"elts", isEmptyList},
	{"entries", isEmptyList}, {"bases", isEmptyList}, {"gdecl", isEmptyList},
	{"ndecl", isEmptyList}, {"locals", isEmptyList}, {"defaults", isEmptyList},
	{"kwonly", isEmptyList}, {"kwdefaults", isEmptyList},
	{"gen", func(v any) bool { return v == false }},
	{"vararg", isNull}, {"kwarg", isNull}, {"conv", isNull}, {"spec", isNull},
	{"t", isNull}, {"name", isNull},
}

func isEmptyList(v any) bool { l, ok := v.([]any); return ok && len(l) == 0 }
func isNull(v any) bool      { return v == nil }

func stripDefaults(v any) {
	switch t := v.(type) {
	case []any:
		for _, x := range t {
			stripDefaults(x)
		}
	case map[string]any:
		isConst := t["op"] == "const"
		for _, od := range omitDefaults {
			if val, ok := t[od.key]; ok && od.isEmpty(val) {
				if isConst && od.key == "t" {
					continue
				}
				delete(t, od.key)
			}
		}
		for _, x := range t {
			stripDefaults(x)
		}
	}
}

// normalizedBundle decodes and canonicalizes a bundle for comparison.
func normalizedBundle(t *testing.T, data []byte) any {
	v := decodeJSON(t, data)
	stripDefaults(v)
	return v
}

// TestBuildMatchesNative proves `diaspore-go build` (the embedded self-hosted
// compiler running on this VM) produces bundles structurally identical to
// the native Python compiler.
func TestBuildMatchesNative(t *testing.T) {
	haveUV(t)
	bin := filepath.Join(t.TempDir(), "diaspore-go-test")
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("go build: %v\n%s", err, out)
	}
	entries := []string{
		"tests/corpus/smoke.py",
		"tests/corpus/13_classes.py",
		"tests/corpus/29_imports/main.py",
		"selfhost/dspc.py",
		"examples/http/server.py",
	}
	for _, entry := range entries {
		for _, mode := range []struct {
			name string
			args []string
		}{
			{"linked", nil},
			{"self-contained", []string{"--self-contained"}},
		} {
			t.Run(filepath.Base(filepath.Dir(entry))+"/"+filepath.Base(entry)+"/"+mode.name, func(t *testing.T) {
				goOut := filepath.Join(t.TempDir(), "go.json")
				pyOut := filepath.Join(t.TempDir(), "py.json")
				goArgs := append([]string{"build", entry, "-o", goOut}, mode.args...)
				cmd := exec.Command(bin, goArgs...)
				cmd.Dir = repoRoot
				if out, err := cmd.CombinedOutput(); err != nil {
					t.Fatalf("diaspore-go build %s: %v\n%s", entry, err, out)
				}
				pyArgs := append([]string{"run", "diaspore", "build", entry, "-o", pyOut}, mode.args...)
				cmd = exec.Command("uv", pyArgs...)
				cmd.Dir = repoRoot
				if out, err := cmd.CombinedOutput(); err != nil {
					t.Fatalf("uv run diaspore build %s: %v\n%s", entry, err, out)
				}
				goBundle, err := os.ReadFile(goOut)
				if err != nil {
					t.Fatal(err)
				}
				pyBundle, err := os.ReadFile(pyOut)
				if err != nil {
					t.Fatal(err)
				}
				if !reflect.DeepEqual(normalizedBundle(t, goBundle), normalizedBundle(t, pyBundle)) {
					t.Fatalf("bundle divergence on %s (%s)", entry, mode.name)
				}
			})
		}
	}
}

func TestBuildSearchPaths(t *testing.T) {
	bin := filepath.Join(t.TempDir(), "diaspore-go-test")
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("go build: %v\n%s", err, out)
	}
	root := t.TempDir()
	files := map[string]string{
		"app/main.py":       "import localmod\nimport shared\nimport extra\nprint(localmod.value, shared.value, extra.value)\n",
		"app/json_usage.py": "import json\nprint(json.dumps([1, 2], separators=(',', ':')))\n",
		"app/localmod.py":   "value = 'entry'\n",
		"first/localmod.py": "value = 'wrong'\n",
		"first/shared.py":   "value = 'first'\n",
		"second/shared.py":  "value = 'second'\n",
		"second/extra.py":   "value = 'extra'\n",
	}
	for name, source := range files {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(source), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	for _, tc := range []struct {
		name string
		args []string
		want string
	}{
		{"ordered", []string{"--path", "first", "app/main.py", "--path", "second"}, "entry first extra\n"},
		{"reversed", []string{"app/main.py", "--path=second", "--path=first"}, "entry second extra\n"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			bundle := filepath.Join(t.TempDir(), "app.dsp.json")
			args := append([]string{"build"}, tc.args...)
			args = append(args, "-o", bundle)
			cmd := exec.Command(bin, args...)
			cmd.Dir = root
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("build: %v\n%s", err, out)
			}
			out, err := exec.Command(bin, "run", bundle).CombinedOutput()
			if err != nil {
				t.Fatalf("run: %v\n%s", err, out)
			}
			if string(out) != tc.want {
				t.Fatalf("output %q, want %q", out, tc.want)
			}
		})
	}
	for _, tc := range []struct {
		name string
		args []string
		want string
	}{
		{"no paths", []string{"app/main.py"}, "shared"},
		{"one path", []string{"app/main.py", "--path", "first"}, "extra"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			bundle := filepath.Join(t.TempDir(), "app.dsp.json")
			args := append([]string{"build"}, tc.args...)
			args = append(args, "-o", bundle)
			cmd := exec.Command(bin, args...)
			cmd.Dir = root
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("build: %v\n%s", err, out)
			}
			data, err := os.ReadFile(bundle)
			if err != nil {
				t.Fatal(err)
			}
			modules := decodeJSON(t, data).(map[string]any)["modules"].(map[string]any)
			if _, ok := modules[tc.want]; ok {
				t.Fatalf("unrequested module %q included in bundle", tc.want)
			}
			out, err := exec.Command(bin, "run", bundle).CombinedOutput()
			if err == nil {
				t.Fatalf("run unexpectedly succeeded: %s", out)
			}
			if !strings.Contains(string(out), tc.want) {
				t.Fatalf("output %q does not contain %q", out, tc.want)
			}
		})
	}
	id, err := interp.StdlibID()
	if err != nil {
		t.Fatal(err)
	}
	for _, selfContained := range []bool{false, true} {
		name := "linked stdlib"
		if selfContained {
			name = "self-contained stdlib"
		}
		t.Run(name, func(t *testing.T) {
			bundlePath := filepath.Join(t.TempDir(), "library.dsp.json")
			args := []string{"build", "app/json_usage.py", "-o", bundlePath}
			if selfContained {
				args = append(args, "--self-contained")
			}
			cmd := exec.Command(bin, args...)
			cmd.Dir = root
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("build: %v\n%s", err, out)
			}
			data, err := os.ReadFile(bundlePath)
			if err != nil {
				t.Fatal(err)
			}
			bundle := decodeJSON(t, data).(map[string]any)
			modules := bundle["modules"].(map[string]any)
			if selfContained {
				if _, linked := bundle["stdlib"]; linked {
					t.Fatal("--self-contained left a store reference in the bundle")
				}
				if _, included := modules["json"]; !included {
					t.Fatal("--self-contained omitted json")
				}
			} else {
				if bundle["stdlib"] != id {
					t.Fatalf("stdlib reference = %v, want %s", bundle["stdlib"], id)
				}
				if _, included := modules["json"]; included {
					t.Fatal("default build embedded a linked standard-library module")
				}
			}
			output, err := exec.Command(bin, "run", bundlePath).CombinedOutput()
			if err != nil || string(output) != "[1,2]\n" {
				t.Fatalf("run: %v, output %q", err, output)
			}
		})
	}
	t.Run("missing value", func(t *testing.T) {
		cmd := exec.Command(bin, "build", "app/main.py", "--path")
		cmd.Dir = root
		out, err := cmd.CombinedOutput()
		if err == nil {
			t.Fatalf("build unexpectedly succeeded: %s", out)
		}
		if !strings.Contains(string(out), "--path requires a value") {
			t.Fatalf("unexpected diagnostic: %s", out)
		}
	})
}
