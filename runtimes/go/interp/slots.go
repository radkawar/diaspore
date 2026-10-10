package interp

import (
	"sort"
	"strings"
)

type instanceLayout struct {
	hasDict      bool
	hasWeakref   bool
	variableSize bool
	slotCount    int
	solid        Value
}

type slotStorage uint8

const (
	slotNone slotStorage = iota
	slotMember
	slotDictionary
	slotWeakref
	slotTupleMember
	slotNullableMember
)

func instanceHasDict(cls *Class) bool {
	if cls.layout != nil {
		return cls.layout.hasDict
	}
	return cls != objectClass
}

func instanceSlotCount(cls *Class) int {
	if cls.layout != nil {
		return cls.layout.slotCount
	}
	return 0
}

func layoutOf(owner Value) instanceLayout {
	switch cls := owner.(type) {
	case *BuiltinType:
		return instanceLayout{
			hasDict:      cls.Name == "type" || cls.Name == "classmethod" || cls.Name == "staticmethod",
			hasWeakref:   cls.Name == "type" || cls.Name == "set" || cls.Name == "frozenset" || cls.Name == "GenericAlias",
			variableSize: cls.Name == "int" || cls.Name == "tuple" || cls.Name == "bytes" || cls.Name == "type",
			solid:        cls,
		}
	case *Class:
		if cls.layout != nil {
			return *cls.layout
		}
		if cls == objectClass {
			return instanceLayout{solid: objectClass}
		}
		if excClasses[cls.Name] == cls {
			return instanceLayout{hasDict: true, solid: baseExc}
		}
		return instanceLayout{hasDict: true, hasWeakref: true, solid: objectClass}
	}
	panic("layout owner is not a type")
}

func layoutSubtype(left, right Value) bool {
	if left == right || right == objectClass {
		return true
	}
	if cls, ok := left.(*Class); ok {
		switch parent := right.(type) {
		case *Class:
			return cls.IsSub(parent)
		case *BuiltinType:
			return cls.Prim == parent
		}
	}
	return false
}

func (vm *VM) initializeSlots(cls *Class) error {
	bases := baseValues(cls.Bases, cls.directPrim, cls.primIndex)
	if len(bases) == 0 {
		bases = []Value{objectClass}
	}
	best := bases[0]
	inherited := layoutOf(best)
	hasDict, hasWeakref := false, false
	for _, base := range bases {
		if native, ok := base.(*Class); ok && native.nativeFinal {
			return newErrorf("TypeError", "type '%s' is not an acceptable base type", native.diagnosticName())
		}
		if primitive, ok := base.(*BuiltinType); ok {
			switch primitive.Name {
			case "bool", "NoneType", "NotImplementedType", "ellipsis", "range", "slice", "memoryview":
				return newErrorf("TypeError", "type '%s' is not an acceptable base type", primitive.Name)
			}
		}
		candidate := layoutOf(base)
		hasDict = hasDict || candidate.hasDict
		hasWeakref = hasWeakref || candidate.hasWeakref
		if candidate.solid == inherited.solid {
			continue
		}
		if layoutSubtype(candidate.solid, inherited.solid) {
			best, inherited = base, candidate
		} else if !layoutSubtype(inherited.solid, candidate.solid) {
			return newError("TypeError", "multiple bases have instance lay-out conflict")
		}
	}
	var names []string
	addDict, addWeakref := false, false
	declared, present := cls.NS["__slots__"]
	if !present {
		addDict, addWeakref = !hasDict, !hasWeakref && !inherited.variableSize
	} else {
		var items []Value
		if text, ok := rawPrim(declared).(string); ok {
			items = []Value{text}
		} else {
			var err error
			items, err = vm.iterToSlice(declared)
			if err != nil {
				return err
			}
		}
		if len(items) != 0 && inherited.variableSize {
			return newErrorf("TypeError", "nonempty __slots__ not supported for subtype of '%s'", metadataOwnerName(best))
		}
		for _, item := range items {
			name, ok := rawPrim(item).(string)
			if !ok {
				return newError("TypeError", "__slots__ items must be strings")
			}
			if !pyIsIdentifier(name) {
				return newError("TypeError", "__slots__ must be identifiers")
			}
			switch name {
			case "__dict__":
				if hasDict || addDict {
					return newError("TypeError", "__dict__ slot disallowed: we already got one")
				}
				addDict = true
			case "__weakref__":
				if hasWeakref || addWeakref {
					return newError("TypeError", "__weakref__ slot disallowed: we already got one")
				}
				addWeakref = true
			default:
				if strings.HasPrefix(name, "__") && !strings.HasSuffix(name, "__") {
					if owner := strings.TrimLeft(cls.Name, "_"); owner != "" {
						name = "_" + owner + name
					}
				}
				if _, conflict := cls.NS[name]; conflict {
					return newErrorf("ValueError", "'%s' in __slots__ conflicts with class variable", name)
				}
				names = append(names, name)
			}
		}
	}
	sort.Strings(names)
	layout := &instanceLayout{
		hasDict: hasDict || addDict, hasWeakref: hasWeakref || addWeakref,
		variableSize: inherited.variableSize, slotCount: inherited.slotCount + len(names),
		solid: inherited.solid,
	}
	if len(names) != 0 {
		layout.solid = cls
	}
	cls.layout = layout
	for index, name := range names {
		if index+1 < len(names) && names[index+1] == name {
			continue
		}
		cls.NS[name] = &metadataDescriptor{owner: cls, name: name, kind: "member_descriptor",
			getter: true, writable: true, storage: slotMember, slotIndex: inherited.slotCount + index}
		cls.dictOrder = append(cls.dictOrder, name)
	}
	for _, entry := range []struct {
		add     bool
		name    string
		storage slotStorage
	}{{addDict, "__dict__", slotDictionary}, {addWeakref, "__weakref__", slotWeakref}} {
		if _, overridden := cls.NS[entry.name]; entry.add && !overridden {
			cls.NS[entry.name] = &metadataDescriptor{owner: cls, name: entry.name, kind: "getset_descriptor",
				getter: true, writable: entry.storage == slotDictionary, storage: entry.storage}
			cls.dictOrder = append(cls.dictOrder, entry.name)
		}
	}
	return nil
}

func (d *metadataDescriptor) storageInstance(value Value) (*Instance, error) {
	instance, ok := value.(*Instance)
	if !ok || !d.accepts(value) {
		return nil, newErrorf("TypeError", "descriptor '%s' for '%s' objects doesn't apply to a '%s' object",
			d.name, metadataOwnerName(d.owner), typeName(value))
	}
	return instance, nil
}

func (d *metadataDescriptor) storageGet(vm *VM, value Value) (Value, error) {
	instance, err := d.storageInstance(value)
	if err != nil {
		return nil, err
	}
	switch d.storage {
	case slotTupleMember:
		tuple, ok := instance.Prim.(*Tuple)
		if !ok || d.slotIndex < 0 || d.slotIndex >= len(tuple.Items) {
			return nil, newError("TypeError", "invalid tuple member storage")
		}
		return tuple.Items[d.slotIndex], nil
	case slotDictionary:
		return vm.instanceDictionary(instance)
	case slotWeakref:
		module, loaded := vm.modules["_weakref"]
		if !loaded {
			return None, nil
		}
		registry, err := vm.getAttr(module, "_registry")
		if err != nil {
			return nil, err
		}
		identity, err := biID(vm, []Value{instance}, nil)
		if err != nil {
			return nil, err
		}
		references, err := vm.getIndex(registry, identity)
		if _, missing := raisedAs(err, "KeyError"); missing {
			return None, nil
		}
		if err != nil {
			return nil, err
		}
		if entries, ok := references.(*List); ok && len(entries.Items) != 0 {
			return entries.Items[0], nil
		}
		return None, nil
	}
	if d.slotIndex < len(instance.slotValues) && instance.slotValues[d.slotIndex] != nil {
		return instance.slotValues[d.slotIndex], nil
	}
	if d.storage == slotNullableMember {
		return None, nil
	}
	return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'", instance.Class.Name, d.name)
}

func (d *metadataDescriptor) storageSet(vm *VM, value, replacement Value, deleting bool) error {
	instance, err := d.storageInstance(value)
	if err != nil {
		return err
	}
	switch d.storage {
	case slotTupleMember:
		return newError("AttributeError", "readonly attribute")
	case slotDictionary:
		if deleting {
			instance.dictionary = nil
			return nil
		}
		return vm.replaceInstanceDictionary(instance, replacement)
	case slotWeakref:
		return newErrorf("AttributeError", "attribute '__weakref__' of '%s' objects is not writable", instance.Class.Name)
	}
	if deleting {
		if d.slotIndex >= len(instance.slotValues) || instance.slotValues[d.slotIndex] == nil {
			if d.storage == slotNullableMember {
				return nil
			}
			return newError("AttributeError", d.name)
		}
		instance.slotValues[d.slotIndex] = nil
		return nil
	}
	if instance.slotValues == nil {
		instance.slotValues = make([]Value, instanceSlotCount(instance.Class))
	}
	instance.slotValues[d.slotIndex] = replacement
	return nil
}
