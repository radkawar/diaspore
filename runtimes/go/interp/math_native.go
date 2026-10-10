package interp

import (
	"math"
	"math/big"
)

// mathFloat implements PyFloat_AsDouble rather than float(): strings are not
// accepted, conversion slots bind on the type, and oversized integers overflow.
func mathFloat(vm *VM, value Value) (float64, error) {
	// PyFloat_Check accepts float subclasses without invoking __float__.
	primitive := rawPrim(value)
	if result, ok := primitive.(float64); ok {
		return result, nil
	}
	var class *Class
	switch object := value.(type) {
	case *Instance:
		class = object.Class
	case *Class:
		class = object.Meta
	}
	if class != nil {
		converted, found, err := vm.callNoArgSpecial(value, class, "__float__")
		if err != nil {
			return 0, err
		}
		if found {
			if result, ok := rawPrim(converted).(float64); ok {
				return result, nil
			}
			return 0, newErrorf("TypeError", "__float__ returned non-float (type %s)", typeName(converted))
		}
	}
	if !isIntKind(primitive) && class != nil {
		converted, found, err := vm.callNoArgSpecial(value, class, "__index__")
		if err != nil {
			return 0, err
		}
		if found {
			primitive = rawPrim(converted)
			if !isIntKind(primitive) {
				return 0, newErrorf("TypeError", "__index__ returned non-int (type %s)", typeName(converted))
			}
		}
	}
	if isIntKind(primitive) {
		result, _ := asFloat(primitive)
		if math.IsInf(result, 0) {
			return 0, newError("OverflowError", "int too large to convert to float")
		}
		return result, nil
	}
	return 0, newErrorf("TypeError", "must be real number, not %s", typeName(value))
}

func mathUnary(name string, operation func(float64) float64, rangeError bool) *Builtin {
	return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := noKw(name, kw); err != nil {
			return nil, err
		}
		if err := argCount(name, args, 1, 1); err != nil {
			return nil, err
		}
		x, err := mathFloat(vm, args[0])
		if err != nil {
			return nil, err
		}
		if name == "gamma" && (x == 0 || (x < 0 && x == math.Trunc(x))) {
			return nil, newError("ValueError", "math domain error")
		}
		if name == "log1p" && x <= -1 {
			return nil, newError("ValueError", "math domain error")
		}
		result := operation(x)
		if math.IsNaN(result) && !math.IsNaN(x) {
			return nil, newError("ValueError", "math domain error")
		}
		if rangeError && math.IsInf(result, 0) && !math.IsInf(x, 0) {
			return nil, newError("OverflowError", "math range error")
		}
		return result, nil
	})
}

func mathBinary(name string, operation func(float64, float64) float64) *Builtin {
	return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := noKw(name, kw); err != nil {
			return nil, err
		}
		if err := argCount(name, args, 2, 2); err != nil {
			return nil, err
		}
		x, err := mathFloat(vm, args[0])
		if err != nil {
			return nil, err
		}
		y, err := mathFloat(vm, args[1])
		if err != nil {
			return nil, err
		}
		result := operation(x, y)
		if math.IsNaN(result) && !math.IsNaN(x) && !math.IsNaN(y) {
			return nil, newError("ValueError", "math domain error")
		}
		return result, nil
	})
}

func biMathTrunc(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("trunc", kw); err != nil {
		return nil, err
	}
	if err := argCount("trunc", args, 1, 1); err != nil {
		return nil, err
	}
	var class *Class
	switch object := args[0].(type) {
	case *Instance:
		class = object.Class
	case *Class:
		class = object.Meta
	}
	if class != nil {
		result, found, err := vm.callNoArgSpecial(args[0], class, "__trunc__")
		if found || err != nil {
			return result, err
		}
	}
	value := rawPrim(args[0])
	if isIntKind(value) {
		if n, ok := asInt(value); ok {
			return mkInt(n), nil
		}
		return value, nil
	}
	if x, ok := value.(float64); ok {
		if math.IsNaN(x) {
			return nil, newError("ValueError", "cannot convert float NaN to integer")
		}
		if math.IsInf(x, 0) {
			return nil, newError("OverflowError", "cannot convert float infinity to integer")
		}
		return intFromFloat(math.Trunc(x)), nil
	}
	return nil, newErrorf("TypeError", "type %s doesn't define __trunc__ method", typeName(args[0]))
}

func biMathProd(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("prod", args, 1, 1); err != nil {
		return nil, err
	}
	var result Value = int64(1)
	seen := false
	for _, keyword := range kw {
		if keyword.Name != "start" {
			return nil, newErrorf("TypeError", "'%s' is an invalid keyword argument for prod()", keyword.Name)
		}
		if seen {
			return nil, newError("TypeError", "prod() got multiple values for keyword argument 'start'")
		}
		seen, result = true, keyword.Val
	}
	iterator, err := vm.getIter(args[0])
	if err != nil {
		return nil, err
	}
	for {
		value, found, err := iterator.Next()
		if err != nil {
			return nil, err
		}
		if !found {
			return result, nil
		}
		if err := vm.fuelN(1); err != nil {
			return nil, err
		}
		result, err = vm.binop("mul", result, value)
		if err != nil {
			return nil, err
		}
	}
}

func biMathULP(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := noKw("ulp", kw); err != nil {
		return nil, err
	}
	if err := argCount("ulp", args, 1, 1); err != nil {
		return nil, err
	}
	x, err := mathFloat(vm, args[0])
	if err != nil {
		return nil, err
	}
	if math.IsNaN(x) {
		return x, nil
	}
	x = math.Abs(x)
	if math.IsInf(x, 0) {
		return x, nil
	}
	y := math.Nextafter(x, math.Inf(1))
	if math.IsInf(y, 0) {
		return x - math.Nextafter(x, math.Inf(-1)), nil
	}
	return y - x, nil
}

func biMathNextafter(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("nextafter", args, 2, 2); err != nil {
		return nil, err
	}
	var steps Value = None
	seen := false
	for _, keyword := range kw {
		if keyword.Name != "steps" {
			return nil, newErrorf("TypeError", "'%s' is an invalid keyword argument for nextafter()", keyword.Name)
		}
		if seen {
			return nil, newError("TypeError", "nextafter() got multiple values for keyword argument 'steps'")
		}
		seen, steps = true, keyword.Val
	}
	x, err := mathFloat(vm, args[0])
	if err != nil {
		return nil, err
	}
	y, err := mathFloat(vm, args[1])
	if err != nil {
		return nil, err
	}
	if _, none := steps.(NoneType); none {
		// Go keeps x when both compare equal; CPython/libm returns y,
		// including the sign of zero when stepping between -0.0 and +0.0.
		if x == y {
			return y, nil
		}
		return math.Nextafter(x, y), nil
	}
	index, err := percentIndex(vm, steps)
	if err != nil {
		return nil, err
	}
	var count uint64
	if wide, ok := index.(*big.Int); ok {
		if wide.Sign() < 0 {
			return nil, newError("ValueError", "steps must be a non-negative integer")
		}
		if wide.IsUint64() {
			count = wide.Uint64()
		} else {
			count = ^uint64(0)
		}
	} else {
		n, _ := asInt(index)
		if n < 0 {
			return nil, newError("ValueError", "steps must be a non-negative integer")
		}
		count = uint64(n)
	}
	if count == 0 || math.IsNaN(x) {
		return x, nil
	}
	if math.IsNaN(y) {
		return y, nil
	}
	// CPython's bit-distance algorithm includes signed zero exactly once.
	// It also permits arbitrarily large step counts without iteration.
	const sign = uint64(1) << 63
	ux, uy := math.Float64bits(x), math.Float64bits(y)
	ax, ay := ux&^sign, uy&^sign
	if (ux^uy)&sign != 0 {
		if ax+ay <= count {
			return y, nil
		}
		if ax < count {
			return math.Float64frombits((uy & sign) | (count - ax)), nil
		}
		return math.Float64frombits(ux - count), nil
	}
	if ax > ay {
		if ax-ay >= count {
			return math.Float64frombits(ux - count), nil
		}
	} else if ay-ax >= count {
		return math.Float64frombits(ux + count), nil
	}
	return y, nil
}

func biMathIsclose(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if len(args) > 2 {
		return nil, newErrorf("TypeError", "isclose() takes at most 2 positional arguments (%d given)", len(args))
	}
	values := [4]Value{nil, nil, float64(1e-9), float64(0)}
	seen := [4]bool{}
	for i, value := range args {
		values[i], seen[i] = value, true
	}
	for _, keyword := range kw {
		var i int
		switch keyword.Name {
		case "a":
			i = 0
		case "b":
			i = 1
		case "rel_tol":
			i = 2
		case "abs_tol":
			i = 3
		default:
			return nil, newErrorf("TypeError", "'%s' is an invalid keyword argument for isclose()", keyword.Name)
		}
		if seen[i] {
			return nil, newErrorf("TypeError", "isclose() got multiple values for argument '%s'", keyword.Name)
		}
		values[i], seen[i] = keyword.Val, true
	}
	if !seen[0] || !seen[1] {
		return nil, newError("TypeError", "isclose() missing required argument")
	}
	var xs [4]float64
	for i, value := range values {
		x, err := mathFloat(vm, value)
		if err != nil {
			return nil, err
		}
		xs[i] = x
	}
	a, b, relative, absolute := xs[0], xs[1], xs[2], xs[3]
	if relative < 0 || absolute < 0 {
		return nil, newError("ValueError", "tolerances must be non-negative")
	}
	if a == b {
		return true, nil
	}
	if math.IsInf(a, 0) || math.IsInf(b, 0) {
		return false, nil
	}
	difference := math.Abs(b - a)
	return difference <= math.Abs(relative*b) || difference <= math.Abs(relative*a) || difference <= absolute, nil
}
