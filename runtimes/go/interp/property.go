package interp

func propertyValue(value Value) (*Property, bool) {
	if instance, ok := value.(*Instance); ok {
		value = instance.Prim
	}
	property, ok := value.(*Property)
	return property, ok
}

func propertyOptional(value Value) Value {
	if _, none := value.(NoneType); none {
		return nil
	}
	return value
}

func ctorProperty(vm *VM, args []Value, kw []KwArg) (Value, error) {
	property := &Property{}
	if err := vm.initProperty(property, args, kw); err != nil {
		return nil, err
	}
	return property, nil
}

func (vm *VM) initProperty(receiver Value, args []Value, kw []KwArg) error {
	property, ok := propertyValue(receiver)
	if !ok {
		return newError("TypeError", "descriptor '__init__' requires a 'property' object")
	}
	if err := argCount("property", args, 0, 4); err != nil {
		return err
	}
	var values [4]Value
	var assigned [4]bool
	for i, value := range args {
		values[i], assigned[i] = propertyOptional(value), true
	}
	for _, item := range kw {
		index := 0
		switch item.Name {
		case "fget":
			index = 0
		case "fset":
			index = 1
		case "fdel":
			index = 2
		case "doc":
			index = 3
		default:
			return newErrorf("TypeError", "'%s' is an invalid keyword argument for property()", item.Name)
		}
		if assigned[index] {
			return newErrorf("TypeError", "argument for property() given by name ('%s') and position (%d)", item.Name, index+1)
		}
		values[index], assigned[index] = propertyOptional(item.Val), true
	}
	property.Fget, property.Fset, property.Fdel = values[0], values[1], values[2]
	property.Doc, property.getterDoc, property.propName = nil, false, nil
	doc := values[3]
	if doc == nil && property.Fget != nil {
		value, err := vm.getAttr(property.Fget, "__doc__")
		if err != nil {
			if _, missing := raisedAs(err, "AttributeError"); !missing {
				return err
			}
		} else {
			doc = propertyOptional(value)
			property.getterDoc = doc != nil
		}
	}
	if _, subtype := receiver.(*Instance); subtype {
		// A subclass's class doc shadows the inherited member descriptor.
		// CPython therefore stores the property doc on the descriptor instance.
		if err := vm.setAttr(receiver, "__doc__", orNone(doc)); err != nil {
			if _, missing := raisedAs(err, "AttributeError"); !property.getterDoc && missing {
				return nil
			}
			return err
		}
	} else {
		property.Doc = doc
	}
	return nil
}

func (vm *VM) propertyMissingAccessor(property *Property, object Value, accessor string) error {
	name := property.propName
	if name == nil && property.Fget != nil {
		value, err := vm.getAttr(property.Fget, "__name__")
		if err != nil {
			if _, missing := raisedAs(err, "AttributeError"); !missing {
				return err
			}
		} else {
			name = value
		}
	}
	if name != nil {
		text, err := vm.repr(name)
		if err != nil {
			return err
		}
		return newErrorf("AttributeError", "property %s of '%s' object has no %s", text, typeName(object), accessor)
	}
	return newErrorf("AttributeError", "property of '%s' object has no %s", typeName(object), accessor)
}

func (vm *VM) propertyBuiltinGet(descriptor, object Value) (Value, error) {
	property, ok := propertyValue(descriptor)
	if !ok {
		return nil, newError("TypeError", "descriptor '__get__' requires a 'property' object")
	}
	if _, none := object.(NoneType); none {
		return descriptor, nil
	}
	if property.Fget == nil {
		return nil, vm.propertyMissingAccessor(property, object, "getter")
	}
	return vm.call(property.Fget, []Value{object}, nil)
}

func (vm *VM) propertyBuiltinSet(descriptor, object, value Value, deleting bool) error {
	property, ok := propertyValue(descriptor)
	if !ok {
		return newError("TypeError", "descriptor requires a 'property' object")
	}
	if deleting {
		if property.Fdel == nil {
			return vm.propertyMissingAccessor(property, object, "deleter")
		}
		_, err := vm.call(property.Fdel, []Value{object}, nil)
		return err
	}
	if property.Fset == nil {
		return vm.propertyMissingAccessor(property, object, "setter")
	}
	_, err := vm.call(property.Fset, []Value{object, value}, nil)
	return err
}

func (vm *VM) propertyGet(descriptor, object, owner Value) (Value, error) {
	if subtype, ok := descriptor.(*Instance); ok {
		if _, overrides := subtype.Class.Lookup("__get__"); overrides {
			return vm.callDunder(subtype, "__get__", []Value{object, owner})
		}
	}
	return vm.propertyBuiltinGet(descriptor, object)
}

func (vm *VM) propertySet(descriptor, object, value Value) error {
	if subtype, ok := descriptor.(*Instance); ok {
		if _, overrides := subtype.Class.Lookup("__set__"); overrides {
			_, err := vm.callDunder(subtype, "__set__", []Value{object, value})
			return err
		}
	}
	return vm.propertyBuiltinSet(descriptor, object, value, false)
}

func (vm *VM) propertyDelete(descriptor, object Value) error {
	if subtype, ok := descriptor.(*Instance); ok {
		if _, overrides := subtype.Class.Lookup("__delete__"); overrides {
			_, err := vm.callDunder(subtype, "__delete__", []Value{object})
			return err
		}
	}
	return vm.propertyBuiltinSet(descriptor, object, nil, true)
}

func (vm *VM) copyProperty(receiver Value, name string, replacement Value) (Value, error) {
	property, ok := propertyValue(receiver)
	if !ok {
		return nil, newErrorf("TypeError", "descriptor '%s' requires a 'property' object", name)
	}
	getter, setter, deleter := property.Fget, property.Fset, property.Fdel
	if replacement = propertyOptional(replacement); replacement != nil {
		switch name {
		case "getter":
			getter = replacement
		case "setter":
			setter = replacement
		case "deleter":
			deleter = replacement
		}
	}
	doc := property.Doc
	if property.getterDoc {
		doc = nil
	}
	args := []Value{orNone(getter), orNone(setter), orNone(deleter), orNone(doc)}
	var result Value
	var err error
	if subtype, ok := receiver.(*Instance); ok {
		// Reconstruct through the actual type, including user __new__/__init__.
		result, err = vm.call(subtype.Class, args, nil)
	} else {
		result, err = ctorProperty(vm, args, nil)
	}
	if err == nil {
		if copied, ok := propertyValue(result); ok {
			copied.propName = property.propName
		}
	}
	return result, err
}

func (vm *VM) propertyAbstract(property *Property) (Value, error) {
	for _, accessor := range [3]Value{property.Fget, property.Fset, property.Fdel} {
		if accessor == nil {
			continue
		}
		value, err := vm.getAttr(accessor, "__isabstractmethod__")
		if err != nil {
			if _, missing := raisedAs(err, "AttributeError"); missing {
				continue
			}
			return nil, err
		}
		abstract, err := vm.truth(value)
		if err != nil || abstract {
			return abstract, err
		}
	}
	return false, nil
}

func (vm *VM) propertyAttr(receiver Value, name string) (Value, error) {
	property, ok := propertyValue(receiver)
	if !ok {
		return nil, newError("TypeError", "property attribute requires a property")
	}
	switch name {
	case "fget":
		return orNone(property.Fget), nil
	case "fset":
		return orNone(property.Fset), nil
	case "fdel":
		return orNone(property.Fdel), nil
	case "__doc__":
		return orNone(property.Doc), nil
	case "__isabstractmethod__":
		return vm.propertyAbstract(property)
	case "__class__":
		if subtype, ok := receiver.(*Instance); ok {
			return subtype.Class, nil
		}
		return vm.types["property"], nil
	}
	if method := vm.propertyTypeAttr(name); method != nil {
		if fn, ok := method.(*Builtin); ok {
			if name == "__new__" {
				return fn, nil
			}
			return &BoundMethod{Fn: fn, Recv: receiver}, nil
		}
	}
	return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'", typeName(receiver), name)
}

func (vm *VM) propertySetAttr(receiver Value, name string, value Value, deleting bool) (bool, error) {
	property, ok := propertyValue(receiver)
	if !ok {
		return false, nil
	}
	if subtype, ok := receiver.(*Instance); ok {
		// Subclass class attributes may replace the inherited member descriptors.
		if _, overrides := subtype.Class.Lookup(name); overrides {
			return false, nil
		}
		if name == "__doc__" {
			return false, nil
		}
	}
	switch name {
	case "fget", "fset", "fdel", "__isabstractmethod__":
		return true, newError("AttributeError", "readonly attribute")
	case "__doc__":
		if deleting {
			property.Doc = nil
		} else {
			property.Doc = propertyOptional(value)
		}
		return true, nil
	}
	return false, nil
}

func (vm *VM) propertyTypeAttr(name string) Value {
	switch name {
	case "__new__":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if len(args) == 0 {
				return nil, newError("TypeError", "property.__new__ requires a type argument")
			}
			if args[0] == vm.types["property"] {
				return &Property{}, nil
			}
			if subtype, ok := args[0].(*Class); ok && subtype.Prim != nil && subtype.Prim.Name == "property" {
				return newInstance(subtype, &Property{}), nil
			}
			return nil, newError("TypeError", "property.__new__ requires a subtype of property")
		})
	case "__init__":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if len(args) == 0 {
				return nil, newError("TypeError", "property.__init__ requires a property argument")
			}
			return None, vm.initProperty(args[0], args[1:], kw)
		})
	case "__set_name__":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 3, 3); err != nil {
				return nil, err
			}
			property, ok := propertyValue(args[0])
			if !ok {
				return nil, newError("TypeError", "descriptor '__set_name__' requires a property")
			}
			property.propName = args[2]
			return None, nil
		})
	case "getter", "setter", "deleter":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 2, 2); err != nil {
				return nil, err
			}
			return vm.copyProperty(args[0], name, args[1])
		})
	case "__get__":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 2, 3); err != nil {
				return nil, err
			}
			return vm.propertyBuiltinGet(args[0], args[1])
		})
	case "__set__", "__delete__":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			count := 3
			if name == "__delete__" {
				count = 2
			}
			if err := argCount(name, args, count, count); err != nil {
				return nil, err
			}
			if count == 2 {
				return None, vm.propertyBuiltinSet(args[0], args[1], nil, true)
			}
			return None, vm.propertyBuiltinSet(args[0], args[1], args[2], false)
		})
	case "fget", "fset", "fdel":
		return &metadataDescriptor{owner: vm.types["property"], name: name, kind: "member_descriptor", getter: true}
	case "__isabstractmethod__":
		return &metadataDescriptor{owner: vm.types["property"], name: name, kind: "getset_descriptor", getter: true}
	}
	return nil
}

func callableDescriptorKind(value Value) string {
	switch rawPrim(value).(type) {
	case *StaticMethod:
		return "staticmethod"
	case *ClassMethod:
		return "classmethod"
	}
	return ""
}

func callableDescriptorStorage(value Value) (*Value, **Dict) {
	switch descriptor := rawPrim(value).(type) {
	case *StaticMethod:
		return &descriptor.Fn, &descriptor.attrs
	case *ClassMethod:
		return &descriptor.Fn, &descriptor.attrs
	}
	return nil, nil
}

func ctorStaticMethod(vm *VM, args []Value, kw []KwArg) (Value, error) {
	descriptor := &StaticMethod{}
	return descriptor, vm.initCallableDescriptor(descriptor, args, kw)
}

func ctorClassMethod(vm *VM, args []Value, kw []KwArg) (Value, error) {
	descriptor := &ClassMethod{}
	return descriptor, vm.initCallableDescriptor(descriptor, args, kw)
}

func (vm *VM) initCallableDescriptor(receiver Value, args []Value, kw []KwArg) error {
	kind := callableDescriptorKind(receiver)
	if kind == "" {
		return newError("TypeError", "descriptor initializer requires a classmethod or staticmethod")
	}
	if err := noKw(kind, kw); err != nil {
		return err
	}
	if err := argCount(kind, args, 1, 1); err != nil {
		return err
	}
	callable, _ := callableDescriptorStorage(receiver)
	*callable = args[0]
	for _, name := range [...]string{"__module__", "__name__", "__qualname__", "__doc__", "__annotations__"} {
		value, err := vm.getAttr(args[0], name)
		if err != nil {
			if _, missing := raisedAs(err, "AttributeError"); missing {
				continue
			}
			return err
		}
		if err := vm.setAttr(receiver, name, value); err != nil {
			return err
		}
	}
	return nil
}

func (vm *VM) callableDescriptorBuiltinGet(descriptor, object, owner Value) (Value, error) {
	callable, _ := callableDescriptorStorage(descriptor)
	kind := callableDescriptorKind(descriptor)
	if callable == nil {
		return nil, newError("TypeError", "descriptor requires a classmethod or staticmethod")
	}
	if *callable == nil {
		return nil, newErrorf("RuntimeError", "uninitialized %s object", kind)
	}
	if kind == "staticmethod" {
		return *callable, nil
	}
	if owner == nil {
		owner = None
	}
	if _, none := owner.(NoneType); none {
		if _, none := object.(NoneType); none {
			return nil, newError("TypeError", "__get__(None, None) is invalid")
		}
		var err error
		owner, err = ctorType(vm, []Value{object}, nil)
		if err != nil {
			return nil, err
		}
	}
	// CPython 3.12 classmethod delegates to a wrapped descriptor's __get__.
	// In particular, wrapping a property passes the class to its getter.
	if _, property := propertyValue(*callable); property {
		return vm.propertyGet(*callable, owner, owner)
	}
	if callableDescriptorKind(*callable) != "" {
		return vm.callableDescriptorGet(*callable, owner, owner)
	}
	if instance, ok := (*callable).(*Instance); ok {
		if _, descriptor := instance.Class.Lookup("__get__"); descriptor {
			return vm.callDunder(instance, "__get__", []Value{owner, owner})
		}
	}
	if descriptor, ok := (*callable).(*metadataDescriptor); ok {
		get, err := descriptor.attr(vm, "__get__")
		if err != nil {
			return nil, err
		}
		return vm.call(get, []Value{owner, owner}, nil)
	}
	return &BoundMethod{Fn: *callable, Recv: owner}, nil
}

func (vm *VM) callableDescriptorGet(descriptor, object, owner Value) (Value, error) {
	if subtype, ok := descriptor.(*Instance); ok {
		if _, overrides := subtype.Class.Lookup("__get__"); overrides {
			return vm.callDunder(subtype, "__get__", []Value{object, owner})
		}
	}
	return vm.callableDescriptorBuiltinGet(descriptor, object, owner)
}

func (vm *VM) callableDescriptorAttr(receiver Value, name string) (Value, error) {
	callable, attrs := callableDescriptorStorage(receiver)
	if callable == nil {
		return nil, newError("TypeError", "descriptor attribute requires a classmethod or staticmethod")
	}
	kind := callableDescriptorKind(receiver)
	switch name {
	case "__func__", "__wrapped__":
		return orNone(*callable), nil
	case "__class__":
		if instance, ok := receiver.(*Instance); ok {
			return instance.Class, nil
		}
		return vm.types[kind], nil
	case "__dict__":
		if instance, ok := receiver.(*Instance); ok {
			return vm.baseGetAttr(instance, name)
		}
		if *attrs == nil {
			*attrs = NewDict()
		}
		return *attrs, nil
	case "__isabstractmethod__":
		if *callable == nil {
			return false, nil
		}
		value, err := vm.getAttr(*callable, name)
		if err != nil {
			if _, missing := raisedAs(err, "AttributeError"); missing {
				return false, nil
			}
			return nil, err
		}
		return vm.truth(value)
	}
	if *attrs != nil {
		if value, found := (*attrs).GetStr(name); found {
			return value, nil
		}
	}
	if method := vm.callableDescriptorTypeAttr(kind, name); method != nil {
		if fn, ok := method.(*Builtin); ok {
			if name == "__new__" {
				return fn, nil
			}
			return &BoundMethod{Fn: fn, Recv: receiver}, nil
		}
	}
	return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'", kind, name)
}

func (vm *VM) callableDescriptorSetAttr(receiver Value, name string, value Value, deleting bool) (bool, error) {
	_, attrs := callableDescriptorStorage(receiver)
	if attrs == nil {
		return false, nil
	}
	if subtype, ok := receiver.(*Instance); ok {
		if _, overrides := subtype.Class.Lookup(name); overrides {
			return false, nil
		}
		switch name {
		case "__func__", "__wrapped__", "__isabstractmethod__":
			return true, newError("AttributeError", "readonly attribute")
		}
		return false, nil
	}
	switch name {
	case "__func__", "__wrapped__", "__isabstractmethod__":
		return true, newError("AttributeError", "readonly attribute")
	case "__dict__":
		if deleting {
			return true, newError("TypeError", "cannot delete __dict__")
		}
		dict, ok := value.(*Dict)
		if !ok {
			return true, newError("TypeError", "__dict__ must be set to a dictionary")
		}
		*attrs = dict
		return true, nil
	}
	if deleting {
		if *attrs != nil {
			_, found, err := (*attrs).Delete(name)
			if err != nil || found {
				return true, err
			}
		}
		return true, newErrorf("AttributeError", "'%s' object has no attribute '%s'", callableDescriptorKind(receiver), name)
	}
	if *attrs == nil {
		*attrs = NewDict()
	}
	(*attrs).SetStr(name, value)
	return true, nil
}

func (vm *VM) callableDescriptorTypeAttr(kind, name string) Value {
	if kind != "classmethod" && kind != "staticmethod" {
		return nil
	}
	switch name {
	case "__new__":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if len(args) == 0 {
				return nil, newErrorf("TypeError", "%s.__new__ requires a type argument", kind)
			}
			var primitive Value
			if kind == "staticmethod" {
				primitive = &StaticMethod{}
			} else {
				primitive = &ClassMethod{}
			}
			if args[0] == vm.types[kind] {
				return primitive, nil
			}
			if cls, ok := args[0].(*Class); ok && cls.Prim != nil && cls.Prim.Name == kind {
				return newInstance(cls, primitive), nil
			}
			return nil, newErrorf("TypeError", "%s.__new__ requires a subtype of %s", kind, kind)
		})
	case "__init__":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if len(args) == 0 || callableDescriptorKind(args[0]) != kind {
				return nil, newErrorf("TypeError", "%s.__init__ requires a %s object", kind, kind)
			}
			return None, vm.initCallableDescriptor(args[0], args[1:], kw)
		})
	case "__get__":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 2, 3); err != nil {
				return nil, err
			}
			if callableDescriptorKind(args[0]) != kind {
				return nil, newErrorf("TypeError", "descriptor '__get__' requires a %s object", kind)
			}
			owner := Value(None)
			if len(args) == 3 {
				owner = args[2]
			}
			return vm.callableDescriptorBuiltinGet(args[0], args[1], owner)
		})
	case "__call__":
		if kind == "staticmethod" {
			return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if len(args) == 0 || callableDescriptorKind(args[0]) != kind {
					return nil, newError("TypeError", "descriptor '__call__' requires a staticmethod object")
				}
				callable, _ := callableDescriptorStorage(args[0])
				return vm.call(*callable, args[1:], kw)
			})
		}
	case "__func__", "__wrapped__":
		return &metadataDescriptor{owner: vm.types[kind], name: name, kind: "member_descriptor", getter: true}
	case "__isabstractmethod__":
		return &metadataDescriptor{owner: vm.types[kind], name: name, kind: "getset_descriptor", getter: true}
	}
	return nil
}
