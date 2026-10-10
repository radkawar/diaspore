package interp

import (
	"bytes"
	"math/big"
)

func bytesSearch(vm *VM, source []byte, method string, args []Value, kw []KwArg) (Value, error) {
	if err := noKw(method, kw); err != nil {
		return nil, err
	}
	if err := argCount(method, args, 1, 3); err != nil {
		return nil, err
	}
	start, end := 0, len(source)
	// Bounds are converted before the needle, as in CPython's binary methods.
	for i, bound := range args[1:] {
		if _, none := bound.(NoneType); none {
			continue
		}
		index, found, err := binarySearchIndex(vm, bound)
		if err != nil {
			return nil, err
		}
		if !found {
			return nil, newError("TypeError", "slice indices must be integers or None or have an __index__ method")
		}
		var n int64
		if wide, ok := index.(*big.Int); ok && !wide.IsInt64() {
			if wide.Sign() < 0 {
				n = 0
			} else {
				n = int64(len(source)) + 1
			}
		} else if wide, ok := index.(*big.Int); ok {
			n = wide.Int64()
		} else {
			n, _ = asInt(index)
		}
		if n < 0 {
			n += int64(len(source))
			if n < 0 {
				n = 0
			}
		}
		if i == 0 {
			// A start beyond the end must remain distinct from start == len,
			// especially for an empty needle.
			if n > int64(len(source)) {
				start = len(source) + 1
			} else {
				start = int(n)
			}
		} else {
			if n > int64(len(source)) {
				end = len(source)
			} else {
				end = int(n)
			}
		}
	}
	var needle []byte
	var single [1]byte
	switch rawPrim(args[0]).(type) {
	case []byte, *ByteArray, *MemoryView:
		var err error
		needle, err = contiguousBufferBytes(args[0])
		if err != nil {
			return nil, err
		}
	default:
		index, found, err := binarySearchIndex(vm, args[0])
		if err != nil {
			return nil, err
		}
		if !found {
			return nil, newErrorf("TypeError", "argument should be integer or bytes-like object, not '%s'", typeName(args[0]))
		}
		value, err := byteValue(index)
		if err != nil {
			return nil, err
		}
		single[0] = value
		needle = single[:]
	}
	if start > end {
		if method == "rfind" {
			return int64(-1), nil
		}
		return int64(0), nil
	}
	if len(needle) == 0 {
		if method == "rfind" {
			return int64(end), nil
		}
		return int64(end-start) + 1, nil
	}
	if err := vm.fuelN(int64(end - start)); err != nil {
		return nil, err
	}
	if method == "rfind" {
		position := bytes.LastIndex(source[start:end], needle)
		if position < 0 {
			return int64(-1), nil
		}
		return int64(start + position), nil
	}
	return int64(bytes.Count(source[start:end], needle)), nil
}

// The index protocol applies to metaclass-defined numeric slots too. Looking
// up the special method on the actual type avoids instance attribute shadows.
func binarySearchIndex(vm *VM, value Value) (Value, bool, error) {
	if isIntKind(rawPrim(value)) {
		return rawPrim(value), true, nil
	}
	var class *Class
	switch object := value.(type) {
	case *Instance:
		class = object.Class
	case *Class:
		class = object.Meta
	}
	if class == nil {
		return nil, false, nil
	}
	index, found, err := vm.callNoArgSpecial(value, class, "__index__")
	if err != nil || !found {
		return nil, found, err
	}
	if !isIntKind(rawPrim(index)) {
		return nil, true, newErrorf("TypeError", "__index__ returned non-int (type %s)", typeName(index))
	}
	return rawPrim(index), true, nil
}
