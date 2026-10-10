package interp

import (
	"math"
	"math/big"
)

// Ordinary ranges retain inline int64 fields. Wide bounds allocate this payload
// only when necessary; all its integers remain immutable after construction.
type bigRange struct {
	start, stop, step, length *big.Int
}

func newRange(start, stop, step Value) *Range {
	if value, ok := start.(*big.Int); ok {
		start = normBig(value)
	}
	if value, ok := stop.(*big.Int); ok {
		stop = normBig(value)
	}
	if value, ok := step.(*big.Int); ok {
		step = normBig(value)
	}
	a, aSmall := asInt(start)
	b, bSmall := asInt(stop)
	c, cSmall := asInt(step)
	if aSmall && bSmall && cSmall {
		return &Range{Start: a, Stop: b, Step: c}
	}
	x, _ := asBig(start)
	y, _ := asBig(stop)
	z, _ := asBig(step)
	length := new(big.Int)
	if z.Sign() > 0 && x.Cmp(y) < 0 {
		length.Sub(y, x)
	} else if z.Sign() < 0 && x.Cmp(y) > 0 {
		length.Sub(x, y)
	}
	if length.Sign() != 0 {
		var stride big.Int
		stride.Abs(z)
		length.Sub(length, big.NewInt(1))
		length.Quo(length, &stride)
		length.Add(length, big.NewInt(1))
	}
	return &Range{big: &bigRange{start: x, stop: y, step: z, length: length}}
}

// smallLength uses unsigned distances so ranges spanning the entire int64
// domain do not overflow, including a MinInt64 negative step.
func (r *Range) smallLength() uint64 {
	if r.Step > 0 {
		if r.Stop <= r.Start {
			return 0
		}
		return (uint64(r.Stop)-uint64(r.Start)-1)/uint64(r.Step) + 1
	}
	if r.Stop >= r.Start {
		return 0
	}
	return (uint64(r.Start)-uint64(r.Stop)-1)/(0-uint64(r.Step)) + 1
}

func (r *Range) lengthValue() Value {
	if r.big != nil {
		return normBig(r.big.length)
	}
	n := r.smallLength()
	if n <= math.MaxInt64 {
		return mkInt(int64(n))
	}
	return new(big.Int).SetUint64(n)
}

// Len returns a platform-sized length or the same overflow as Python len().
func (r *Range) Len() (int64, error) {
	if r.big != nil {
		if r.big.length.IsInt64() {
			return r.big.length.Int64(), nil
		}
	} else if n := r.smallLength(); n <= math.MaxInt64 {
		return int64(n), nil
	}
	return 0, newError("OverflowError", "Python int too large to convert to C ssize_t")
}

func (r *Range) empty() bool {
	if r.big != nil {
		return r.big.length.Sign() == 0
	}
	return r.smallLength() == 0
}

// Bounds returns the exact start, stop, and step, including arbitrary integers.
func (r *Range) Bounds() (Value, Value, Value) {
	if r.big != nil {
		return normBig(r.big.start), normBig(r.big.stop), normBig(r.big.step)
	}
	return mkInt(r.Start), mkInt(r.Stop), mkInt(r.Step)
}

func rangeEqual(left, right *Range) bool {
	if left.big == nil && right.big == nil {
		n := left.smallLength()
		return n == right.smallLength() && (n == 0 ||
			left.Start == right.Start && (n == 1 || left.Step == right.Step))
	}
	length := left.lengthValue()
	if numCmp(length, right.lengthValue()) != 0 {
		return false
	}
	if numCmp(length, int64(0)) == 0 {
		return true
	}
	start, _, step := left.Bounds()
	otherStart, _, otherStep := right.Bounds()
	return numCmp(start, otherStart) == 0 &&
		(numCmp(length, int64(1)) == 0 || numCmp(step, otherStep) == 0)
}

func rangeHash(r *Range) (int64, error) {
	values := [3]Value{r.lengthValue(), None, None}
	if !r.empty() {
		start, _, step := r.Bounds()
		values[1] = start
		if numCmp(values[0], int64(1)) != 0 {
			values[2] = step
		}
	}
	return compoundHash(values[:])
}

func (r *Range) containsInteger(value Value) bool {
	if r.big == nil {
		if wide, ok := value.(*big.Int); ok {
			if !wide.IsInt64() {
				return false
			}
			value = wide.Int64()
		}
		n, _ := asInt(value)
		if r.Step > 0 {
			return n >= r.Start && n < r.Stop &&
				(uint64(n)-uint64(r.Start))%uint64(r.Step) == 0
		}
		return n <= r.Start && n > r.Stop &&
			(uint64(r.Start)-uint64(n))%(0-uint64(r.Step)) == 0
	}
	n, _ := asBig(value)
	b := r.big
	if b.step.Sign() > 0 {
		if n.Cmp(b.start) < 0 || n.Cmp(b.stop) >= 0 {
			return false
		}
	} else if n.Cmp(b.start) > 0 || n.Cmp(b.stop) <= 0 {
		return false
	}
	var difference big.Int
	difference.Sub(n, b.start)
	return difference.Rem(&difference, b.step).Sign() == 0
}

func (r *Range) integerPosition(value Value) Value {
	if r.big == nil {
		if wide, ok := value.(*big.Int); ok {
			value = wide.Int64()
		}
		n, _ := asInt(value)
		var index uint64
		if r.Step > 0 {
			index = (uint64(n) - uint64(r.Start)) / uint64(r.Step)
		} else {
			index = (uint64(r.Start) - uint64(n)) / (0 - uint64(r.Step))
		}
		if index <= math.MaxInt64 {
			return mkInt(int64(index))
		}
		return new(big.Int).SetUint64(index)
	}
	n, _ := asBig(value)
	position := new(big.Int).Sub(n, r.big.start)
	return normBig(position.Quo(position, r.big.step))
}

func (vm *VM) rangeMissing(value Value) (Value, error) {
	text, err := vm.repr(value)
	if err != nil {
		return nil, err
	}
	return nil, newErrorf("ValueError", "%s is not in range", text)
}

func (vm *VM) rangeSearch(r *Range, value Value, mode string) (Value, error) {
	// Only exact integers get the arithmetic shortcut. Float and user-defined
	// equality must retain ordinary sequence comparison and its side effects.
	if isIntKind(value) {
		found := r.containsInteger(value)
		switch mode {
		case "__contains__":
			return found, nil
		case "count":
			if found {
				return int64(1), nil
			}
			return int64(0), nil
		default:
			if found {
				return r.integerPosition(value), nil
			}
			return vm.rangeMissing(value)
		}
	}
	iterator := vm.rangeIterator(r)
	var index, count int64
	for {
		if err := vm.fuel(); err != nil {
			return nil, err
		}
		item, more, err := iterator.Next()
		if err != nil {
			return nil, err
		}
		if !more {
			break
		}
		equal, err := vm.eqElem(item, value)
		if err != nil {
			return nil, err
		}
		if equal {
			switch mode {
			case "__contains__":
				return true, nil
			case "index":
				return mkInt(index), nil
			default:
				if count == math.MaxInt64 {
					return nil, newError("OverflowError", "count exceeds C integer size")
				}
				count++
			}
		}
		if mode == "index" {
			if index == math.MaxInt64 {
				return nil, newError("OverflowError", "index exceeds C integer size")
			}
			index++
		}
	}
	switch mode {
	case "__contains__":
		return false, nil
	case "count":
		return mkInt(count), nil
	default:
		return vm.rangeMissing(value)
	}
}

func rangeHasIndex(value Value) bool {
	if isIntKind(rawPrim(value)) {
		return true
	}
	if instance, ok := value.(*Instance); ok {
		_, found := instance.Class.Lookup("__index__")
		return found
	}
	return false
}

func (vm *VM) rangeGetIndex(r *Range, key Value) (Value, error) {
	if slice, ok := key.(*Slice); ok {
		return vm.sliceRange(r, slice)
	}
	if !rangeHasIndex(key) {
		return nil, indexTypeError("range", key)
	}
	index, err := percentIndex(vm, key)
	if err != nil {
		return nil, err
	}
	if r.big == nil {
		if wide, ok := index.(*big.Int); ok && wide.IsInt64() {
			index = wide.Int64()
		}
		if n, small := asInt(index); small {
			length := r.smallLength()
			position := uint64(n)
			if n < 0 {
				magnitude := 0 - position
				if magnitude > length {
					return nil, newError("IndexError", "range object index out of range")
				}
				position = length - magnitude
			}
			if position >= length {
				return nil, newError("IndexError", "range object index out of range")
			}
			// Intermediate products may exceed int64, but the selected element
			// lies between the inline bounds; unsigned arithmetic preserves it.
			return mkInt(int64(uint64(r.Start) + position*uint64(r.Step))), nil
		}
	}
	position, _ := asBig(index)
	length, _ := asBig(r.lengthValue())
	if position.Sign() < 0 {
		position = new(big.Int).Add(position, length)
	}
	if position.Sign() < 0 || position.Cmp(length) >= 0 {
		return nil, newError("IndexError", "range object index out of range")
	}
	start, _, step := r.Bounds()
	a, _ := asBig(start)
	b, _ := asBig(step)
	result := new(big.Int).Mul(position, b)
	return normBig(result.Add(result, a)), nil
}

func (vm *VM) rangeSliceParts(slice *Slice) ([3]Value, error) {
	parts := [3]Value{slice.Lower, slice.Upper, slice.Step}
	for _, i := range [...]int{2, 0, 1} {
		if parts[i] == nil {
			parts[i] = None
		}
		if _, absent := parts[i].(NoneType); absent {
			if i == 2 {
				parts[i] = int64(1)
			}
			continue
		}
		if !rangeHasIndex(parts[i]) {
			return parts, newError("TypeError", "slice indices must be integers or None or have an __index__ method")
		}
		value, err := percentIndex(vm, parts[i])
		if err != nil {
			return parts, err
		}
		if wide, ok := value.(*big.Int); ok {
			value = normBig(wide)
		}
		if i == 2 && numCmp(value, int64(0)) == 0 {
			return parts, newError("ValueError", "slice step cannot be zero")
		}
		parts[i] = value
	}
	return parts, nil
}

func rangeSmallSliceBounds(parts [3]Value, length int64) (int64, int64, int64, bool) {
	step, ok := asInt(parts[2])
	if !ok {
		return 0, 0, 0, false
	}
	lower, upper := int64(0), length
	if step < 0 {
		lower, upper = -1, length-1
	}
	var bounds [2]int64
	for i := range bounds {
		if _, absent := parts[i].(NoneType); absent {
			if (i == 0) == (step < 0) {
				bounds[i] = upper
			} else {
				bounds[i] = lower
			}
			continue
		}
		value, ok := asInt(parts[i])
		if !ok {
			return 0, 0, 0, false
		}
		if value < 0 {
			value += length
		}
		if value < lower {
			value = lower
		} else if value > upper {
			value = upper
		}
		bounds[i] = value
	}
	return bounds[0], bounds[1], step, true
}

func rangeLinearSmall(start, step, index int64) (int64, bool) {
	product, overflow := mulOvf(step, index)
	if overflow {
		return 0, false
	}
	result, overflow := addOvf(start, product)
	return result, !overflow
}

func (vm *VM) sliceRange(r *Range, slice *Slice) (Value, error) {
	parts, err := vm.rangeSliceParts(slice)
	if err != nil {
		return nil, err
	}
	if r.big == nil && r.smallLength() <= math.MaxInt64 {
		start, stop, step, small := rangeSmallSliceBounds(parts, int64(r.smallLength()))
		if small {
			a, aOK := rangeLinearSmall(r.Start, r.Step, start)
			b, bOK := rangeLinearSmall(r.Start, r.Step, stop)
			c, overflow := mulOvf(r.Step, step)
			if aOK && bOK && !overflow {
				return &Range{Start: a, Stop: b, Step: c}, nil
			}
		}
	}
	length, _ := asBig(r.lengthValue())
	step, _ := asBig(parts[2])
	lower, upper := new(big.Int), new(big.Int).Set(length)
	if step.Sign() < 0 {
		lower.SetInt64(-1)
		upper.Sub(upper, big.NewInt(1))
	}
	var bounds [2]*big.Int
	for i := range bounds {
		if _, absent := parts[i].(NoneType); absent {
			if (i == 0) == (step.Sign() < 0) {
				bounds[i] = upper
			} else {
				bounds[i] = lower
			}
			continue
		}
		value, _ := asBig(parts[i])
		if value.Sign() < 0 {
			value = new(big.Int).Add(value, length)
		}
		if value.Cmp(lower) < 0 {
			value = lower
		} else if value.Cmp(upper) > 0 {
			value = upper
		}
		bounds[i] = value
	}
	origin, _, increment := r.Bounds()
	base, _ := asBig(origin)
	stride, _ := asBig(increment)
	start := new(big.Int).Mul(stride, bounds[0])
	start.Add(start, base)
	stop := new(big.Int).Mul(stride, bounds[1])
	stop.Add(stop, base)
	newStep := new(big.Int).Mul(stride, step)
	return newRange(normBig(start), normBig(stop), normBig(newStep)), nil
}

func (vm *VM) rangeIterator(r *Range) Iterator {
	if r.big == nil {
		length := r.smallLength()
		if length <= math.MaxInt64 && (length == 0 ||
			r.Step > 0 && r.Stop <= math.MaxInt64-(r.Step-1) ||
			r.Step < 0 && r.Stop >= math.MinInt64+(-1-r.Step)) {
			return &rangeIter{cur: r.Start, step: r.Step, left: int64(length)}
		}
	}
	start, _, step := r.Bounds()
	return &longRangeIter{vm: vm, current: start, step: step, remaining: r.lengthValue()}
}

func (vm *VM) rangeReversed(r *Range) Iterator {
	if r.big == nil && r.Step != math.MinInt64 {
		length := r.smallLength()
		stop, overflow := subOvf(r.Start, r.Step)
		if length <= math.MaxInt64 && !overflow {
			start := int64(uint64(stop) + length*uint64(r.Step))
			return &rangeIter{cur: start, step: -r.Step, left: int64(length)}
		}
	}
	start, _, step := r.Bounds()
	a, _ := asBig(start)
	b, _ := asBig(step)
	length := r.lengthValue()
	n, _ := asBig(length)
	last := new(big.Int).Sub(n, big.NewInt(1))
	last.Mul(last, b)
	last.Add(last, a)
	return &longRangeIter{vm: vm, current: normBig(last), step: normBig(new(big.Int).Neg(b)), remaining: length}
}

type longRangeIter struct {
	vm                       *VM
	current, step, remaining Value
}

func (it *longRangeIter) Next() (Value, bool, error) {
	more, err := it.vm.compare("gt", it.remaining, int64(0))
	if err != nil || !more {
		return nil, false, err
	}
	next, err := it.vm.binop("add", it.current, it.step)
	if err != nil {
		return nil, false, err
	}
	left, err := it.vm.binop("sub", it.remaining, int64(1))
	if err != nil {
		return nil, false, err
	}
	value := it.current
	it.current, it.remaining = next, left
	return value, true, nil
}

func (it *longRangeIter) KindName() string { return "longrange_iterator" }

func (vm *VM) rangeAttr(r *Range, name string) (Value, error) {
	switch name {
	case "start", "stop", "step":
		start, stop, step := r.Bounds()
		switch name {
		case "start":
			return start, nil
		case "stop":
			return stop, nil
		default:
			return step, nil
		}
	case "count", "index", "__contains__", "__getitem__", "__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			if err := argCount(name, args, 1, 1); err != nil {
				return nil, err
			}
			switch name {
			case "__lt__", "__le__", "__gt__", "__ge__":
				return NotImplemented, nil
			case "__getitem__":
				return vm.rangeGetIndex(r, args[0])
			case "__eq__", "__ne__":
				other, ok := args[0].(*Range)
				if !ok {
					return NotImplemented, nil
				}
				equal := rangeEqual(r, other)
				if name == "__ne__" {
					equal = !equal
				}
				return equal, nil
			default:
				return vm.rangeSearch(r, args[0], name)
			}
		}), nil
	case "__iter__", "__reversed__", "__len__", "__bool__", "__hash__", "__repr__", "__str__", "__reduce__", "__reduce_ex__":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			nargs := 0
			if name == "__reduce_ex__" {
				nargs = 1
			}
			if err := argCount(name, args, nargs, nargs); err != nil {
				return nil, err
			}
			switch name {
			case "__iter__":
				return vm.rangeIterator(r), nil
			case "__reversed__":
				return vm.rangeReversed(r), nil
			case "__len__":
				return r.Len()
			case "__bool__":
				return !r.empty(), nil
			case "__hash__":
				return rangeHash(r)
			case "__repr__", "__str__":
				return reprRange(r), nil
			default:
				start, stop, step := r.Bounds()
				return &Tuple{Items: []Value{vm.types["range"], &Tuple{Items: []Value{start, stop, step}}}}, nil
			}
		}), nil
	}
	return nil, newErrorf("AttributeError", "'range' object has no attribute '%s'", name)
}

func (vm *VM) rangeIteratorAttr(value Value, name string) (Value, error) {
	switch name {
	case "__iter__", "__next__", "__length_hint__", "__reduce__", "__reduce_ex__", "__setstate__":
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			nargs := 0
			if name == "__setstate__" || name == "__reduce_ex__" {
				nargs = 1
			}
			if err := argCount(name, args, nargs, nargs); err != nil {
				return nil, err
			}
			if name == "__iter__" {
				return value, nil
			}
			if name == "__next__" {
				item, more, err := value.(Iterator).Next()
				if err != nil {
					return nil, err
				}
				if !more {
					return nil, newError("StopIteration", "")
				}
				return item, nil
			}
			var start, step, left Value
			switch iterator := value.(type) {
			case *rangeIter:
				start, step, left = mkInt(iterator.cur), mkInt(iterator.step), mkInt(iterator.left)
			case *longRangeIter:
				start, step, left = iterator.current, iterator.step, iterator.remaining
			}
			if name == "__length_hint__" {
				return left, nil
			}
			if name == "__setstate__" {
				if iterator, small := value.(*rangeIter); small {
					index, err := percentIndex(vm, args[0])
					if err != nil {
						return nil, err
					}
					if wide, ok := index.(*big.Int); ok {
						index = normBig(wide)
					}
					n, ok := asInt(index)
					if !ok {
						return nil, newError("OverflowError", "Python int too large to convert to C long")
					}
					if n < 0 {
						n = 0
					} else if n > iterator.left {
						n = iterator.left
					}
					iterator.cur += n * iterator.step
					iterator.left -= n
					return None, nil
				}
				index := args[0]
				negative, err := vm.compare("lt", index, int64(0))
				if err != nil {
					return nil, err
				}
				if negative {
					index = int64(0)
				} else {
					past, err := vm.compare("gt", index, left)
					if err != nil {
						return nil, err
					}
					if past {
						index = left
					}
				}
				product, err := vm.binop("mul", index, step)
				if err != nil {
					return nil, err
				}
				next, err := vm.binop("add", start, product)
				if err != nil {
					return nil, err
				}
				remaining, err := vm.binop("sub", left, index)
				if err != nil {
					return nil, err
				}
				iterator := value.(*longRangeIter)
				iterator.current, iterator.remaining = next, remaining
				return None, nil
			}
			product, err := vm.binop("mul", left, step)
			if err != nil {
				return nil, err
			}
			stop, err := vm.binop("add", start, product)
			if err != nil {
				return nil, err
			}
			rangeValue, err := ctorRange(vm, []Value{start, stop, step}, nil)
			if err != nil {
				return nil, err
			}
			return &Tuple{Items: []Value{vm.builtins["iter"], &Tuple{Items: []Value{rangeValue}}, None}}, nil
		}), nil
	}
	return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'", typeName(value), name)
}
