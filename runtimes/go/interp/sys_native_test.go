package interp

import (
	"runtime"
	"testing"
)

func TestNativeFilesystemEncoding(t *testing.T) {
	vm := acceleratorTestVM()
	defer vm.Close()
	module := vm.natives["sys"]
	if got := acceleratorCall(t, vm, module.NS["getfilesystemencoding"]); got != "utf-8" {
		t.Fatalf("filesystem encoding = %v", got)
	}
	wantErrors := "surrogateescape"
	if runtime.GOOS == "windows" {
		wantErrors = "surrogatepass"
	}
	if got := acceleratorCall(t, vm, module.NS["getfilesystemencodeerrors"]); got != wantErrors {
		t.Fatalf("filesystem error handling = %v; want %s", got, wantErrors)
	}
	class := &Class{Name: "NativeMethods", NS: map[string]Value{
		"encoding": module.NS["getfilesystemencoding"],
	}}
	class.MRO = []*Class{class, objectClass}
	method, err := vm.getAttr(newInstance(class, nil), "encoding")
	if err != nil {
		t.Fatal(err)
	}
	if got := acceleratorCall(t, vm, method); got != "utf-8" {
		t.Fatalf("class-assigned native function = %v", got)
	}
	for _, name := range []string{"getfilesystemencoding", "getfilesystemencodeerrors"} {
		_, err := vm.call(module.NS[name], []Value{None}, nil)
		acceleratorError(t, err, "TypeError", "")
		_, err = vm.call(module.NS[name], nil, []KwArg{{Name: "value", Val: None}})
		acceleratorError(t, err, "TypeError", "")
	}
}

func TestNativeWarningOptionsAreMachineLocal(t *testing.T) {
	first := acceleratorTestVM()
	defer first.Close()
	second := acceleratorTestVM()
	defer second.Close()
	options, ok := first.natives["sys"].NS["warnoptions"].(*List)
	if !ok || len(options.Items) != 0 {
		t.Fatalf("initial warning options = %#v", first.natives["sys"].NS["warnoptions"])
	}
	options.Items = append(options.Items, "error")
	other, ok := second.natives["sys"].NS["warnoptions"].(*List)
	if !ok || other == options || len(other.Items) != 0 {
		t.Fatalf("warning options leaked across machines: %#v", second.natives["sys"].NS["warnoptions"])
	}
}

func TestNativeWarningHierarchy(t *testing.T) {
	vm := acceleratorTestVM()
	defer vm.Close()
	for _, name := range []string{
		"Warning", "UserWarning", "DeprecationWarning", "PendingDeprecationWarning",
		"SyntaxWarning", "RuntimeWarning", "FutureWarning", "ImportWarning",
		"UnicodeWarning", "BytesWarning", "ResourceWarning", "EncodingWarning",
	} {
		cls, found := vm.builtins[name]
		if !found {
			t.Fatalf("missing builtin %s", name)
		}
		if got := acceleratorCall(t, vm, vm.builtins["issubclass"], cls, vm.builtins["Warning"]); got != true {
			t.Fatalf("%s is not a Warning subclass", name)
		}
		value := acceleratorCall(t, vm, cls, "message")
		if got := acceleratorCall(t, vm, vm.builtins["isinstance"], value, vm.builtins["Exception"]); got != true {
			t.Fatalf("%s is not an Exception instance", name)
		}
		if got := acceleratorCall(t, vm, vm.builtins["str"], value); got != "message" {
			t.Fatalf("%s message = %v", name, got)
		}
	}
}
