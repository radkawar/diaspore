package interp

import (
	"bytes"
	"math"
	"math/big"
	"math/cmplx"
	"reflect"
	"strconv"
	"strings"
)

var binSym = map[string]string{
	"add": "+", "sub": "-", "mul": "*", "div": "/", "floordiv": "//",
	"mod": "%", "pow": "**", "lshift": "<<", "rshift": ">>",
	"bitand": "&", "bitor": "|", "bitxor": "^", "matmul": "@",
}

var binDunder = map[string][2]string{
	"add": {"__add__", "__radd__"}, "sub": {"__sub__", "__rsub__"},
	"mul": {"__mul__", "__rmul__"}, "div": {"__truediv__", "__rtruediv__"},
	"matmul":   {"__matmul__", "__rmatmul__"},
	"floordiv": {"__floordiv__", "__rfloordiv__"}, "mod": {"__mod__", "__rmod__"},
	"pow": {"__pow__", "__rpow__"}, "lshift": {"__lshift__", "__rlshift__"},
	"rshift": {"__rshift__", "__rrshift__"}, "bitand": {"__and__", "__rand__"},
	"bitor": {"__or__", "__ror__"}, "bitxor": {"__xor__", "__rxor__"},
}

func asInt(v Value) (int64, bool) {
	switch t := v.(type) {
	case int64:
		return t, true
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	}
	return 0, false
}

func asFloat(v Value) (float64, bool) {
	switch t := v.(type) {
	case float64:
		return t, true
	case int64:
		return float64(t), true
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	case *big.Int:
		return bigToFloat(t), true
	}
	return 0, false
}

func isNumber(v Value) bool {
	switch v.(type) {
	case int64, float64, bool, *big.Int, complex128:
		return true
	}
	return false
}

func asComplex(v Value) (complex128, bool) {
	switch t := v.(type) {
	case complex128:
		return t, true
	case float64:
		return complex(t, 0), true
	case int64:
		return complex(float64(t), 0), true
	case bool:
		if t {
			return complex(1, 0), true
		}
		return complex(0, 0), true
	case *big.Int:
		return complex(bigToFloat(t), 0), true
	}
	return 0, false
}

func (vm *VM) binopTypeError(kind string, l, r Value) error {
	return newErrorf("TypeError", "unsupported operand type(s) for %s: '%s' and '%s'",
		binSym[kind], typeName(l), typeName(r))
}

func (vm *VM) binop(kind string, l, r Value) (Value, error) {
	return vm.binopWith(kind, l, r, false)
}

func (vm *VM) binopWith(kind string, l, r Value, inplace bool) (Value, error) {
	if kind == "mod" {
		switch l.(type) {
		case string, []byte:
			return nil, newError("TypeError",
				"diaspore does not support %-formatting; use f-strings")
		}
	}
	if kind == "mul" {
		// Charge fuel before allocating repeated sequences.
		seq, n := l, r
		if _, ok := asInt(l); ok {
			seq, n = r, l
		}
		if count, ok := asInt(n); ok && count > 0 {
			var size int64 = -1
			switch t := seq.(type) {
			case string:
				size = int64(len(t))
			case []byte:
				size = int64(len(t))
			case *List:
				size = int64(len(t.Items))
			case *Tuple:
				size = int64(len(t.Items))
			}
			if size >= 0 {
				if err := vm.fuelN(size*count/16 + 1); err != nil {
					return nil, err
				}
			}
		}
	}
	li, lInst := l.(*Instance)
	ri, rInst := r.(*Instance)
	if lInst || rInst {
		if names, ok := binDunder[kind]; ok {
			if inplace && lInst {
				// Augmented assignment tries __i<op>__ first.
				iname := "__i" + names[0][2:]
				if _, ok := li.Class.Lookup(iname); ok {
					return vm.callDunder(li, iname, []Value{r})
				}
			}
			if lInst {
				if _, ok := li.Class.Lookup(names[0]); ok {
					return vm.callDunder(li, names[0], []Value{r})
				}
			}
			if names[1] != "" && rInst {
				if _, ok := ri.Class.Lookup(names[1]); ok {
					return vm.callDunder(ri, names[1], []Value{l})
				}
			}
		}
		return nil, vm.binopTypeError(kind, l, r)
	}

	// int op int (bool counts as int)
	if la, ok := asInt(l); ok {
		if ra, ok := asInt(r); ok {
			return vm.intBinop(kind, la, ra)
		}
	}
	if isIntKind(l) && isIntKind(r) {
		lb, _ := asBig(l)
		rb, _ := asBig(r)
		return vm.bigBinop(kind, lb, rb)
	}
	if _, lc := l.(complex128); lc {
		if isNumber(r) {
			return vm.complexBinop(kind, l, r)
		}
	} else if _, rc := r.(complex128); rc {
		if isNumber(l) {
			return vm.complexBinop(kind, l, r)
		}
	}
	if isNumber(l) && isNumber(r) {
		la, _ := asFloat(l)
		ra, _ := asFloat(r)
		return vm.floatBinop(kind, la, ra)
	}

	switch kind {
	case "add":
		switch a := l.(type) {
		case string:
			if b, ok := r.(string); ok {
				return a + b, nil
			}
			return nil, newErrorf("TypeError",
				"can only concatenate str (not \"%s\") to str", typeName(r))
		case []byte:
			if b, ok := r.([]byte); ok {
				out := make([]byte, 0, len(a)+len(b))
				return append(append(out, a...), b...), nil
			}
			return nil, newErrorf("TypeError",
				"can only concatenate bytes (not \"%s\") to bytes", typeName(r))
		case *List:
			if b, ok := r.(*List); ok {
				if inplace {
					a.Items = append(a.Items, b.Items...)
					return a, nil
				}
				out := make([]Value, 0, len(a.Items)+len(b.Items))
				return &List{Items: append(append(out, a.Items...), b.Items...)}, nil
			}
			if inplace {
				items, err := vm.iterToSlice(r)
				if err == nil {
					a.Items = append(a.Items, items...)
					return a, nil
				}
			}
			return nil, newErrorf("TypeError",
				"can only concatenate list (not \"%s\") to list", typeName(r))
		case *Tuple:
			if b, ok := r.(*Tuple); ok {
				out := make([]Value, 0, len(a.Items)+len(b.Items))
				return &Tuple{Items: append(append(out, a.Items...), b.Items...)}, nil
			}
			return nil, newErrorf("TypeError",
				"can only concatenate tuple (not \"%s\") to tuple", typeName(r))
		}
	case "mul":
		if v, err, done := vm.seqRepeat(l, r); done {
			return v, err
		}
		if v, err, done := vm.seqRepeat(r, l); done {
			return v, err
		}
	case "bitor", "bitand", "bitxor", "sub":
		if a, ok := l.(*Set); ok {
			if b, ok := r.(*Set); ok {
				return setOp(kind, a, b)
			}
		}
	case "mod":
		// no string % formatting in diaspore
	}
	return nil, vm.binopTypeError(kind, l, r)
}

// seqRepeat handles seq * n; done reports whether the pair was a repeat form.
func (vm *VM) seqRepeat(seq, count Value) (Value, error, bool) {
	if _, isBig := count.(*big.Int); isBig {
		switch seq.(type) {
		case string, []byte, *List, *Tuple:
			return nil, newError("OverflowError",
				"cannot fit 'int' into an index-sized integer"), true
		}
	}
	n, ok := asInt(count)
	if !ok {
		switch seq.(type) {
		case string, []byte, *List, *Tuple:
			return nil, newErrorf("TypeError",
				"can't multiply sequence by non-int of type '%s'", typeName(count)), true
		}
		return nil, nil, false
	}
	if n < 0 {
		n = 0
	}
	switch a := seq.(type) {
	case string:
		return strings.Repeat(a, int(n)), nil, true
	case []byte:
		return bytes.Repeat(a, int(n)), nil, true
	case *List:
		out := make([]Value, 0, int(n)*len(a.Items))
		for i := int64(0); i < n; i++ {
			out = append(out, a.Items...)
		}
		return &List{Items: out}, nil, true
	case *Tuple:
		out := make([]Value, 0, int(n)*len(a.Items))
		for i := int64(0); i < n; i++ {
			out = append(out, a.Items...)
		}
		return &Tuple{Items: out}, nil, true
	}
	return nil, nil, false
}

func setOp(kind string, a, b *Set) (Value, error) {
	out := NewSet()
	switch kind {
	case "bitor":
		for _, e := range a.entries {
			out.addHK(e.hk, e.key)
		}
		for _, e := range b.entries {
			out.addHK(e.hk, e.key)
		}
	case "bitand":
		for _, e := range a.entries {
			if _, ok := b.index[e.hk]; ok {
				out.addHK(e.hk, e.key)
			}
		}
	case "sub":
		for _, e := range a.entries {
			if _, ok := b.index[e.hk]; !ok {
				out.addHK(e.hk, e.key)
			}
		}
	case "bitxor":
		for _, e := range a.entries {
			if _, ok := b.index[e.hk]; !ok {
				out.addHK(e.hk, e.key)
			}
		}
		for _, e := range b.entries {
			if _, ok := a.index[e.hk]; !ok {
				out.addHK(e.hk, e.key)
			}
		}
	}
	return out, nil
}

func (s *Set) addHK(hk string, k Value) {
	if _, ok := s.index[hk]; !ok {
		s.index[hk] = len(s.entries)
		s.entries = append(s.entries, dictEntry{hk: hk, key: k})
	}
}

func (vm *VM) intBinop(kind string, a, b int64) (Value, error) {
	switch kind {
	case "matmul":
		return nil, vm.binopTypeError(kind, a, b)
	case "add":
		if c, ovf := addOvf(a, b); !ovf {
			return mkInt(c), nil
		}
		return new(big.Int).Add(big.NewInt(a), big.NewInt(b)), nil
	case "sub":
		if c, ovf := subOvf(a, b); !ovf {
			return mkInt(c), nil
		}
		return new(big.Int).Sub(big.NewInt(a), big.NewInt(b)), nil
	case "mul":
		if c, ovf := mulOvf(a, b); !ovf {
			return mkInt(c), nil
		}
		return new(big.Int).Mul(big.NewInt(a), big.NewInt(b)), nil
	case "div":
		if b == 0 {
			return nil, newError("ZeroDivisionError", "division by zero")
		}
		return float64(a) / float64(b), nil
	case "floordiv":
		if b == 0 {
			return nil, newError("ZeroDivisionError", "integer division or modulo by zero")
		}
		if a == math.MinInt64 && b == -1 {
			return new(big.Int).Neg(big.NewInt(a)), nil
		}
		return mkInt(floorDiv(a, b)), nil
	case "mod":
		if b == 0 {
			return nil, newError("ZeroDivisionError", "integer modulo by zero")
		}
		return mkInt(floorMod(a, b)), nil
	case "pow":
		if b >= 0 {
			return vm.intPow(a, b)
		}
		if a == 0 {
			return nil, newError("ZeroDivisionError", "0.0 cannot be raised to a negative power")
		}
		return math.Pow(float64(a), float64(b)), nil
	case "lshift":
		if b < 0 {
			return nil, newError("ValueError", "negative shift count")
		}
		if c, ovf := lshOvf(a, b); !ovf {
			return mkInt(c), nil
		}
		return vm.bigBinop(kind, big.NewInt(a), big.NewInt(b))
	case "rshift":
		if b < 0 {
			return nil, newError("ValueError", "negative shift count")
		}
		if b >= 64 {
			if a < 0 {
				return int64(-1), nil
			}
			return int64(0), nil
		}
		return a >> uint(b), nil
	case "bitand":
		return a & b, nil
	case "bitor":
		return a | b, nil
	case "bitxor":
		return a ^ b, nil
	}
	return nil, newErrorf("TypeError", "unknown int op %s", kind)
}

func floorDiv(a, b int64) int64 {
	q := a / b
	if a%b != 0 && (a < 0) != (b < 0) {
		q--
	}
	return q
}

func floorMod(a, b int64) int64 {
	m := a % b
	if m != 0 && (m < 0) != (b < 0) {
		m += b
	}
	return m
}

// intPow computes base**exp for exp >= 0, promoting to big on overflow.
func (vm *VM) intPow(base, exp int64) (Value, error) {
	var result int64 = 1
	b, e := base, exp
	for e > 0 {
		var ovf bool
		if e&1 == 1 {
			if result, ovf = mulOvf(result, b); ovf {
				return vm.bigBinop("pow", big.NewInt(base), big.NewInt(exp))
			}
		}
		e >>= 1
		if e > 0 {
			if b, ovf = mulOvf(b, b); ovf {
				return vm.bigBinop("pow", big.NewInt(base), big.NewInt(exp))
			}
		}
	}
	return mkInt(result), nil
}

func (vm *VM) floatBinop(kind string, a, b float64) (Value, error) {
	switch kind {
	case "add":
		return a + b, nil
	case "sub":
		return a - b, nil
	case "mul":
		return a * b, nil
	case "div":
		if b == 0 {
			return nil, newError("ZeroDivisionError", "float division by zero")
		}
		return a / b, nil
	case "floordiv":
		if b == 0 {
			return nil, newError("ZeroDivisionError", "float floor division by zero")
		}
		return math.Floor(a / b), nil
	case "mod":
		if b == 0 {
			return nil, newError("ZeroDivisionError", "float modulo")
		}
		m := math.Mod(a, b)
		if m != 0 && (m < 0) != (b < 0) {
			m += b
		}
		return m, nil
	case "pow":
		return math.Pow(a, b), nil
	}
	return nil, newErrorf("TypeError",
		"unsupported operand type(s) for %s: 'float' and 'float'", binSym[kind])
}

// parseComplex parses a Python complex literal like "3+4j", "1j", "5".
func parseComplex(s string) (complex128, error) {
	str := strings.TrimSpace(s)
	str = strings.TrimPrefix(str, "(")
	str = strings.TrimSuffix(str, ")")
	if str == "" {
		return 0, newError("ValueError", "complex() arg is a malformed string")
	}
	if f, err := strconv.ParseFloat(str, 64); err == nil {
		return complex(f, 0), nil
	}
	if strings.HasSuffix(str, "j") || strings.HasSuffix(str, "J") {
		body := str[:len(str)-1]
		// split into real+imag at the last +/- that isn't an exponent sign
		for i := len(body) - 1; i > 0; i-- {
			c := body[i]
			if (c == '+' || c == '-') && body[i-1] != 'e' && body[i-1] != 'E' {
				re, err1 := strconv.ParseFloat(body[:i], 64)
				imStr := body[i:]
				if imStr == "+" {
					imStr = "1"
				} else if imStr == "-" {
					imStr = "-1"
				}
				im, err2 := strconv.ParseFloat(imStr, 64)
				if err1 == nil && err2 == nil {
					return complex(re, im), nil
				}
			}
		}
		if body == "" || body == "+" {
			return complex(0, 1), nil
		}
		if body == "-" {
			return complex(0, -1), nil
		}
		if im, err := strconv.ParseFloat(body, 64); err == nil {
			return complex(0, im), nil
		}
	}
	return 0, newError("ValueError", "complex() arg is a malformed string")
}

func cmplxPow(a, b complex128) complex128 {
	if b == 0 {
		return complex(1, 0)
	}
	return cmplx.Pow(a, b)
}

func (vm *VM) complexBinop(kind string, l, r Value) (Value, error) {
	a, _ := asComplex(l)
	b, _ := asComplex(r)
	switch kind {
	case "add":
		return a + b, nil
	case "sub":
		return a - b, nil
	case "mul":
		return a * b, nil
	case "div":
		if b == 0 {
			return nil, newError("ZeroDivisionError", "complex division by zero")
		}
		return a / b, nil
	case "pow":
		// Integer exponents: exact repeated multiplication (avoids the
		// rounding of exp(b*log(a))).
		if imag(b) == 0 && real(b) == math.Trunc(real(b)) && math.Abs(real(b)) <= 100 {
			n := int(real(b))
			neg := n < 0
			if neg {
				n = -n
			}
			result := complex(1, 0)
			for i := 0; i < n; i++ {
				result *= a
			}
			if neg {
				if result == 0 {
					return nil, newError("ZeroDivisionError", "0.0 to a negative or complex power")
				}
				result = 1 / result
			}
			return result, nil
		}
		return cmplxPow(a, b), nil
	}
	return nil, newErrorf("TypeError",
		"unsupported operand type(s) for %s: 'complex' and 'complex'", binSym[kind])
}

func (vm *VM) unary(kind string, v Value) (Value, error) {
	if inst, ok := v.(*Instance); ok {
		if kind == "neg" {
			if _, ok := inst.Class.Lookup("__neg__"); ok {
				return vm.callDunder(inst, "__neg__", nil)
			}
		}
		sym := map[string]string{"neg": "-", "pos": "+", "invert": "~"}[kind]
		return nil, newErrorf("TypeError", "bad operand type for unary %s: '%s'",
			sym, inst.Class.Name)
	}
	switch kind {
	case "neg":
		if n, ok := asInt(v); ok {
			if n == math.MinInt64 {
				return new(big.Int).Neg(big.NewInt(n)), nil
			}
			return mkInt(-n), nil
		}
		if f, ok := v.(float64); ok {
			return -f, nil
		}
		if c, ok := v.(complex128); ok {
			return -c, nil
		}
		if z, ok := v.(*big.Int); ok {
			return normBig(new(big.Int).Neg(z)), nil
		}
	case "pos":
		if n, ok := asInt(v); ok {
			return n, nil
		}
		if f, ok := v.(float64); ok {
			return f, nil
		}
		if c, ok := v.(complex128); ok {
			return c, nil
		}
		if z, ok := v.(*big.Int); ok {
			return z, nil
		}
	case "invert":
		if n, ok := asInt(v); ok {
			if n == math.MaxInt64 {
				return normBig(new(big.Int).Not(big.NewInt(n))), nil
			}
			return mkInt(^n), nil
		}
		if z, ok := v.(*big.Int); ok {
			return normBig(new(big.Int).Not(z)), nil
		}
	}
	sym := map[string]string{"neg": "-", "pos": "+", "invert": "~"}[kind]
	return nil, newErrorf("TypeError", "bad operand type for unary %s: '%s'", sym, typeName(v))
}

var cmpSym = map[string]string{"lt": "<", "le": "<=", "gt": ">", "ge": ">="}
var cmpDunder = map[string]string{"lt": "__lt__", "le": "__le__", "gt": "__gt__", "ge": "__ge__"}

func (vm *VM) compare(kind string, l, r Value) (bool, error) {
	switch kind {
	case "is":
		return isIdentical(l, r), nil
	case "isnot":
		return !isIdentical(l, r), nil
	case "in":
		return vm.contains(r, l)
	case "notin":
		ok, err := vm.contains(r, l)
		return !ok, err
	case "eq":
		return vm.eq(l, r)
	case "ne":
		if li, ok := l.(*Instance); ok {
			if _, ok := li.Class.Lookup("__ne__"); ok {
				res, err := vm.callDunder(li, "__ne__", []Value{r})
				if err != nil {
					return false, err
				}
				return vm.truth(res)
			}
		}
		ok, err := vm.eq(l, r)
		return !ok, err
	}
	if li, ok := l.(*Instance); ok {
		if _, has := li.Class.Lookup(cmpDunder[kind]); has {
			res, err := vm.callDunder(li, cmpDunder[kind], []Value{r})
			if err != nil {
				return false, err
			}
			return vm.truth(res)
		}
		return false, vm.orderTypeError(kind, l, r)
	}
	if _, ok := r.(*Instance); ok {
		return false, vm.orderTypeError(kind, l, r)
	}
	c, ordered, err := vm.cmp3(l, r, kind)
	if err != nil {
		return false, err
	}
	if !ordered {
		return false, nil // NaN comparisons
	}
	switch kind {
	case "lt":
		return c < 0, nil
	case "le":
		return c <= 0, nil
	case "gt":
		return c > 0, nil
	case "ge":
		return c >= 0, nil
	}
	return false, newErrorf("TypeError", "unknown comparison %s", kind)
}

// numCmp orders two non-NaN numbers exactly (int64/bool/big/float mixes).
func numCmp(l, r Value) int {
	lb, lBig := l.(*big.Int)
	rb, rBig := r.(*big.Int)
	switch {
	case lBig && rBig:
		return lb.Cmp(rb)
	case lBig:
		if f, ok := r.(float64); ok {
			return bigFloatCmp(lb, f)
		}
		return lb.Sign() // bigs are always outside int64 range
	case rBig:
		if f, ok := l.(float64); ok {
			return -bigFloatCmp(rb, f)
		}
		return -rb.Sign()
	}
	li, lok := asInt(l)
	ri, rok := asInt(r)
	if lok && rok {
		switch {
		case li < ri:
			return -1
		case li > ri:
			return 1
		}
		return 0
	}
	la, _ := asFloat(l)
	ra, _ := asFloat(r)
	switch {
	case la < ra:
		return -1
	case la > ra:
		return 1
	}
	return 0
}

func (vm *VM) orderTypeError(kind string, l, r Value) error {
	return newErrorf("TypeError", "'%s' not supported between instances of '%s' and '%s'",
		cmpSym[kind], typeName(l), typeName(r))
}

// cmp3 orders two non-instance values; ordered=false means an unordered
// float comparison (NaN involved).
func (vm *VM) cmp3(l, r Value, kind string) (c int, ordered bool, err error) {
	if isNumber(l) && isNumber(r) {
		if _, ok := l.(complex128); ok {
			return 0, false, vm.orderTypeError(kind, l, r)
		}
		if _, ok := r.(complex128); ok {
			return 0, false, vm.orderTypeError(kind, l, r)
		}
		if f, ok := l.(float64); ok && math.IsNaN(f) {
			return 0, false, nil
		}
		if f, ok := r.(float64); ok && math.IsNaN(f) {
			return 0, false, nil
		}
		return numCmp(l, r), true, nil
	}
	switch a := l.(type) {
	case string:
		if b, ok := r.(string); ok {
			return strings.Compare(a, b), true, nil
		}
	case []byte:
		if b, ok := r.([]byte); ok {
			return bytes.Compare(a, b), true, nil
		}
	case *List:
		if b, ok := r.(*List); ok {
			return vm.cmpSeq(a.Items, b.Items, kind)
		}
	case *Tuple:
		if b, ok := r.(*Tuple); ok {
			return vm.cmpSeq(a.Items, b.Items, kind)
		}
	case *Set:
		if b, ok := r.(*Set); ok {
			return vm.cmpSets(a, b, kind)
		}
	}
	return 0, false, vm.orderTypeError(kind, l, r)
}

func (vm *VM) cmpSeq(a, b []Value, kind string) (int, bool, error) {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	for i := 0; i < n; i++ {
		same, err := vm.eqElem(a[i], b[i])
		if err != nil {
			return 0, false, err
		}
		if !same {
			lt, err := vm.compare("lt", a[i], b[i])
			if err != nil {
				return 0, false, err
			}
			if lt {
				return -1, true, nil
			}
			return 1, true, nil
		}
	}
	switch {
	case len(a) < len(b):
		return -1, true, nil
	case len(a) > len(b):
		return 1, true, nil
	}
	return 0, true, nil
}

// cmpSets implements subset/superset ordering for < <= > >=.
func (vm *VM) cmpSets(a, b *Set, kind string) (int, bool, error) {
	sub := setIsSubset(a, b)
	sup := setIsSubset(b, a)
	switch {
	case sub && sup:
		return 0, true, nil
	case sub:
		return -1, true, nil
	case sup:
		return 1, true, nil
	}
	// Incomparable sets: Python returns False for every ordering. Encode as
	// unordered.
	return 0, false, nil
}

func setIsSubset(a, b *Set) bool {
	for _, e := range a.entries {
		if _, ok := b.index[e.hk]; !ok {
			return false
		}
	}
	return true
}

func isIdentical(l, r Value) bool {
	switch a := l.(type) {
	case NoneType:
		_, ok := r.(NoneType)
		return ok
	case bool:
		b, ok := r.(bool)
		return ok && a == b
	case int64:
		b, ok := r.(int64)
		return ok && a == b
	case float64:
		b, ok := r.(float64)
		return ok && math.Float64bits(a) == math.Float64bits(b)
	case string:
		b, ok := r.(string)
		return ok && a == b
	case []byte:
		b, ok := r.([]byte)
		if !ok || len(a) != len(b) {
			return false
		}
		return len(a) == 0 || &a[0] == &b[0]
	case *Tuple:
		b, ok := r.(*Tuple)
		if !ok {
			return false
		}
		// CPython interns the empty tuple, so `() is ()` is True.
		return a == b || (len(a.Items) == 0 && len(b.Items) == 0)
	}
	if _, ok := r.([]byte); ok {
		return false
	}
	if !reflect.TypeOf(l).Comparable() || r == nil || !reflect.TypeOf(r).Comparable() {
		return false
	}
	return l == r
}

func (vm *VM) eq(l, r Value) (bool, error) {
	if li, ok := l.(*Instance); ok {
		if _, has := li.Class.Lookup("__eq__"); has {
			res, err := vm.callDunder(li, "__eq__", []Value{r})
			if err != nil {
				return false, err
			}
			return vm.truth(res)
		}
		if ri, ok := r.(*Instance); ok {
			if _, has := ri.Class.Lookup("__eq__"); has {
				res, err := vm.callDunder(ri, "__eq__", []Value{l})
				if err != nil {
					return false, err
				}
				return vm.truth(res)
			}
		}
		return isIdentical(l, r), nil
	}
	if _, ok := r.(*Instance); ok {
		return vm.eq(r, l)
	}
	if isNumber(l) && isNumber(r) {
		_, lc := l.(complex128)
		_, rc := r.(complex128)
		if lc || rc {
			a, _ := asComplex(l)
			b, _ := asComplex(r)
			return a == b, nil
		}
		if f, ok := l.(float64); ok && math.IsNaN(f) {
			return false, nil
		}
		if f, ok := r.(float64); ok && math.IsNaN(f) {
			return false, nil
		}
		return numCmp(l, r) == 0, nil
	}
	switch a := l.(type) {
	case string:
		b, ok := r.(string)
		return ok && a == b, nil
	case []byte:
		b, ok := r.([]byte)
		return ok && bytes.Equal(a, b), nil
	case NoneType:
		_, ok := r.(NoneType)
		return ok, nil
	case *List:
		if b, ok := r.(*List); ok {
			return vm.eqSeq(a.Items, b.Items)
		}
		return false, nil
	case *Tuple:
		if b, ok := r.(*Tuple); ok {
			return vm.eqSeq(a.Items, b.Items)
		}
		return false, nil
	case *Dict:
		b, ok := r.(*Dict)
		if !ok || a.Len() != b.Len() {
			return false, nil
		}
		for _, e := range a.entries {
			bi, ok := b.lookupEntry(&e)
			if !ok {
				return false, nil
			}
			bv := b.entries[bi].val
			same, err := vm.eqElem(e.val, bv)
			if err != nil {
				return false, err
			}
			if !same {
				return false, nil
			}
		}
		return true, nil
	case *Set:
		b, ok := r.(*Set)
		if !ok || a.Len() != b.Len() {
			return false, nil
		}
		return setIsSubset(a, b), nil
	case *Range:
		b, ok := r.(*Range)
		if !ok {
			return false, nil
		}
		if a.Len() != b.Len() {
			return false, nil
		}
		if a.Len() == 0 {
			return true, nil
		}
		if a.Start != b.Start {
			return false, nil
		}
		return a.Len() == 1 || a.Step == b.Step, nil
	}
	return isIdentical(l, r), nil
}

// eqElem is element-level equality inside containers and for `in`, matching
// CPython's PyObject_RichCompareBool: identity implies equality - so
// [nan] == [nan] is True while nan == nan stays False. Diaspore floats are
// unboxed, so any two NaNs count as the identical value.
func (vm *VM) eqElem(a, b Value) (bool, error) {
	if isIdentical(a, b) {
		return true, nil
	}
	if af, ok := a.(float64); ok {
		if bf, ok := b.(float64); ok && math.IsNaN(af) && math.IsNaN(bf) {
			return true, nil
		}
	}
	return vm.eq(a, b)
}

func (vm *VM) eqSeq(a, b []Value) (bool, error) {
	if len(a) != len(b) {
		return false, nil
	}
	for i := range a {
		same, err := vm.eqElem(a[i], b[i])
		if err != nil {
			return false, err
		}
		if !same {
			return false, nil
		}
	}
	return true, nil
}

func (vm *VM) contains(container, item Value) (bool, error) {
	switch c := container.(type) {
	case *Instance:
		if _, ok := c.Class.Lookup("__contains__"); ok {
			res, err := vm.callDunder(c, "__contains__", []Value{item})
			if err != nil {
				return false, err
			}
			return vm.truth(res)
		}
		if c.Prim != nil {
			if _, ok := c.Class.Lookup("__iter__"); !ok {
				return vm.contains(c.Prim, item)
			}
		}
		return vm.iterContains(container, item)
	case string:
		s, ok := item.(string)
		if !ok {
			return false, newErrorf("TypeError",
				"'in <string>' requires string as left operand, not %s", typeName(item))
		}
		return strings.Contains(c, s), nil
	case []byte:
		switch it := item.(type) {
		case []byte:
			return bytes.Contains(c, it), nil
		case int64:
			if it < 0 || it > 255 {
				return false, newError("ValueError", "byte must be in range(0, 256)")
			}
			return bytes.IndexByte(c, byte(it)) >= 0, nil
		}
		return false, newErrorf("TypeError",
			"a bytes-like object is required, not '%s'", typeName(item))
	case *List:
		return vm.seqContains(c.Items, item)
	case *Tuple:
		return vm.seqContains(c.Items, item)
	case *Dict:
		_, ok, err := c.Get(item)
		return ok, err
	case *Set:
		return c.Has(item)
	case *Range:
		n, ok := asInt(item)
		if !ok {
			if f, isF := item.(float64); isF {
				if f != math.Trunc(f) {
					return false, nil
				}
				n = int64(f)
			} else {
				return false, nil
			}
		}
		return rangeContains(c, n), nil
	case Iterator:
		return vm.iterContains(container, item)
	}
	return false, newErrorf("TypeError", "argument of type '%s' is not iterable",
		typeName(container))
}

func rangeContains(r *Range, n int64) bool {
	if r.Step > 0 {
		return n >= r.Start && n < r.Stop && (n-r.Start)%r.Step == 0
	}
	return n <= r.Start && n > r.Stop && (r.Start-n)%(-r.Step) == 0
}

func (vm *VM) seqContains(items []Value, item Value) (bool, error) {
	for _, x := range items {
		same, err := vm.eqElem(x, item)
		if err != nil {
			return false, err
		}
		if same {
			return true, nil
		}
	}
	return false, nil
}

func (vm *VM) iterContains(container, item Value) (bool, error) {
	it, err := vm.getIter(container)
	if err != nil {
		return false, err
	}
	for {
		x, ok, err := it.Next()
		if err != nil {
			return false, err
		}
		if !ok {
			return false, nil
		}
		same, err := vm.eqElem(x, item)
		if err != nil {
			return false, err
		}
		if same {
			return true, nil
		}
	}
}

func (vm *VM) truth(v Value) (bool, error) {
	switch t := v.(type) {
	case bool:
		return t, nil
	case NoneType:
		return false, nil
	case int64:
		return t != 0, nil
	case float64:
		return t != 0, nil
	case string:
		return t != "", nil
	case []byte:
		return len(t) > 0, nil
	case *List:
		return len(t.Items) > 0, nil
	case *Tuple:
		return len(t.Items) > 0, nil
	case *Dict:
		return t.Len() > 0, nil
	case *Set:
		return t.Len() > 0, nil
	case *Range:
		return t.Len() > 0, nil
	case *big.Int:
		return t.Sign() != 0, nil
	case *Instance:
		if _, hasBool := t.Class.Lookup("__bool__"); !hasBool {
			if _, hasLen := t.Class.Lookup("__len__"); !hasLen && t.Prim != nil {
				return vm.truth(t.Prim)
			}
		}
		if _, ok := t.Class.Lookup("__bool__"); ok {
			r, err := vm.callDunder(t, "__bool__", nil)
			if err != nil {
				return false, err
			}
			b, ok := r.(bool)
			if !ok {
				return false, newErrorf("TypeError",
					"__bool__ should return bool, returned %s", typeName(r))
			}
			return b, nil
		}
		if _, ok := t.Class.Lookup("__len__"); ok {
			n, err := vm.diasporeLen(t)
			if err != nil {
				return false, err
			}
			return n != 0, nil
		}
		return true, nil
	}
	return true, nil
}

func (vm *VM) diasporeLen(v Value) (int64, error) {
	switch t := v.(type) {
	case string:
		return int64(len([]rune(t))), nil
	case []byte:
		return int64(len(t)), nil
	case *List:
		return int64(len(t.Items)), nil
	case *Tuple:
		return int64(len(t.Items)), nil
	case *Dict:
		return int64(t.Len()), nil
	case *Set:
		return int64(t.Len()), nil
	case *Range:
		return t.Len(), nil
	case *Instance:
		if _, ok := t.Class.Lookup("__len__"); !ok && t.Prim != nil {
			return vm.diasporeLen(t.Prim)
		}
		r, err := vm.callDunderOrErr(t, "__len__", nil,
			"object of type '"+t.Class.Name+"' has no len()")
		if err != nil {
			return 0, err
		}
		if n, ok := r.(int64); ok {
			return n, nil
		}
		return 0, newError("TypeError", "__len__ should return an int")
	}
	return 0, newErrorf("TypeError", "object of type '%s' has no len()", typeName(v))
}

func seqIndexCheck(n int, idx int64, kindMsg string) (int, error) {
	i := idx
	if i < 0 {
		i += int64(n)
	}
	if i < 0 || i >= int64(n) {
		return 0, newError("IndexError", kindMsg)
	}
	return int(i), nil
}

func indexTypeError(container string, k Value) error {
	return newErrorf("TypeError", "%s indices must be integers or slices, not %s",
		container, typeName(k))
}

func (vm *VM) getIndex(o, k Value) (Value, error) {
	switch t := o.(type) {
	case *Instance:
		if _, ok := t.Class.Lookup("__getitem__"); !ok && t.Prim != nil {
			if d, ok := t.Prim.(*Dict); ok {
				if _, present, _ := d.Get(k); !present {
					if _, hasMissing := t.Class.Lookup("__missing__"); hasMissing {
						return vm.callDunder(t, "__missing__", []Value{k})
					}
				}
			}
			return vm.getIndex(t.Prim, k)
		}
		return vm.callDunderOrErr(t, "__getitem__", []Value{k},
			"'"+t.Class.Name+"' object is not subscriptable")
	case *List:
		if sl, ok := k.(*Slice); ok {
			start, stop, step, err := sliceIndices(sl, len(t.Items))
			if err != nil {
				return nil, err
			}
			return &List{Items: sliceSeq(t.Items, start, stop, step)}, nil
		}
		n, ok := asInt(k)
		if !ok {
			return nil, indexTypeError("list", k)
		}
		i, err := seqIndexCheck(len(t.Items), n, "list index out of range")
		if err != nil {
			return nil, err
		}
		return t.Items[i], nil
	case *Tuple:
		if sl, ok := k.(*Slice); ok {
			start, stop, step, err := sliceIndices(sl, len(t.Items))
			if err != nil {
				return nil, err
			}
			return &Tuple{Items: sliceSeq(t.Items, start, stop, step)}, nil
		}
		n, ok := asInt(k)
		if !ok {
			return nil, indexTypeError("tuple", k)
		}
		i, err := seqIndexCheck(len(t.Items), n, "tuple index out of range")
		if err != nil {
			return nil, err
		}
		return t.Items[i], nil
	case string:
		rs := []rune(t)
		if sl, ok := k.(*Slice); ok {
			start, stop, step, err := sliceIndices(sl, len(rs))
			if err != nil {
				return nil, err
			}
			var b strings.Builder
			forSlice(start, stop, step, func(i int) { b.WriteRune(rs[i]) })
			return b.String(), nil
		}
		n, ok := asInt(k)
		if !ok {
			return nil, indexTypeError("string", k)
		}
		i, err := seqIndexCheck(len(rs), n, "string index out of range")
		if err != nil {
			return nil, err
		}
		return string(rs[i]), nil
	case []byte:
		if sl, ok := k.(*Slice); ok {
			start, stop, step, err := sliceIndices(sl, len(t))
			if err != nil {
				return nil, err
			}
			out := []byte{}
			forSlice(start, stop, step, func(i int) { out = append(out, t[i]) })
			return out, nil
		}
		n, ok := asInt(k)
		if !ok {
			return nil, indexTypeError("byte", k)
		}
		i, err := seqIndexCheck(len(t), n, "index out of range")
		if err != nil {
			return nil, err
		}
		return int64(t[i]), nil
	case *Dict:
		if _, isSlice := k.(*Slice); isSlice {
			return nil, newError("TypeError", "unhashable type: 'slice'")
		}
		v, ok, err := t.Get(k)
		if err != nil {
			return nil, err
		}
		if !ok {
			return nil, newErrorArgs("KeyError", k)
		}
		return v, nil
	case *Range:
		if sl, ok := k.(*Slice); ok {
			return sliceRange(t, sl)
		}
		n, ok := asInt(k)
		if !ok {
			return nil, indexTypeError("range", k)
		}
		i, err := seqIndexCheck(int(t.Len()), n, "range object index out of range")
		if err != nil {
			return nil, err
		}
		return t.Start + int64(i)*t.Step, nil
	}
	return nil, newErrorf("TypeError", "'%s' object is not subscriptable", typeName(o))
}

func (vm *VM) setIndex(o, k, v Value) error {
	switch t := o.(type) {
	case *Instance:
		if _, ok := t.Class.Lookup("__setitem__"); !ok && t.Prim != nil {
			return vm.setIndex(t.Prim, k, v)
		}
		_, err := vm.callDunderOrErr(t, "__setitem__", []Value{k, v},
			"'"+t.Class.Name+"' object does not support item assignment")
		return err
	case *List:
		if sl, ok := k.(*Slice); ok {
			return vm.setListSlice(t, sl, v)
		}
		n, ok := asInt(k)
		if !ok {
			return indexTypeError("list", k)
		}
		i, err := seqIndexCheck(len(t.Items), n, "list assignment index out of range")
		if err != nil {
			return err
		}
		t.Items[i] = v
		return nil
	case *Dict:
		if _, isSlice := k.(*Slice); isSlice {
			return newError("TypeError", "unhashable type: 'slice'")
		}
		return t.Set(k, v)
	}
	return newErrorf("TypeError", "'%s' object does not support item assignment", typeName(o))
}

func (vm *VM) setListSlice(l *List, sl *Slice, v Value) error {
	items, err := vm.iterToSlice(v)
	if err != nil {
		return err
	}
	start, stop, step, err := sliceIndices(sl, len(l.Items))
	if err != nil {
		return err
	}
	if step == 1 {
		if stop < start {
			stop = start
		}
		out := make([]Value, 0, len(l.Items)-(stop-start)+len(items))
		out = append(out, l.Items[:start]...)
		out = append(out, items...)
		out = append(out, l.Items[stop:]...)
		l.Items = out
		return nil
	}
	var idxs []int
	forSlice(start, stop, step, func(i int) { idxs = append(idxs, i) })
	if len(idxs) != len(items) {
		return newErrorf("ValueError",
			"attempt to assign sequence of size %d to extended slice of size %d",
			len(items), len(idxs))
	}
	for j, i := range idxs {
		l.Items[i] = items[j]
	}
	return nil
}

func (vm *VM) delIndex(o, k Value) error {
	switch t := o.(type) {
	case *Instance:
		if _, ok := t.Class.Lookup("__delitem__"); !ok && t.Prim != nil {
			return vm.delIndex(t.Prim, k)
		}
		_, err := vm.callDunderOrErr(t, "__delitem__", []Value{k},
			"'"+t.Class.Name+"' object doesn't support item deletion")
		return err
	case *List:
		if sl, ok := k.(*Slice); ok {
			start, stop, step, err := sliceIndices(sl, len(t.Items))
			if err != nil {
				return err
			}
			keep := make(map[int]bool)
			forSlice(start, stop, step, func(i int) { keep[i] = true })
			out := t.Items[:0]
			for i, x := range t.Items {
				if !keep[i] {
					out = append(out, x)
				}
			}
			t.Items = out
			return nil
		}
		n, ok := asInt(k)
		if !ok {
			return indexTypeError("list", k)
		}
		i, err := seqIndexCheck(len(t.Items), n, "list assignment index out of range")
		if err != nil {
			return err
		}
		t.Items = append(t.Items[:i], t.Items[i+1:]...)
		return nil
	case *Dict:
		_, ok, err := t.Delete(k)
		if err != nil {
			return err
		}
		if !ok {
			return newErrorArgs("KeyError", k)
		}
		return nil
	}
	return newErrorf("TypeError", "'%s' object doesn't support item deletion", typeName(o))
}

// sliceIndices resolves a slice against a sequence of length n, CPython-style.
func sliceIndices(sl *Slice, n int) (start, stop, step int, err error) {
	getInt := func(v Value) (int64, bool, error) {
		if _, isNone := v.(NoneType); isNone {
			return 0, false, nil
		}
		i, ok := asInt(v)
		if !ok {
			return 0, false, newError("TypeError",
				"slice indices must be integers or None or have an __index__ method")
		}
		return i, true, nil
	}
	stepV, hasStep, err := getInt(sl.Step)
	if err != nil {
		return 0, 0, 0, err
	}
	step = 1
	if hasStep {
		if stepV == 0 {
			return 0, 0, 0, newError("ValueError", "slice step cannot be zero")
		}
		step = int(stepV)
	}
	clamp := func(v int64) int {
		if v < 0 {
			v += int64(n)
			if v < 0 {
				if step < 0 {
					return -1
				}
				return 0
			}
		} else if v >= int64(n) {
			if step < 0 {
				return n - 1
			}
			return n
		}
		return int(v)
	}
	lowV, hasLow, err := getInt(sl.Lower)
	if err != nil {
		return 0, 0, 0, err
	}
	upV, hasUp, err := getInt(sl.Upper)
	if err != nil {
		return 0, 0, 0, err
	}
	if step > 0 {
		start, stop = 0, n
	} else {
		start, stop = n-1, -1
	}
	if hasLow {
		start = clamp(lowV)
	}
	if hasUp {
		stop = clamp(upV)
	}
	return start, stop, step, nil
}

func forSlice(start, stop, step int, fn func(i int)) {
	if step > 0 {
		for i := start; i < stop; i += step {
			fn(i)
		}
	} else {
		for i := start; i > stop; i += step {
			fn(i)
		}
	}
}

func sliceSeq(items []Value, start, stop, step int) []Value {
	out := []Value{}
	forSlice(start, stop, step, func(i int) { out = append(out, items[i]) })
	return out
}

func sliceRange(r *Range, sl *Slice) (Value, error) {
	n := int(r.Len())
	start, stop, step, err := sliceIndices(sl, n)
	if err != nil {
		return nil, err
	}
	count := 0
	forSlice(start, stop, step, func(int) { count++ })
	newStart := r.Start + int64(start)*r.Step
	newStep := r.Step * int64(step)
	newStop := newStart + int64(count)*newStep
	return &Range{Start: newStart, Stop: newStop, Step: newStep}, nil
}

// stableSort is a stable merge sort using only the provided less function
// (which may fail with a diaspore TypeError for incomparable values).
func stableSort(items []Value, less func(a, b Value) (bool, error)) error {
	n := len(items)
	if n < 2 {
		return nil
	}
	buf := make([]Value, n)
	var rec func(lo, hi int) error
	rec = func(lo, hi int) error {
		if hi-lo < 2 {
			return nil
		}
		mid := (lo + hi) / 2
		if err := rec(lo, mid); err != nil {
			return err
		}
		if err := rec(mid, hi); err != nil {
			return err
		}
		i, j, k := lo, mid, lo
		for i < mid && j < hi {
			l, err := less(items[j], items[i])
			if err != nil {
				return err
			}
			if l {
				buf[k] = items[j]
				j++
			} else {
				buf[k] = items[i]
				i++
			}
			k++
		}
		for i < mid {
			buf[k] = items[i]
			i++
			k++
		}
		for j < hi {
			buf[k] = items[j]
			j++
			k++
		}
		copy(items[lo:hi], buf[lo:hi])
		return nil
	}
	return rec(0, n)
}

// sortValues sorts items in place, stably, with optional key function and
// reverse flag (Python semantics: reverse is applied around the stable sort).
func (vm *VM) sortValues(items []Value, key Value, reverse bool) error {
	type kv struct{ k, v Value }
	useKey := key != nil
	if _, isNone := key.(NoneType); isNone {
		useKey = false
	}
	less := func(a, b Value) (bool, error) { return vm.compare("lt", a, b) }
	if reverse {
		reverseSlice(items)
	}
	var err error
	if useKey {
		dec := make([]Value, len(items))
		for i, x := range items {
			k, kerr := vm.call(key, []Value{x}, nil)
			if kerr != nil {
				err = kerr
				break
			}
			dec[i] = &kv{k, x}
		}
		if err == nil {
			err = stableSort(dec, func(a, b Value) (bool, error) {
				return less(a.(*kv).k, b.(*kv).k)
			})
			if err == nil {
				for i, d := range dec {
					items[i] = d.(*kv).v
				}
			}
		}
	} else {
		err = stableSort(items, less)
	}
	if reverse {
		reverseSlice(items)
	}
	return err
}

func reverseSlice(items []Value) {
	for i, j := 0, len(items)-1; i < j; i, j = i+1, j-1 {
		items[i], items[j] = items[j], items[i]
	}
}
