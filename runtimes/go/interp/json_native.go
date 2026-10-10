package interp

import (
	"math"
	"math/big"
	"strconv"
	"strings"
)

// The accelerator implements the private CPython _json interface. Public JSON
// policy (including indentation and subclass overrides) stays in json.py.
func (vm *VM) makeJSONAcceleratorModule() *NativeModule {
	scannerType := vm.metadataType("Scanner")
	scannerType.ctor = jsonNativeMakeScanner
	encoderType := vm.metadataType("Encoder")
	encoderType.ctor = jsonNativeMakeEncoder
	return &NativeModule{Name: "_json", NS: map[string]Value{
		"scanstring":              bi("scanstring", jsonNativeScanString),
		"encode_basestring":       bi("encode_basestring", jsonNativeEncodeString),
		"encode_basestring_ascii": bi("encode_basestring_ascii", jsonNativeEncodeASCII),
		"make_scanner":            scannerType,
		"make_encoder":            encoderType,
	}}
}

func jsonNativeString(v Value) (string, error) {
	if s, ok := rawPrim(v).(string); ok {
		return s, nil
	}
	return "", newErrorf("TypeError", "first argument must be a string, not %s", typeName(v))
}

func jsonNativeIndex(vm *VM, v Value) (int64, error) {
	v, err := percentIndex(vm, v)
	if err != nil {
		return 0, err
	}
	if n, ok := asInt(v); ok {
		return n, nil
	}
	return 0, newError("OverflowError", "Python int too large to convert to C ssize_t")
}

func jsonNativeEncodeASCII(vm *VM, args []Value, kw []KwArg) (Value, error) {
	return jsonNativeEncodeBase(args, kw, true)
}

func jsonNativeEncodeString(vm *VM, args []Value, kw []KwArg) (Value, error) {
	return jsonNativeEncodeBase(args, kw, false)
}

func jsonNativeEncodeBase(args []Value, kw []KwArg, ascii bool) (Value, error) {
	name := "encode_basestring"
	if ascii {
		name += "_ascii"
	}
	if err := noKw(name, kw); err != nil {
		return nil, err
	}
	if err := argCount(name, args, 1, 1); err != nil {
		return nil, err
	}
	s, err := jsonNativeString(args[0])
	if err != nil {
		return nil, err
	}
	var b strings.Builder
	jsonNativeEscape(&b, s, ascii)
	return b.String(), nil
}

func jsonNativeEscape(b *strings.Builder, s string, ascii bool) {
	const hex = "0123456789abcdef"
	b.WriteByte('"')
	for len(s) > 0 {
		r, width := pyDecodeRuneInString(s)
		part := s[:width]
		s = s[width:]
		switch r {
		case '"', '\\':
			b.WriteByte('\\')
			b.WriteByte(byte(r))
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		default:
			if r >= 0x20 && (!ascii || r <= 0x7e) {
				b.WriteString(part)
				continue
			}
			if r > 0xffff {
				high := 0xd800 + ((r - 0x10000) >> 10)
				b.WriteString(`\u`)
				b.WriteByte(hex[(high>>12)&15])
				b.WriteByte(hex[(high>>8)&15])
				b.WriteByte(hex[(high>>4)&15])
				b.WriteByte(hex[high&15])
				r = 0xdc00 + ((r - 0x10000) & 0x3ff)
			}
			b.WriteString(`\u`)
			b.WriteByte(hex[(r>>12)&15])
			b.WriteByte(hex[(r>>8)&15])
			b.WriteByte(hex[(r>>4)&15])
			b.WriteByte(hex[r&15])
		}
	}
	b.WriteByte('"')
}

// Cursor positions track Python codepoints, not UTF-8 bytes. ASCII token
// scanning can advance both offsets together without allocating a rune slice.
type jsonNativeParser struct {
	vm      *VM
	scanner *jsonScanner
	doc     string
	i       int
	pos     int64
	depth   int
}

func (p *jsonNativeParser) fail(msg string, pos int64) error {
	module, err := p.vm.importModule("json.decoder")
	if err != nil {
		return err
	}
	cls, err := p.vm.getAttr(module, "JSONDecodeError")
	if err != nil {
		return err
	}
	exc, err := p.vm.call(cls, []Value{msg, p.doc, pos}, nil)
	if err != nil {
		return err
	}
	return &Raised{Exc: exc}
}

func (p *jsonNativeParser) ascii(n int) {
	p.i += n
	p.pos += int64(n)
}

func (p *jsonNativeParser) space() {
	for p.i < len(p.doc) {
		switch p.doc[p.i] {
		case ' ', '\t', '\r', '\n':
			p.ascii(1)
		default:
			return
		}
	}
}

func (p *jsonNativeParser) seek(pos int64) bool {
	for p.pos < pos && p.i < len(p.doc) {
		_, width := pyDecodeRuneInString(p.doc[p.i:])
		p.i += width
		p.pos++
	}
	return p.pos == pos
}

func (p *jsonNativeParser) hex() (rune, error) {
	// CPython requires another character after the four hex digits.
	if p.i+5 >= len(p.doc) {
		return 0, p.fail("Invalid \\uXXXX escape", p.pos)
	}
	var r rune
	for j := 1; j <= 4; j++ {
		r <<= 4
		c := p.doc[p.i+j]
		switch {
		case c >= '0' && c <= '9':
			r |= rune(c - '0')
		case c >= 'a' && c <= 'f':
			r |= rune(c - 'a' + 10)
		case c >= 'A' && c <= 'F':
			r |= rune(c - 'A' + 10)
		default:
			return 0, p.fail("Invalid \\uXXXX escape", p.pos)
		}
	}
	p.ascii(5)
	return r, nil
}

func (p *jsonNativeParser) string(strict bool) (string, error) {
	begin := p.pos - 1
	start := p.i
	escaped := false
	var b strings.Builder
	for p.i < len(p.doc) {
		c := p.doc[p.i]
		switch {
		case c == '"':
			part := p.doc[start:p.i]
			p.ascii(1)
			if !escaped {
				return part, nil
			}
			b.WriteString(part)
			return b.String(), nil
		case c == '\\':
			escaped = true
			b.WriteString(p.doc[start:p.i])
			p.ascii(1)
			if p.i == len(p.doc) {
				return "", p.fail("Unterminated string starting at", begin)
			}
			c = p.doc[p.i]
			if c == 'u' {
				r, err := p.hex()
				if err != nil {
					return "", err
				}
				if r >= 0xd800 && r <= 0xdbff && p.i+6 < len(p.doc) && p.doc[p.i:p.i+2] == `\u` {
					i, pos := p.i, p.pos
					p.ascii(1)
					low, err := p.hex()
					if err != nil {
						return "", err
					}
					if low >= 0xdc00 && low <= 0xdfff {
						r = 0x10000 + ((r - 0xd800) << 10) + low - 0xdc00
					} else {
						p.i, p.pos = i, pos
					}
				}
				b.WriteString(pyRuneString(r))
			} else {
				switch c {
				case '"', '\\', '/':
					b.WriteByte(c)
				case 'b':
					b.WriteByte('\b')
				case 'f':
					b.WriteByte('\f')
				case 'n':
					b.WriteByte('\n')
				case 'r':
					b.WriteByte('\r')
				case 't':
					b.WriteByte('\t')
				default:
					return "", p.fail("Invalid \\escape", p.pos-1)
				}
				p.ascii(1)
			}
			start = p.i
		case c < 0x20 && strict:
			return "", p.fail("Invalid control character at", p.pos)
		default:
			_, width := pyDecodeRuneInString(p.doc[p.i:])
			p.i += width
			p.pos++
		}
	}
	return "", p.fail("Unterminated string starting at", begin)
}

func jsonNativeScanString(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("scanstring", kw); err != nil {
		return nil, err
	}
	if err := argCount("scanstring", args, 2, 3); err != nil {
		return nil, err
	}
	s, err := jsonNativeString(args[0])
	if err != nil {
		return nil, err
	}
	end, err := jsonNativeIndex(vm, args[1])
	if err != nil {
		return nil, err
	}
	strict := true
	if len(args) == 3 {
		strict, err = vm.truth(args[2])
		if err != nil {
			return nil, err
		}
	}
	p := &jsonNativeParser{vm: vm, doc: s}
	if end < 0 || !p.seek(end) {
		return nil, newError("ValueError", "end is out of bounds")
	}
	text, err := p.string(strict)
	if err != nil {
		return nil, err
	}
	return &Tuple{Items: []Value{text, p.pos}}, nil
}

type jsonScanner struct {
	strict          bool
	objectHook      Value
	objectPairsHook Value
	parseFloat      Value
	parseInt        Value
	parseConstant   Value
}

func (s *jsonScanner) attr(name string) (Value, bool) {
	switch name {
	case "strict":
		return s.strict, true
	case "object_hook":
		return s.objectHook, true
	case "object_pairs_hook":
		return s.objectPairsHook, true
	case "parse_float":
		return s.parseFloat, true
	case "parse_int":
		return s.parseInt, true
	case "parse_constant":
		return s.parseConstant, true
	}
	return nil, false
}

func jsonNativeMakeScanner(vm *VM, args []Value, kw []KwArg) (Value, error) {
	args, err := reArgs("make_scanner", args, kw, []string{"context"}, 1, nil)
	if err != nil {
		return nil, err
	}
	strict, err := vm.getAttr(args[0], "strict")
	if err != nil {
		return nil, err
	}
	s := &jsonScanner{}
	s.strict, err = vm.truth(strict)
	if err != nil {
		return nil, err
	}
	for _, field := range []struct {
		name string
		out  *Value
	}{{"object_hook", &s.objectHook}, {"object_pairs_hook", &s.objectPairsHook}, {"parse_float", &s.parseFloat}, {"parse_int", &s.parseInt}, {"parse_constant", &s.parseConstant}} {
		*field.out, err = vm.getAttr(args[0], field.name)
		if err != nil {
			return nil, err
		}
	}
	return s, nil
}

func (s *jsonScanner) call(vm *VM, args []Value, kw []KwArg) (Value, error) {
	args, err := reArgs("scan_once", args, kw, []string{"string", "idx"}, 2, nil)
	if err != nil {
		return nil, err
	}
	doc, err := jsonNativeString(args[0])
	if err != nil {
		return nil, err
	}
	idx, err := jsonNativeIndex(vm, args[1])
	if err != nil {
		return nil, err
	}
	if idx < 0 {
		return nil, newError("ValueError", "idx cannot be negative")
	}
	p := &jsonNativeParser{vm: vm, scanner: s, doc: doc}
	if !p.seek(idx) {
		return nil, newErrorArgs("StopIteration", idx)
	}
	v, err := p.value()
	if err != nil {
		return nil, err
	}
	return &Tuple{Items: []Value{v, p.pos}}, nil
}

func (p *jsonNativeParser) value() (Value, error) {
	if err := p.vm.fuel(); err != nil {
		return nil, err
	}
	if p.i >= len(p.doc) {
		return nil, newErrorArgs("StopIteration", p.pos)
	}
	switch p.doc[p.i] {
	case '"':
		p.ascii(1)
		return p.string(p.scanner.strict)
	case '{', '[':
		if p.depth >= 1000 {
			return nil, newError("RecursionError", "maximum recursion depth exceeded while decoding a JSON object from a unicode string")
		}
		p.depth++
		defer func() { p.depth-- }()
		if p.doc[p.i] == '{' {
			return p.object()
		}
		return p.array()
	}
	for _, lit := range []struct {
		text string
		v    Value
	}{{"null", None}, {"true", true}, {"false", false}} {
		if strings.HasPrefix(p.doc[p.i:], lit.text) {
			p.ascii(len(lit.text))
			return lit.v, nil
		}
	}
	for _, text := range []string{"NaN", "Infinity", "-Infinity"} {
		if strings.HasPrefix(p.doc[p.i:], text) {
			p.ascii(len(text))
			return p.vm.call(p.scanner.parseConstant, []Value{text}, nil)
		}
	}
	start := p.i
	i := start
	if p.doc[i] == '-' {
		i++
	}
	if i >= len(p.doc) || p.doc[i] < '0' || p.doc[i] > '9' {
		return nil, newErrorArgs("StopIteration", p.pos)
	}
	if p.doc[i] == '0' {
		i++
	} else {
		for i < len(p.doc) && p.doc[i] >= '0' && p.doc[i] <= '9' {
			i++
		}
	}
	floating := false
	if i+1 < len(p.doc) && p.doc[i] == '.' && p.doc[i+1] >= '0' && p.doc[i+1] <= '9' {
		floating = true
		i += 2
		for i < len(p.doc) && p.doc[i] >= '0' && p.doc[i] <= '9' {
			i++
		}
	}
	if i < len(p.doc) && (p.doc[i] == 'e' || p.doc[i] == 'E') {
		end := i + 1
		if end < len(p.doc) && (p.doc[end] == '+' || p.doc[end] == '-') {
			end++
		}
		if end < len(p.doc) && p.doc[end] >= '0' && p.doc[end] <= '9' {
			floating = true
			i = end + 1
			for i < len(p.doc) && p.doc[i] >= '0' && p.doc[i] <= '9' {
				i++
			}
		}
	}
	parser := p.scanner.parseInt
	if floating {
		parser = p.scanner.parseFloat
	}
	p.ascii(i - start)
	return p.vm.call(parser, []Value{p.doc[start:i]}, nil)
}

func (p *jsonNativeParser) array() (Value, error) {
	p.ascii(1)
	p.space()
	out := &List{}
	if p.i < len(p.doc) && p.doc[p.i] == ']' {
		p.ascii(1)
		return out, nil
	}
	for {
		v, err := p.value()
		if err != nil {
			return nil, err
		}
		out.Items = append(out.Items, v)
		p.space()
		if p.i < len(p.doc) && p.doc[p.i] == ']' {
			p.ascii(1)
			return out, nil
		}
		if p.i >= len(p.doc) || p.doc[p.i] != ',' {
			return nil, p.fail("Expecting ',' delimiter", p.pos)
		}
		p.ascii(1)
		p.space()
	}
}

func (p *jsonNativeParser) object() (Value, error) {
	p.ascii(1)
	p.space()
	_, noPairs := p.scanner.objectPairsHook.(NoneType)
	var out *Dict
	var pairs *List
	if noPairs {
		out = NewDict()
	} else {
		pairs = &List{}
	}
	if p.i < len(p.doc) && p.doc[p.i] == '}' {
		p.ascii(1)
	} else {
		for {
			if p.i >= len(p.doc) || p.doc[p.i] != '"' {
				return nil, p.fail("Expecting property name enclosed in double quotes", p.pos)
			}
			p.ascii(1)
			key, err := p.string(p.scanner.strict)
			if err != nil {
				return nil, err
			}
			p.space()
			if p.i >= len(p.doc) || p.doc[p.i] != ':' {
				return nil, p.fail("Expecting ':' delimiter", p.pos)
			}
			p.ascii(1)
			p.space()
			v, err := p.value()
			if err != nil {
				return nil, err
			}
			if noPairs {
				out.SetStr(key, v)
			} else {
				pairs.Items = append(pairs.Items, &Tuple{Items: []Value{key, v}})
			}
			p.space()
			if p.i < len(p.doc) && p.doc[p.i] == '}' {
				p.ascii(1)
				break
			}
			if p.i >= len(p.doc) || p.doc[p.i] != ',' {
				return nil, p.fail("Expecting ',' delimiter", p.pos)
			}
			p.ascii(1)
			p.space()
		}
	}
	if !noPairs {
		return p.vm.call(p.scanner.objectPairsHook, []Value{pairs}, nil)
	}
	if _, none := p.scanner.objectHook.(NoneType); !none {
		return p.vm.call(p.scanner.objectHook, []Value{out}, nil)
	}
	return out, nil
}

type jsonEncoder struct {
	markers       Value
	defaultFn     Value
	encoder       Value
	indent        Value
	keySeparator  string
	itemSeparator string
	sortKeys      bool
	skipKeys      bool
	allowNaN      bool
	fast          int
}

func (e *jsonEncoder) attr(name string) (Value, bool) {
	switch name {
	case "markers":
		return e.markers, true
	case "default":
		return e.defaultFn, true
	case "encoder":
		return e.encoder, true
	case "indent":
		return e.indent, true
	case "key_separator":
		return e.keySeparator, true
	case "item_separator":
		return e.itemSeparator, true
	case "sort_keys":
		return e.sortKeys, true
	case "skipkeys":
		return e.skipKeys, true
	}
	return nil, false
}

func jsonNativeMakeEncoder(vm *VM, args []Value, kw []KwArg) (Value, error) {
	args, err := reArgs("make_encoder", args, kw, []string{"markers", "default", "encoder", "indent", "key_separator", "item_separator", "sort_keys", "skipkeys", "allow_nan"}, 9, nil)
	if err != nil {
		return nil, err
	}
	if _, none := args[0].(NoneType); !none {
		if _, ok := rawPrim(args[0]).(*Dict); !ok {
			return nil, newErrorf("TypeError", "make_encoder() argument 1 must be dict or None, not %s", typeName(args[0]))
		}
	}
	e := &jsonEncoder{markers: args[0], defaultFn: args[1], encoder: args[2], indent: args[3]}
	var ok bool
	e.keySeparator, ok = rawPrim(args[4]).(string)
	if !ok {
		return nil, newErrorf("TypeError", "make_encoder() argument 5 must be str, not %s", typeName(args[4]))
	}
	e.itemSeparator, ok = rawPrim(args[5]).(string)
	if !ok {
		return nil, newErrorf("TypeError", "make_encoder() argument 6 must be str, not %s", typeName(args[5]))
	}
	for i, out := range []*bool{&e.sortKeys, &e.skipKeys, &e.allowNaN} {
		*out, err = vm.truth(args[i+6])
		if err != nil {
			return nil, err
		}
	}
	if module, ok := vm.natives["_json"]; ok {
		if encoder, builtin := e.encoder.(*Builtin); builtin {
			if encoder == module.NS["encode_basestring_ascii"] {
				e.fast = 1
			} else if encoder == module.NS["encode_basestring"] {
				e.fast = 2
			}
		}
	}
	return e, nil
}

func (e *jsonEncoder) call(vm *VM, args []Value, kw []KwArg) (Value, error) {
	args, err := reArgs("_iterencode", args, kw, []string{"obj", "_current_indent_level"}, 2, nil)
	if err != nil {
		return nil, err
	}
	if _, err := jsonNativeIndex(vm, args[1]); err != nil {
		return nil, err
	}
	var b strings.Builder
	if err := e.write(vm, &b, args[0], 0); err != nil {
		return nil, err
	}
	return &Tuple{Items: []Value{b.String()}}, nil
}

func (e *jsonEncoder) string(vm *VM, b *strings.Builder, v Value) error {
	if e.fast != 0 {
		jsonNativeEscape(b, rawPrim(v).(string), e.fast == 1)
		return nil
	}
	encoded, err := vm.call(e.encoder, []Value{v}, nil)
	if err != nil {
		return err
	}
	s, ok := rawPrim(encoded).(string)
	if !ok {
		return newErrorf("TypeError", "encoder() must return a string, not %s", typeName(encoded))
	}
	b.WriteString(s)
	return nil
}

func (e *jsonEncoder) float(v float64) (string, error) {
	if !math.IsNaN(v) && !math.IsInf(v, 0) {
		return pyFloatStr(v), nil
	}
	if !e.allowNaN {
		return "", newErrorf("ValueError", "Out of range float values are not JSON compliant: %s", pyFloatStr(v))
	}
	if math.IsNaN(v) {
		return "NaN", nil
	}
	if math.IsInf(v, -1) {
		return "-Infinity", nil
	}
	return "Infinity", nil
}

func (e *jsonEncoder) mark(vm *VM, v Value) (Value, error) {
	markers, ok := rawPrim(e.markers).(*Dict)
	if !ok {
		return nil, nil
	}
	id, err := biID(vm, []Value{v}, nil)
	if err != nil {
		return nil, err
	}
	if _, exists, err := markers.Get(id); err != nil {
		return nil, err
	} else if exists {
		return nil, newError("ValueError", "Circular reference detected")
	}
	if err := markers.Set(id, v); err != nil {
		return nil, err
	}
	return id, nil
}

func (e *jsonEncoder) unmark(id Value) error {
	if id == nil {
		return nil
	}
	_, found, err := rawPrim(e.markers).(*Dict).Delete(id)
	if err != nil {
		return err
	}
	if !found {
		return newErrorArgs("KeyError", id)
	}
	return nil
}

func (e *jsonEncoder) write(vm *VM, b *strings.Builder, v Value, depth int) error {
	if err := vm.fuel(); err != nil {
		return err
	}
	if depth >= 1000 {
		return newError("RecursionError", "maximum recursion depth exceeded while encoding a JSON object")
	}
	switch t := rawPrim(v).(type) {
	case NoneType:
		b.WriteString("null")
	case bool:
		b.WriteString(strconv.FormatBool(t))
	case string:
		return e.string(vm, b, v)
	case int64:
		b.WriteString(strconv.FormatInt(t, 10))
	case *big.Int:
		b.WriteString(t.String())
	case float64:
		s, err := e.float(t)
		if err != nil {
			return err
		}
		b.WriteString(s)
	case *List:
		return e.sequence(vm, b, v, t.Items, depth)
	case *Tuple:
		return e.sequence(vm, b, v, t.Items, depth)
	case *Dict:
		return e.object(vm, b, v, t, depth)
	default:
		id, err := e.mark(vm, v)
		if err != nil {
			return err
		}
		replacement, err := vm.call(e.defaultFn, []Value{v}, nil)
		if err != nil {
			return err
		}
		if err := e.write(vm, b, replacement, depth+1); err != nil {
			return err
		}
		return e.unmark(id)
	}
	return nil
}

func (e *jsonEncoder) sequence(vm *VM, b *strings.Builder, original Value, items []Value, depth int) error {
	var err error
	if _, subclass := original.(*Instance); subclass {
		items, err = vm.iterToSlice(original)
		if err != nil {
			return err
		}
	}
	if len(items) == 0 {
		b.WriteString("[]")
		return nil
	}
	id, err := e.mark(vm, original)
	if err != nil {
		return err
	}
	b.WriteByte('[')
	for i, v := range items {
		if i != 0 {
			b.WriteString(e.itemSeparator)
		}
		if err := e.write(vm, b, v, depth+1); err != nil {
			return err
		}
	}
	b.WriteByte(']')
	return e.unmark(id)
}

func (e *jsonEncoder) key(v Value) (string, bool, error) {
	switch t := rawPrim(v).(type) {
	case string:
		return t, true, nil
	case NoneType:
		return "null", true, nil
	case bool:
		return strconv.FormatBool(t), true, nil
	case int64:
		return strconv.FormatInt(t, 10), true, nil
	case *big.Int:
		return t.String(), true, nil
	case float64:
		s, err := e.float(t)
		return s, true, err
	}
	if e.skipKeys {
		return "", false, nil
	}
	return "", false, newErrorf("TypeError", "keys must be str, int, float, bool or None, not %s", typeName(v))
}

func (e *jsonEncoder) object(vm *VM, b *strings.Builder, original Value, d *Dict, depth int) error {
	if d.Len() == 0 {
		b.WriteString("{}")
		return nil
	}
	id, err := e.mark(vm, original)
	if err != nil {
		return err
	}
	entries := d.entries
	_, subclass := original.(*Instance)
	if e.sortKeys || subclass {
		var pairs []Value
		if subclass {
			method, err := vm.getAttr(original, "items")
			if err != nil {
				return err
			}
			result, err := vm.call(method, nil, nil)
			if err != nil {
				return err
			}
			pairs, err = vm.iterToSlice(result)
			if err != nil {
				return err
			}
		} else {
			pairs = make([]Value, len(entries))
			for i, entry := range entries {
				pairs[i] = &Tuple{Items: []Value{entry.key, entry.val}}
			}
		}
		if e.sortKeys {
			if err := vm.sortValues(pairs, None, false); err != nil {
				return err
			}
		}
		entries = make([]dictEntry, len(pairs))
		for i, pair := range pairs {
			tuple, ok := rawPrim(pair).(*Tuple)
			if !ok || len(tuple.Items) != 2 {
				return newError("ValueError", "items must return 2-tuples")
			}
			entries[i] = dictEntry{key: tuple.Items[0], val: tuple.Items[1]}
		}
	}
	b.WriteByte('{')
	first := true
	for _, entry := range entries {
		key, include, err := e.key(entry.key)
		if err != nil {
			return err
		}
		if !include {
			continue
		}
		if !first {
			b.WriteString(e.itemSeparator)
		}
		first = false
		var keyValue Value = key
		if _, isString := rawPrim(entry.key).(string); isString {
			keyValue = entry.key
		}
		if err := e.string(vm, b, keyValue); err != nil {
			return err
		}
		b.WriteString(e.keySeparator)
		if err := e.write(vm, b, entry.val, depth+1); err != nil {
			return err
		}
	}
	b.WriteByte('}')
	return e.unmark(id)
}
