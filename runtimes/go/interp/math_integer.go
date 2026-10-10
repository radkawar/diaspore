package interp

import (
	"fmt"
	"math"
	"math/big"
	"math/bits"
)

var mathSmallFactorials = [...]int64{
	1, 1, 2, 6, 24, 120, 720, 5040, 40320, 362880,
	3628800, 39916800, 479001600, 6227020800, 87178291200,
	1307674368000, 20922789888000, 355687428096000,
	6402373705728000, 121645100408832000, 2432902008176640000,
}

// Precharge native bulk work without overflowing the VM's step counter.
func mathIntegerFuel(vm *VM, work int64) error {
	if vm.maxSteps > 0 && (vm.steps >= vm.maxSteps || work > vm.maxSteps-vm.steps) {
		return &Trap{Msg: fmt.Sprintf("diaspore: step budget exceeded (%d)", vm.maxSteps), Code: 3}
	}
	if work > math.MaxInt64-vm.steps {
		vm.steps = math.MaxInt64
		return nil
	}
	return vm.fuelN(work)
}

func mathAbsUint(value int64) uint64 {
	if value < 0 {
		return uint64(-(value + 1)) + 1
	}
	return uint64(value)
}

func mathGCDUint(left, right uint64) uint64 {
	for right != 0 {
		left, right = right, left%right
	}
	return left
}

func mathPositiveUint(value uint64) Value {
	if value <= math.MaxInt64 {
		return mkInt(int64(value))
	}
	return new(big.Int).SetUint64(value)
}

func biMathGCD(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("gcd", kw); err != nil {
		return nil, err
	}
	var result Value = int64(0)
	for _, argument := range args {
		if err := mathIntegerFuel(vm, 1); err != nil {
			return nil, err
		}
		value, err := percentIndex(vm, argument)
		if err != nil {
			return nil, err
		}
		if result == int64(1) {
			continue
		}
		left, leftSmall := asInt(result)
		right, rightSmall := asInt(value)
		if leftSmall && rightSmall {
			result = mathPositiveUint(mathGCDUint(mathAbsUint(left), mathAbsUint(right)))
			continue
		}
		a, _ := asBig(result)
		b, _ := asBig(value)
		if err := mathIntegerFuel(vm, int64(a.BitLen()/64+b.BitLen()/64+1)); err != nil {
			return nil, err
		}
		result = normBig(new(big.Int).GCD(nil, nil, a, b))
	}
	return result, nil
}

func biMathLCM(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("lcm", kw); err != nil {
		return nil, err
	}
	var result Value = int64(1)
	for _, argument := range args {
		if err := mathIntegerFuel(vm, 1); err != nil {
			return nil, err
		}
		value, err := percentIndex(vm, argument)
		if err != nil {
			return nil, err
		}
		if result == int64(0) {
			continue
		}
		left, leftSmall := asInt(result)
		right, rightSmall := asInt(value)
		if rightSmall && right == 0 {
			result = int64(0)
			continue
		}
		if leftSmall && rightSmall {
			a, b := mathAbsUint(left), mathAbsUint(right)
			hi, lo := bits.Mul64(a/mathGCDUint(a, b), b)
			if hi == 0 {
				result = mathPositiveUint(lo)
				continue
			}
		}
		a, _ := asBig(result)
		b, _ := asBig(value)
		if err := mathIntegerFuel(vm, int64(a.BitLen()/64+b.BitLen()/64+1)); err != nil {
			return nil, err
		}
		var divisor big.Int
		divisor.GCD(nil, nil, a, b)
		product := new(big.Int).Quo(a, &divisor)
		product.Mul(product, b)
		result = normBig(product.Abs(product))
	}
	return result, nil
}

func biMathFactorial(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("factorial", args, 1, 1); err != nil {
		return nil, err
	}
	if err := noKw("factorial", kw); err != nil {
		return nil, err
	}
	return mathFactorial(vm, args[0])
}

func mathFactorial(vm *VM, argument Value) (Value, error) {
	value, err := percentIndex(vm, argument)
	if err != nil {
		return nil, err
	}
	n, small := asInt(value)
	if !small {
		large, _ := asBig(value)
		if large.Sign() < 0 {
			return nil, newError("ValueError", "factorial() not defined for negative values")
		}
		if !large.IsInt64() {
			return nil, newError("OverflowError", "factorial() argument should not exceed 9223372036854775807")
		}
		n = large.Int64()
	}
	if n < 0 {
		return nil, newError("ValueError", "factorial() not defined for negative values")
	}
	if n < int64(len(mathSmallFactorials)) {
		return mkInt(mathSmallFactorials[n]), nil
	}
	if err := mathIntegerFuel(vm, n); err != nil {
		return nil, err
	}
	return normBig(new(big.Int).MulRange(2, n)), nil
}

func biMathComb(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("comb", args, 2, 2); err != nil {
		return nil, err
	}
	if err := noKw("comb", kw); err != nil {
		return nil, err
	}
	return mathPermComb(vm, args[0], args[1], true)
}

func biMathPerm(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("perm", args, 1, 2); err != nil {
		return nil, err
	}
	if err := noKw("perm", kw); err != nil {
		return nil, err
	}
	if len(args) == 1 {
		return mathFactorial(vm, args[0])
	}
	if _, none := args[1].(NoneType); none {
		return mathFactorial(vm, args[0])
	}
	return mathPermComb(vm, args[0], args[1], false)
}

func mathPermComb(vm *VM, nValue, kValue Value, combination bool) (Value, error) {
	nIndex, err := percentIndex(vm, nValue)
	if err != nil {
		return nil, err
	}
	kIndex, err := percentIndex(vm, kValue)
	if err != nil {
		return nil, err
	}
	n, _ := asBig(nIndex)
	k, _ := asBig(kIndex)
	if n.Sign() < 0 {
		return nil, newError("ValueError", "n must be a non-negative integer")
	}
	if k.Sign() < 0 {
		return nil, newError("ValueError", "k must be a non-negative integer")
	}
	if n.Cmp(k) < 0 {
		return int64(0), nil
	}
	var complement big.Int
	if combination {
		complement.Sub(n, k)
		if complement.Cmp(k) < 0 {
			k = &complement
		}
	}
	if !k.IsInt64() {
		if combination {
			return nil, newError("OverflowError", "min(n - k, k) must not exceed 9223372036854775807")
		}
		return nil, newError("OverflowError", "k must not exceed 9223372036854775807")
	}
	count := k.Int64()
	if count == 0 {
		return int64(1), nil
	}
	if count == 1 {
		return normBig(n), nil
	}
	if err := mathIntegerFuel(vm, count); err != nil {
		return nil, err
	}
	if !combination && n.IsInt64() {
		upper := n.Int64()
		return normBig(new(big.Int).MulRange(upper-count+1, upper)), nil
	}
	// Reuse all arithmetic temporaries. Division at each combination step
	// keeps intermediates close to the result instead of constructing n!.
	result := big.NewInt(1)
	var factor, divisor big.Int
	factor.Sub(n, k)
	one := big.NewInt(1)
	for index := int64(0); index < count; index++ {
		if err := mathIntegerFuel(vm, int64(result.BitLen()/64+factor.BitLen()/64+1)); err != nil {
			return nil, err
		}
		factor.Add(&factor, one)
		result.Mul(result, &factor)
		if combination {
			divisor.SetInt64(index + 1)
			result.Quo(result, &divisor)
		}
	}
	return normBig(result), nil
}
