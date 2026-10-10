package interp

import (
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"
)

// percentArguments keeps mapping lookups separate from positional consumption.
// A mapping value is one argument even when that value happens to be a tuple.
type percentArguments struct {
	value Value
	tuple *Tuple
	index int
	used  bool
}

func (a *percentArguments) next() (Value, error) {
	if a.tuple != nil {
		if a.index < len(a.tuple.Items) {
			v := a.tuple.Items[a.index]
			a.index++
			return v, nil
		}
	} else if !a.used {
		a.used = true
		return a.value, nil
	}
	return nil, newError("TypeError", "not enough arguments for format string")
}

func percentMapping(v Value, binary bool) bool {
	switch rawPrim(v).(type) {
	case *Tuple, string:
		return false
	case []byte, *ByteArray:
		return !binary
	case *Dict, *classDict, *List, *Range, *MemoryView:
		return true
	}
	if inst, ok := v.(*Instance); ok {
		_, ok = inst.Class.Lookup("__getitem__")
		return ok
	}
	return false
}

// percentFormat implements the str and bytes percent grammars. It deliberately
// does not use Go's printf grammar, whose argument and numeric rules differ.
func percentFormat(vm *VM, format Value, args Value) (Value, error) {
	var text string
	binary, mutable := false, false
	switch f := rawPrim(format).(type) {
	case string:
		text = f
	case []byte:
		text, binary = string(f), true
	case *ByteArray:
		text, binary, mutable = string(f.data), true, true
	default:
		return nil, vm.binopTypeError("mod", format, args)
	}
	if err := vm.fuelN(int64(len(text))); err != nil {
		return nil, err
	}
	state := percentArguments{value: args}
	state.tuple, _ = rawPrim(args).(*Tuple)
	mapping := percentMapping(args, binary)
	var out strings.Builder
	for i := 0; i < len(text); {
		start := i
		for i < len(text) && text[i] != '%' {
			i++
		}
		out.WriteString(text[start:i])
		if i == len(text) {
			break
		}
		i++
		if i < len(text) && text[i] == '%' {
			out.WriteByte('%')
			i++
			continue
		}
		if i < len(text) && text[i] == '(' {
			if !mapping {
				return nil, newError("TypeError", "format requires a mapping")
			}
			i++
			start = i
			depth := 1
			for i < len(text) && depth != 0 {
				switch text[i] {
				case '(':
					depth++
				case ')':
					depth--
				}
				i++
			}
			if depth != 0 {
				return nil, newError("ValueError", "incomplete format key")
			}
			var key Value = text[start : i-1]
			if binary {
				key = []byte(text[start : i-1])
			}
			value, err := vm.getIndex(args, key)
			if err != nil {
				return nil, err
			}
			state = percentArguments{value: value}
		}
		fs := fmtSpec{fill: ' ', align: '>', prec: -1}
		alternate := false
	flags:
		for i < len(text) {
			switch text[i] {
			case '-':
				fs.align = '<'
			case '+':
				fs.sign = '+'
			case ' ':
				if fs.sign != '+' {
					fs.sign = ' '
				}
			case '#':
				alternate = true
			case '0':
				fs.zero = true
			default:
				break flags
			}
			i++
		}
		var err error
		if i < len(text) && text[i] == '*' {
			fs.width, err = percentStar(&state, false)
			i++
			if fs.width < 0 {
				if fs.width == -int(^uint(0)>>1)-1 {
					return nil, newError("OverflowError", "width too big")
				}
				fs.width, fs.align = -fs.width, '<'
			}
		} else {
			fs.width, err = percentDigits(text, &i, int(^uint(0)>>1), "width too big")
		}
		if err != nil {
			return nil, err
		}
		if i < len(text) && text[i] == '.' {
			i++
			fs.hasPrec = true
			if i < len(text) && text[i] == '*' {
				fs.prec, err = percentStar(&state, true)
				i++
				if fs.prec < 0 {
					fs.prec = 0
				}
			} else {
				message := "precision too big"
				if binary {
					message = "prec too big"
				}
				fs.prec, err = percentDigits(text, &i, math.MaxInt32, message)
			}
			if err != nil {
				return nil, err
			}
		}
		if i < len(text) && (text[i] == 'h' || text[i] == 'l' || text[i] == 'L') {
			i++
		}
		if i == len(text) {
			return nil, newError("ValueError", "incomplete format")
		}
		position := i
		fs.typ = rune(text[i])
		i++
		if !binary && fs.typ >= utf8.RuneSelf {
			var size int
			fs.typ, size = pyDecodeRuneInString(text[position:])
			i = position + size
		}
		value, err := state.next()
		if err != nil {
			return nil, err
		}
		if !strings.ContainsRune("diuoxXeEfFgGcras", fs.typ) && !(binary && fs.typ == 'b') {
			ch := fs.typ
			if !binary {
				position = pyRuneCount(text[:position])
				if ch < 31 || ch > 126 {
					ch = '?'
				}
			}
			return nil, newErrorf("ValueError", "unsupported format character '%c' (0x%x) at index %d", ch, fs.typ, position)
		}
		// Charge expansion before allocating padding or numeric precision.
		work := fs.width
		if fs.prec > work {
			work = fs.prec
		}
		if work > int(^uint(0)>>1)-1024 {
			return nil, newError("MemoryError", "")
		}
		if err := vm.fuelN(int64(work)); err != nil {
			return nil, err
		}
		part, err := percentValue(vm, value, &fs, alternate, binary)
		if err != nil {
			return nil, err
		}
		out.WriteString(part)
	}
	if !mapping && ((state.tuple != nil && state.index < len(state.tuple.Items)) || (state.tuple == nil && !state.used)) {
		kind := "string"
		if binary {
			kind = "bytes"
		}
		return nil, newErrorf("TypeError", "not all arguments converted during %s formatting", kind)
	}
	if mutable {
		return &ByteArray{data: []byte(out.String())}, nil
	}
	if binary {
		return []byte(out.String()), nil
	}
	return out.String(), nil
}

func percentDigits(text string, position *int, limit int, message string) (int, error) {
	n := 0
	for *position < len(text) && text[*position] >= '0' && text[*position] <= '9' {
		digit := int(text[*position] - '0')
		if n > (limit-digit)/10 {
			return 0, newError("ValueError", message)
		}
		n = n*10 + digit
		*position++
	}
	return n, nil
}

func percentStar(args *percentArguments, precision bool) (int, error) {
	v, err := args.next()
	if err != nil {
		return 0, err
	}
	v = rawPrim(v)
	var n int64
	if z, ok := v.(*big.Int); ok {
		if !z.IsInt64() {
			if precision {
				return 0, newError("OverflowError", "Python int too large to convert to C int")
			}
			return 0, newError("OverflowError", "Python int too large to convert to C ssize_t")
		}
		n = z.Int64()
	} else {
		var ok bool
		n, ok = asInt(v)
		if !ok {
			return 0, newError("TypeError", "* wants int")
		}
	}
	if precision && (n < math.MinInt32 || n > math.MaxInt32) {
		return 0, newError("OverflowError", "Python int too large to convert to C int")
	}
	if int64(int(n)) != n {
		return 0, newError("OverflowError", "Python int too large to convert to C ssize_t")
	}
	return int(n), nil
}

func percentValue(vm *VM, v Value, fs *fmtSpec, alternate, binary bool) (string, error) {
	var text string
	var err error
	switch fs.typ {
	case 'd', 'i', 'u', 'o', 'x', 'X':
		if binary && fs.typ == 'i' {
			fs.typ = 'd'
		}
		return percentInteger(vm, v, fs, alternate)
	case 'e', 'E', 'f', 'F', 'g', 'G':
		return percentFloat(vm, v, fs, alternate)
	case 'c':
		text, err = percentChar(vm, v, binary)
	case 'a':
		text, err = vm.ascii(v)
	case 'r':
		if binary {
			text, err = vm.ascii(v)
		} else {
			text, err = vm.repr(v)
		}
	case 's', 'b':
		if binary {
			text, err = percentBytes(vm, v)
		} else {
			text, err = vm.str(v)
		}
	}
	if err != nil {
		return "", err
	}
	n := len(text)
	if binary {
		if fs.typ != 'c' && fs.hasPrec && n > fs.prec {
			text, n = text[:fs.prec], fs.prec
		}
	} else if fs.typ == 'c' {
		n = 1
	} else {
		n = 0
		for offset := 0; offset < len(text); {
			if fs.hasPrec && n == fs.prec {
				text = text[:offset]
				break
			}
			_, size := pyDecodeRuneInString(text[offset:])
			offset += size
			n++
		}
	}
	return pad(text, n, fs.align, ' ', fs.width), nil
}

func percentIndex(vm *VM, v Value) (Value, error) {
	if isIntKind(rawPrim(v)) {
		return rawPrim(v), nil
	}
	if inst, ok := v.(*Instance); ok {
		if _, found := inst.Class.Lookup("__index__"); found {
			result, err := vm.callDunder(inst, "__index__", nil)
			if err != nil {
				return nil, err
			}
			if !isIntKind(rawPrim(result)) {
				return nil, newErrorf("TypeError", "__index__ returned non-int (type %s)", typeName(result))
			}
			return rawPrim(result), nil
		}
	}
	return nil, newErrorf("TypeError", "'%s' object cannot be interpreted as an integer", typeName(v))
}

func percentInteger(vm *VM, v Value, fs *fmtSpec, alternate bool) (string, error) {
	integer := fs.typ == 'o' || fs.typ == 'x' || fs.typ == 'X'
	n := rawPrim(v)
	var err error
	if !isIntKind(n) {
		if integer {
			n, err = percentIndex(vm, v)
		} else {
			if inst, ok := v.(*Instance); ok {
				if _, found := inst.Class.Lookup("__int__"); found {
					n, err = vm.callDunder(inst, "__int__", nil)
					if err == nil && !isIntKind(rawPrim(n)) {
						err = newError("TypeError", "__int__ returned non-int")
					}
				} else if _, found := inst.Class.Lookup("__index__"); found {
					n, err = percentIndex(vm, v)
				}
			}
			if f, ok := rawPrim(n).(float64); ok && err == nil {
				switch {
				case math.IsNaN(f):
					err = newError("ValueError", "cannot convert float NaN to integer")
				case math.IsInf(f, 0):
					err = newError("OverflowError", "cannot convert float infinity to integer")
				default:
					n = intFromFloat(f)
				}
			}
		}
	}
	if err != nil {
		if _, wrongType := raisedAs(err, "TypeError"); !wrongType {
			return "", err
		}
	}
	if err != nil || !isIntKind(rawPrim(n)) {
		required := "a real number"
		if integer {
			required = "an integer"
		}
		return "", newErrorf("TypeError", "%%%c format: %s is required, not %s", fs.typ, required, typeName(v))
	}
	n = rawPrim(n)
	base := 10
	prefix := ""
	switch fs.typ {
	case 'o':
		base, prefix = 8, "0o"
	case 'x':
		base, prefix = 16, "0x"
	case 'X':
		base, prefix = 16, "0X"
	}
	var body string
	if z, ok := n.(*big.Int); ok {
		body = z.Text(base)
	} else {
		i, _ := asInt(n)
		body = strconv.FormatInt(i, base)
	}
	negative := strings.HasPrefix(body, "-")
	if negative {
		body = body[1:]
	}
	if fs.typ == 'X' {
		body = strings.ToUpper(body)
	}
	if fs.hasPrec && fs.prec > len(body) {
		body = strings.Repeat("0", fs.prec-len(body)) + body
	}
	if !alternate {
		prefix = ""
	}
	if fs.zero && fs.align != '<' {
		signLen := 0
		if negative || fs.sign != 0 {
			signLen = 1
		}
		if padding := fs.width - len(prefix) - len(body) - signLen; padding > 0 {
			body = strings.Repeat("0", padding) + body
		}
	}
	return padNumber(prefix+body, negative, fs), nil
}

func percentFloatValue(vm *VM, v Value) (float64, error) {
	if inst, ok := v.(*Instance); ok {
		if _, found := inst.Class.Lookup("__float__"); found {
			result, err := vm.callDunder(inst, "__float__", nil)
			if err != nil {
				return 0, err
			}
			f, ok := rawPrim(result).(float64)
			if !ok {
				return 0, newErrorf("TypeError", "%s.__float__ returned non-float (type %s)", inst.Class.Name, typeName(result))
			}
			return f, nil
		}
	}
	n := rawPrim(v)
	if !isNumber(n) {
		inst, instance := v.(*Instance)
		if !instance {
			return 0, newErrorf("TypeError", "must be real number, not %s", typeName(v))
		}
		if _, found := inst.Class.Lookup("__index__"); !found {
			return 0, newErrorf("TypeError", "must be real number, not %s", typeName(v))
		}
		var err error
		n, err = percentIndex(vm, v)
		if err != nil {
			return 0, err
		}
	}
	f, ok := asFloat(n)
	if !ok {
		return 0, newErrorf("TypeError", "must be real number, not %s", typeName(v))
	}
	if _, bigInt := n.(*big.Int); bigInt && math.IsInf(f, 0) {
		return 0, newError("OverflowError", "int too large to convert to float")
	}
	return f, nil
}

func percentFloat(vm *VM, v Value, fs *fmtSpec, alternate bool) (string, error) {
	f, err := percentFloatValue(vm, v)
	if err != nil {
		return "", err
	}
	typ := byte(fs.typ)
	upper := typ >= 'A' && typ <= 'Z'
	if upper {
		typ += 'a' - 'A'
	}
	precision := 6
	if fs.hasPrec {
		precision = fs.prec
	}
	if typ == 'g' && precision == 0 {
		precision = 1
	}
	var body string
	switch {
	case math.IsNaN(f):
		body = "nan"
	case math.IsInf(f, 0):
		body = "inf"
	default:
		body = strconv.FormatFloat(math.Abs(f), typ, precision, 64)
		if alternate {
			mantissa, exponent := body, ""
			if at := strings.IndexByte(body, 'e'); at >= 0 {
				mantissa, exponent = body[:at], body[at:]
			}
			if !strings.ContainsRune(mantissa, '.') {
				mantissa += "."
			}
			if typ == 'g' {
				digits, nonzero := 0, false
				for _, ch := range mantissa {
					if ch == '.' {
						continue
					}
					if ch != '0' {
						nonzero = true
					}
					if nonzero {
						digits++
					}
				}
				if !nonzero {
					digits = 1
				}
				if precision > digits {
					mantissa += strings.Repeat("0", precision-digits)
				}
			}
			body = mantissa + exponent
		}
	}
	if upper {
		body = strings.ToUpper(body)
	}
	if fs.zero && fs.align != '<' {
		fs.align, fs.fill = '=', '0'
	}
	return padNumber(body, math.Signbit(f) && !math.IsNaN(f), fs), nil
}

func percentChar(vm *VM, v Value, binary bool) (string, error) {
	if binary {
		switch b := rawPrim(v).(type) {
		case []byte:
			if len(b) == 1 {
				return string(b), nil
			}
		case *ByteArray:
			if len(b.data) == 1 {
				return string(b.data), nil
			}
		}
	} else if s, ok := rawPrim(v).(string); ok && pyRuneCount(s) == 1 {
		return s, nil
	}
	n, err := percentIndex(vm, v)
	if err != nil {
		if _, wrongType := raisedAs(err, "TypeError"); !wrongType {
			return "", err
		}
		message := "%c requires int or char"
		if binary {
			message = "%c requires an integer in range(256) or a single byte"
		}
		return "", newError("TypeError", message)
	}
	i, small := asInt(n)
	if z, ok := n.(*big.Int); ok && z.IsInt64() {
		i, small = z.Int64(), true
	}
	limit, bounds := int64(0x110000), "0x110000"
	if binary {
		limit, bounds = 256, "256"
	}
	if !small || i < 0 || i >= limit {
		return "", newErrorf("OverflowError", "%%c arg not in range(%s)", bounds)
	}
	if binary {
		return string([]byte{byte(i)}), nil
	}
	// Go's rune encoder replaces surrogates; preserve Python's code point in
	// the same UTF-8-shaped representation used for arbitrary string payloads.
	if i >= 0xd800 && i <= 0xdfff {
		return string([]byte{byte(0xe0 | i>>12), byte(0x80 | (i>>6)&0x3f), byte(0x80 | i&0x3f)}), nil
	}
	return pyRuneString(rune(i)), nil
}

func percentBytes(vm *VM, v Value) (string, error) {
	switch b := rawPrim(v).(type) {
	case []byte:
		return string(b), nil
	case *ByteArray:
		return string(b.data), nil
	}
	if inst, ok := v.(*Instance); ok {
		if _, found := inst.Class.Lookup("__bytes__"); found {
			result, err := vm.callDunder(inst, "__bytes__", nil)
			if err != nil {
				return "", err
			}
			b, ok := rawPrim(result).([]byte)
			if !ok {
				return "", newErrorf("TypeError", "__bytes__ returned non-bytes (type %s)", typeName(result))
			}
			return string(b), nil
		}
	}
	if _, ok := rawPrim(v).(*MemoryView); ok {
		b, err := bufferBytes(v)
		return string(b), err
	}
	return "", newErrorf("TypeError", "%%b requires a bytes-like object, or an object that implements __bytes__, not '%s'", typeName(v))
}
