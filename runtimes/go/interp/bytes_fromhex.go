package interp

// bytesFromHex follows CPython 3.12's _PyBytes_FromHex: ASCII whitespace is
// permitted only between complete byte pairs. Non-ASCII characters take
// precedence over malformed ASCII pairs, with positions measured in codepoints.
func (vm *VM) bytesFromHex(cls Value, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("fromhex", kw); err != nil {
		return nil, err
	}
	if err := argCount("fromhex", args, 1, 1); err != nil {
		return nil, err
	}
	text, ok := rawPrim(args[0]).(string)
	if !ok {
		return nil, newErrorf("TypeError", "fromhex() argument must be str, not %s", typeName(args[0]))
	}
	invalid := func(position int) (Value, error) {
		return nil, newErrorf("ValueError", "non-hexadecimal number found in fromhex() arg at position %d", position)
	}
	for i := 0; i < len(text); i++ {
		if text[i] >= 128 {
			// Everything preceding the first non-ASCII codepoint is ASCII,
			// so its byte offset is also its Python character index.
			return invalid(i)
		}
	}
	if err := vm.fuelN(int64(len(text))); err != nil {
		return nil, err
	}
	output := make([]byte, 0, len(text)/2)
	for i := 0; i < len(text); {
		switch text[i] {
		case ' ', '\t', '\n', '\r', '\v', '\f':
			i++
			continue
		}
		top := fromHexDigit(text[i])
		if top < 0 {
			return invalid(i)
		}
		i++
		if i == len(text) {
			return invalid(i)
		}
		bottom := fromHexDigit(text[i])
		if bottom < 0 {
			return invalid(i)
		}
		i++
		output = append(output, byte(top<<4|bottom))
	}
	if cls == vm.types["bytes"] {
		return output, nil
	}
	if cls == vm.types["bytearray"] {
		return &ByteArray{data: output}, nil
	}
	// CPython constructs subclasses from an exact bytes instance, including
	// bytearray subclasses, and honors their __new__/__init__ implementations.
	return vm.call(cls, []Value{output}, nil)
}

func fromHexDigit(value byte) int {
	switch {
	case value >= '0' && value <= '9':
		return int(value - '0')
	case value >= 'a' && value <= 'f':
		return int(value-'a') + 10
	case value >= 'A' && value <= 'F':
		return int(value-'A') + 10
	default:
		return -1
	}
}
