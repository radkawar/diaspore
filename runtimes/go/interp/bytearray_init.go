package interp

import (
	"math/big"
	"strings"
)

func bytearrayInit(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if len(args) == 0 {
		return nil, newError("TypeError", "descriptor '__init__' of 'bytearray' object needs an argument")
	}
	target, ok := rawPrim(args[0]).(*ByteArray)
	if !ok {
		return nil, newErrorf("TypeError", "descriptor '__init__' requires a 'bytearray' object but received a '%s'", typeName(args[0]))
	}
	if err := bytearrayInitialize(vm, target, args[1:], kw); err != nil {
		return nil, err
	}
	return None, nil
}

func bytearrayInitialize(vm *VM, target *ByteArray, args []Value, kw []KwArg) error {
	if err := argCount("bytearray", args, 0, 3); err != nil {
		return err
	}
	var values [3]Value
	var present [3]bool
	for i, value := range args {
		values[i], present[i] = value, true
	}
	for _, keyword := range kw {
		var i int
		switch keyword.Name {
		case "source":
			i = 0
		case "encoding":
			i = 1
		case "errors":
			i = 2
		default:
			return newErrorf("TypeError", "'%s' is an invalid keyword argument for bytearray()", keyword.Name)
		}
		if present[i] {
			return newErrorf("TypeError", "argument for bytearray() given by name and position (%d)", i+1)
		}
		values[i], present[i] = keyword.Val, true
	}
	encoding, errors := "", "strict"
	for i := 1; i < 3; i++ {
		if !present[i] {
			continue
		}
		text, ok := rawPrim(values[i]).(string)
		if !ok {
			return newErrorf("TypeError", "bytearray() argument %d must be str, not %s", i+1, typeName(values[i]))
		}
		if strings.IndexByte(text, 0) >= 0 {
			return newError("ValueError", "embedded null character")
		}
		if i == 1 {
			encoding = text
		} else {
			errors = text
		}
	}
	// Like CPython, validation by the argument parser happens first, but all
	// semantic conversions happen after discarding the previous contents.
	if len(target.data) != 0 {
		if err := bytearrayResize(vm, target, 0); err != nil {
			return err
		}
	}
	if !present[0] {
		if present[1] {
			return newError("TypeError", "encoding without a string argument")
		}
		if present[2] {
			return newError("TypeError", "errors without a string argument")
		}
		return nil
	}
	source := values[0]
	if text, ok := rawPrim(source).(string); ok {
		if !present[1] {
			return newError("TypeError", "string argument without an encoding")
		}
		encoded, err := vm.encodeText(text, encoding, errors)
		if err != nil {
			return err
		}
		data, ok := rawPrim(encoded).([]byte)
		if !ok {
			return newErrorf("TypeError", "encoder returned '%s' instead of bytes", typeName(encoded))
		}
		if err := bytearrayResize(vm, target, len(data)); err != nil {
			return err
		}
		copy(target.data, data)
		return nil
	}
	if present[1] {
		return newError("TypeError", "encoding without a string argument")
	}
	if present[2] {
		return newError("TypeError", "errors without a string argument")
	}
	indexable := isIntKind(rawPrim(source))
	if instance, ok := source.(*Instance); ok {
		if _, found := instance.Class.Lookup("__index__"); found {
			indexable = true
		}
	}
	if indexable {
		index, err := percentIndex(vm, source)
		if err == nil {
			if wide, ok := index.(*big.Int); ok {
				if !wide.IsInt64() {
					return newError("OverflowError", "cannot fit 'int' into an index-sized integer")
				}
				index = wide.Int64()
			}
			count, _ := asInt(index)
			if count > int64(int(^uint(0)>>1)) {
				return newError("OverflowError", "cannot fit 'int' into an index-sized integer")
			}
			if count < 0 {
				return newError("ValueError", "negative count")
			}
			return bytearrayResize(vm, target, int(count))
		}
		if _, typeError := raisedAs(err, "TypeError"); !typeError {
			return err
		}
		// An index conversion TypeError falls back to the iterable protocol.
	}
	switch rawPrim(source).(type) {
	case []byte, *ByteArray, *MemoryView:
		data, err := bufferBytes(source)
		if err != nil {
			return err
		}
		if err := bytearrayResize(vm, target, len(data)); err != nil {
			return err
		}
		copy(target.data, data)
		return nil
	}
	var items []Value
	switch sequence := source.(type) {
	case *List:
		items = sequence.Items
	case *Tuple:
		items = sequence.Items
	}
	if items != nil {
		if err := bytearrayResize(vm, target, len(items)); err != nil {
			return err
		}
		complete := true
		for i, item := range items {
			switch item.(type) {
			case int64, *big.Int:
				value, err := byteValue(item)
				if err != nil {
					return err
				}
				target.data[i] = value
			default:
				if err := bytearrayResize(vm, target, 0); err != nil {
					return err
				}
				complete = false
			}
			if !complete {
				break
			}
		}
		if complete {
			return nil
		}
	}
	iterator, err := vm.getIter(source)
	if err != nil {
		if _, typeError := raisedAs(err, "TypeError"); typeError {
			return newErrorf("TypeError", "cannot convert '%s' object to bytearray", typeName(source))
		}
		return err
	}
	for {
		item, found, err := iterator.Next()
		if err != nil {
			return err
		}
		if !found {
			return nil
		}
		index, err := percentIndex(vm, item)
		if err != nil {
			return err
		}
		value, err := byteValue(index)
		if err != nil {
			return err
		}
		if target.exports.Load() != 0 {
			return newError("BufferError", "Existing exports of data: object cannot be re-sized")
		}
		if err := vm.fuelN(1); err != nil {
			return err
		}
		target.data = append(target.data, value)
	}
}

func bytearrayResize(vm *VM, target *ByteArray, length int) error {
	if len(target.data) == length {
		return nil
	}
	if target.exports.Load() != 0 {
		return newError("BufferError", "Existing exports of data: object cannot be re-sized")
	}
	if err := vm.fuelN(int64(length)); err != nil {
		return err
	}
	// Zero newly initialized storage: native uninitialized bytes must never
	// escape into the guest even when an exact-sequence conversion fails.
	if length == 0 {
		target.data = nil
	} else {
		target.data = make([]byte, length)
	}
	return nil
}
