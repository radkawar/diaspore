package interp

import (
	"fmt"
	"regexp/syntax"
	"slices"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

const (
	reIgnoreCase = 2
	reLocale     = 4
	reMultiline  = 8
	reDotAll     = 16
	reUnicode    = 32
	reVerbose    = 64
	reASCII      = 256
)

// The translator preserves Python character sets and scoped flags. Assertions
// absent from RE2 are represented by private, non-user-visible capture slots.
type reTranslator struct {
	s               string
	i               int
	bytes           bool
	serial          int
	prefix          string
	assertions      map[string]string
	global          int64
	asciiExplicit   bool
	explicitModes   int64
	specials        map[string]reSpecial
	groups          int
	groupNames      map[string]int
	closedGroups    map[int]bool
	lookbehindFloor int
	stoppedAtPipe   bool
}

func reRanges(rs []rune) string {
	var b strings.Builder
	b.WriteByte('[')
	for i := 0; i < len(rs); i += 2 {
		fmt.Fprintf(&b, `\x{%x}`, rs[i])
		if rs[i] != rs[i+1] {
			fmt.Fprintf(&b, `-\x{%x}`, rs[i+1])
		}
	}
	b.WriteByte(']')
	if len(rs) == 0 {
		return `[^\x{0}-\x{10ffff}]`
	}
	return b.String()
}

func reParsedRanges(r *syntax.Regexp) ([]rune, error) {
	switch r.Op {
	case syntax.OpLiteral:
		if len(r.Rune) == 1 {
			ch := r.Rune[0]
			if r.Flags&syntax.FoldCase == 0 {
				return []rune{ch, ch}, nil
			}
			chars := []rune{ch}
			for folded := unicode.SimpleFold(ch); folded != ch; folded = unicode.SimpleFold(folded) {
				chars = append(chars, folded)
			}
			slices.Sort(chars)
			ranges := make([]rune, 2*len(chars))
			for i, ch := range chars {
				ranges[2*i], ranges[2*i+1] = ch, ch
			}
			return ranges, nil
		}
	case syntax.OpCharClass:
		return r.Rune, nil
	case syntax.OpAnyChar:
		return []rune{0, unicode.MaxRune}, nil
	case syntax.OpAnyCharNotNL:
		return []rune{0, '\n' - 1, '\n' + 1, unicode.MaxRune}, nil
	case syntax.OpNoMatch:
		return nil, nil
	}
	return nil, reFailure("invalid character class")
}

func reAtom(atom string, flags int64, negate bool) (string, error) {
	r, err := syntax.Parse(atom, syntax.Perl)
	if err != nil {
		return "", reSyntaxDiagnostic(err)
	}
	ranges, err := reParsedRanges(r)
	if err != nil {
		return "", err
	}
	if flags&reIgnoreCase != 0 {
		// Fold the positive set before negating it; folding a complement would
		// incorrectly admit the excluded character through another case.
		var b strings.Builder
		b.WriteString(reRanges(ranges))
		for i := 0; i < len(ranges); i += 2 {
			for ch := ranges[i]; ch <= ranges[i+1]; ch++ {
				if flags&reASCII != 0 {
					if ch >= 'a' && ch <= 'z' {
						fmt.Fprintf(&b, `|\x{%x}`, ch-32)
					}
					if ch >= 'A' && ch <= 'Z' {
						fmt.Fprintf(&b, `|\x{%x}`, ch+32)
					}
				} else {
					for f := unicode.SimpleFold(ch); f != ch; f = unicode.SimpleFold(f) {
						fmt.Fprintf(&b, `|\x{%x}`, f)
					}
					if ch == 'i' || ch == 'I' || ch == '\u0130' || ch == '\u0131' {
						b.WriteString(`|[Ii\x{130}\x{131}]`)
					}
				}
			}
		}
		r, err = syntax.Parse(b.String(), syntax.Perl)
		if err != nil {
			return "", reSyntaxDiagnostic(err)
		}
		ranges, err = reParsedRanges(r)
		if err != nil {
			return "", err
		}
	}
	if negate {
		var inverse []rune
		start := rune(0)
		for i := 0; i < len(ranges); i += 2 {
			if start < ranges[i] {
				inverse = append(inverse, start, ranges[i]-1)
			}
			start = ranges[i+1] + 1
		}
		if start <= unicode.MaxRune {
			inverse = append(inverse, start, unicode.MaxRune)
		}
		ranges = inverse
	}
	return reRanges(ranges), nil
}

func reClassEscape(c byte, flags int64) string {
	ascii := flags&reASCII != 0
	var body string
	switch c | 32 {
	case 'd':
		body = `\p{Nd}`
		if ascii {
			body = `0-9`
		}
	case 'w':
		body = `\p{L}\p{N}_`
		if ascii {
			body = `a-zA-Z0-9_`
		}
	case 's':
		body = `\x09-\x0d\x1c-\x20\x85\x{a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}`
		if ascii {
			body = `\x09-\x0d `
		}
	}
	if c >= 'A' && c <= 'Z' {
		r, _ := syntax.Parse("[^"+body+"]", syntax.Perl)
		return strings.TrimSuffix(strings.TrimPrefix(reRanges(r.Rune), "["), "]")
	}
	return body
}

func (t *reTranslator) assertion(kind string) string {
	name := fmt.Sprintf("%s%d", t.prefix, t.serial)
	t.serial++
	t.assertions[name] = kind
	return "(?P<" + name + ">)"
}

func (t *reTranslator) escape(flags int64, inClass bool) (string, error) {
	escapeStart := t.i
	t.i++
	if t.i == len(t.s) {
		return "", t.errorAt("bad escape (end of pattern)", escapeStart)
	}
	if t.s[t.i] >= utf8.RuneSelf {
		r, size := pyDecodeRuneInString(t.s[t.i:])
		t.i += size
		return fmt.Sprintf(`\x{%x}`, r), nil
	}
	c := t.s[t.i]
	t.i++
	if strings.ContainsRune("dDwWsS", rune(c)) {
		body := reClassEscape(c, flags)
		if inClass {
			return body, nil
		}
		return "[" + body + "]", nil
	}
	if c == 'b' || c == 'B' {
		if inClass {
			if c == 'B' {
				return "", t.errorAt(`bad escape \B`, escapeStart)
			}
			return `\x08`, nil
		}
		kind := string(c)
		if flags&reASCII != 0 {
			kind += "a"
		}
		return t.assertion(kind), nil
	}
	if c == 'A' || c == 'Z' || c == 'z' {
		if inClass {
			return "", t.errorAt("bad escape \\"+string(c), escapeStart)
		}
		if c == 'A' {
			return `\A`, nil
		}
		return `\z`, nil
	}
	if strings.ContainsRune("nrtfav", rune(c)) {
		values := map[byte]rune{'n': '\n', 'r': '\r', 't': '\t', 'f': '\f', 'a': '\a', 'v': '\v'}
		return fmt.Sprintf(`\x{%x}`, values[c]), nil
	}
	if c == 'x' || c == 'u' || c == 'U' {
		n := 2
		if c == 'u' {
			n = 4
		}
		if c == 'U' {
			n = 8
		}
		if t.bytes && c != 'x' {
			return "", t.errorAt("bad escape \\"+string(c), escapeStart)
		}
		digits := t.i
		for t.i < len(t.s) && t.i-digits < n && strings.ContainsRune("0123456789abcdefABCDEF", rune(t.s[t.i])) {
			t.i++
		}
		if t.i-digits != n {
			return "", t.errorAt("incomplete escape "+t.s[escapeStart:t.i], escapeStart)
		}
		v, err := strconv.ParseUint(t.s[digits:t.i], 16, 32)
		if err != nil || v > unicode.MaxRune {
			return "", t.errorAt("bad escape "+t.s[escapeStart:t.i], escapeStart)
		}
		return fmt.Sprintf(`\x{%x}`, v), nil
	}
	if c >= '0' && c <= '9' {
		start := t.i - 1
		octal := c == '0' || inClass || c <= '7' && t.i+1 < len(t.s) && t.s[t.i] >= '0' && t.s[t.i] <= '7' && t.s[t.i+1] >= '0' && t.s[t.i+1] <= '7'
		if !octal {
			if t.i < len(t.s) && t.s[t.i] >= '0' && t.s[t.i] <= '9' {
				t.i++
			}
			return t.reference(t.s[start:t.i], flags, start, escapeStart)
		}
		for t.i < len(t.s) && t.i-start < 3 && t.s[t.i] >= '0' && t.s[t.i] <= '7' {
			t.i++
		}
		v, err := strconv.ParseUint(t.s[start:t.i], 8, 16)
		if err != nil || v > 255 {
			return "", t.errorAt("octal escape value "+t.s[escapeStart:t.i]+" outside of range 0-0o377", escapeStart)
		}
		return fmt.Sprintf(`\x{%x}`, v), nil
	}
	if c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' {
		return "", t.errorAt("bad escape \\"+string(c), escapeStart)
	}
	return fmt.Sprintf(`\x{%x}`, c), nil
}

func (t *reTranslator) translate(flags int64, nested bool) (string, error) {
	return t.translatePart(flags, nested, false)
}

func (t *reTranslator) translatePart(flags int64, nested, stopAtPipe bool) (string, error) {
	var out strings.Builder
	repeatable, repeated := false, false
	quantify := func(text string, start int) error {
		if !repeatable {
			return t.errorAt("nothing to repeat", start)
		}
		if repeated {
			return t.errorAt("multiple repeat", start)
		}
		out.WriteString(text)
		if t.i < len(t.s) && t.s[t.i] == '+' {
			return t.errorAt("possessive quantifiers are not supported", t.i)
		}
		if t.i < len(t.s) && t.s[t.i] == '?' {
			out.WriteByte('?')
			t.i++
		}
		repeated = true
		return nil
	}
	for t.i < len(t.s) {
		c := t.s[t.i]
		if stopAtPipe && c == '|' {
			t.stoppedAtPipe = true
			return out.String(), nil
		}
		if flags&reVerbose != 0 {
			if strings.ContainsRune(" \t\r\n\v\f", rune(c)) {
				t.i++
				continue
			}
			if c == '#' {
				for t.i < len(t.s) && t.s[t.i] != '\n' {
					t.i++
				}
				continue
			}
		}
		if c == ')' {
			if !nested {
				return "", t.errorAt("unbalanced parenthesis", t.i)
			}
			t.i++
			t.stoppedAtPipe = false
			return out.String(), nil
		}
		if c == '(' {
			groupStart := t.i
			t.i++
			opener := "("
			childFlags := flags
			group := 0
			if t.i == len(t.s) || t.s[t.i] != '?' {
				group = t.openGroup("")
			}
			if t.i < len(t.s) && t.s[t.i] == '?' {
				t.i++
				if extension, handled, err := t.specialGroup(flags); handled {
					if err != nil {
						return "", t.groupError(err, groupStart)
					}
					out.WriteString(extension)
					repeatable, repeated = true, false
					continue
				}
				if strings.HasPrefix(t.s[t.i:], "#") {
					e := strings.IndexByte(t.s[t.i:], ')')
					if e < 0 {
						return "", t.errorAt("missing ), unterminated comment", groupStart)
					}
					t.i += e + 1
					continue
				}
				if strings.HasPrefix(t.s[t.i:], "P<") {
					e := strings.IndexByte(t.s[t.i+2:], '>')
					if e < 0 {
						return "", t.errorAt("missing >, unterminated name", t.i+2)
					}
					name := t.s[t.i+2 : t.i+2+e]
					t.i += e + 3
					opener = "(?P<" + name + ">"
					if name == "" {
						return "", t.errorAt("missing group name", t.i-1)
					}
					if !pyIsIdentifier(name) {
						return "", t.errorAt("bad character in group name "+reprString(name), t.i-len(name)-1)
					}
					if _, exists := t.groupNames[name]; exists {
						return "", t.errorAt(fmt.Sprintf("redefinition of group name %s as group %d; was group %d", reprString(name), t.groups+1, t.groupNames[name]), t.i-len(name)-1)
					}
					group = t.openGroup(name)
				} else if t.i < len(t.s) && t.s[t.i] == ':' {
					t.i++
					opener = "(?:"
				} else {
					minus, seen := false, false
					modes := int64(0)
					enabled, disabled := int64(0), int64(0)
					for t.i < len(t.s) {
						ch := t.s[t.i]
						if ch == '-' {
							if minus {
								return "", t.errorAt("missing flag", t.i)
							}
							minus = true
							t.i++
							continue
						}
						bit := int64(0)
						switch ch {
						case 'i':
							bit = reIgnoreCase
						case 'm':
							bit = reMultiline
						case 's':
							bit = reDotAll
						case 'x':
							bit = reVerbose
						case 'a':
							bit = reASCII
						case 'u':
							bit = reUnicode
						case 'L':
							if !t.bytes {
								return "", t.errorAt("bad inline flags: cannot use 'L' flag with a str pattern", t.i+1)
							}
							return "", t.errorAt("LOCALE is not supported", t.i+1)
						}
						if bit == 0 {
							break
						}
						seen = true
						t.i++
						if bit&(reASCII|reUnicode) != 0 {
							modes |= bit
							if modes == reASCII|reUnicode {
								return "", t.errorAt("bad inline flags: flags 'a', 'u' and 'L' are incompatible", t.i)
							}
						}
						if minus {
							disabled |= bit
							if bit&(reASCII|reUnicode) != 0 {
								return "", t.errorAt("bad inline flags: cannot turn off flags 'a', 'u' and 'L'", t.i)
							}
							childFlags &^= bit
						} else {
							enabled |= bit
							if bit == reASCII {
								childFlags &^= reUnicode
							}
							if bit == reUnicode {
								if t.bytes {
									return "", t.errorAt("bad inline flags: cannot use 'u' flag with a bytes pattern", t.i)
								}
								childFlags &^= reASCII
							}
							childFlags |= bit
						}
					}
					if enabled&disabled != 0 {
						return "", t.errorAt("bad inline flags: flag turned on and off", t.i)
					}
					if minus && disabled == 0 {
						return "", t.errorAt("missing flag", t.i)
					}
					if t.i == len(t.s) {
						if !seen {
							return "", t.errorAt("unexpected end of pattern", t.i)
						}
						return "", t.errorAt("missing -, : or )", t.i)
					}
					if !seen {
						return "", t.errorAt("unknown extension ?"+string(t.s[t.i]), groupStart+1)
					}
					if t.s[t.i] == ')' {
						if nested || out.Len() != 0 || minus {
							return "", t.errorAt("global flags not at the start of the expression", groupStart)
						}
						t.explicitModes |= modes
						if t.explicitModes == reASCII|reUnicode {
							return "", newError("ValueError", "ASCII and UNICODE flags are incompatible")
						}
						if modes&reASCII != 0 {
							t.asciiExplicit = true
						}
						t.i++
						flags = childFlags
						t.global = flags
						continue
					}
					if t.s[t.i] != ':' {
						message := "missing -, : or )"
						if minus {
							message = "missing :"
						}
						ch := t.s[t.i]
						if ch >= 'a' && ch <= 'z' || ch >= 'A' && ch <= 'Z' {
							message = "unknown flag"
						}
						return "", t.errorAt(message, t.i)
					}
					t.i++
					opener = "(?:"
				}
			}
			body, err := t.translate(childFlags, true)
			if err != nil {
				return "", t.groupError(err, groupStart)
			}
			out.WriteString(opener + body + ")")
			repeatable, repeated = true, false
			if group > 0 {
				t.closedGroups[group] = true
			}
			continue
		}
		if c == '[' {
			classStart := t.i
			t.i++
			negate := false
			if t.i < len(t.s) && t.s[t.i] == '^' {
				negate = true
				t.i++
			}
			var body, categories strings.Builder
			first := true
			closed := false
			for t.i < len(t.s) {
				if t.s[t.i] == ']' && !first {
					t.i++
					closed = true
					break
				}
				if t.s[t.i] == '\\' {
					category := t.i+1 < len(t.s) && strings.ContainsRune("dDwWsS", rune(t.s[t.i+1]))
					atom, err := t.escape(flags, true)
					if err != nil {
						return "", err
					}
					if category {
						literals := body.String()
						if strings.HasSuffix(literals, "-") && len(literals) > 1 || t.i+1 < len(t.s) && t.s[t.i] == '-' && t.s[t.i+1] != ']' {
							return "", t.classError(reFailure("bad character range"), classStart, flags)
						}
						categories.WriteString(atom)
					} else {
						body.WriteString(atom)
					}
				} else {
					ch, size := pyDecodeRuneInString(t.s[t.i:])
					t.i += size
					if ch >= utf8.RuneSelf {
						fmt.Fprintf(&body, `\x{%x}`, ch)
					} else {
						if ch == ']' || ch == '[' || ch == '^' {
							body.WriteByte('\\')
						}
						body.WriteByte(byte(ch))
					}
				}
				first = false
			}
			if !closed {
				return "", t.errorAt("unterminated character set", classStart)
			}
			var pieces strings.Builder
			if body.Len() > 0 {
				folded, err := reAtom("["+body.String()+"]", flags, false)
				if err != nil {
					return "", t.classError(err, classStart, flags)
				}
				pieces.WriteString(folded[1 : len(folded)-1])
			}
			pieces.WriteString(categories.String())
			atom, err := reAtom("["+pieces.String()+"]", 0, negate)
			if err != nil {
				return "", t.classError(err, classStart, flags)
			}
			out.WriteString(atom)
			repeatable, repeated = true, false
			continue
		}
		if c == '\\' {
			assertion := t.i+1 < len(t.s) && strings.ContainsRune("AbBZz", rune(t.s[t.i+1]))
			atom, err := t.escape(flags, false)
			if err != nil {
				return "", err
			}
			if strings.HasPrefix(atom, `\x{`) {
				atom, err = reAtom(atom, flags, false)
				if err != nil {
					return "", err
				}
			}
			out.WriteString(atom)
			repeatable, repeated = !assertion, false
			continue
		}
		t.i++
		switch c {
		case '.':
			repeatable, repeated = true, false
			if flags&reDotAll != 0 {
				out.WriteString("(?s:.)")
			} else {
				out.WriteByte('.')
			}
		case '^':
			repeatable, repeated = false, false
			if flags&reMultiline != 0 {
				out.WriteString("(?m:^)")
			} else {
				out.WriteString(`\A`)
			}
		case '$':
			repeatable, repeated = false, false
			if flags&reMultiline != 0 {
				out.WriteString("(?m:$)")
			} else {
				out.WriteString(t.assertion("$"))
			}
		case '*', '+', '?':
			if err := quantify(string(c), t.i-1); err != nil {
				return "", err
			}
		case '{':
			repeatStart := t.i - 1
			end := strings.IndexByte(t.s[t.i:], '}')
			if end >= 0 {
				body := t.s[t.i : t.i+end]
				valid := len(body) > 0 && strings.Count(body, ",") <= 1
				for _, ch := range body {
					if ch != ',' && (ch < '0' || ch > '9') {
						valid = false
					}
				}
				if valid {
					bounds := strings.Split(body, ",")
					low, high := uint64(0), uint64(0)
					var err error
					if bounds[0] != "" {
						low, err = strconv.ParseUint(bounds[0], 10, 32)
					}
					if err != nil || low >= 1<<32-1 {
						return "", newError("OverflowError", "the repetition number is too large")
					}
					if len(bounds) == 2 && bounds[1] != "" {
						high, err = strconv.ParseUint(bounds[1], 10, 32)
						if err != nil || high >= 1<<32-1 {
							return "", newError("OverflowError", "the repetition number is too large")
						}
						if high < low {
							return "", t.errorAt("min repeat greater than max repeat", repeatStart+1)
						}
					}
					if strings.HasPrefix(body, ",") {
						body = "0" + body
					}
					t.i += end + 1
					if err := quantify("{"+body+"}", repeatStart); err != nil {
						return "", err
					}
					continue
				}
			}
			out.WriteString(`\{`)
			repeatable, repeated = true, false
		case '|':
			repeatable, repeated = false, false
			out.WriteByte(c)
		default:
			repeatable, repeated = true, false
			// The source is UTF-8, including the Latin-1 projection of bytes.
			start := t.i - 1
			r, size := pyDecodeRuneInString(t.s[start:])
			t.i = start + size
			atom, err := reAtom(fmt.Sprintf(`\x{%x}`, r), flags, false)
			if err != nil {
				return "", err
			}
			out.WriteString(atom)
		}
	}
	if nested {
		return "", reFailure("missing ), unterminated subpattern")
	}
	return out.String(), nil
}

type reAssertion struct{ kind string }
type reState struct {
	pc  uint32
	pos int
}
type reFrame struct {
	state   reState
	slot    int
	old     int
	last    int
	restore bool
}

// run evaluates Go regexp instructions with Python's ordered backtracking
// and empty-repeat guards. Every instruction is charged to VM fuel.
func (c *compiledRe) run(vm *VM, text []rune, start, end int, full, nonempty bool) ([]int, error) {
	caps := make([]int, c.prog.NumCap+c.guards)
	for i := range caps {
		caps[i] = -1
	}
	caps[0] = start
	target := -1
	if full {
		target = end
	}
	caps, last, finish, err := c.execute(vm, text, uint32(c.prog.Start), start, end, target, nonempty, caps, -1)
	if err != nil || caps == nil {
		return nil, err
	}
	loc := make([]int, 2*len(c.slots)+1)
	caps[1] = finish
	for group, slot := range c.slots {
		loc[2*group], loc[2*group+1] = caps[2*slot], caps[2*slot+1]
	}
	loc[len(loc)-1] = last
	return loc, nil
}

// execute keeps backtracking local to each assertion or atomic group. Positive
// assertions commit their capture changes, but never their consumed position.
func (c *compiledRe) execute(vm *VM, text []rune, entry uint32, start, end, target int, nonempty bool, caps []int, last int) ([]int, int, int, error) {
	stack := []reFrame{{state: reState{entry, start}}}
	for len(stack) > 0 {
		frame := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if frame.restore {
			caps[frame.slot], last = frame.old, frame.last
			continue
		}
		pc, pos := frame.state.pc, frame.state.pos
		for {
			if err := vm.fuel(); err != nil {
				return nil, 0, 0, err
			}
			if special, ok := c.specials[pc]; ok {
				switch special.kind {
				case "reference":
					left, right := caps[2*special.slot], caps[2*special.slot+1]
					if left < 0 || right < left || right-left > end-pos {
						break
					}
					matched := true
					for offset := range right - left {
						if err := vm.fuel(); err != nil {
							return nil, 0, 0, err
						}
						if !reFoldEqual(text[left+offset], text[pos+offset], special.flags) {
							matched = false
							break
						}
					}
					if !matched {
						break
					}
					pos += right - left
					pc = c.prog.Inst[pc].Out
					continue
				case "conditional":
					if caps[2*special.slot] >= 0 && caps[2*special.slot+1] >= 0 {
						pc = special.entry
					} else {
						pc = special.alternate
					}
					continue
				default:
					begin, requiredEnd := pos, -1
					behind := special.kind == "behind" || special.kind == "notbehind"
					negative := special.kind == "notahead" || special.kind == "notbehind"
					if behind {
						begin, requiredEnd = pos-special.width, pos
					}
					var result []int
					resultLast, finish := last, pos
					if begin >= 0 {
						var err error
						result, resultLast, finish, err = c.execute(vm, text, special.entry, begin, end, requiredEnd, false, append([]int(nil), caps...), last)
						if err != nil {
							return nil, 0, 0, err
						}
					}
					if negative {
						if result != nil {
							break
						}
					} else {
						if result == nil {
							break
						}
						stack = append(stack, reFrame{slot: 0, old: caps[0], last: last, restore: true})
						for slot, value := range result {
							if value != caps[slot] {
								stack = append(stack, reFrame{slot: slot, old: caps[slot], last: last, restore: true})
								caps[slot] = value
							}
						}
						last = resultLast
						if special.kind == "atomic" {
							pos = finish
						}
					}
					pc = c.prog.Inst[pc].Out
					continue
				}
				break
			}
			if loop, ok := c.loops[pc]; ok && caps[loop.slot] == pos {
				pc = loop.exit
				continue
			}
			in := &c.prog.Inst[pc]
			switch in.Op {
			case syntax.InstFail:
			case syntax.InstAlt, syntax.InstAltMatch:
				stack = append(stack, reFrame{state: reState{in.Arg, pos}})
				pc = in.Out
				continue
			case syntax.InstNop:
				if progress, ok := c.progress[pc]; ok {
					stack = append(stack, reFrame{slot: progress.slot, old: caps[progress.slot], last: last, restore: true})
					caps[progress.slot] = pos
					if progress.reset {
						caps[progress.slot] = -1
					}
				}
				pc = in.Out
				continue
			case syntax.InstCapture:
				if assertion, ok := c.assertions[int(in.Arg)/2]; ok {
					if in.Arg%2 == 0 && !reAssert(assertion.kind, text, pos, end) {
						break
					}
					pc = in.Out
					continue
				}
				stack = append(stack, reFrame{slot: int(in.Arg), old: caps[in.Arg], last: last, restore: true})
				caps[in.Arg] = pos
				if in.Arg%2 == 1 {
					last = c.public[int(in.Arg)/2]
				}
				pc = in.Out
				continue
			case syntax.InstEmptyWidth:
				before, after := rune(-1), rune(-1)
				if pos > 0 {
					before = text[pos-1]
				}
				if pos < end {
					after = text[pos]
				}
				if syntax.EmptyOpContext(before, after)&syntax.EmptyOp(in.Arg) != syntax.EmptyOp(in.Arg) {
					break
				}
				pc = in.Out
				continue
			case syntax.InstRune, syntax.InstRune1:
				if pos >= end || !in.MatchRune(text[pos]) {
					break
				}
				pos++
				pc = in.Out
				continue
			case syntax.InstRuneAny, syntax.InstRuneAnyNotNL:
				if pos >= end || in.Op == syntax.InstRuneAnyNotNL && text[pos] == '\n' {
					break
				}
				pos++
				pc = in.Out
				continue
			case syntax.InstMatch:
				if target >= 0 && pos != target || nonempty && pos == start {
					break
				}
				return caps, last, pos, nil
			}
			break
		}
	}
	return nil, 0, 0, nil
}

func reAssert(kind string, s []rune, pos, end int) bool {
	if kind == "$" {
		return pos == end || pos == end-1 && s[pos] == '\n'
	}
	ascii := strings.HasSuffix(kind, "a")
	word := func(r rune) bool {
		if r == '_' {
			return true
		}
		if ascii {
			return r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9'
		}
		return unicode.IsLetter(r) || unicode.IsNumber(r)
	}
	left, right := false, false
	if pos > 0 {
		left = word(s[pos-1])
	}
	if pos < end {
		right = word(s[pos])
	}
	if kind[0] == 'b' {
		return left != right
	}
	return left == right && end > 0
}
