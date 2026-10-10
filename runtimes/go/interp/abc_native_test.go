package interp

import (
	"reflect"
	"testing"
)

func abcTestVM() (*VM, *NativeModule, *Class) {
	vm := New(map[string]any{
		"diaspore": float64(1), "main": "test",
		"modules": map[string]any{"test": map[string]any{"body": []any{}}},
	})
	module := vm.natives["_abc"]
	meta := &Class{Name: "ABCMeta", NS: map[string]Value{}}
	meta.MRO = []*Class{meta, objectClass}
	for _, operation := range []struct{ method, native string }{
		{"__subclasscheck__", "_abc_subclasscheck"},
		{"__instancecheck__", "_abc_instancecheck"},
	} {
		meta.NS[operation.method] = bi(operation.method, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			return vm.call(module.NS[operation.native], args, kw)
		})
	}
	return vm, module, meta
}

func abcTestClass(name string, meta, base *Class) *Class {
	cls := &Class{Name: name, Meta: meta, NS: map[string]Value{}}
	if base == nil {
		cls.Bases = []*Class{objectClass}
		cls.MRO = []*Class{cls, objectClass}
	} else {
		cls.Bases = []*Class{base}
		cls.MRO = append([]*Class{cls}, base.MRO...)
		base.subclasses = append(base.subclasses, cls)
	}
	return cls
}

func abcTestCall(t *testing.T, vm *VM, module *NativeModule, name string, args ...Value) Value {
	t.Helper()
	result, err := vm.call(module.NS[name], args, nil)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func abcTestError(t *testing.T, err error, kind, message string) {
	t.Helper()
	raised, ok := err.(*Raised)
	if !ok {
		t.Fatalf("error = %v; want %s", err, kind)
	}
	instance, ok := raised.Exc.(*Instance)
	if !ok || instance.Class.Name != kind {
		t.Fatalf("error = %v; want %s", err, kind)
	}
	if message != "" && plainExcStr(instance) != message {
		t.Fatalf("message = %q; want %q", plainExcStr(instance), message)
	}
}

func TestNativeABCAbstractMethods(t *testing.T) {
	vm, module, meta := abcTestVM()
	base := abcTestClass("Abstract", meta, nil)
	abstract := newInstance(abcTestClass("AbstractDescriptor", nil, nil), nil)
	abstract.dictSet("__isabstractmethod__", true)
	base.NS["method"] = abstract
	base.NS["concrete"] = int64(1)
	abcTestCall(t, vm, module, "_abc_init", base)
	methods := base.NS["__abstractmethods__"].(*FrozenSet)
	if !reflect.DeepEqual(methods.set.Elems(), []Value{"method"}) {
		t.Fatalf("abstract methods = %#v", methods.set.Elems())
	}
	_, err := vm.call(base, nil, nil)
	abcTestError(t, err, "TypeError", "")
	inherited := abcTestClass("Inherited", meta, base)
	abcTestCall(t, vm, module, "_abc_init", inherited)
	if inherited.NS["__abstractmethods__"].(*FrozenSet).set.Len() != 1 {
		t.Fatal("inherited abstract method lost")
	}
	concrete := abcTestClass("Concrete", meta, inherited)
	concrete.NS["method"] = int64(2)
	abcTestCall(t, vm, module, "_abc_init", concrete)
	if concrete.NS["__abstractmethods__"].(*FrozenSet).set.Len() != 0 {
		t.Fatal("concrete override remained abstract")
	}
	if _, err := vm.call(concrete, nil, nil); err != nil {
		t.Fatal(err)
	}
	if base.NS["_abc_impl"] == inherited.NS["_abc_impl"] {
		t.Fatal("subclasses share ABC state")
	}
	abstract.dictSet("__isabstractmethod__", false)
	abcTestCall(t, vm, module, "_abc_init", base)
	if base.NS["__abstractmethods__"].(*FrozenSet).set.Len() != 0 {
		t.Fatal("reinitialization did not recompute abstracts")
	}
}

func TestNativeABCRegistryAndInvalidation(t *testing.T) {
	vm, module, meta := abcTestVM()
	abc := abcTestClass("ABC", meta, nil)
	virtual := abcTestClass("Virtual", nil, nil)
	child := abcTestClass("Child", nil, virtual)
	abcTestCall(t, vm, module, "_abc_init", abc)
	data := abc.NS["_abc_impl"].(*abcData)
	if got := abcTestCall(t, vm, module, "_abc_subclasscheck", abc, virtual); got != false {
		t.Fatalf("initial subclass = %v", got)
	}
	if !data.negative.contains(virtual) {
		t.Fatal("negative cache not populated")
	}
	before := abcTestCall(t, vm, module, "get_cache_token").(int64)
	if got := abcTestCall(t, vm, module, "_abc_register", abc, virtual); got != virtual {
		t.Fatal("register did not return class")
	}
	if got := abcTestCall(t, vm, module, "get_cache_token"); got != before+1 {
		t.Fatalf("token = %v", got)
	}
	if data.version != before {
		t.Fatal("registration eagerly updated negative-cache version")
	}
	if got := abcTestCall(t, vm, module, "_abc_subclasscheck", abc, virtual); got != true {
		t.Fatalf("registered subclass = %v", got)
	}
	if data.positive.contains(virtual) {
		t.Fatal("direct registry hit populated positive cache")
	}
	if data.negative.contains(virtual) || data.version != before+1 {
		t.Fatal("negative cache not invalidated lazily")
	}
	if got := abcTestCall(t, vm, module, "_abc_subclasscheck", abc, child); got != true {
		t.Fatalf("registered descendant = %v", got)
	}
	if !data.positive.contains(child) {
		t.Fatal("descendant missing positive cache")
	}
	abcTestCall(t, vm, module, "_abc_register", abc, virtual)
	abcTestCall(t, vm, module, "_abc_register", abc, abc)
	if got := abcTestCall(t, vm, module, "get_cache_token"); got != before+1 {
		t.Fatal("duplicate registration changed token")
	}
	abcTestCall(t, vm, module, "_reset_registry", abc)
	if got := abcTestCall(t, vm, module, "_abc_subclasscheck", abc, virtual); got != false {
		t.Fatal("registry reset retained direct virtual subclass")
	}
	if got := abcTestCall(t, vm, module, "_abc_subclasscheck", abc, child); got != true {
		t.Fatal("registry reset unexpectedly cleared positive cache")
	}
	abcTestCall(t, vm, module, "_reset_caches", abc)
	if len(data.negative) != 0 || len(data.positive) != 0 || data.version != before+1 {
		t.Fatal("cache reset changed version or retained entries")
	}
	if got := abcTestCall(t, vm, module, "_abc_subclasscheck", abc, child); got != false {
		t.Fatal("cache reset retained virtual descendant")
	}
	if got := abcTestCall(t, vm, module, "get_cache_token"); got != before+1 {
		t.Fatal("reset changed global token")
	}
}

func TestNativeABCTransitiveRegistration(t *testing.T) {
	vm, module, meta := abcTestVM()
	outer := abcTestClass("Outer", meta, nil)
	inner := abcTestClass("Inner", meta, nil)
	candidate := abcTestClass("Candidate", nil, nil)
	for _, cls := range []*Class{outer, inner} {
		abcTestCall(t, vm, module, "_abc_init", cls)
	}
	abcTestCall(t, vm, module, "_abc_register", outer, inner)
	if got := abcTestCall(t, vm, module, "_abc_subclasscheck", outer, candidate); got != false {
		t.Fatal("unexpected initial subclass")
	}
	abcTestCall(t, vm, module, "_abc_register", inner, candidate)
	if got := abcTestCall(t, vm, module, "_abc_subclasscheck", outer, candidate); got != true {
		t.Fatal("global negative-cache invalidation failed")
	}
	_, err := vm.call(module.NS["_abc_register"], []Value{inner, outer}, nil)
	abcTestError(t, err, "RuntimeError", "Refusing to create an inheritance cycle")
	abcTestCall(t, vm, module, "_abc_register", inner, vm.builtins["int"])
	if got := abcTestCall(t, vm, module, "_abc_instancecheck", outer, int64(7)); got != true {
		t.Fatalf("registered builtin instance = %v", got)
	}
	if got := abcTestCall(t, vm, module, "_abc_subclasscheck", inner, vm.builtins["bool"]); got != true {
		t.Fatalf("registered builtin descendant = %v", got)
	}
	parent := abcTestClass("Parent", meta, nil)
	sub := abcTestClass("Sub", meta, parent)
	for _, cls := range []*Class{parent, sub} {
		abcTestCall(t, vm, module, "_abc_init", cls)
	}
	abcTestCall(t, vm, module, "_abc_register", sub, candidate)
	if got := abcTestCall(t, vm, module, "_abc_subclasscheck", parent, candidate); got != true {
		t.Fatal("real subclass registry was not consulted")
	}
}

func TestNativeABCSubclassHooks(t *testing.T) {
	vm, module, meta := abcTestVM()
	abc := abcTestClass("Hooked", meta, nil)
	candidate := abcTestClass("Candidate", nil, abc)
	calls := 0
	var answer Value = false
	abc.NS["__subclasshook__"] = &ClassMethod{Fn: bi("__subclasshook__", func(_ *VM, args []Value, _ []KwArg) (Value, error) {
		calls++
		if args[0] != abc || args[1] != candidate {
			t.Fatalf("hook args = %#v", args)
		}
		return answer, nil
	})}
	abcTestCall(t, vm, module, "_abc_init", abc)
	for range 2 {
		if got := abcTestCall(t, vm, module, "_abc_subclasscheck", abc, candidate); got != false {
			t.Fatal("False hook failed to override nominal inheritance")
		}
	}
	if calls != 1 {
		t.Fatalf("negative-cache hook calls = %d", calls)
	}
	answer = true
	abcTestCall(t, vm, module, "_reset_caches", abc)
	for range 2 {
		if got := abcTestCall(t, vm, module, "_abc_subclasscheck", abc, candidate); got != true {
			t.Fatal("True hook ignored")
		}
	}
	if calls != 2 {
		t.Fatalf("positive-cache hook calls = %d", calls)
	}
	answer = int64(1)
	abcTestCall(t, vm, module, "_reset_caches", abc)
	_, err := vm.call(module.NS["_abc_subclasscheck"], []Value{abc, candidate}, nil)
	abcTestError(t, err, "AssertionError", "__subclasshook__ must return either False, True, or NotImplemented")
	answer = NotImplemented
	if got := abcTestCall(t, vm, module, "_abc_subclasscheck", abc, candidate); got != true {
		t.Fatal("NotImplemented hook bypassed nominal check")
	}
}

func TestNativeABCInstanceCallbackFrameAndClass(t *testing.T) {
	vm, module, meta := abcTestVM()
	abc := abcTestClass("ABC", meta, nil)
	abcTestCall(t, vm, module, "_abc_init", abc)
	actual := abcTestClass("Actual", nil, nil)
	disguise := abcTestClass("Disguise", nil, nil)
	instance := newInstance(actual, nil)
	actual.NS["__class__"] = &Property{Fget: bi("__class__", func(_ *VM, _ []Value, _ []KwArg) (Value, error) { return disguise, nil })}
	caller := &frame{moduleName: "abc", fname: "__instancecheck__"}
	vm.metadata.active = caller
	var checked []Value
	meta.NS["__subclasscheck__"] = bi("__subclasscheck__", func(vm *VM, args []Value, _ []KwArg) (Value, error) {
		if vm.metadata.active != caller {
			t.Fatal("native ABC callback inserted or replaced a sandbox frame")
		}
		checked = append(checked, args[1])
		return args[1] == actual, nil
	})
	if got := abcTestCall(t, vm, module, "_abc_instancecheck", abc, instance); got != true {
		t.Fatalf("spoofed class instance check = %v", got)
	}
	if !reflect.DeepEqual(checked, []Value{disguise, actual}) {
		t.Fatalf("subclass callback order = %#v", checked)
	}
	checked = nil
	meta.NS["__subclasscheck__"] = bi("__subclasscheck__", func(_ *VM, args []Value, _ []KwArg) (Value, error) {
		checked = append(checked, args[1])
		return "truthy result", nil
	})
	if got := abcTestCall(t, vm, module, "_abc_instancecheck", abc, instance); got != "truthy result" {
		t.Fatalf("custom callback result = %#v", got)
	}
	if !reflect.DeepEqual(checked, []Value{disguise}) {
		t.Fatal("truthy disguised class still checked real class")
	}
}

func TestNativeABCFlagsAndData(t *testing.T) {
	vm, module, meta := abcTestVM()
	abc := abcTestClass("Sequence", meta, nil)
	abc.NS["__abc_tpflags__"] = int64(32)
	abcTestCall(t, vm, module, "_abc_init", abc)
	if abc.abcFlags != 32 {
		t.Fatal("sequence flag missing")
	}
	if _, present := abc.NS["__abc_tpflags__"]; present {
		t.Fatal("public flag declaration not consumed")
	}
	virtual := abcTestClass("Virtual", nil, nil)
	child := abcTestClass("Child", nil, virtual)
	abcTestCall(t, vm, module, "_abc_register", abc, virtual)
	if virtual.abcFlags != 32 || child.abcFlags != 32 {
		t.Fatal("collection flags did not propagate to virtual descendants")
	}
	bad := abcTestClass("BadFlags", meta, nil)
	bad.NS["__abc_tpflags__"] = int64(96)
	_, err := vm.call(module.NS["_abc_init"], []Value{bad}, nil)
	abcTestError(t, err, "TypeError", "__abc_tpflags__ cannot be both Py_TPFLAGS_SEQUENCE and Py_TPFLAGS_MAPPING")
	dataType, err := ctorType(vm, []Value{abc.NS["_abc_impl"]}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, exported := module.NS["_abc_data"]; exported {
		t.Fatal("internal ABC data type was publicly exported")
	}
	value, err := vm.call(dataType, []Value{int64(1)}, []KwArg{{Name: "ignored", Val: true}})
	if err != nil {
		t.Fatal(err)
	}
	fresh := value.(*abcData)
	if fresh.version != abcTestCall(t, vm, module, "get_cache_token") {
		t.Fatal("new ABC data has stale token")
	}
	typeValue, err := ctorType(vm, []Value{fresh}, nil)
	if err != nil || typeValue != dataType {
		t.Fatalf("ABC data type = %#v, %v", typeValue, err)
	}
	abc.NS["_abc_impl"] = fresh
	if got := abcTestCall(t, vm, module, "_abc_subclasscheck", abc, virtual); got != false {
		t.Fatal("replacement ABC data retained registry")
	}
	abc.NS["_abc_impl"] = None
	_, err = vm.call(module.NS["_reset_caches"], []Value{abc}, nil)
	abcTestError(t, err, "TypeError", "_abc_impl is set to a wrong type")
}

func TestNativeABCArgumentsAndIsolation(t *testing.T) {
	vm, module, meta := abcTestVM()
	abc := abcTestClass("ABC", meta, nil)
	abcTestCall(t, vm, module, "_abc_init", abc)
	_, err := vm.call(module.NS["_abc_register"], []Value{abc, int64(7)}, nil)
	abcTestError(t, err, "TypeError", "Can only register classes")
	_, err = vm.call(module.NS["_abc_subclasscheck"], []Value{abc, int64(7)}, nil)
	abcTestError(t, err, "TypeError", "issubclass() arg 1 must be a class")
	_, err = vm.call(module.NS["_abc_init"], nil, []KwArg{{Name: "self", Val: abc}})
	abcTestError(t, err, "TypeError", "")
	otherVM, otherModule, _ := abcTestVM()
	initial := abcTestCall(t, otherVM, otherModule, "get_cache_token")
	abcTestCall(t, vm, module, "_abc_register", abc, vm.builtins["str"])
	if abcTestCall(t, otherVM, otherModule, "get_cache_token") != initial {
		t.Fatal("ABC token leaked between VMs")
	}
}
