package interp

func unaryMethodName(kind string) string {
	switch kind {
	case "abs":
		return "__abs__"
	case "neg":
		return "__neg__"
	case "pos":
		return "__pos__"
	case "invert":
		return "__invert__"
	}
	return ""
}

func unaryMethodKind(name string) string {
	switch name {
	case "__neg__":
		return "neg"
	case "__pos__":
		return "pos"
	case "__invert__":
		return "invert"
	}
	return ""
}

// Unary special methods are looked up on the type, never in the instance
// dictionary or via __getattribute__. Descriptors still bind their receiver.
func (vm *VM) unarySpecial(receiver Value, class *Class, kind string) (Value, bool, error) {
	return vm.callNoArgSpecial(receiver, class, unaryMethodName(kind))
}

func (vm *VM) callNoArgSpecial(receiver Value, class *Class, name string) (Value, bool, error) {
	if class == nil {
		return nil, false, nil
	}
	method, found := class.Lookup(name)
	if !found {
		return nil, false, nil
	}
	var callable Value
	var err error
	if descriptor, ok := method.(*metadataDescriptor); ok && descriptor.storage != slotNone {
		callable, err = descriptor.storageGet(vm, receiver)
	} else if _, property := propertyValue(method); property {
		callable, err = vm.propertyGet(method, receiver, class)
	} else if callableDescriptorKind(method) != "" {
		callable, err = vm.callableDescriptorGet(method, receiver, class)
	} else if descriptor, ok := method.(*Instance); ok {
		if _, gets := descriptor.Class.Lookup("__get__"); gets {
			callable, err = vm.callDunder(descriptor, "__get__", []Value{receiver, class})
		} else {
			callable = method
		}
	} else {
		callable = vm.bindAttr(method, receiver, class)
	}
	if err != nil {
		return nil, true, err
	}
	result, err := vm.call(callable, nil, nil)
	return result, true, err
}
