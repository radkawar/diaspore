package interp

import (
	"math/big"
	"testing"
)

func TestNativeOperatorIndex(t *testing.T) {
	vm := acceleratorTestVM()
	module := vm.natives["_operator"]
	fn := module.NS["index"]
	large := new(big.Int).Lsh(big.NewInt(1), 1000)
	for _, pair := range [][2]Value{{int64(4), int64(4)}, {false, int64(0)}, {true, int64(1)}, {large, large}} {
		if got := acceleratorCall(t, vm, fn, pair[0]); got != pair[1] {
			t.Fatalf("index(%v) = %v; want %v", pair[0], got, pair[1])
		}
	}
	class := &Class{Name: "Index", NS: map[string]Value{}}
	class.MRO = []*Class{class, objectClass}
	value := newInstance(class, nil)
	calls := 0
	class.NS["__index__"] = bi("__index__", func(_ *VM, args []Value, _ []KwArg) (Value, error) {
		if len(args) != 1 || args[0] != value {
			t.Fatal("index protocol did not bind its receiver")
		}
		calls++
		return large, nil
	})
	class.NS["__int__"] = bi("__int__", func(_ *VM, _ []Value, _ []KwArg) (Value, error) {
		t.Fatal("index called __int__")
		return nil, nil
	})
	if got := acceleratorCall(t, vm, fn, value); got != large || calls != 1 {
		t.Fatalf("index provider = %v (%d calls)", got, calls)
	}
	value.Prim = int64(7)
	if got := acceleratorCall(t, vm, fn, value); got != int64(7) || calls != 1 {
		t.Fatalf("index int subclass = %v (%d protocol calls)", got, calls)
	}
	value.Prim = nil
	class.NS["__index__"] = bi("__index__", func(_ *VM, _ []Value, _ []KwArg) (Value, error) {
		return "7", nil
	})
	_, err := vm.call(fn, []Value{value}, nil)
	acceleratorError(t, err, "TypeError", "__index__ returned non-int (type str)")
	_, err = vm.call(fn, []Value{7.0}, nil)
	acceleratorError(t, err, "TypeError", "'float' object cannot be interpreted as an integer")
	if module.NS["__doc__"] != None || module.NS["__name__"] != "_operator" {
		t.Fatal("native operator module metadata missing")
	}
}

func TestStopIterationValue(t *testing.T) {
	vm := acceleratorTestVM()
	for _, args := range [][]Value{nil, {int64(3)}, {int64(4), "ignored"}} {
		want := Value(None)
		if len(args) != 0 {
			want = args[0]
		}
		for _, value := range []Value{makeExc("StopIteration", args...), acceleratorCall(t, vm, excClasses["StopIteration"], args...)} {
			got, err := vm.getAttr(value, "value")
			if err != nil || got != want {
				t.Fatalf("StopIteration%v.value = %v, %v; want %v", args, got, err, want)
			}
		}
	}
}

func TestNativeOperatorAdd(t *testing.T) {
	vm := acceleratorTestVM()
	defer vm.Close()
	add := vm.natives["_operator"].NS["add"]
	if got := acceleratorCall(t, vm, add, int64(3), int64(4)); got != int64(7) {
		t.Fatalf("integer addition = %v", got)
	}
	if got := acceleratorCall(t, vm, add, "ab", "cd"); got != "abcd" {
		t.Fatalf("string addition = %v", got)
	}
	class := &Class{Name: "ReflectedAdd", NS: map[string]Value{}}
	class.MRO = []*Class{class, objectClass}
	value := newInstance(class, nil)
	class.NS["__radd__"] = bi("__radd__", func(_ *VM, args []Value, _ []KwArg) (Value, error) {
		if len(args) != 2 || args[0] != value || args[1] != int64(7) {
			t.Fatalf("reflected addition arguments = %v", args)
		}
		return "reflected", nil
	})
	if got := acceleratorCall(t, vm, add, int64(7), value); got != "reflected" {
		t.Fatalf("reflected addition = %v", got)
	}
}
