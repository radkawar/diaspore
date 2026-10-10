package interp

import "testing"

func TestNativeSetPop(t *testing.T) {
	vm := acceleratorTestVM()
	set := NewSet()
	for _, value := range []Value{int64(1), int64(2), "three"} {
		if err := set.Add(value); err != nil {
			t.Fatal(err)
		}
	}
	original := set.Copy()
	method, err := vm.getAttr(set, "pop")
	if err != nil {
		t.Fatal(err)
	}
	unbound, err := vm.getAttr(vm.types["set"], "pop")
	if err != nil {
		t.Fatal(err)
	}
	seen := NewSet()
	for remaining := 3; remaining > 0; remaining-- {
		var value Value
		if remaining == 2 {
			value, err = vm.call(unbound, []Value{set}, nil)
		} else {
			value, err = vm.call(method, nil, nil)
		}
		if err != nil {
			t.Fatal(err)
		}
		contained, lookupErr := original.Has(value)
		duplicate, duplicateErr := seen.Has(value)
		if lookupErr != nil || duplicateErr != nil || !contained || duplicate || set.Len() != remaining-1 {
			t.Fatalf("invalid popped member %v: contained=%v duplicate=%v remaining=%d", value, contained, duplicate, set.Len())
		}
		if err := seen.Add(value); err != nil {
			t.Fatal(err)
		}
		if present, err := set.Has(value); present || err != nil {
			t.Fatalf("popped member retained: %v %v", present, err)
		}
	}
	_, err = vm.call(method, nil, nil)
	raised, ok := raisedAs(err, "KeyError")
	if !ok {
		t.Fatalf("empty pop: %v", err)
	}
	args := excArgs(raised.Exc.(*Instance))
	if len(args) != 1 || args[0] != "pop from an empty set" {
		t.Fatalf("empty pop args: %#v", args)
	}
	if err := set.Add("again"); err != nil {
		t.Fatal(err)
	}
	for _, invocation := range []struct {
		args []Value
		kw   []KwArg
	}{{args: []Value{int64(0)}}, {kw: []KwArg{{Name: "default", Val: None}}}} {
		_, err := vm.call(method, invocation.args, invocation.kw)
		if _, ok := raisedAs(err, "TypeError"); !ok || set.Len() != 1 {
			t.Fatalf("invalid argument changed set or did not fail: %v", err)
		}
	}
}

func TestNativeSetPopUsesStoredHash(t *testing.T) {
	vm := acceleratorTestVM()
	calls := 0
	class := &Class{Name: "CollidingSetKey", NS: map[string]Value{}, Bases: []*Class{objectClass}, vm: vm}
	class.MRO = []*Class{class, objectClass}
	class.NS["__hash__"] = bi("__hash__", func(_ *VM, _ []Value, _ []KwArg) (Value, error) {
		calls++
		return int64(7), nil
	})
	first, second := newInstance(class, nil), newInstance(class, nil)
	set := NewSet()
	for _, value := range []Value{first, second} {
		if err := set.Add(value); err != nil {
			t.Fatal(err)
		}
	}
	before := calls
	method := setMethod(set, "pop")
	popped, err := method(vm, nil, nil)
	if err != nil || calls != before || set.slowKeys != 1 || set.Len() != 1 {
		t.Fatalf("pop rehashed or corrupted collision storage: %v %v hashes=%d slow=%d", popped, err, calls-before, set.slowKeys)
	}
	remaining := first
	if popped == first {
		remaining = second
	} else if popped != second {
		t.Fatalf("unexpected key: %v", popped)
	}
	if found, err := set.Has(remaining); !found || err != nil {
		t.Fatalf("remaining collision lost: %v %v", found, err)
	}
	if err := set.Add(popped); err != nil {
		t.Fatal(err)
	}
	if removed, err := set.Discard(remaining); !removed || err != nil {
		t.Fatalf("discard after pop/reinsert: %v %v", removed, err)
	}
	before = calls
	if value, err := method(vm, nil, nil); value != popped || err != nil || calls != before || set.slowKeys != 0 {
		t.Fatalf("final pop: %v %v hashes=%d slow=%d", value, err, calls-before, set.slowKeys)
	}
}
