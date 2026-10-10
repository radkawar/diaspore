//go:build !linux

package interp

import "testing"

func TestNativeNetSelectUnsupported(t *testing.T) {
	vm := &VM{caps: map[string]bool{"net": true}}
	module := vm.makeNetModule()
	args := []Value{&List{}, &List{}, &List{}, int64(0)}
	vm.gil.Lock()
	_, err := module.NS["select"].(*Builtin).Fn(vm, args, nil)
	vm.gil.Unlock()
	if _, ok := raisedAs(err, "NotImplementedError"); !ok {
		t.Fatalf("unsupported platform must fail explicitly: %v", err)
	}
}
