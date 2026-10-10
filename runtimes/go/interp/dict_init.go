package interp

func dictInit(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("dict.__init__", args, 1, 2); err != nil {
		return nil, err
	}
	target := rawPrim(args[0])
	switch target.(type) {
	case *Dict, *metadataNamespace:
	default:
		return nil, newError("TypeError", "descriptor '__init__' requires a 'dict' object")
	}
	return None, dictInitialize(vm, target, args[1:], kw)
}

func dictInitialize(vm *VM, target Value, args []Value, kw []KwArg) error {
	if err := argCount("dict", args, 0, 1); err != nil {
		return err
	}
	if len(args) == 1 {
		if err := dictMergeFrom(vm, target, args[0]); err != nil {
			return err
		}
	}
	for _, item := range kw {
		if err := vm.setIndex(target, item.Name, item.Val); err != nil {
			return err
		}
	}
	return nil
}

// Dictionary construction, update and initialization share the same mapping
// protocol. Apply each entry immediately so callbacks observe prior writes and
// an iteration error preserves the already-applied prefix.
func dictMergeFrom(vm *VM, target, source Value) error {
	if source == target {
		return nil
	}
	if sourceDict, exact := source.(*Dict); exact {
		for _, item := range sourceDict.entries {
			if err := vm.fuel(); err != nil {
				return err
			}
			if err := vm.setIndex(target, item.key, item.val); err != nil {
				return err
			}
		}
		return nil
	}
	keys, err := vm.getAttr(source, "keys")
	if err == nil {
		keyIterable, err := vm.call(keys, nil, nil)
		if err != nil {
			return err
		}
		// CPython freezes mapping keys before fetching any values.
		items, err := vm.iterToSlice(keyIterable)
		if err != nil {
			return err
		}
		for _, key := range items {
			if err := vm.fuel(); err != nil {
				return err
			}
			value, err := vm.getIndex(source, key)
			if err != nil {
				return err
			}
			if err := vm.setIndex(target, key, value); err != nil {
				return err
			}
		}
		return nil
	}
	if _, missing := raisedAs(err, "AttributeError"); !missing {
		return err
	}
	iterator, err := vm.getIter(source)
	if err != nil {
		return err
	}
	for index := 0; ; index++ {
		item, found, err := iterator.Next()
		if err != nil || !found {
			return err
		}
		if err := vm.fuel(); err != nil {
			return err
		}
		pair, err := vm.iterToSlice(item)
		if err != nil {
			if _, wrongType := raisedAs(err, "TypeError"); wrongType {
				return newErrorf("TypeError", "cannot convert dictionary update sequence element #%d to a sequence", index)
			}
			return err
		}
		if len(pair) != 2 {
			return newErrorf("ValueError", "dictionary update sequence element #%d has length %d; 2 is required", index, len(pair))
		}
		if err := vm.setIndex(target, pair[0], pair[1]); err != nil {
			return err
		}
	}
}
