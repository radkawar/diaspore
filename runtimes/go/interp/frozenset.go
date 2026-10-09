package interp

// FrozenSet is an immutable, hashable Diaspore set.
// Its elements are accessible through the iteration protocol.
type FrozenSet struct {
	set    *Set
	hash   int64
	hashed bool
}

func ctorFrozenSet(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("frozenset", args, 0, 1); err != nil {
		return nil, err
	}
	if err := noKw("frozenset", kw); err != nil {
		return nil, err
	}
	if len(args) == 0 {
		return &FrozenSet{set: NewSet()}, nil
	}
	if frozen, ok := args[0].(*FrozenSet); ok {
		return frozen, nil
	}
	value := rawPrim(args[0])
	if err := vm.chargeSized(value); err != nil {
		return nil, err
	}
	if set, ok := setValue(value); ok {
		return &FrozenSet{set: set.Copy()}, nil
	}
	set, err := vm.toSet(value)
	if err != nil {
		return nil, err
	}
	return &FrozenSet{set: set}, nil
}

func setValue(v Value) (*Set, bool) {
	switch t := v.(type) {
	case *Set:
		return t, true
	case *FrozenSet:
		return t.set, true
	}
	return nil, false
}

func frozenHash(f *FrozenSet) int64 {
	if f.hashed {
		return f.hash
	}
	s := f.set
	var hash uint64
	for _, entry := range s.entries {
		h := uint64(encodedHash(entry.key, entry.hk))
		hash ^= ((h ^ 89869747) ^ (h << 16)) * 3644798167
	}
	hash ^= uint64(s.Len()+1) * 1927868237
	hash ^= (hash >> 11) ^ (hash >> 25)
	hash = hash*69069 + 907133923
	if hash == ^uint64(0) {
		hash = 590923713
	}
	f.hash, f.hashed = int64(hash), true
	return f.hash
}

func frozenSetMethod(s *FrozenSet, name string) func(*VM, []Value, []KwArg) (Value, error) {
	switch name {
	case "copy":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount(name, args, 0, 0); err != nil {
				return nil, err
			}
			return s, noKw(name, kw)
		}
	case "union", "intersection", "difference", "symmetric_difference", "issubset", "issuperset", "isdisjoint":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if name == "symmetric_difference" {
				if err := argCount(name, args, 1, 1); err != nil {
					return nil, err
				}
			}
			result, err := setMethod(s.set, name)(vm, args, kw)
			if err != nil {
				return nil, err
			}
			if set, ok := result.(*Set); ok {
				return &FrozenSet{set: set}, nil
			}
			return result, nil
		}
	}
	return nil
}
