package interp

func (vm *VM) instantiateMeta(meta *Class, args []Value, kw []KwArg) (Value, error) {
	var value Value
	var err error
	if constructor, ok := meta.Lookup("__new__"); ok {
		if sm, ok := constructor.(*StaticMethod); ok {
			constructor = sm.Fn
		}
		value, err = vm.call(constructor, append([]Value{meta}, args...), kw)
	} else {
		value, err = ctorTypeWithMeta(vm, args, kw, meta)
	}
	if err != nil {
		return nil, err
	}
	return vm.initializeMeta(meta, value, args, kw)
}

func (vm *VM) initializeMeta(meta *Class, value Value, args []Value, kw []KwArg) (Value, error) {
	if cls, ok := value.(*Class); ok {
		matches := meta == nil || cls.Meta == meta
		if cls.Meta != nil {
			for _, ancestor := range cls.Meta.MRO {
				if ancestor == meta {
					matches = true
					break
				}
			}
		}
		if !matches {
			return value, nil
		}
		if cls.Meta == nil {
			return value, nil
		}
		meta = cls.Meta
		if init, ok := meta.Lookup("__init__"); ok {
			result, err := vm.call(vm.bindAttr(init, cls, meta), args, kw)
			if err != nil {
				return nil, err
			}
			if _, ok := result.(NoneType); !ok {
				return nil, newError("TypeError", "__init__() should return None")
			}
		}
	}
	return value, nil
}

func (vm *VM) selectMetaclass(explicit Value, bases []*Class) (Value, error) {
	winner := explicit
	if winner == nil {
		winner = vm.types["type"]
	}
	for _, base := range bases {
		if base.Meta == nil {
			continue
		}
		if bt, ok := winner.(*BuiltinType); ok && bt.Name == "type" {
			winner = base.Meta
			continue
		}
		current, ok := winner.(*Class)
		if !ok {
			continue
		}
		if current.IsSub(base.Meta) {
			continue
		}
		if base.Meta.IsSub(current) {
			winner = base.Meta
			continue
		}
		return nil, newError("TypeError", "metaclass conflict: the metaclass of a derived class must be a subclass of the metaclasses of all its bases")
	}
	return winner, nil
}

func (vm *VM) objectNewBuiltin() *Builtin {
	if vm.metadata.objectConstructor == nil {
		constructor := bi("__new__", objectNew)
		constructor.NoBind = true
		constructor.owner = objectClass
		vm.metadata.objectConstructor = constructor
	}
	return vm.metadata.objectConstructor
}

func (vm *VM) primitiveNew(t *BuiltinType) *Builtin {
	if constructor := vm.metadata.constructors[t]; constructor != nil {
		return constructor
	}
	constructor := bi("__new__", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if len(args) == 0 {
			return nil, newError("TypeError", "__new__ requires a type argument")
		}
		if t.Name == "type" {
			if len(args) != 4 {
				return nil, newError("TypeError", "type.__new__() takes exactly 3 arguments")
			}
			meta, isClass := args[0].(*Class)
			if isClass {
				if meta.Prim == nil || meta.Prim.Name != "type" {
					return nil, newError("TypeError", "type.__new__() argument must be a subtype of type")
				}
			} else if args[0] != t {
				return nil, newError("TypeError", "type.__new__() argument must be a subtype of type")
			}
			return ctorTypeWithMeta(vm, args[1:], kw, meta)
		}
		cls, ok := args[0].(*Class)
		if !ok {
			if args[0] == t {
				if t.Name == "dict" || t.Name == "bytearray" {
					return primEmpty(t.Name), nil
				}
				return t.ctor(vm, args[1:], kw)
			}
			return nil, newError("TypeError", "__new__ requires a subtype")
		}
		if cls.Prim == nil || cls.Prim.Name != t.Name {
			return nil, newError("TypeError", "__new__ requires a subtype")
		}
		if t.Name == "dict" || t.Name == "bytearray" {
			return newInstance(cls, primEmpty(t.Name)), nil
		}
		value, err := t.ctor(vm, args[1:], kw)
		if err != nil {
			return nil, err
		}
		return newInstance(cls, value), nil
	})
	constructor.NoBind = true
	constructor.owner = t
	if vm.metadata.constructors == nil {
		vm.metadata.constructors = map[*BuiltinType]*Builtin{}
	}
	vm.metadata.constructors[t] = constructor
	return constructor
}

func objectNew(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if len(args) == 0 {
		return nil, newError("TypeError", "object.__new__() requires a type")
	}
	cls, ok := args[0].(*Class)
	if !ok {
		return nil, newError("TypeError", "object.__new__() argument must be a type")
	}
	if cls.Prim != nil {
		return nil, newError("TypeError", "object.__new__() is not safe for builtin subtypes")
	}
	return newInstance(cls, nil), nil
}
