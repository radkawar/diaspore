package interp

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
)

// jsonDumps serializes v like CPython's json.dumps (default separators,
// ensure_ascii=True, floats via repr, optional indent).
func (vm *VM) jsonDumps(v Value, indentV Value) (string, error) {
	hasIndent := false
	indent := 0
	if _, isNone := indentV.(NoneType); !isNone {
		n, ok := asInt(indentV)
		if !ok {
			return "", newError("TypeError", "indent must be an int or None")
		}
		hasIndent = true
		if n > 0 {
			indent = int(n)
		}
	}
	var b strings.Builder
	if err := jsonWrite(&b, v, indent, hasIndent, 0); err != nil {
		return "", err
	}
	return b.String(), nil
}

func jsonWrite(b *strings.Builder, v Value, indent int, hasIndent bool, depth int) error {
	pad := func(d int) {
		if hasIndent {
			b.WriteByte('\n')
			b.WriteString(strings.Repeat(" ", indent*d))
		}
	}
	itemSep := ", "
	if hasIndent {
		itemSep = ","
	}
	switch t := v.(type) {
	case NoneType:
		b.WriteString("null")
	case bool:
		if t {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case int64:
		b.WriteString(strconv.FormatInt(t, 10))
	case *big.Int:
		b.WriteString(t.String())
	case float64:
		switch {
		case math.IsInf(t, 1):
			b.WriteString("Infinity")
		case math.IsInf(t, -1):
			b.WriteString("-Infinity")
		case math.IsNaN(t):
			b.WriteString("NaN")
		default:
			b.WriteString(pyFloatStr(t))
		}
	case string:
		jsonEscapeString(b, t)
	case *List:
		return jsonWriteArray(b, t.Items, indent, hasIndent, depth, itemSep, pad)
	case *Tuple:
		return jsonWriteArray(b, t.Items, indent, hasIndent, depth, itemSep, pad)
	case *Dict:
		if t.Len() == 0 {
			b.WriteString("{}")
			return nil
		}
		b.WriteByte('{')
		for i, e := range t.entries {
			if i > 0 {
				b.WriteString(itemSep)
			}
			pad(depth + 1)
			ks, ok := e.key.(string)
			if !ok {
				return newError("TypeError", "json keys must be str")
			}
			jsonEscapeString(b, ks)
			b.WriteString(": ")
			if err := jsonWrite(b, e.val, indent, hasIndent, depth+1); err != nil {
				return err
			}
		}
		pad(depth)
		b.WriteByte('}')
	default:
		return newErrorf("TypeError", "Object of type %s is not JSON serializable", typeName(v))
	}
	return nil
}

func jsonWriteArray(b *strings.Builder, items []Value, indent int, hasIndent bool,
	depth int, itemSep string, pad func(int)) error {
	if len(items) == 0 {
		b.WriteString("[]")
		return nil
	}
	b.WriteByte('[')
	for i, x := range items {
		if i > 0 {
			b.WriteString(itemSep)
		}
		pad(depth + 1)
		if err := jsonWrite(b, x, indent, hasIndent, depth+1); err != nil {
			return err
		}
	}
	pad(depth)
	b.WriteByte(']')
	return nil
}

func jsonEscapeString(b *strings.Builder, s string) {
	b.WriteByte('"')
	for len(s) > 0 {
		r, width := pyDecodeRuneInString(s)
		s = s[width:]
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			switch {
			case r < 0x20:
				fmt.Fprintf(b, `\u%04x`, r)
			case r < 0x7F:
				b.WriteRune(r)
			case r > 0xFFFF:
				// surrogate pair
				r -= 0x10000
				fmt.Fprintf(b, `\u%04x\u%04x`, 0xD800+(r>>10), 0xDC00+(r&0x3FF))
			default:
				fmt.Fprintf(b, `\u%04x`, r)
			}
		}
	}
	b.WriteByte('"')
}

type jsonParser struct {
	s     string
	i     int
	depth int
	host  bool
}

// DecodeJSON parses JSON into Go maps, slices, strings, float64 numbers, bools,
// and nil. Unlike encoding/json, it preserves lone surrogate escapes in strings
// as WTF-8. Adjacent high/low surrogate escapes decode to one Unicode scalar.
func DecodeJSON(data []byte) (any, error) {
	p := &jsonParser{s: string(data), host: true}
	return p.document()
}

func jsonLoads(s string) (Value, error) {
	p := &jsonParser{s: s}
	return p.document()
}

func (p *jsonParser) document() (Value, error) {
	p.skipWS()
	v, err := p.value()
	if err != nil {
		return nil, err
	}
	p.skipWS()
	if p.i != len(p.s) {
		return nil, newError("ValueError", "invalid JSON: Extra data")
	}
	return v, nil
}

func (p *jsonParser) fail(msg string) error {
	return newErrorf("ValueError", "invalid JSON: %s", msg)
}

func (p *jsonParser) skipWS() {
	for p.i < len(p.s) {
		switch p.s[p.i] {
		case ' ', '\t', '\n', '\r':
			p.i++
		default:
			return
		}
	}
}

func (p *jsonParser) value() (Value, error) {
	if p.i >= len(p.s) {
		return nil, p.fail("Expecting value")
	}
	if p.s[p.i] == '{' || p.s[p.i] == '[' {
		if p.depth >= 10000 {
			return nil, p.fail("maximum nesting depth exceeded")
		}
		p.depth++
		defer func() { p.depth-- }()
	}
	switch c := p.s[p.i]; {
	case c == '{':
		return p.object()
	case c == '[':
		return p.array()
	case c == '"':
		return p.string()
	case c == 't':
		if p.lit("true") {
			return true, nil
		}
	case c == 'f':
		if p.lit("false") {
			return false, nil
		}
	case c == 'n':
		if p.lit("null") {
			if p.host {
				return nil, nil
			}
			return None, nil
		}
	case c == 'N':
		if !p.host && p.lit("NaN") {
			return math.NaN(), nil
		}
	case c == 'I':
		if !p.host && p.lit("Infinity") {
			return math.Inf(1), nil
		}
	case c == '-' || (c >= '0' && c <= '9'):
		return p.number()
	}
	return nil, p.fail("Expecting value")
}

func (p *jsonParser) lit(word string) bool {
	if strings.HasPrefix(p.s[p.i:], word) {
		p.i += len(word)
		return true
	}
	return false
}

func (p *jsonParser) number() (Value, error) {
	start := p.i
	if p.s[p.i] == '-' {
		p.i++
		if !p.host && p.lit("Infinity") {
			return math.Inf(-1), nil
		}
	}
	if p.i >= len(p.s) {
		return nil, p.fail("Expecting value")
	}
	if p.s[p.i] == '0' {
		p.i++
	} else if p.s[p.i] >= '1' && p.s[p.i] <= '9' {
		p.digits()
	} else {
		return nil, p.fail("Expecting value")
	}
	isFloat := false
	if p.i < len(p.s) && p.s[p.i] == '.' {
		isFloat = true
		p.i++
		if !p.digits() {
			return nil, p.fail("Expecting digit after decimal point")
		}
	}
	if p.i < len(p.s) && (p.s[p.i] == 'e' || p.s[p.i] == 'E') {
		isFloat = true
		p.i++
		if p.i < len(p.s) && (p.s[p.i] == '+' || p.s[p.i] == '-') {
			p.i++
		}
		if !p.digits() {
			return nil, p.fail("Expecting exponent digits")
		}
	}
	text := p.s[start:p.i]
	if !isFloat && !p.host {
		if n, err := strconv.ParseInt(text, 10, 64); err == nil {
			return n, nil
		}
		if z, ok := new(big.Int).SetString(text, 10); ok {
			return normBig(z), nil
		}
		return nil, p.fail("Invalid integer")
	}
	f, err := strconv.ParseFloat(text, 64)
	if err != nil {
		// Python accepts an overflowing JSON float as infinity. Host decoding
		// keeps encoding/json's rejection of numbers outside float64 range.
		if numErr, ok := err.(*strconv.NumError); !ok || numErr.Err != strconv.ErrRange || p.host {
			return nil, p.fail("Number out of range")
		}
	}
	return f, nil
}

func (p *jsonParser) digits() bool {
	start := p.i
	for p.i < len(p.s) && p.s[p.i] >= '0' && p.s[p.i] <= '9' {
		p.i++
	}
	return p.i != start
}

func (p *jsonParser) string() (string, error) {
	p.i++ // opening quote
	start := p.i
	escaped := false
	var b strings.Builder
	for p.i < len(p.s) {
		c := p.s[p.i]
		switch {
		case c == '"':
			text := p.s[start:p.i]
			p.i++
			if !escaped {
				return text, nil
			}
			b.WriteString(text)
			return b.String(), nil
		case c == '\\':
			escaped = true
			b.WriteString(p.s[start:p.i])
			p.i++
			if p.i >= len(p.s) {
				return "", p.fail("Unterminated string starting at")
			}
			switch p.s[p.i] {
			case '"', '\\', '/':
				b.WriteByte(p.s[p.i])
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
			case 'u':
				r, err := p.uEscape()
				if err != nil {
					return "", err
				}
				if r >= 0xD800 && r <= 0xDBFF && p.i+6 < len(p.s) &&
					p.s[p.i+1] == '\\' && p.s[p.i+2] == 'u' {
					save := p.i
					p.i += 2
					r2, err := p.uEscape()
					if err != nil {
						return "", err
					}
					if r2 >= 0xDC00 && r2 <= 0xDFFF {
						r = 0x10000 + ((r - 0xD800) << 10) + (r2 - 0xDC00)
					} else {
						p.i = save
					}
				}
				b.WriteString(pyRuneString(r))
			default:
				return "", p.fail("Invalid \\escape")
			}
			p.i++
			start = p.i
		case c < 0x20:
			return "", p.fail("Invalid control character")
		case c < 0x80:
			p.i++
		default:
			r, width := pyDecodeRuneInString(p.s[p.i:])
			if r == '\uFFFD' && width == 1 {
				return "", p.fail("Invalid UTF-8 in string")
			}
			p.i += width
		}
	}
	return "", p.fail("Unterminated string starting at")
}

// uEscape parses the 4 hex digits after \u; on entry p.i is at 'u'.
func (p *jsonParser) uEscape() (rune, error) {
	if p.i+4 >= len(p.s) {
		return 0, p.fail("Invalid \\uXXXX escape")
	}
	var n rune
	for _, c := range p.s[p.i+1 : p.i+5] {
		n <<= 4
		switch {
		case c >= '0' && c <= '9':
			n |= c - '0'
		case c >= 'a' && c <= 'f':
			n |= c - 'a' + 10
		case c >= 'A' && c <= 'F':
			n |= c - 'A' + 10
		default:
			return 0, p.fail("Invalid \\uXXXX escape")
		}
	}
	p.i += 4
	return n, nil
}

func (p *jsonParser) array() (Value, error) {
	p.i++ // '['
	var items []Value
	if p.host {
		items = make([]Value, 0)
	}
	p.skipWS()
	if p.i < len(p.s) && p.s[p.i] == ']' {
		p.i++
		if p.host {
			return items, nil
		}
		return &List{Items: items}, nil
	}
	for {
		p.skipWS()
		v, err := p.value()
		if err != nil {
			return nil, err
		}
		items = append(items, v)
		p.skipWS()
		if p.i >= len(p.s) {
			return nil, p.fail("Expecting ',' delimiter")
		}
		switch p.s[p.i] {
		case ',':
			p.i++
		case ']':
			p.i++
			if p.host {
				return items, nil
			}
			return &List{Items: items}, nil
		default:
			return nil, p.fail("Expecting ',' delimiter")
		}
	}
}

func (p *jsonParser) object() (Value, error) {
	p.i++ // '{'
	var out *Dict
	var object map[string]any
	if p.host {
		object = make(map[string]any)
	} else {
		out = NewDict()
	}
	p.skipWS()
	if p.i < len(p.s) && p.s[p.i] == '}' {
		p.i++
		if p.host {
			return object, nil
		}
		return out, nil
	}
	for {
		p.skipWS()
		if p.i >= len(p.s) || p.s[p.i] != '"' {
			return nil, p.fail("Expecting property name enclosed in double quotes")
		}
		k, err := p.string()
		if err != nil {
			return nil, err
		}
		p.skipWS()
		if p.i >= len(p.s) || p.s[p.i] != ':' {
			return nil, p.fail("Expecting ':' delimiter")
		}
		p.i++
		p.skipWS()
		v, err := p.value()
		if err != nil {
			return nil, err
		}
		if p.host {
			object[k] = v
		} else {
			out.SetStr(k, v)
		}
		p.skipWS()
		if p.i >= len(p.s) {
			return nil, p.fail("Expecting ',' delimiter")
		}
		switch p.s[p.i] {
		case ',':
			p.i++
		case '}':
			p.i++
			if p.host {
				return object, nil
			}
			return out, nil
		default:
			return nil, p.fail("Expecting ',' delimiter")
		}
	}
}
