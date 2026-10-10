package interp

import (
	"errors"
	"fmt"
	"strings"
	"testing"
)

type raisedAsTestAdapter struct{ raised *Raised }

func (adapter raisedAsTestAdapter) Error() string { return "adapted exception" }
func (adapter raisedAsTestAdapter) As(target any) bool {
	if raised, ok := target.(**Raised); ok {
		*raised = adapter.raised
		return true
	}
	return false
}

func TestRaisedAsPreservesWrappedErrorProtocol(t *testing.T) {
	raised := newError("AttributeError", "missing")
	other := newError("TypeError", "other")
	for _, test := range []struct {
		name string
		err  error
		want *Raised
	}{
		{"nil", nil, nil},
		{"direct", raised, raised},
		{"different_class", other, nil},
		{"wrapped", fmt.Errorf("outer: %w", raised), raised},
		{"joined", errors.Join(errors.New("unrelated"), raised), raised},
		{"first_raised_wins", errors.Join(other, raised), nil},
		{"custom_as", raisedAsTestAdapter{raised}, raised},
		{"ordinary_error", errors.New("plain"), nil},
	} {
		t.Run(test.name, func(t *testing.T) {
			got, ok := raisedAs(test.err, "AttributeError")
			if got != test.want || ok != (test.want != nil) {
				t.Fatalf("raisedAs = %v, %v; want %v", got, ok, test.want)
			}
		})
	}
	if got, ok := raisedAs(raised, "Exception"); !ok || got != raised {
		t.Fatal("exception subclass matching was lost")
	}
}

func TestRaisedAsDirectDoesNotAllocate(t *testing.T) {
	raised := newError("AttributeError", "missing")
	for _, err := range []error{nil, raised} {
		allocations := testing.AllocsPerRun(100, func() {
			got, matched := raisedAs(err, "AttributeError")
			if (err == nil && (matched || got != nil)) || (err != nil && (!matched || got != raised)) {
				panic("incorrect direct exception discrimination")
			}
		})
		if allocations != 0 {
			t.Fatalf("direct error check allocates %g objects", allocations)
		}
	}
}

func TestInstanceStringAccessDoesNotAllocate(t *testing.T) {
	vm := acceleratorTestVM()
	defer vm.Close()
	class := propertyTestClass(t, vm, "Record", nil, nil)
	name := strings.Clone("field")
	var expected Value = int64(42)
	for _, dictionary := range []Value{
		NewDict(),
		&metadataNamespace{values: map[string]Value{}},
		&metadataNamespace{cells: map[string]*cell{}},
	} {
		instance := newInstance(class, nil)
		instance.dictionary = dictionary
		if err := vm.instanceStore(instance, name, expected); err != nil {
			t.Fatal(err)
		}
		allocations := testing.AllocsPerRun(100, func() {
			got, found, err := vm.instanceLookup(instance, name)
			if err != nil || !found || got != expected {
				panic("incorrect instance string lookup")
			}
			if err := vm.instanceStore(instance, name, expected); err != nil {
				panic("incorrect instance string update")
			}
			if _, found, err := vm.instanceLookup(instance, "absent"); found || err != nil {
				panic("incorrect absent instance attribute")
			}
		})
		if allocations != 0 {
			t.Fatalf("%T string access allocates %g objects", dictionary, allocations)
		}
	}
}

func TestInstanceStringAccessPreservesArbitraryKeys(t *testing.T) {
	vm := acceleratorTestVM()
	defer vm.Close()
	name := "field"
	hash, err := hashValue(name)
	if err != nil {
		t.Fatal(err)
	}
	comparisons := 0
	fail := false
	keyClass := propertyTestClass(t, vm, "EqualKey", nil, map[string]Value{
		"__hash__": bi("__hash__", func(_ *VM, _ []Value, _ []KwArg) (Value, error) {
			return hash, nil
		}),
		"__eq__": bi("__eq__", func(_ *VM, args []Value, _ []KwArg) (Value, error) {
			comparisons++
			if fail {
				return nil, newError("ValueError", "key equality failed")
			}
			return args[1] == name, nil
		}),
	})
	key := newInstance(keyClass, nil)
	dictionary := NewDict()
	if err := dictionary.Set(key, int64(7)); err != nil {
		t.Fatal(err)
	}
	instance := newInstance(propertyTestClass(t, vm, "Record", nil, nil), nil)
	instance.dictionary = dictionary
	if value, found, err := vm.instanceLookup(instance, name); err != nil || !found || value != int64(7) || comparisons == 0 {
		t.Fatalf("equivalent nonstring key lookup: %v, %v, %v", value, found, err)
	}
	if err := vm.instanceStore(instance, name, int64(8)); err != nil || dictionary.Len() != 1 || dictionary.entries[0].key != key {
		t.Fatalf("attribute update replaced the original dictionary key: %v", err)
	}
	fail = true
	if _, _, err := vm.instanceLookup(instance, name); err == nil {
		t.Fatal("attribute lookup suppressed key comparison failure")
	}
	if err := vm.instanceStore(instance, name, int64(9)); err == nil {
		t.Fatal("attribute update suppressed key comparison failure")
	}
	if dictionary.entries[0].val != int64(8) {
		t.Fatal("failed update mutated the dictionary")
	}
	if err := vm.instanceStore(newInstance(instance.Class, nil), "new", int64(1)); err != nil {
		t.Fatal(err)
	}
}

func TestBoundFastCallPreservesFuelAndArgumentEvaluation(t *testing.T) {
	for _, limit := range []int64{1, 2} {
		vm := acceleratorTestVM()
		defer vm.Close()
		cf := &compiledFunc{
			name: "method", fastCall: true, poolable: true, nSlots: 2,
			layout: map[string]int{"self": 0, "value": 1},
			params: &paramSpec{args: []string{"self", "value"}, argIdx: []int{0, 1}},
			body:   execNop,
		}
		called := 0
		compiler := &compiler{vm: vm, sc: &scope{}, cells: map[string]*cell{
			"method": {v: &BoundMethod{Fn: &Function{cf: cf}, Recv: int64(7)}},
			"argument": {v: bi("argument", func(_ *VM, _ []Value, _ []KwArg) (Value, error) {
				called++
				return int64(9), nil
			})},
		}}
		call := compiler.compileCall(node{"op": "call", "f": node{"op": "name", "id": "method"}, "args": []any{
			node{"op": "call", "f": node{"op": "name", "id": "argument"}},
		}})
		if compiler.err != nil {
			t.Fatal(compiler.err)
		}
		vm.steps, vm.maxSteps = 0, limit
		_, err := call(&frame{})
		if _, trap := err.(*Trap); !trap || called != 1 || vm.steps != limit+1 {
			t.Fatalf("fuel limit %d: argument calls=%d steps=%d error=%v", limit, called, vm.steps, err)
		}
	}
}
