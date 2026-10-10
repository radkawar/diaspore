package interp

import (
	"encoding/binary"
	"encoding/hex"
	"math"
	"math/big"
	"runtime"
	"strconv"
)

// Native formats use the runtime's C ABI, not the byte order of the bundle.
func nativeViewFormat(format string) (byte, int, bool) {
	code := format
	if len(code) == 2 && code[0] == '@' {
		code = code[1:]
	}
	if len(code) != 1 {
		return 0, 0, false
	}
	size := 0
	switch code[0] {
	case 'b', 'B', 'c', '?':
		size = 1
	case 'h', 'H', 'e':
		size = 2
	case 'i', 'I', 'f':
		size = 4
	case 'l', 'L':
		size = strconv.IntSize / 8
		if runtime.GOOS == "windows" {
			size = 4
		}
	case 'q', 'Q', 'd':
		size = 8
	case 'n', 'N', 'P':
		size = strconv.IntSize / 8
	default:
		return 0, 0, false
	}
	return code[0], size, true
}

func viewByteFormat(code byte) bool { return code == 'b' || code == 'B' || code == 'c' }

func (v *MemoryView) byteLen() int {
	n := v.itemsize
	for _, size := range v.shape {
		n *= size
	}
	return n
}

func (v *MemoryView) cContiguous() bool {
	if v.byteLen() == 0 {
		return true
	}
	stride := v.itemsize
	for i := len(v.shape) - 1; i >= 0; i-- {
		if v.shape[i] > 1 && v.strides[i] != stride {
			return false
		}
		stride *= v.shape[i]
	}
	return true
}

func (v *MemoryView) fContiguous() bool {
	if v.byteLen() == 0 {
		return true
	}
	stride := v.itemsize
	for i, size := range v.shape {
		if size > 1 && v.strides[i] != stride {
			return false
		}
		stride *= size
	}
	return true
}

func (v *MemoryView) flatOffset(index int, fortran bool) int {
	offset := v.start
	if fortran {
		for i, size := range v.shape {
			offset += (index % size) * v.strides[i]
			index /= size
		}
	} else {
		for i := len(v.shape) - 1; i >= 0; i-- {
			offset += (index % v.shape[i]) * v.strides[i]
			index /= v.shape[i]
		}
	}
	return offset
}

func (v *MemoryView) load(offset int) Value {
	code, size, _ := nativeViewFormat(v.format)
	data := v.data[offset : offset+size]
	var bits uint64
	switch size {
	case 1:
		bits = uint64(data[0])
	case 2:
		bits = uint64(binary.NativeEndian.Uint16(data))
	case 4:
		bits = uint64(binary.NativeEndian.Uint32(data))
	case 8:
		bits = binary.NativeEndian.Uint64(data)
	}
	switch code {
	case 'c':
		return []byte{byte(bits)}
	case '?':
		return bits != 0
	case 'f':
		return float64(math.Float32frombits(uint32(bits)))
	case 'e':
		return unpackViewHalf(uint16(bits))
	case 'd':
		return math.Float64frombits(bits)
	case 'b', 'h', 'i', 'l', 'q', 'n':
		return int64(bits<<uint(64-size*8)) >> uint(64-size*8)
	default:
		if bits > math.MaxInt64 {
			return new(big.Int).SetUint64(bits)
		}
		return int64(bits)
	}
}

func (vm *VM) memoryViewStore(v *MemoryView, offset int, value Value) (err error) {
	defer func() {
		if err == nil {
			return
		}
		code, _, _ := nativeViewFormat(v.format)
		if code == '?' {
			return
		}
		if _, ok := raisedAs(err, "TypeError"); ok {
			err = newErrorf("TypeError", "memoryview: invalid type for format '%s'", v.format)
		}
		if _, ok := raisedAs(err, "OverflowError"); ok {
			err = newErrorf("ValueError", "memoryview: invalid value for format '%s'", v.format)
		}
	}()
	code, size, _ := nativeViewFormat(v.format)
	var bits uint64
	switch code {
	case 'c':
		b, ok := rawPrim(value).([]byte)
		if !ok {
			return newError("TypeError", "memoryview: invalid type for format 'c'")
		}
		if len(b) != 1 {
			return newError("ValueError", "memoryview: invalid value for format 'c'")
		}
		bits = uint64(b[0])
	case '?':
		truth, err := vm.truth(value)
		if err != nil {
			return err
		}
		if truth {
			bits = 1
		}
	case 'e', 'f', 'd':
		f, ok := asFloat(rawPrim(value))
		if !ok {
			inst, isInstance := value.(*Instance)
			if !isInstance {
				return newError("TypeError", "memoryview: invalid type for format")
			}
			if _, found := inst.Class.Lookup("__float__"); found {
				x, err := vm.callDunder(inst, "__float__", nil)
				if err != nil {
					return err
				}
				f, ok = rawPrim(x).(float64)
				if !ok {
					return newError("TypeError", "__float__ returned non-float")
				}
			} else {
				x, err := percentIndex(vm, value)
				if err != nil {
					return err
				}
				f, _ = asFloat(x)
				if math.IsInf(f, 0) {
					return newError("OverflowError", "int too large to convert to float")
				}
			}
		}
		if z, ok := rawPrim(value).(*big.Int); ok && math.IsInf(f, 0) && z.Sign() != 0 {
			return newError("OverflowError", "int too large to convert to float")
		}
		switch code {
		case 'e':
			half, err := packViewHalf(f)
			if err != nil {
				return err
			}
			bits = uint64(half)
		case 'f':
			bits = uint64(math.Float32bits(float32(f)))
		case 'd':
			bits = math.Float64bits(f)
		}
	default:
		x := rawPrim(value)
		if code == 'P' {
			if !isIntKind(x) {
				return newError("TypeError", "an integer is required")
			}
		} else {
			var err error
			x, err = percentIndex(vm, value)
			if err != nil {
				return err
			}
		}
		signed := code == 'b' || code == 'h' || code == 'i' || code == 'l' || code == 'q' || code == 'n'
		if code == 'P' {
			if z, ok := x.(*big.Int); ok {
				signed = z.Sign() < 0
			} else {
				n, _ := asInt(x)
				signed = n < 0
			}
		}
		valid := true
		if z, ok := x.(*big.Int); ok {
			if signed {
				valid = z.IsInt64()
				if valid {
					n := z.Int64()
					bits = uint64(n)
					if size < 8 {
						limit := int64(1) << uint(size*8-1)
						valid = n >= -limit && n < limit
					}
				}
			} else {
				valid = z.Sign() >= 0 && z.BitLen() <= size*8
				if valid {
					bits = z.Uint64()
				}
			}
		} else {
			n, _ := asInt(x)
			bits = uint64(n)
			if signed {
				if size < 8 {
					limit := int64(1) << uint(size*8-1)
					valid = n >= -limit && n < limit
				}
			} else {
				valid = n >= 0
				if size < 8 {
					valid = valid && bits < (uint64(1)<<uint(size*8))
				}
			}
		}
		if !valid {
			return newErrorf("ValueError", "memoryview: invalid value for format '%s'", v.format)
		}
	}
	if err := v.check(); err != nil {
		return err
	}
	data := v.data[offset : offset+size]
	switch size {
	case 1:
		data[0] = byte(bits)
	case 2:
		binary.NativeEndian.PutUint16(data, uint16(bits))
	case 4:
		binary.NativeEndian.PutUint32(data, uint32(bits))
	case 8:
		binary.NativeEndian.PutUint64(data, bits)
	}
	return nil
}

func (vm *VM) memoryViewOffset(v *MemoryView, key Value) (int, error) {
	if len(v.shape) == 0 {
		if tuple, ok := key.(*Tuple); ok && len(tuple.Items) == 0 {
			return v.start, nil
		}
		return 0, newError("TypeError", "invalid indexing of 0-dim memory")
	}
	if tuple, ok := key.(*Tuple); ok {
		slices := 0
		for _, item := range tuple.Items {
			if _, ok := item.(*Slice); ok {
				slices++
				continue
			}
			if !isBufferIndex(item) {
				return 0, newError("TypeError", "memoryview: invalid slice key")
			}
		}
		if slices > 0 {
			if slices == len(tuple.Items) {
				return 0, newError("NotImplementedError", "multi-dimensional slicing is not implemented")
			}
			return 0, newError("TypeError", "memoryview: invalid slice key")
		}
		if len(tuple.Items) > len(v.shape) {
			return 0, newErrorf("TypeError", "cannot index %d-dimension view with %d-element tuple", len(v.shape), len(tuple.Items))
		}
		if len(tuple.Items) < len(v.shape) {
			return 0, newError("NotImplementedError", "sub-views are not implemented")
		}
		offset := v.start
		for dim, item := range tuple.Items {
			i, err := vm.bufferIndex(item, v.shape[dim])
			if err != nil {
				return 0, err
			}
			offset += i * v.strides[dim]
		}
		return offset, v.check()
	}
	if !isBufferIndex(key) {
		return 0, newError("TypeError", "memoryview: invalid slice key")
	}
	index, err := vm.bufferIndex(key, v.length)
	if err != nil {
		return 0, err
	}
	if len(v.shape) > 1 {
		return 0, newError("NotImplementedError", "multi-dimensional sub-views are not implemented")
	}
	return v.start + index*v.strides[0], v.check()
}

func (vm *VM) memoryViewGet(v *MemoryView, key Value) (Value, error) {
	if err := v.check(); err != nil {
		return nil, err
	}
	defer runtime.KeepAlive(v)
	if _, ok := key.(EllipsisType); ok && len(v.shape) == 0 {
		return v, nil
	}
	if sl, ok := key.(*Slice); ok {
		if len(v.shape) == 0 {
			return nil, newError("TypeError", "invalid indexing of 0-dim memory")
		}
		start, stop, step, err := vm.bufferSliceIndices(sl, v.length)
		if err != nil {
			return nil, err
		}
		if err := v.check(); err != nil {
			return nil, err
		}
		x := *v
		x.shape = append([]int(nil), v.shape...)
		x.strides = append([]int(nil), v.strides...)
		x.length = sliceCount(start, stop, step)
		x.shape[0] = x.length
		x.start = v.start + start*v.strides[0]
		x.step = v.strides[0] * step
		x.strides[0] = x.step
		return retainMemoryView(&x), nil
	}
	offset, err := vm.memoryViewOffset(v, key)
	if err != nil {
		return nil, err
	}
	return v.load(offset), nil
}

func (vm *VM) memoryViewSet(v *MemoryView, key, value Value, del bool) error {
	if err := v.check(); err != nil {
		return err
	}
	defer runtime.KeepAlive(v)
	if v.readonly {
		return newError("TypeError", "cannot modify read-only memory")
	}
	if del {
		return newError("TypeError", "cannot delete memory")
	}
	if _, ok := key.(EllipsisType); ok && len(v.shape) == 0 {
		return vm.memoryViewStore(v, v.start, value)
	}
	if sl, ok := key.(*Slice); ok {
		if len(v.shape) == 0 {
			return newError("TypeError", "invalid indexing of 0-dim memory")
		}
		if len(v.shape) != 1 {
			return newError("NotImplementedError", "memoryview slice assignments are currently restricted to ndim = 1")
		}
		start, stop, step, err := vm.bufferSliceIndices(sl, v.length)
		if err != nil {
			return err
		}
		count := sliceCount(start, stop, step)
		if err := v.check(); err != nil {
			return err
		}
		var source []byte
		switch rhs := rawPrim(value).(type) {
		case *MemoryView:
			if err := rhs.check(); err != nil {
				return err
			}
			if !equivalentViewFormat(rhs.format, v.format) || len(rhs.shape) != 1 || rhs.length != count {
				return newError("ValueError", "memoryview assignment: lvalue and rvalue have different structures")
			}
			source, err = bufferBytes(rhs)
			if err != nil {
				return err
			}
		case []byte:
			if !equivalentViewFormat(v.format, "B") || len(rhs) != count {
				return newError("ValueError", "memoryview assignment: lvalue and rvalue have different structures")
			}
			source = rhs
		case *ByteArray:
			if !equivalentViewFormat(v.format, "B") || len(rhs.data) != count {
				return newError("ValueError", "memoryview assignment: lvalue and rvalue have different structures")
			}
			source = rhs.data
		default:
			return newErrorf("TypeError", "a bytes-like object is required, not '%s'", typeName(value))
		}
		if count == 0 {
			return nil
		}
		offset := v.start + start*v.strides[0]
		stride := step * v.strides[0]
		if stride == v.itemsize {
			copy(v.data[offset:offset+len(source)], source)
			return nil
		}
		// A strided destination may overwrite a later source element.
		if sameBufferOwner(v.owner, value) {
			source = append([]byte(nil), source...)
		}
		for i := range count {
			copy(v.data[offset+i*stride:offset+i*stride+v.itemsize], source[i*v.itemsize:(i+1)*v.itemsize])
		}
		return nil
	}
	offset, err := vm.memoryViewOffset(v, key)
	if err != nil {
		return err
	}
	return vm.memoryViewStore(v, offset, value)
}

func sameBufferOwner(owner, value Value) bool {
	if v, ok := rawPrim(value).(*MemoryView); ok {
		value = v.owner
	}
	if b, ok := owner.(*ByteArray); ok {
		other, ok := rawPrim(value).(*ByteArray)
		return ok && b == other
	}
	return false
}

func (vm *VM) memoryViewCast(v *MemoryView, args []Value, kw []KwArg) (Value, error) {
	if err := v.check(); err != nil {
		return nil, err
	}
	if err := argCount("cast", args, 1, 2); err != nil {
		return nil, err
	}
	shapeValue := Value(nil)
	if len(args) == 2 {
		shapeValue = args[1]
	}
	for _, k := range kw {
		if k.Name != "shape" {
			return nil, newErrorf("TypeError", "'%s' is an invalid keyword argument for cast()", k.Name)
		}
		if shapeValue != nil {
			return nil, newError("TypeError", "shape given by name and position")
		}
		shapeValue = k.Val
	}
	format, err := wantStr("format", args[0])
	if err != nil {
		return nil, err
	}
	if !v.cContiguous() {
		return nil, newError("TypeError", "memoryview: casts are restricted to C-contiguous views")
	}
	if shapeValue != nil || len(v.shape) != 1 {
		for _, n := range v.shape {
			if n == 0 {
				return nil, newError("TypeError", "memoryview: cannot cast view with zeros in shape or strides")
			}
		}
	}
	code, size, ok := nativeViewFormat(format)
	if !ok {
		return nil, newError("ValueError", "memoryview: destination format must be a native single character format prefixed with an optional '@'")
	}
	oldCode, _, _ := nativeViewFormat(v.format)
	if !viewByteFormat(oldCode) && !viewByteFormat(code) {
		return nil, newError("TypeError", "memoryview: cannot cast between two non-byte formats")
	}
	nbytes := v.byteLen()
	if nbytes%size != 0 {
		return nil, newError("TypeError", "memoryview: length is not a multiple of itemsize")
	}
	shape := []int{nbytes / size}
	if shapeValue != nil {
		var values []Value
		switch s := shapeValue.(type) {
		case *List:
			values = s.Items
		case *Tuple:
			values = s.Items
		default:
			return nil, newError("TypeError", "shape must be a list or a tuple")
		}
		if len(values) > 64 {
			return nil, newError("ValueError", "memoryview: number of dimensions must not exceed 64")
		}
		shape = make([]int, len(values))
		product := 1
		for i, value := range values {
			// The shape API requires integers, not arbitrary __index__ providers.
			if !isIntKind(rawPrim(value)) {
				return nil, newError("TypeError", "memoryview.cast(): elements of shape must be integers")
			}
			if z, ok := rawPrim(value).(*big.Int); ok {
				if !z.IsInt64() {
					return nil, newError("OverflowError", "Python int too large to convert to C ssize_t")
				}
				value = z.Int64()
			}
			n, err := wantInt("shape", rawPrim(value))
			if err != nil {
				return nil, err
			}
			if n <= 0 {
				return nil, newError("ValueError", "memoryview.cast(): elements of shape must be integers > 0")
			}
			if uint64(n) > uint64(^uint(0)>>1) {
				return nil, newError("OverflowError", "Python int too large to convert to C ssize_t")
			}
			if int(n) > int(^uint(0)>>1)/size/product {
				return nil, newError("ValueError", "memoryview.cast(): product(shape) > SSIZE_MAX")
			}
			shape[i] = int(n)
			product *= int(n)
		}
		if product != nbytes/size {
			return nil, newError("TypeError", "memoryview: product(shape) * itemsize != buffer size")
		}
	}
	if len(v.shape) != 1 && len(shape) != 1 {
		return nil, newError("TypeError", "memoryview: cast must be 1D -> ND or ND -> 1D")
	}
	strides := make([]int, len(shape))
	stride := size
	for i := len(shape) - 1; i >= 0; i-- {
		strides[i] = stride
		stride *= shape[i]
	}
	x := *v
	x.format = format
	x.itemsize = size
	x.shape = shape
	x.strides = strides
	x.length = 1
	x.step = size
	if len(shape) > 0 {
		x.length = shape[0]
		x.step = strides[0]
	}
	return retainMemoryView(&x), nil
}

func intTuple(values []int) *Tuple {
	items := make([]Value, len(values))
	for i, n := range values {
		items[i] = int64(n)
	}
	return &Tuple{Items: items}
}

func (v *MemoryView) listAt(dim, offset int) Value {
	if dim == len(v.shape) {
		return v.load(offset)
	}
	items := make([]Value, v.shape[dim])
	for i := range items {
		items[i] = v.listAt(dim+1, offset+i*v.strides[dim])
	}
	return &List{Items: items}
}

func (vm *VM) memoryViewAttr(v *MemoryView, n string) (Value, error) {
	if n == "release" {
		return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
			if err := noArgs(n, a, k); err != nil {
				return nil, err
			}
			v.release()
			return None, nil
		}), nil
	}
	switch n {
	case "obj", "readonly", "c_contiguous", "f_contiguous", "contiguous", "nbytes", "itemsize", "ndim", "format", "shape", "strides", "suboffsets":
		if err := v.check(); err != nil {
			return nil, err
		}
	}
	switch n {
	case "obj":
		return v.owner, nil
	case "readonly":
		return v.readonly, nil
	case "c_contiguous":
		return v.cContiguous(), nil
	case "f_contiguous":
		return v.fContiguous(), nil
	case "contiguous":
		return v.cContiguous() || v.fContiguous(), nil
	case "nbytes":
		return int64(v.byteLen()), nil
	case "itemsize":
		return int64(v.itemsize), nil
	case "ndim":
		return int64(len(v.shape)), nil
	case "format":
		return v.format, nil
	case "shape":
		return intTuple(v.shape), nil
	case "strides":
		return intTuple(v.strides), nil
	case "suboffsets":
		return &Tuple{}, nil
	case "cast":
		return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) { return vm.memoryViewCast(v, a, k) }), nil
	case "__enter__":
		return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
			if err := noArgs(n, a, k); err != nil {
				return nil, err
			}
			return v, v.check()
		}), nil
	case "__exit__":
		return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
			if err := noKw(n, k); err != nil {
				return nil, err
			}
			if err := argCount(n, a, 3, 3); err != nil {
				return nil, err
			}
			v.release()
			return None, nil
		}), nil
	case "toreadonly":
		return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
			if err := noArgs(n, a, k); err != nil {
				return nil, err
			}
			x, err := ctorMemoryView(vm, []Value{v}, nil)
			if err != nil {
				return nil, err
			}
			x.(*MemoryView).readonly = true
			return x, nil
		}), nil
	case "tolist":
		return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
			if err := noArgs(n, a, k); err != nil {
				return nil, err
			}
			if err := v.check(); err != nil {
				return nil, err
			}
			defer runtime.KeepAlive(v)
			return v.listAt(0, v.start), nil
		}), nil
	case "tobytes":
		return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
			if err := v.check(); err != nil {
				return nil, err
			}
			defer runtime.KeepAlive(v)
			if err := argCount(n, a, 0, 1); err != nil {
				return nil, err
			}
			order := Value("C")
			if len(a) > 0 {
				order = a[0]
			}
			for _, key := range k {
				if key.Name != "order" {
					return nil, newErrorf("TypeError", "'%s' is an invalid keyword argument for tobytes()", key.Name)
				}
				if len(a) > 0 {
					return nil, newError("TypeError", "order given by name and position")
				}
				order = key.Val
			}
			if _, ok := order.(NoneType); ok {
				order = "C"
			}
			text, err := wantStr("order", order)
			if err != nil {
				return nil, err
			}
			if text != "C" && text != "F" && text != "A" {
				return nil, newError("ValueError", "order must be 'C', 'F' or 'A'")
			}
			if text != "F" || v.fContiguous() {
				data, err := bufferBytes(v)
				if err != nil {
					return nil, err
				}
				if !v.cContiguous() {
					return data, nil
				}
				return append([]byte{}, data...), nil
			}
			out := make([]byte, v.byteLen())
			for i := range len(out) / v.itemsize {
				offset := v.flatOffset(i, true)
				copy(out[i*v.itemsize:], v.data[offset:offset+v.itemsize])
			}
			return out, nil
		}), nil
	case "hex":
		return bi(n, func(vm *VM, a []Value, k []KwArg) (Value, error) {
			data, err := bufferBytes(v)
			if err != nil {
				return nil, err
			}
			return bufferHex(vm, data, a, k)
		}), nil
	}
	return nil, newErrorf("AttributeError", "'memoryview' object has no attribute '%s'", n)
}

func bufferHex(vm *VM, data []byte, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("hex", args, 0, 2); err != nil {
		return nil, err
	}
	sepValue := Value(nil)
	groupValue := Value(int64(1))
	if len(args) > 0 {
		sepValue = args[0]
	}
	if len(args) > 1 {
		groupValue = args[1]
	}
	for _, k := range kw {
		switch k.Name {
		case "sep":
			if len(args) > 0 {
				return nil, newError("TypeError", "multiple values for sep")
			}
			sepValue = k.Val
		case "bytes_per_sep":
			if len(args) > 1 {
				return nil, newError("TypeError", "multiple values for bytes_per_sep")
			}
			groupValue = k.Val
		default:
			return nil, newErrorf("TypeError", "invalid keyword argument '%s'", k.Name)
		}
	}
	if sepValue == nil {
		return hex.EncodeToString(data), nil
	}
	var sep byte
	switch s := sepValue.(type) {
	case string:
		if len(s) != 1 {
			return nil, newError("ValueError", "sep must be length 1.")
		}
		sep = s[0]
	case []byte:
		if len(s) != 1 {
			return nil, newError("ValueError", "sep must be length 1.")
		}
		sep = s[0]
	default:
		return nil, newError("TypeError", "sep must be str or bytes.")
	}
	if sep > 127 {
		return nil, newError("ValueError", "sep must be ASCII.")
	}
	x, err := percentIndex(vm, groupValue)
	if err != nil {
		return nil, err
	}
	n, err := wantInt("bytes_per_sep", x)
	if err != nil {
		return nil, err
	}
	if n > math.MaxInt32 || n < math.MinInt32 {
		return nil, newError("OverflowError", "Python int too large to convert to C int")
	}
	if n == 0 || len(data) == 0 {
		return hex.EncodeToString(data), nil
	}
	group := n
	if group < 0 {
		group = -group
	}
	count := (len(data) - 1) / int(group)
	out := make([]byte, len(data)*2+count)
	const digits = "0123456789abcdef"
	dst := 0
	for i, b := range data {
		if i > 0 && ((n > 0 && (len(data)-i)%int(group) == 0) || (n < 0 && i%int(group) == 0)) {
			out[dst] = sep
			dst++
		}
		out[dst], out[dst+1] = digits[b>>4], digits[b&15]
		dst += 2
	}
	return string(out), nil
}

func (vm *VM) memoryViewEqual(left, right Value) (bool, error) {
	view := func(value Value) (*MemoryView, bool) {
		switch b := rawPrim(value).(type) {
		case *MemoryView:
			return b, true
		case *ByteArray:
			return &MemoryView{owner: b, data: b.data, length: len(b.data), step: 1, itemsize: 1, format: "B", shape: []int{len(b.data)}, strides: []int{1}}, true
		case []byte:
			return &MemoryView{owner: b, data: b, length: len(b), step: 1, itemsize: 1, format: "B", shape: []int{len(b)}, strides: []int{1}, readonly: true}, true
		}
		return nil, false
	}
	a, ok := view(left)
	if !ok {
		return false, nil
	}
	b, ok := view(right)
	if !ok {
		return false, nil
	}
	defer runtime.KeepAlive(a)
	defer runtime.KeepAlive(b)
	if a.released || b.released {
		return a == b, nil
	}
	if len(a.shape) != len(b.shape) {
		return false, nil
	}
	for i, n := range a.shape {
		if b.shape[i] != n {
			return false, nil
		}
	}
	for i := range a.byteLen() / a.itemsize {
		same, err := vm.eq(a.load(a.flatOffset(i, false)), b.load(b.flatOffset(i, false)))
		if err != nil || !same {
			return false, err
		}
	}
	return true, nil
}

func equivalentViewFormat(a, b string) bool {
	ac, as, _ := nativeViewFormat(a)
	bc, bs, _ := nativeViewFormat(b)
	return ac == bc && as == bs
}

func isBufferIndex(value Value) bool {
	if isIntKind(rawPrim(value)) {
		return true
	}
	if inst, ok := value.(*Instance); ok {
		_, found := inst.Class.Lookup("__index__")
		return found
	}
	return false
}

func unpackViewHalf(bits uint16) float64 {
	sign := 1.0
	if bits&0x8000 != 0 {
		sign = -1
	}
	exponent := int((bits >> 10) & 31)
	fraction := int(bits & 1023)
	if exponent == 31 {
		if fraction == 0 {
			return math.Inf(int(sign))
		}
		return math.Float64frombits(uint64(bits&0x8000)<<48 | 0x7ff8000000000000)
	}
	if exponent == 0 {
		return math.Copysign(math.Ldexp(float64(fraction), -24), sign)
	}
	return sign * math.Ldexp(float64(1024+fraction), exponent-25)
}

func packViewHalf(value float64) (uint16, error) {
	sign := uint16(0)
	if math.Signbit(value) {
		sign = 0x8000
	}
	f := math.Abs(value)
	if math.IsInf(f, 0) {
		return sign | 0x7c00, nil
	}
	if math.IsNaN(f) {
		return sign | 0x7e00, nil
	}
	if f < math.Ldexp(1, -14) {
		return sign | uint16(math.RoundToEven(math.Ldexp(f, 24))), nil
	}
	_, exponent := math.Frexp(f)
	if exponent > 16 {
		return 0, newError("ValueError", "memoryview: invalid value for format 'e'")
	}
	fraction := int(math.RoundToEven(math.Ldexp(f, 11-exponent)))
	if fraction == 2048 {
		fraction = 1024
		exponent++
	}
	if exponent > 16 {
		return 0, newError("ValueError", "memoryview: invalid value for format 'e'")
	}
	return sign | uint16(exponent+14)<<10 | uint16(fraction-1024), nil
}
