package interp

import (
	"fmt"
	"regexp"
	"strings"
	"unicode/utf8"
)

// The re module (spec 11.8): a validated portable subset of Python regular
// expressions implemented over Go's RE2 engine. Patterns outside the subset
// raise ValueError with the same messages as the Python VM.

const (
	reIgnoreCase = 2
	reMultiline  = 8
	reDotAll     = 16
)

// reValidate mirrors diaspore-py's _re_validate byte for byte.
func reValidate(pattern string) error {
	i, n := 0, len(pattern)
	for i < n {
		ch := pattern[i]
		if ch == '\\' {
			if i+1 < n && pattern[i+1] >= '1' && pattern[i+1] <= '9' {
				return newError("ValueError", "diaspore re: backreferences are not supported")
			}
			if i+1 < n && (pattern[i+1] == 'A' || pattern[i+1] == 'Z') {
				return newError("ValueError", `diaspore re: \A/\Z are not supported (use ^ and $)`)
			}
			i += 2
			continue
		}
		if strings.HasPrefix(pattern[i:], "(?") {
			j := i + 2
			flags := 0
			for j < n && (pattern[j] == 'i' || pattern[j] == 'm' || pattern[j] == 's') {
				flags++
				j++
			}
			ok := (j < n && pattern[j] == ':') || (flags > 0 && j < n && pattern[j] == ')')
			if !ok {
				return newError("ValueError",
					"diaspore re: only (?:...) groups and (?ims) flags are supported")
			}
			i = j + 1
			continue
		}
		if (ch == '*' || ch == '+' || ch == '?') && i+1 < n && pattern[i+1] == '+' {
			return newError("ValueError", "diaspore re: possessive quantifiers are not supported")
		}
		i++
	}
	return nil
}

// compiledRe caches the anchor variants for one (pattern, flags) key.
type compiledRe struct {
	prefix    string // inline-flag prefix, e.g. "(?im)"
	pat       string
	search    *regexp.Regexp
	match     *regexp.Regexp // \A-anchored
	fullmatch *regexp.Regexp // \A(?:p)\z
	probe     *regexp.Regexp // lazily built: \A(?s:.)(?:p)
}

// findAllPy returns Go's match stream repaired to CPython finditer semantics:
// Go drops an empty match occurring exactly at the end of the preceding
// non-empty match; Python (3.7+) keeps it.
func (c *compiledRe) findAllPy(s string) [][]int {
	raw := c.search.FindAllStringSubmatchIndex(s, -1)
	out := make([][]int, 0, len(raw))
	for i, m := range raw {
		out = append(out, m)
		if m[1] > m[0] {
			e := m[1]
			if i+1 < len(raw) && raw[i+1][0] == e {
				continue
			}
			if em := c.emptyMatchAt(s, e); em != nil {
				out = append(out, em)
			}
		}
	}
	return out
}

// emptyMatchAt reports the group vector of a preferred empty match at byte
// position e (always > 0 here: e is the end of a non-empty match). Context
// assertions (^, $, \b) are preserved by probing on the previous rune plus
// the suffix with a one-dot prefix.
func (c *compiledRe) emptyMatchAt(s string, e int) []int {
	if e <= 0 || e > len(s) {
		return nil
	}
	if c.probe == nil {
		p, err := regexp.Compile(c.prefix + `\A(?s:.)(?:` + c.pat + `)`)
		if err != nil {
			return nil
		}
		c.probe = p
	}
	_, size := utf8.DecodeLastRuneInString(s[:e])
	off := e - size
	loc := c.probe.FindStringSubmatchIndex(s[off:])
	if loc == nil || loc[1] != size {
		return nil // no match, or the pattern prefers a non-empty match here
	}
	out := make([]int, len(loc))
	out[0], out[1] = e, e
	for g := 2; g < len(loc); g++ {
		if loc[g] < 0 {
			out[g] = -1
		} else {
			out[g] = loc[g] + off
		}
	}
	return out
}

type reCache struct {
	m map[string]*compiledRe
}

func flagPrefix(flags int64) string {
	var b strings.Builder
	if flags&(reIgnoreCase|reMultiline|reDotAll) != 0 {
		b.WriteString("(?")
		if flags&reIgnoreCase != 0 {
			b.WriteByte('i')
		}
		if flags&reMultiline != 0 {
			b.WriteByte('m')
		}
		if flags&reDotAll != 0 {
			b.WriteByte('s')
		}
		b.WriteByte(')')
	}
	return b.String()
}

func (rc *reCache) get(pattern Value, flags int64) (*compiledRe, error) {
	p, ok := pattern.(string)
	if !ok {
		return nil, newError("TypeError", "re pattern must be a string")
	}
	key := fmt.Sprintf("%d:%s", flags, p)
	if c, ok := rc.m[key]; ok {
		return c, nil
	}
	if err := reValidate(p); err != nil {
		return nil, err
	}
	prefix := flagPrefix(flags)
	compile := func(src string) (*regexp.Regexp, error) {
		re, err := regexp.Compile(src)
		if err != nil {
			msg := err.Error()
			if i := strings.Index(msg, ": "); i >= 0 {
				msg = msg[i+2:]
			}
			return nil, newErrorf("ValueError", "diaspore re: invalid pattern: %s", msg)
		}
		return re, nil
	}
	c := &compiledRe{prefix: prefix, pat: p}
	var err error
	if c.search, err = compile(prefix + p); err != nil {
		return nil, err
	}
	if c.match, err = compile(prefix + `\A(?:` + p + `)`); err != nil {
		return nil, err
	}
	if c.fullmatch, err = compile(prefix + `\A(?:` + p + `)\z`); err != nil {
		return nil, err
	}
	rc.m[key] = c
	return c, nil
}

// runeIndex converts a byte offset in s to a code-point offset (CPython
// match positions are code-point based).
func runeIndex(s string, byteOff int) int64 {
	return int64(utf8.RuneCountInString(s[:byteOff]))
}

// wrapMatch builds a re.Match NativeObject over a submatch-index vector.
func wrapMatch(s string, loc []int) Value {
	nGroups := len(loc)/2 - 1
	groupVal := func(n int) Value {
		if n < 0 || n > nGroups {
			return nil
		}
		if loc[2*n] < 0 {
			return None
		}
		return s[loc[2*n]:loc[2*n+1]]
	}
	groupArg := func(name string, args []Value) (int, error) {
		n := int64(0)
		if len(args) > 1 {
			return 0, newErrorf("TypeError", "%s expected at most 1 arguments, got %d",
				name, len(args))
		}
		if len(args) == 1 {
			v, ok := asInt(args[0])
			if !ok {
				return 0, newError("IndexError", "no such group")
			}
			n = v
		}
		if n < 0 || int(n) > nGroups {
			return 0, newError("IndexError", "no such group")
		}
		return int(n), nil
	}
	spanOf := func(n int) (Value, Value) {
		if loc[2*n] < 0 {
			return int64(-1), int64(-1)
		}
		return mkInt(runeIndex(s, loc[2*n])), mkInt(runeIndex(s, loc[2*n+1]))
	}
	return &NativeObject{Kind: "re.Match", methods: map[string]*Builtin{
		"group": bi("group", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			n, err := groupArg("group", args)
			if err != nil {
				return nil, err
			}
			return groupVal(n), nil
		}),
		"groups": bi("groups", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			items := make([]Value, nGroups)
			for i := 1; i <= nGroups; i++ {
				items[i-1] = groupVal(i)
			}
			return &Tuple{Items: items}, nil
		}),
		"start": bi("start", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			n, err := groupArg("start", args)
			if err != nil {
				return nil, err
			}
			v, _ := spanOf(n)
			return v, nil
		}),
		"end": bi("end", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			n, err := groupArg("end", args)
			if err != nil {
				return nil, err
			}
			_, v := spanOf(n)
			return v, nil
		}),
		"span": bi("span", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			n, err := groupArg("span", args)
			if err != nil {
				return nil, err
			}
			a, b := spanOf(n)
			return &Tuple{Items: []Value{a, b}}, nil
		}),
	}}
}

// reEscapeSet is CPython's re.escape special-character set.
var reEscapeSet = func() [128]bool {
	var set [128]bool
	for _, c := range []byte("()[]{}?*+-|^$\\.&~# \t\n\r\v\f") {
		set[c] = true
	}
	return set
}()

func reEscape(s string) string {
	var b strings.Builder
	for _, r := range s {
		if r < 128 && reEscapeSet[r] {
			b.WriteByte('\\')
		}
		b.WriteRune(r)
	}
	return b.String()
}

// expandTemplate applies a sub() replacement template to one match.
func expandTemplate(b *strings.Builder, repl, s string, loc []int) error {
	nGroups := len(loc)/2 - 1
	for i := 0; i < len(repl); i++ {
		c := repl[i]
		if c != '\\' {
			b.WriteByte(c)
			continue
		}
		i++
		if i >= len(repl) {
			return newError("ValueError", "diaspore re: bad escape (end of pattern)")
		}
		e := repl[i]
		switch {
		case e >= '1' && e <= '9':
			n := int(e - '0')
			if n > nGroups {
				return newErrorf("ValueError", "invalid group reference %d", n)
			}
			if loc[2*n] >= 0 {
				b.WriteString(s[loc[2*n]:loc[2*n+1]])
			}
		case e == '\\':
			b.WriteByte('\\')
		case e == 'n':
			b.WriteByte('\n')
		case e == 't':
			b.WriteByte('\t')
		case e == 'r':
			b.WriteByte('\r')
		case e == 'f':
			b.WriteByte('\f')
		case e == 'v':
			b.WriteByte('\v')
		case e == 'a':
			b.WriteByte('\a')
		case e == 'b':
			b.WriteByte('\b')
		case e == '0':
			b.WriteByte(0)
		default:
			return newErrorf("ValueError", `diaspore re: bad escape \%c`, e)
		}
	}
	return nil
}

func (vm *VM) makeReModule() *NativeModule {
	cache := &reCache{m: map[string]*compiledRe{}}

	intArg := func(what string, v Value) (int64, error) {
		n, ok := asInt(v)
		if !ok {
			return 0, newErrorf("TypeError", "%s must be an integer", what)
		}
		return n, nil
	}

	// parse handles the (pattern, string, flags=0) argument shape shared by
	// search/match/fullmatch/findall.
	parse := func(name string, args []Value, kw []KwArg) (*compiledRe, string, error) {
		if err := needArgs(name, args, 2, 3); err != nil {
			return nil, "", err
		}
		flags := int64(0)
		if len(args) == 3 {
			f, err := intArg("flags", args[2])
			if err != nil {
				return nil, "", err
			}
			flags = f
		}
		if v, ok := kwGet(kw, "flags"); ok {
			f, err := intArg("flags", v)
			if err != nil {
				return nil, "", err
			}
			flags = f
		}
		c, err := cache.get(args[0], flags)
		if err != nil {
			return nil, "", err
		}
		s, ok := args[1].(string)
		if !ok {
			return nil, "", newErrorf("TypeError",
				"expected string or bytes-like object, got '%s'", typeName(args[1]))
		}
		return c, s, nil
	}

	matcher := func(name string, pick func(c *compiledRe) *regexp.Regexp) *Builtin {
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			c, s, err := parse(name, args, kw)
			if err != nil {
				return nil, err
			}
			loc := pick(c).FindStringSubmatchIndex(s)
			if loc == nil {
				return None, nil
			}
			return wrapMatch(s, loc), nil
		})
	}

	findall := bi("findall", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		c, s, err := parse("findall", args, kw)
		if err != nil {
			return nil, err
		}
		nGroups := c.search.NumSubexp()
		locs := c.findAllPy(s)
		out := &List{}
		for _, loc := range locs {
			switch {
			case nGroups == 0:
				out.Items = append(out.Items, s[loc[0]:loc[1]])
			case nGroups == 1:
				out.Items = append(out.Items, groupOrEmpty(s, loc, 1))
			default:
				row := make([]Value, nGroups)
				for g := 1; g <= nGroups; g++ {
					row[g-1] = groupOrEmpty(s, loc, g)
				}
				out.Items = append(out.Items, &Tuple{Items: row})
			}
		}
		return out, nil
	})

	split := bi("split", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := needArgs("split", args, 2, 4); err != nil {
			return nil, err
		}
		maxsplit, flags := int64(0), int64(0)
		var err error
		if len(args) >= 3 {
			if maxsplit, err = intArg("maxsplit", args[2]); err != nil {
				return nil, err
			}
		}
		if v, ok := kwGet(kw, "maxsplit"); ok {
			if maxsplit, err = intArg("maxsplit", v); err != nil {
				return nil, err
			}
		}
		if len(args) == 4 {
			if flags, err = intArg("flags", args[3]); err != nil {
				return nil, err
			}
		}
		if v, ok := kwGet(kw, "flags"); ok {
			if flags, err = intArg("flags", v); err != nil {
				return nil, err
			}
		}
		c, err := cache.get(args[0], flags)
		if err != nil {
			return nil, err
		}
		s, ok := args[1].(string)
		if !ok {
			return nil, newErrorf("TypeError",
				"expected string or bytes-like object, got '%s'", typeName(args[1]))
		}
		nGroups := c.search.NumSubexp()
		out := &List{}
		last := 0
		count := int64(0)
		for _, loc := range c.findAllPy(s) {
			if maxsplit > 0 && count >= maxsplit {
				break
			}
			out.Items = append(out.Items, s[last:loc[0]])
			for g := 1; g <= nGroups; g++ {
				if loc[2*g] < 0 {
					out.Items = append(out.Items, None)
				} else {
					out.Items = append(out.Items, s[loc[2*g]:loc[2*g+1]])
				}
			}
			last = loc[1]
			count++
		}
		out.Items = append(out.Items, s[last:])
		return out, nil
	})

	sub := bi("sub", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := needArgs("sub", args, 3, 5); err != nil {
			return nil, err
		}
		repl, ok := args[1].(string)
		if !ok {
			return nil, newError("TypeError", "diaspore re: repl must be a string")
		}
		if i := strings.Index(repl, `\g`); i >= 0 {
			return nil, newError("ValueError", `diaspore re: \g<...> replacements are not supported`)
		}
		count := int64(0)
		if len(args) >= 4 {
			n, ok := asInt(args[3])
			if !ok {
				return nil, newError("TypeError", "count must be an integer")
			}
			count = n
		}
		if v, ok := kwGet(kw, "count"); ok {
			n, ok := asInt(v)
			if !ok {
				return nil, newError("TypeError", "count must be an integer")
			}
			count = n
		}
		flags := int64(0)
		if len(args) == 5 {
			f, ok := asInt(args[4])
			if !ok {
				return nil, newError("TypeError", "flags must be an integer")
			}
			flags = f
		}
		if v, ok := kwGet(kw, "flags"); ok {
			f, ok := asInt(v)
			if !ok {
				return nil, newError("TypeError", "flags must be an integer")
			}
			flags = f
		}
		c, err := cache.get(args[0], flags)
		if err != nil {
			return nil, err
		}
		s, ok := args[2].(string)
		if !ok {
			return nil, newErrorf("TypeError",
				"expected string or bytes-like object, got '%s'", typeName(args[2]))
		}
		var b strings.Builder
		last := 0
		done := int64(0)
		for _, loc := range c.findAllPy(s) {
			if count > 0 && done >= count {
				break
			}
			b.WriteString(s[last:loc[0]])
			if err := expandTemplate(&b, repl, s, loc); err != nil {
				return nil, err
			}
			last = loc[1]
			done++
		}
		b.WriteString(s[last:])
		return b.String(), nil
	})

	escape := bi("escape", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := needArgs("escape", args, 1, 1); err != nil {
			return nil, err
		}
		s, ok := args[0].(string)
		if !ok {
			return nil, newErrorf("TypeError", "expected string, got '%s'", typeName(args[0]))
		}
		return reEscape(s), nil
	})

	return &NativeModule{Name: "re", NS: map[string]Value{
		"IGNORECASE": int64(reIgnoreCase),
		"MULTILINE":  int64(reMultiline),
		"DOTALL":     int64(reDotAll),
		"search":     matcher("search", func(c *compiledRe) *regexp.Regexp { return c.search }),
		"match":      matcher("match", func(c *compiledRe) *regexp.Regexp { return c.match }),
		"fullmatch":  matcher("fullmatch", func(c *compiledRe) *regexp.Regexp { return c.fullmatch }),
		"findall":    findall,
		"split":      split,
		"sub":        sub,
		"escape":     escape,
	}}
}

func groupOrEmpty(s string, loc []int, g int) Value {
	if loc[2*g] < 0 {
		return ""
	}
	return s[loc[2*g]:loc[2*g+1]]
}
