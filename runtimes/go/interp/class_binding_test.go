package interp

import (
	"strings"
	"testing"
)

func classBindingBundle(body []any) map[string]any {
	return map[string]any{
		"diaspore": float64(1), "main": "test",
		"modules": map[string]any{"test": map[string]any{"body": body}},
	}
}

func TestClassBindingPreservesClassName(t *testing.T) {
	inner := node{"op": "class", "name": "__Inner", "binding": "_Outer__Inner", "body": []any{}}
	outer := node{"op": "class", "name": "Outer", "body": []any{inner}}
	vm := New(classBindingBundle([]any{outer}))
	defer vm.Close()
	module, err := vm.importModule("test")
	if err != nil {
		t.Fatal(err)
	}
	outerValue, err := vm.getAttr(module, "Outer")
	if err != nil {
		t.Fatal(err)
	}
	innerValue, err := vm.getAttr(outerValue, "_Outer__Inner")
	if err != nil {
		t.Fatal(err)
	}
	innerClass, ok := innerValue.(*Class)
	if !ok || innerClass.Name != "__Inner" {
		t.Fatalf("class construction used lexical binding: %#v", innerValue)
	}
	name, err := vm.getAttr(innerClass, "__name__")
	if err != nil || name != "__Inner" {
		t.Fatalf("__name__ = %v, %v", name, err)
	}
	qualname, err := vm.getAttr(innerClass, "__qualname__")
	if err != nil || qualname != "Outer.__Inner" {
		t.Fatalf("__qualname__ = %v, %v", qualname, err)
	}
	if _, found := outerValue.(*Class).NS["__Inner"]; found {
		t.Fatal("private class was also stored under its unmangled name")
	}
}

func TestClassBindingAllocatesLexicalSlot(t *testing.T) {
	sc := &scope{layout: map[string]int{}}
	collectSoftSlots([]any{node{
		"op": "class", "name": "__Inner", "binding": "_Outer__Inner", "body": []any{},
	}}, sc)
	if _, found := sc.layout["_Outer__Inner"]; !found || sc.nSlots != 1 {
		t.Fatalf("lexical layout = %v, slots = %d", sc.layout, sc.nSlots)
	}
	if _, found := sc.layout["__Inner"]; found {
		t.Fatal("class metadata name allocated a lexical slot")
	}
}

func TestClassBindingRejectsNonString(t *testing.T) {
	for _, binding := range []any{nil, float64(1), true, []any{}} {
		vm := New(classBindingBundle([]any{node{
			"op": "class", "name": "C", "binding": binding, "body": []any{},
		}}))
		if vm.badBundle == nil || !strings.Contains(vm.badBundle.Error(), "class binding must be a string") {
			t.Errorf("binding %#v: error = %v", binding, vm.badBundle)
		}
		vm.Close()
	}
}
