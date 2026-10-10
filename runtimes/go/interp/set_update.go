package interp

import "slices"

func (vm *VM) setUpdate(target *Set, source Value) error {
	if other, native := setValue(rawPrim(source)); native {
		if other == target || other.Len() == 0 {
			return nil
		}
		if target.Len() == 0 {
			// Source entries are already unique. A clean native-set merge must
			// not call guest __hash__ or compare colliding source keys again.
			if err := vm.fuelN(int64(other.Len())); err != nil {
				return err
			}
			target.entries = append(target.entries[:0], other.entries...)
			if target.index == nil {
				target.index = make(map[string]int, len(other.index))
			} else {
				clear(target.index)
			}
			for hash, index := range other.index {
				target.index[hash] = index
			}
			target.slowKeys = other.slowKeys
			return nil
		}
		target.entries = slices.Grow(target.entries, other.Len())
		for index := 0; index < len(other.entries); index++ {
			entry := other.entries[index]
			if err := vm.fuel(); err != nil {
				return err
			}
			if err := target.addHK(entry.hk, entry.key); err != nil {
				return err
			}
		}
		return nil
	}
	if dictionary, exact := source.(*Dict); exact {
		target.entries = slices.Grow(target.entries, dictionary.Len())
		for index := 0; index < len(dictionary.entries); index++ {
			entry := dictionary.entries[index]
			if err := vm.fuel(); err != nil {
				return err
			}
			hash := entry.hk
			if entry.str {
				hash = "s" + hash
			}
			if err := target.addHK(hash, entry.key); err != nil {
				return err
			}
		}
		return nil
	}
	// Dict subclasses and other iterables use their actual iterator. Apply
	// each item immediately, retaining successful insertions if iteration or
	// a later hash/equality callback fails.
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
		if err := target.Add(item); err != nil {
			return err
		}
	}
}
