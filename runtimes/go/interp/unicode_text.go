package interp

import (
	"fmt"
	"strings"
	"unicode/utf8"
)

// Python strings use UTF-8 with three-byte encodings for surrogate codepoints.
// Adjacent surrogate codepoints remain separate; only a JSON escape decoder may
// combine a surrogate pair. Other malformed bytes decode as RuneError, one byte
// at a time, just as in unicode/utf8. Codecs must validate external bytes before
// constructing a Python string rather than use that malformed-input behavior as
// an error handler. Runes outside 0..0x10ffff encode as RuneError.

func irString(n node, field string) (string, error) {
	raw, present := n["codepoints"]
	if !present {
		return nStr(n, field), nil
	}
	points, ok := raw.([]any)
	if !ok {
		return "", fmt.Errorf("diaspore: codepoints must be an array")
	}
	var text strings.Builder
	text.Grow(len(points))
	for _, value := range points {
		point, ok := value.(float64)
		if !ok || point < 0 || point > utf8.MaxRune || point != float64(rune(point)) {
			return "", fmt.Errorf("diaspore: invalid string codepoint")
		}
		pyWriteRune(&text, rune(point))
	}
	return text.String(), nil
}

func preserveIRCodepoints(n node, field, text string) {
	for offset := 0; offset < len(text); {
		point, size := pyDecodeRuneInString(text[offset:])
		offset += size
		if point >= 0xd800 && point <= 0xdfff {
			points := make([]any, 0, pyRuneCount(text))
			var canonical strings.Builder
			canonical.Grow(len(text))
			for offset = 0; offset < len(text); {
				point, size = pyDecodeRuneInString(text[offset:])
				offset += size
				points = append(points, float64(point))
				if point >= 0xd800 && point <= 0xdbff && offset < len(text) {
					low, width := pyDecodeRuneInString(text[offset:])
					if low >= 0xdc00 && low <= 0xdfff {
						points = append(points, float64(low))
						offset += width
						point = 0x10000 + (point-0xd800)<<10 + low - 0xdc00
					}
				}
				pyWriteRune(&canonical, point)
			}
			n["codepoints"] = points
			n[field] = canonical.String()
			return
		}
	}
}

func pyDecodeRuneInString(s string) (rune, int) {
	if len(s) == 0 {
		return utf8.RuneError, 0
	}
	if s[0] < utf8.RuneSelf {
		return rune(s[0]), 1
	}
	if s[0] == 0xed && len(s) >= 3 && s[1] >= 0xa0 && s[1] <= 0xbf && s[2]&0xc0 == 0x80 {
		return rune(s[0]&0x0f)<<12 | rune(s[1]&0x3f)<<6 | rune(s[2]&0x3f), 3
	}
	return utf8.DecodeRuneInString(s)
}

// pyEncodeRune has utf8.EncodeRune's buffer-size contract, but accepts surrogates.
func pyEncodeRune(p []byte, r rune) int {
	if r >= 0xd800 && r <= 0xdfff {
		_ = p[2]
		p[0] = 0xe0 | byte(r>>12)
		p[1] = 0x80 | byte(r>>6)&0x3f
		p[2] = 0x80 | byte(r)&0x3f
		return 3
	}
	return utf8.EncodeRune(p, r)
}

func pyRuneCount(s string) int {
	n := 0
	for i := 0; i < len(s); n++ {
		if s[i] < utf8.RuneSelf {
			i++
		} else {
			_, size := pyDecodeRuneInString(s[i:])
			i += size
		}
	}
	return n
}

func pyRunes(s string) []rune {
	rs := make([]rune, pyRuneCount(s))
	for i, j := 0, 0; i < len(s); j++ {
		if s[i] < utf8.RuneSelf {
			rs[j] = rune(s[i])
			i++
		} else {
			r, size := pyDecodeRuneInString(s[i:])
			rs[j] = r
			i += size
		}
	}
	return rs
}

func pyRuneString(r rune) string {
	if r < 0xd800 || r > 0xdfff {
		return string(r)
	}
	var buf [3]byte
	pyEncodeRune(buf[:], r)
	return string(buf[:])
}

func pyString(rs []rune) string {
	n := 0
	for _, r := range rs {
		size := utf8.RuneLen(r)
		if size < 0 {
			size = 3
		}
		n += size
	}
	var b strings.Builder
	b.Grow(n)
	var buf [utf8.UTFMax]byte
	for _, r := range rs {
		if r >= 0 && r < utf8.RuneSelf {
			b.WriteByte(byte(r))
		} else {
			size := pyEncodeRune(buf[:], r)
			b.Write(buf[:size])
		}
	}
	return b.String()
}

func pyWriteRune(b *strings.Builder, r rune) {
	if r >= 0 && r < utf8.RuneSelf {
		b.WriteByte(byte(r))
		return
	}
	var buf [utf8.UTFMax]byte
	n := pyEncodeRune(buf[:], r)
	b.Write(buf[:n])
}
