package interp

// builtinInplace applies inherited mutable-container slots before binary
// reflection. A primitive subclass keeps its identity when storage is mutated.
func (vm *VM) builtinInplace(kind string, left, right Value) (Value, bool, error) {
	primitive := rawPrim(left)
	switch target := primitive.(type) {
	case *List:
		if kind == "add" {
			if source, exact := right.(*List); exact {
				target.Items = append(target.Items, source.Items...)
				return left, true, nil
			}
			if isIdentical(left, right) {
				items, err := vm.iterToSlice(right)
				if err != nil {
					return nil, true, err
				}
				target.Items = append(target.Items, items...)
				return left, true, nil
			}
			iterator, err := vm.getIter(right)
			if err != nil {
				return nil, true, err
			}
			for {
				item, found, err := iterator.Next()
				if err != nil {
					return nil, true, err
				}
				if !found {
					return left, true, nil
				}
				if err := vm.fuel(); err != nil {
					return nil, true, err
				}
				target.Items = append(target.Items, item)
			}
		}
		if kind == "mul" {
			countValue, err := percentIndex(vm, right)
			if err != nil {
				return nil, true, err
			}
			count, ok := asInt(countValue)
			if !ok {
				return nil, true, newError("OverflowError", "cannot fit 'int' into an index-sized integer")
			}
			if count <= 0 {
				target.Items = nil
				return left, true, nil
			}
			length := len(target.Items)
			if count == 1 || length == 0 {
				return left, true, nil
			}
			if count > int64(int(^uint(0)>>1)/length) {
				return nil, true, newError("MemoryError", "repeated list is too long")
			}
			if err := vm.fuelN(int64(length)*count/16 + 1); err != nil {
				return nil, true, err
			}
			size := int(count) * length
			if cap(target.Items) >= size {
				target.Items = target.Items[:size]
			} else {
				items := make([]Value, size)
				copy(items, target.Items)
				target.Items = items
			}
			for copied := length; copied < size; {
				copied += copy(target.Items[copied:], target.Items[:copied])
			}
			return left, true, nil
		}
	case *Dict:
		if kind == "bitor" {
			return left, true, dictMergeFrom(vm, target, right)
		}
	}
	return nil, false, nil
}
