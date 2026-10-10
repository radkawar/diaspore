package interp

import (
	"strings"
	"unicode"
)

// braceFormatter shares numbering state with nested format specifications.
type braceFormatter struct {
	vm      *VM
	args    []Value
	kw      []KwArg
	mapping Value
	next    int
	mode    byte // 0, 'a' for automatic, or 'm' for manual
}

// strFormat uses mapping == nil for str.format; any Python value, including
// None, selects str.format_map. Argument-count validation belongs to the method.
func strFormat(vm *VM, format string, args []Value, kw []KwArg, mapping Value) (Value, error) {
	f := braceFormatter{vm: vm, args: args, kw: kw, mapping: mapping}
	return f.expand(format, 2)
}

func (f *braceFormatter) expand(text string, depth int) (string, error) {
	if depth <= 0 {
		return "", newError("ValueError", "Max string recursion exceeded")
	}
	if err := f.vm.fuelN(int64(len(text))); err != nil {
		return "", err
	}
	var out strings.Builder
	for i := 0; i < len(text); {
		start := i
		for i < len(text) && text[i] != '{' && text[i] != '}' {
			i++
		}
		out.WriteString(text[start:i])
		if i == len(text) {
			break
		}
		brace := text[i]
		i++
		if i < len(text) && text[i] == brace {
			out.WriteByte(brace)
			i++
			continue
		}
		if brace == '}' || i == len(text) {
			return "", newErrorf("ValueError", "Single '%c' encountered in format string", brace)
		}
		field, conversion, spec, nested, end, err := braceField(text, i)
		if err != nil {
			return "", err
		}
		i = end
		value, err := f.lookup(field)
		if err != nil {
			return "", err
		}
		switch conversion {
		case 0:
		case 's':
			value, err = f.vm.str(value)
		case 'r':
			value, err = f.vm.repr(value)
		case 'a':
			value, err = f.vm.ascii(value)
		default:
			return "", newErrorf("ValueError", "Unknown conversion specifier %s", pyRuneString(conversion))
		}
		if err != nil {
			return "", err
		}
		if nested {
			spec, err = f.expand(spec, depth-1)
			if err != nil {
				return "", err
			}
		}
		part, err := braceFormatValue(f.vm, value, spec)
		if err != nil {
			return "", err
		}
		out.WriteString(part)
	}
	return out.String(), nil
}

func braceField(text string, start int) (field string, conversion rune, spec string, nested bool, end int, err error) {
	i := start
	for i < len(text) {
		switch text[i] {
		case '{':
			err = newError("ValueError", "unexpected '{' in field name")
			return
		case '[':
			i++
			for i < len(text) && text[i] != ']' {
				i++
			}
		case '}', ':', '!':
			field = text[start:i]
			goto suffix
		}
		i++
	}
	err = newError("ValueError", "expected '}' before end of string")
	return

suffix:
	if text[i] == '}' {
		end = i + 1
		return
	}
	if text[i] == '!' {
		i++
		if i == len(text) {
			err = newError("ValueError", "end of string while looking for conversion specifier")
			return
		}
		var size int
		conversion, size = pyDecodeRuneInString(text[i:])
		i += size
		if i < len(text) {
			if text[i] == '}' {
				end = i + 1
				return
			}
			if text[i] != ':' {
				err = newError("ValueError", "expected ':' after conversion specifier")
				return
			}
		}
	}
	if i < len(text) {
		i++ // ':'
	}
	start = i
	count := 1
	for i < len(text) {
		switch text[i] {
		case '{':
			count++
			nested = true
		case '}':
			count--
			if count == 0 {
				spec, end = text[start:i], i+1
				return
			}
		}
		i++
	}
	err = newError("ValueError", "unmatched '{' in format spec")
	return
}

func (f *braceFormatter) lookup(field string) (Value, error) {
	i := 0
	for i < len(field) && field[i] != '.' && field[i] != '[' {
		i++
	}
	first := field[:i]
	index, numeric, err := braceIndex(first)
	if err != nil {
		return nil, err
	}
	automatic := first == ""
	var value Value
	if automatic || numeric {
		if automatic {
			if f.mode == 'm' {
				return nil, newError("ValueError", "cannot switch from manual field specification to automatic field numbering")
			}
			f.mode = 'a'
			index = f.next
			f.next++
		} else {
			if f.mode == 'a' {
				return nil, newError("ValueError", "cannot switch from automatic field numbering to manual field specification")
			}
			f.mode = 'm'
		}
		if f.mapping != nil {
			return nil, newError("ValueError", "Format string contains positional fields")
		}
		if index >= len(f.args) {
			return nil, newErrorf("IndexError", "Replacement index %d out of range for positional args tuple", index)
		}
		value = f.args[index]
	} else if f.mapping != nil {
		value, err = f.vm.getIndex(f.mapping, first)
	} else {
		var found bool
		value, found = kwGet(f.kw, first)
		if !found {
			return nil, newErrorArgs("KeyError", first)
		}
	}
	if err != nil {
		return nil, err
	}
	for i < len(field) {
		kind := field[i]
		i++
		start := i
		switch kind {
		case '.':
			for i < len(field) && field[i] != '.' && field[i] != '[' {
				i++
			}
			if i == start {
				return nil, newError("ValueError", "Empty attribute in format string")
			}
			value, err = f.vm.getAttr(value, field[start:i])
		case '[':
			for i < len(field) && field[i] != ']' {
				i++
			}
			if i == len(field) {
				return nil, newError("ValueError", "Missing ']' in format string")
			}
			if i == start {
				return nil, newError("ValueError", "Empty attribute in format string")
			}
			var key Value = field[start:i]
			index, numeric, parseErr := braceIndex(field[start:i])
			if parseErr != nil {
				return nil, parseErr
			}
			if numeric {
				key = int64(index)
			}
			i++
			value, err = f.vm.getIndex(value, key)
		default:
			return nil, newError("ValueError", "Only '.' or '[' may follow ']' in format field specifier")
		}
		if err != nil {
			return nil, err
		}
	}
	return value, nil
}

// braceIndex accepts Unicode decimal digits, but not other numeric characters.
func braceIndex(text string) (int, bool, error) {
	if text == "" {
		return 0, false, nil
	}
	n := 0
	for i := 0; i < len(text); {
		ch, size := pyDecodeRuneInString(text[i:])
		i += size
		digit := int(ch - '0')
		if ch < '0' || ch > '9' {
			digit = -1
			if ch <= 0xffff {
				for _, span := range unicode.Digit.R16 {
					if uint32(ch) >= uint32(span.Lo) && uint32(ch) <= uint32(span.Hi) && (uint32(ch)-uint32(span.Lo))%uint32(span.Stride) == 0 {
						digit = int((uint32(ch)-uint32(span.Lo))/uint32(span.Stride)) % 10
						break
					}
				}
			} else {
				for _, span := range unicode.Digit.R32 {
					if uint32(ch) >= span.Lo && uint32(ch) <= span.Hi && (uint32(ch)-span.Lo)%span.Stride == 0 {
						digit = int((uint32(ch)-span.Lo)/span.Stride) % 10
						break
					}
				}
			}
			if digit < 0 {
				return 0, false, nil
			}
		}
		if n > (int(^uint(0)>>1)-digit)/10 {
			return 0, false, newError("ValueError", "Too many decimal digits in format string")
		}
		n = n*10 + digit
	}
	return n, true, nil
}

func braceFormatValue(vm *VM, value Value, spec string) (string, error) {
	if inst, ok := value.(*Instance); ok {
		if _, found := inst.Class.Lookup("__format__"); found {
			result, err := vm.callDunder(inst, "__format__", []Value{spec})
			if err != nil {
				return "", err
			}
			text, ok := rawPrim(result).(string)
			if !ok {
				return "", newErrorf("TypeError", "__format__ must return a str, not %s", typeName(result))
			}
			return text, nil
		}
	}
	if spec == "" {
		return vm.formatValue(value, spec)
	}
	primitive := rawPrim(value)
	if _, text := primitive.(string); !text && !isNumber(primitive) {
		return "", newErrorf("TypeError", "unsupported format string passed to %s.__format__", typeName(value))
	}
	return vm.formatValue(primitive, spec)
}
