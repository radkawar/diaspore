package interp

import (
	"bytes"
	"fmt"
	"strings"
	"unicode"
	"unicode/utf8"
)

// rawPrim unwraps a builtin-subclass instance to its underlying primitive.
func rawPrim(v Value) Value {
	if inst, ok := v.(*Instance); ok && inst.Prim != nil {
		return inst.Prim
	}
	return v
}

// builtinMethodNames lists the reflected method surface per builtin type, so
// unbound access (dict.keys, list.append) can be resolved.
var builtinMethodNames = map[string]map[string]bool{
	"range":              setOfNames("__getitem__", "__len__", "__bool__", "__contains__", "__iter__", "__reversed__", "__hash__", "__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__", "__repr__", "__str__", "__reduce__", "__reduce_ex__", "count", "index"),
	"range_iterator":     setOfNames("__iter__", "__next__", "__length_hint__", "__reduce__", "__reduce_ex__", "__setstate__"),
	"longrange_iterator": setOfNames("__iter__", "__next__", "__length_hint__", "__reduce__", "__reduce_ex__", "__setstate__"),
	"dict":               setOfNames("__getitem__", "__setitem__", "__delitem__", "__len__", "__contains__", "__iter__", "get", "keys", "values", "items", "pop", "update", "setdefault", "clear", "copy"),
	"list":               setOfNames("__getitem__", "__setitem__", "__delitem__", "__len__", "__contains__", "__iter__", "__reversed__", "append", "extend", "insert", "pop", "remove", "clear", "index", "count", "reverse", "sort", "copy"),
	"set":                setOfNames("__len__", "__contains__", "__iter__", "add", "update", "remove", "discard", "pop", "clear", "copy", "union", "intersection", "difference", "difference_update", "symmetric_difference", "issubset", "issuperset"),
	"frozenset":          setOfNames("__len__", "__contains__", "__iter__", "copy", "union", "intersection", "difference", "symmetric_difference", "issubset", "issuperset", "isdisjoint"),
	"str":                setOfNames("__getitem__", "__len__", "__contains__", "__iter__", "upper", "lower", "title", "capitalize", "casefold", "expandtabs", "strip", "lstrip", "rstrip", "split", "rsplit", "splitlines", "join", "replace", "translate", "find", "rfind", "index", "count", "startswith", "endswith", "isascii", "isdecimal", "isdigit", "isalpha", "isalnum", "isspace", "isidentifier", "isprintable", "islower", "isupper", "swapcase", "zfill", "ljust", "rjust", "center", "encode", "partition", "rpartition", "removeprefix", "removesuffix", "format", "format_map"),
	"tuple":              setOfNames("__getitem__", "__len__", "__contains__", "__iter__", "count", "index"),
	"bytes":              setOfNames("__getitem__", "__len__", "__contains__", "__iter__", "decode", "find", "rfind", "count", "split", "strip", "lstrip", "rstrip", "replace", "startswith", "endswith", "upper", "lower", "hex", "join", "translate", "ljust", "rjust", "center", "isascii"),
	"bytearray":          setOfNames("__getitem__", "__setitem__", "__delitem__", "__len__", "__contains__", "__iter__", "append", "extend", "insert", "remove", "clear", "pop", "reverse", "copy", "decode", "find", "rfind", "count", "split", "strip", "lstrip", "rstrip", "replace", "startswith", "endswith", "upper", "lower", "hex", "join", "translate", "ljust", "rjust", "center", "isascii"),
}

func setOfNames(names ...string) map[string]bool {
	m := make(map[string]bool, len(names))
	for _, n := range names {
		m[n] = true
	}
	return m
}

// builtinUnbound returns `dict.__getitem__`, `list.append`, ... as unbound
// functions that operate on the underlying primitive (bypassing subclass overrides),
// so a builtin-subclass can call its base implementation.
func (vm *VM) builtinUnbound(tname, method string) *Builtin {
	switch method {
	case "__repr__", "__str__":
		return bi(method, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount(method, args, 1, 1); err != nil {
				return nil, err
			}
			if err := noKw(method, kw); err != nil {
				return nil, err
			}
			value := rawPrim(args[0])
			if method == "__str__" {
				return vm.str(value)
			}
			return vm.repr(value)
		})
	case "__abs__", "__hash__", "__neg__", "__pos__", "__invert__":
		if tname == "int" || tname == "bool" || tname == "float" || tname == "complex" {
			if method == "__invert__" && tname != "int" && tname != "bool" {
				return nil
			}
			return bi(method, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := argCount(method, args, 1, 1); err != nil {
					return nil, err
				}
				if err := noKw(method, kw); err != nil {
					return nil, err
				}
				value := rawPrim(args[0])
				valid := typeName(value) == tname || (tname == "int" && isIntKind(value))
				if !valid {
					return nil, newErrorf("TypeError", "descriptor '%s' for '%s' objects doesn't apply to a '%s' object", method, tname, typeName(args[0]))
				}
				if method == "__hash__" {
					return hashValue(value)
				}
				if kind := unaryMethodKind(method); kind != "" {
					return vm.unary(kind, value)
				}
				return biAbs(vm, []Value{value}, nil)
			})
		}
	case "__index__", "__int__":
		if tname == "int" || tname == "bool" {
			return bi(method, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := argCount(method, args, 1, 1); err != nil {
					return nil, err
				}
				if err := noKw(method, kw); err != nil {
					return nil, err
				}
				value := rawPrim(args[0])
				if n, ok := asInt(value); ok {
					return n, nil
				}
				if isIntKind(value) {
					return value, nil
				}
				return nil, newError("TypeError", "descriptor requires an int object")
			})
		}
	case "__getitem__":
		return bi(method, func(vm *VM, a []Value, kw []KwArg) (Value, error) {
			if err := argCount(method, a, 2, 2); err != nil {
				return nil, err
			}
			return vm.getIndex(rawPrim(a[0]), a[1])
		})
	case "__setitem__":
		return bi(method, func(vm *VM, a []Value, kw []KwArg) (Value, error) {
			if err := argCount(method, a, 3, 3); err != nil {
				return nil, err
			}
			if err := vm.setIndex(rawPrim(a[0]), a[1], a[2]); err != nil {
				return nil, err
			}
			return None, nil
		})
	case "__delitem__":
		return bi(method, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount(method, args, 2, 2); err != nil {
				return nil, err
			}
			if err := noKw(method, kw); err != nil {
				return nil, err
			}
			if err := vm.delIndex(rawPrim(args[0]), args[1]); err != nil {
				return nil, err
			}
			return None, nil
		})
	case "__len__":
		return bi(method, func(vm *VM, a []Value, kw []KwArg) (Value, error) {
			n, err := vm.diasporeLen(rawPrim(a[0]))
			if err != nil {
				return nil, err
			}
			return mkInt(n), nil
		})
	case "__contains__":
		return bi(method, func(vm *VM, a []Value, kw []KwArg) (Value, error) {
			b, err := vm.contains(rawPrim(a[0]), a[1])
			if err != nil {
				return nil, err
			}
			return b, nil
		})
	case "__iter__":
		return bi(method, func(vm *VM, a []Value, kw []KwArg) (Value, error) {
			it, err := vm.getIter(rawPrim(a[0]))
			if err != nil {
				return nil, err
			}
			return it, nil
		})
	}
	if names := builtinMethodNames[tname]; names != nil && names[method] {
		return bi(method, func(vm *VM, a []Value, kw []KwArg) (Value, error) {
			if len(a) < 1 {
				return nil, newErrorf("TypeError", "unbound method %s.%s needs an argument", tname, method)
			}
			m, err := vm.builtinMethod(rawPrim(a[0]), method)
			if err != nil {
				return nil, err
			}
			return vm.call(m, a[1:], kw)
		})
	}
	return nil
}

func (vm *VM) builtinMethod(o Value, n string) (Value, error) {
	var fn func(vm *VM, args []Value, kw []KwArg) (Value, error)
	switch t := o.(type) {
	case *Range:
		return vm.rangeAttr(t, n)
	case *rangeIter, *longRangeIter:
		return vm.rangeIteratorAttr(t, n)
	case string:
		if n == "maketrans" {
			return strMakeTransBuiltin, nil
		}
		fn = strMethod(t, n)
	case *List:
		fn = listMethod(t, n)
	case *Dict:
		fn = dictMethod(t, n)
	case *classDict:
		if t.mapping != nil && (n == "keys" || n == "values" || n == "items") {
			return vm.builtinMethod(t.mapping, n)
		}
		switch n {
		case "keys", "values", "items":
			fn = func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				if err := argCount(n, args, 0, 0); err != nil {
					return nil, err
				}
				return &DictView{class: t.cls, kind: n}, noKw(n, kw)
			}
		case "get", "copy":
			fn = func(vm *VM, args []Value, kw []KwArg) (Value, error) {
				return dictMethod(t.snapshot(), n)(vm, args, kw)
			}
		}
	case *Set:
		fn = setMethod(t, n)
	case *FrozenSet:
		fn = frozenSetMethod(t, n)
	case *DictView:
		fn = dictViewMethod(t, n)
	case *Tuple:
		fn = tupleMethod(t, n)
	case []byte:
		if n == "maketrans" {
			return bytesMakeTransBuiltin, nil
		}
		fn = bytesMethod(t, n)
	case *ByteArray, *MemoryView:
		return vm.bufferAttr(t, n)
	}
	if fn == nil {
		return nil, newErrorf("AttributeError", "'%s' object has no attribute '%s'",
			typeName(o), n)
	}
	return &Builtin{Name: n, Fn: fn}, nil
}

func noKw(name string, kw []KwArg) error {
	if len(kw) > 0 {
		return newErrorf("TypeError", "%s() takes no keyword arguments", name)
	}
	return nil
}

func needArgs(name string, args []Value, min, max int) error {
	if len(args) < min {
		return newErrorf("TypeError", "%s expected at least %d arguments, got %d",
			name, min, len(args))
	}
	if max >= 0 && len(args) > max {
		return newErrorf("TypeError", "%s expected at most %d arguments, got %d",
			name, max, len(args))
	}
	return nil
}

func wantInt(what string, v Value) (int64, error) {
	if n, ok := asInt(v); ok {
		return n, nil
	}
	return 0, newErrorf("TypeError", "%s must be an integer, not '%s'", what, typeName(v))
}

func wantStr(what string, v Value) (string, error) {
	if s, ok := v.(string); ok {
		return s, nil
	}
	return "", newErrorf("TypeError", "%s must be str, not %s", what, typeName(v))
}

func strMethod(s string, n string) func(*VM, []Value, []KwArg) (Value, error) {
	simple := func(f func(vm *VM, args []Value) (Value, error)) func(*VM, []Value, []KwArg) (Value, error) {
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(n, kw); err != nil {
				return nil, err
			}
			return f(vm, args)
		}
	}
	switch n {
	case "expandtabs":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			return strExpandTabs(vm, s, args, kw)
		}
	case "translate":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			return strTranslate(vm, s, args, kw)
		}
	case "format":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) { return strFormat(vm, s, args, kw, nil) }
	case "format_map":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount(n, args, 1, 1); err != nil {
				return nil, err
			}
			if err := noKw(n, kw); err != nil {
				return nil, err
			}
			return strFormat(vm, s, nil, nil, args[0])
		}
	case "upper":
		return simple(func(vm *VM, args []Value) (Value, error) {
			return pyUpper(s), nil
		})
	case "lower":
		return simple(func(vm *VM, args []Value) (Value, error) {
			return pyLower(s), nil
		})
	case "title":
		return simple(func(vm *VM, args []Value) (Value, error) {
			return pyTitle(s), nil
		})
	case "capitalize":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if len(s) == 0 {
				return s, nil
			}
			var b strings.Builder
			first, size := pyDecodeRuneInString(s)
			pyWriteRune(&b, unicode.ToTitle(first))
			b.WriteString(pyLower(s[size:]))
			return b.String(), nil
		})
	case "casefold":
		return simple(func(vm *VM, args []Value) (Value, error) {
			var b strings.Builder
			for i := 0; i < len(s); {
				r, size := pyDecodeRuneInString(s[i:])
				i += size
				switch r {
				case 0x00DF, 0x1E9E:
					b.WriteString("ss")
				default:
					pyWriteRune(&b, unicode.ToLower(r))
				}
			}
			return b.String(), nil
		})
	case "strip", "lstrip", "rstrip":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 0, 1); err != nil {
				return nil, err
			}
			inSet := unicode.IsSpace
			if len(args) == 1 {
				if _, isNone := args[0].(NoneType); !isNone {
					chars, err := wantStr(n+" arg", args[0])
					if err != nil {
						return nil, err
					}
					set := map[rune]bool{}
					for i := 0; i < len(chars); {
						r, size := pyDecodeRuneInString(chars[i:])
						i += size
						set[r] = true
					}
					inSet = func(r rune) bool { return set[r] }
				}
			}
			start, end := 0, len(s)
			if n != "rstrip" {
				for start < end {
					r, size := pyDecodeRuneInString(s[start:])
					if !inSet(r) {
						break
					}
					start += size
				}
			}
			if n != "lstrip" {
				last := start
				for i := start; i < end; {
					r, size := pyDecodeRuneInString(s[i:])
					i += size
					if !inSet(r) {
						last = i
					}
				}
				end = last
			}
			return s[start:end], nil
		})
	case "split", "rsplit":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 0, 2); err != nil {
				return nil, err
			}
			values := [2]Value{None, int64(-1)}
			seen := [2]bool{}
			for i, value := range args {
				values[i], seen[i] = value, true
			}
			for _, keyword := range kw {
				index := 0
				switch keyword.Name {
				case "sep":
				case "maxsplit":
					index = 1
				default:
					return nil, newErrorf("TypeError", "'%s' is an invalid keyword argument for %s()", keyword.Name, n)
				}
				if seen[index] {
					return nil, newErrorf("TypeError", "%s() got multiple values for argument '%s'", n, keyword.Name)
				}
				values[index], seen[index] = keyword.Val, true
			}
			index, err := percentIndex(vm, values[1])
			if err != nil {
				return nil, err
			}
			maxsplit, err := wantInt("maxsplit", index)
			if err != nil {
				return nil, err
			}
			var sep string
			hasSep := false
			if _, isNone := values[0].(NoneType); !isNone {
				sep, err = wantStr("sep", rawPrim(values[0]))
				if err != nil {
					return nil, err
				}
				if sep == "" {
					return nil, newError("ValueError", "empty separator")
				}
				hasSep = true
			}
			var parts []string
			if !hasSep {
				parts = splitWhitespace(s, maxsplit, n == "rsplit")
			} else if n == "split" {
				if maxsplit < 0 {
					parts = strings.Split(s, sep)
				} else {
					parts = strings.SplitN(s, sep, int(maxsplit)+1)
				}
			} else {
				parts = strings.Split(s, sep)
				if maxsplit >= 0 && int64(len(parts)-1) > maxsplit {
					keep := len(parts) - int(maxsplit)
					head := strings.Join(parts[:keep], sep)
					parts = append([]string{head}, parts[keep:]...)
				}
			}
			return strListValue(parts), nil
		}
	case "splitlines":
		return simple(func(vm *VM, args []Value) (Value, error) {
			return strListValue(splitLines(s)), nil
		})
	case "join":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			items, err := vm.iterToSlice(args[0])
			if err != nil {
				return nil, err
			}
			size := 0
			for i, x := range items {
				xs, ok := x.(string)
				if !ok {
					return nil, newErrorf("TypeError",
						"sequence item %d: expected str instance, %s found", i, typeName(x))
				}
				size += len(xs)
			}
			if len(items) > 1 {
				size += len(s) * (len(items) - 1)
			}
			var b strings.Builder
			b.Grow(size)
			for i, x := range items {
				if i > 0 {
					b.WriteString(s)
				}
				b.WriteString(x.(string))
			}
			return b.String(), nil
		})
	case "replace":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 2, 3); err != nil {
				return nil, err
			}
			old, err := wantStr("replace old", args[0])
			if err != nil {
				return nil, err
			}
			newS, err := wantStr("replace new", args[1])
			if err != nil {
				return nil, err
			}
			count := -1
			if len(args) == 3 {
				c, err := wantInt("count", args[2])
				if err != nil {
					return nil, err
				}
				count = int(c)
			}
			if old == "" && count != 0 {
				var b strings.Builder
				offset, inserted := 0, 0
				for count < 0 || inserted < count {
					b.WriteString(newS)
					inserted++
					if offset == len(s) {
						break
					}
					_, size := pyDecodeRuneInString(s[offset:])
					b.WriteString(s[offset : offset+size])
					offset += size
				}
				b.WriteString(s[offset:])
				return b.String(), nil
			}
			return strings.Replace(s, old, newS, count), nil
		})
	case "find", "rfind", "index", "count":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 1, 3); err != nil {
				return nil, err
			}
			sub, err := wantStr("sub", args[0])
			if err != nil {
				return nil, err
			}
			rs := pyRunes(s)
			start, end, err := strRegion(rs, args[1:])
			if err != nil {
				return nil, err
			}
			if n == "count" {
				return int64(countRunes(rs[start:end], pyRunes(sub))), nil
			}
			idx := findRunes(rs, pyRunes(sub), start, end, n == "rfind")
			if n == "index" && idx < 0 {
				return nil, newError("ValueError", "substring not found")
			}
			return int64(idx), nil
		})
	case "startswith", "endswith":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 1, 3); err != nil {
				return nil, err
			}
			rs := pyRunes(s)
			start, end, err := strRegion(rs, args[1:])
			if err != nil {
				return nil, err
			}
			region := pyString(rs[start:end])
			var pats []Value
			if tup, ok := args[0].(*Tuple); ok {
				pats = tup.Items
			} else {
				pats = []Value{args[0]}
			}
			for _, p := range pats {
				ps, ok := p.(string)
				if !ok {
					return nil, newErrorf("TypeError",
						"%s first arg must be str or a tuple of str, not %s", n, typeName(p))
				}
				if n == "startswith" && strings.HasPrefix(region, ps) {
					return true, nil
				}
				if n == "endswith" && strings.HasSuffix(region, ps) {
					return true, nil
				}
			}
			return false, nil
		})
	case "isascii":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 0, 0); err != nil {
				return nil, err
			}
			for i := range len(s) {
				if s[i] >= utf8.RuneSelf {
					return false, nil
				}
			}
			return true, nil
		})
	case "isprintable":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 0, 0); err != nil {
				return nil, err
			}
			for offset := 0; offset < len(s); {
				r, size := pyDecodeRuneInString(s[offset:])
				if !unicode.IsPrint(r) {
					return false, nil
				}
				offset += size
			}
			return true, nil
		})
	case "isidentifier":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 0, 0); err != nil {
				return nil, err
			}
			return pyIsIdentifier(s), nil
		})
	case "isdecimal", "isdigit", "isalpha", "isalnum", "isspace":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 0, 0); err != nil {
				return nil, err
			}
			if s == "" {
				return false, nil
			}
			var pred func(rune) bool
			switch n {
			case "isdecimal", "isdigit":
				pred = unicode.IsDigit
			case "isalpha":
				pred = unicode.IsLetter
			case "isalnum":
				pred = func(r rune) bool {
					return unicode.IsLetter(r) || unicode.IsDigit(r) || unicode.IsNumber(r)
				}
			case "isspace":
				pred = unicode.IsSpace
			}
			for i := 0; i < len(s); {
				r, size := pyDecodeRuneInString(s[i:])
				i += size
				if !pred(r) {
					return false, nil
				}
			}
			return true, nil
		})
	case "zfill":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			w, err := wantInt("width", args[0])
			if err != nil {
				return nil, err
			}
			rs := pyRunes(s)
			if int64(len(rs)) >= w {
				return s, nil
			}
			sign := ""
			body := s
			if len(rs) > 0 && (rs[0] == '+' || rs[0] == '-') {
				sign = pyRuneString(rs[0])
				body = pyString(rs[1:])
			}
			return sign + strings.Repeat("0", int(w)-len(rs)) + body, nil
		})
	case "ljust", "rjust":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 1, 2); err != nil {
				return nil, err
			}
			w, err := wantInt("width", args[0])
			if err != nil {
				return nil, err
			}
			fill := " "
			if len(args) == 2 {
				f, err := wantStr("fillchar", args[1])
				if err != nil {
					return nil, err
				}
				if pyRuneCount(f) != 1 {
					return nil, newError("TypeError",
						"The fill character must be exactly one character long")
				}
				fill = f
			}
			pad := int(w) - pyRuneCount(s)
			if pad <= 0 {
				return s, nil
			}
			if n == "ljust" {
				return s + strings.Repeat(fill, pad), nil
			}
			return strings.Repeat(fill, pad) + s, nil
		})
	case "encode":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			encoding, errors, err := codecArguments(n, args, kw)
			if err != nil {
				return nil, err
			}
			return vm.encodeText(s, encoding, errors)
		}
	case "partition", "rpartition":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			sep, err := wantStr("sep", args[0])
			if err != nil {
				return nil, err
			}
			if sep == "" {
				return nil, newError("ValueError", "empty separator")
			}
			var i int
			if n == "partition" {
				i = strings.Index(s, sep)
				if i < 0 {
					return &Tuple{Items: []Value{s, "", ""}}, nil
				}
			} else {
				i = strings.LastIndex(s, sep)
				if i < 0 {
					return &Tuple{Items: []Value{"", "", s}}, nil
				}
			}
			return &Tuple{Items: []Value{s[:i], sep, s[i+len(sep):]}}, nil
		})
	case "removeprefix":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			p, err := wantStr("prefix", args[0])
			if err != nil {
				return nil, err
			}
			return strings.TrimPrefix(s, p), nil
		})
	case "removesuffix":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			p, err := wantStr("suffix", args[0])
			if err != nil {
				return nil, err
			}
			if p != "" {
				return strings.TrimSuffix(s, p), nil
			}
			return s, nil
		})
	case "center":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 1, 2); err != nil {
				return nil, err
			}
			w, err := wantInt("width", args[0])
			if err != nil {
				return nil, err
			}
			fill := " "
			if len(args) == 2 {
				f, err := wantStr("fillchar", args[1])
				if err != nil {
					return nil, err
				}
				if pyRuneCount(f) != 1 {
					return nil, newError("TypeError",
						"The fill character must be exactly one character long")
				}
				fill = f
			}
			width := int(w)
			marg := width - pyRuneCount(s)
			if marg <= 0 {
				return s, nil
			}
			left := marg/2 + (marg & width & 1)
			return strings.Repeat(fill, left) + s + strings.Repeat(fill, marg-left), nil
		})
	case "islower", "isupper":
		return simple(func(vm *VM, args []Value) (Value, error) {
			hasCased := false
			for i := 0; i < len(s); {
				r, size := pyDecodeRuneInString(s[i:])
				i += size
				if unicode.IsLower(r) || unicode.IsUpper(r) || unicode.IsTitle(r) {
					hasCased = true
					if n == "islower" && !unicode.IsLower(r) {
						return false, nil
					}
					if n == "isupper" && !unicode.IsUpper(r) {
						return false, nil
					}
				}
			}
			return hasCased, nil
		})
	case "swapcase":
		return simple(func(vm *VM, args []Value) (Value, error) {
			var b strings.Builder
			for i := 0; i < len(s); {
				r, size := pyDecodeRuneInString(s[i:])
				i += size
				switch {
				case unicode.IsUpper(r) || unicode.IsTitle(r):
					pyWriteRune(&b, unicode.ToLower(r))
				case unicode.IsLower(r):
					pyWriteRune(&b, unicode.ToUpper(r))
				default:
					pyWriteRune(&b, r)
				}
			}
			return b.String(), nil
		})
	}
	return nil
}

func encodeStr(s, enc string) (Value, error) {
	encoding := strings.ToLower(strings.ReplaceAll(enc, "_", "-"))
	limit, reason := rune(0x10ffff), "surrogates not allowed"
	switch encoding {
	case "utf-8", "utf8":
		encoding = "utf-8"
	case "ascii", "us-ascii":
		encoding, limit, reason = "ascii", 0x7f, "ordinal not in range(128)"
	case "latin-1", "latin1", "iso-8859-1":
		encoding, limit, reason = "latin-1", 0xff, "ordinal not in range(256)"
	default:
		return nil, newErrorf("LookupError", "unknown encoding: %s", enc)
	}
	position, start, end := int64(0), int64(-1), int64(0)
	for offset := 0; offset < len(s); position++ {
		r, size := pyDecodeRuneInString(s[offset:])
		offset += size
		invalid := r > limit
		if encoding == "utf-8" {
			invalid = r >= 0xd800 && r <= 0xdfff
		}
		if invalid {
			if start < 0 {
				start = position
			}
			end = position + 1
		} else if start >= 0 {
			break
		}
	}
	if start >= 0 {
		return nil, newUnicodeError("UnicodeEncodeError", encoding, s, start, end, reason)
	}
	if encoding != "latin-1" {
		return []byte(s), nil
	}
	out := make([]byte, 0, len(s))
	for offset := 0; offset < len(s); {
		r, size := pyDecodeRuneInString(s[offset:])
		offset += size
		out = append(out, byte(r))
	}
	return out, nil
}

// upperExpansions holds the supported one-to-many uppercase
// mappings from Unicode SpecialCasing (Go's strings.ToUpper only performs
// one-to-one mappings).
var upperExpansions = map[rune]string{
	0x00DF: "SS",
	0xFB00: "FF",
	0xFB01: "FI",
	0xFB02: "FL",
	0xFB03: "FFI",
	0xFB04: "FFL",
	0xFB05: "ST",
	0xFB06: "ST",
	0x0149: "ʼN",
	0x1E9A: "Aʾ",
}

func pyLower(s string) string {
	var b strings.Builder
	b.Grow(len(s))
	for i := 0; i < len(s); {
		r, size := pyDecodeRuneInString(s[i:])
		i += size
		pyWriteRune(&b, unicode.ToLower(r))
	}
	return b.String()
}

func pyUpper(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); {
		r, size := pyDecodeRuneInString(s[i:])
		i += size
		if exp, ok := upperExpansions[r]; ok {
			b.WriteString(exp)
		} else {
			pyWriteRune(&b, unicode.ToUpper(r))
		}
	}
	return b.String()
}

func pyTitle(s string) string {
	var b strings.Builder
	prevCased := false
	for i := 0; i < len(s); {
		r, size := pyDecodeRuneInString(s[i:])
		i += size
		cased := unicode.IsLower(r) || unicode.IsUpper(r) || unicode.IsTitle(r)
		if cased {
			if prevCased {
				pyWriteRune(&b, unicode.ToLower(r))
			} else {
				pyWriteRune(&b, unicode.ToTitle(r))
			}
		} else {
			pyWriteRune(&b, r)
		}
		prevCased = cased
	}
	return b.String()
}

func splitWhitespace(s string, maxsplit int64, fromRight bool) []string {
	fields := strings.FieldsFunc(s, unicode.IsSpace)
	if maxsplit < 0 || int64(len(fields)-1) <= maxsplit {
		return fields
	}
	// Rare path: bounded whitespace split. Recompute against the original
	// string so the remainder keeps its inner whitespace.
	var parts []string
	if !fromRight {
		rest := strings.TrimLeftFunc(s, unicode.IsSpace)
		for int64(len(parts)) < maxsplit && rest != "" {
			i := strings.IndexFunc(rest, unicode.IsSpace)
			if i < 0 {
				break
			}
			parts = append(parts, rest[:i])
			rest = strings.TrimLeftFunc(rest[i:], unicode.IsSpace)
		}
		if rest != "" {
			parts = append(parts, rest)
		}
		return parts
	}
	rest := strings.TrimRightFunc(s, unicode.IsSpace)
	var tail []string
	for int64(len(tail)) < maxsplit && rest != "" {
		i := strings.LastIndexFunc(rest, unicode.IsSpace)
		if i < 0 {
			break
		}
		_, size := utf8.DecodeRuneInString(rest[i:])
		tail = append([]string{rest[i+size:]}, tail...)
		rest = strings.TrimRightFunc(rest[:i], unicode.IsSpace)
	}
	if rest != "" {
		tail = append([]string{rest}, tail...)
	}
	return tail
}

func splitLines(s string) []string {
	var out []string
	var cur strings.Builder
	rs := pyRunes(s)
	for i := 0; i < len(rs); i++ {
		r := rs[i]
		switch r {
		case '\n', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029:
			out = append(out, cur.String())
			cur.Reset()
		case '\r':
			if i+1 < len(rs) && rs[i+1] == '\n' {
				i++
			}
			out = append(out, cur.String())
			cur.Reset()
		default:
			pyWriteRune(&cur, r)
		}
	}
	if cur.Len() > 0 {
		out = append(out, cur.String())
	}
	return out
}

func strListValue(parts []string) *List {
	items := make([]Value, len(parts))
	for i, p := range parts {
		items[i] = p
	}
	return &List{Items: items}
}

// strRegion resolves optional start/end arguments against a rune slice.
func strRegion(rs []rune, bounds []Value) (int, int, error) {
	start, end := 0, len(rs)
	adjust := func(v Value, def int) (int, error) {
		if _, isNone := v.(NoneType); isNone {
			return def, nil
		}
		n, ok := asInt(v)
		if !ok {
			return 0, newErrorf("TypeError",
				"slice indices must be integers or None or have an __index__ method")
		}
		i := int(n)
		if i < 0 {
			i += len(rs)
			if i < 0 {
				i = 0
			}
		} else if i > len(rs) {
			i = len(rs)
		}
		return i, nil
	}
	var err error
	if len(bounds) >= 1 {
		start, err = adjust(bounds[0], 0)
		if err != nil {
			return 0, 0, err
		}
	}
	if len(bounds) >= 2 {
		end, err = adjust(bounds[1], len(rs))
		if err != nil {
			return 0, 0, err
		}
	}
	if end < start {
		end = start
	}
	return start, end, nil
}

func findRunes(rs, sub []rune, start, end int, last bool) int {
	if len(sub) == 0 {
		if last {
			return end
		}
		return start
	}
	if last {
		for i := end - len(sub); i >= start; i-- {
			if runesMatch(rs, sub, i) {
				return i
			}
		}
		return -1
	}
	for i := start; i+len(sub) <= end; i++ {
		if runesMatch(rs, sub, i) {
			return i
		}
	}
	return -1
}

func runesMatch(rs, sub []rune, at int) bool {
	if at < 0 || at+len(sub) > len(rs) {
		return false
	}
	for j, r := range sub {
		if rs[at+j] != r {
			return false
		}
	}
	return true
}

func countRunes(rs, sub []rune) int {
	if len(sub) == 0 {
		return len(rs) + 1
	}
	count := 0
	for i := 0; i+len(sub) <= len(rs); {
		if runesMatch(rs, sub, i) {
			count++
			i += len(sub)
		} else {
			i++
		}
	}
	return count
}

func listMethod(l *List, n string) func(*VM, []Value, []KwArg) (Value, error) {
	switch n {
	case "__reversed__":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 0, 0); err != nil {
				return nil, err
			}
			if err := noKw(n, kw); err != nil {
				return nil, err
			}
			return reversedValue(vm, l)
		}
	case "append":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			l.Items = append(l.Items, args[0])
			return None, nil
		}
	case "extend":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			items, err := vm.iterToSlice(args[0])
			if err != nil {
				return nil, err
			}
			l.Items = append(l.Items, items...)
			return None, nil
		}
	case "insert":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 2, 2); err != nil {
				return nil, err
			}
			idx, err := wantInt("index", args[0])
			if err != nil {
				return nil, err
			}
			i := int(idx)
			if i < 0 {
				i += len(l.Items)
				if i < 0 {
					i = 0
				}
			} else if i > len(l.Items) {
				i = len(l.Items)
			}
			l.Items = append(l.Items, nil)
			copy(l.Items[i+1:], l.Items[i:])
			l.Items[i] = args[1]
			return None, nil
		}
	case "pop":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 0, 1); err != nil {
				return nil, err
			}
			if len(l.Items) == 0 {
				return nil, newError("IndexError", "pop from empty list")
			}
			i := len(l.Items) - 1
			if len(args) == 1 {
				idx, err := wantInt("index", args[0])
				if err != nil {
					return nil, err
				}
				i = int(idx)
				if i < 0 {
					i += len(l.Items)
				}
				if i < 0 || i >= len(l.Items) {
					return nil, newError("IndexError", "pop index out of range")
				}
			}
			v := l.Items[i]
			l.Items = append(l.Items[:i], l.Items[i+1:]...)
			return v, nil
		}
	case "remove":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			for i, x := range l.Items {
				same, err := vm.eqElem(x, args[0])
				if err != nil {
					return nil, err
				}
				if same {
					l.Items = append(l.Items[:i], l.Items[i+1:]...)
					return None, nil
				}
			}
			return nil, newError("ValueError", "list.remove(x): x not in list")
		}
	case "clear":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			l.Items = nil
			return None, nil
		}
	case "index":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 3); err != nil {
				return nil, err
			}
			i, err := seqIndexOf(vm, l.Items, args)
			if err != nil {
				return nil, err
			}
			if i < 0 {
				r, rerr := vm.repr(args[0])
				if rerr != nil {
					return nil, rerr
				}
				return nil, newErrorf("ValueError", "%s is not in list", r)
			}
			return int64(i), nil
		}
	case "count":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			count := int64(0)
			for _, x := range l.Items {
				same, err := vm.eqElem(x, args[0])
				if err != nil {
					return nil, err
				}
				if same {
					count++
				}
			}
			return count, nil
		}
	case "reverse":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			reverseSlice(l.Items)
			return None, nil
		}
	case "sort":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if len(args) > 0 {
				return nil, newError("TypeError", "sort() takes no positional arguments")
			}
			key, reverse, err := keyReverseKw("sort", vm, kw)
			if err != nil {
				return nil, err
			}
			if err := vm.sortValues(l.Items, key, reverse); err != nil {
				return nil, err
			}
			return None, nil
		}
	case "copy":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			return &List{Items: append([]Value{}, l.Items...)}, nil
		}
	}
	return nil
}

func keyReverseKw(name string, vm *VM, kw []KwArg) (key Value, reverse bool, err error) {
	for _, k := range kw {
		switch k.Name {
		case "key":
			key = k.Val
		case "reverse":
			b, terr := vm.truth(k.Val)
			if terr != nil {
				return nil, false, terr
			}
			reverse = b
		default:
			return nil, false, newErrorf("TypeError",
				"'%s' is an invalid keyword argument for %s()", k.Name, name)
		}
	}
	if _, isNone := key.(NoneType); isNone {
		key = nil
	}
	return key, reverse, nil
}

func seqIndexOf(vm *VM, items []Value, args []Value) (int, error) {
	start, end := 0, len(items)
	if len(args) >= 2 {
		n, err := wantInt("start", args[1])
		if err != nil {
			return 0, err
		}
		start = int(n)
		if start < 0 {
			start += len(items)
			if start < 0 {
				start = 0
			}
		}
	}
	if len(args) >= 3 {
		n, err := wantInt("stop", args[2])
		if err != nil {
			return 0, err
		}
		end = int(n)
		if end < 0 {
			end += len(items)
		}
		if end > len(items) {
			end = len(items)
		}
	}
	for i := start; i < end && i < len(items); i++ {
		same, err := vm.eqElem(items[i], args[0])
		if err != nil {
			return 0, err
		}
		if same {
			return i, nil
		}
	}
	return -1, nil
}

func dictMethod(d *Dict, n string) func(*VM, []Value, []KwArg) (Value, error) {
	switch n {
	case "get":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 2); err != nil {
				return nil, err
			}
			v, ok, err := d.Get(args[0])
			if err != nil {
				return nil, err
			}
			if ok {
				return v, nil
			}
			if len(args) == 2 {
				return args[1], nil
			}
			return None, nil
		}
	case "keys", "values", "items":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount(n, args, 0, 0); err != nil {
				return nil, err
			}
			if err := noKw(n, kw); err != nil {
				return nil, err
			}
			return &DictView{dict: d, kind: n}, nil
		}
	case "pop":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 2); err != nil {
				return nil, err
			}
			v, ok, err := d.Delete(args[0])
			if err != nil {
				return nil, err
			}
			if ok {
				return v, nil
			}
			if len(args) == 2 {
				return args[1], nil
			}
			return nil, newErrorArgs("KeyError", args[0])
		}
	case "update":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 0, 1); err != nil {
				return nil, err
			}
			if len(args) == 1 {
				if err := dictUpdateFrom(vm, d, args[0]); err != nil {
					return nil, err
				}
			}
			for _, k := range kw {
				if err := d.Set(k.Name, k.Val); err != nil {
					return nil, err
				}
			}
			return None, nil
		}
	case "setdefault":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 2); err != nil {
				return nil, err
			}
			v, ok, err := d.Get(args[0])
			if err != nil {
				return nil, err
			}
			if ok {
				return v, nil
			}
			var dv Value = None
			if len(args) == 2 {
				dv = args[1]
			}
			if err := d.Set(args[0], dv); err != nil {
				return nil, err
			}
			return dv, nil
		}
	case "clear":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			d.Clear()
			return None, nil
		}
	case "copy":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			return d.Copy(), nil
		}
	}
	return nil
}

func dictUpdateFrom(vm *VM, d *Dict, src Value) error {
	return dictMergeFrom(vm, d, src)
}

func setMethod(s *Set, n string) func(*VM, []Value, []KwArg) (Value, error) {
	switch n {
	case "update":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(n, kw); err != nil {
				return nil, err
			}
			for _, source := range args {
				if err := vm.setUpdate(s, source); err != nil {
					return nil, err
				}
			}
			return None, nil
		}
	case "difference_update":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(n, kw); err != nil {
				return nil, err
			}
			for _, source := range args {
				if err := vm.setDifferenceUpdate(s, source); err != nil {
					return nil, err
				}
			}
			return None, nil
		}
	case "pop":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noArgs("pop", args, kw); err != nil {
				return nil, err
			}
			if len(s.entries) == 0 {
				return nil, newErrorArgs("KeyError", "pop from an empty set")
			}
			// Any member may be removed. Taking the last stored entry avoids
			// shifting entries and never calls guest __hash__ or __eq__ again.
			last := len(s.entries) - 1
			entry := s.entries[last]
			s.entries[last] = dictEntry{}
			s.entries = s.entries[:last]
			delete(s.index, entry.hk)
			if len(entry.hk) != 0 && entry.hk[0] == 'h' {
				s.slowKeys--
			}
			return entry.key, nil
		}
	case "add":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			if err := s.Add(args[0]); err != nil {
				return nil, err
			}
			return None, nil
		}
	case "remove":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			ok, err := s.Discard(args[0])
			if err != nil {
				return nil, err
			}
			if !ok {
				return nil, newErrorArgs("KeyError", args[0])
			}
			return None, nil
		}
	case "discard":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			if _, err := s.Discard(args[0]); err != nil {
				return nil, err
			}
			return None, nil
		}
	case "clear":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			s.Clear()
			return None, nil
		}
	case "copy":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			return s.Copy(), nil
		}
	case "union", "intersection", "difference", "symmetric_difference":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			out := s.Copy()
			for _, a := range args {
				other, err := vm.toSet(a)
				if err != nil {
					return nil, err
				}
				var op string
				switch n {
				case "union":
					op = "bitor"
				case "intersection":
					op = "bitand"
				case "difference":
					op = "sub"
				case "symmetric_difference":
					op = "bitxor"
				}
				res, err := setOp(op, out, other)
				if err != nil {
					return nil, err
				}
				out = res.(*Set)
			}
			return out, nil
		}
	case "issubset":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			other, err := vm.toSet(args[0])
			if err != nil {
				return nil, err
			}
			return setIsSubset(s, other)
		}
	case "issuperset":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			other, err := vm.toSet(args[0])
			if err != nil {
				return nil, err
			}
			return setIsSubset(other, s)
		}
	case "isdisjoint":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			it, err := vm.getIter(args[0])
			if err != nil {
				return nil, err
			}
			for {
				value, more, err := it.Next()
				if err != nil || !more {
					return !more, err
				}
				found, err := s.Has(value)
				if err != nil || found {
					return false, err
				}
			}
		}
	}
	return nil
}

func (vm *VM) toSet(v Value) (*Set, error) {
	if s, ok := setValue(v); ok {
		return s, nil
	}
	items, err := vm.iterToSlice(v)
	if err != nil {
		return nil, err
	}
	out := NewSet()
	for _, x := range items {
		if err := out.Add(x); err != nil {
			return nil, err
		}
	}
	return out, nil
}

func tupleMethod(t *Tuple, n string) func(*VM, []Value, []KwArg) (Value, error) {
	switch n {
	case "count":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			count := int64(0)
			for _, x := range t.Items {
				same, err := vm.eqElem(x, args[0])
				if err != nil {
					return nil, err
				}
				if same {
					count++
				}
			}
			return count, nil
		}
	case "index":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 3); err != nil {
				return nil, err
			}
			i, err := seqIndexOf(vm, t.Items, args)
			if err != nil {
				return nil, err
			}
			if i < 0 {
				return nil, newError("ValueError", "tuple.index(x): x not in tuple")
			}
			return int64(i), nil
		}
	}
	return nil
}

func bytesMethod(b []byte, n string) func(*VM, []Value, []KwArg) (Value, error) {
	switch n {
	case "count", "rfind":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) { return bytesSearch(vm, b, n, args, kw) }
	case "isascii":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noKw(n, kw); err != nil {
				return nil, err
			}
			if err := argCount(n, args, 0, 0); err != nil {
				return nil, err
			}
			for _, value := range b {
				if value >= 128 {
					return false, nil
				}
			}
			return true, nil
		}
	case "ljust", "rjust", "center":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) { return bytesPad(vm, b, n, args, kw) }
	case "hex":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) { return bufferHex(vm, b, args, kw) }
	case "join":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) { return bytesJoin(vm, b, args, kw) }
	case "translate":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) { return bytesTranslate(vm, b, args, kw) }
	case "decode":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			encoding, errors, err := codecArguments(n, args, kw)
			if err != nil {
				return nil, err
			}
			return vm.decodeText(b, encoding, errors)
		}
	case "find":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			sub, ok := args[0].([]byte)
			if !ok {
				return nil, newErrorf("TypeError",
					"argument should be a bytes-like object, not '%s'", typeName(args[0]))
			}
			return int64(bytes.Index(b, sub)), nil
		}
	case "split":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 0, 2); err != nil {
				return nil, err
			}
			var parts [][]byte
			if len(args) == 0 {
				parts = bytes.Fields(b)
			} else {
				if _, isNone := args[0].(NoneType); isNone {
					parts = bytes.Fields(b)
				} else {
					sep, ok := args[0].([]byte)
					if !ok {
						return nil, newErrorf("TypeError",
							"a bytes-like object is required, not '%s'", typeName(args[0]))
					}
					if len(sep) == 0 {
						return nil, newError("ValueError", "empty separator")
					}
					parts = bytes.Split(b, sep)
				}
			}
			items := make([]Value, len(parts))
			for i, p := range parts {
				items[i] = append([]byte{}, p...)
			}
			return &List{Items: items}, nil
		}
	case "strip", "lstrip", "rstrip":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 0, 1); err != nil {
				return nil, err
			}
			var set [256]bool
			if len(args) == 0 {
				for _, c := range []byte(" \t\n\r\v\f") {
					set[c] = true
				}
			} else {
				cut, ok := args[0].([]byte)
				if !ok {
					return nil, newErrorf("TypeError",
						"a bytes-like object is required, not '%s'", typeName(args[0]))
				}
				for _, c := range cut {
					set[c] = true
				}
			}
			lo, hi := 0, len(b)
			if n != "rstrip" {
				for lo < hi && set[b[lo]] {
					lo++
				}
			}
			if n != "lstrip" {
				for hi > lo && set[b[hi-1]] {
					hi--
				}
			}
			return append([]byte{}, b[lo:hi]...), nil
		}
	case "replace":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 2, 3); err != nil {
				return nil, err
			}
			old, ok := args[0].([]byte)
			if !ok {
				return nil, newErrorf("TypeError",
					"a bytes-like object is required, not '%s'", typeName(args[0]))
			}
			newB, ok := args[1].([]byte)
			if !ok {
				return nil, newErrorf("TypeError",
					"a bytes-like object is required, not '%s'", typeName(args[1]))
			}
			count := -1
			if len(args) == 3 {
				c, err := wantInt("count", args[2])
				if err != nil {
					return nil, err
				}
				count = int(c)
			}
			return bytes.Replace(b, old, newB, count), nil
		}
	case "startswith", "endswith":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 1, 1); err != nil {
				return nil, err
			}
			var pats []Value
			if tup, ok := args[0].(*Tuple); ok {
				pats = tup.Items
			} else {
				pats = []Value{args[0]}
			}
			for _, p := range pats {
				pb, ok := p.([]byte)
				if !ok {
					return nil, newErrorf("TypeError",
						"%s first arg must be bytes or a tuple of bytes, not %s",
						n, typeName(p))
				}
				if n == "startswith" && bytes.HasPrefix(b, pb) {
					return true, nil
				}
				if n == "endswith" && bytes.HasSuffix(b, pb) {
					return true, nil
				}
			}
			return false, nil
		}
	case "upper", "lower":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			out := make([]byte, len(b))
			for i, c := range b {
				if n == "upper" && c >= 'a' && c <= 'z' {
					c -= 32
				} else if n == "lower" && c >= 'A' && c <= 'Z' {
					c += 32
				}
				out[i] = c
			}
			return out, nil
		}
	}
	return nil
}

func decodeBytes(b []byte, enc string) (Value, error) {
	switch strings.ToLower(strings.ReplaceAll(enc, "_", "-")) {
	case "utf-8", "utf8":
		for i := 0; i < len(b); {
			r, size := utf8.DecodeRune(b[i:])
			if r != utf8.RuneError || size != 1 {
				i += size
				continue
			}
			end, reason := utf8ErrorSpan(b, i)
			return nil, newUnicodeError("UnicodeDecodeError", "utf-8", b, int64(i), int64(end), reason)
		}
		return string(b), nil
	case "ascii", "us-ascii", "us_ascii":
		for i, c := range b {
			if c > 127 {
				return nil, newUnicodeError("UnicodeDecodeError", "ascii", b, int64(i), int64(i+1), "ordinal not in range(128)")
			}
		}
		return string(b), nil
	case "latin-1", "latin1", "iso-8859-1":
		rs := make([]rune, len(b))
		for i, c := range b {
			rs[i] = rune(c)
		}
		return pyString(rs), nil
	}
	return nil, newErrorf("LookupError", "unknown encoding: %s", enc)
}

func utf8ErrorSpan(b []byte, start int) (int, string) {
	width := 0
	switch {
	case b[start] >= 0xc2 && b[start] <= 0xdf:
		width = 2
	case b[start] >= 0xe0 && b[start] <= 0xef:
		width = 3
	case b[start] >= 0xf0 && b[start] <= 0xf4:
		width = 4
	}
	if width == 0 {
		return start + 1, "invalid start byte"
	}
	end := start + 1
	for j := 1; j < width && start+j < len(b); j++ {
		c := b[start+j]
		valid := c >= 0x80 && c <= 0xbf
		if j == 1 {
			switch b[start] {
			case 0xe0:
				valid = c >= 0xa0 && c <= 0xbf
			case 0xed:
				valid = c >= 0x80 && c <= 0x9f
			case 0xf0:
				valid = c >= 0x90 && c <= 0xbf
			case 0xf4:
				valid = c >= 0x80 && c <= 0x8f
			}
		}
		if !valid {
			return end, "invalid continuation byte"
		}
		end++
	}
	return end, "unexpected end of data"
}

func codecArguments(name string, args []Value, kw []KwArg) (string, string, error) {
	if err := needArgs(name, args, 0, 2); err != nil {
		return "", "", err
	}
	values := [2]string{"utf-8", "strict"}
	seen := [2]bool{}
	for i, value := range args {
		text, err := wantStr("argument", value)
		if err != nil {
			return "", "", err
		}
		values[i], seen[i] = text, true
	}
	for _, k := range kw {
		i := 0
		switch k.Name {
		case "encoding":
		case "errors":
			i = 1
		default:
			return "", "", newErrorf("TypeError", "'%s' is an invalid keyword argument for %s()", k.Name, name)
		}
		if seen[i] {
			return "", "", newErrorf("TypeError", "%s() got multiple values for argument '%s'", name, k.Name)
		}
		text, err := wantStr(k.Name, k.Val)
		if err != nil {
			return "", "", err
		}
		values[i], seen[i] = text, true
	}
	return values[0], values[1], nil
}

func nativeTextEncoding(name string) string {
	name = strings.ToLower(strings.ReplaceAll(strings.ReplaceAll(name, "_", "-"), " ", "-"))
	switch name {
	case "utf-8", "utf8", "utf", "u8", "utf8-ucs2", "utf8-ucs4", "cp65001":
		return "utf-8"
	case "ascii", "us-ascii", "646", "ansi-x3.4-1968", "ansi-x3.4-1986", "ansi-x3-4-1968", "cp367", "csascii", "ibm367", "iso646-us", "iso-646.irv-1991", "iso-ir-6", "us":
		return "ascii"
	case "latin-1", "latin1", "latin", "l1", "iso-8859-1", "iso8859-1", "iso-8859-1:1987", "iso-ir-100", "cp819", "ibm819", "csisolatin1":
		return "latin-1"
	}
	return ""
}

func (vm *VM) codecDispatch(operation string, value Value, encoding, errors string) (Value, error) {
	if vm == nil || !vm.hasModule("codecs") {
		if nativeTextEncoding(encoding) != "" {
			return nil, newErrorf("LookupError", "unknown error handler name '%s'; import codecs to register or bundle the handler", errors)
		}
		name := strings.ReplaceAll(strings.ReplaceAll(strings.ToLower(encoding), "-", "_"), " ", "_")
		return nil, newErrorf("LookupError", "unknown encoding: %s; import encodings.%s to bundle the codec", encoding, name)
	}
	module, err := vm.importModule("codecs")
	if err != nil {
		return nil, err
	}
	fn, err := vm.getAttr(module, operation)
	if err != nil {
		return nil, err
	}
	result, err := vm.call(fn, []Value{value, encoding, errors}, nil)
	if err != nil {
		if raised, ok := err.(*Raised); ok {
			if inst, ok := raised.Exc.(*Instance); ok && inst.Class.Name == "LookupError" && strings.HasPrefix(plainExcStr(inst), "unknown encoding:") {
				name := strings.ReplaceAll(strings.ReplaceAll(strings.ToLower(encoding), "-", "_"), " ", "_")
				return nil, newErrorf("LookupError", "unknown encoding: %s; import encodings.%s to bundle the codec", encoding, name)
			}
		}
		return nil, err
	}
	if operation == "encode" {
		if _, ok := result.([]byte); !ok {
			return nil, newErrorf("TypeError", "'%s' encoder returned '%s' instead of 'bytes'", encoding, typeName(result))
		}
	} else if _, ok := result.(string); !ok {
		return nil, newErrorf("TypeError", "'%s' decoder returned '%s' instead of 'str'", encoding, typeName(result))
	}
	return result, nil
}

func (vm *VM) encodeText(s, encoding, errors string) (Value, error) {
	enc := nativeTextEncoding(encoding)
	if enc == "" {
		return vm.codecDispatch("encode", s, encoding, errors)
	}
	switch errors {
	case "strict":
		return encodeStr(s, enc)
	case "ignore", "replace", "backslashreplace", "surrogateescape", "surrogatepass":
	default:
		result, err := encodeStr(s, enc)
		if err == nil {
			return result, nil
		}
		return vm.codecDispatch("encode", s, encoding, errors)
	}
	limit, reason := rune(0x10ffff), "surrogates not allowed"
	if enc == "ascii" {
		limit, reason = 127, "ordinal not in range(128)"
	}
	if enc == "latin-1" {
		limit, reason = 255, "ordinal not in range(256)"
	}
	invalid := func(r rune) bool {
		if enc == "utf-8" {
			return r >= 0xd800 && r <= 0xdfff
		}
		return r > limit
	}
	out := make([]byte, 0, len(s))
	position := int64(0)
	for offset := 0; offset < len(s); {
		r, size := pyDecodeRuneInString(s[offset:])
		if !invalid(r) {
			if enc == "utf-8" {
				out = append(out, s[offset:offset+size]...)
			} else {
				out = append(out, byte(r))
			}
			offset += size
			position++
			continue
		}
		start, end := offset, position
		escapable := true
		for offset < len(s) {
			r, size = pyDecodeRuneInString(s[offset:])
			if !invalid(r) {
				break
			}
			if r < 0xdc80 || r > 0xdcff {
				escapable = false
			}
			offset += size
			end++
		}
		if errors == "surrogateescape" && !escapable || errors == "surrogatepass" && enc != "utf-8" {
			return nil, newUnicodeError("UnicodeEncodeError", enc, s, position, end, reason)
		}
		for i := start; i < offset; {
			r, size = pyDecodeRuneInString(s[i:])
			i += size
			switch errors {
			case "replace":
				out = append(out, '?')
			case "backslashreplace":
				switch {
				case r <= 0xff:
					out = fmt.Appendf(out, `\x%02x`, r)
				case r <= 0xffff:
					out = fmt.Appendf(out, `\u%04x`, r)
				default:
					out = fmt.Appendf(out, `\U%08x`, r)
				}
			case "surrogateescape":
				out = append(out, byte(r-0xdc00))
			case "surrogatepass":
				out = append(out, s[i-size:i]...)
			}
		}
		position = end
	}
	return out, nil
}

func (vm *VM) decodeText(b []byte, encoding, errors string) (Value, error) {
	enc := nativeTextEncoding(encoding)
	if enc == "" {
		return vm.codecDispatch("decode", b, encoding, errors)
	}
	if enc == "latin-1" || errors == "strict" {
		return decodeBytes(b, enc)
	}
	switch errors {
	case "ignore", "replace", "backslashreplace", "surrogateescape", "surrogatepass":
	default:
		result, err := decodeBytes(b, enc)
		if err == nil {
			return result, nil
		}
		return vm.codecDispatch("decode", b, encoding, errors)
	}
	var out strings.Builder
	out.Grow(len(b))
	for i := 0; i < len(b); {
		if b[i] < 128 {
			out.WriteByte(b[i])
			i++
			continue
		}
		if enc == "utf-8" {
			r, size := utf8.DecodeRune(b[i:])
			if r != utf8.RuneError || size != 1 {
				out.Write(b[i : i+size])
				i += size
				continue
			}
			if errors == "surrogatepass" && i+3 <= len(b) && b[i] == 0xed && b[i+1] >= 0xa0 && b[i+1] <= 0xbf && b[i+2]&0xc0 == 0x80 {
				out.Write(b[i : i+3])
				i += 3
				continue
			}
		}
		end, reason := i+1, "ordinal not in range(128)"
		if enc == "utf-8" {
			end, reason = utf8ErrorSpan(b, i)
		}
		switch errors {
		case "surrogatepass":
			return nil, newUnicodeError("UnicodeDecodeError", enc, b, int64(i), int64(end), reason)
		case "replace":
			pyWriteRune(&out, utf8.RuneError)
		case "backslashreplace":
			for _, c := range b[i:end] {
				fmt.Fprintf(&out, `\x%02x`, c)
			}
		case "surrogateescape":
			for _, c := range b[i:end] {
				pyWriteRune(&out, 0xdc00+rune(c))
			}
		}
		i = end
	}
	return out.String(), nil
}
