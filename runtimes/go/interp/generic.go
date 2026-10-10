package interp

import "strings"

// GenericAlias preserves the origin and parameters of a runtime generic type.
type GenericAlias struct {
	Origin     Value
	Args       *Tuple
	Unpacked   bool
	parameters *Tuple
}

// UnionType retains first-occurrence order for repr while comparing as a set.
type UnionType struct {
	Args       *Tuple
	parameters *Tuple
}

func (vm *VM) genericType(v Value) bool {
	switch rawPrim(v).(type) {
	case *Class, *BuiltinType, *GenericAlias, *UnionType, NoneType:
		return true
	}
	inst, ok := v.(*Instance)
	if !ok || vm == nil || vm.moduleDict == nil {
		return false
	}
	module, found, err := vm.moduleDict.Get("_typing")
	if err != nil || !found {
		return false
	}
	aliasType, err := vm.getAttr(module, "TypeAliasType")
	return err == nil && aliasType == inst.Class
}

// biTypingUnion is the portable _typing provider's native union operation.
func biTypingUnion(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("_typing_union", kw); err != nil {
		return nil, err
	}
	if err := argCount("_typing_union", args, 2, 2); err != nil {
		return nil, err
	}
	if !vm.genericType(args[0]) || !vm.genericType(args[1]) {
		return NotImplemented, nil
	}
	return vm.makeUnion(args[0], args[1])
}

// Honor user metaclass and GenericAlias-subclass operators before native union
// construction, including reflected-subclass precedence.
func (vm *VM) genericUnionBinop(left, right Value) (Value, error) {
	operatorClass := func(value Value) *Class {
		switch value := value.(type) {
		case *Class:
			return value.Meta
		case *Instance:
			return value.Class
		}
		return nil
	}
	leftClass, rightClass := operatorClass(left), operatorClass(right)
	var forward, reverse, leftReverse Value
	if leftClass != nil {
		forward, _ = leftClass.Lookup("__or__")
		leftReverse, _ = leftClass.Lookup("__ror__")
	}
	if rightClass != nil {
		reverse, _ = rightClass.Lookup("__ror__")
	}
	rightFirst := reverse != nil && rightClass != leftClass &&
		(leftClass == nil || rightClass.IsSub(leftClass)) &&
		!isIdentical(reverse, leftReverse)
	if rightFirst {
		value, err := vm.call(vm.bindAttr(reverse, right, rightClass), []Value{left}, nil)
		if _, declined := value.(NotImplementedType); err != nil || !declined {
			return value, err
		}
	}
	if forward != nil {
		value, err := vm.call(vm.bindAttr(forward, left, leftClass), []Value{right}, nil)
		if _, declined := value.(NotImplementedType); err != nil || !declined {
			return value, err
		}
	} else if _, none := left.(NoneType); !none {
		return vm.makeUnion(left, right)
	}
	if !rightFirst && (leftClass != rightClass || leftClass == nil) {
		if reverse != nil {
			value, err := vm.call(vm.bindAttr(reverse, right, rightClass), []Value{left}, nil)
			if _, declined := value.(NotImplementedType); err != nil || !declined {
				return value, err
			}
		} else if _, none := right.(NoneType); !none {
			return vm.makeUnion(left, right)
		}
	}
	return nil, vm.binopTypeError("bitor", left, right)
}

func (vm *VM) makeUnion(left, right Value) (Value, error) {
	items := make([]Value, 0, 2)
	var add func(Value) error
	add = func(v Value) error {
		if _, none := v.(NoneType); none {
			v = vm.types["NoneType"]
		}
		if union, ok := v.(*UnionType); ok {
			for _, item := range union.Args.Items {
				if err := add(item); err != nil {
					return err
				}
			}
			return nil
		}
		for _, item := range items {
			same, err := vm.eqElem(item, v)
			if err != nil {
				return err
			}
			if same {
				return nil
			}
		}
		items = append(items, v)
		return nil
	}
	if err := add(left); err != nil {
		return nil, err
	}
	if err := add(right); err != nil {
		return nil, err
	}
	if len(items) == 1 {
		return items[0], nil
	}
	return &UnionType{Args: &Tuple{Items: items}}, nil
}

func genericIdentityIndex(items []Value, target Value) int {
	for i, item := range items {
		if isIdentical(item, target) {
			return i
		}
	}
	return -1
}

// A type's descriptors are not typing parameters. Only an absent attribute is
// ignored; property failures and errors raised by parameter hooks propagate.
func (vm *VM) typingAttr(o Value, name string) (Value, bool, error) {
	switch o.(type) {
	case *Class, *BuiltinType:
		return nil, false, nil
	}
	value, err := vm.getAttr(o, name)
	if err != nil {
		if _, absent := raisedAs(err, "AttributeError"); absent {
			return nil, false, nil
		}
		return nil, false, err
	}
	return value, true, nil
}

func (vm *VM) genericParameters(o Value) (*Tuple, error) {
	var args *Tuple
	var cache **Tuple
	switch value := o.(type) {
	case *GenericAlias:
		args, cache = value.Args, &value.parameters
	case *UnionType:
		args, cache = value.Args, &value.parameters
	}
	if *cache != nil {
		return *cache, nil
	}
	var parameters []Value
	add := func(p Value) {
		if genericIdentityIndex(parameters, p) < 0 {
			parameters = append(parameters, p)
		}
	}
	for _, arg := range args.Items {
		if _, present, err := vm.typingAttr(arg, "__typing_subst__"); err != nil {
			return nil, err
		} else if present {
			add(arg)
			continue
		}
		nested, _, err := vm.typingAttr(arg, "__parameters__")
		if err != nil {
			return nil, err
		}
		if tuple, ok := rawPrim(nested).(*Tuple); ok {
			for _, p := range tuple.Items {
				add(p)
			}
		}
	}
	*cache = &Tuple{Items: parameters}
	return *cache, nil
}

func (vm *VM) unpackTypingArguments(key Value) (*Tuple, error) {
	items := []Value{key}
	if tuple, ok := rawPrim(key).(*Tuple); ok {
		items = tuple.Items
	}
	var unpacked []Value
	for _, item := range items {
		value, _, err := vm.typingAttr(item, "__typing_unpacked_tuple_args__")
		if err != nil {
			return nil, err
		}
		if tuple, ok := rawPrim(value).(*Tuple); ok {
			n := len(tuple.Items)
			if n == 0 || !isIdentical(tuple.Items[n-1], Ellipsis) {
				unpacked = append(unpacked, tuple.Items...)
				continue
			}
		}
		unpacked = append(unpacked, item)
	}
	return &Tuple{Items: unpacked}, nil
}

func (vm *VM) substituteGenericArg(arg Value, parameters, replacements *Tuple) (Value, error) {
	subst, present, err := vm.typingAttr(arg, "__typing_subst__")
	if err != nil {
		return nil, err
	}
	if present {
		index := genericIdentityIndex(parameters.Items, arg)
		if index < 0 {
			return nil, newError("TypeError", "typing parameters changed during substitution")
		}
		return vm.call(subst, []Value{replacements.Items[index]}, nil)
	}
	nested, _, err := vm.typingAttr(arg, "__parameters__")
	if err != nil {
		return nil, err
	}
	subparams, ok := rawPrim(nested).(*Tuple)
	if !ok || len(subparams.Items) == 0 {
		return arg, nil
	}
	subargs := make([]Value, 0, len(subparams.Items))
	for _, parameter := range subparams.Items {
		replacement := parameter
		if index := genericIdentityIndex(parameters.Items, parameter); index >= 0 {
			replacement = replacements.Items[index]
			// TypeVarTuple exposes iteration, unlike TypeVar and ParamSpec.
			if inst, ok := parameter.(*Instance); ok {
				if _, iterable := inst.Class.Lookup("__iter__"); iterable {
					if tuple, ok := rawPrim(replacement).(*Tuple); ok {
						subargs = append(subargs, tuple.Items...)
						continue
					}
				}
			}
		}
		subargs = append(subargs, replacement)
	}
	return vm.getIndex(arg, &Tuple{Items: subargs})
}

func (vm *VM) genericSubscript(o, key Value) (Value, error) {
	parameters, err := vm.genericParameters(o)
	if err != nil {
		return nil, err
	}
	if len(parameters.Items) == 0 {
		text, err := vm.genericRepr(o)
		if err != nil {
			return nil, err
		}
		return nil, newErrorf("TypeError", "%s is not a generic class", text)
	}
	initial, err := vm.unpackTypingArguments(key)
	if err != nil {
		return nil, err
	}
	var prepared Value = initial
	for _, parameter := range parameters.Items {
		hook, present, err := vm.typingAttr(parameter, "__typing_prepare_subst__")
		if err != nil {
			return nil, err
		}
		if _, none := hook.(NoneType); !present || none {
			continue
		}
		if _, tuple := rawPrim(prepared).(*Tuple); !tuple {
			prepared = &Tuple{Items: []Value{prepared}}
		}
		prepared, err = vm.call(hook, []Value{o, prepared}, nil)
		if err != nil {
			return nil, err
		}
	}
	replacements, ok := rawPrim(prepared).(*Tuple)
	if !ok {
		replacements = &Tuple{Items: []Value{prepared}}
	}
	if len(replacements.Items) != len(parameters.Items) {
		word := "few"
		if len(replacements.Items) > len(parameters.Items) {
			word = "many"
		}
		text, err := vm.genericRepr(o)
		if err != nil {
			return nil, err
		}
		return nil, newErrorf("TypeError", "Too %s arguments for %s; actual %d, expected %d", word, text, len(replacements.Items), len(parameters.Items))
	}
	var args *Tuple
	switch value := o.(type) {
	case *GenericAlias:
		args = value.Args
	case *UnionType:
		args = value.Args
	}
	result := make([]Value, 0, len(args.Items))
	for _, arg := range args.Items {
		unpackValue, _, err := vm.typingAttr(arg, "__typing_is_unpacked_typevartuple__")
		if err != nil {
			return nil, err
		}
		unpack := false
		if unpackValue != nil {
			unpack, err = vm.truth(unpackValue)
			if err != nil {
				return nil, err
			}
		}
		replacement, err := vm.substituteGenericArg(arg, parameters, replacements)
		if err != nil {
			return nil, err
		}
		if unpack {
			tuple, ok := rawPrim(replacement).(*Tuple)
			if !ok {
				return nil, newError("TypeError", "unpacked type variable substitution must return a tuple")
			}
			result = append(result, tuple.Items...)
		} else {
			result = append(result, replacement)
		}
	}
	if value, ok := o.(*GenericAlias); ok {
		return &GenericAlias{Origin: value.Origin, Args: &Tuple{Items: result}, Unpacked: value.Unpacked}, nil
	}
	var union Value = result[0]
	for _, next := range result[1:] {
		union, err = vm.binop("bitor", union, next)
		if err != nil {
			return nil, err
		}
	}
	return union, nil
}

func genericHash(o Value) (int64, error) {
	switch value := o.(type) {
	case *GenericAlias:
		origin, err := hashValue(value.Origin)
		if err != nil {
			return 0, err
		}
		args, err := hashValue(value.Args)
		if err != nil {
			return 0, err
		}
		hash := origin ^ args
		if hash == -1 {
			hash = -2
		}
		return hash, nil
	case *UnionType:
		// Match frozenHash without constructing an intermediate set.
		var hash uint64
		for _, arg := range value.Args.Items {
			h, err := hashValue(arg)
			if err != nil {
				return 0, err
			}
			part := uint64(h)
			hash ^= ((part ^ 89869747) ^ (part << 16)) * 3644798167
		}
		hash ^= uint64(len(value.Args.Items)+1) * 1927868237
		hash ^= (hash >> 11) ^ (hash >> 25)
		hash = hash*69069 + 907133923
		if hash == ^uint64(0) {
			hash = 590923713
		}
		return int64(hash), nil
	}
	panic("genericHash called with a non-generic value")
}

func (vm *VM) unionEqual(left, right *UnionType) (bool, error) {
	// Union equality has frozenset semantics, including hashability errors.
	var small [16]int64
	n, m := len(left.Args.Items), len(right.Args.Items)
	hashes := small[:]
	if n+m > len(small) {
		hashes = make([]int64, n+m)
	}
	for i, arg := range left.Args.Items {
		hash, err := hashValue(arg)
		if err != nil {
			return false, err
		}
		hashes[i] = hash
	}
	for i, arg := range right.Args.Items {
		hash, err := hashValue(arg)
		if err != nil {
			return false, err
		}
		hashes[n+i] = hash
	}
	if n != m {
		return false, nil
	}
	for i, arg := range left.Args.Items {
		found := false
		for j, other := range right.Args.Items {
			if hashes[i] != hashes[n+j] {
				continue
			}
			same, err := vm.eqElem(arg, other)
			if err != nil {
				return false, err
			}
			if same {
				found = true
				break
			}
		}
		if !found {
			return false, nil
		}
	}
	return true, nil
}

func (vm *VM) callGenericAlias(value *GenericAlias, args []Value, kw []KwArg) (Value, error) {
	instance, err := vm.call(value.Origin, args, kw)
	if err != nil {
		return nil, err
	}
	if err := vm.setAttr(instance, "__orig_class__", value); err != nil {
		_, attribute := raisedAs(err, "AttributeError")
		_, typeError := raisedAs(err, "TypeError")
		if !attribute && !typeError {
			return nil, err
		}
	}
	return instance, nil
}

func genericIterator(value *GenericAlias) Iterator {
	unpacked := &GenericAlias{Origin: value.Origin, Args: value.Args, Unpacked: true}
	return &seqIter{items: []Value{unpacked}}
}
func alias(origin, key Value) *GenericAlias {
	args, ok := key.(*Tuple)
	if !ok {
		args = &Tuple{Items: []Value{key}}
	}
	return &GenericAlias{Origin: origin, Args: args}
}
func (vm *VM) genericAttr(o Value, n string) (Value, error) {
	var args *Tuple
	switch value := o.(type) {
	case *GenericAlias:
		args = value.Args
		switch n {
		case "__origin__":
			return value.Origin, nil
		case "__unpacked__":
			return value.Unpacked, nil
		case "__typing_unpacked_tuple_args__":
			if value.Unpacked && value.Origin == vm.types["tuple"] {
				return value.Args, nil
			}
			return None, nil
		case "__mro_entries__":
			return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
				if err := noKw(n, k); err != nil {
					return nil, err
				}
				if err := argCount(n, a, 1, 1); err != nil {
					return nil, err
				}
				return &Tuple{Items: []Value{value.Origin}}, nil
			}), nil
		case "__call__":
			return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) { return vm.callGenericAlias(value, a, k) }), nil
		case "__iter__":
			return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
				if err := noKw(n, k); err != nil {
					return nil, err
				}
				if err := argCount(n, a, 0, 0); err != nil {
					return nil, err
				}
				return genericIterator(value), nil
			}), nil
		}
	case *UnionType:
		args = value.Args
	}
	switch n {
	case "__args__":
		return args, nil
	case "__parameters__":
		return vm.genericParameters(o)
	case "__getitem__", "__or__", "__ror__", "__eq__", "__ne__", "__instancecheck__", "__subclasscheck__":
		return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
			if err := noKw(n, k); err != nil {
				return nil, err
			}
			if err := argCount(n, a, 1, 1); err != nil {
				return nil, err
			}
			switch n {
			case "__getitem__":
				return vm.genericSubscript(o, a[0])
			case "__or__", "__ror__":
				if !vm.genericType(a[0]) {
					return NotImplemented, nil
				}
				if n == "__ror__" {
					return vm.makeUnion(a[0], o)
				}
				return vm.makeUnion(o, a[0])
			case "__instancecheck__":
				if _, alias := o.(*GenericAlias); alias {
					return nil, newError("TypeError", "isinstance() argument 2 cannot be a parameterized generic")
				}
				return biIsinstance(vm, []Value{a[0], o}, nil)
			case "__subclasscheck__":
				if _, alias := o.(*GenericAlias); alias {
					return nil, newError("TypeError", "issubclass() argument 2 cannot be a parameterized generic")
				}
				return biIssubclass(vm, []Value{a[0], o}, nil)
			default:
				other := rawPrim(a[0])
				switch o.(type) {
				case *GenericAlias:
					if _, ok := other.(*GenericAlias); !ok {
						return NotImplemented, nil
					}
				case *UnionType:
					if _, ok := other.(*UnionType); !ok {
						return NotImplemented, nil
					}
				}
				same, err := vm.eqBuiltin(o, other)
				if n == "__ne__" {
					same = !same
				}
				return same, err
			}
		}), nil
	case "__hash__":
		return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
			if err := noKw(n, k); err != nil {
				return nil, err
			}
			if err := argCount(n, a, 0, 0); err != nil {
				return nil, err
			}
			return genericHash(o)
		}), nil
	}
	if value, ok := o.(*GenericAlias); ok {
		return vm.getAttr(value.Origin, n)
	}
	return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'", typeName(o), n)
}
func (vm *VM) genericRepr(o Value) (string, error) {
	var args *Tuple
	prefix := ""
	separator := " | "
	switch value := o.(type) {
	case *GenericAlias:
		args = value.Args
		separator = ", "
		if value.Unpacked {
			prefix = "*"
		}
		switch origin := value.Origin.(type) {
		case *BuiltinType:
			prefix += origin.Name
		case *Class:
			prefix += origin.Name
		default:
			s, err := vm.repr(origin)
			if err != nil {
				return "", err
			}
			prefix += s
		}
	case *UnionType:
		args = value.Args
	}
	parts := make([]string, len(args.Items))
	for i, arg := range args.Items {
		switch t := arg.(type) {
		case *BuiltinType:
			parts[i] = t.Name
			if _, union := o.(*UnionType); union && t.Name == "NoneType" {
				parts[i] = "None"
			}
		case *Class:
			parts[i] = t.Name
		case NoneType:
			parts[i] = "None"
		default:
			s, err := vm.repr(arg)
			if err != nil {
				return "", err
			}
			parts[i] = s
		}
	}
	text := strings.Join(parts, separator)
	if len(args.Items) == 0 {
		text = "()"
	}
	if _, ok := o.(*GenericAlias); ok {
		return prefix + "[" + text + "]", nil
	}
	return text, nil
}
