package interp

import "math"

// These compensated algorithms follow CPython 3.12.12 Modules/mathmodule.c
// (math_fsum and vector_norm), distributed under the PSF license retained in
// src/diaspore/cpython/LICENSE. Intermediate rounding order is significant.
func biMathFsum(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("math.fsum", kw); err != nil {
		return nil, err
	}
	if err := argCount("math.fsum", args, 1, 1); err != nil {
		return nil, err
	}
	iterator, err := vm.getIter(args[0])
	if err != nil {
		return nil, err
	}
	var storage [32]float64
	partials := storage[:0]
	specialSum, infinitySum := 0.0, 0.0
	for {
		value, found, err := iterator.Next()
		if err != nil {
			return nil, err
		}
		if !found {
			break
		}
		if err := vm.fuel(); err != nil {
			return nil, err
		}
		x, err := mathFloat(vm, value)
		if err != nil {
			return nil, err
		}
		original := x
		count := 0
		for _, y := range partials {
			if math.Abs(x) < math.Abs(y) {
				x, y = y, x
			}
			hi := x + y
			roundedY := hi - x
			lo := y - roundedY
			if lo != 0 {
				partials[count] = lo
				count++
			}
			x = hi
		}
		partials = partials[:count]
		if x != 0 {
			if math.IsInf(x, 0) || math.IsNaN(x) {
				if !math.IsInf(original, 0) && !math.IsNaN(original) {
					return nil, newError("OverflowError", "intermediate overflow in fsum")
				}
				if math.IsInf(original, 0) {
					infinitySum += original
				}
				specialSum += original
				partials = partials[:0]
			} else {
				partials = append(partials, x)
			}
		}
	}
	if specialSum != 0 {
		if math.IsNaN(infinitySum) {
			return nil, newError("ValueError", "-inf + inf in fsum")
		}
		return specialSum, nil
	}
	hi, lo := 0.0, 0.0
	remaining := len(partials)
	if remaining != 0 {
		remaining--
		hi = partials[remaining]
		for remaining > 0 {
			remaining--
			x, y := hi, partials[remaining]
			hi = x + y
			roundedY := hi - x
			lo = y - roundedY
			if lo != 0 {
				break
			}
		}
		// Correct half-even rounding using the sign of the remaining partials.
		if remaining > 0 && ((lo < 0 && partials[remaining-1] < 0) || (lo > 0 && partials[remaining-1] > 0)) {
			y := lo * 2
			x := hi + y
			if x-hi == y {
				hi = x
			}
		}
	}
	return hi, nil
}

func mathPointValues(vm *VM, value Value) ([]Value, error) {
	// CPython reads tuple subclasses' immutable storage directly. Other
	// iterables are materialized before dimension checks or float callbacks.
	if tuple, ok := rawPrim(value).(*Tuple); ok {
		return tuple.Items, nil
	}
	return vm.iterToSlice(value)
}

func biMathDist(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("math.dist", kw); err != nil {
		return nil, err
	}
	if err := argCount("math.dist", args, 2, 2); err != nil {
		return nil, err
	}
	left, err := mathPointValues(vm, args[0])
	if err != nil {
		return nil, err
	}
	right, err := mathPointValues(vm, args[1])
	if err != nil {
		return nil, err
	}
	if len(left) != len(right) {
		return nil, newError("ValueError", "both points must have the same number of dimensions")
	}
	if err := vm.fuelN(int64(len(left))); err != nil {
		return nil, err
	}
	var storage [16]float64
	differences := storage[:]
	if len(left) > len(storage) {
		differences = make([]float64, len(left))
	} else {
		differences = differences[:len(left)]
	}
	maximum, foundNaN := 0.0, false
	for index := range left {
		p, err := mathFloat(vm, left[index])
		if err != nil {
			return nil, err
		}
		q, err := mathFloat(vm, right[index])
		if err != nil {
			return nil, err
		}
		difference := math.Abs(p - q)
		differences[index] = difference
		foundNaN = foundNaN || math.IsNaN(difference)
		if difference > maximum {
			maximum = difference
		}
	}
	return mathVectorNorm(differences, maximum, foundNaN), nil
}

func mathVectorNorm(values []float64, maximum float64, foundNaN bool) float64 {
	if math.IsInf(maximum, 0) {
		return maximum
	}
	if foundNaN {
		return math.NaN()
	}
	if maximum == 0 || len(values) <= 1 {
		return maximum
	}
	_, exponent := math.Frexp(maximum)
	if exponent < -1023 {
		const minimumNormal = 0x1p-1022
		for index := range values {
			values[index] /= minimumNormal
		}
		return minimumNormal * mathVectorNorm(values, maximum/minimumNormal, false)
	}
	scale := math.Ldexp(1, -exponent)
	sum, fraction1, fraction2 := 1.0, 0.0, 0.0
	for _, value := range values {
		x := value * scale
		product := float64(x * x)
		productError := math.FMA(x, x, -product)
		hi := sum + product
		lo := (sum - hi) + product
		sum = hi
		fraction1 += productError
		fraction2 += lo
	}
	norm := math.Sqrt(sum - 1 + (fraction1 + fraction2))
	product := float64(-norm * norm)
	productError := math.FMA(-norm, norm, -product)
	hi := sum + product
	lo := (sum - hi) + product
	sum = hi
	fraction1 += productError
	fraction2 += lo
	correction := sum - 1 + (fraction1 + fraction2)
	norm += correction / (2 * norm)
	return norm / scale
}
