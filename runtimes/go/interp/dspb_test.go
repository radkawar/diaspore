package interp

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
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
		cmd := exec.Command("uv", "run", "diaspore", "build", "--self-contained", e, "-o", dst)
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

func dspbLinkedTestBundle(id string) map[string]any {
	bundle := map[string]any{
		"diaspore": float64(1), "main": "m",
		"modules": map[string]any{"m": map[string]any{
			"name": "m", "file": "m", "body": []any{},
		}},
	}
	if id != "" {
		bundle["stdlib"] = id
	}
	return bundle
}

func dspbLinkedTestBytes(id string) []byte {
	data := append([]byte("DSPB02"), 2, 1, 'm', byte(len(id)))
	data = append(data, id...)
	return append(data, 1, 0, 1, 1, 0, 0, 0, 0)
}

func TestDSPBLinkedWireFormat(t *testing.T) {
	id := "sha256:" + strings.Repeat("0123456789abcdef", 4)
	for _, test := range []struct {
		name, id string
		wire     []byte
	}{
		{"standalone", "", append([]byte("DSPB01"), 1, 1, 'm', 1, 0, 1, 0, 0, 0, 0)},
		{"linked", id, dspbLinkedTestBytes(id)},
	} {
		t.Run(test.name, func(t *testing.T) {
			bundle := dspbLinkedTestBundle(test.id)
			encoded, err := EncodeDSPB(bundle, nil)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(encoded, test.wire) {
				t.Fatalf("wire = %x; want %x", encoded, test.wire)
			}
			if !LooksLikeDSPB(encoded) {
				t.Fatal("supported version was not detected")
			}
			decoded, err := DecodeDSPB(test.wire)
			if err != nil || !reflect.DeepEqual(decoded, bundle) {
				t.Fatalf("decoded = %#v, %v; want %#v", decoded, err, bundle)
			}
			if BundleDigest(decoded) != BundleDigest(bundle) {
				t.Fatal("binary roundtrip changed canonical digest")
			}
			var compressed bytes.Buffer
			writer := gzip.NewWriter(&compressed)
			if _, err := writer.Write(test.wire); err != nil {
				t.Fatal(err)
			}
			if err := writer.Close(); err != nil {
				t.Fatal(err)
			}
			jsonBytes, err := json.Marshal(bundle)
			if err != nil {
				t.Fatal(err)
			}
			for _, data := range [][]byte{test.wire, compressed.Bytes(), jsonBytes} {
				got, err := LoadBundle(data)
				if err != nil || !reflect.DeepEqual(got, bundle) {
					t.Fatalf("container load = %#v, %v", got, err)
				}
			}
		})
	}
	if BundleDigest(dspbLinkedTestBundle(id)) == BundleDigest(dspbLinkedTestBundle("")) {
		t.Fatal("stdlib reference did not participate in digest")
	}
	if LooksLikeDSPB([]byte("DSPB03")) {
		t.Fatal("unsupported version was detected as supported")
	}
}

func TestDSPBRejectsInvalidStdlibReference(t *testing.T) {
	for _, value := range []any{
		nil, false, float64(1), "", "sha256:",
		"sha256:" + strings.Repeat("0", 63),
		"sha256:" + strings.Repeat("0", 65),
		"sha256:" + strings.Repeat("A", 64),
		"SHA256:" + strings.Repeat("a", 64),
		"sha256:" + strings.Repeat("g", 64),
		"sha256:" + strings.Repeat("0", 63) + "\n",
	} {
		bundle := dspbLinkedTestBundle("")
		bundle["stdlib"] = value
		if _, err := EncodeDSPB(bundle, nil); err == nil {
			t.Fatalf("encoder accepted invalid stdlib %#v", value)
		}
		data, err := json.Marshal(bundle)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := LoadBundle(data); err == nil {
			t.Fatalf("JSON loader accepted invalid stdlib %#v", value)
		}
		if id, ok := value.(string); ok {
			if _, err := DecodeDSPB(dspbLinkedTestBytes(id)); err == nil {
				t.Fatalf("binary decoder accepted invalid stdlib %q", id)
			}
		}
	}
}

func TestDSPBLinkedCrossImplementation(t *testing.T) {
	if _, err := exec.LookPath("uv"); err != nil {
		t.Skip("uv not available")
	}
	id := "sha256:" + strings.Repeat("a", 64)
	bundle := dspbLinkedTestBundle(id)
	data, err := EncodeDSPB(bundle, nil)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "linked.dspb")
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("uv", "run", "python", "-c", `
import sys
from diaspore.dspb import decode, encode
raw = open(sys.argv[1], "rb").read()
model = decode(raw)
assert model["stdlib"] == sys.argv[2]
assert encode(model) == raw
`, path, id)
	cmd.Dir = filepath.Join("..", "..", "..")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("linked cross-implementation: %v\n%s", err, out)
	}
}

func TestDSPBClassBindingFlags(t *testing.T) {
	for _, keyword := range []bool{false, true} {
		class := map[string]any{"op": "class", "name": "__Inner", "binding": "_Outer__Inner", "body": []any{}}
		if keyword {
			class["kwargs"] = []any{map[string]any{"n": "metaclass", "v": map[string]any{"op": "name", "id": "type"}}}
		}
		bundle := dspbLinkedTestBundle("")
		bundle["modules"].(map[string]any)["m"].(map[string]any)["body"] = []any{class}
		encoded, err := EncodeDSPB(bundle, nil)
		if err != nil {
			t.Fatal(err)
		}
		decoded, err := DecodeDSPB(encoded)
		if err != nil || !reflect.DeepEqual(decoded, bundle) {
			t.Fatalf("class binding roundtrip = %#v, %v", decoded, err)
		}
	}
	opcode, _ := indexOf(dspbOps, "class")
	reader := &dspbReader{data: []byte{byte(opcode), 0, 0, 4}, strings: []string{"C"}}
	reader.decNode()
	if reader.err == nil || !strings.Contains(reader.err.Error(), "class flags") {
		t.Fatalf("unknown class flags accepted: %v", reader.err)
	}
}
