package interp

import (
	"math/big"
	"runtime"
	"sync/atomic"
)

// ByteArray owns mutable bytes and counts outstanding exported views.
type ByteArray struct {
	data    []byte
	exports atomic.Int64
}

// MemoryView shares its owner's storage. Offsets and strides are in bytes.
// Shape and strides are immutable, so derived views may share their metadata.
type MemoryView struct {
	owner               Value
	data                []byte
	start, step, length int
	format              string
	itemsize            int
	shape, strides      []int
	readonly, released  bool
	hashBytes           []byte
	hashReady           bool
	cleanup             runtime.Cleanup
}

func indexInt(k Value, length int) (int, error) {
	n, err := wantInt("index", k)
	if err != nil {
		return 0, err
	}
	return seqIndexCheck(length, n, "index out of range")
}

func (vm *VM) bufferIndex(k Value, length int) (int, error) {
	v, err := percentIndex(vm, k)
	if err != nil {
		return 0, err
	}
	if z, ok := v.(*big.Int); ok {
		if !z.IsInt64() {
			return 0, newError("IndexError", "cannot fit 'int' into an index-sized integer")
		}
		v = z.Int64()
	}
	return indexInt(v, length)
}

type bufferIterator struct {
	vm    *VM
	value Value
	index int64
}

func (it *bufferIterator) KindName() string { return "iterator" }
func (it *bufferIterator) Next() (Value, bool, error) {
	v, err := it.vm.bufferGet(it.value, it.index)
	if err != nil {
		if _, done := raisedAs(err, "IndexError"); done {
			return nil, false, nil
		}
		return nil, false, err
	}
	it.index++
	return v, true, nil
}

func ctorByteArray(vm *VM, args []Value, kw []KwArg) (Value, error) {
	value := &ByteArray{}
	if err := bytearrayInitialize(vm, value, args, kw); err != nil {
		return nil, err
	}
	return value, nil
}

func retainMemoryView(v *MemoryView) *MemoryView {
	v.hashBytes, v.hashReady = nil, false
	if b, ok := v.owner.(*ByteArray); ok {
		b.exports.Add(1)
		v.cleanup = runtime.AddCleanup(v, func(owner *ByteArray) { owner.exports.Add(-1) }, b)
	}
	return v
}

func ctorMemoryView(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("memoryview", args, 1, 1); err != nil {
		return nil, err
	}
	if err := noKw("memoryview", kw); err != nil {
		return nil, err
	}
	v := &MemoryView{step: 1, itemsize: 1, format: "B"}
	switch x := rawPrim(args[0]).(type) {
	case []byte:
		v.owner, v.data, v.length, v.readonly = x, x, len(x), true
	case *ByteArray:
		v.owner, v.data, v.length = x, x.data, len(x.data)
	case *MemoryView:
		if err := x.check(); err != nil {
			return nil, err
		}
		*v = *x
	default:
		return nil, newErrorf("TypeError", "memoryview: a bytes-like object is required, not '%s'", typeName(args[0]))
	}
	if v.shape == nil {
		v.shape, v.strides = []int{v.length}, []int{1}
	}
	return retainMemoryView(v), nil
}

func (v *MemoryView) check() error {
	if v.released {
		return newError("ValueError", "operation forbidden on released memoryview object")
	}
	return nil
}

func (v *MemoryView) release() {
	if v.released {
		return
	}
	v.cleanup.Stop()
	if b, ok := v.owner.(*ByteArray); ok {
		b.exports.Add(-1)
	}
	v.released = true
	v.owner, v.data = nil, nil
}

func (v *MemoryView) lenValue() (int64, error) {
	if err := v.check(); err != nil {
		return 0, err
	}
	if len(v.shape) == 0 {
		return 0, newError("TypeError", "0-dim memory has no length")
	}
	return int64(v.length), nil
}

func contiguousBufferBytes(value Value) ([]byte, error) {
	if v, ok := rawPrim(value).(*MemoryView); ok {
		if err := v.check(); err != nil {
			return nil, err
		}
		if !v.cContiguous() {
			return nil, newError("BufferError", "memoryview: underlying buffer is not C-contiguous")
		}
	}
	return bufferBytes(value)
}

func bufferBytes(value Value) ([]byte, error) {
	switch b := rawPrim(value).(type) {
	case []byte:
		return b, nil
	case *ByteArray:
		return b.data, nil
	case *MemoryView:
		if err := b.check(); err != nil {
			return nil, err
		}
		defer runtime.KeepAlive(b)
		n := b.byteLen()
		if n == 0 {
			return b.data[:0], nil
		}
		if b.cContiguous() {
			return b.data[b.start : b.start+n], nil
		}
		out := make([]byte, n)
		for i := range n / b.itemsize {
			offset := b.flatOffset(i, false)
			copy(out[i*b.itemsize:], b.data[offset:offset+b.itemsize])
		}
		return out, nil
	}
	return nil, newErrorf("TypeError", "a bytes-like object is required, not '%s'", typeName(value))
}

func byteValue(v Value) (byte, error) {
	if z, ok := rawPrim(v).(*big.Int); ok {
		if !z.IsInt64() {
			return 0, newError("ValueError", "byte must be in range(0, 256)")
		}
		v = z.Int64()
	}
	n, err := wantInt("byte", rawPrim(v))
	if err != nil {
		return 0, err
	}
	if n < 0 || n > 255 {
		return 0, newError("ValueError", "byte must be in range(0, 256)")
	}
	return byte(n), nil
}

func (vm *VM) bufferGet(o Value, k Value) (Value, error) {
	switch b := o.(type) {
	case *ByteArray:
		if _, ok := k.(*Slice); !ok {
			index, err := percentIndex(vm, k)
			if err != nil {
				return nil, err
			}
			i, err := vm.bufferIndex(index, len(b.data))
			if err != nil {
				return nil, err
			}
			return int64(b.data[i]), nil
		}
		sl, err := vm.bufferSlice(k.(*Slice))
		if err != nil {
			return nil, err
		}
		start, stop, step, err := sliceIndices(sl, len(b.data))
		if err != nil {
			return nil, err
		}
		out := make([]byte, sliceCount(start, stop, step))
		for i := range out {
			out[i] = b.data[start+i*step]
		}
		return &ByteArray{data: out}, nil
	case *MemoryView:
		return vm.memoryViewGet(b, k)
	}
	return nil, newError("TypeError", "not a buffer")
}

func sliceCount(start, stop, step int) int {
	if step > 0 {
		if start >= stop {
			return 0
		}
		return 1 + (stop-1-start)/step
	}
	if start <= stop {
		return 0
	}
	return 1 + (start-1-stop)/(-step)
}

func (vm *VM) bufferSet(o, k, value Value, del bool) error {
	if view, ok := o.(*MemoryView); ok {
		return vm.memoryViewSet(view, k, value, del)
	}
	array, ok := o.(*ByteArray)
	if !ok {
		return newError("TypeError", "not a writable buffer")
	}
	if sl, ok := k.(*Slice); ok {
		normalized, err := vm.bufferSlice(sl)
		if err != nil {
			return err
		}
		var rhs []byte
		if !del {
			if isIntKind(rawPrim(value)) {
				return newError("TypeError", "can assign only bytes, buffers, or iterables of ints in range(0, 256)")
			}
			switch rawPrim(value).(type) {
			case []byte, *ByteArray, *MemoryView:
				rhs, err = bufferBytes(value)
				if err != nil {
					return err
				}
				if sameBufferOwner(array, value) {
					rhs = append([]byte(nil), rhs...)
				}
			default:
				r, err := ctorBytes(vm, []Value{value}, nil)
				if err != nil {
					return err
				}
				rhs = r.([]byte)
			}
		}
		data := array.data
		start, stop, step, err := sliceIndices(normalized, len(data))
		if err != nil {
			return err
		}
		count := sliceCount(start, stop, step)
		if step == 1 {
			if stop < start {
				stop = start
			}
			if len(rhs) == count {
				copy(data[start:stop], rhs)
				return nil
			}
			if array.exports.Load() > 0 {
				return newError("BufferError", "Existing exports of data: object cannot be re-sized")
			}
			newLen := len(data) - count + len(rhs)
			if newLen <= cap(data) {
				out := data[:newLen]
				copy(out[start+len(rhs):], data[stop:])
				copy(out[start:], rhs)
				array.data = out
			} else {
				out := make([]byte, newLen)
				copy(out, data[:start])
				copy(out[start:], rhs)
				copy(out[start+len(rhs):], data[stop:])
				array.data = out
			}
			return nil
		}
		if del {
			if count == 0 {
				return nil
			}
			if array.exports.Load() > 0 {
				return newError("BufferError", "Existing exports of data: object cannot be re-sized")
			}
			first, stride := start, step
			if stride < 0 {
				first = start + (count-1)*step
				stride = -stride
			}
			next, left, dst := first, count, 0
			for i, b := range data {
				if left > 0 && i == next {
					next += stride
					left--
					continue
				}
				data[dst] = b
				dst++
			}
			array.data = data[:dst]
			return nil
		}
		if len(rhs) != count {
			return newErrorf("ValueError", "attempt to assign bytes of size %d to extended slice of size %d", len(rhs), count)
		}
		for i := range count {
			data[start+i*step] = rhs[i]
		}
		return nil
	}
	index, err := percentIndex(vm, k)
	if err != nil {
		return err
	}
	if del {
		data := array.data
		i, err := vm.bufferIndex(index, len(data))
		if err != nil {
			return err
		}
		if array.exports.Load() > 0 {
			return newError("BufferError", "Existing exports of data: object cannot be re-sized")
		}
		array.data = append(data[:i], data[i+1:]...)
		return nil
	}
	value, err = percentIndex(vm, value)
	if err != nil {
		return err
	}
	if z, ok := value.(*big.Int); ok {
		if !z.IsInt64() {
			return newError("ValueError", "byte must be in range(0, 256)")
		}
		value = z.Int64()
	}
	b, err := byteValue(value)
	if err != nil {
		return err
	}
	data := array.data
	i, err := vm.bufferIndex(index, len(data))
	if err != nil {
		return err
	}
	data[i] = b
	return nil
}

func (vm *VM) bufferAttr(o Value, n string) (Value, error) {
	if v, ok := o.(*MemoryView); ok {
		return vm.memoryViewAttr(v, n)
	}
	if b, ok := o.(*ByteArray); ok {
		if n == "maketrans" {
			return bytesMakeTransBuiltin, nil
		}
		switch n {
		case "hex":
			return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
				return bufferHex(vm, b.data, a, k)
			}), nil
		case "append", "extend", "insert", "remove", "clear", "pop", "reverse", "copy":
			return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
				if err := noKw(n, k); err != nil {
					return nil, err
				}
				switch n {
				case "copy":
					if err := argCount(n, a, 0, 0); err != nil {
						return nil, err
					}
					return &ByteArray{data: append([]byte{}, b.data...)}, nil
				case "reverse":
					if err := argCount(n, a, 0, 0); err != nil {
						return nil, err
					}
					for i, j := 0, len(b.data)-1; i < j; i, j = i+1, j-1 {
						b.data[i], b.data[j] = b.data[j], b.data[i]
					}
					return None, nil
				case "clear":
					if err := argCount(n, a, 0, 0); err != nil {
						return nil, err
					}
					return None, vm.bufferSet(b, &Slice{Lower: None, Upper: None, Step: None}, nil, true)
				case "pop":
					if err := argCount(n, a, 0, 1); err != nil {
						return nil, err
					}
					if len(b.data) == 0 {
						return nil, newError("IndexError", "pop from empty bytearray")
					}
					index := Value(int64(-1))
					if len(a) > 0 {
						index = a[0]
					}
					x, err := vm.bufferGet(b, index)
					if err != nil {
						return nil, err
					}
					return x, vm.bufferSet(b, index, nil, true)
				case "insert":
					if err := argCount(n, a, 2, 2); err != nil {
						return nil, err
					}
					iv, err := percentIndex(vm, a[0])
					if err != nil {
						return nil, err
					}
					index, err := wantInt("index", iv)
					if err != nil {
						return nil, err
					}
					if index < 0 {
						index += int64(len(b.data))
						if index < 0 {
							index = 0
						}
					}
					if index > int64(len(b.data)) {
						index = int64(len(b.data))
					}
					x, err := percentIndex(vm, a[1])
					if err != nil {
						return nil, err
					}
					by, err := byteValue(x)
					if err != nil {
						return nil, err
					}
					return None, vm.bufferSet(b, &Slice{Lower: index, Upper: index, Step: None}, []byte{by}, false)
				case "remove":
					if err := argCount(n, a, 1, 1); err != nil {
						return nil, err
					}
					x, err := percentIndex(vm, a[0])
					if err != nil {
						return nil, err
					}
					by, err := byteValue(x)
					if err != nil {
						return nil, err
					}
					for i, c := range b.data {
						if c == by {
							return None, vm.bufferSet(b, int64(i), nil, true)
						}
					}
					return nil, newError("ValueError", "value not found in bytearray")
				default:
					if err := argCount(n, a, 1, 1); err != nil {
						return nil, err
					}
					value := a[0]
					if n == "append" {
						x, err := percentIndex(vm, value)
						if err != nil {
							return nil, err
						}
						by, err := byteValue(x)
						if err != nil {
							return nil, err
						}
						value = []byte{by}
					}
					end := int64(len(b.data))
					return None, vm.bufferSet(b, &Slice{Lower: end, Upper: end, Step: None}, value, false)
				}
			}), nil
		}
		if bytesMethod(b.data, n) != nil {
			return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
				x, err := bytesMethod(b.data, n)(vm, a, k)
				if data, ok := x.([]byte); ok {
					x = &ByteArray{data: data}
				}
				return x, err
			}), nil
		}
	}
	return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'", typeName(o), n)
}

func intFromBytes(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("from_bytes", args, 1, 2); err != nil {
		return nil, err
	}
	order := "big"
	signed := false
	if len(args) == 2 {
		v, err := wantStr("byteorder", args[1])
		if err != nil {
			return nil, err
		}
		order = v
	}
	for _, k := range kw {
		switch k.Name {
		case "byteorder":
			if len(args) == 2 {
				return nil, newError("TypeError", "multiple values for byteorder")
			}
			v, err := wantStr("byteorder", k.Val)
			if err != nil {
				return nil, err
			}
			order = v
		case "signed":
			v, err := vm.truth(k.Val)
			if err != nil {
				return nil, err
			}
			signed = v
		default:
			return nil, newErrorf("TypeError", "invalid keyword argument '%s'", k.Name)
		}
	}
	if order != "big" && order != "little" {
		return nil, newError("ValueError", "byteorder must be either 'little' or 'big'")
	}
	if isIntKind(rawPrim(args[0])) {
		return nil, newErrorf("TypeError", "cannot convert '%s' object to bytes", typeName(args[0]))
	}
	v, err := ctorBytes(vm, args[:1], nil)
	if err != nil {
		return nil, err
	}
	data := v.([]byte)
	if order == "little" {
		for i, j := 0, len(data)-1; i < j; i, j = i+1, j-1 {
			data[i], data[j] = data[j], data[i]
		}
	}
	z := new(big.Int).SetBytes(data)
	if signed && len(data) > 0 && data[0]&128 != 0 {
		z.Sub(z, new(big.Int).Lsh(big.NewInt(1), uint(8*len(data))))
	}
	if z.IsInt64() {
		return z.Int64(), nil
	}
	return z, nil
}

func intToBytes(vm *VM, value Value, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("to_bytes", args, 0, 2); err != nil {
		return nil, err
	}
	length := Value(int64(1))
	orderValue := Value("big")
	signed := false
	if len(args) > 0 {
		length = args[0]
	}
	if len(args) > 1 {
		orderValue = args[1]
	}
	for _, k := range kw {
		switch k.Name {
		case "length":
			if len(args) > 0 {
				return nil, newError("TypeError", "multiple values for length")
			}
			length = k.Val
		case "byteorder":
			if len(args) > 1 {
				return nil, newError("TypeError", "multiple values for byteorder")
			}
			orderValue = k.Val
		case "signed":
			v, err := vm.truth(k.Val)
			if err != nil {
				return nil, err
			}
			signed = v
		default:
			return nil, newErrorf("TypeError", "invalid keyword argument '%s'", k.Name)
		}
	}
	iv, err := percentIndex(vm, length)
	if err != nil {
		return nil, err
	}
	if z, ok := iv.(*big.Int); ok {
		if !z.IsInt64() {
			return nil, newError("OverflowError", "Python int too large to convert to C ssize_t")
		}
		iv = z.Int64()
	}
	n, err := wantInt("length", iv)
	if err != nil {
		return nil, err
	}
	if n < 0 {
		return nil, newError("ValueError", "length argument must be non-negative")
	}
	if uint64(n) > uint64(^uint(0)>>1)/8 {
		return nil, newError("OverflowError", "Python int too large to convert to C ssize_t")
	}
	order, err := wantStr("byteorder", orderValue)
	if err != nil {
		return nil, err
	}
	if order != "big" && order != "little" {
		return nil, newError("ValueError", "byteorder must be either 'little' or 'big'")
	}
	if integer, ok := asInt(rawPrim(value)); ok {
		if integer < 0 && !signed {
			return nil, newError("OverflowError", "can't convert negative int to unsigned")
		}
		if n == 0 {
			if integer == 0 || (signed && integer == -1) {
				return []byte{}, nil
			}
			return nil, newError("OverflowError", "int too big to convert")
		}
		if n < 8 {
			bits := uint(n * 8)
			if signed {
				limit := int64(1) << (bits - 1)
				if integer < -limit || integer >= limit {
					return nil, newError("OverflowError", "int too big to convert")
				}
			} else if uint64(integer) >= uint64(1)<<bits {
				return nil, newError("OverflowError", "int too big to convert")
			}
		}
		out := make([]byte, int(n))
		for i := range out {
			if order == "little" {
				out[i] = byte(integer)
			} else {
				out[len(out)-1-i] = byte(integer)
			}
			integer >>= 8
		}
		return out, nil
	}
	var z big.Int
	b, ok := rawPrim(value).(*big.Int)
	if !ok {
		return nil, newError("TypeError", "to_bytes requires an integer")
	}
	z.Set(b)
	if z.Sign() < 0 && !signed {
		return nil, newError("OverflowError", "can't convert negative int to unsigned")
	}
	bits := int(n) * 8
	if n == 0 {
		if z.Sign() == 0 || (signed && z.Cmp(big.NewInt(-1)) == 0) {
			return []byte{}, nil
		}
		return nil, newError("OverflowError", "int too big to convert")
	}
	if z.Sign() < 0 {
		var complement big.Int
		complement.Not(&z)
		if complement.BitLen() > bits-1 {
			return nil, newError("OverflowError", "int too big to convert")
		}
		z.Add(&z, new(big.Int).Lsh(big.NewInt(1), uint(bits)))
	} else {
		limit := bits
		if signed {
			limit--
		}
		if z.BitLen() > limit {
			return nil, newError("OverflowError", "int too big to convert")
		}
	}
	out := make([]byte, int(n))
	z.FillBytes(out)
	if order == "little" {
		for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
			out[i], out[j] = out[j], out[i]
		}
	}
	return out, nil
}

func (vm *VM) bufferSlice(sl *Slice) (*Slice, error) {
	convert := func(value Value) (Value, error) {
		if _, ok := value.(NoneType); ok {
			return value, nil
		}
		n, err := percentIndex(vm, value)
		if err != nil {
			return nil, err
		}
		max := int64(^uint(0) >> 1)
		min := -max
		if z, ok := n.(*big.Int); ok {
			if !z.IsInt64() {
				if z.Sign() < 0 {
					return min, nil
				}
				return max, nil
			}
			n = z.Int64()
		}
		i, _ := asInt(n)
		if i > max {
			i = max
		}
		if i < min {
			i = min
		}
		return i, nil
	}
	step, err := convert(sl.Step)
	if err != nil {
		return nil, err
	}
	if n, ok := asInt(step); ok && n == 0 {
		return nil, newError("ValueError", "slice step cannot be zero")
	}
	start, err := convert(sl.Lower)
	if err != nil {
		return nil, err
	}
	stop, err := convert(sl.Upper)
	if err != nil {
		return nil, err
	}
	return &Slice{Lower: start, Upper: stop, Step: step}, nil
}

func (vm *VM) bufferSliceIndices(sl *Slice, length int) (int, int, int, error) {
	normalized, err := vm.bufferSlice(sl)
	if err != nil {
		return 0, 0, 0, err
	}
	return sliceIndices(normalized, length)
}

func memoryViewHashBytes(v *MemoryView) ([]byte, error) {
	if v.hashReady {
		return v.hashBytes, nil
	}
	if err := v.check(); err != nil {
		return nil, err
	}
	if !v.readonly {
		return nil, newError("ValueError", "cannot hash writable memoryview object")
	}
	code, _, _ := nativeViewFormat(v.format)
	if !viewByteFormat(code) {
		return nil, newError("ValueError", "memoryview: hashing is restricted to formats 'B', 'b' or 'c'")
	}
	if _, ok := v.owner.(*ByteArray); ok {
		return nil, newError("TypeError", "unhashable type: 'bytearray'")
	}
	data, err := bufferBytes(v)
	if err != nil {
		return nil, err
	}
	v.hashBytes, v.hashReady = data, true
	return data, nil
}
