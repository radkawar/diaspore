package interp

func newInstance(cls *Class, primitive Value) *Instance {
	return &Instance{Class: cls, Prim: primitive}
}

// These accessors are for runtime-owned string attributes. Normal Python
// attribute access uses the full dictionary protocol and descriptor lookup.
func (instance *Instance) dictValue(name string) Value {
	value, _ := instance.dictLookup(name)
	return value
}

func (instance *Instance) dictLookup(name string) (Value, bool) {
	switch dictionary := rawPrim(instance.dictionary).(type) {
	case *Dict:
		return dictionary.GetStr(name)
	case *metadataNamespace:
		if dictionary.cells != nil {
			if entry := dictionary.cells[name]; entry != nil && entry.v != nil {
				return entry.v, true
			}
			return nil, false
		}
		value, found := dictionary.values[name]
		return value, found
	}
	return nil, false
}

func (instance *Instance) dictSet(name string, value Value) {
	if instance.dictionary == nil {
		instance.dictionary = NewDict()
	}
	switch dictionary := rawPrim(instance.dictionary).(type) {
	case *Dict:
		dictionary.SetStr(name, value)
	case *metadataNamespace:
		if dictionary.cells != nil {
			if entry := dictionary.cells[name]; entry != nil {
				entry.v = value
			} else {
				dictionary.cells[name] = &cell{v: value}
			}
		} else {
			if dictionary.values == nil {
				dictionary.values = map[string]Value{}
			}
			dictionary.values[name] = value
		}
	}
}

func (instance *Instance) dictDelete(name string) bool {
	switch dictionary := rawPrim(instance.dictionary).(type) {
	case *Dict:
		if index, found := dictionary.strIndex[name]; found {
			dictionary.deleteAt(index)
			return true
		}
		return false
	case *metadataNamespace:
		if dictionary.cells != nil {
			if entry := dictionary.cells[name]; entry != nil && entry.v != nil {
				entry.v = nil
				return true
			}
			return false
		}
		_, found := dictionary.values[name]
		delete(dictionary.values, name)
		return found
	}
	return false
}

func (instance *Instance) dictNames() []string {
	var names []string
	switch dictionary := rawPrim(instance.dictionary).(type) {
	case *Dict:
		for _, entry := range dictionary.entries {
			if name, ok := rawPrim(entry.key).(string); ok {
				names = append(names, name)
			}
		}
	case *metadataNamespace:
		for _, key := range dictionary.keys() {
			if name, ok := rawPrim(key).(string); ok {
				names = append(names, name)
			}
		}
	}
	return names
}

func instanceDictionaryEmpty(instance *Instance) bool {
	switch dictionary := rawPrim(instance.dictionary).(type) {
	case *Dict:
		return dictionary.Len() == 0
	case *metadataNamespace:
		return dictionary.len() == 0
	}
	return true
}

func (vm *VM) instanceDictionary(instance *Instance) (Value, error) {
	if !instanceHasDict(instance.Class) {
		return nil, newErrorf("AttributeError", "'%s' object has no attribute '__dict__'", instance.Class.diagnosticName())
	}
	if instance.dictionary == nil {
		instance.dictionary = NewDict()
	}
	return instance.dictionary, nil
}

func (vm *VM) replaceInstanceDictionary(instance *Instance, value Value) error {
	switch rawPrim(value).(type) {
	case *Dict, *metadataNamespace:
		instance.dictionary = value
		return nil
	default:
		return newErrorf("TypeError", "__dict__ must be set to a dictionary, not a '%s'", typeName(value))
	}
}

func (vm *VM) instanceLookup(instance *Instance, name string) (Value, bool, error) {
	switch dictionary := rawPrim(instance.dictionary).(type) {
	case *Dict:
		if dictionary.slowKeys == 0 {
			value, found := dictionary.GetStr(name)
			return value, found, nil
		}
		return dictionary.Get(name)
	case *metadataNamespace:
		return dictionary.getString(name)
	}
	return nil, false, nil
}

func (vm *VM) instanceStore(instance *Instance, name string, value Value) error {
	dictionary, err := vm.instanceDictionary(instance)
	if err != nil {
		return newErrorf("AttributeError", "'%s' object has no attribute '%s'", instance.Class.diagnosticName(), name)
	}
	switch target := rawPrim(dictionary).(type) {
	case *Dict:
		if target.slowKeys == 0 {
			target.SetStr(name, value)
			return nil
		}
		return target.Set(name, value)
	case *metadataNamespace:
		return target.setString(name, value)
	}
	return newError("TypeError", "invalid instance dictionary")
}

func (vm *VM) instanceDelete(instance *Instance, name string) error {
	var found bool
	var err error
	switch dictionary := rawPrim(instance.dictionary).(type) {
	case *Dict:
		_, found, err = dictionary.Delete(name)
	case *metadataNamespace:
		_, found, err = dictionary.get(name)
		if err == nil && found {
			err = dictionary.del(name)
		}
	}
	if err != nil {
		return err
	}
	if !found {
		return newErrorf("AttributeError", "'%s' object has no attribute '%s'", instance.Class.diagnosticName(), name)
	}
	return nil
}
