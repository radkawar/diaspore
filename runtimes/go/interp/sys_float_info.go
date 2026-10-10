package interp

import (
	"math"
	"strings"
)

// Field documentation follows CPython 3.12.12 Objects/floatobject.c.
// The retained upstream PSF license is src/diaspore/cpython/LICENSE.
type sysInfoField struct {
	name  string
	doc   string
	value Value
}

var floatInfoFields = [...]sysInfoField{
	{"max", "DBL_MAX -- maximum representable finite float", float64(math.MaxFloat64)},
	{"max_exp", "DBL_MAX_EXP -- maximum int e such that radix**(e-1) is representable", int64(1024)},
	{"max_10_exp", "DBL_MAX_10_EXP -- maximum int e such that 10**e is representable", int64(308)},
	{"min", "DBL_MIN -- Minimum positive normalized float", float64(0x1p-1022)},
	{"min_exp", "DBL_MIN_EXP -- minimum int e such that radix**(e-1) is a normalized float", int64(-1021)},
	{"min_10_exp", "DBL_MIN_10_EXP -- minimum int e such that 10**e is a normalized float", int64(-307)},
	{"dig", "DBL_DIG -- maximum number of decimal digits that can be faithfully represented in a float", int64(15)},
	{"mant_dig", "DBL_MANT_DIG -- mantissa digits", int64(53)},
	{"epsilon", "DBL_EPSILON -- Difference between 1 and the next representable float", float64(0x1p-52)},
	{"radix", "FLT_RADIX -- radix of exponent", int64(2)},
	{"rounds", "FLT_ROUNDS -- rounding mode used for arithmetic operations", int64(1)},
}

func (vm *VM) makeFloatInfo() *Instance {
	return vm.makeSystemInfo("float_info", "A named tuple holding information about the float type. It contains low level\ninformation about the precision and internal representation. Please study\nyour system's :file:`float.h` for more information.", floatInfoFields[:])
}

// makeSystemInfo builds an immutable tuple-backed native struct sequence.
// Its member descriptors use the same physical tuple storage as indexing.
func (vm *VM) makeSystemInfo(name, doc string, fields []sysInfoField) *Instance {
	cls := &Class{
		Name: name, NS: map[string]Value{"__module__": "sys"},
		Prim: vm.types["tuple"], directPrim: vm.types["tuple"],
		nativeImmutable: true, nativeFinal: true, vm: vm,
	}
	cls.MRO = []*Class{cls, objectClass}
	cls.layout = &instanceLayout{solid: cls, variableSize: true}
	cls.NS["__doc__"] = "sys." + name + "\n\n" + doc
	cls.NS["n_fields"] = int64(len(fields))
	cls.NS["n_sequence_fields"] = int64(len(fields))
	cls.NS["n_unnamed_fields"] = int64(0)
	values := make([]Value, len(fields))
	names := make([]Value, len(fields))
	for i, field := range fields {
		values[i], names[i] = field.value, field.name
		cls.NS[field.name] = &metadataDescriptor{
			owner: cls, name: field.name, kind: "member_descriptor", getter: true,
			storage: slotTupleMember, slotIndex: i, doc: field.doc,
		}
	}
	cls.NS["__match_args__"] = &Tuple{Items: names}
	constructor := bi("__new__", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if len(args) == 0 || args[0] != cls {
			return nil, newErrorf("TypeError", "sys.%s.__new__ requires the sys.%s type", name, name)
		}
		return vm.newSystemInfo(cls, len(fields), args[1:], kw)
	})
	constructor.NoBind, constructor.owner = true, cls
	cls.NS["__new__"] = constructor
	cls.NS["__repr__"] = &metadataDescriptor{owner: cls, name: "__repr__", kind: "wrapper_descriptor", fn: bi("__repr__", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		return systemInfoRepr(vm, fields, args, kw)
	})}
	cls.NS["__reduce__"] = &metadataDescriptor{owner: cls, name: "__reduce__", kind: "method_descriptor", fn: bi("__reduce__", systemInfoReduce)}
	return newInstance(cls, &Tuple{Items: values})
}

func (vm *VM) newSystemInfo(cls *Class, count int, args []Value, kw []KwArg) (Value, error) {
	if len(args)+len(kw) > 2 {
		return nil, newErrorf("TypeError", "structseq() takes at most 2 arguments (%d given)", len(args)+len(kw))
	}
	var sequence, mapping Value
	if len(args) > 0 {
		sequence = args[0]
	}
	if len(args) > 1 {
		mapping = args[1]
	}
	for _, keyword := range kw {
		switch keyword.Name {
		case "sequence":
			if sequence != nil {
				return nil, newError("TypeError", "argument for structseq() given by name ('sequence') and position (1)")
			}
			sequence = keyword.Val
		case "dict":
			if mapping != nil {
				return nil, newError("TypeError", "argument for structseq() given by name ('dict') and position (2)")
			}
			mapping = keyword.Val
		default:
			return nil, newErrorf("TypeError", "'%s' is an invalid keyword argument for structseq()", keyword.Name)
		}
	}
	if sequence == nil {
		return nil, newError("TypeError", "structseq() missing required argument 'sequence' (pos 1)")
	}
	if mapping != nil {
		if _, valid := rawPrim(mapping).(*Dict); !valid {
			return nil, newErrorf("TypeError", "sys.%s() takes a dict as second arg, if any", cls.Name)
		}
	}
	iterator, err := vm.getIter(sequence)
	if err != nil {
		if _, typeError := raisedAs(err, "TypeError"); typeError {
			return nil, newError("TypeError", "constructor requires a sequence")
		}
		return nil, err
	}
	items := make([]Value, 0, count)
	for {
		value, found, err := iterator.Next()
		if err != nil {
			return nil, err
		}
		if !found {
			break
		}
		items = append(items, value)
	}
	if len(items) != count {
		return nil, newErrorf("TypeError", "sys.%s() takes a %d-sequence (%d-sequence given)", cls.Name, count, len(items))
	}
	return newInstance(cls, &Tuple{Items: items}), nil
}

func systemInfoRepr(vm *VM, fields []sysInfoField, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("__repr__", kw); err != nil {
		return nil, err
	}
	if err := argCount("__repr__", args, 1, 1); err != nil {
		return nil, err
	}
	instance := args[0].(*Instance)
	values := instance.Prim.(*Tuple).Items
	var result strings.Builder
	result.WriteString("sys.")
	result.WriteString(instance.Class.Name)
	result.WriteByte('(')
	for i, field := range fields {
		if i > 0 {
			result.WriteString(", ")
		}
		result.WriteString(field.name)
		result.WriteByte('=')
		text, err := vm.repr(values[i])
		if err != nil {
			return nil, err
		}
		result.WriteString(text)
	}
	result.WriteByte(')')
	return result.String(), nil
}

func systemInfoReduce(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("__reduce__", kw); err != nil {
		return nil, err
	}
	if err := argCount("__reduce__", args, 1, 1); err != nil {
		return nil, err
	}
	instance := args[0].(*Instance)
	sequence := &Tuple{Items: append([]Value(nil), instance.Prim.(*Tuple).Items...)}
	return &Tuple{Items: []Value{instance.Class, &Tuple{Items: []Value{sequence, NewDict()}}}}, nil
}
