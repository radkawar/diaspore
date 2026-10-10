package interp

import "strings"

var strMakeTransBuiltin *Builtin

func init() {
	strMakeTransBuiltin = bi("maketrans", strMakeTrans)
	strMakeTransBuiltin.NoBind = true
}

func strMakeTrans(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("maketrans", kw); err != nil {
		return nil, err
	}
	if err := argCount("maketrans", args, 1, 3); err != nil {
		return nil, err
	}
	result := NewDict()
	if len(args) == 1 {
		var source *Dict
		switch dictionary := args[0].(type) {
		case *Dict:
			source = dictionary
		case *metadataNamespace:
			source = dictionary.snapshot()
		default:
			return nil, newError("TypeError", "if you give only one argument to maketrans it must be a dict")
		}
		for _, entry := range source.entries {
			if err := vm.fuel(); err != nil {
				return nil, err
			}
			key := entry.key
			if text, ok := rawPrim(key).(string); ok {
				point, size := pyDecodeRuneInString(text)
				if size == 0 || size != len(text) {
					return nil, newError("ValueError", "string keys in translate table must be of length 1")
				}
				key = mkInt(int64(point))
			} else if !isIntKind(rawPrim(key)) {
				return nil, newError("TypeError", "keys in translate table must be strings or integers")
			}
			if err := result.Set(key, entry.val); err != nil {
				return nil, err
			}
		}
		return result, nil
	}
	target, ok := rawPrim(args[1]).(string)
	if !ok {
		return nil, newError("TypeError", "maketrans() argument 2 must be str")
	}
	var deleted string
	if len(args) == 3 {
		deleted, ok = rawPrim(args[2]).(string)
		if !ok {
			return nil, newError("TypeError", "maketrans() argument 3 must be str")
		}
	}
	source, ok := rawPrim(args[0]).(string)
	if !ok {
		return nil, newError("TypeError", "first maketrans argument must be a string if there is a second argument")
	}
	if pyRuneCount(source) != pyRuneCount(target) {
		return nil, newError("ValueError", "the first two maketrans arguments must have equal length")
	}
	for i, j := 0, 0; i < len(source); {
		if err := vm.fuel(); err != nil {
			return nil, err
		}
		from, fromSize := pyDecodeRuneInString(source[i:])
		to, toSize := pyDecodeRuneInString(target[j:])
		if err := result.Set(mkInt(int64(from)), mkInt(int64(to))); err != nil {
			return nil, err
		}
		i, j = i+fromSize, j+toSize
	}
	for offset := 0; offset < len(deleted); {
		if err := vm.fuel(); err != nil {
			return nil, err
		}
		point, size := pyDecodeRuneInString(deleted[offset:])
		if err := result.Set(mkInt(int64(point)), None); err != nil {
			return nil, err
		}
		offset += size
	}
	return result, nil
}

func (vm *VM) translationLookup(table Value, point rune) (Value, error) {
	if err := vm.fuel(); err != nil {
		return nil, err
	}
	key := mkInt(int64(point))
	var value Value
	var err error
	switch mapping := table.(type) {
	case *Dict:
		var found bool
		value, found, err = mapping.Get(key)
		if err == nil && !found {
			return key, nil
		}
	case *metadataNamespace:
		var found bool
		value, found, err = mapping.get(key)
		if err == nil && !found {
			return key, nil
		}
	default:
		value, err = vm.getIndex(table, key)
	}
	if err != nil {
		if _, absent := raisedAs(err, "LookupError"); absent {
			return mkInt(int64(point)), nil
		}
		return nil, err
	}
	value = rawPrim(value)
	if isIntKind(value) {
		ordinal, small := asInt(value)
		if !small || ordinal < 0 || ordinal > 0x10ffff {
			return nil, newError("ValueError", "character mapping must be in range(0x110000)")
		}
		return mkInt(ordinal), nil
	}
	switch value.(type) {
	case NoneType, string:
		return value, nil
	}
	return nil, newError("TypeError", "character mapping must return integer, None or str")
}

func strTranslate(vm *VM, text string, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("translate", kw); err != nil {
		return nil, err
	}
	if err := argCount("translate", args, 1, 1); err != nil {
		return nil, err
	}
	ascii := true
	for i := range len(text) {
		if text[i] >= 128 {
			ascii = false
			break
		}
	}
	var cache [128]int16
	var cached [128]bool
	var output strings.Builder
	changed := false
	for offset := 0; offset < len(text); {
		point, width := pyDecodeRuneInString(text[offset:])
		var replacement Value
		if ascii && cached[point] {
			if cache[point] < 0 {
				replacement = None
			} else {
				replacement = mkInt(int64(cache[point]))
			}
		} else {
			var err error
			replacement, err = vm.translationLookup(args[0], point)
			if err != nil {
				return nil, err
			}
			if ascii {
				code := int16(-2)
				switch value := replacement.(type) {
				case NoneType:
					code = -1
				case int64:
					if value < 128 {
						code = int16(value)
					}
				case string:
					if len(value) == 1 && value[0] < 128 {
						code = int16(value[0])
					}
				}
				if code == -2 {
					// The CPython ASCII cache falls back without consuming this
					// character; a stateful mapping observes a second lookup.
					ascii = false
					continue
				}
				cache[point], cached[point] = code, true
			}
		}
		unchanged := false
		switch value := replacement.(type) {
		case int64:
			unchanged = rune(value) == point
		case string:
			unchanged = value == text[offset:offset+width]
		}
		if !unchanged && !changed {
			output.Grow(len(text))
			output.WriteString(text[:offset])
			changed = true
		}
		if changed {
			switch value := replacement.(type) {
			case int64:
				pyWriteRune(&output, rune(value))
			case string:
				output.WriteString(value)
			}
		}
		offset += width
		if _, deleted := replacement.(NoneType); deleted && !ascii {
			// The general translation loop groups deletions, looking ahead to
			// the first retained character before translating it normally.
			for offset < len(text) {
				next, size := pyDecodeRuneInString(text[offset:])
				value, err := vm.translationLookup(args[0], next)
				if err != nil {
					return nil, err
				}
				if _, deleted := value.(NoneType); !deleted {
					break
				}
				offset += size
			}
		}
	}
	if !changed {
		return text, nil
	}
	return output.String(), nil
}

func strExpandTabs(vm *VM, text string, args []Value, kw []KwArg) (Value, error) {
	if count := len(args) + len(kw); count > 1 {
		return nil, newErrorf("TypeError", "expandtabs() takes at most 1 argument (%d given)", count)
	}
	var sizeValue Value = int64(8)
	if len(args) != 0 {
		sizeValue = args[0]
	}
	for _, keyword := range kw {
		if keyword.Name != "tabsize" {
			return nil, newErrorf("TypeError", "'%s' is an invalid keyword argument for expandtabs()", keyword.Name)
		}
		sizeValue = keyword.Val
	}
	index, err := percentIndex(vm, sizeValue)
	if err != nil {
		return nil, err
	}
	tabsize, fits := asInt(index)
	if !fits || tabsize < -2147483648 || tabsize > 2147483647 {
		return nil, newError("OverflowError", "Python int too large to convert to C int")
	}
	if strings.IndexByte(text, '\t') < 0 {
		return text, nil
	}
	maximum := int(^uint(0) >> 1)
	length := len(text)
	column := int64(0)
	for offset := 0; offset < len(text); {
		point, width := pyDecodeRuneInString(text[offset:])
		offset += width
		switch point {
		case '\t':
			spaces := int64(0)
			if tabsize > 0 {
				spaces = tabsize - column
			}
			if spaces > 0 && spaces-1 > int64(maximum-length) {
				return nil, newError("OverflowError", "expanded string is too long")
			}
			length += int(spaces) - 1
			column = 0
		case '\r', '\n':
			column = 0
		default:
			column++
			if tabsize > 0 && column == tabsize {
				column = 0
			}
		}
	}
	if err := vm.fuelN(int64(length)); err != nil {
		return nil, err
	}
	var result strings.Builder
	result.Grow(length)
	column = 0
	start := 0
	const padding = "                                                                "
	for offset := 0; offset < len(text); {
		point, width := pyDecodeRuneInString(text[offset:])
		switch point {
		case '\t':
			result.WriteString(text[start:offset])
			if tabsize > 0 {
				spaces := tabsize - column
				for spaces > int64(len(padding)) {
					result.WriteString(padding)
					spaces -= int64(len(padding))
				}
				result.WriteString(padding[:int(spaces)])
			}
			column = 0
			start = offset + width
		case '\r', '\n':
			column = 0
		default:
			column++
			if tabsize > 0 && column == tabsize {
				column = 0
			}
		}
		offset += width
	}
	result.WriteString(text[start:])
	return result.String(), nil
}
