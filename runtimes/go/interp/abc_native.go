package interp

import (
	"math/big"
	"weak"
)

// ABC registries must not extend user class lifetimes. Builtin types live for
// the VM's lifetime; weak.Pointer preserves identity for collectible classes.
type abcClassRef struct {
	class   weak.Pointer[Class]
	builtin *BuiltinType
}

type abcWeakSet map[abcClassRef]struct{}

func abcReference(value Value) (abcClassRef, bool) {
	switch cls := value.(type) {
	case *Class:
		return abcClassRef{class: weak.Make(cls)}, true
	case *BuiltinType:
		return abcClassRef{builtin: cls}, true
	default:
		return abcClassRef{}, false
	}
}

func (ref abcClassRef) value() Value {
	if ref.builtin != nil {
		return ref.builtin
	}
	if cls := ref.class.Value(); cls != nil {
		return cls
	}
	return nil
}

func (set abcWeakSet) contains(value Value) bool {
	ref, ok := abcReference(value)
	if !ok {
		return false
	}
	_, found := set[ref]
	return found
}

func (set *abcWeakSet) add(value Value) {
	ref, _ := abcReference(value)
	if *set == nil {
		*set = make(abcWeakSet)
	}
	(*set)[ref] = struct{}{}
}

// Callbacks can mutate registries. Hold a snapshot while invoking Python, as
// the C accelerator does, and prune dead references before exposing a dump.
func (set abcWeakSet) snapshot() []Value {
	values := make([]Value, 0, len(set))
	for ref := range set {
		if value := ref.value(); value != nil {
			values = append(values, value)
		} else {
			delete(set, ref)
		}
	}
	return values
}

type abcRuntime struct{ token int64 }

type abcData struct {
	owner                        *abcRuntime
	registry, positive, negative abcWeakSet
	version                      int64
}

func (vm *VM) makeABCModule() *NativeModule {
	state := &abcRuntime{}
	module := &NativeModule{Name: "_abc", NS: map[string]Value{}}
	dataType := vm.metadataType("_abc_data")
	dataType.ctor = func(_ *VM, _ []Value, _ []KwArg) (Value, error) {
		return &abcData{owner: state, version: state.token}, nil
	}
	for _, operation := range []struct {
		name  string
		arity int
		fn    func(*VM, []Value) (Value, error)
	}{
		{"_abc_init", 1, state.init},
		{"_abc_register", 2, state.register},
		{"_abc_instancecheck", 2, state.instancecheck},
		{"_abc_subclasscheck", 2, state.subclasscheck},
		{"_get_dump", 1, state.dump},
		{"_reset_registry", 1, state.resetRegistry},
		{"_reset_caches", 1, state.resetCaches},
		{"get_cache_token", 0, func(_ *VM, _ []Value) (Value, error) { return state.token, nil }},
	} {
		module.NS[operation.name] = bi(operation.name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(operation.name, kw); err != nil {
				return nil, err
			}
			if err := argCount(operation.name, args, operation.arity, operation.arity); err != nil {
				return nil, err
			}
			return operation.fn(vm, args)
		})
	}
	return module
}

func (state *abcRuntime) data(vm *VM, cls Value) (*abcData, error) {
	value, err := vm.getAttr(cls, "_abc_impl")
	if err != nil {
		return nil, err
	}
	data, ok := value.(*abcData)
	if !ok || data.owner != state {
		return nil, newError("TypeError", "_abc_impl is set to a wrong type")
	}
	return data, nil
}

func abcOptionalAttr(vm *VM, value Value, name string) (Value, error) {
	result, err := vm.getAttr(value, name)
	if _, absent := raisedAs(err, "AttributeError"); absent {
		return None, nil
	}
	return result, err
}

func abcAbstract(vm *VM, value Value) (bool, error) {
	flag, err := abcOptionalAttr(vm, value, "__isabstractmethod__")
	if err != nil {
		return false, err
	}
	return vm.truth(flag)
}

func (state *abcRuntime) init(vm *VM, args []Value) (Value, error) {
	cls := args[0]
	namespace, err := vm.getAttr(cls, "__dict__")
	if err != nil {
		return nil, err
	}
	itemsMethod, err := vm.getAttr(namespace, "items")
	if err != nil {
		return nil, err
	}
	items, err := vm.call(itemsMethod, nil, nil)
	if err != nil {
		return nil, err
	}
	entries, err := vm.iterToSlice(items)
	if err != nil {
		return nil, err
	}
	abstracts := NewSet()
	for _, entry := range entries {
		pair, err := vm.iterToSlice(entry)
		if err != nil {
			return nil, err
		}
		if len(pair) != 2 {
			return nil, newError("TypeError", "items() returned item which size is not 2")
		}
		abstract, err := abcAbstract(vm, pair[1])
		if err != nil {
			return nil, err
		}
		if abstract {
			if err := abstracts.Add(pair[0]); err != nil {
				return nil, err
			}
		}
	}
	basesValue, err := vm.getAttr(cls, "__bases__")
	if err != nil {
		return nil, err
	}
	bases, ok := rawPrim(basesValue).(*Tuple)
	if !ok {
		return nil, newError("TypeError", "__bases__ is not tuple")
	}
	for _, base := range bases.Items {
		names, err := vm.getAttr(base, "__abstractmethods__")
		if _, absent := raisedAs(err, "AttributeError"); absent {
			continue
		}
		if err != nil {
			return nil, err
		}
		iterator, err := vm.getIter(names)
		if err != nil {
			return nil, err
		}
		for {
			name, more, err := iterator.Next()
			if err != nil {
				return nil, err
			}
			if !more {
				break
			}
			text, ok := rawPrim(name).(string)
			if !ok {
				return nil, newError("TypeError", "attribute name must be string")
			}
			value, err := abcOptionalAttr(vm, cls, text)
			if err != nil {
				return nil, err
			}
			abstract, err := abcAbstract(vm, value)
			if err != nil {
				return nil, err
			}
			if abstract {
				if err := abstracts.Add(name); err != nil {
					return nil, err
				}
			}
		}
	}
	if err := vm.setAttr(cls, "__abstractmethods__", &FrozenSet{set: abstracts}); err != nil {
		return nil, err
	}
	if err := vm.setAttr(cls, "_abc_impl", &abcData{owner: state, version: state.token}); err != nil {
		return nil, err
	}
	if user, ok := cls.(*Class); ok {
		if value, present := user.NS["__abc_tpflags__"]; present {
			var flags int64
			switch flag := value.(type) {
			case int64:
				flags = flag
			case *big.Int:
				if !flag.IsInt64() {
					return nil, newError("OverflowError", "Python int too large to convert to C long")
				}
				flags = flag.Int64()
			}
			if flags&96 == 96 {
				return nil, newError("TypeError", "__abc_tpflags__ cannot be both Py_TPFLAGS_SEQUENCE and Py_TPFLAGS_MAPPING")
			}
			user.abcFlags |= flags & 96
			delete(user.NS, "__abc_tpflags__")
		}
	}
	return None, nil
}

func (state *abcRuntime) register(vm *VM, args []Value) (Value, error) {
	cls, subclass := args[0], args[1]
	if _, ok := abcReference(subclass); !ok {
		return nil, newError("TypeError", "Can only register classes")
	}
	result, err := biIssubclass(vm, []Value{subclass, cls}, nil)
	if err != nil {
		return nil, err
	}
	if result == true {
		return subclass, nil
	}
	result, err = biIssubclass(vm, []Value{cls, subclass}, nil)
	if err != nil {
		return nil, err
	}
	if result == true {
		return nil, newError("RuntimeError", "Refusing to create an inheritance cycle")
	}
	data, err := state.data(vm, cls)
	if err != nil {
		return nil, err
	}
	data.registry.add(subclass)
	state.token++
	if base, ok := cls.(*Class); ok && base.abcFlags != 0 {
		if child, ok := subclass.(*Class); ok {
			abcSetFlags(child, base.abcFlags)
		}
	}
	return subclass, nil
}

func abcSetFlags(cls *Class, flags int64) {
	if cls == objectClass || excClasses[cls.Name] == cls || cls.abcFlags == flags {
		return
	}
	cls.abcFlags = flags
	for _, child := range cls.subclasses {
		abcSetFlags(child, flags)
	}
}

func (state *abcRuntime) instancecheck(vm *VM, args []Value) (Value, error) {
	cls, instance := args[0], args[1]
	data, err := state.data(vm, cls)
	if err != nil {
		return nil, err
	}
	subclass, err := vm.getAttr(instance, "__class__")
	if err != nil {
		return nil, err
	}
	if data.positive.contains(subclass) {
		return true, nil
	}
	subtype, err := ctorType(vm, []Value{instance}, nil)
	if err != nil {
		return nil, err
	}
	same := subtype == subclass
	if same && data.version == state.token && data.negative.contains(subclass) {
		return false, nil
	}
	check, err := vm.getAttr(cls, "__subclasscheck__")
	if err != nil {
		return nil, err
	}
	// Do not insert an interpreted wrapper here: typing inspects the caller's
	// sandbox frame to distinguish ABC-internal checks from user issubclass.
	result, err := vm.call(check, []Value{subclass}, nil)
	if err != nil || same {
		return result, err
	}
	truth, err := vm.truth(result)
	if err != nil || truth {
		return result, err
	}
	return vm.call(check, []Value{subtype}, nil)
}

func (state *abcRuntime) subclasscheck(vm *VM, args []Value) (Value, error) {
	cls, subclass := args[0], args[1]
	if _, ok := abcReference(subclass); !ok {
		return nil, newError("TypeError", "issubclass() arg 1 must be a class")
	}
	data, err := state.data(vm, cls)
	if err != nil {
		return nil, err
	}
	if data.positive.contains(subclass) {
		return true, nil
	}
	if data.version < state.token {
		clear(data.negative)
		data.version = state.token
	} else if data.negative.contains(subclass) {
		return false, nil
	}
	hook, err := vm.getAttr(cls, "__subclasshook__")
	if err != nil {
		return nil, err
	}
	result, err := vm.call(hook, []Value{subclass}, nil)
	if err != nil {
		return nil, err
	}
	if truth, ok := result.(bool); ok {
		if truth {
			data.positive.add(subclass)
		} else {
			data.negative.add(subclass)
		}
		return truth, nil
	}
	if _, declined := result.(NotImplementedType); !declined {
		return nil, newError("AssertionError", "__subclasshook__ must return either False, True, or NotImplemented")
	}
	nominal := subclass == cls || cls == objectClass
	if candidate, ok := subclass.(*Class); ok {
		for _, base := range candidate.MRO {
			if base == cls {
				nominal = true
				break
			}
		}
		if candidate.Prim != nil && candidate.Prim == cls {
			nominal = true
		}
	}
	if nominal {
		data.positive.add(subclass)
		return true, nil
	}
	// The direct registry shortcut deliberately does not fill the positive
	// cache, matching _abc's observable cache dump and registry-reset behavior.
	if data.registry.contains(subclass) {
		return true, nil
	}
	for _, registered := range data.registry.snapshot() {
		result, err := biIssubclass(vm, []Value{subclass, registered}, nil)
		if err != nil {
			return nil, err
		}
		if result == true {
			data.positive.add(subclass)
			return true, nil
		}
	}
	childrenMethod, err := vm.getAttr(cls, "__subclasses__")
	if err != nil {
		return nil, err
	}
	childrenValue, err := vm.call(childrenMethod, nil, nil)
	if err != nil {
		return nil, err
	}
	children, ok := rawPrim(childrenValue).(*List)
	if !ok {
		return nil, newError("TypeError", "__subclasses__() must return a list")
	}
	for _, child := range children.Items {
		result, err := biIssubclass(vm, []Value{subclass, child}, nil)
		if err != nil {
			return nil, err
		}
		if result == true {
			data.positive.add(subclass)
			return true, nil
		}
	}
	data.negative.add(subclass)
	return false, nil
}

func (state *abcRuntime) dump(vm *VM, args []Value) (Value, error) {
	data, err := state.data(vm, args[0])
	if err != nil {
		return nil, err
	}
	module, err := vm.importModule("_weakref")
	if err != nil {
		return nil, err
	}
	ref, err := vm.getAttr(module, "ref")
	if err != nil {
		return nil, err
	}
	result := &Tuple{Items: make([]Value, 4)}
	for i, cache := range []abcWeakSet{data.registry, data.positive, data.negative} {
		set := NewSet()
		for _, cls := range cache.snapshot() {
			value, err := vm.call(ref, []Value{cls}, nil)
			if err != nil {
				return nil, err
			}
			if err := set.Add(value); err != nil {
				return nil, err
			}
		}
		result.Items[i] = set
	}
	result.Items[3] = data.version
	return result, nil
}

func (state *abcRuntime) resetRegistry(vm *VM, args []Value) (Value, error) {
	data, err := state.data(vm, args[0])
	if err != nil {
		return nil, err
	}
	clear(data.registry)
	return None, nil
}

func (state *abcRuntime) resetCaches(vm *VM, args []Value) (Value, error) {
	data, err := state.data(vm, args[0])
	if err != nil {
		return nil, err
	}
	clear(data.positive)
	clear(data.negative)
	return None, nil
}
