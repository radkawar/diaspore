package interp

import (
	"bytes"
	"strings"
	"testing"
)

func linkedTestBundle(t *testing.T) map[string]any {
	t.Helper()
	id, err := StdlibID()
	if err != nil {
		t.Fatal(err)
	}
	return map[string]any{
		"diaspore": float64(1), "main": "test", "stdlib": id,
		"modules": map[string]any{"test": map[string]any{"body": []any{}}},
	}
}

func TestLinkedStdlibLazyAndIsolated(t *testing.T) {
	bundle := linkedTestBundle(t)
	first, second := New(bundle), New(bundle)
	defer first.Close()
	defer second.Close()
	if first.badBundle != nil || second.badBundle != nil {
		t.Fatalf("linked load: %v, %v", first.badBundle, second.badBundle)
	}
	if first.stdlib != second.stdlib || len(first.modcode) != 1 || len(second.modcode) != 1 {
		t.Fatal("store IR was not shared lazily")
	}
	if _, found := first.moduleDict.GetStr("keyword"); found {
		t.Fatal("linked module executed before import")
	}
	module, err := first.importModule("keyword")
	if err != nil {
		t.Fatal(err)
	}
	again, err := first.importModule("keyword")
	if err != nil || again != module {
		t.Fatalf("module was not cached: %v", err)
	}
	if _, found := second.moduleDict.GetStr("keyword"); found || second.modcode["keyword"] != nil {
		t.Fatal("module state leaked to a second VM")
	}
	words, err := first.getAttr(module, "kwlist")
	if err != nil {
		t.Fatal(err)
	}
	list := words.(*List)
	original := len(list.Items)
	list.Items = append(list.Items, "private_keyword")
	other, err := second.importModule("keyword")
	if err != nil {
		t.Fatal(err)
	}
	otherWords, err := second.getAttr(other, "kwlist")
	if err != nil || len(otherWords.(*List).Items) != original {
		t.Fatalf("module globals are shared: %v", err)
	}
	file, err := first.getAttr(module, "__file__")
	if err != nil || file != "<stdlib>/keyword.py" {
		t.Fatalf("stdlib filename = %v, %v", file, err)
	}
}

func TestLinkedStdlibPrecedenceAndStandalone(t *testing.T) {
	bundle := linkedTestBundle(t)
	bundle["modules"].(map[string]any)["keyword"] = map[string]any{"body": []any{}, "file": "/app/keyword.py"}
	vm := New(bundle)
	defer vm.Close()
	module, err := vm.importModule("keyword")
	if err != nil {
		t.Fatal(err)
	}
	file, err := vm.getAttr(module, "__file__")
	if err != nil || file != "/app/keyword.py" {
		t.Fatalf("program did not shadow stdlib: %v, %v", file, err)
	}
	delete(bundle, "stdlib")
	delete(bundle["modules"].(map[string]any), "keyword")
	standalone := New(bundle)
	defer standalone.Close()
	if standalone.stdlib != nil || standalone.stdlibID != "" {
		t.Fatal("standalone VM acquired a linked store")
	}
	if _, err := standalone.importModule("keyword"); err == nil {
		t.Fatal("standalone imported an absent shipped module")
	} else if _, expected := raisedAs(err, "ModuleNotFoundError"); !expected {
		t.Fatal(err)
	}
	if _, err := standalone.importModule("_operator"); err != nil {
		t.Fatalf("standalone native import: %v", err)
	}
}

func TestLinkedStdlibIdentityValidation(t *testing.T) {
	bundle := linkedTestBundle(t)
	installed := bundle["stdlib"].(string)
	wrong := "sha256:" + strings.Repeat("0", 64)
	bundle["stdlib"] = wrong
	var stderr bytes.Buffer
	vm := New(bundle, WithStderr(&stderr))
	defer vm.Close()
	if code := vm.Run(); code != 2 || !strings.Contains(stderr.String(), wrong) || !strings.Contains(stderr.String(), installed) {
		t.Fatalf("mismatch outcome %d: %s", code, stderr.String())
	}
	for _, invalid := range []any{nil, float64(1), "", "sha256:" + strings.Repeat("A", 64)} {
		bundle["stdlib"] = invalid
		invalidVM := New(bundle)
		if invalidVM.badBundle == nil {
			t.Fatalf("invalid stdlib reference accepted: %#v", invalid)
		}
		invalidVM.Close()
	}
}

func TestStdlibBridgeCopiesIR(t *testing.T) {
	vm := acceleratorTestVM()
	defer vm.Close()
	bridge := vm.natives["_diaspore_stdlib"]
	id := acceleratorCall(t, vm, bridge.NS["get_id"])
	installed, err := StdlibID()
	if err != nil || id != installed {
		t.Fatalf("bridge id = %v, %v", id, err)
	}
	names := acceleratorCall(t, vm, bridge.NS["module_names"]).(*List)
	if len(names.Items) == 0 {
		t.Fatal("empty installed module inventory")
	}
	names.Items[0] = "private_module"
	again := acceleratorCall(t, vm, bridge.NS["module_names"]).(*List)
	if again.Items[0] == "private_module" {
		t.Fatal("module inventory aliases shared state")
	}
	first := acceleratorCall(t, vm, bridge.NS["get_module"], "keyword").(*Dict)
	body, _ := first.GetStr("body")
	body.(*List).Items = nil
	second := acceleratorCall(t, vm, bridge.NS["get_module"], "keyword").(*Dict)
	otherBody, _ := second.GetStr("body")
	if len(otherBody.(*List).Items) == 0 {
		t.Fatal("module IR aliases shared state")
	}
	if got := acceleratorCall(t, vm, bridge.NS["get_module"], "absent_module"); got != None {
		t.Fatalf("missing module = %v", got)
	}
	if vm.stdlib != nil {
		t.Fatal("bridge access silently linked standalone imports")
	}
	if _, err := vm.importModule("keyword"); err == nil {
		t.Fatal("bridge access granted standalone imports")
	}
}

func TestAddLinkedModules(t *testing.T) {
	vm := acceleratorTestVM()
	defer vm.Close()
	bundle := linkedTestBundle(t)
	if err := vm.AddModules(bundle); err != nil {
		t.Fatal(err)
	}
	if vm.stdlibID != bundle["stdlib"] || vm.stdlib == nil {
		t.Fatal("explicit linked addition did not attach installed store")
	}
	if _, err := vm.importModule("keyword"); err != nil {
		t.Fatal(err)
	}
	bundle["stdlib"] = "sha256:" + strings.Repeat("f", 64)
	if err := vm.AddModules(bundle); err == nil {
		t.Fatal("addition accepted a mismatched store")
	}
}

func TestStdlibIndexDefersIRDecoding(t *testing.T) {
	store, err := loadStdlib()
	if err != nil {
		t.Fatal(err)
	}
	for name, module := range store.modules {
		if len(module.raw) == 0 || module.ir != nil {
			t.Fatalf("module %q was not retained as serialized IR", name)
		}
	}
	keyword := store.modules["keyword"]
	first, err := keyword.load()
	if err != nil {
		t.Fatal(err)
	}
	second, err := keyword.load()
	if err != nil || first["file"] != second["file"] || keyword.ir == nil {
		t.Fatalf("module decoding cache failed: %v", err)
	}
	for name, module := range store.modules {
		if name != "keyword" && module.ir != nil {
			t.Fatalf("import decoded unrelated module %q", name)
		}
	}
}

func BenchmarkStdlibColdIndex(b *testing.B) {
	b.ReportAllocs()
	for range b.N {
		store, err := loadStdlib()
		if err != nil || len(store.names) == 0 {
			b.Fatalf("load stdlib: %v", err)
		}
	}
}
