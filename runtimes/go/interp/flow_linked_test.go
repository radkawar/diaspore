package interp

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func linkedFlowTestBundle(id string) map[string]any {
	bundle := dspbLinkedTestBundle(id)
	module := bundle["modules"].(map[string]any)["m"].(map[string]any)
	module["body"] = []any{
		map[string]any{"op": "importfrom", "module": "flow", "names": []any{
			map[string]any{"n": "suspend", "alias": "suspend"},
		}},
		map[string]any{"op": "expr", "v": map[string]any{
			"op": "call", "f": map[string]any{"op": "name", "id": "suspend"},
			"args": []any{map[string]any{"op": "const", "t": "str", "v": "waiting"}},
		}},
	}
	return bundle
}

func linkedFlowTestVM(t *testing.T, id string) *VM {
	t.Helper()
	var output bytes.Buffer
	vm := New(linkedFlowTestBundle(id), WithDurable(), WithStdout(&output), WithStderr(&output))
	if vm.badBundle != nil {
		t.Fatal(vm.badBundle)
	}
	t.Cleanup(func() {
		if output.Len() != 0 {
			t.Log(output.String())
		}
	})
	return vm
}

func TestFlowSnapshotStdlibRoundTrip(t *testing.T) {
	id, err := StdlibID()
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct{ name, id string }{{"standalone", ""}, {"linked", id}} {
		t.Run(test.name, func(t *testing.T) {
			vm := linkedFlowTestVM(t, test.id)
			outcome := vm.RunDurable()
			if !outcome.Suspended || outcome.Payload != "waiting" {
				t.Fatalf("initial outcome = %#v", outcome)
			}
			var snapshot map[string]any
			if err := json.Unmarshal(outcome.Snapshot, &snapshot); err != nil {
				t.Fatal(err)
			}
			value, present := snapshot["stdlib"]
			if test.id == "" && present || test.id != "" && (!present || value != test.id) {
				t.Fatalf("snapshot stdlib = %#v, present %v; want %q", value, present, test.id)
			}
			if snapshot["bundle_sha256"] != BundleDigest(vm.bundle) {
				t.Fatal("snapshot bundle digest omitted linked metadata")
			}
			resumed := linkedFlowTestVM(t, test.id)
			result, err := resumed.Resume(outcome.Snapshot, "received")
			if err != nil || result == nil || result.Suspended || result.Code != 0 {
				t.Fatalf("resume = %#v, %v", result, err)
			}
		})
	}
}

func TestFlowResumeRejectsStdlibMismatch(t *testing.T) {
	id, err := StdlibID()
	if err != nil {
		t.Fatal(err)
	}
	vm := linkedFlowTestVM(t, id)
	outcome := vm.RunDurable()
	if !outcome.Suspended {
		t.Fatalf("initial outcome = %#v", outcome)
	}
	otherID := "sha256:" + strings.Repeat("0", 64)
	if otherID == id {
		otherID = "sha256:" + strings.Repeat("1", 64)
	}
	for _, test := range []struct {
		name string
		id   Value
		omit bool
	}{
		{"missing", nil, true},
		{"different", otherID, false},
		{"null", nil, false},
		{"number", float64(1), false},
		{"empty", "", false},
		{"uppercase", "sha256:" + strings.Repeat("A", 64), false},
	} {
		t.Run(test.name, func(t *testing.T) {
			var snapshot map[string]any
			if err := json.Unmarshal(outcome.Snapshot, &snapshot); err != nil {
				t.Fatal(err)
			}
			if test.omit {
				delete(snapshot, "stdlib")
			} else {
				snapshot["stdlib"] = test.id
			}
			data, err := json.Marshal(snapshot)
			if err != nil {
				t.Fatal(err)
			}
			resumed := linkedFlowTestVM(t, id)
			_, err = resumed.Resume(data, None)
			if err == nil || !strings.Contains(err.Error(), "stdlib") {
				t.Fatalf("resume accepted invalid snapshot stdlib: %v", err)
			}
			if test.name == "different" && (!strings.Contains(err.Error(), id) || !strings.Contains(err.Error(), otherID)) {
				t.Fatalf("mismatch error omitted identifiers: %v", err)
			}
		})
	}
	standalone := linkedFlowTestVM(t, "")
	if _, err := standalone.Resume(outcome.Snapshot, None); err == nil || !strings.Contains(err.Error(), "stdlib") {
		t.Fatalf("standalone VM accepted linked snapshot: %v", err)
	}
	standaloneOutcome := linkedFlowTestVM(t, "").RunDurable()
	if !standaloneOutcome.Suspended {
		t.Fatalf("standalone outcome = %#v", standaloneOutcome)
	}
	if _, err := linkedFlowTestVM(t, id).Resume(standaloneOutcome.Snapshot, None); err == nil || !strings.Contains(err.Error(), "stdlib") {
		t.Fatalf("linked VM accepted standalone snapshot: %v", err)
	}
}
