package interp

import (
	"reflect"
	"testing"
)

func nativeReCompile(t *testing.T, pattern Value, flags int64) *compiledRe {
	t.Helper()
	cache := &reCache{m: map[string]*compiledRe{}}
	compiled, err := cache.get(pattern, flags)
	if err != nil {
		t.Fatal(err)
	}
	return compiled
}

func nativeReLocations(t *testing.T, pattern Value, flags int64, input Value) [][]int {
	t.Helper()
	compiled := nativeReCompile(t, pattern, flags)
	text, _, err := reText(input)
	if err != nil {
		t.Fatal(err)
	}
	it := &reMatchIterator{vm: &VM{maxSteps: 1000000}, c: compiled, original: input, text: text, end: len(text)}
	var result [][]int
	for {
		loc, err := it.location()
		if err != nil {
			t.Fatal(err)
		}
		if loc == nil {
			return result
		}
		result = append(result, loc[:len(loc)-1])
	}
}

func TestNativeRegexMatching(t *testing.T) {
	tests := []struct {
		name    string
		pattern Value
		flags   int64
		input   Value
		want    [][]int
	}{
		{"lazy empty alternatives", `.*?`, 0, "ab", [][]int{{0, 0}, {0, 1}, {1, 1}, {1, 2}, {2, 2}}},
		{"empty after nonempty", `a*`, 0, "aa", [][]int{{0, 2}, {2, 2}}},
		{"alternative after empty", `|a`, 0, "a", [][]int{{0, 0}, {0, 1}, {1, 1}}},
		{"nullable repeated capture", `(a?)*`, 0, "a", [][]int{{0, 1, 1, 1}, {1, 1, 1, 1}}},
		{"nested nullable repetition", `(a*)*`, 0, "a", [][]int{{0, 1, 1, 1}, {1, 1, 1, 1}}},
		{"dollar before final newline", `$`, 0, "a\n", [][]int{{1, 1}, {2, 2}}},
		{"unicode word", `\w+`, 0, "é٣_!", [][]int{{0, 3}}},
		{"ascii scoped word", `(?a:\w+)`, 0, "é٣_a", [][]int{{2, 4}}},
		{"unicode boundary", `\b.\b`, 0, "é!", [][]int{{0, 1}}},
		{"python whitespace", `\s`, 0, "\x1c\u0085", [][]int{{0, 1}, {1, 2}}},
		{"bytes whitespace", []byte(`\s`), 0, []byte{0x1c, 0x85, ' '}, [][]int{{2, 3}}},
		{"bytes all values", []byte(`[^\x00]+`), 0, []byte{0xff, 0x80, 0, 'x'}, [][]int{{0, 2}, {3, 4}}},
		{"unicode ignorecase", `[a-z]+`, reIgnoreCase, "İıſK", [][]int{{0, 4}}},
		{"ascii ignorecase", `(?ai:[a-z]+)`, 0, "İıſKAb", [][]int{{4, 6}}},
		{"negated ignorecase", `[^a]`, reIgnoreCase, "aAb", [][]int{{2, 3}}},
		{"word category is not case folded", `[\w]`, reIgnoreCase, "\u0345", nil},
		{"nonword category is not case folded", `[\W]`, reIgnoreCase, "\u0345", [][]int{{0, 1}}},
		{"complementary categories", `[\s\S]`, 0, "\n", [][]int{{0, 1}}},
		{"literal caret exclusion", `[^^]`, 0, "^a", [][]int{{1, 2}}},
		{"surrogate dot", `.`, 0, pyRuneString(0xd800), [][]int{{0, 1}}},
		{"surrogate literal", pyRuneString(0xd800), 0, pyRuneString(0xd800), [][]int{{0, 1}}},
		{"surrogate class", "[" + pyRuneString(0xd800) + "]", 0, pyRuneString(0xd800), [][]int{{0, 1}}},
		{"verbose scoped literal", " a # comment\n (?-x: b)", reVerbose, "a b", [][]int{{0, 3}}},
		{"verbose and dotall", " a # comment\n . b", reVerbose | reDotAll, "a\nb", [][]int{{0, 3}}},
		{"named captures", `(?P<word>\w+)(?P<punct>!)?`, 0, "é! x", [][]int{{0, 2, 0, 1, 1, 2}, {3, 4, 3, 4, -1, -1}}},
		{"lazy capture", `(.*?)(:)`, 0, "ab:c:", [][]int{{0, 3, 0, 2, 2, 3}, {3, 5, 3, 4, 4, 5}}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := nativeReLocations(t, test.pattern, test.flags, test.input)
			if !reflect.DeepEqual(got, test.want) {
				t.Fatalf("locations = %v; want %v", got, test.want)
			}
		})
	}
}

func TestNativeRegexBoundsAndGroups(t *testing.T) {
	vm := &VM{maxSteps: 1000000}
	compiled := nativeReCompile(t, `(?P<letter>é)(x)?$`, 0)
	value, err := compiled.invoke(vm, "search", []Value{"aé\nz", int64(1), int64(3)}, nil)
	if err != nil {
		t.Fatal(err)
	}
	match, ok := value.(*NativeObject)
	if !ok {
		t.Fatalf("search = %v; want match", value)
	}
	got, err := match.methods["group"].Fn(vm, []Value{"letter", int64(2)}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got.(*Tuple).Items, []Value{"é", None}) {
		t.Fatalf("group = %#v", got)
	}
	got, err = match.methods["span"].Fn(vm, []Value{"letter"}, nil)
	if err != nil || !reflect.DeepEqual(got.(*Tuple).Items, []Value{int64(1), int64(2)}) {
		t.Fatalf("span = %#v, %v", got, err)
	}
	anchored := nativeReCompile(t, `^é`, 0)
	got, err = anchored.invoke(vm, "match", []Value{"aé", int64(1)}, nil)
	if err != nil || !reIsNone(got) {
		t.Fatalf("anchored match = %#v, %v", got, err)
	}
	boundary := nativeReCompile(t, `\bé`, 0)
	got, err = boundary.invoke(vm, "match", []Value{"aé", int64(1)}, nil)
	if err != nil || !reIsNone(got) {
		t.Fatalf("boundary match = %#v, %v", got, err)
	}
}

func TestNativeRegexSubstitution(t *testing.T) {
	vm := &VM{maxSteps: 1000000}
	compiled := nativeReCompile(t, `(?P<letter>[a-z])`, 0)
	got, err := compiled.invoke(vm, "subn", []Value{`<\g<letter>>`, "a1b"}, nil)
	if err != nil || !reflect.DeepEqual(got.(*Tuple).Items, []Value{"<a>1<b>", int64(2)}) {
		t.Fatalf("subn = %#v, %v", got, err)
	}
	callback := bi("replacement", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		match := args[0].(*NativeObject)
		value, err := match.methods["group"].Fn(vm, []Value{int64(0)}, nil)
		if err != nil {
			return nil, err
		}
		return "[" + value.(string) + "]", nil
	})
	got, err = compiled.invoke(vm, "sub", []Value{callback, "a1b"}, nil)
	if err != nil || got != "[a]1[b]" {
		t.Fatalf("callable sub = %#v, %v", got, err)
	}
	got, err = compiled.invoke(vm, "sub", []Value{`\g<missing>`, "123"}, nil)
	if err == nil {
		t.Fatalf("invalid template accepted: %#v", got)
	}
}

func TestNativeRegexRejectsUnsupportedConstructs(t *testing.T) {
	for _, pattern := range []string{`(?<=a+)b`, `(?<=a|bc)d`, `(a\1)`, `\1(a)`, `(?P=x)`, `(?(2)a|b)(x)`, `(?(1)a|b|c)(x)`, `a++`, `(?au:a)`} {
		cache := &reCache{m: map[string]*compiledRe{}}
		if _, err := cache.get(pattern, 0); err == nil {
			t.Errorf("accepted unsupported pattern %q", pattern)
		}
	}
}

func TestNativeRegexConsumesFuel(t *testing.T) {
	compiled := nativeReCompile(t, `a+`, 0)
	_, err := compiled.invoke(&VM{maxSteps: 5}, "match", []Value{"aaaa"}, nil)
	if _, ok := err.(*Trap); !ok {
		t.Fatalf("matching did not propagate fuel exhaustion: %v", err)
	}
}

func TestNativeRegexLookaroundReferencesAndAtomicGroups(t *testing.T) {
	tests := []struct {
		name    string
		pattern string
		input   string
		want    [][]int
	}{
		{"positive lookahead", `a(?=b)`, "ab ac", [][]int{{0, 1}}},
		{"negative lookahead", `a(?!b)`, "ab ac", [][]int{{3, 4}}},
		{"positive lookbehind", `(?<=a)b`, "ab cb", [][]int{{1, 2}}},
		{"negative lookbehind", `(?<!a)b`, "ab cb", [][]int{{4, 5}}},
		{"equal width lookbehind branches", `(?<=ab|cd)e`, "abe cde", [][]int{{2, 3}, {6, 7}}},
		{"lookahead capture commit", `(?=(a+))a`, "aa", [][]int{{0, 1, 0, 2}, {1, 2, 1, 2}}},
		{"negative lookahead discards captures", `(?!(a))b`, "b", [][]int{{0, 1, -1, -1}}},
		{"lookbehind right context", `(?<=(a)(?=b))b`, "ab", [][]int{{1, 2, 0, 1}}},
		{"numeric references", `(a|b)\1`, "aa bb ab", [][]int{{0, 2, 0, 1}, {3, 5, 3, 4}}},
		{"named references", `(?P<word>[a-z]+)-(?P=word)`, "ab-ab xx-y", [][]int{{0, 5, 0, 2}}},
		{"case insensitive references", `(?i:(a)\1)`, "aA", [][]int{{0, 2, 0, 1}}},
		{"backreference dotted I lowercase", `(?i:(i)\1)`, "iİ", [][]int{{0, 2, 0, 1}}},
		{"backreference dotless I not equivalent", `(?i:(i)\1)`, "iı", nil},
		{"backreference final sigma not equivalent", `(?i:(σ)\1)`, "σς", nil},
		{"fixed width group reference lookbehind", `(ab)(?<=\1)c`, "abc", [][]int{{0, 3, 0, 2}}},
		{"atomic group rejects external backtracking", `(?>ab|a)b`, "ab", nil},
		{"atomic group rejects alternative retry", `(?>a|ab)c`, "abc", nil},
		{"conditional participating group", `(a)?(?(1)b|c)`, "ab c", [][]int{{0, 2, 0, 1}, {3, 4, -1, -1}}},
		{"conditional with outer alternation", `(a)?(?(1)b)|c`, "ab c", [][]int{{0, 2, 0, 1}, {2, 2, -1, -1}, {3, 3, -1, -1}, {3, 4, -1, -1}, {4, 4, -1, -1}}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := nativeReLocations(t, test.pattern, 0, test.input)
			if !reflect.DeepEqual(got, test.want) {
				t.Fatalf("locations = %v; want %v", got, test.want)
			}
		})
	}
}

func TestNativeRegexUpstreamTextwrapSeparator(t *testing.T) {
	// These fragments and the separator grammar are from CPython textwrap.
	wordPunct := `[\w!"'&.,?]`
	letter := `[^\d\W]`
	whitespace := `[\t\n\v\f\r ]`
	nowhitespace := `[^\t\n\v\f\r ]`
	pattern := `(
		` + whitespace + `+
		| (?<=` + wordPunct + `) -{2,} (?=\w)
		| ` + nowhitespace + `+? (?:
			-(?: (?<=` + letter + `{2}-) | (?<=` + letter + `-` + letter + `-))
			(?= ` + letter + ` -? ` + letter + `)
			| (?=` + whitespace + `|\Z)
			| (?<=` + wordPunct + `) (?=-{2,}\w)
		)
	)`
	compiled := nativeReCompile(t, pattern, reVerbose)
	value, err := compiled.invoke(&VM{maxSteps: 1000000}, "findall", []Value{"Hello there -- you goof-ball, use the -b option!"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	want := []Value{"Hello", " ", "there", " ", "--", " ", "you", " ", "goof-", "ball,", " ", "use", " ", "the", " ", "-b", " ", "option!"}
	if !reflect.DeepEqual(value.(*List).Items, want) {
		t.Fatalf("chunks = %#v; want %#v", value.(*List).Items, want)
	}
}

func TestNativeRegexAssertionBoundsAndCaptureRollback(t *testing.T) {
	vm := &VM{maxSteps: 1000000}
	compiled := nativeReCompile(t, `(?<=a)b(?=c)`, 0)
	value, err := compiled.invoke(vm, "search", []Value{"abc", int64(1), int64(2)}, nil)
	if err != nil || !reIsNone(value) {
		t.Fatalf("endpos leaked right context: %v, %v", value, err)
	}
	value, err = compiled.invoke(vm, "search", []Value{"abc", int64(1), int64(3)}, nil)
	if err != nil || reIsNone(value) {
		t.Fatalf("pos discarded left context: %v, %v", value, err)
	}
	compiled = nativeReCompile(t, `(?:(?=(a))b|a)(?(1)x|y)`, 0)
	value, err = compiled.invoke(vm, "fullmatch", []Value{"ay"}, nil)
	if err != nil || reIsNone(value) {
		t.Fatalf("failed branch leaked assertion captures: %v, %v", value, err)
	}
}

func TestNativeRegexUpstreamTokenizeTripleStrings(t *testing.T) {
	for _, test := range []struct{ pattern, input string }{
		{`[^'\\]*(?:(?:\\.|'(?!''))[^'\\]*)*'''`, `a''b'''`},
		{`[^"\\]*(?:(?:\\.|"(?!""))[^"\\]*)*"""`, `a""b"""`},
		{`[^'\\]*(?:(?:\\.|'(?!''))[^'\\]*)*'''`, `a\'b'''`},
	} {
		got := nativeReLocations(t, test.pattern, 0, test.input)
		if !reflect.DeepEqual(got, [][]int{{0, 7}}) {
			t.Fatalf("triple string %q: %v", test.input, got)
		}
	}
}

func TestNativeRegexMatchRegs(t *testing.T) {
	tests := []struct {
		name    string
		pattern Value
		input   Value
		want    [][2]int64
	}{
		{"unicode and unmatched", `(é)(x)?`, "aé", [][2]int64{{1, 2}, {1, 2}, {-1, -1}}},
		{"bytes and unmatched", []byte(`(\xff)(x)?`), []byte{'a', 0xff}, [][2]int64{{1, 2}, {1, 2}, {-1, -1}}},
		{"lookahead capture beyond match", `(?=(é.))é`, "aéx", [][2]int64{{1, 2}, {1, 3}}},
		{"lookbehind capture before match", `(?<=(é))x`, "aéx", [][2]int64{{2, 3}, {1, 2}}},
		{"negative assertion unmatched capture", `(?!(x))é`, "é", [][2]int64{{0, 1}, {-1, -1}}},
		{"empty participating capture", `()(x)?`, "", [][2]int64{{0, 0}, {0, 0}, {-1, -1}}},
		{"group zero only", `é`, "aé", [][2]int64{{1, 2}}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			vm := &VM{maxSteps: 1000000}
			compiled := nativeReCompile(t, test.pattern, 0)
			value, err := compiled.invoke(vm, "search", []Value{test.input}, nil)
			if err != nil {
				t.Fatal(err)
			}
			match, ok := value.(*NativeObject)
			if !ok {
				t.Fatalf("search = %v; want match", value)
			}
			registerValue, err := vm.getAttr(match, "regs")
			if err != nil {
				t.Fatal(err)
			}
			regs, ok := registerValue.(*Tuple)
			if !ok || len(regs.Items) != len(test.want) {
				t.Fatalf("regs = %#v; want %v", registerValue, test.want)
			}
			again, err := vm.getAttr(match, "regs")
			if err != nil || again != regs {
				t.Fatalf("regs did not preserve cached identity: %v", err)
			}
			if err := vm.setAttr(match, "regs", None); err == nil {
				t.Fatal("regs attribute accepted assignment")
			}
			if err := vm.setIndex(regs, int64(0), None); err == nil {
				t.Fatal("regs tuple accepted item assignment")
			}
			for group, span := range test.want {
				want := &Tuple{Items: []Value{span[0], span[1]}}
				if !reflect.DeepEqual(regs.Items[group], want) {
					t.Fatalf("regs[%d] = %#v; want %#v", group, regs.Items[group], want)
				}
				if err := vm.setIndex(regs.Items[group], int64(0), int64(7)); err == nil {
					t.Fatalf("regs[%d] span accepted item assignment", group)
				}
				got, err := match.methods["span"].Fn(vm, []Value{int64(group)}, nil)
				if err != nil || !reflect.DeepEqual(got, regs.Items[group]) {
					t.Fatalf("span(%d) = %#v, %v; regs = %#v", group, got, err, regs.Items[group])
				}
				if got == regs.Items[group] {
					t.Fatalf("span(%d) reused cached regs pair", group)
				}
				second, err := match.methods["span"].Fn(vm, []Value{int64(group)}, nil)
				if err != nil || second == got {
					t.Fatalf("span(%d) did not return a fresh tuple: %v", group, err)
				}
			}
		})
	}
}
