package interp

import "testing"

func propertyTestVM() *VM {
	return New(map[string]any{"diaspore": float64(1), "main": "test", "modules": map[string]any{
		"test": map[string]any{"name": "test", "body": []any{}},
	}})
}

func propertyTestCall(t *testing.T, vm *VM, callable Value, args ...Value) Value {
	t.Helper()
	value, err := vm.call(callable, args, nil)
	if err != nil {
		t.Fatal(err)
	}
	return value
}

func propertyTestAttr(t *testing.T, vm *VM, object Value, name string) Value {
	t.Helper()
	value, err := vm.getAttr(object, name)
	if err != nil {
		t.Fatalf("getattr(%s, %q): %v", typeName(object), name, err)
	}
	return value
}

func propertyTestClass(t *testing.T, vm *VM, name string, bases []Value, attrs map[string]Value) *Class {
	t.Helper()
	ns := NewDict()
	for key, value := range attrs {
		ns.SetStr(key, value)
	}
	value, err := ctorType(vm, []Value{name, &Tuple{Items: bases}, ns}, nil)
	if err != nil {
		t.Fatalf("type(%s): %v", name, err)
	}
	class, ok := value.(*Class)
	if !ok {
		t.Fatalf("type(%s) returned %T", name, value)
	}
	return class
}

func propertyTestCallable(t *testing.T, vm *VM, doc Value, call func([]Value) (Value, error)) *Instance {
	t.Helper()
	class := propertyTestClass(t, vm, "Accessor", nil, map[string]Value{
		"__call__": bi("__call__", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			return call(args[1:])
		}),
	})
	instance := newInstance(class, nil)
	instance.dictSet("__doc__", doc)
	return instance
}

func propertyTestError(t *testing.T, err error, kind string) {
	t.Helper()
	if _, ok := raisedAs(err, kind); !ok {
		t.Fatalf("error = %v, want %s", err, kind)
	}
}

func TestPropertySubtypeBindingAndCopies(t *testing.T) {
	vm := propertyTestVM()
	subtype := propertyTestClass(t, vm, "DocumentedProperty", []Value{vm.builtins["property"]}, nil)
	getter := propertyTestCallable(t, vm, "Original documentation.", func(args []Value) (Value, error) {
		return args[0].(*Instance).dictValue("stored"), nil
	})
	descriptor := propertyTestCall(t, vm, subtype, getter)
	owner := propertyTestClass(t, vm, "Owner", nil, map[string]Value{"value": descriptor})
	object := propertyTestCall(t, vm, owner).(*Instance)
	object.dictSet("stored", int64(11))
	object.dictSet("value", int64(99))
	if got := propertyTestAttr(t, vm, object, "value"); got != int64(11) {
		t.Fatalf("data descriptor returned %v", got)
	}
	if got := propertyTestAttr(t, vm, owner, "value"); got != descriptor {
		t.Fatal("class access did not return the property descriptor")
	}
	if got := propertyTestAttr(t, vm, descriptor, "__doc__"); got != "Original documentation." {
		t.Fatalf("inherited property documentation = %v", got)
	}
	replacementGetter := propertyTestCallable(t, vm, "Replacement documentation.", func(args []Value) (Value, error) {
		return int64(13), nil
	})
	replacement := propertyTestCall(t, vm, propertyTestAttr(t, vm, descriptor, "getter"), replacementGetter)
	if got := propertyTestCall(t, vm, vm.builtins["type"], replacement); got != subtype {
		t.Fatal("getter() did not preserve the property subclass")
	}
	if got := propertyTestAttr(t, vm, replacement, "__doc__"); got != "Replacement documentation." {
		t.Fatalf("replacement documentation = %v", got)
	}
	get := propertyTestAttr(t, vm, replacement, "__get__")
	if got := propertyTestCall(t, vm, get, object, owner); got != int64(13) {
		t.Fatalf("explicit descriptor read = %v", got)
	}
	if got := propertyTestCall(t, vm, get, None, owner); got != replacement {
		t.Fatal("__get__(None, owner) did not return self")
	}
	setter := propertyTestCallable(t, vm, None, func(args []Value) (Value, error) {
		args[0].(*Instance).dictSet("stored", args[1])
		return int64(100), nil
	})
	deleter := propertyTestCallable(t, vm, None, func(args []Value) (Value, error) {
		args[0].(*Instance).dictDelete("stored")
		return None, nil
	})
	writable := propertyTestCall(t, vm, propertyTestAttr(t, vm, descriptor, "setter"), setter)
	writable = propertyTestCall(t, vm, propertyTestAttr(t, vm, writable, "deleter"), deleter)
	owner.NS["value"] = writable
	if err := vm.setAttr(object, "value", int64(21)); err != nil {
		t.Fatal(err)
	}
	if object.dictValue("stored") != int64(21) {
		t.Fatal("property setter did not receive instance and value")
	}
	if err := vm.delAttr(object, "value"); err != nil {
		t.Fatal(err)
	}
	if _, exists := object.dictLookup("stored"); exists {
		t.Fatal("property deleter did not run")
	}
	propertyTestError(t, vm.setAttr(descriptor, "fget", replacementGetter), "AttributeError")
}

func TestPropertyArgumentsAndExplicitDocumentation(t *testing.T) {
	vm := propertyTestVM()
	getter := propertyTestCallable(t, vm, "Getter documentation.", func(args []Value) (Value, error) { return int64(7), nil })
	_, err := vm.call(vm.builtins["property"], []Value{getter}, []KwArg{{Name: "fget", Val: getter}})
	propertyTestError(t, err, "TypeError")
	_, err = vm.call(vm.builtins["property"], nil, []KwArg{{Name: "unexpected", Val: None}})
	propertyTestError(t, err, "TypeError")
	descriptor := propertyTestCall(t, vm, vm.builtins["property"], getter, None, None, "Explicit documentation.")
	replacement := propertyTestCall(t, vm, propertyTestAttr(t, vm, descriptor, "getter"), getter)
	if got := propertyTestAttr(t, vm, replacement, "__doc__"); got != "Explicit documentation." {
		t.Fatalf("explicit documentation changed to %v", got)
	}
	if err := vm.setAttr(descriptor, "__doc__", "Edited documentation."); err != nil {
		t.Fatal(err)
	}
	if got := propertyTestAttr(t, vm, descriptor, "__doc__"); got != "Edited documentation." {
		t.Fatal("property documentation is not writable")
	}
	missing := propertyTestCall(t, vm, vm.builtins["property"], None)
	_, err = vm.call(propertyTestAttr(t, vm, missing, "__get__"), []Value{int64(1)}, nil)
	propertyTestError(t, err, "AttributeError")
	noncallable := propertyTestCall(t, vm, vm.builtins["property"], int64(5))
	_, err = vm.call(propertyTestAttr(t, vm, noncallable, "__get__"), []Value{int64(1)}, nil)
	propertyTestError(t, err, "TypeError")
}

func TestPropertySubtypeInitializerAndDescriptorOverrides(t *testing.T) {
	vm := propertyTestVM()
	initializations := 0
	writes, deletes := 0, 0
	subtype := propertyTestClass(t, vm, "CustomProperty", []Value{vm.builtins["property"]}, map[string]Value{
		"__init__": bi("__init__", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			initializations++
			return None, vm.initProperty(args[0], args[1:], kw)
		}),
		"__get__":    bi("__get__", func(vm *VM, args []Value, kw []KwArg) (Value, error) { return "override", nil }),
		"__set__":    bi("__set__", func(vm *VM, args []Value, kw []KwArg) (Value, error) { writes++; return None, nil }),
		"__delete__": bi("__delete__", func(vm *VM, args []Value, kw []KwArg) (Value, error) { deletes++; return None, nil }),
	})
	getter := propertyTestCallable(t, vm, "Accessor documentation.", func(args []Value) (Value, error) { return int64(22), nil })
	descriptor := propertyTestCall(t, vm, subtype, getter)
	propertyTestCall(t, vm, propertyTestAttr(t, vm, descriptor, "getter"), getter)
	if initializations != 2 {
		t.Fatalf("subclass initializer ran %d times, want 2", initializations)
	}
	owner := propertyTestClass(t, vm, "Owner", nil, map[string]Value{"value": descriptor})
	object := propertyTestCall(t, vm, owner)
	if got := propertyTestAttr(t, vm, object, "value"); got != "override" {
		t.Fatalf("subtype __get__ returned %v", got)
	}
	if err := vm.setAttr(object, "value", int64(1)); err != nil {
		t.Fatal(err)
	}
	if err := vm.delAttr(object, "value"); err != nil {
		t.Fatal(err)
	}
	if writes != 1 || deletes != 1 {
		t.Fatalf("override calls: set=%d delete=%d", writes, deletes)
	}
	baseGet := propertyTestAttr(t, vm, vm.builtins["property"], "__get__")
	if got := propertyTestCall(t, vm, baseGet, descriptor, object, owner); got != int64(22) {
		t.Fatalf("explicit base __get__ did not bypass override: %v", got)
	}
	allocated := propertyTestCall(t, vm, propertyTestAttr(t, vm, vm.builtins["property"], "__new__"), subtype, getter)
	if got := propertyTestAttr(t, vm, allocated, "fget"); got != None {
		t.Fatal("property.__new__ initialized the accessor before __init__")
	}
	propertyTestCall(t, vm, propertyTestAttr(t, vm, vm.builtins["property"], "__init__"), allocated, getter)
	if got := propertyTestAttr(t, vm, allocated, "fget"); got != getter {
		t.Fatal("property.__init__ did not initialize a subtype")
	}
}

func TestCallableDescriptorSubtypes(t *testing.T) {
	for _, kind := range []string{"classmethod", "staticmethod"} {
		t.Run(kind, func(t *testing.T) {
			vm := propertyTestVM()
			subtype := propertyTestClass(t, vm, "WrappedMethod", []Value{vm.builtins[kind]}, nil)
			wrapped := propertyTestCallable(t, vm, "Wrapped documentation.", func(args []Value) (Value, error) {
				return &Tuple{Items: args}, nil
			})
			wrapped.dictSet("__isabstractmethod__", true)
			descriptor := propertyTestCall(t, vm, subtype, wrapped)
			if got := propertyTestAttr(t, vm, descriptor, "__func__"); got != wrapped {
				t.Fatal("__func__ does not retain the wrapped callable")
			}
			if got := propertyTestAttr(t, vm, descriptor, "__doc__"); got != "Wrapped documentation." {
				t.Fatalf("wrapped documentation = %v", got)
			}
			if got := propertyTestAttr(t, vm, descriptor, "__isabstractmethod__"); got != true {
				t.Fatal("abstract flag was not derived from the wrapped callable")
			}
			wrapped.dictSet("__isabstractmethod__", false)
			owner := propertyTestClass(t, vm, "MethodOwner", nil, map[string]Value{"method": descriptor})
			derived := propertyTestClass(t, vm, "DerivedOwner", []Value{owner}, nil)
			for _, receiver := range []Value{derived, propertyTestCall(t, vm, derived)} {
				method := propertyTestAttr(t, vm, receiver, "method")
				result := propertyTestCall(t, vm, method, int64(9)).(*Tuple)
				if kind == "classmethod" {
					if len(result.Items) != 2 || result.Items[0] != derived || result.Items[1] != int64(9) {
						t.Fatalf("classmethod binding = %v", result.Items)
					}
				} else if len(result.Items) != 1 || result.Items[0] != int64(9) {
					t.Fatalf("staticmethod binding = %v", result.Items)
				}
			}
			if kind == "staticmethod" {
				result := propertyTestCall(t, vm, descriptor, int64(10)).(*Tuple)
				if len(result.Items) != 1 || result.Items[0] != int64(10) {
					t.Fatalf("static descriptor call = %v", result.Items)
				}
			}
			propertyTestError(t, vm.setAttr(descriptor, "__func__", None), "AttributeError")
		})
	}
}

func TestCallableDescriptorBaseInitializationAndNamespace(t *testing.T) {
	vm := propertyTestVM()
	wrapped := propertyTestCallable(t, vm, "Original documentation.", func(args []Value) (Value, error) { return None, nil })
	for _, kind := range []string{"classmethod", "staticmethod"} {
		subtype := propertyTestClass(t, vm, "MethodSubtype", []Value{vm.builtins[kind]}, nil)
		allocated := propertyTestCall(t, vm, propertyTestAttr(t, vm, vm.builtins[kind], "__new__"), subtype, wrapped)
		if got := propertyTestAttr(t, vm, allocated, "__func__"); got != None {
			t.Fatal("descriptor.__new__ initialized the wrapped callable")
		}
		propertyTestCall(t, vm, propertyTestAttr(t, vm, vm.builtins[kind], "__init__"), allocated, wrapped)
		if got := propertyTestAttr(t, vm, allocated, "__func__"); got != wrapped {
			t.Fatal("descriptor.__init__ did not initialize the subtype")
		}
		base := propertyTestCall(t, vm, vm.builtins[kind], wrapped)
		ns := propertyTestAttr(t, vm, base, "__dict__").(*Dict)
		ns.SetStr("extra", int64(42))
		if got := propertyTestAttr(t, vm, base, "extra"); got != int64(42) {
			t.Fatal("descriptor namespace is not live")
		}
		replacement := NewDict()
		replacement.SetStr("replacement", true)
		if err := vm.setAttr(base, "__dict__", replacement); err != nil {
			t.Fatal(err)
		}
		if got := propertyTestAttr(t, vm, base, "__dict__"); got != replacement {
			t.Fatal("descriptor __dict__ assignment lost dictionary identity")
		}
	}
}
