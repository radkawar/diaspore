package interp

import (
	"math"
	"math/big"
)

const numericHashModulus uint64 = (1 << 61) - 1

func pythonIntegerHash(value Value) int64 {
	var magnitude uint64
	negative := false
	if wide, ok := value.(*big.Int); ok {
		var remainder, modulus big.Int
		modulus.SetUint64(numericHashModulus)
		remainder.Rem(wide, &modulus)
		hash := remainder.Int64()
		if hash == -1 {
			hash = -2
		}
		return hash
	}
	n, _ := asInt(value)
	negative = n < 0
	magnitude = uint64(n)
	if negative {
		magnitude = 0 - magnitude
	}
	hash := int64(magnitude % numericHashModulus)
	if negative {
		hash = -hash
	}
	if hash == -1 {
		hash = -2
	}
	return hash
}

// Hash a finite IEEE-754 value as a rational modulo 2**61-1. Rotations encode
// multiplication/division by powers of two without allocating big integers.
func pythonFloatHash(value float64) int64 {
	if math.IsInf(value, 1) {
		return 314159
	}
	if math.IsInf(value, -1) {
		return -314159
	}
	mantissa, exponent := math.Frexp(value)
	negative := mantissa < 0
	if negative {
		mantissa = -mantissa
	}
	var hash uint64
	for mantissa != 0 {
		hash = ((hash << 28) & numericHashModulus) | (hash >> 33)
		mantissa *= 268435456
		exponent -= 28
		part := uint64(mantissa)
		mantissa -= float64(part)
		hash += part
		if hash >= numericHashModulus {
			hash -= numericHashModulus
		}
	}
	exponent %= 61
	if exponent < 0 {
		exponent += 61
	}
	if exponent != 0 {
		hash = ((hash << uint(exponent)) & numericHashModulus) | (hash >> uint(61-exponent))
	}
	result := int64(hash)
	if negative {
		result = -result
	}
	if result == -1 {
		result = -2
	}
	return result
}
