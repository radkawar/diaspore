package interp

// DictView is a live keys, values, or items view of a dictionary.
type DictView struct {
	dict  *Dict
	class *Class
	kind  string
}

func dictionaryViewType(name string) *BuiltinType {
	return &BuiltinType{Name: name, ctor: func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		return nil, newErrorf("TypeError", "cannot create '%s' instances", name)
	}}
}

func (v *DictView) source() *Dict {
	if v.class != nil {
		return (&classDict{cls: v.class}).snapshot()
	}
	return v.dict
}

func (v *DictView) length() int {
	if v.class != nil {
		return len(v.class.NS)
	}
	return v.dict.Len()
}

func (v *DictView) element(entry *dictEntry) Value {
	switch v.kind {
	case "keys":
		return entry.key
	case "values":
		return entry.val
	default:
		return &Tuple{Items: []Value{entry.key, entry.val}}
	}
}

func (v *DictView) elements() []Value {
	d := v.source()
	items := make([]Value, d.Len())
	for i := range d.entries {
		items[i] = v.element(&d.entries[i])
	}
	return items
}

func (v *DictView) iterator(reverse bool) *dictViewIter {
	d := v.source()
	index := 0
	if reverse {
		index = d.Len() - 1
	}
	return &dictViewIter{view: v, dict: d, size: d.Len(), index: index, reverse: reverse}
}

type dictViewIter struct {
	view      *DictView
	dict      *Dict
	size      int
	index     int
	reverse   bool
	exhausted bool
}

func (it *dictViewIter) Next() (Value, bool, error) {
	if it.exhausted {
		return nil, false, nil
	}
	if it.size != it.view.length() {
		return nil, false, newError("RuntimeError", "dictionary changed size during iteration")
	}
	if it.index < 0 || it.index >= it.dict.Len() {
		it.exhausted = true
		return nil, false, nil
	}
	value := it.view.element(&it.dict.entries[it.index])
	if it.reverse {
		it.index--
	} else {
		it.index++
	}
	return value, true, nil
}

func (it *dictViewIter) KindName() string {
	kind := "dict_"
	if it.reverse {
		kind += "reverse"
	}
	switch it.view.kind {
	case "keys":
		return kind + "keyiterator"
	case "values":
		return kind + "valueiterator"
	default:
		return kind + "itemiterator"
	}
}

func (vm *VM) viewContains(v *DictView, item Value) (bool, error) {
	d := v.source()
	switch v.kind {
	case "keys":
		_, found, err := d.Get(item)
		return found, err
	case "items":
		pair, ok := item.(*Tuple)
		if !ok || len(pair.Items) != 2 {
			return false, nil
		}
		value, found, err := d.Get(pair.Items[0])
		if err != nil || !found {
			return false, err
		}
		return vm.eqElem(value, pair.Items[1])
	default:
		for _, entry := range d.entries {
			equal, err := vm.eqElem(entry.val, item)
			if err != nil || equal {
				return equal, err
			}
		}
		return false, nil
	}
}

func viewSetLike(v Value) bool {
	if view, ok := v.(*DictView); ok {
		return view.kind != "values"
	}
	_, ok := setValue(v)
	return ok
}

func (vm *VM) viewSubset(left, right Value) (bool, error) {
	it, err := vm.getIter(left)
	if err != nil {
		return false, err
	}
	for {
		item, more, err := it.Next()
		if err != nil || !more {
			return !more, err
		}
		present, err := vm.contains(right, item)
		if err != nil || !present {
			return false, err
		}
	}
}

func (vm *VM) viewEqual(left, right Value) (bool, error) {
	if !viewSetLike(left) || !viewSetLike(right) {
		return isIdentical(left, right), nil
	}
	leftLen, err := vm.diasporeLen(left)
	if err != nil {
		return false, err
	}
	rightLen, err := vm.diasporeLen(right)
	if err != nil || leftLen != rightLen {
		return false, err
	}
	return vm.viewSubset(left, right)
}

func (vm *VM) viewOrder(left, right Value, kind string) (int, bool, error) {
	if !viewSetLike(left) || !viewSetLike(right) {
		return 0, false, vm.orderTypeError(kind, left, right)
	}
	sub, err := vm.viewSubset(left, right)
	if err != nil {
		return 0, false, err
	}
	sup, err := vm.viewSubset(right, left)
	if err != nil {
		return 0, false, err
	}
	switch {
	case sub && sup:
		return 0, true, nil
	case sub:
		return -1, true, nil
	case sup:
		return 1, true, nil
	}
	return 0, false, nil
}

func (vm *VM) viewBinop(kind string, left, right Value) (Value, error) {
	if kind == "bitand" {
		// Intersect via membership so unmatched unhashable item values are harmless.
		view, iterable := left, right
		if _, ok := view.(*DictView); !ok {
			view, iterable = right, left
		}
		it, err := vm.getIter(iterable)
		if err != nil {
			return nil, err
		}
		out := NewSet()
		for {
			item, more, err := it.Next()
			if err != nil {
				return nil, err
			}
			if !more {
				return out, nil
			}
			present, err := vm.contains(view, item)
			if err != nil {
				return nil, err
			}
			if present {
				if err := out.Add(item); err != nil {
					return nil, err
				}
			}
		}
	}
	l, err := vm.toSet(left)
	if err != nil {
		return nil, err
	}
	r, err := vm.toSet(right)
	if err != nil {
		return nil, err
	}
	return setOp(kind, l, r)
}

func dictViewMethod(view *DictView, name string) func(*VM, []Value, []KwArg) (Value, error) {
	if name != "isdisjoint" || view.kind == "values" {
		return nil
	}
	return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := argCount(name, args, 1, 1); err != nil {
			return nil, err
		}
		if err := noKw(name, kw); err != nil {
			return nil, err
		}
		it, err := vm.getIter(args[0])
		if err != nil {
			return nil, err
		}
		for {
			item, more, err := it.Next()
			if err != nil || !more {
				return !more, err
			}
			found, err := vm.viewContains(view, item)
			if err != nil || found {
				return false, err
			}
		}
	}
}
