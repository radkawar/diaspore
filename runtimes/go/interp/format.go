package interp

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode"
)

// fmtCpart formats a complex component like CPython: float repr without a
// trailing ".0".
func fmtCpart(f float64) string {
	return strings.TrimSuffix(pyFloatStr(f), ".0")
}

// pyComplexStr renders a complex like CPython's repr: (3+4j), 1j, 0j, (1-0j).
func pyComplexStr(z complex128) string {
	re, im := real(z), imag(z)
	if re == 0 && !math.Signbit(re) {
		return fmtCpart(im) + "j"
	}
	sign := "+"
	if math.Signbit(im) {
		sign = "-"
		im = math.Abs(im)
	}
	return "(" + fmtCpart(re) + sign + fmtCpart(im) + "j)"
}

// pyFloatStr renders a float exactly like CPython's repr: shortest
// round-trip digits, fixed notation unless exp < -4 or exp >= 16, integral
// floats get ".0", exponent styled e+NN / e-NN with at least two digits.
func pyFloatStr(f float64) string {
	if math.IsInf(f, 1) {
		return "inf"
	}
	if math.IsInf(f, -1) {
		return "-inf"
	}
	if math.IsNaN(f) {
		return "nan"
	}
	if f == 0 {
		if math.Signbit(f) {
			return "-0.0"
		}
		return "0.0"
	}
	s := strconv.FormatFloat(f, 'e', -1, 64)
	mantissa, expPart, _ := strings.Cut(s, "e")
	exp, _ := strconv.Atoi(expPart)
	neg := strings.HasPrefix(mantissa, "-")
	mantissa = strings.TrimPrefix(mantissa, "-")
	digits := strings.Replace(mantissa, ".", "", 1)
	var out string
	switch {
	case exp < -4 || exp >= 16:
		m := digits[:1]
		if len(digits) > 1 {
			m += "." + digits[1:]
		}
		out = m + "e" + expString(exp)
	case exp >= len(digits)-1:
		out = digits + strings.Repeat("0", exp-len(digits)+1) + ".0"
	case exp >= 0:
		out = digits[:exp+1] + "." + digits[exp+1:]
	default:
		out = "0." + strings.Repeat("0", -exp-1) + digits
	}
	if neg {
		out = "-" + out
	}
	return out
}

func expString(exp int) string {
	sign := "+"
	if exp < 0 {
		sign = "-"
		exp = -exp
	}
	es := strconv.Itoa(exp)
	if len(es) < 2 {
		es = "0" + es
	}
	return sign + es
}

// reprString renders a str repr with CPython quote selection and escapes.
func reprString(s string) string {
	q := byte('\'')
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		q = '"'
	}
	var b strings.Builder
	b.WriteByte(q)
	for i := 0; i < len(s); {
		r, size := pyDecodeRuneInString(s[i:])
		i += size
		switch {
		case r == '\\':
			b.WriteString(`\\`)
		case r == rune(q):
			b.WriteByte('\\')
			b.WriteByte(q)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case !unicode.IsPrint(r):
			switch {
			case r <= 0xff:
				fmt.Fprintf(&b, `\x%02x`, r)
			case r <= 0xffff:
				fmt.Fprintf(&b, `\u%04x`, r)
			default:
				fmt.Fprintf(&b, `\U%08x`, r)
			}
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte(q)
	return b.String()
}

// reprBytes renders a bytes repr (b'...').
func reprBytes(bs []byte) string {
	hasSingle, hasDouble := false, false
	for _, c := range bs {
		if c == '\'' {
			hasSingle = true
		} else if c == '"' {
			hasDouble = true
		}
	}
	q := byte('\'')
	if hasSingle && !hasDouble {
		q = '"'
	}
	var b strings.Builder
	b.WriteString("b")
	b.WriteByte(q)
	for _, c := range bs {
		switch {
		case c == '\\':
			b.WriteString(`\\`)
		case c == q:
			b.WriteByte('\\')
			b.WriteByte(q)
		case c == '\n':
			b.WriteString(`\n`)
		case c == '\r':
			b.WriteString(`\r`)
		case c == '\t':
			b.WriteString(`\t`)
		case c < 0x20 || c >= 0x7f:
			fmt.Fprintf(&b, `\x%02x`, c)
		default:
			b.WriteByte(c)
		}
	}
	b.WriteByte(q)
	return b.String()
}

func reprRange(r *Range) string {
	if r.big != nil {
		if r.big.step.IsInt64() && r.big.step.Int64() == 1 {
			return fmt.Sprintf("range(%s, %s)", r.big.start.String(), r.big.stop.String())
		}
		return fmt.Sprintf("range(%s, %s, %s)", r.big.start.String(), r.big.stop.String(), r.big.step.String())
	}
	if r.Step == 1 {
		return fmt.Sprintf("range(%d, %d)", r.Start, r.Stop)
	}
	return fmt.Sprintf("range(%d, %d, %d)", r.Start, r.Stop, r.Step)
}

// reprNoVM renders a repr without dunder dispatch; used only for host-facing
// error text on values that never need dunders (exception args).
func reprNoVM(v Value) string {
	switch t := v.(type) {
	case NoneType:
		return "None"
	case NotImplementedType:
		return "NotImplemented"
	case bool:
		if t {
			return "True"
		}
		return "False"
	case int64:
		return strconv.FormatInt(t, 10)
	case float64:
		return pyFloatStr(t)
	case *big.Int:
		return t.String()
	case string:
		return reprString(t)
	case []byte:
		return reprBytes(t)
	case *Range:
		return reprRange(t)
	case *Tuple:
		parts := make([]string, len(t.Items))
		for i, x := range t.Items {
			parts[i] = reprNoVM(x)
		}
		if len(parts) == 1 {
			return "(" + parts[0] + ",)"
		}
		return "(" + strings.Join(parts, ", ") + ")"
	case *Instance:
		return "<" + t.Class.Name + " object>"
	}
	return "<" + typeName(v) + ">"
}

func (vm *VM) repr(v Value) (string, error) {
	switch v.(type) {
	case *List, *Tuple, *Dict, *DictView:
		if vm.repring[v] {
			switch v.(type) {
			case *List:
				return "[...]", nil
			case *Tuple:
				return "(...)", nil
			case *DictView:
				return "...", nil
			default:
				return "{...}", nil
			}
		}
		vm.repring[v] = true
		defer delete(vm.repring, v)
	}
	switch t := v.(type) {
	case NoneType:
		return "None", nil
	case EllipsisType:
		return "Ellipsis", nil
	case NotImplementedType:
		return "NotImplemented", nil
	case *classDict:
		s, err := vm.repr(t.snapshot())
		return "mappingproxy(" + s + ")", err
	case *metadataNamespace:
		return vm.repr(t.snapshot())
	case *GenericAlias, *UnionType:
		return vm.genericRepr(t)
	case bool:
		if t {
			return "True", nil
		}
		return "False", nil
	case int64:
		return strconv.FormatInt(t, 10), nil
	case float64:
		return pyFloatStr(t), nil
	case complex128:
		return pyComplexStr(t), nil
	case string:
		return reprString(t), nil
	case []byte:
		return reprBytes(t), nil
	case *ByteArray:
		return "bytearray(" + reprBytes(t.data) + ")", nil
	case *MemoryView:
		return fmt.Sprintf("<memory at %p>", t), nil
	case *Range:
		return reprRange(t), nil
	case *List:
		parts, err := vm.reprAll(t.Items)
		if err != nil {
			return "", err
		}
		return "[" + strings.Join(parts, ", ") + "]", nil
	case *Tuple:
		parts, err := vm.reprAll(t.Items)
		if err != nil {
			return "", err
		}
		if len(parts) == 1 {
			return "(" + parts[0] + ",)", nil
		}
		return "(" + strings.Join(parts, ", ") + ")", nil
	case *Dict:
		parts := make([]string, 0, t.Len())
		for _, e := range t.entries {
			ks, err := vm.repr(e.key)
			if err != nil {
				return "", err
			}
			vs, err := vm.repr(e.val)
			if err != nil {
				return "", err
			}
			parts = append(parts, ks+": "+vs)
		}
		return "{" + strings.Join(parts, ", ") + "}", nil
	case *DictView:
		parts, err := vm.reprAll(t.elements())
		if err != nil {
			return "", err
		}
		return "dict_" + t.kind + "([" + strings.Join(parts, ", ") + "])", nil
	case *FrozenSet:
		if t.set.Len() == 0 {
			return "frozenset()", nil
		}
		s, err := vm.repr(t.set)
		return "frozenset(" + s + ")", err
	case *Set:
		if t.Len() == 0 {
			return "set()", nil
		}
		parts, err := vm.reprAll(t.Elems())
		if err != nil {
			return "", err
		}
		return "{" + strings.Join(parts, ", ") + "}", nil
	case *Slice:
		l, err := vm.repr(t.Lower)
		if err != nil {
			return "", err
		}
		u, err := vm.repr(t.Upper)
		if err != nil {
			return "", err
		}
		s, err := vm.repr(t.Step)
		if err != nil {
			return "", err
		}
		return "slice(" + l + ", " + u + ", " + s + ")", nil
	case *Instance:
		if t.Class.IsSub(baseExc) {
			if _, ok := t.Class.Lookup("__repr__"); !ok {
				parts, err := vm.reprAll(excArgs(t))
				if err != nil {
					return "", err
				}
				return t.Class.Name + "(" + strings.Join(parts, ", ") + ")", nil
			}
		}
		if _, ok := t.Class.Lookup("__repr__"); ok {
			r, err := vm.callDunder(t, "__repr__", nil)
			if err != nil {
				return "", err
			}
			s, ok := r.(string)
			if !ok {
				return "", newError("TypeError", "__repr__ returned non-string")
			}
			return s, nil
		}
		if t.Prim != nil {
			return vm.repr(t.Prim)
		}
		return "<" + t.Class.Name + " object>", nil
	case *Class:
		return "<class '" + t.Name + "'>", nil
	case *BuiltinType:
		return "<class '" + t.Name + "'>", nil
	case *Function:
		return "<function " + t.cf.name + ">", nil
	case *BoundMethod:
		return "<bound method " + callableName(t.Fn) + ">", nil
	case *Generator:
		return "<generator " + t.fn.cf.name + ">", nil
	case *Module:
		return "<module '" + t.Name + "'>", nil
	case *NativeModule:
		return "<module '" + t.Name + "'>", nil
	case *File:
		return "<file>", nil
	case *NativeObject:
		return "<" + t.Kind + ">", nil
	case *big.Int:
		return t.String(), nil
	case *Builtin:
		return "<builtin " + t.Name + ">", nil
	}
	return "<" + typeName(v) + ">", nil
}

func (vm *VM) reprAll(items []Value) ([]string, error) {
	parts := make([]string, len(items))
	for i, x := range items {
		s, err := vm.repr(x)
		if err != nil {
			return nil, err
		}
		parts[i] = s
	}
	return parts, nil
}

func callableName(f Value) string {
	switch t := f.(type) {
	case *Function:
		return t.cf.name
	case *Builtin:
		return t.Name
	case *Class:
		return t.Name
	case *BuiltinType:
		return t.Name
	}
	return "function"
}

func (vm *VM) str(v Value) (string, error) {
	switch t := v.(type) {
	case string:
		return t, nil
	case *classDict:
		return vm.str(t.snapshot())
	case *Instance:
		if t.Class.IsSub(baseExc) {
			if _, ok := t.Class.Lookup("__str__"); !ok {
				return vm.excStr(t)
			}
		}
		for _, name := range []string{"__str__", "__repr__"} {
			if _, ok := t.Class.Lookup(name); ok {
				r, err := vm.callDunder(t, name, nil)
				if err != nil {
					return "", err
				}
				s, ok := r.(string)
				if !ok {
					return "", newErrorf("TypeError", "%s returned non-string (%s)", name, typeName(r))
				}
				return s, nil
			}
		}
		if t.Prim != nil {
			return vm.str(t.Prim)
		}
		return "<" + t.Class.Name + " object>", nil
	}
	return vm.repr(v)
}

// ascii renders repr(v) with non-ASCII characters backslash-escaped, like
// CPython's ascii() / "backslashreplace".
func (vm *VM) ascii(v Value) (string, error) {
	r, err := vm.repr(v)
	if err != nil {
		return "", err
	}
	var b strings.Builder
	for i := 0; i < len(r); {
		c, size := pyDecodeRuneInString(r[i:])
		i += size
		switch {
		case c < 0x80:
			b.WriteRune(c)
		case c < 0x100:
			fmt.Fprintf(&b, `\x%02x`, c)
		case c < 0x10000:
			fmt.Fprintf(&b, `\u%04x`, c)
		default:
			fmt.Fprintf(&b, `\U%08x`, c)
		}
	}
	return b.String(), nil
}

// excStr renders str(e) for an exception instance without a user __str__.
func (vm *VM) excStr(inst *Instance) (string, error) {
	if _, ok := inst.Class.Lookup("__str__"); ok {
		return vm.str(inst)
	}
	if message, ok := unicodeErrorStr(inst); ok {
		return message, nil
	}
	if inst.Class.IsSub(excClasses["OSError"]) {
		errno, hasErrno := inst.dictLookup("errno")
		message, hasMessage := inst.dictLookup("strerror")
		if _, none := errno.(NoneType); hasErrno && hasMessage && !none {
			number, err := vm.str(errno)
			if err != nil {
				return "", err
			}
			reason, err := vm.str(message)
			if err != nil {
				return "", err
			}
			text := "[Errno " + number + "] " + reason
			if filename, ok := inst.dictLookup("filename"); ok {
				if _, none := filename.(NoneType); !none {
					name, err := vm.repr(filename)
					if err != nil {
						return "", err
					}
					text += ": " + name
					if filename2, ok := inst.dictLookup("filename2"); ok {
						if _, none := filename2.(NoneType); !none {
							name2, err := vm.repr(filename2)
							if err != nil {
								return "", err
							}
							text += " -> " + name2
						}
					}
				}
			}
			return text, nil
		}
	}
	args := excArgs(inst)
	if inst.Class.IsSub(excClasses["KeyError"]) && len(args) == 1 {
		return vm.repr(args[0])
	}
	if len(args) == 0 {
		return "", nil
	}
	if len(args) == 1 {
		return vm.str(args[0])
	}
	return vm.repr(&Tuple{Items: args})
}

func unicodeErrorStr(inst *Instance) (string, bool) {
	encoding, hasEncoding := inst.dictValue("encoding").(string)
	reason, hasReason := inst.dictValue("reason").(string)
	start, hasStart := asInt(inst.dictValue("start"))
	end, hasEnd := asInt(inst.dictValue("end"))
	if !hasEncoding || !hasReason || !hasStart || !hasEnd || start < 0 || end <= start {
		return "", false
	}
	if inst.Class.IsSub(excClasses["UnicodeEncodeError"]) {
		text, ok := inst.dictValue("object").(string)
		if !ok {
			return "", false
		}
		if end > start+1 {
			return fmt.Sprintf("'%s' codec can't encode characters in position %d-%d: %s",
				encoding, start, end-1, reason), true
		}
		index := int64(0)
		for i := 0; i < len(text); {
			r, size := pyDecodeRuneInString(text[i:])
			i += size
			if index == start {
				var escaped string
				switch {
				case r <= 0xff:
					escaped = fmt.Sprintf("\\x%02x", r)
				case r <= 0xffff:
					escaped = fmt.Sprintf("\\u%04x", r)
				default:
					escaped = fmt.Sprintf("\\U%08x", r)
				}
				return fmt.Sprintf("'%s' codec can't encode character '%s' in position %d: %s",
					encoding, escaped, start, reason), true
			}
			index++
		}
	}
	if inst.Class.IsSub(excClasses["UnicodeDecodeError"]) {
		data, ok := inst.dictValue("object").([]byte)
		if !ok || start >= int64(len(data)) {
			return "", false
		}
		if end > start+1 {
			return fmt.Sprintf("'%s' codec can't decode bytes in position %d-%d: %s",
				encoding, start, end-1, reason), true
		}
		return fmt.Sprintf("'%s' codec can't decode byte 0x%02x in position %d: %s",
			encoding, data[start], start, reason), true
	}
	return "", false
}

type fmtSpec struct {
	fill    rune
	align   rune // 0, '<', '>', '^', '='
	sign    rune // 0, '+', '-', ' '
	zero    bool
	width   int
	comma   bool
	hasPrec bool
	prec    int
	typ     rune // 0 or one of d b o x X f e g % s
}

func parseFmtSpec(spec string) (*fmtSpec, error) {
	fs := &fmtSpec{fill: ' '}
	rs := pyRunes(spec)
	i := 0
	isAlign := func(r rune) bool { return r == '<' || r == '>' || r == '^' || r == '=' }
	if len(rs) >= 2 && isAlign(rs[1]) {
		fs.fill = rs[0]
		fs.align = rs[1]
		i = 2
	} else if len(rs) >= 1 && isAlign(rs[0]) {
		fs.align = rs[0]
		i = 1
	}
	if i < len(rs) && (rs[i] == '+' || rs[i] == '-' || rs[i] == ' ') {
		fs.sign = rs[i]
		i++
	}
	if i < len(rs) && rs[i] == '0' {
		fs.zero = true
		if fs.align == 0 {
			fs.align = '='
			fs.fill = '0'
		}
		i++
	}
	for i < len(rs) && rs[i] >= '0' && rs[i] <= '9' {
		fs.width = fs.width*10 + int(rs[i]-'0')
		i++
	}
	if i < len(rs) && rs[i] == ',' {
		fs.comma = true
		i++
	}
	if i < len(rs) && rs[i] == '.' {
		i++
		if i >= len(rs) || rs[i] < '0' || rs[i] > '9' {
			return nil, newError("ValueError", "Format specifier missing precision")
		}
		fs.hasPrec = true
		for i < len(rs) && rs[i] >= '0' && rs[i] <= '9' {
			fs.prec = fs.prec*10 + int(rs[i]-'0')
			i++
		}
	}
	if i < len(rs) {
		fs.typ = rs[i]
		i++
	}
	if i != len(rs) {
		return nil, newErrorf("ValueError", "Invalid format specifier %s", reprString(spec))
	}
	switch fs.typ {
	case 0, 'd', 'b', 'o', 'x', 'X', 'f', 'e', 'g', '%', 's':
		return fs, nil
	}
	return nil, newErrorf("ValueError", "Unknown format code '%s'", pyRuneString(fs.typ))
}

// formatValue implements format(v, spec) for the supported mini-language.
func (vm *VM) formatValue(v Value, spec string) (string, error) {
	switch v.(type) {
	case int64, float64, bool, string, []byte, NoneType,
		*List, *Tuple, *Dict, *Set, *Range, *big.Int:
	default:
		s, err := vm.str(v)
		if err != nil {
			return "", err
		}
		v = s
	}
	if spec == "" {
		return vm.str(v)
	}
	fs, err := parseFmtSpec(spec)
	if err != nil {
		return "", err
	}
	if b, ok := v.(bool); ok && fs.typ != 's' {
		if b {
			v = int64(1)
		} else {
			v = int64(0)
		}
	}
	switch t := v.(type) {
	case int64:
		return formatInt(t, fs)
	case *big.Int:
		return formatBigInt(t, fs)
	case float64:
		return formatFloat(t, fs)
	case string:
		return formatStr(t, fs)
	case bool:
		return formatStr(map[bool]string{true: "True", false: "False"}[t], fs)
	}
	if fs.typ == 's' || fs.typ == 0 {
		s, err := vm.str(v)
		if err != nil {
			return "", err
		}
		return formatStr(s, fs)
	}
	return "", newErrorf("TypeError", "unsupported format string passed to %s.__format__", typeName(v))
}

func formatStr(s string, fs *fmtSpec) (string, error) {
	switch fs.typ {
	case 0, 's':
	default:
		return "", newErrorf("ValueError", "Unknown format code '%s' for object of type 'str'", pyRuneString(fs.typ))
	}
	if fs.sign != 0 {
		return "", newError("ValueError", "Sign not allowed in string format specifier")
	}
	if fs.align == '=' {
		return "", newError("ValueError", "'=' alignment not allowed in string format specifier")
	}
	length := 0
	for offset := 0; offset < len(s); {
		if fs.hasPrec && length == fs.prec {
			s = s[:offset]
			break
		}
		_, size := pyDecodeRuneInString(s[offset:])
		offset += size
		length++
	}
	align := fs.align
	if align == 0 {
		align = '<'
	}
	return pad(s, length, align, fs.fill, fs.width), nil
}

func formatInt(n int64, fs *fmtSpec) (string, error) {
	if fs.typ == 'f' || fs.typ == 'e' || fs.typ == 'g' || fs.typ == '%' {
		return formatFloat(float64(n), fs)
	}
	if fs.hasPrec {
		return "", newError("ValueError", "Precision not allowed in integer format specifier")
	}
	neg := n < 0
	var mag uint64
	if neg {
		mag = uint64(-(n + 1)) + 1
	} else {
		mag = uint64(n)
	}
	var body string
	switch fs.typ {
	case 0, 'd':
		body = strconv.FormatUint(mag, 10)
		if fs.comma {
			body = groupWithZeroFill(body, "", neg, fs)
		}
	case 'b':
		body = strconv.FormatUint(mag, 2)
	case 'o':
		body = strconv.FormatUint(mag, 8)
	case 'x':
		body = strconv.FormatUint(mag, 16)
	case 'X':
		body = strings.ToUpper(strconv.FormatUint(mag, 16))
	}
	return padNumber(body, neg, fs), nil
}

func formatFloat(f float64, fs *fmtSpec) (string, error) {
	neg := math.Signbit(f) && !math.IsNaN(f)
	av := math.Abs(f)
	prec := 6
	if fs.hasPrec {
		prec = fs.prec
	}
	var body string
	switch fs.typ {
	case 'f':
		body = strconv.FormatFloat(av, 'f', prec, 64)
	case 'e':
		body = strconv.FormatFloat(av, 'e', prec, 64)
	case '%':
		body = strconv.FormatFloat(av*100, 'f', prec, 64) + "%"
	case 'g':
		if prec == 0 {
			prec = 1
		}
		body = strconv.FormatFloat(av, 'g', prec, 64)
	case 0:
		if fs.hasPrec {
			if prec == 0 {
				prec = 1
			}
			body = strconv.FormatFloat(av, 'g', prec, 64)
		} else {
			body = strings.TrimPrefix(pyFloatStr(av), "-")
		}
	case 'd', 'b', 'o', 'x', 'X':
		return "", newErrorf("ValueError", "Unknown format code '%s' for object of type 'float'", pyRuneString(fs.typ))
	}
	if fs.comma {
		if dot := strings.IndexAny(body, ".e%"); dot >= 0 {
			body = groupWithZeroFill(body[:dot], body[dot:], neg, fs)
		} else {
			body = groupWithZeroFill(body, "", neg, fs)
		}
	}
	return padNumber(body, neg, fs), nil
}

// groupWithZeroFill groups the integer digits with thousands separators;
// under zero padding ('=' alignment with '0' fill) the padding zeros take
// part in the grouping, CPython-style (format(-42, "08,d") == "-000,042").
func groupWithZeroFill(digits, rest string, neg bool, fs *fmtSpec) string {
	grouped := groupDigits(digits)
	if fs.align == '=' && fs.fill == '0' {
		signLen := 0
		if neg || fs.sign == '+' || fs.sign == ' ' {
			signLen = 1
		}
		room := fs.width - signLen - pyRuneCount(rest)
		for len(grouped) < room {
			digits = "0" + digits
			grouped = groupDigits(digits)
		}
	}
	return grouped + rest
}

// groupDigits inserts thousands separators into a run of decimal digits.
func groupDigits(s string) string {
	if len(s) <= 3 {
		return s
	}
	var b strings.Builder
	lead := len(s) % 3
	if lead > 0 {
		b.WriteString(s[:lead])
	}
	for i := lead; i < len(s); i += 3 {
		if b.Len() > 0 {
			b.WriteByte(',')
		}
		b.WriteString(s[i : i+3])
	}
	return b.String()
}

func padNumber(body string, neg bool, fs *fmtSpec) string {
	sign := ""
	if neg {
		sign = "-"
	} else if fs.sign == '+' {
		sign = "+"
	} else if fs.sign == ' ' {
		sign = " "
	}
	align := fs.align
	if align == 0 {
		align = '>'
	}
	if align == '=' {
		n := fs.width - len(sign) - pyRuneCount(body)
		if n < 0 {
			n = 0
		}
		return sign + strings.Repeat(pyRuneString(fs.fill), n) + body
	}
	s := sign + body
	return pad(s, pyRuneCount(s), align, fs.fill, fs.width)
}

func pad(s string, runeLen int, align rune, fill rune, width int) string {
	n := width - runeLen
	if n <= 0 {
		return s
	}
	switch align {
	case '<':
		return s + strings.Repeat(pyRuneString(fill), n)
	case '>':
		return strings.Repeat(pyRuneString(fill), n) + s
	case '^':
		left := n / 2
		return strings.Repeat(pyRuneString(fill), left) + s + strings.Repeat(pyRuneString(fill), n-left)
	}
	return s
}
