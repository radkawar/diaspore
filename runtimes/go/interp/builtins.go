package interp

import (
	"fmt"
	"hash/fnv"
	"io"
	"math"
	"math/big"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"unicode"
)

type missingT struct{}

var missingV = &missingT{}

func bi(name string, fn func(vm *VM, args []Value, kw []KwArg) (Value, error)) *Builtin {
	return &Builtin{Name: name, Fn: fn}
}

func (vm *VM) makeBuiltins() map[string]Value {
	types := map[string]*BuiltinType{
		"int":      {Name: "int", ctor: ctorInt},
		"float":    {Name: "float", ctor: ctorFloat},
		"bool":     {Name: "bool", ctor: ctorBool},
		"str":      {Name: "str", ctor: ctorStr},
		"bytes":    {Name: "bytes", ctor: ctorBytes},
		"list":     {Name: "list", ctor: ctorList},
		"tuple":    {Name: "tuple", ctor: ctorTuple},
		"dict":     {Name: "dict", ctor: ctorDict},
		"set":      {Name: "set", ctor: ctorSet},
		"range":    {Name: "range", ctor: ctorRange},
		"complex":  {Name: "complex", ctor: ctorComplex},
		"type":     {Name: "type", ctor: ctorType},
		"NoneType": {Name: "NoneType", ctor: func(vm *VM, args []Value, kw []KwArg) (Value, error) { return None, nil }},
	}
	vm.types = types

	b := map[string]Value{
		"print":        bi("print", biPrint),
		"len":          bi("len", biLen),
		"enumerate":    bi("enumerate", biEnumerate),
		"zip":          bi("zip", biZip),
		"map":          bi("map", biMap),
		"filter":       bi("filter", biFilter),
		"reversed":     bi("reversed", biReversed),
		"sorted":       bi("sorted", biSorted),
		"sum":          bi("sum", biSum),
		"min":          bi("min", biMin),
		"max":          bi("max", biMax),
		"any":          bi("any", biAny),
		"all":          bi("all", biAll),
		"abs":          bi("abs", biAbs),
		"round":        bi("round", biRound),
		"divmod":       bi("divmod", biDivmod),
		"pow":          bi("pow", biPow),
		"ord":          bi("ord", biOrd),
		"chr":          bi("chr", biChr),
		"hex":          bi("hex", biHex),
		"bin":          bi("bin", biBin),
		"oct":          bi("oct", biOct),
		"repr":         bi("repr", biRepr),
		"ascii":        bi("ascii", biAscii),
		"isinstance":   bi("isinstance", biIsinstance),
		"issubclass":   bi("issubclass", biIssubclass),
		"callable":     bi("callable", biCallable),
		"hash":         bi("hash", biHash),
		"id":           bi("id", biID),
		"iter":         bi("iter", biIter),
		"next":         bi("next", biNext),
		"getattr":      bi("getattr", biGetattr),
		"setattr":      bi("setattr", biSetattr),
		"hasattr":      bi("hasattr", biHasattr),
		"input":        bi("input", biInput),
		"open":         bi("open", biOpen),
		"exit":         bi("exit", biExit),
		"staticmethod": bi("staticmethod", biStaticmethod),
		"classmethod":  bi("classmethod", biClassmethod),
		"format":       bi("format", biFormat),
		".await":       bi(".await", biAwait),
		"compile":      bi("compile", biCompile),
		"exec":         bi("exec", biExec),
		"eval":         bi("eval", biEval),
		"property":     bi("property", biProperty),
		"slice":        bi("slice", biSlice),
		"vars":         bi("vars", biVars),
		"dir":          bi("dir", biDir),
		"super":        superMarker,
		"object":       objectClass,
		"Ellipsis":     Ellipsis,
	}
	for name, t := range types {
		if name != "NoneType" {
			b[name] = t
		}
	}
	for name, cls := range excClasses {
		b[name] = cls
	}
	return b
}

func argCount(name string, args []Value, min, max int) error {
	if len(args) < min || (max >= 0 && len(args) > max) {
		if min == max {
			return newErrorf("TypeError", "%s() takes exactly %d argument%s (%d given)",
				name, min, plural(min, ""), len(args))
		}
		return newErrorf("TypeError", "%s expected at most %d arguments, got %d",
			name, max, len(args))
	}
	return nil
}

func biPrint(vm *VM, args []Value, kw []KwArg) (Value, error) {
	sep, end := " ", "\n"
	for _, k := range kw {
		switch k.Name {
		case "sep":
			if _, isNone := k.Val.(NoneType); !isNone {
				s, ok := k.Val.(string)
				if !ok {
					return nil, newErrorf("TypeError",
						"sep must be None or a string, not %s", typeName(k.Val))
				}
				sep = s
			}
		case "end":
			if _, isNone := k.Val.(NoneType); !isNone {
				s, ok := k.Val.(string)
				if !ok {
					return nil, newErrorf("TypeError",
						"end must be None or a string, not %s", typeName(k.Val))
				}
				end = s
			}
		default:
			return nil, newErrorf("TypeError",
				"'%s' is an invalid keyword argument for print()", k.Name)
		}
	}
	if vm.replaying {
		// Replay must still evaluate each argument (str() may raise); the
		// output already happened in the recorded segment.
		for _, a := range args {
			if _, err := vm.str(a); err != nil {
				return nil, err
			}
		}
		return None, nil
	}
	// CPython streams arguments: if str() of a later argument raises, the
	// earlier arguments (and separator) are already on stdout.
	var err error
	for i, a := range args {
		if i > 0 {
			io.WriteString(vm.stdout, sep)
		}
		var s string
		if s, err = vm.str(a); err != nil {
			break
		}
		io.WriteString(vm.stdout, s)
	}
	if err == nil {
		io.WriteString(vm.stdout, end)
	}
	vm.flushOut()
	if err != nil {
		return nil, err
	}
	return None, nil
}

func biLen(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("len", args, 1, 1); err != nil {
		return nil, err
	}
	return vm.diasporeLen(args[0])
}

func biEnumerate(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("enumerate", args, 1, 2); err != nil {
		return nil, err
	}
	start := int64(0)
	if len(args) == 2 {
		n, ok := asInt(args[1])
		if !ok {
			return nil, newErrorf("TypeError", "'%s' object cannot be interpreted as an integer",
				typeName(args[1]))
		}
		start = n
	}
	if v, ok := kwGet(kw, "start"); ok {
		n, ok := asInt(v)
		if !ok {
			return nil, newErrorf("TypeError", "'%s' object cannot be interpreted as an integer",
				typeName(v))
		}
		start = n
	}
	it, err := vm.getIter(args[0])
	if err != nil {
		return nil, err
	}
	return &enumIter{it: it, n: start}, nil
}

func biZip(vm *VM, args []Value, kw []KwArg) (Value, error) {
	its := make([]Iterator, len(args))
	for i, a := range args {
		it, err := vm.getIter(a)
		if err != nil {
			return nil, err
		}
		its[i] = it
	}
	return &zipIter{its: its}, nil
}

func biMap(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if len(args) < 2 {
		return nil, newErrorf("TypeError", "map() must have at least two arguments.")
	}
	its := make([]Iterator, len(args)-1)
	for i, a := range args[1:] {
		it, err := vm.getIter(a)
		if err != nil {
			return nil, err
		}
		its[i] = it
	}
	return &mapIter{vm: vm, fn: args[0], its: its}, nil
}

func biFilter(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("filter", args, 2, 2); err != nil {
		return nil, err
	}
	it, err := vm.getIter(args[1])
	if err != nil {
		return nil, err
	}
	var fn Value
	if _, isNone := args[0].(NoneType); !isNone {
		fn = args[0]
	}
	return &filterIter{vm: vm, fn: fn, it: it}, nil
}

func biReversed(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("reversed", args, 1, 1); err != nil {
		return nil, err
	}
	var items []Value
	switch t := args[0].(type) {
	case *List:
		items = append([]Value{}, t.Items...)
	case *Tuple:
		items = append([]Value{}, t.Items...)
	case string:
		for _, r := range t {
			items = append(items, string(r))
		}
	case []byte:
		for _, c := range t {
			items = append(items, int64(c))
		}
	case *Range:
		for i := int64(0); i < t.Len(); i++ {
			items = append(items, t.Start+i*t.Step)
		}
	default:
		return nil, newErrorf("TypeError", "'%s' object is not reversible", typeName(args[0]))
	}
	reverseSlice(items)
	return &seqIter{items: items, kind: "reversed"}, nil
}

// chargeSized charges fuel for the length of a sized container before a bulk
// host-speed operation consumes it (mirrors diaspore-py's _charge_sized).
func (vm *VM) chargeSized(v Value) error {
	var n int64 = -1
	switch t := v.(type) {
	case string:
		n = int64(len(t))
	case []byte:
		n = int64(len(t))
	case *List:
		n = int64(len(t.Items))
	case *Tuple:
		n = int64(len(t.Items))
	case *Dict:
		n = int64(t.Len())
	case *Set:
		n = int64(t.Len())
	case *Range:
		n = t.Len()
	}
	if n > 0 {
		return vm.fuelN(n)
	}
	return nil
}

func biSorted(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("sorted", args, 1, 1); err != nil {
		return nil, err
	}
	if err := vm.chargeSized(args[0]); err != nil {
		return nil, err
	}
	items, err := vm.iterToSlice(args[0])
	if err != nil {
		return nil, err
	}
	if err := vm.fuelN(int64(len(items))); err != nil {
		return nil, err
	}
	key, reverse, err := keyReverseKw("sorted", vm, kw)
	if err != nil {
		return nil, err
	}
	if err := vm.sortValues(items, key, reverse); err != nil {
		return nil, err
	}
	return &List{Items: items}, nil
}

func biSum(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("sum", args, 1, 2); err != nil {
		return nil, err
	}
	var total Value = int64(0)
	if len(args) == 2 {
		total = args[1]
	}
	if v, ok := kwGet(kw, "start"); ok {
		total = v
	}
	it, err := vm.getIter(args[0])
	if err != nil {
		return nil, err
	}
	for {
		x, ok, err := it.Next()
		if err != nil {
			return nil, err
		}
		if !ok {
			return total, nil
		}
		total, err = vm.binop("add", total, x)
		if err != nil {
			return nil, err
		}
	}
}

func minMax(vm *VM, name string, args []Value, kw []KwArg, isMin bool) (Value, error) {
	if len(args) == 0 {
		return nil, newErrorf("TypeError", "%s expected at least 1 argument, got 0", name)
	}
	var key Value
	var deflt Value = missingV
	for _, k := range kw {
		switch k.Name {
		case "key":
			if _, isNone := k.Val.(NoneType); !isNone {
				key = k.Val
			}
		case "default":
			deflt = k.Val
		default:
			return nil, newErrorf("TypeError",
				"'%s' is an invalid keyword argument for %s()", k.Name, name)
		}
	}
	var items []Value
	if len(args) == 1 {
		if err := vm.chargeSized(args[0]); err != nil {
			return nil, err
		}
		var err error
		items, err = vm.iterToSlice(args[0])
		if err != nil {
			return nil, err
		}
		if err := vm.fuelN(int64(len(items))); err != nil {
			return nil, err
		}
		if len(items) == 0 {
			if deflt != missingV {
				return deflt, nil
			}
			return nil, newErrorf("ValueError", "%s() iterable argument is empty", name)
		}
	} else {
		items = args
	}
	best := items[0]
	bestK := best
	if key != nil {
		k, err := vm.call(key, []Value{best}, nil)
		if err != nil {
			return nil, err
		}
		bestK = k
	}
	for _, x := range items[1:] {
		xk := x
		if key != nil {
			k, err := vm.call(key, []Value{x}, nil)
			if err != nil {
				return nil, err
			}
			xk = k
		}
		var pick bool
		var err error
		if isMin {
			pick, err = vm.compare("lt", xk, bestK)
		} else {
			pick, err = vm.compare("lt", bestK, xk)
		}
		if err != nil {
			return nil, err
		}
		if pick {
			best, bestK = x, xk
		}
	}
	return best, nil
}

func biMin(vm *VM, args []Value, kw []KwArg) (Value, error) {
	return minMax(vm, "min", args, kw, true)
}

func biMax(vm *VM, args []Value, kw []KwArg) (Value, error) {
	return minMax(vm, "max", args, kw, false)
}

func biAny(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("any", args, 1, 1); err != nil {
		return nil, err
	}
	it, err := vm.getIter(args[0])
	if err != nil {
		return nil, err
	}
	for {
		x, ok, err := it.Next()
		if err != nil {
			return nil, err
		}
		if !ok {
			return false, nil
		}
		b, err := vm.truth(x)
		if err != nil {
			return nil, err
		}
		if b {
			return true, nil
		}
	}
}

func biAll(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("all", args, 1, 1); err != nil {
		return nil, err
	}
	it, err := vm.getIter(args[0])
	if err != nil {
		return nil, err
	}
	for {
		x, ok, err := it.Next()
		if err != nil {
			return nil, err
		}
		if !ok {
			return true, nil
		}
		b, err := vm.truth(x)
		if err != nil {
			return nil, err
		}
		if !b {
			return false, nil
		}
	}
}

func biAbs(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("abs", args, 1, 1); err != nil {
		return nil, err
	}
	if n, ok := asInt(args[0]); ok {
		if n == math.MinInt64 {
			return new(big.Int).Neg(big.NewInt(n)), nil
		}
		if n < 0 {
			return mkInt(-n), nil
		}
		return mkInt(n), nil
	}
	if f, ok := args[0].(float64); ok {
		return math.Abs(f), nil
	}
	if z, ok := args[0].(*big.Int); ok {
		return new(big.Int).Abs(z), nil
	}
	if c, ok := args[0].(complex128); ok {
		return math.Hypot(real(c), imag(c)), nil
	}
	return nil, newErrorf("TypeError", "bad operand type for abs(): '%s'", typeName(args[0]))
}

func biRound(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("round", args, 1, 2); err != nil {
		return nil, err
	}
	hasND := false
	var nd int64
	if len(args) == 2 {
		if _, isNone := args[1].(NoneType); !isNone {
			n, ok := asInt(args[1])
			if !ok {
				return nil, newErrorf("TypeError",
					"'%s' object cannot be interpreted as an integer", typeName(args[1]))
			}
			nd = n
			hasND = true
		}
	}
	if n, ok := asInt(args[0]); ok {
		return n, nil
	}
	if z, ok := args[0].(*big.Int); ok {
		return z, nil
	}
	f, ok := args[0].(float64)
	if !ok {
		return nil, newErrorf("TypeError",
			"type %s doesn't define __round__ method", typeName(args[0]))
	}
	if !hasND {
		if math.IsNaN(f) {
			return nil, newError("ValueError", "cannot convert float NaN to integer")
		}
		if math.IsInf(f, 0) {
			return nil, newError("OverflowError", "cannot convert float infinity to integer")
		}
		return int64(math.RoundToEven(f)), nil
	}
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return f, nil
	}
	if nd >= 0 {
		if nd > 323 {
			return f, nil
		}
		s := strconv.FormatFloat(f, 'f', int(nd), 64)
		r, _ := strconv.ParseFloat(s, 64)
		return r, nil
	}
	scale := math.Pow(10, float64(-nd))
	return math.RoundToEven(f/scale) * scale, nil
}

func biDivmod(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("divmod", args, 2, 2); err != nil {
		return nil, err
	}
	q, err := vm.binop("floordiv", args[0], args[1])
	if err != nil {
		return nil, err
	}
	r, err := vm.binop("mod", args[0], args[1])
	if err != nil {
		return nil, err
	}
	return &Tuple{Items: []Value{q, r}}, nil
}

func biPow(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("pow", args, 2, 3); err != nil {
		return nil, err
	}
	if len(args) == 3 {
		a, aok := asBig(args[0])
		b, bok := asBig(args[1])
		m, mok := asBig(args[2])
		if !aok || !bok || !mok {
			return nil, newError("TypeError",
				"pow() 3rd argument not allowed unless all arguments are integers")
		}
		if m.Sign() == 0 {
			return nil, newError("ValueError", "pow() 3rd argument cannot be 0")
		}
		if b.Sign() < 0 {
			return nil, newError("ValueError",
				"base is not invertible for the given modulus")
		}
		return normBig(new(big.Int).Exp(a, b, m)), nil
	}
	return vm.binop("pow", args[0], args[1])
}

func biOrd(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("ord", args, 1, 1); err != nil {
		return nil, err
	}
	switch t := args[0].(type) {
	case string:
		rs := []rune(t)
		if len(rs) != 1 {
			return nil, newErrorf("TypeError",
				"ord() expected a character, but string of length %d found", len(rs))
		}
		return int64(rs[0]), nil
	case []byte:
		if len(t) != 1 {
			return nil, newErrorf("TypeError",
				"ord() expected a character, but string of length %d found", len(t))
		}
		return int64(t[0]), nil
	}
	return nil, newErrorf("TypeError",
		"ord() expected string of length 1, but %s found", typeName(args[0]))
}

func biChr(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("chr", args, 1, 1); err != nil {
		return nil, err
	}
	n, ok := asInt(args[0])
	if !ok {
		return nil, newErrorf("TypeError",
			"'%s' object cannot be interpreted as an integer", typeName(args[0]))
	}
	if n < 0 || n >= 0x110000 {
		return nil, newError("ValueError", "chr() arg not in range(0x110000)")
	}
	return string(rune(n)), nil
}

func intToBase(n int64, prefix string, base int) string {
	sign := ""
	mag := uint64(n)
	if n < 0 {
		sign = "-"
		mag = uint64(-(n + 1)) + 1
	}
	return sign + prefix + strconv.FormatUint(mag, base)
}

func baseConv(name, prefix string, base int) func(*VM, []Value, []KwArg) (Value, error) {
	return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := argCount(name, args, 1, 1); err != nil {
			return nil, err
		}
		if n, ok := asInt(args[0]); ok {
			return intToBase(n, prefix, base), nil
		}
		if z, ok := args[0].(*big.Int); ok {
			sign := ""
			mag := z
			if z.Sign() < 0 {
				sign = "-"
				mag = new(big.Int).Neg(z)
			}
			return sign + prefix + mag.Text(base), nil
		}
		return nil, newErrorf("TypeError",
			"'%s' object cannot be interpreted as an integer", typeName(args[0]))
	}
}

func biHex(vm *VM, args []Value, kw []KwArg) (Value, error) {
	return baseConv("hex", "0x", 16)(vm, args, kw)
}

func biBin(vm *VM, args []Value, kw []KwArg) (Value, error) {
	return baseConv("bin", "0b", 2)(vm, args, kw)
}

func biOct(vm *VM, args []Value, kw []KwArg) (Value, error) {
	return baseConv("oct", "0o", 8)(vm, args, kw)
}

func biRepr(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("repr", args, 1, 1); err != nil {
		return nil, err
	}
	return vm.repr(args[0])
}

func biAscii(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("ascii", args, 1, 1); err != nil {
		return nil, err
	}
	return vm.ascii(args[0])
}

func valueMatchesType(v Value, bt *BuiltinType) bool {
	switch bt.Name {
	case "int":
		switch v.(type) {
		case int64, bool, *big.Int:
			return true
		}
		return false
	case "bool":
		_, ok := v.(bool)
		return ok
	case "float":
		_, ok := v.(float64)
		return ok
	case "str":
		_, ok := v.(string)
		return ok
	case "bytes":
		_, ok := v.([]byte)
		return ok
	case "list":
		_, ok := v.(*List)
		return ok
	case "tuple":
		_, ok := v.(*Tuple)
		return ok
	case "dict":
		_, ok := v.(*Dict)
		return ok
	case "set":
		_, ok := v.(*Set)
		return ok
	case "range":
		_, ok := v.(*Range)
		return ok
	case "complex":
		_, ok := v.(complex128)
		return ok
	case "NoneType":
		_, ok := v.(NoneType)
		return ok
	case "type":
		switch v.(type) {
		case *Class, *BuiltinType:
			return true
		}
	}
	return false
}

func biIsinstance(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("isinstance", args, 2, 2); err != nil {
		return nil, err
	}
	var classes []Value
	if tup, ok := args[1].(*Tuple); ok {
		classes = tup.Items
	} else {
		classes = []Value{args[1]}
	}
	for _, c := range classes {
		switch cls := c.(type) {
		case *Class:
			if cls == objectClass {
				return true, nil
			}
			if inst, ok := args[0].(*Instance); ok && inst.Class.IsSub(cls) {
				return true, nil
			}
		case *BuiltinType:
			if inst, isInst := args[0].(*Instance); isInst {
				if inst.Class.Prim != nil {
					if inst.Class.Prim.Name == cls.Name ||
						(cls.Name == "int" && inst.Class.Prim.Name == "bool") {
						return true, nil
					}
					if valueMatchesType(inst.Prim, cls) {
						return true, nil
					}
				}
			} else if valueMatchesType(args[0], cls) {
				return true, nil
			}
		default:
			return nil, newError("TypeError",
				"isinstance() arg 2 must be a type or tuple of types")
		}
	}
	return false, nil
}

func biIssubclass(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("issubclass", args, 2, 2); err != nil {
		return nil, err
	}
	var classes []Value
	if tup, ok := args[1].(*Tuple); ok {
		classes = tup.Items
	} else {
		classes = []Value{args[1]}
	}
	for _, c := range classes {
		if cls, ok := c.(*Class); ok && cls == objectClass {
			switch args[0].(type) {
			case *Class, *BuiltinType:
				return true, nil
			}
		}
	}
	switch a := args[0].(type) {
	case *Class:
		for _, c := range classes {
			if cls, ok := c.(*Class); ok && a.IsSub(cls) {
				return true, nil
			}
		}
	case *BuiltinType:
		for _, c := range classes {
			if bt, ok := c.(*BuiltinType); ok {
				if a.Name == bt.Name || (a.Name == "bool" && bt.Name == "int") {
					return true, nil
				}
			}
		}
	default:
		return nil, newError("TypeError", "issubclass() arg 1 must be a class")
	}
	return false, nil
}

func biCallable(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("callable", args, 1, 1); err != nil {
		return nil, err
	}
	switch t := args[0].(type) {
	case *Function, *BoundMethod, *Class, *BuiltinType, *Builtin:
		return true, nil
	case *Instance:
		_, ok := t.Class.Lookup("__call__")
		return ok, nil
	}
	return false, nil
}

func biHash(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("hash", args, 1, 1); err != nil {
		return nil, err
	}
	if inst, ok := args[0].(*Instance); ok {
		return int64(reflect.ValueOf(inst).Pointer()) &^ (1 << 62), nil
	}
	if c, ok := args[0].(complex128); ok && imag(c) == 0 {
		return biHash(vm, []Value{real(c)}, nil)
	}
	if f, ok := args[0].(float64); ok && !math.IsInf(f, 0) && !math.IsNaN(f) &&
		f == math.Trunc(f) && f >= float64(math.MinInt64) && f <= float64(math.MaxInt64) {
		return int64(f), nil
	}
	if n, ok := asInt(args[0]); ok {
		return n, nil
	}
	hk, err := hashKey(args[0])
	if err != nil {
		return nil, err
	}
	h := fnv.New64a()
	h.Write([]byte(hk))
	return int64(h.Sum64() &^ (1 << 63)), nil
}

func biID(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("id", args, 1, 1); err != nil {
		return nil, err
	}
	rv := reflect.ValueOf(args[0])
	switch rv.Kind() {
	case reflect.Ptr, reflect.Slice, reflect.Map, reflect.Func:
		return int64(rv.Pointer()), nil
	}
	hk, _ := hashKey(args[0])
	h := fnv.New64a()
	h.Write([]byte(hk))
	return int64(h.Sum64() &^ (1 << 63)), nil
}

func biIter(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("iter", args, 1, 1); err != nil {
		return nil, err
	}
	it, err := vm.getIter(args[0])
	if err != nil {
		return nil, err
	}
	return it, nil
}

func biNext(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("next", args, 1, 2); err != nil {
		return nil, err
	}
	var it Iterator
	switch t := args[0].(type) {
	case *Generator:
		it = t
	case Iterator:
		it = t
	case *Instance:
		if _, ok := t.Class.Lookup("__next__"); ok {
			it = &instIter{vm: vm, obj: t, retval: None}
		}
	}
	if it == nil {
		return nil, newErrorf("TypeError", "'%s' object is not an iterator", typeName(args[0]))
	}
	v, ok, err := it.Next()
	if err != nil {
		return nil, err
	}
	if ok {
		return v, nil
	}
	if len(args) == 2 {
		return args[1], nil
	}
	if rv, ok := it.(retValIterator); ok {
		return nil, stopWithValue(rv.RetVal())
	}
	return nil, newErrorArgs("StopIteration")
}

func biGetattr(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("getattr", args, 2, 3); err != nil {
		return nil, err
	}
	name, err := wantStr("attribute name", args[1])
	if err != nil {
		return nil, err
	}
	v, err := vm.getAttr(args[0], name)
	if err != nil {
		if len(args) == 3 {
			if _, ok := raisedAs(err, "AttributeError"); ok {
				return args[2], nil
			}
		}
		return nil, err
	}
	return v, nil
}

func biSetattr(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("setattr", args, 3, 3); err != nil {
		return nil, err
	}
	name, err := wantStr("attribute name", args[1])
	if err != nil {
		return nil, err
	}
	if err := vm.setAttr(args[0], name, args[2]); err != nil {
		return nil, err
	}
	return None, nil
}

func biHasattr(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("hasattr", args, 2, 2); err != nil {
		return nil, err
	}
	name, err := wantStr("attribute name", args[1])
	if err != nil {
		return nil, err
	}
	if _, err := vm.getAttr(args[0], name); err != nil {
		if _, ok := raisedAs(err, "AttributeError"); ok {
			return false, nil
		}
		return nil, err
	}
	return true, nil
}

func biInput(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("input", args, 0, 1); err != nil {
		return nil, err
	}
	if vm.durable {
		replayed, r, rerr := vm.replayNext("builtins", "input")
		if rerr != nil {
			return nil, rerr
		}
		if replayed {
			return r, nil
		}
	}
	if len(args) == 1 && !vm.replaying {
		s, err := vm.str(args[0])
		if err != nil {
			return nil, err
		}
		fmt.Fprint(vm.stdout, s)
		vm.flushOut()
	}
	line, err := vm.stdin.ReadString('\n')
	if err != nil && line == "" {
		return nil, newError("RuntimeError", "EOF when reading a line")
	}
	line = strings.TrimSuffix(line, "\n")
	if vm.durable {
		vm.journal = append(vm.journal, jEntry{"builtins", "input", line})
	}
	return line, nil
}

func biExit(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("exit", args, 0, 1); err != nil {
		return nil, err
	}
	var code Value = int64(0)
	if len(args) == 1 {
		code = args[0]
	}
	return nil, newErrorArgs("SystemExit", code)
}

func biFormat(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("format", args, 1, 2); err != nil {
		return nil, err
	}
	spec := ""
	if len(args) == 2 {
		s, ok := args[1].(string)
		if !ok {
			return nil, newErrorf("TypeError",
				"format() argument 2 must be str, not %s", typeName(args[1]))
		}
		spec = s
	}
	return vm.formatValue(args[0], spec)
}

// biAwait is the compiler-emitted `.await` helper behind `await x`: it
// resolves an awaitable to the coroutine/generator that drives it.
func biAwait(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount(".await", args, 1, 1); err != nil {
		return nil, err
	}
	switch t := args[0].(type) {
	case *Generator:
		return t, nil
	case *Instance:
		if _, ok := t.Class.Lookup("__await__"); ok {
			r, err := vm.callDunder(t, "__await__", nil)
			if err != nil {
				return nil, err
			}
			if g, ok := r.(*Generator); ok {
				return g, nil
			}
			return nil, newError("TypeError",
				"__await__() must return a coroutine or generator")
		}
	}
	return nil, newErrorf("TypeError",
		"object %s can't be used in 'await' expression", typeName(args[0]))
}

func biCompile(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("compile", args, 1, 6); err != nil {
		return nil, err
	}
	filename := "<string>"
	mode := "exec"
	if len(args) >= 2 {
		if s, ok := args[1].(string); ok {
			filename = s
		}
	}
	if len(args) >= 3 {
		m, ok := args[2].(string)
		if !ok || (m != "exec" && m != "eval" && m != "single") {
			return nil, newError("ValueError", "compile() mode must be 'exec', 'eval' or 'single'")
		}
		mode = m
	}
	return vm.compileSourceObject(args[0], filename, mode)
}

// biExec/biEval are the value forms (e.g. passed to map); direct calls are
// compiled with the calling frame so their default namespace is the caller.
func biExec(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("exec", args, 1, 3); err != nil {
		return nil, err
	}
	var glob, loc Value
	if len(args) >= 2 {
		glob = args[1]
	}
	if len(args) >= 3 {
		loc = args[2]
	}
	return vm.execEval(vm.mainFrame(), args[0], glob, loc, "exec")
}

func biEval(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("eval", args, 1, 3); err != nil {
		return nil, err
	}
	var glob, loc Value
	if len(args) >= 2 {
		glob = args[1]
	}
	if len(args) >= 3 {
		loc = args[2]
	}
	return vm.execEval(vm.mainFrame(), args[0], glob, loc, "eval")
}

func biProperty(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("property", args, 0, 4); err != nil {
		return nil, err
	}
	p := &Property{}
	get := func(i int) Value {
		if i < len(args) {
			if _, isNone := args[i].(NoneType); !isNone {
				return args[i]
			}
		}
		return nil
	}
	p.Fget, p.Fset, p.Fdel = get(0), get(1), get(2)
	p.Doc = get(3)
	for _, k := range kw {
		switch k.Name {
		case "fget":
			p.Fget = k.Val
		case "fset":
			p.Fset = k.Val
		case "fdel":
			p.Fdel = k.Val
		case "doc":
			p.Doc = k.Val
		}
	}
	return p, nil
}

func biSlice(vm *VM, args []Value, kw []KwArg) (Value, error) {
	switch len(args) {
	case 1:
		return &Slice{Lower: None, Upper: args[0], Step: None}, nil
	case 2:
		return &Slice{Lower: args[0], Upper: args[1], Step: None}, nil
	case 3:
		return &Slice{Lower: args[0], Upper: args[1], Step: args[2]}, nil
	}
	return nil, newError("TypeError", "slice expected 1-3 arguments")
}

func biVars(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("vars", args, 0, 1); err != nil {
		return nil, err
	}
	if len(args) == 0 {
		return nil, newError("TypeError", "vars() with no argument unsupported here")
	}
	d := NewDict()
	switch t := args[0].(type) {
	case *Instance:
		for k, v := range t.D {
			d.SetStr(k, v)
		}
		return d, nil
	case *Module:
		for k, c := range t.frame.cells {
			if c.v != nil {
				d.SetStr(k, c.v)
			}
		}
		return d, nil
	case *Class:
		for k, v := range t.NS {
			d.SetStr(k, v)
		}
		return d, nil
	}
	return nil, newError("TypeError", "vars() argument must have __dict__")
}

func biDir(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("dir", args, 0, 1); err != nil {
		return nil, err
	}
	if len(args) == 0 {
		return &List{}, nil
	}
	names := map[string]bool{}
	switch t := args[0].(type) {
	case *Instance:
		for k := range t.D {
			names[k] = true
		}
		for _, c := range t.Class.MRO {
			for k := range c.NS {
				names[k] = true
			}
		}
	case *Module:
		for k, c := range t.frame.cells {
			if c.v != nil {
				names[k] = true
			}
		}
	case *Class:
		for _, c := range t.MRO {
			for k := range c.NS {
				names[k] = true
			}
		}
	default:
		return &List{}, nil
	}
	out := make([]string, 0, len(names))
	for k := range names {
		out = append(out, k)
	}
	sort.Strings(out)
	items := make([]Value, len(out))
	for i, k := range out {
		items[i] = k
	}
	return &List{Items: items}, nil
}

func biStaticmethod(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("staticmethod", args, 1, 1); err != nil {
		return nil, err
	}
	return &StaticMethod{Fn: args[0]}, nil
}

func biClassmethod(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("classmethod", args, 1, 1); err != nil {
		return nil, err
	}
	return &ClassMethod{Fn: args[0]}, nil
}

func ctorInt(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("int", args, 0, 2); err != nil {
		return nil, err
	}
	if len(args) == 0 {
		return int64(0), nil
	}
	if inst, ok := args[0].(*Instance); ok {
		for _, d := range []string{"__int__", "__index__", "__trunc__"} {
			if _, has := inst.Class.Lookup(d); has {
				return vm.callDunder(inst, d, nil)
			}
		}
		if inst.Prim != nil {
			args = append([]Value{inst.Prim}, args[1:]...)
		} else {
			return nil, newErrorf("TypeError",
				"int() argument must be a string or a real number, not '%s'", inst.Class.Name)
		}
	}
	if len(args) == 2 {
		base, ok := asInt(args[1])
		if !ok {
			return nil, newErrorf("TypeError",
				"'%s' object cannot be interpreted as an integer", typeName(args[1]))
		}
		s, ok := args[0].(string)
		if !ok {
			return nil, newError("TypeError",
				"int() can't convert non-string with explicit base")
		}
		return parsePyInt(s, int(base))
	}
	switch t := args[0].(type) {
	case int64:
		return t, nil
	case *big.Int:
		return t, nil
	case bool:
		if t {
			return int64(1), nil
		}
		return int64(0), nil
	case float64:
		if math.IsNaN(t) {
			return nil, newError("ValueError", "cannot convert float NaN to integer")
		}
		if math.IsInf(t, 0) {
			return nil, newError("OverflowError", "cannot convert float infinity to integer")
		}
		return intFromFloat(t), nil
	case string:
		return parsePyInt(t, 10)
	}
	return nil, newErrorf("TypeError",
		"int() argument must be a string or a real number, not '%s'", typeName(args[0]))
}

func parsePyInt(orig string, base int) (Value, error) {
	fail := func() error {
		return newErrorf("ValueError", "invalid literal for int() with base %d: %s",
			base, reprString(orig))
	}
	s := strings.TrimFunc(orig, unicode.IsSpace)
	sign := ""
	if strings.HasPrefix(s, "+") || strings.HasPrefix(s, "-") {
		sign = s[:1]
		s = s[1:]
	}
	lower := strings.ToLower(s)
	switch base {
	case 16:
		lower = strings.TrimPrefix(lower, "0x")
	case 8:
		lower = strings.TrimPrefix(lower, "0o")
	case 2:
		lower = strings.TrimPrefix(lower, "0b")
	}
	cleaned, ok := stripUnderscores(lower)
	if !ok || cleaned == "" {
		return nil, fail()
	}
	n, err := strconv.ParseInt(sign+cleaned, base, 64)
	if err == nil {
		return mkInt(n), nil
	}
	if z, ok := new(big.Int).SetString(sign+cleaned, base); ok {
		return normBig(z), nil
	}
	return nil, fail()
}

// stripUnderscores removes Python numeric-literal underscores (only valid
// between digits).
func stripUnderscores(s string) (string, bool) {
	if !strings.Contains(s, "_") {
		return s, true
	}
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '_' {
			if i == 0 || i == len(s)-1 || s[i-1] == '_' {
				return "", false
			}
			continue
		}
		b.WriteByte(s[i])
	}
	return b.String(), true
}

func ctorFloat(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if len(args) == 1 {
		if inst, ok := args[0].(*Instance); ok {
			if _, has := inst.Class.Lookup("__float__"); has {
				return vm.callDunder(inst, "__float__", nil)
			}
			if inst.Prim != nil {
				args[0] = inst.Prim
			} else {
				return nil, newErrorf("TypeError",
					"float() argument must be a string or a real number, not '%s'", inst.Class.Name)
			}
		}
	}
	return ctorFloatInner(vm, args, kw)
}

func ctorFloatInner(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("float", args, 0, 1); err != nil {
		return nil, err
	}
	if len(args) == 0 {
		return float64(0), nil
	}
	switch t := args[0].(type) {
	case float64:
		return t, nil
	case int64:
		return float64(t), nil
	case *big.Int:
		return bigToFloat(t), nil
	case bool:
		if t {
			return float64(1), nil
		}
		return float64(0), nil
	case string:
		return parsePyFloat(t)
	}
	return nil, newErrorf("TypeError",
		"float() argument must be a string or a real number, not '%s'", typeName(args[0]))
}

func parsePyFloat(orig string) (Value, error) {
	fail := func() error {
		return newErrorf("ValueError", "could not convert string to float: %s",
			reprString(orig))
	}
	s := strings.TrimFunc(orig, unicode.IsSpace)
	if s == "" {
		return nil, fail()
	}
	if strings.ContainsAny(s, "xXpP") {
		return nil, fail()
	}
	cleaned, ok := stripUnderscores(s)
	if !ok {
		return nil, fail()
	}
	f, err := strconv.ParseFloat(cleaned, 64)
	if err != nil {
		return nil, fail()
	}
	return f, nil
}

func ctorBool(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("bool", args, 0, 1); err != nil {
		return nil, err
	}
	if len(args) == 0 {
		return false, nil
	}
	return vm.truth(args[0])
}

func ctorStr(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("str", args, 0, 1); err != nil {
		return nil, err
	}
	if len(args) == 0 {
		return "", nil
	}
	return vm.str(args[0])
}

func ctorBytes(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("bytes", args, 0, 2); err != nil {
		return nil, err
	}
	if len(args) == 0 {
		return []byte{}, nil
	}
	switch t := args[0].(type) {
	case string:
		if len(args) < 2 {
			return nil, newError("TypeError", "string argument without an encoding")
		}
		enc, err := wantStr("encoding", args[1])
		if err != nil {
			return nil, err
		}
		return encodeStr(t, enc)
	case []byte:
		return append([]byte{}, t...), nil
	case int64:
		if t < 0 {
			return nil, newError("ValueError", "negative count")
		}
		return make([]byte, t), nil
	case bool:
		n := int64(0)
		if t {
			n = 1
		}
		return make([]byte, n), nil
	}
	items, err := vm.iterToSlice(args[0])
	if err != nil {
		return nil, err
	}
	out := make([]byte, len(items))
	for i, x := range items {
		n, ok := asInt(x)
		if !ok {
			return nil, newErrorf("TypeError",
				"'%s' object cannot be interpreted as an integer", typeName(x))
		}
		if n < 0 || n > 255 {
			return nil, newError("ValueError", "bytes must be in range(0, 256)")
		}
		out[i] = byte(n)
	}
	return out, nil
}

func ctorList(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if len(args) == 1 {
		args[0] = rawPrim(args[0])
		if err := vm.chargeSized(args[0]); err != nil {
			return nil, err
		}
	}
	if err := argCount("list", args, 0, 1); err != nil {
		return nil, err
	}
	if len(args) == 0 {
		return &List{}, nil
	}
	items, err := vm.iterToSlice(args[0])
	if err != nil {
		return nil, err
	}
	return &List{Items: items}, nil
}

func ctorTuple(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if len(args) == 1 {
		args[0] = rawPrim(args[0])
		if err := vm.chargeSized(args[0]); err != nil {
			return nil, err
		}
	}
	if err := argCount("tuple", args, 0, 1); err != nil {
		return nil, err
	}
	if len(args) == 0 {
		return &Tuple{}, nil
	}
	items, err := vm.iterToSlice(args[0])
	if err != nil {
		return nil, err
	}
	return &Tuple{Items: items}, nil
}

func ctorSet(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if len(args) == 1 {
		args[0] = rawPrim(args[0])
		if err := vm.chargeSized(args[0]); err != nil {
			return nil, err
		}
	}
	if err := argCount("set", args, 0, 1); err != nil {
		return nil, err
	}
	out := NewSet()
	if len(args) == 1 {
		items, err := vm.iterToSlice(args[0])
		if err != nil {
			return nil, err
		}
		for _, x := range items {
			if err := out.Add(x); err != nil {
				return nil, err
			}
		}
	}
	return out, nil
}

func ctorDict(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if len(args) > 1 {
		return nil, newErrorf("TypeError", "dict expected at most 1 argument, got %d", len(args))
	}
	out := NewDict()
	if len(args) == 1 {
		if err := dictUpdateFrom(vm, out, args[0]); err != nil {
			return nil, err
		}
	}
	for _, k := range kw {
		out.SetStr(k.Name, k.Val)
	}
	return out, nil
}

func ctorRange(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("range", args, 1, 3); err != nil {
		return nil, err
	}
	vals := make([]int64, len(args))
	for i, a := range args {
		n, ok := asInt(a)
		if !ok {
			return nil, newErrorf("TypeError",
				"'%s' object cannot be interpreted as an integer", typeName(a))
		}
		vals[i] = n
	}
	switch len(vals) {
	case 1:
		return &Range{Start: 0, Stop: vals[0], Step: 1}, nil
	case 2:
		return &Range{Start: vals[0], Stop: vals[1], Step: 1}, nil
	default:
		if vals[2] == 0 {
			return nil, newError("ValueError", "range() arg 3 must not be zero")
		}
		return &Range{Start: vals[0], Stop: vals[1], Step: vals[2]}, nil
	}
}

func ctorComplex(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("complex", args, 0, 2); err != nil {
		return nil, err
	}
	if len(args) == 0 {
		return complex(0, 0), nil
	}
	if s, ok := args[0].(string); ok {
		if len(args) != 1 {
			return nil, newError("TypeError", "complex() can't take second arg if first is a string")
		}
		z, err := parseComplex(s)
		if err != nil {
			return nil, err
		}
		return z, nil
	}
	re, ok := asComplex(args[0])
	if !ok {
		return nil, newErrorf("TypeError", "complex() first argument must be a string or a number, not '%s'", typeName(args[0]))
	}
	if len(args) == 2 {
		im, ok := asComplex(args[1])
		if !ok {
			return nil, newErrorf("TypeError", "complex() second argument must be a number, not '%s'", typeName(args[1]))
		}
		// complex(a, b) = (a.real - b.imag) + (a.imag + b.real)j
		return complex(real(re)-imag(im), imag(re)+real(im)), nil
	}
	return re, nil
}

func ctorType(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if len(args) == 3 {
		name, nameOK := args[0].(string)
		basesTup, basesOK := args[1].(*Tuple)
		nsDict, nsOK := args[2].(*Dict)
		if !nameOK || !basesOK || !nsOK {
			return nil, newError("TypeError", "type() argument types: (str, tuple, dict)")
		}
		var bases []*Class
		var primBase *BuiltinType
		for _, b := range basesTup.Items {
			switch cls := b.(type) {
			case *Class:
				bases = append(bases, cls)
			case *BuiltinType:
				if subclassablePrim(cls.Name) {
					primBase = cls
				} else {
					r, _ := vm.repr(b)
					return nil, newErrorf("TypeError", "cannot subclass %s in diaspore", r)
				}
			default:
				r, rerr := vm.repr(b)
				if rerr != nil {
					r = typeName(b)
				}
				return nil, newErrorf("TypeError", "cannot subclass %s in diaspore", r)
			}
		}
		ns := map[string]Value{}
		var order []string
		for _, k := range nsDict.Keys() {
			if ks, ok := k.(string); ok {
				v, _, _ := nsDict.Get(ks)
				ns[ks] = v
				order = append(order, ks)
			}
		}
		return vm.buildClass(name, bases, ns, order, nil, nil, primBase)
	}
	if len(args) == 2 || len(args) > 3 {
		return nil, newError("TypeError", "type() takes 1 or 3 arguments")
	}
	if err := argCount("type", args, 1, 1); err != nil {
		return nil, err
	}
	switch t := args[0].(type) {
	case *Instance:
		return t.Class, nil
	case *Class, *BuiltinType:
		return vm.types["type"], nil
	case bool:
		return vm.types["bool"], nil
	case int64:
		return vm.types["int"], nil
	case float64:
		return vm.types["float"], nil
	case string:
		return vm.types["str"], nil
	case []byte:
		return vm.types["bytes"], nil
	case *List:
		return vm.types["list"], nil
	case *Tuple:
		return vm.types["tuple"], nil
	case *Dict:
		return vm.types["dict"], nil
	case *Set:
		return vm.types["set"], nil
	case *Range:
		return vm.types["range"], nil
	case *big.Int:
		return vm.types["int"], nil
	case complex128:
		return vm.types["complex"], nil
	case NoneType:
		return vm.types["NoneType"], nil
	}
	name := typeName(args[0])
	return &BuiltinType{Name: name, ctor: func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		return nil, newErrorf("TypeError", "cannot construct '%s' values", name)
	}}, nil
}
