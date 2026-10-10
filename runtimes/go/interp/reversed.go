package interp

func reverseNotSupported(value Value) error {
	return newErrorf("TypeError", "'%s' object is not reversible", typeName(value))
}

func reversedValue(vm *VM, value Value) (Value, error) {
	if instance, ok := value.(*Instance); ok {
		// Special lookup binds the class descriptor without consulting instance
		// attributes or __getattribute__, just like constructor-argument hooks.
		method, found, err := objectReductionSpecial(vm, instance, "__reversed__")
		if err != nil {
			return nil, err
		}
		if found {
			if _, disabled := method.(NoneType); disabled {
				return nil, reverseNotSupported(value)
			}
			return vm.call(method, nil, nil)
		}
		switch primitive := instance.Prim.(type) {
		case *List, *Dict, *Range:
			// These builtin types supply __reversed__, which reads builtin
			// storage rather than a subclass's __getitem__ override.
			return reversedValue(vm, primitive)
		}
		if _, indexed := instance.Class.Lookup("__getitem__"); !indexed {
			switch instance.Prim.(type) {
			case string, *Tuple, []byte, *ByteArray, *MemoryView:
			default:
				return nil, reverseNotSupported(value)
			}
		}
		length, err := vm.diasporeLen(value)
		if err != nil {
			return nil, err
		}
		if length < 0 {
			return nil, newError("ValueError", "__len__() should return >= 0")
		}
		return &reverseSequenceIterator{vm: vm, sequence: value, index: length - 1, kind: "reversed"}, nil
	}
	if cls, ok := value.(*Class); ok && cls.Meta != nil {
		if method, found := cls.Meta.Lookup("__reversed__"); found {
			if _, disabled := method.(NoneType); disabled {
				return nil, reverseNotSupported(value)
			}
			var bound Value
			var err error
			if _, property := propertyValue(method); property {
				bound, err = vm.propertyGet(method, cls, cls.Meta)
			} else if callableDescriptorKind(method) != "" {
				bound, err = vm.callableDescriptorGet(method, cls, cls.Meta)
			} else if descriptor, ok := method.(*Instance); ok {
				if _, gets := descriptor.Class.Lookup("__get__"); gets {
					bound, err = vm.callDunder(descriptor, "__get__", []Value{cls, cls.Meta})
				} else {
					bound = method
				}
			} else {
				bound = vm.bindAttr(method, cls, cls.Meta)
			}
			if err != nil {
				return nil, err
			}
			if _, disabled := bound.(NoneType); disabled {
				return nil, reverseNotSupported(value)
			}
			return vm.call(bound, nil, nil)
		}
	}
	switch sequence := value.(type) {
	case *DictView:
		return sequence.iterator(true), nil
	case *Dict:
		return (&DictView{dict: sequence, kind: "keys"}).iterator(true), nil
	case *Range:
		return vm.rangeReversed(sequence), nil
	case string:
		return &reverseStringIterator{text: sequence, end: len(sequence)}, nil
	case *List:
		return &reverseSequenceIterator{vm: vm, sequence: sequence, index: int64(len(sequence.Items)) - 1, kind: "list_reverseiterator"}, nil
	case *Tuple, []byte, *ByteArray, *MemoryView:
		length, err := vm.diasporeLen(sequence)
		if err != nil {
			return nil, err
		}
		return &reverseSequenceIterator{vm: vm, sequence: sequence, index: length - 1, kind: "reversed"}, nil
	}
	return nil, reverseNotSupported(value)
}

type reverseSequenceIterator struct {
	vm       *VM
	sequence Value
	index    int64
	kind     string
}

// KindName returns the Python iterator type name.
func (iterator *reverseSequenceIterator) KindName() string { return iterator.kind }

// Next advances the iterator, observing mutations to the underlying sequence.
func (iterator *reverseSequenceIterator) Next() (Value, bool, error) {
	if iterator.index < 0 || iterator.sequence == nil {
		iterator.sequence = nil
		return nil, false, nil
	}
	value, err := iterator.vm.getIndex(iterator.sequence, iterator.index)
	if err == nil {
		iterator.index--
		return value, true, nil
	}
	iterator.index = -1
	iterator.sequence = nil
	if _, stopped := raisedAs(err, "IndexError"); stopped {
		return nil, false, nil
	}
	if _, stopped := raisedAs(err, "StopIteration"); stopped {
		return nil, false, nil
	}
	return nil, false, err
}

type reverseStringIterator struct {
	text string
	end  int
}

// KindName returns the Python iterator type name.
func (iterator *reverseStringIterator) KindName() string { return "reversed" }

// Next advances by a Python code point, including lone surrogates.
func (iterator *reverseStringIterator) Next() (Value, bool, error) {
	if iterator.end == 0 {
		iterator.text = ""
		return nil, false, nil
	}
	start := iterator.end - 1
	for start > 0 && iterator.text[start]&0xc0 == 0x80 {
		start--
	}
	value := iterator.text[start:iterator.end]
	iterator.end = start
	return value, true, nil
}
