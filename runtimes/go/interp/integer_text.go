package interp

import (
	"strings"
	"unicode"
)

func unicodeDecimalValue(point rune) (byte, bool) {
	if point >= '0' && point <= '9' {
		return byte(point - '0'), true
	}
	if point <= 0xffff {
		for _, interval := range unicode.Nd.R16 {
			if uint32(point) < uint32(interval.Lo) {
				break
			}
			if uint32(point) <= uint32(interval.Hi) && (uint32(point)-uint32(interval.Lo))%uint32(interval.Stride) == 0 {
				return byte(((uint32(point) - uint32(interval.Lo)) / uint32(interval.Stride)) % 10), true
			}
		}
	} else {
		for _, interval := range unicode.Nd.R32 {
			if uint32(point) < interval.Lo {
				break
			}
			if uint32(point) <= interval.Hi && (uint32(point)-interval.Lo)%interval.Stride == 0 {
				return byte(((uint32(point) - interval.Lo) / interval.Stride) % 10), true
			}
		}
	}
	return 0, false
}

// Keep ordinary ASCII parsing allocation-free. CPython accepts all Unicode Nd
// digits, but not other characters that merely have a numeric Unicode value.
func normalizeIntegerText(text string) string {
	for offset := 0; offset < len(text); offset++ {
		if text[offset] < 0x80 {
			continue
		}
		var output strings.Builder
		output.Grow(len(text))
		output.WriteString(text[:offset])
		for offset < len(text) {
			point, size := pyDecodeRuneInString(text[offset:])
			offset += size
			if digit, ok := unicodeDecimalValue(point); ok {
				output.WriteByte('0' + digit)
			} else {
				pyWriteRune(&output, point)
			}
		}
		return output.String()
	}
	return text
}
