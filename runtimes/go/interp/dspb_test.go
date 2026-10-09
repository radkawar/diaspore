package interp

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"testing"
)

// dspbCompact mirrors the native compiler's compact_bundle pass so
// verbose fixtures normalize to the form DecodeDSPB materializes.
func dspbCompact(v any) {
	type od struct {
		key     string
		isEmpty func(v any) bool
	}
	emptyList := func(v any) bool { l, ok := v.([]any); return ok && len(l) == 0 }
	null := func(v any) bool { return v == nil }
	defaults := []od{
		{"orelse", emptyList}, {"finalbody", emptyList}, {"handlers", emptyList},
		{"kw", emptyList}, {"args", emptyList}, {"elts", emptyList},
		{"entries", emptyList}, {"bases", emptyList}, {"gdecl", emptyList},
		{"ndecl", emptyList}, {"locals", emptyList}, {"defaults", emptyList},
		{"kwonly", emptyList}, {"kwdefaults", emptyList},
		{"gen", func(v any) bool { return v == false }},
		{"vararg", null}, {"kwarg", null}, {"conv", null}, {"spec", null},
		{"t", null}, {"name", null},
	}
	var walk func(v any)
	walk = func(v any) {
		switch t := v.(type) {
		case []any:
			for _, x := range t {
				walk(x)
			}
		case map[string]any:
			isConst := t["op"] == "const"
			for _, d := range defaults {
				if val, ok := t[d.key]; ok && d.isEmpty(val) {
					if isConst && d.key == "t" {
						continue
					}
					delete(t, d.key)
				}
			}
			for _, x := range t {
				walk(x)
			}
		}
	}
	walk(v)
}

func dspbFixtures(t *testing.T) []string {
	t.Helper()
	if _, err := exec.LookPath("uv"); err != nil {
		t.Skip("uv not available")
	}
	root := filepath.Join("..", "..", "..")
	entries := []string{
		"tests/corpus/smoke.py",
		"tests/corpus/16_generators.py",
		"tests/corpus/29_imports/main.py",
		"tests/corpus/47_packages/main.py",
		"tests/corpus/54_async/main.py",      // coro flag
		"tests/corpus/52_class_machinery.py", // metaclass/kwargs cflags
	}
	var out []string
	for _, e := range entries {
		dst := filepath.Join(t.TempDir(), filepath.Base(e)+".json")
		cmd := exec.Command("uv", "run", "diaspore", "build", e, "-o", dst)
		cmd.Dir = root
		if b, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("uv run diaspore build %s: %v\n%s", e, err, b)
		}
		out = append(out, dst)
	}
	return out
}

// TestDSPBRoundTrip asserts decode(encode(x)) == compact(x) for real corpus
// bundles, and that the decoded model digests identically to the JSON form.
func TestDSPBRoundTrip(t *testing.T) {
	for _, fixture := range dspbFixtures(t) {
		data, err := os.ReadFile(fixture)
		if err != nil {
			t.Fatal(err)
		}
		var bundle map[string]any
		if err := json.Unmarshal(data, &bundle); err != nil {
			t.Fatal(err)
		}
		enc, err := EncodeDSPB(bundle, nil)
		if err != nil {
			t.Fatalf("%s: encode: %v", fixture, err)
		}
		dec, err := DecodeDSPB(enc)
		if err != nil {
			t.Fatalf("%s: decode: %v", fixture, err)
		}
		var want map[string]any
		if err := json.Unmarshal(data, &want); err != nil {
			t.Fatal(err)
		}
		dspbCompact(want)
		if !reflect.DeepEqual(dec, want) {
			t.Fatalf("%s: decode(encode(x)) != compact(x)", fixture)
		}
		if BundleDigest(dec) != BundleDigest(want) {
			t.Fatalf("%s: digest mismatch across containers", fixture)
		}
		// LoadBundle sniffing: raw DSPB and JSON both land on the same model.
		viaLoad, err := LoadBundle(enc)
		if err != nil {
			t.Fatalf("%s: LoadBundle(DSPB): %v", fixture, err)
		}
		if !reflect.DeepEqual(viaLoad, want) {
			t.Fatalf("%s: LoadBundle(DSPB) diverges", fixture)
		}
	}
}

// TestDSPBCrossImplementation encodes with Go and decodes with the Python
// reference (and vice versa), asserting decoded-model equality.
func TestDSPBCrossImplementation(t *testing.T) {
	fixtures := dspbFixtures(t)
	root := filepath.Join("..", "..", "..")
	for _, fixture := range fixtures {
		data, err := os.ReadFile(fixture)
		if err != nil {
			t.Fatal(err)
		}
		var bundle map[string]any
		if err := json.Unmarshal(data, &bundle); err != nil {
			t.Fatal(err)
		}
		// Go encode -> Python decode == compact model.
		enc, err := EncodeDSPB(bundle, nil)
		if err != nil {
			t.Fatal(err)
		}
		lbPath := filepath.Join(t.TempDir(), "go.dspb")
		if err := os.WriteFile(lbPath, enc, 0o644); err != nil {
			t.Fatal(err)
		}
		pyLB := filepath.Join(t.TempDir(), "py.dspb")
		cmd := exec.Command("uv", "run", "python", "-c", `
import json, sys
from diaspore.dspb import decode, encode
from diaspore.compiler import compact_bundle
go_model = decode(open(sys.argv[1], "rb").read())
native = compact_bundle(json.load(open(sys.argv[2])))
assert go_model == native, "py-decode(go-encode) != compact(native)"
open(sys.argv[3], "wb").write(encode(native))
`, lbPath, fixture, pyLB)
		cmd.Dir = root
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("%s: python side: %v\n%s", fixture, err, out)
		}
		// Python encode -> Go decode: must equal the compact model.
		pyBytes, err := os.ReadFile(pyLB)
		if err != nil {
			t.Fatal(err)
		}
		goDecoded, err := DecodeDSPB(pyBytes)
		if err != nil {
			t.Fatalf("%s: go-decode(py-encode): %v", fixture, err)
		}
		var want map[string]any
		if err := json.Unmarshal(data, &want); err != nil {
			t.Fatal(err)
		}
		dspbCompact(want)
		if !reflect.DeepEqual(goDecoded, want) {
			t.Fatalf("%s: go-decode(py-encode) diverges from compact model", fixture)
		}
	}
}
