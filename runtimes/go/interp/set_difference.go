package interp

func (vm *VM) setDifferenceUpdate(target *Set, source Value) error {
	if other, native := setValue(rawPrim(source)); native {
		if other == target {
			target.Clear()
			return nil
		}
		// CPython bounds work for a much larger source by first intersecting.
		// Both paths reuse entry hashes rather than invoking __hash__ again.
		if other.Len()>>3 > target.Len() {
			intersection, err := setOp("bitand", target, other)
			if err != nil {
				return err
			}
			other = intersection.(*Set)
		}
		for index := 0; index < len(other.entries); index++ {
			entry := other.entries[index]
			if err := vm.fuel(); err != nil {
				return err
			}
			if _, err := target.discardHK(entry.hk, entry.key); err != nil {
				return err
			}
		}
		return nil
	}
	iterator, err := vm.getIter(source)
	if err != nil {
		return err
	}
	for {
		item, found, err := iterator.Next()
		if err != nil || !found {
			return err
		}
		if err := vm.fuel(); err != nil {
			return err
		}
		// Unlike set.discard, difference_update does not implicitly freeze
		// mutable-set keys from an arbitrary iterable.
		hash, err := hashKey(item)
		if err != nil {
			return err
		}
		if _, err := target.discardHK(hash, item); err != nil {
			return err
		}
	}
}
