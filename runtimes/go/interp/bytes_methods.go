package interp

var bytesMakeTransBuiltin = bi("maketrans", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("maketrans", kw); err != nil {
		return nil, err
	}
	if err := argCount("maketrans", args, 2, 2); err != nil {
		return nil, err
	}
	from, err := contiguousBufferBytes(rawPrim(args[0]))
	if err != nil {
		return nil, err
	}
	to, err := contiguousBufferBytes(rawPrim(args[1]))
	if err != nil {
		return nil, err
	}
	if len(from) != len(to) {
		return nil, newError("ValueError", "maketrans arguments must have same length")
	}
	table := make([]byte, 256)
	for i := range table {
		table[i] = byte(i)
	}
	for i, value := range from {
		table[value] = to[i]
	}
	return table, nil
})

func bytesTranslate(vm *VM, source []byte, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("translate", args, 1, 2); err != nil {
		return nil, err
	}
	var deleted Value = []byte{}
	if len(args) == 2 {
		deleted = args[1]
	}
	seenDelete := len(args) == 2
	for _, keyword := range kw {
		if keyword.Name != "delete" {
			return nil, newErrorf("TypeError", "'%s' is an invalid keyword argument for translate()", keyword.Name)
		}
		if seenDelete {
			return nil, newError("TypeError", "argument for translate() given by name and position")
		}
		seenDelete, deleted = true, keyword.Val
	}
	var table []byte
	if _, none := args[0].(NoneType); !none {
		var err error
		table, err = contiguousBufferBytes(rawPrim(args[0]))
		if err != nil {
			return nil, err
		}
		if len(table) != 256 {
			return nil, newError("ValueError", "translation table must be 256 characters long")
		}
	}
	remove, err := contiguousBufferBytes(rawPrim(deleted))
	if err != nil {
		return nil, err
	}
	var deletion [256]bool
	for _, value := range remove {
		deletion[value] = true
	}
	count := 0
	for _, value := range source {
		if !deletion[value] {
			count++
		}
	}
	output := make([]byte, 0, count)
	for _, value := range source {
		if deletion[value] {
			continue
		}
		if table != nil {
			value = table[value]
		}
		output = append(output, value)
	}
	return output, nil
}

func bytesJoin(vm *VM, separator []byte, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("join", kw); err != nil {
		return nil, err
	}
	if err := argCount("join", args, 1, 1); err != nil {
		return nil, err
	}
	items, err := vm.iterToSlice(args[0])
	if err != nil {
		return nil, err
	}
	parts := make([][]byte, len(items))
	total := 0
	maximum := int(^uint(0) >> 1)
	for i, item := range items {
		part, err := contiguousBufferBytes(rawPrim(item))
		if err != nil {
			if _, wrongType := raisedAs(err, "TypeError"); wrongType {
				return nil, newErrorf("TypeError", "sequence item %d: expected a bytes-like object, %s found", i, typeName(item))
			}
			return nil, err
		}
		if i != 0 {
			if len(separator) > maximum-total {
				return nil, newError("OverflowError", "join() result is too long for a Python bytes")
			}
			total += len(separator)
		}
		if len(part) > maximum-total {
			return nil, newError("OverflowError", "join() result is too long for a Python bytes")
		}
		total += len(part)
		parts[i] = part
	}
	if err := vm.fuelN(int64(total)); err != nil {
		return nil, err
	}
	output := make([]byte, total)
	position := 0
	for i, part := range parts {
		if i != 0 {
			position += copy(output[position:], separator)
		}
		position += copy(output[position:], part)
	}
	return output, nil
}

func bytesPad(vm *VM, source []byte, method string, args []Value, kw []KwArg) (Value, error) {
	if err := noKw(method, kw); err != nil {
		return nil, err
	}
	if err := argCount(method, args, 1, 2); err != nil {
		return nil, err
	}
	widthValue, err := percentIndex(vm, args[0])
	if err != nil {
		return nil, err
	}
	width, small := asInt(widthValue)
	if !small {
		return nil, newError("OverflowError", "Python int too large to convert to C ssize_t")
	}
	fill := byte(' ')
	if len(args) == 2 {
		data, err := contiguousBufferBytes(rawPrim(args[1]))
		if err != nil {
			return nil, err
		}
		if len(data) != 1 {
			return nil, newErrorf("TypeError", "%s() argument 2 must be a byte string of length 1, not %s", method, typeName(args[1]))
		}
		fill = data[0]
	}
	if width <= int64(len(source)) {
		// Bytearray methods reuse this helper and require an independent result.
		return append([]byte{}, source...), nil
	}
	if uint64(width) > uint64(int(^uint(0)>>1)) {
		return nil, newError("OverflowError", "Python int too large to convert to C ssize_t")
	}
	if err := vm.fuelN(width); err != nil {
		return nil, err
	}
	margin := int(width) - len(source)
	left := 0
	switch method {
	case "rjust":
		left = margin
	case "center":
		left = margin/2 + (margin & int(width) & 1)
	}
	result := make([]byte, int(width))
	for i := range left {
		result[i] = fill
	}
	copy(result[left:], source)
	for i := left + len(source); i < len(result); i++ {
		result[i] = fill
	}
	return result, nil
}
