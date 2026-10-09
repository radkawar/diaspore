package interp

import (
	"bytes"
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
	"dict":      setOfNames("get", "keys", "values", "items", "pop", "update", "setdefault", "clear", "copy"),
	"list":      setOfNames("append", "extend", "insert", "pop", "remove", "clear", "index", "count", "reverse", "sort", "copy"),
	"set":       setOfNames("add", "remove", "discard", "clear", "copy", "union", "intersection", "difference", "symmetric_difference", "issubset", "issuperset"),
	"frozenset": setOfNames("copy", "union", "intersection", "difference", "symmetric_difference", "issubset", "issuperset", "isdisjoint"),
	"str":       setOfNames("upper", "lower", "title", "capitalize", "casefold", "strip", "lstrip", "rstrip", "split", "rsplit", "splitlines", "join", "replace", "find", "rfind", "index", "count", "startswith", "endswith", "isdigit", "isalpha", "isalnum", "isspace", "islower", "isupper", "swapcase", "zfill", "ljust", "rjust", "center", "encode", "partition", "rpartition", "removeprefix", "removesuffix", "format"),
	"tuple":     setOfNames("count", "index"),
	"bytes":     setOfNames("decode", "find", "split", "strip", "lstrip", "rstrip", "replace", "startswith", "endswith", "upper", "lower"),
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
	case string:
		fn = strMethod(t, n)
	case *List:
		fn = listMethod(t, n)
	case *Dict:
		fn = dictMethod(t, n)
	case *classDict:
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
		fn = bytesMethod(t, n)
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
	case "upper":
		return simple(func(vm *VM, args []Value) (Value, error) {
			return pyUpper(s), nil
		})
	case "lower":
		return simple(func(vm *VM, args []Value) (Value, error) {
			return strings.ToLower(s), nil
		})
	case "title":
		return simple(func(vm *VM, args []Value) (Value, error) {
			return pyTitle(s), nil
		})
	case "capitalize":
		return simple(func(vm *VM, args []Value) (Value, error) {
			rs := []rune(s)
			if len(rs) == 0 {
				return s, nil
			}
			var b strings.Builder
			b.WriteRune(unicode.ToTitle(rs[0]))
			b.WriteString(strings.ToLower(string(rs[1:])))
			return b.String(), nil
		})
	case "casefold":
		return simple(func(vm *VM, args []Value) (Value, error) {
			var b strings.Builder
			for _, r := range s {
				switch r {
				case 0x00DF, 0x1E9E:
					b.WriteString("ss")
				default:
					b.WriteRune(unicode.ToLower(r))
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
					for _, r := range chars {
						set[r] = true
					}
					inSet = func(r rune) bool { return set[r] }
				}
			}
			out := s
			if n != "rstrip" {
				out = strings.TrimLeftFunc(out, inSet)
			}
			if n != "lstrip" {
				out = strings.TrimRightFunc(out, inSet)
			}
			return out, nil
		})
	case "split", "rsplit":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 0, 2); err != nil {
				return nil, err
			}
			maxsplit := int64(-1)
			if len(args) >= 2 {
				m, err := wantInt("maxsplit", args[1])
				if err != nil {
					return nil, err
				}
				maxsplit = m
			}
			var sep string
			hasSep := false
			if len(args) >= 1 {
				if _, isNone := args[0].(NoneType); !isNone {
					sp, err := wantStr("sep", args[0])
					if err != nil {
						return nil, err
					}
					if sp == "" {
						return nil, newError("ValueError", "empty separator")
					}
					sep = sp
					hasSep = true
				}
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
		})
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
			rs := []rune(s)
			start, end, err := strRegion(rs, args[1:])
			if err != nil {
				return nil, err
			}
			if n == "count" {
				return int64(countRunes(rs[start:end], []rune(sub))), nil
			}
			idx := findRunes(rs, []rune(sub), start, end, n == "rfind")
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
			rs := []rune(s)
			start, end, err := strRegion(rs, args[1:])
			if err != nil {
				return nil, err
			}
			region := string(rs[start:end])
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
	case "isdigit", "isalpha", "isalnum", "isspace":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if s == "" {
				return false, nil
			}
			var pred func(rune) bool
			switch n {
			case "isdigit":
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
			for _, r := range s {
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
			rs := []rune(s)
			if int64(len(rs)) >= w {
				return s, nil
			}
			sign := ""
			body := s
			if len(rs) > 0 && (rs[0] == '+' || rs[0] == '-') {
				sign = string(rs[0])
				body = string(rs[1:])
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
				if len([]rune(f)) != 1 {
					return nil, newError("TypeError",
						"The fill character must be exactly one character long")
				}
				fill = f
			}
			pad := int(w) - len([]rune(s))
			if pad <= 0 {
				return s, nil
			}
			if n == "ljust" {
				return s + strings.Repeat(fill, pad), nil
			}
			return strings.Repeat(fill, pad) + s, nil
		})
	case "encode":
		return simple(func(vm *VM, args []Value) (Value, error) {
			if err := needArgs(n, args, 0, 1); err != nil {
				return nil, err
			}
			enc := "utf-8"
			if len(args) == 1 {
				e, err := wantStr("encoding", args[0])
				if err != nil {
					return nil, err
				}
				enc = e
			}
			return encodeStr(s, enc)
		})
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
				if len([]rune(f)) != 1 {
					return nil, newError("TypeError",
						"The fill character must be exactly one character long")
				}
				fill = f
			}
			width := int(w)
			marg := width - len([]rune(s))
			if marg <= 0 {
				return s, nil
			}
			left := marg/2 + (marg & width & 1)
			return strings.Repeat(fill, left) + s + strings.Repeat(fill, marg-left), nil
		})
	case "islower", "isupper":
		return simple(func(vm *VM, args []Value) (Value, error) {
			hasCased := false
			for _, r := range s {
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
			for _, r := range s {
				switch {
				case unicode.IsUpper(r) || unicode.IsTitle(r):
					b.WriteRune(unicode.ToLower(r))
				case unicode.IsLower(r):
					b.WriteRune(unicode.ToUpper(r))
				default:
					b.WriteRune(r)
				}
			}
			return b.String(), nil
		})
	}
	return nil
}

func encodeStr(s, enc string) (Value, error) {
	switch strings.ToLower(enc) {
	case "utf-8", "utf8":
		return []byte(s), nil
	case "ascii", "us-ascii", "us_ascii":
		position, start, end := int64(0), int64(-1), int64(0)
		for _, r := range s {
			if r > 127 {
				if start < 0 {
					start = position
				}
				end = position + 1
			} else if start >= 0 {
				break
			}
			position++
		}
		if start >= 0 {
			return nil, newUnicodeError("UnicodeEncodeError", "ascii", s, start, end, "ordinal not in range(128)")
		}
		return []byte(s), nil
	case "latin-1", "latin1", "iso-8859-1":
		out := make([]byte, 0, len(s))
		for _, r := range s {
			if r > 0xFF {
				return nil, newErrorf("ValueError",
					"'latin-1' codec can't encode character %s", reprString(string(r)))
			}
			out = append(out, byte(r))
		}
		return out, nil
	}
	return nil, newErrorf("ValueError", "unknown encoding: %s", enc)
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

func pyUpper(s string) string {
	var b strings.Builder
	for _, r := range s {
		if exp, ok := upperExpansions[r]; ok {
			b.WriteString(exp)
		} else {
			b.WriteRune(unicode.ToUpper(r))
		}
	}
	return b.String()
}

func pyTitle(s string) string {
	var b strings.Builder
	prevCased := false
	for _, r := range s {
		cased := unicode.IsLower(r) || unicode.IsUpper(r) || unicode.IsTitle(r)
		if cased {
			if prevCased {
				b.WriteRune(unicode.ToLower(r))
			} else {
				b.WriteRune(unicode.ToTitle(r))
			}
		} else {
			b.WriteRune(r)
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
	rs := []rune(s)
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
			cur.WriteRune(r)
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
	src = rawPrim(src)
	if view, ok := src.(*classDict); ok {
		src = view.snapshot()
	}
	if other, ok := src.(*Dict); ok {
		for _, e := range other.entries {
			if err := d.Set(e.key, e.val); err != nil {
				return err
			}
		}
		return nil
	}
	pairs, err := vm.iterToSlice(src)
	if err != nil {
		return err
	}
	for i, p := range pairs {
		kv, err := vm.iterToSlice(p)
		if err != nil {
			return err
		}
		if len(kv) != 2 {
			return newErrorf("ValueError",
				"dictionary update sequence element #%d has length %d; 2 is required",
				i, len(kv))
		}
		if err := d.Set(kv[0], kv[1]); err != nil {
			return err
		}
	}
	return nil
}

func setMethod(s *Set, n string) func(*VM, []Value, []KwArg) (Value, error) {
	switch n {
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
	case "decode":
		return func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs(n, args, 0, 1); err != nil {
				return nil, err
			}
			enc := "utf-8"
			if len(args) == 1 {
				e, err := wantStr("encoding", args[0])
				if err != nil {
					return nil, err
				}
				enc = e
			}
			if v, ok := kwGet(kw, "encoding"); ok {
				e, err := wantStr("encoding", v)
				if err != nil {
					return nil, err
				}
				enc = e
			}
			return decodeBytes(b, enc)
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
	switch strings.ToLower(enc) {
	case "utf-8", "utf8":
		if !utf8.Valid(b) {
			return nil, newError("ValueError",
				"'utf-8' codec can't decode bytes: invalid data")
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
		return string(rs), nil
	}
	return nil, newErrorf("ValueError", "unknown encoding: %s", enc)
}
