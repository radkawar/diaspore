package interp

import (
	"math"
	"math/big"
	"strings"
)

// Diaspore ints are arbitrary precision like CPython. The int64 fast path handles small values;
// arithmetic promotes to *big.Int only on actual overflow, and every big
// result that fits int64 demotes back - so a *big.Int value is always
// outside the int64 range (which keeps hashing and equality canonical).

// normBig demotes a big result that fits int64.
func normBig(z *big.Int) Value {
	if z.IsInt64() {
		return mkInt(z.Int64())
	}
	return z
}

// asBig converts an int-kind value to *big.Int (never mutate the result of a
// *big.Int passthrough).
func asBig(v Value) (*big.Int, bool) {
	switch t := v.(type) {
	case int64:
		return big.NewInt(t), true
	case bool:
		if t {
			return big.NewInt(1), true
		}
		return big.NewInt(0), true
	case *big.Int:
		return t, true
	}
	return nil, false
}

func isIntKind(v Value) bool {
	switch v.(type) {
	case int64, bool, *big.Int:
		return true
	}
	return false
}

// bigToFloat converts with round-to-nearest like CPython's float(int).
func bigToFloat(z *big.Int) float64 {
	f, _ := new(big.Float).SetInt(z).Float64()
	return f
}

// bigFromFloat converts an integral float to its exact integer value.
func bigFromFloat(f float64) *big.Int {
	z, _ := new(big.Float).SetFloat64(f).Int(nil)
	return z
}

// intFromFloat truncates a finite float toward zero, promoting when the
// result exceeds int64.
func intFromFloat(f float64) Value {
	t := math.Trunc(f)
	if t >= math.MinInt64 && t <= math.MaxInt64 {
		return mkInt(int64(t))
	}
	return bigFromFloat(t)
}

// bigFloatCmp compares a big int with a finite float exactly.
func bigFloatCmp(z *big.Int, f float64) int {
	return new(big.Float).SetInt(z).Cmp(new(big.Float).SetFloat64(f))
}

func addOvf(a, b int64) (int64, bool) {
	c := a + b
	return c, (a >= 0) == (b >= 0) && (c >= 0) != (a >= 0)
}

func subOvf(a, b int64) (int64, bool) {
	c := a - b
	return c, (a >= 0) != (b >= 0) && (c >= 0) != (a >= 0)
}

func mulOvf(a, b int64) (int64, bool) {
	if a == 0 || b == 0 {
		return 0, false
	}
	if (a == -1 && b == math.MinInt64) || (b == -1 && a == math.MinInt64) {
		return 0, true
	}
	c := a * b
	return c, c/b != a
}

func lshOvf(a, b int64) (int64, bool) {
	if a == 0 {
		return 0, false
	}
	if b >= 63 {
		return 0, true
	}
	c := a << uint(b)
	return c, c>>uint(b) != a
}

func (vm *VM) bigBinop(kind string, a, b *big.Int) (Value, error) {
	switch kind {
	case "matmul":
		return nil, vm.binopTypeError(kind, a, b)
	case "add":
		return normBig(new(big.Int).Add(a, b)), nil
	case "sub":
		return normBig(new(big.Int).Sub(a, b)), nil
	case "mul":
		if err := vm.fuelN(int64(a.BitLen()+b.BitLen())/512 + 1); err != nil {
			return nil, err
		}
		return normBig(new(big.Int).Mul(a, b)), nil
	case "div":
		if b.Sign() == 0 {
			return nil, newError("ZeroDivisionError", "division by zero")
		}
		q := new(big.Float).SetPrec(200).SetInt(a)
		q.Quo(q, new(big.Float).SetPrec(200).SetInt(b))
		f, _ := q.Float64()
		return f, nil
	case "floordiv", "mod":
		if b.Sign() == 0 {
			if kind == "mod" {
				return nil, newError("ZeroDivisionError", "integer modulo by zero")
			}
			return nil, newError("ZeroDivisionError", "integer division or modulo by zero")
		}
		q, r := new(big.Int).QuoRem(a, b, new(big.Int))
		if r.Sign() != 0 && (r.Sign() < 0) != (b.Sign() < 0) {
			q.Sub(q, big.NewInt(1))
			r.Add(r, b)
		}
		if kind == "floordiv" {
			return normBig(q), nil
		}
		return normBig(r), nil
	case "pow":
		if b.Sign() < 0 {
			if a.Sign() == 0 {
				return nil, newError("ZeroDivisionError",
					"0.0 cannot be raised to a negative power")
			}
			return math.Pow(bigToFloat(a), bigToFloat(b)), nil
		}
		if !b.IsInt64() {
			return nil, newError("OverflowError", "exponent too large")
		}
		if err := vm.fuelN(int64(a.BitLen())*b.Int64()/512 + 1); err != nil {
			return nil, err
		}
		return normBig(new(big.Int).Exp(a, b, nil)), nil
	case "lshift":
		if b.Sign() < 0 {
			return nil, newError("ValueError", "negative shift count")
		}
		if !b.IsInt64() || b.Int64() > math.MaxUint32 {
			return nil, newError("OverflowError", "shift count too large")
		}
		if err := vm.fuelN((int64(a.BitLen())+b.Int64())/512 + 1); err != nil {
			return nil, err
		}
		return normBig(new(big.Int).Lsh(a, uint(b.Int64()))), nil
	case "rshift":
		if b.Sign() < 0 {
			return nil, newError("ValueError", "negative shift count")
		}
		if !b.IsInt64() {
			if a.Sign() < 0 {
				return mkInt(-1), nil
			}
			return mkInt(0), nil
		}
		sh := b.Int64()
		if sh > math.MaxUint32 {
			sh = math.MaxUint32
		}
		return normBig(new(big.Int).Rsh(a, uint(sh))), nil
	case "bitand":
		return normBig(new(big.Int).And(a, b)), nil
	case "bitor":
		return normBig(new(big.Int).Or(a, b)), nil
	case "bitxor":
		return normBig(new(big.Int).Xor(a, b)), nil
	}
	return nil, newErrorf("TypeError", "unknown int op %s", kind)
}

// formatBigInt renders a big int for the format-spec mini-language.
func formatBigInt(z *big.Int, fs *fmtSpec) (string, error) {
	switch fs.typ {
	case 'f', 'e', 'g', '%':
		return formatFloat(bigToFloat(z), fs)
	}
	if fs.hasPrec {
		return "", newError("ValueError", "Precision not allowed in integer format specifier")
	}
	neg := z.Sign() < 0
	mag := new(big.Int).Abs(z)
	var body string
	switch fs.typ {
	case 0, 'd':
		body = mag.Text(10)
		if fs.comma {
			body = groupWithZeroFill(body, "", neg, fs)
		}
	case 'b':
		body = mag.Text(2)
	case 'o':
		body = mag.Text(8)
	case 'x':
		body = mag.Text(16)
	case 'X':
		body = strings.ToUpper(mag.Text(16))
	}
	return padNumber(body, neg, fs), nil
}
