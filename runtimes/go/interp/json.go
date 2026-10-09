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
	for _, r := range s {
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
			case r < 0x80:
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
	s []byte
	i int
}

func jsonLoads(s string) (Value, error) {
	p := &jsonParser{s: []byte(s)}
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
			return None, nil
		}
	case c == 'N':
		if p.lit("NaN") {
			return math.NaN(), nil
		}
	case c == 'I':
		if p.lit("Infinity") {
			return math.Inf(1), nil
		}
	case c == '-' || (c >= '0' && c <= '9'):
		return p.number()
	}
	return nil, p.fail("Expecting value")
}

func (p *jsonParser) lit(word string) bool {
	if strings.HasPrefix(string(p.s[p.i:]), word) {
		p.i += len(word)
		return true
	}
	return false
}

func (p *jsonParser) number() (Value, error) {
	start := p.i
	if p.i < len(p.s) && p.s[p.i] == '-' {
		p.i++
		if p.lit("Infinity") {
			return math.Inf(-1), nil
		}
	}
	isFloat := false
	for p.i < len(p.s) {
		c := p.s[p.i]
		if c >= '0' && c <= '9' {
			p.i++
		} else if c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-' {
			isFloat = isFloat || c == '.' || c == 'e' || c == 'E'
			p.i++
		} else {
			break
		}
	}
	text := string(p.s[start:p.i])
	if !isFloat {
		if n, err := strconv.ParseInt(text, 10, 64); err == nil {
			return n, nil
		}
		if z, ok := new(big.Int).SetString(text, 10); ok {
			return normBig(z), nil
		}
	}
	f, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return nil, p.fail("Expecting value")
	}
	return f, nil
}

func (p *jsonParser) string() (string, error) {
	p.i++ // opening quote
	var b strings.Builder
	for {
		if p.i >= len(p.s) {
			return "", p.fail("Unterminated string starting at")
		}
		c := p.s[p.i]
		if c == '"' {
			p.i++
			return b.String(), nil
		}
		if c == '\\' {
			p.i++
			if p.i >= len(p.s) {
				return "", p.fail("Unterminated string starting at")
			}
			switch p.s[p.i] {
			case '"':
				b.WriteByte('"')
			case '\\':
				b.WriteByte('\\')
			case '/':
				b.WriteByte('/')
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
				if r >= 0xD800 && r <= 0xDBFF && p.i+6 <= len(p.s) &&
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
				b.WriteRune(r)
			default:
				return "", p.fail("Invalid \\escape")
			}
			p.i++
			continue
		}
		b.WriteByte(c)
		p.i++
	}
}

// uEscape parses the 4 hex digits after \u; on entry p.i is at 'u'.
func (p *jsonParser) uEscape() (rune, error) {
	if p.i+4 >= len(p.s) {
		return 0, p.fail("Invalid \\uXXXX escape")
	}
	n, err := strconv.ParseUint(string(p.s[p.i+1:p.i+5]), 16, 32)
	if err != nil {
		return 0, p.fail("Invalid \\uXXXX escape")
	}
	p.i += 4
	return rune(n), nil
}

func (p *jsonParser) array() (Value, error) {
	p.i++ // '['
	out := &List{}
	p.skipWS()
	if p.i < len(p.s) && p.s[p.i] == ']' {
		p.i++
		return out, nil
	}
	for {
		p.skipWS()
		v, err := p.value()
		if err != nil {
			return nil, err
		}
		out.Items = append(out.Items, v)
		p.skipWS()
		if p.i >= len(p.s) {
			return nil, p.fail("Expecting ',' delimiter")
		}
		switch p.s[p.i] {
		case ',':
			p.i++
		case ']':
			p.i++
			return out, nil
		default:
			return nil, p.fail("Expecting ',' delimiter")
		}
	}
}

func (p *jsonParser) object() (Value, error) {
	p.i++ // '{'
	out := NewDict()
	p.skipWS()
	if p.i < len(p.s) && p.s[p.i] == '}' {
		p.i++
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
		out.SetStr(k, v)
		p.skipWS()
		if p.i >= len(p.s) {
			return nil, p.fail("Expecting ',' delimiter")
		}
		switch p.s[p.i] {
		case ',':
			p.i++
		case '}':
			p.i++
			return out, nil
		default:
			return nil, p.fail("Expecting ',' delimiter")
		}
	}
}
