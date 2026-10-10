package interp

import (
	"math/big"
	"strings"
)

func objectReduce(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("__reduce__", kw); err != nil {
		return nil, err
	}
	if err := argCount("__reduce__", args, 1, 1); err != nil {
		return nil, err
	}
	return objectCommonReduce(vm, args[0], 0)
}

func objectReduceEx(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("__reduce_ex__", kw); err != nil {
		return nil, err
	}
	if err := argCount("__reduce_ex__", args, 2, 2); err != nil {
		return nil, err
	}
	protocol, err := objectReductionProtocol(vm, args[1])
	if err != nil {
		return nil, err
	}
	self := args[0]
	reduce, err := vm.getAttr(self, "__reduce__")
	if _, missing := raisedAs(err, "AttributeError"); !missing {
		if err != nil {
			return nil, err
		}
		cls, err := ctorType(vm, []Value{self}, nil)
		if err != nil {
			return nil, err
		}
		classReduce, err := vm.getAttr(cls, "__reduce__")
		if err != nil {
			return nil, err
		}
		if classReduce != vm.objectMetadataDescriptor("__reduce__") {
			return vm.call(reduce, nil, nil)
		}
	}
	return objectCommonReduce(vm, self, protocol)
}

func objectReductionProtocol(vm *VM, value Value) (int64, error) {
	index, err := percentIndex(vm, value)
	if err != nil {
		return 0, err
	}
	protocol, ok := asInt(index)
	if !ok || protocol < -2147483648 || protocol > 2147483647 {
		return 0, newError("OverflowError", "Python int too large to convert to C int")
	}
	return protocol, nil
}

func objectCopyreg(vm *VM, name string) (Value, error) {
	module, err := vm.importModule("copyreg")
	if err != nil {
		return nil, err
	}
	return vm.getAttr(module, name)
}

func objectCommonReduce(vm *VM, self Value, protocol int64) (Value, error) {
	if protocol < 2 {
		helper, err := objectCopyreg(vm, "_reduce_ex")
		if err != nil {
			return nil, err
		}
		return vm.call(helper, []Value{self, protocol}, nil)
	}
	cls, err := ctorType(vm, []Value{self}, nil)
	if err != nil {
		return nil, err
	}
	if !objectCanNewReduce(self) {
		return nil, objectPickleError(self)
	}
	args, kwargs, err := objectNewArguments(vm, self)
	if err != nil {
		return nil, err
	}
	var constructor Value
	var newargs *Tuple
	if kwargs == nil || !objectReductionDictNonempty(kwargs) {
		constructor, err = objectCopyreg(vm, "__newobj__")
		var items []Value
		if args != nil {
			items = rawPrim(args).(*Tuple).Items
		}
		newargs = &Tuple{Items: make([]Value, len(items)+1)}
		newargs.Items[0] = cls
		copy(newargs.Items[1:], items)
	} else {
		constructor, err = objectCopyreg(vm, "__newobj_ex__")
		newargs = &Tuple{Items: []Value{cls, args, kwargs}}
	}
	if err != nil {
		return nil, err
	}
	_, list := rawPrim(self).(*List)
	_, dict := rawPrim(self).(*Dict)
	state, err := objectReductionState(vm, self, args == nil && !list && !dict)
	if err != nil {
		return nil, err
	}
	var listitems, dictitems Value = None, None
	if list {
		listitems, err = vm.getIter(self)
		if err != nil {
			return nil, err
		}
	}
	if dict {
		method, err := vm.getAttr(self, "items")
		if err != nil {
			return nil, err
		}
		items, err := vm.call(method, nil, nil)
		if err != nil {
			return nil, err
		}
		dictitems, err = vm.getIter(items)
		if err != nil {
			return nil, err
		}
	}
	return &Tuple{Items: []Value{constructor, newargs, state, listitems, dictitems}}, nil
}

func objectCanNewReduce(self Value) bool {
	switch self.(type) {
	case *Instance, NoneType, bool, int64, *big.Int, float64, complex128, string, []byte, *Tuple, *List, *Dict:
		return true
	}
	return false
}

func objectPickleError(self Value) error {
	return newErrorf("TypeError", "cannot pickle '%s' object", typeName(self))
}

func objectReductionDictNonempty(value Value) bool {
	switch dict := rawPrim(value).(type) {
	case *Dict:
		return dict.Len() != 0
	case *metadataNamespace:
		return dict.len() != 0
	}
	return false
}

// __getnewargs__ uses special lookup: instance attributes and a custom
// __getattribute__ must not replace a method defined on the actual type.
func objectReductionSpecial(vm *VM, self Value, name string) (Value, bool, error) {
	instance, ok := self.(*Instance)
	if !ok {
		return nil, false, nil
	}
	slot, found := instance.Class.Lookup(name)
	if !found {
		return nil, false, nil
	}
	if _, property := propertyValue(slot); property {
		value, err := vm.propertyGet(slot, self, instance.Class)
		return value, true, err
	}
	if callableDescriptorKind(slot) != "" {
		value, err := vm.callableDescriptorGet(slot, self, instance.Class)
		return value, true, err
	}
	if descriptor, ok := slot.(*Instance); ok {
		if _, found := descriptor.Class.Lookup("__get__"); found {
			value, err := vm.callDunder(descriptor, "__get__", []Value{self, instance.Class})
			return value, true, err
		}
	}
	return vm.bindAttr(slot, self, instance.Class), true, nil
}

func objectNewArguments(vm *VM, self Value) (Value, Value, error) {
	method, found, err := objectReductionSpecial(vm, self, "__getnewargs_ex__")
	if err != nil {
		return nil, nil, err
	}
	if found {
		result, err := vm.call(method, nil, nil)
		if err != nil {
			return nil, nil, err
		}
		pair, ok := rawPrim(result).(*Tuple)
		if !ok {
			return nil, nil, newErrorf("TypeError", "__getnewargs_ex__ should return a tuple, not '%s'", typeName(result))
		}
		if len(pair.Items) != 2 {
			return nil, nil, newErrorf("ValueError", "__getnewargs_ex__ should return a tuple of length 2, not %d", len(pair.Items))
		}
		_, ok = rawPrim(pair.Items[0]).(*Tuple)
		if !ok {
			return nil, nil, newErrorf("TypeError", "first item of the tuple returned by __getnewargs_ex__ must be a tuple, not '%s'", typeName(pair.Items[0]))
		}
		switch rawPrim(pair.Items[1]).(type) {
		case *Dict, *metadataNamespace:
			return pair.Items[0], pair.Items[1], nil
		default:
			return nil, nil, newErrorf("TypeError", "second item of the tuple returned by __getnewargs_ex__ must be a dict, not '%s'", typeName(pair.Items[1]))
		}
	}
	method, found, err = objectReductionSpecial(vm, self, "__getnewargs__")
	if err != nil {
		return nil, nil, err
	}
	if found {
		result, err := vm.call(method, nil, nil)
		if err != nil {
			return nil, nil, err
		}
		_, ok := rawPrim(result).(*Tuple)
		if !ok {
			return nil, nil, newErrorf("TypeError", "__getnewargs__ should return a tuple, not '%s'", typeName(result))
		}
		return result, nil, nil
	}
	switch value := rawPrim(self).(type) {
	case bool:
		integer := int64(0)
		if value {
			integer = 1
		}
		return &Tuple{Items: []Value{integer}}, nil, nil
	case int64, *big.Int, float64, string, []byte, *Tuple:
		return &Tuple{Items: []Value{value}}, nil, nil
	case complex128:
		return &Tuple{Items: []Value{real(value), imag(value)}}, nil, nil
	}
	return nil, nil, nil
}

func objectGetState(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("__getstate__", kw); err != nil {
		return nil, err
	}
	if err := argCount("__getstate__", args, 1, 1); err != nil {
		return nil, err
	}
	return objectDefaultState(vm, args[0], false)
}

func objectReductionState(vm *VM, self Value, required bool) (Value, error) {
	method, err := vm.getAttr(self, "__getstate__")
	if err != nil {
		return nil, err
	}
	if descriptor, ok := method.(*metadataDescriptor); ok && descriptor.owner == objectClass && descriptor.name == "__getstate__" && isIdentical(descriptor.receiver, self) {
		return objectDefaultState(vm, self, required)
	}
	return vm.call(method, nil, nil)
}

func objectSlotNames(vm *VM, cls *Class) (*List, error) {
	value, cached := cls.NS["__slotnames__"]
	if cached {
		if _, none := value.(NoneType); none {
			return nil, nil
		}
		if names, ok := rawPrim(value).(*List); ok {
			return names, nil
		}
		return nil, newErrorf("TypeError", "%s.__slotnames__ should be a list or None, not %s", cls.Name, typeName(value))
	}
	helper, err := objectCopyreg(vm, "_slotnames")
	if err != nil {
		return nil, err
	}
	value, err = vm.call(helper, []Value{cls}, nil)
	if err != nil {
		return nil, err
	}
	if _, none := value.(NoneType); none {
		return nil, nil
	}
	if names, ok := rawPrim(value).(*List); ok {
		return names, nil
	}
	return nil, newError("TypeError", "copyreg._slotnames didn't return a list or None")
}

func objectDefaultState(vm *VM, self Value, required bool) (Value, error) {
	instance, ok := self.(*Instance)
	if !ok {
		if required && !objectCanNewReduce(self) {
			return nil, objectPickleError(self)
		}
		if function, ok := self.(*Function); ok && len(function.attrs) != 0 {
			return vm.getAttr(function, "__dict__")
		}
		return None, nil
	}
	if required && instance.Prim != nil {
		switch rawPrim(instance).(type) {
		case NoneType, *Instance:
		default:
			return nil, objectPickleError(self)
		}
	}
	var state Value = None
	if instanceHasDict(instance.Class) && !instanceDictionaryEmpty(instance) {
		var err error
		state, err = vm.instanceDictionary(instance)
		if err != nil {
			return nil, err
		}
	}
	names, err := objectSlotNames(vm, instance.Class)
	if err != nil {
		return nil, err
	}
	if required {
		count := 0
		if names != nil {
			count = len(names.Items)
		}
		if instanceSlotCount(instance.Class) > count {
			return nil, objectPickleError(self)
		}
	}
	if names == nil || len(names.Items) == 0 {
		return state, nil
	}
	slots := NewDict()
	count := len(names.Items)
	for i := range count {
		nameValue := names.Items[i]
		name, ok := rawPrim(nameValue).(string)
		if !ok {
			return nil, newError("TypeError", "attribute name must be string")
		}
		value, err := vm.getAttr(self, name)
		if _, absent := raisedAs(err, "AttributeError"); !absent {
			if err != nil {
				return nil, err
			}
			if err := slots.Set(nameValue, value); err != nil {
				return nil, err
			}
		}
		if count != len(names.Items) {
			return nil, newError("RuntimeError", "__slotsname__ changed size during iteration")
		}
	}
	if slots.Len() != 0 {
		return &Tuple{Items: []Value{state, slots}}, nil
	}
	return state, nil
}

func objectBuiltinReduce(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("__reduce__", kw); err != nil {
		return nil, err
	}
	if err := argCount("__reduce__", args, 1, 1); err != nil {
		return nil, err
	}
	self := args[0]
	switch rawPrim(self).(type) {
	case EllipsisType:
		return "Ellipsis", nil
	case NotImplementedType:
		return "NotImplemented", nil
	case *ByteArray:
		return bytearrayReduction(vm, self, 2)
	case *Set, *FrozenSet:
		items, err := vm.iterToSlice(self)
		if err != nil {
			return nil, err
		}
		cls, err := ctorType(vm, []Value{self}, nil)
		if err != nil {
			return nil, err
		}
		state, err := objectReductionState(vm, self, false)
		if err != nil {
			return nil, err
		}
		return &Tuple{Items: []Value{cls, &Tuple{Items: []Value{&List{Items: items}}}, state}}, nil
	}
	return nil, objectPickleError(self)
}

func bytearrayReduceEx(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("__reduce_ex__", kw); err != nil {
		return nil, err
	}
	if err := argCount("__reduce_ex__", args, 1, 2); err != nil {
		return nil, err
	}
	protocol := int64(0)
	if len(args) == 2 {
		var err error
		protocol, err = objectReductionProtocol(vm, args[1])
		if err != nil {
			return nil, err
		}
	}
	return bytearrayReduction(vm, args[0], protocol)
}

func bytearrayReduction(vm *VM, self Value, protocol int64) (Value, error) {
	value, ok := rawPrim(self).(*ByteArray)
	if !ok {
		return nil, newError("TypeError", "bytearray reduction requires a bytearray object")
	}
	state, err := objectReductionState(vm, self, false)
	if err != nil {
		return nil, err
	}
	cls, err := ctorType(vm, []Value{self}, nil)
	if err != nil {
		return nil, err
	}
	args := &Tuple{}
	if len(value.data) != 0 {
		if protocol < 3 {
			var text strings.Builder
			for _, b := range value.data {
				text.WriteRune(rune(b))
			}
			args.Items = []Value{text.String(), "latin-1"}
		} else {
			// A reduction captures immutable bytes, not the mutable backing array.
			args.Items = []Value{append([]byte(nil), value.data...)}
		}
	}
	return &Tuple{Items: []Value{cls, args, state}}, nil
}
