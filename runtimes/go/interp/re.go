package interp

import (
	"fmt"
	"regexp/syntax"
	"strconv"
	"strings"
)

// compiledRe owns a Python-compatible translation of the Go regexp program.
// Unsupported constructs fail at compilation instead of approximating matches.
type compiledRe struct {
	pattern    Value
	flags      int64
	bytes      bool
	prog       *syntax.Prog
	slots      []int
	public     map[int]int
	names      map[string]int
	assertions map[int]reAssertion
	loops      map[uint32]reLoop
	progress   map[uint32]reProgress
	guards     int
	specials   map[uint32]reSpecialInst
	object     *NativeObject
	errorClass *Class
}

type reCache struct {
	m          map[string]*compiledRe
	errorClass *Class
}

func reText(v Value) ([]rune, bool, error) {
	switch s := rawPrim(v).(type) {
	case string:
		return pyRunes(s), false, nil
	case []byte:
		r := make([]rune, len(s))
		for i, b := range s {
			r[i] = rune(b)
		}
		return r, true, nil
	default:
		return nil, false, newErrorf("TypeError", "expected string or bytes, got '%s'", typeName(v))
	}
}

func reValue(s []rune, binary bool) Value {
	if !binary {
		return pyString(s)
	}
	b := make([]byte, len(s))
	for i, r := range s {
		b[i] = byte(r)
	}
	return b
}

func reInteger(v Value, name string) (int64, error) {
	n, ok := asInt(v)
	if !ok {
		return 0, newErrorf("TypeError", "%s must be an integer", name)
	}
	return n, nil
}

func reIsNone(v Value) bool { _, ok := v.(NoneType); return ok }

func reArgs(name string, args []Value, kw []KwArg, names []string, required int, defaults []Value) ([]Value, error) {
	if len(args) > len(names) {
		return nil, newErrorf("TypeError", "%s expected at most %d arguments", name, len(names))
	}
	out := make([]Value, len(names))
	copy(out[required:], defaults)
	copy(out, args)
	set := make([]bool, len(names))
	for i := range args {
		set[i] = true
	}
	for _, k := range kw {
		index := -1
		for i, n := range names {
			if n == k.Name {
				index = i
				break
			}
		}
		if index < 0 {
			return nil, newErrorf("TypeError", "%s got an unexpected keyword argument '%s'", name, k.Name)
		}
		if set[index] {
			return nil, newErrorf("TypeError", "%s got multiple values for '%s'", name, k.Name)
		}
		out[index] = k.Val
		set[index] = true
	}
	for i := 0; i < required; i++ {
		if !set[i] {
			return nil, newErrorf("TypeError", "%s missing required argument '%s'", name, names[i])
		}
	}
	return out, nil
}

func (rc *reCache) get(pattern Value, flags int64) (result *compiledRe, err error) {
	if rc.errorClass == nil {
		rc.errorClass = makeReErrorClass()
	}
	defer func() { err = reRaiseDiagnostic(err, pattern, rc.errorClass) }()
	if object, ok := pattern.(*NativeObject); ok && object.Kind == "re.Pattern" {
		if flags != 0 {
			return nil, newError("ValueError", "cannot process flags argument with a compiled pattern")
		}
		return object.payload.(*compiledRe), nil
	}
	cacheFlags := flags
	runes, binary, err := reText(pattern)
	if err != nil {
		return nil, err
	}
	if flags & ^int64(reIgnoreCase|reLocale|reMultiline|reDotAll|reUnicode|reVerbose|reASCII) != 0 {
		return nil, newError("ValueError", "unsupported regex flags")
	}
	if flags&reLocale != 0 {
		return nil, newError("ValueError", "LOCALE is not supported")
	}
	if flags&reASCII != 0 && flags&reUnicode != 0 {
		return nil, newError("ValueError", "ASCII and UNICODE flags are incompatible")
	}
	if binary {
		if flags&reUnicode != 0 {
			return nil, newError("ValueError", "cannot use UNICODE flag with a bytes pattern")
		}
	} else if flags&reASCII == 0 {
		flags |= reUnicode
	}
	source := pyString(runes)
	key := fmt.Sprintf("%t:%d:%s", binary, cacheFlags, source)
	if c, ok := rc.m[key]; ok {
		return c, nil
	}
	translateFlags := flags
	if binary {
		translateFlags |= reASCII
	}
	prefix := "__diaspore_assert_"
	for strings.Contains(source, prefix) {
		prefix += "_"
	}
	t := &reTranslator{s: source, bytes: binary, prefix: prefix, assertions: map[string]string{}, global: translateFlags, asciiExplicit: flags&reASCII != 0, explicitModes: cacheFlags & (reASCII | reUnicode)}
	translated, err := t.translate(translateFlags, false)
	if err != nil {
		return nil, err
	}
	parsed, err := syntax.Parse(translated, syntax.Perl)
	if err != nil {
		return nil, reSyntaxDiagnostic(err)
	}
	c := &compiledRe{pattern: pattern, flags: t.global, bytes: binary, slots: []int{0}, public: map[int]int{0: 0}, names: map[string]int{}, assertions: map[int]reAssertion{}, errorClass: rc.errorClass}
	if binary && !t.asciiExplicit {
		c.flags &^= reASCII
	}
	for slot, name := range parsed.CapNames() {
		if slot == 0 {
			continue
		}
		if assertion, ok := t.assertions[name]; ok {
			c.assertions[slot] = reAssertion{assertion}
			continue
		}
		if _, ok := t.specials[name]; ok {
			continue
		}
		group := len(c.slots)
		c.slots = append(c.slots, slot)
		c.public[slot] = group
		if name != "" {
			if _, exists := c.names[name]; exists {
				return nil, reFailure("redefinition of group name " + name)
			}
			c.names[name] = group
		}
	}
	specials, err := c.prepareSpecials(parsed, t.specials)
	if err != nil {
		return nil, err
	}
	c.prog, c.loops, c.progress, c.guards, c.specials = reCompileProgram(parsed, specials)
	c.object = c.makeObject()
	if len(rc.m) >= 512 {
		clear(rc.m)
	}
	rc.m[key] = c
	return c, nil
}

func (c *compiledRe) groupNumber(v Value) (int, error) {
	if name, ok := v.(string); ok {
		if n, found := c.names[name]; found {
			return n, nil
		}
		return 0, newError("IndexError", "no such group")
	}
	n, ok := asInt(v)
	if !ok || n < 0 || n >= int64(len(c.slots)) {
		return 0, newError("IndexError", "no such group")
	}
	return int(n), nil
}

func (c *compiledRe) groupValue(s []rune, loc []int, n int, def Value) Value {
	if loc[2*n] < 0 {
		return def
	}
	return reValue(s[loc[2*n]:loc[2*n+1]], c.bytes)
}

func (c *compiledRe) wrapMatch(original Value, s []rune, loc []int, pos, end int) *NativeObject {
	last := loc[len(loc)-1]
	var lastIndex, lastGroup Value = None, None
	if last > 0 {
		lastIndex = int64(last)
		for name, n := range c.names {
			if n == last {
				lastGroup = name
				break
			}
		}
	}
	regs := &Tuple{Items: make([]Value, len(c.slots))}
	for group := range c.slots {
		regs.Items[group] = &Tuple{Items: []Value{int64(loc[2*group]), int64(loc[2*group+1])}}
	}
	obj := &NativeObject{Kind: "re.Match", attrs: map[string]Value{"re": c.object, "string": original, "pos": int64(pos), "endpos": int64(end), "lastindex": lastIndex, "lastgroup": lastGroup, "regs": regs}, methods: map[string]*Builtin{}}
	obj.methods["group"] = bi("group", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if len(kw) != 0 {
			return nil, newError("TypeError", "group takes no keyword arguments")
		}
		if len(args) == 0 {
			return c.groupValue(s, loc, 0, None), nil
		}
		values := make([]Value, len(args))
		for i, arg := range args {
			n, err := c.groupNumber(arg)
			if err != nil {
				return nil, err
			}
			values[i] = c.groupValue(s, loc, n, None)
		}
		if len(values) == 1 {
			return values[0], nil
		}
		return &Tuple{Items: values}, nil
	})
	obj.methods["__getitem__"] = bi("__getitem__", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := needArgs("__getitem__", args, 1, 1); err != nil {
			return nil, err
		}
		return obj.methods["group"].Fn(vm, args, kw)
	})
	for _, operation := range []string{"groups", "groupdict"} {
		name := operation
		obj.methods[name] = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			values, err := reArgs(name, args, kw, []string{"default"}, 0, []Value{None})
			if err != nil {
				return nil, err
			}
			if name == "groupdict" {
				d := NewDict()
				for slot, groupName := range c.progGroupNames() {
					if groupName != "" {
						d.SetStr(groupName, c.groupValue(s, loc, slot, values[0]))
					}
				}
				return d, nil
			}
			out := make([]Value, len(c.slots)-1)
			for i := range out {
				out[i] = c.groupValue(s, loc, i+1, values[0])
			}
			return &Tuple{Items: out}, nil
		})
	}
	for _, operation := range []string{"start", "end", "span"} {
		name := operation
		obj.methods[name] = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			values, err := reArgs(name, args, kw, []string{"group"}, 0, []Value{int64(0)})
			if err != nil {
				return nil, err
			}
			n, err := c.groupNumber(values[0])
			if err != nil {
				return nil, err
			}
			a, b := int64(loc[2*n]), int64(loc[2*n+1])
			if name == "start" {
				return a, nil
			}
			if name == "end" {
				return b, nil
			}
			return &Tuple{Items: []Value{a, b}}, nil
		})
	}
	obj.methods["expand"] = bi("expand", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		values, err := reArgs("expand", args, kw, []string{"template"}, 1, nil)
		if err != nil {
			return nil, err
		}
		parts, err := c.template(values[0])
		if err != nil {
			return nil, err
		}
		return c.expand(parts, s, loc), nil
	})
	return obj
}

func (c *compiledRe) progGroupNames() []string {
	names := make([]string, len(c.slots))
	for name, n := range c.names {
		names[n] = name
	}
	return names
}

type reMatchIterator struct {
	vm             *VM
	c              *compiledRe
	original       Value
	text           []rune
	pos, end, next int
	nonempty, done bool
}

func (it *reMatchIterator) KindName() string { return "callable_iterator" }
func (it *reMatchIterator) location() ([]int, error) {
	if it.done {
		return nil, nil
	}
	for it.next <= it.end {
		loc, err := it.c.run(it.vm, it.text, it.next, it.end, false, it.nonempty)
		if err != nil {
			return nil, err
		}
		if loc != nil {
			it.next = loc[1]
			it.nonempty = loc[0] == loc[1]
			return loc, nil
		}
		it.next++
		it.nonempty = false
	}
	it.done = true
	return nil, nil
}
func (it *reMatchIterator) Next() (Value, bool, error) {
	loc, err := it.location()
	if err != nil {
		return nil, false, err
	}
	if loc == nil {
		return nil, false, nil
	}
	return it.c.wrapMatch(it.original, it.text, loc, it.pos, it.end), true, nil
}

func reBound(v Value, length int, name string) (int, error) {
	n, err := reInteger(v, name)
	if err != nil {
		return 0, err
	}
	if n < 0 {
		return 0, nil
	}
	if n > int64(length) {
		return length, nil
	}
	return int(n), nil
}

func (c *compiledRe) makeObject() *NativeObject {
	index := NewDict()
	for slot, name := range c.progGroupNames() {
		if name != "" {
			index.SetStr(name, int64(slot))
		}
	}
	obj := &NativeObject{Kind: "re.Pattern", payload: c, attrs: map[string]Value{"pattern": c.pattern, "flags": c.flags, "groups": int64(len(c.slots) - 1), "groupindex": index}, methods: map[string]*Builtin{}}
	for _, operation := range []string{"search", "match", "fullmatch", "findall", "finditer", "split", "sub", "subn"} {
		name := operation
		obj.methods[name] = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) { return c.invoke(vm, name, args, kw) })
	}
	return obj
}

func (c *compiledRe) invoke(vm *VM, name string, args []Value, kw []KwArg) (Value, error) {
	var names []string
	var defaults []Value
	required := 1
	switch name {
	case "split":
		names = []string{"string", "maxsplit"}
		defaults = []Value{int64(0)}
	case "sub", "subn":
		names = []string{"repl", "string", "count"}
		defaults = []Value{int64(0)}
		required = 2
	default:
		names = []string{"string", "pos", "endpos"}
		defaults = []Value{int64(0), missingV}
	}
	values, err := reArgs(name, args, kw, names, required, defaults)
	if err != nil {
		return nil, err
	}
	original := values[0]
	if required == 2 {
		original = values[1]
	}
	text, binary, err := reText(original)
	if err != nil {
		return nil, err
	}
	if binary != c.bytes {
		return nil, newError("TypeError", "cannot mix string and bytes patterns")
	}
	pos, end := 0, len(text)
	limit := int64(0)
	if name == "split" || required == 2 {
		limit, err = reInteger(values[len(values)-1], names[len(names)-1])
		if err != nil {
			return nil, err
		}
	} else {
		pos, err = reBound(values[1], len(text), "pos")
		if err != nil {
			return nil, err
		}
		if values[2] != missingV {
			end, err = reBound(values[2], len(text), "endpos")
			if err != nil {
				return nil, err
			}
		}
	}
	it := &reMatchIterator{vm: vm, c: c, original: original, text: text, pos: pos, end: end, next: pos}
	if name == "finditer" {
		return it, nil
	}
	if name == "match" || name == "fullmatch" || name == "search" {
		var loc []int
		if pos <= end {
			if name == "search" {
				loc, err = it.location()
			} else {
				loc, err = c.run(vm, text, pos, end, name == "fullmatch", false)
			}
		}
		if err != nil {
			return nil, err
		}
		if loc == nil {
			return None, nil
		}
		return c.wrapMatch(original, text, loc, pos, end), nil
	}
	if name == "findall" {
		out := &List{}
		for {
			loc, err := it.location()
			if err != nil {
				return nil, err
			}
			if loc == nil {
				break
			}
			empty := reValue(nil, c.bytes)
			if len(c.slots) == 1 {
				out.Items = append(out.Items, c.groupValue(text, loc, 0, empty))
			} else if len(c.slots) == 2 {
				out.Items = append(out.Items, c.groupValue(text, loc, 1, empty))
			} else {
				row := make([]Value, len(c.slots)-1)
				for i := range row {
					row[i] = c.groupValue(text, loc, i+1, empty)
				}
				out.Items = append(out.Items, &Tuple{Items: row})
			}
		}
		return out, nil
	}
	if name == "split" {
		out := &List{}
		last := 0
		done := int64(0)
		for limit >= 0 && (limit == 0 || done < limit) {
			loc, err := it.location()
			if err != nil {
				return nil, err
			}
			if loc == nil {
				break
			}
			out.Items = append(out.Items, reValue(text[last:loc[0]], c.bytes))
			for g := 1; g < len(c.slots); g++ {
				out.Items = append(out.Items, c.groupValue(text, loc, g, None))
			}
			last = loc[1]
			done++
		}
		out.Items = append(out.Items, reValue(text[last:], c.bytes))
		return out, nil
	}
	replacement := values[0]
	var parts []reTemplatePart
	callable := false
	switch rawPrim(replacement).(type) {
	case string, []byte:
		parts, err = c.template(replacement)
		if err != nil {
			return nil, err
		}
	default:
		accepted, err := biCallable(vm, []Value{replacement}, nil)
		if err != nil {
			return nil, err
		}
		if accepted != true {
			return nil, newError("TypeError", "replacement must be a string or callable")
		}
		callable = true
	}
	var result []rune
	last := 0
	done := int64(0)
	for limit >= 0 && (limit == 0 || done < limit) {
		loc, err := it.location()
		if err != nil {
			return nil, err
		}
		if loc == nil {
			break
		}
		result = append(result, text[last:loc[0]]...)
		if callable {
			value, err := vm.call(replacement, []Value{c.wrapMatch(original, text, loc, pos, end)}, nil)
			if err != nil {
				return nil, err
			}
			if !reIsNone(value) {
				runes, binary, err := reText(value)
				if err != nil {
					return nil, err
				}
				if binary != c.bytes {
					return nil, newError("TypeError", "replacement has the wrong string type")
				}
				result = append(result, runes...)
			}
		} else {
			result = c.appendTemplate(result, parts, text, loc)
		}
		last = loc[1]
		done++
	}
	result = append(result, text[last:]...)
	value := reValue(result, c.bytes)
	if name == "subn" {
		return &Tuple{Items: []Value{value, done}}, nil
	}
	return value, nil
}

type reTemplatePart struct {
	literal []rune
	group   int
}

func (c *compiledRe) template(value Value) (result []reTemplatePart, err error) {
	defer func() { err = reRaiseDiagnostic(err, value, c.errorClass) }()
	r, binary, err := reText(value)
	if err != nil {
		return nil, err
	}
	if binary != c.bytes {
		return nil, newError("TypeError", "replacement has the wrong string type")
	}
	var parts []reTemplatePart
	var literal []rune
	flush := func() {
		if len(literal) > 0 {
			parts = append(parts, reTemplatePart{literal: literal, group: -1})
			literal = nil
		}
	}
	for i := 0; i < len(r); i++ {
		ch := r[i]
		if ch != '\\' {
			literal = append(literal, ch)
			continue
		}
		escapeStart := i
		i++
		if i == len(r) {
			return nil, reErrorAt("bad escape (end of pattern)", escapeStart)
		}
		ch = r[i]
		switch {
		case ch == 'g':
			if i+1 >= len(r) || r[i+1] != '<' {
				return nil, reErrorAt("missing <", i+1)
			}
			i += 2
			start := i
			for i < len(r) && r[i] != '>' {
				i++
			}
			if i == start {
				return nil, reErrorAt("missing group name", start)
			}
			if i == len(r) {
				return nil, reErrorAt("missing >, unterminated name", start)
			}
			name := pyString(r[start:i])
			numeric := true
			for _, digit := range r[start:i] {
				if digit < '0' || digit > '9' {
					numeric = false
					break
				}
			}
			var group int
			if numeric {
				n, parseErr := strconv.ParseInt(name, 10, 64)
				if parseErr != nil || n >= int64(len(c.slots)) {
					number := strings.TrimLeft(name, "0")
					if number == "" {
						number = "0"
					}
					return nil, reErrorAt("invalid group reference "+number, start)
				}
				group = int(n)
			} else {
				ascii := true
				for _, ch := range r[start:i] {
					if ch > 127 {
						ascii = false
						break
					}
				}
				if !pyIsIdentifier(name) || binary && !ascii {
					return nil, reErrorAt("bad character in group name "+reprString(name), start)
				}
				var exists bool
				group, exists = c.names[name]
				if !exists {
					return nil, newError("IndexError", "unknown group name "+reprString(name))
				}
			}
			flush()
			parts = append(parts, reTemplatePart{group: group})
		case ch >= '0' && ch <= '9':
			start := i
			if ch == '0' || i+2 < len(r) && ch <= '7' && r[i+1] >= '0' && r[i+1] <= '7' && r[i+2] >= '0' && r[i+2] <= '7' {
				for i+1 < len(r) && i-start < 2 && r[i+1] >= '0' && r[i+1] <= '7' {
					i++
				}
				n, parseErr := strconv.ParseInt(string(r[start:i+1]), 8, 32)
				if parseErr != nil || n > 255 {
					return nil, reErrorAt("octal escape value "+pyString(r[escapeStart:i+1])+" outside of range 0-0o377", escapeStart)
				}
				literal = append(literal, rune(n))
			} else {
				if i+1 < len(r) && r[i+1] >= '0' && r[i+1] <= '9' {
					i++
				}
				n, _ := strconv.ParseInt(string(r[start:i+1]), 10, 64)
				if n >= int64(len(c.slots)) {
					return nil, reErrorAt(fmt.Sprintf("invalid group reference %d", n), start)
				}
				flush()
				parts = append(parts, reTemplatePart{group: int(n)})
			}
		case ch == '\\':
			literal = append(literal, '\\')
		case strings.ContainsRune("nrtfavb", ch):
			literal = append(literal, map[rune]rune{'n': '\n', 'r': '\r', 't': '\t', 'f': '\f', 'a': '\a', 'v': '\v', 'b': '\b'}[ch])
		case ch >= 'a' && ch <= 'z' || ch >= 'A' && ch <= 'Z':
			return nil, reErrorAt("bad escape \\"+string(ch), escapeStart)
		default:
			literal = append(literal, '\\', ch)
		}
	}
	flush()
	return parts, nil
}
func (c *compiledRe) appendTemplate(out []rune, parts []reTemplatePart, s []rune, loc []int) []rune {
	for _, part := range parts {
		if part.group < 0 {
			out = append(out, part.literal...)
		} else if loc[2*part.group] >= 0 {
			out = append(out, s[loc[2*part.group]:loc[2*part.group+1]]...)
		}
	}
	return out
}
func (c *compiledRe) expand(parts []reTemplatePart, s []rune, loc []int) Value {
	return reValue(c.appendTemplate(nil, parts, s, loc), c.bytes)
}

func (vm *VM) makeReModule() *NativeModule {
	cache := &reCache{m: map[string]*compiledRe{}, errorClass: makeReErrorClass()}
	ns := map[string]Value{"NOFLAG": int64(0), "I": int64(reIgnoreCase), "IGNORECASE": int64(reIgnoreCase), "L": int64(reLocale), "LOCALE": int64(reLocale), "M": int64(reMultiline), "MULTILINE": int64(reMultiline), "S": int64(reDotAll), "DOTALL": int64(reDotAll), "U": int64(reUnicode), "UNICODE": int64(reUnicode), "X": int64(reVerbose), "VERBOSE": int64(reVerbose), "A": int64(reASCII), "ASCII": int64(reASCII), "DEBUG": int64(128), "T": int64(1), "TEMPLATE": int64(1), "error": cache.errorClass, "PatternError": cache.errorClass}
	ns["compile"] = bi("compile", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		values, err := reArgs("compile", args, kw, []string{"pattern", "flags"}, 1, []Value{int64(0)})
		if err != nil {
			return nil, err
		}
		flags, err := reInteger(values[1], "flags")
		if err != nil {
			return nil, err
		}
		c, err := cache.get(values[0], flags)
		if err != nil {
			return nil, err
		}
		return c.object, nil
	})
	for _, operation := range []string{"search", "match", "fullmatch", "findall", "finditer", "split", "sub", "subn"} {
		name := operation
		ns[name] = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			names := []string{"pattern", "string", "flags"}
			required := 2
			defaults := []Value{int64(0)}
			if name == "split" {
				names = []string{"pattern", "string", "maxsplit", "flags"}
				defaults = []Value{int64(0), int64(0)}
			}
			if name == "sub" || name == "subn" {
				names = []string{"pattern", "repl", "string", "count", "flags"}
				required = 3
				defaults = []Value{int64(0), int64(0)}
			}
			values, err := reArgs(name, args, kw, names, required, defaults)
			if err != nil {
				return nil, err
			}
			flags, err := reInteger(values[len(values)-1], "flags")
			if err != nil {
				return nil, err
			}
			c, err := cache.get(values[0], flags)
			if err != nil {
				return nil, err
			}
			return c.invoke(vm, name, values[1:len(values)-1], nil)
		})
	}
	ns["escape"] = bi("escape", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		values, err := reArgs("escape", args, kw, []string{"pattern"}, 1, nil)
		if err != nil {
			return nil, err
		}
		r, binary, err := reText(values[0])
		if err != nil {
			return nil, err
		}
		out := make([]rune, 0, len(r))
		for _, ch := range r {
			if strings.ContainsRune("()[]{}?*+-|^$\\.&~# \t\n\r\v\f", ch) {
				out = append(out, '\\')
			}
			out = append(out, ch)
		}
		return reValue(out, binary), nil
	})
	ns["purge"] = bi("purge", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := noArgs("purge", args, kw); err != nil {
			return nil, err
		}
		clear(cache.m)
		return None, nil
	})
	for _, name := range []string{"Pattern", "Match"} {
		kind := "re." + name
		typ := &BuiltinType{Name: kind, ctor: func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			return nil, newError("TypeError", "cannot create regex objects directly")
		}}
		ns[name] = typ
		vm.types[kind] = typ
	}
	return &NativeModule{Name: "re", NS: ns}
}
