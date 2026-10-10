package interp

import (
	"reflect"
	"testing"
	"unicode/utf8"
)

func TestPythonTextRoundTrip(t *testing.T) {
	tests := []struct {
		name  string
		text  string
		runes []rune
	}{
		{"empty", "", []rune{}},
		{"ASCII", "a\x00z", []rune{'a', 0, 'z'}},
		{"scalar boundaries", "\u007f\u0080\u07ff\u0800\ud7ff\ue000\uffff\U00010000\U0010ffff", []rune{0x7f, 0x80, 0x7ff, 0x800, 0xd7ff, 0xe000, 0xffff, 0x10000, 0x10ffff}},
		{"high surrogate", "\xed\xa0\x80", []rune{0xd800}},
		{"low surrogate", "\xed\xbf\xbf", []rune{0xdfff}},
		{"uncombined pair", "\xed\xa0\xbd\xed\xb8\x80", []rune{0xd83d, 0xde00}},
		{"mixed", "a\xed\xa0\x80\U0001f600\xed\xbf\xbf\ufffdz", []rune{'a', 0xd800, 0x1f600, 0xdfff, 0xfffd, 'z'}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := pyRunes(tt.text); !reflect.DeepEqual(got, tt.runes) {
				t.Fatalf("pyRunes(%q) = %x, want %x", tt.text, got, tt.runes)
			}
			if got := pyRuneCount(tt.text); got != len(tt.runes) {
				t.Fatalf("pyRuneCount(%q) = %d, want %d", tt.text, got, len(tt.runes))
			}
			if got := pyString(tt.runes); got != tt.text {
				t.Fatalf("pyString(%x) = %x, want %x", tt.runes, got, tt.text)
			}
			remaining := tt.text
			for _, want := range tt.runes {
				r, size := pyDecodeRuneInString(remaining)
				if r != want || size < 1 || size > len(remaining) {
					t.Fatalf("pyDecodeRuneInString(%x) = (%x, %d), want %x", remaining, r, size, want)
				}
				var buf [utf8.UTFMax]byte
				n := pyEncodeRune(buf[:], r)
				if n != size || string(buf[:n]) != remaining[:size] || pyRuneString(r) != remaining[:size] {
					t.Fatalf("encoding %x did not preserve bytes %x", r, remaining[:size])
				}
				remaining = remaining[size:]
			}
			if r, size := pyDecodeRuneInString(remaining); r != utf8.RuneError || size != 0 {
				t.Fatalf("empty decode = (%x, %d)", r, size)
			}
		})
	}
}

func TestIRStringCodepoints(t *testing.T) {
	points := []any{float64(0xd800), float64(0xdfff), float64(0x1f600)}
	literal := node{"op": "const", "t": "str", "v": "\U000103ff\U0001f600", "codepoints": points}
	got, err := parseConst(literal)
	if err != nil {
		t.Fatal(err)
	}
	want := []rune{0xd800, 0xdfff, 0x1f600}
	if !reflect.DeepEqual(pyRunes(got.(string)), want) {
		t.Fatalf("constant codepoints = %x, want %x", pyRunes(got.(string)), want)
	}
	part := node{"s": "\U000103ff\U0001f600", "codepoints": points}
	for _, original := range []node{
		literal,
		{"op": "fstring", "parts": []any{part}},
		{"op": "funcexpr", "name": "annotation", "annotation_scope": true, "params": node{},
			"body": []any{node{"op": "return", "v": literal}}},
	} {
		writer := &dspbWriter{strings: map[string]int{}}
		writer.node(original)
		if writer.err != nil {
			t.Fatal(writer.err)
		}
		reader := &dspbReader{data: writer.buf.Bytes(), strings: writer.strlist}
		decoded := reader.decNode()
		if reader.err != nil {
			t.Fatal(reader.err)
		}
		dspbCompact(original)
		if !reflect.DeepEqual(decoded, original) {
			t.Fatalf("DSPB round trip = %#v, want %#v", decoded, original)
		}
	}
	for _, invalid := range []any{nil, "text", []any{-1.0}, []any{1114112.0}, []any{1.5}} {
		if _, err := irString(node{"v": "", "codepoints": invalid}, "v"); err == nil {
			t.Fatalf("accepted invalid codepoints %#v", invalid)
		}
	}
}

func TestPythonTextAllSurrogates(t *testing.T) {
	for r := rune(0xd800); r <= 0xdfff; r++ {
		want := string([]byte{0xed, 0x80 | byte(r>>6)&0x3f, 0x80 | byte(r)&0x3f})
		if got := pyRuneString(r); got != want {
			t.Fatalf("pyRuneString(%x) = %x, want %x", r, got, want)
		}
		if got := pyString([]rune{r}); got != want {
			t.Fatalf("pyString([%x]) = %x, want %x", r, got, want)
		}
		if got, size := pyDecodeRuneInString(want); got != r || size != 3 {
			t.Fatalf("pyDecodeRuneInString(%x) = (%x, %d), want (%x, 3)", want, got, size, r)
		}
		if got := pyRunes(want); len(got) != 1 || got[0] != r || pyRuneCount(want) != 1 {
			t.Fatalf("surrogate %x was not one codepoint: %x", r, got)
		}
	}
}

func TestPythonTextMalformedBytes(t *testing.T) {
	tests := []struct {
		text  string
		runes []rune
	}{
		{"\x80", []rune{utf8.RuneError}},
		{"\xff", []rune{utf8.RuneError}},
		{"\xc0\x80", []rune{utf8.RuneError, utf8.RuneError}},
		{"\xed\xa0", []rune{utf8.RuneError, utf8.RuneError}},
		{"\xed\xa0\x7f", []rune{utf8.RuneError, utf8.RuneError, 0x7f}},
		{"\xe0\x80\x80", []rune{utf8.RuneError, utf8.RuneError, utf8.RuneError}},
		{"\xf4\x90\x80\x80", []rune{utf8.RuneError, utf8.RuneError, utf8.RuneError, utf8.RuneError}},
		{"\xf0\x9f\x98", []rune{utf8.RuneError, utf8.RuneError, utf8.RuneError}},
	}
	for _, tt := range tests {
		if got := pyRunes(tt.text); !reflect.DeepEqual(got, tt.runes) {
			t.Fatalf("pyRunes(%x) = %x, want %x", tt.text, got, tt.runes)
		}
		if got := pyRuneCount(tt.text); got != len(tt.runes) {
			t.Fatalf("pyRuneCount(%x) = %d, want %d", tt.text, got, len(tt.runes))
		}
		for i, want := range tt.runes {
			if got, size := pyDecodeRuneInString(tt.text[i:]); got != want || size != 1 {
				t.Fatalf("malformed decode at %d in %x = (%x, %d), want (%x, 1)", i, tt.text, got, size, want)
			}
		}
	}
}

func TestPythonTextOutOfRangeRunes(t *testing.T) {
	for _, r := range []rune{-1, 0x110000, 0x7fffffff} {
		var buf [utf8.UTFMax]byte
		n := pyEncodeRune(buf[:], r)
		if n != 3 || string(buf[:n]) != "\ufffd" || pyRuneString(r) != "\ufffd" || pyString([]rune{r}) != "\ufffd" {
			t.Fatalf("out-of-range rune %x did not encode as RuneError", r)
		}
	}
}

func TestPythonSurrogateStringMethods(t *testing.T) {
	high, low := "\xed\xa0\x80", "\xed\xbf\xbf"
	tests := []struct {
		name string
		text string
		args []Value
		want Value
	}{
		{"lower", "A" + high + "B", nil, "a" + high + "b"},
		{"upper", "a" + high + "b", nil, "A" + high + "B"},
		{"capitalize", high + "ABC", nil, high + "abc"},
		{"title", "a" + high + "b", nil, "A" + high + "B"},
		{"casefold", "A" + high + "B", nil, "a" + high + "b"},
		{"swapcase", "A" + high + "b", nil, "a" + high + "B"},
		{"strip", high + "a" + low, []Value{high}, "a" + low},
		{"rstrip", high + "a" + low, []Value{low}, high + "a"},
		{"strip", high + "a" + high, []Value{"\ufffd"}, high + "a" + high},
		{"replace", high + "x", []Value{"", "-"}, "-" + high + "-x-"},
		{"replace", high + "x", []Value{"", "-", int64(2)}, "-" + high + "-x"},
		{"find", high + low, []Value{low}, int64(1)},
		{"startswith", high + low, []Value{low, int64(1)}, true},
		{"zfill", "-" + high, []Value{int64(4)}, "-00" + high},
		{"center", "x", []Value{int64(3), high}, high + "x" + high},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := strMethod(tt.text, tt.name)(nil, tt.args, nil)
			if err != nil || !reflect.DeepEqual(got, tt.want) {
				t.Fatalf("%s(%x, %v) = (%v, %v), want %v", tt.name, tt.text, tt.args, got, err, tt.want)
			}
		})
	}
	if got := splitLines(high + "\n" + low); !reflect.DeepEqual(got, []string{high, low}) {
		t.Fatalf("splitLines lost surrogate codepoints: %x", got)
	}
	if !builtinMethodNames["str"]["format_map"] {
		t.Fatal("str.format_map is not registered")
	}
}

func TestPythonSurrogateFormatting(t *testing.T) {
	surrogate := "\xed\xa0\x80"
	if got := reprString(surrogate); got != "'\\ud800'" {
		t.Fatalf("reprString = %q", got)
	}
	spec, err := parseFmtSpec(surrogate + "^5.2s")
	if err != nil {
		t.Fatal(err)
	}
	got, err := formatStr("a"+surrogate+"b", spec)
	want := surrogate + "a" + surrogate + surrogate + surrogate
	if err != nil || got != want {
		t.Fatalf("formatStr = (%x, %v), want %x", got, err, want)
	}
}

func TestPythonStrictSurrogateCodecs(t *testing.T) {
	s := "a\xed\xa0\x80\xed\xbf\xbfb"
	for _, encoding := range []string{"utf-8", "ascii", "latin-1"} {
		_, err := encodeStr(s, encoding)
		raised, ok := err.(*Raised)
		if !ok {
			t.Fatalf("%s encode error = %v", encoding, err)
		}
		inst := raised.Exc.(*Instance)
		if inst.Class.Name != "UnicodeEncodeError" || inst.dictValue("object") != s || inst.dictValue("start") != int64(1) || inst.dictValue("end") != int64(3) {
			t.Fatalf("%s encode error metadata: object=%v start=%v end=%v", encoding, inst.dictValue("object"), inst.dictValue("start"), inst.dictValue("end"))
		}
	}
	tests := []struct {
		bytes  string
		end    int64
		reason string
	}{
		{"\xed\xa0\x80", 1, "invalid continuation byte"},
		{"\xed\xa0", 1, "invalid continuation byte"},
		{"\xe2\x82", 2, "unexpected end of data"},
		{"\xe2\x82a", 2, "invalid continuation byte"},
		{"\xff", 1, "invalid start byte"},
	}
	for _, tt := range tests {
		_, err := decodeBytes([]byte(tt.bytes), "utf-8")
		raised, ok := err.(*Raised)
		if !ok {
			t.Fatalf("decode %x error = %v", tt.bytes, err)
		}
		inst := raised.Exc.(*Instance)
		if inst.Class.Name != "UnicodeDecodeError" || inst.dictValue("start") != int64(0) || inst.dictValue("end") != tt.end || inst.dictValue("reason") != tt.reason {
			t.Fatalf("decode %x metadata: start=%v end=%v reason=%v", tt.bytes, inst.dictValue("start"), inst.dictValue("end"), inst.dictValue("reason"))
		}
	}
}

func TestOSErrorAttributeFormatting(t *testing.T) {
	vm := &VM{}
	tests := []struct {
		filename  Value
		filename2 Value
		want      string
	}{
		{None, None, "[Errno 2] No such file or directory"},
		{"missing", None, "[Errno 2] No such file or directory: 'missing'"},
		{"\xed\xa0\x80", "target", "[Errno 2] No such file or directory: '\\ud800' -> 'target'"},
	}
	for _, tt := range tests {
		inst := makeExc("OSError", int64(2), "No such file or directory")
		inst.dictSet("errno", int64(2))
		inst.dictSet("strerror", "No such file or directory")
		inst.dictSet("filename", tt.filename)
		inst.dictSet("filename2", tt.filename2)
		got, err := vm.excStr(inst)
		if err != nil || got != tt.want {
			t.Fatalf("OSError string = (%q, %v), want %q", got, err, tt.want)
		}
	}
	got, err := vm.excStr(makeExc("OSError", "legacy message"))
	if err != nil || got != "legacy message" {
		t.Fatalf("legacy OSError string = (%q, %v)", got, err)
	}
}

func TestPythonNativeCodecErrorHandlers(t *testing.T) {
	vm := &VM{}
	tests := []struct {
		encoding string
		errors   string
		text     string
		want     string
	}{
		{"UTF_8", "ignore", "a\xed\xa0\x80b", "ab"},
		{"utf8", "replace", "a\xed\xa0\x80\xed\xbf\xbfb", "a??b"},
		{"ascii", "backslashreplace", "\u00e9\u1234\U0001f600", `\xe9\u1234\U0001f600`},
		{"latin1", "replace", "\u00e9\u1234", "\xe9?"},
		{"utf-8", "surrogateescape", "a\xed\xb2\x80\xed\xb3\xbfb", "a\x80\xffb"},
		{"utf-8", "surrogatepass", "\xed\xa0\x80", "\xed\xa0\x80"},
		{"cp65001", "strict", "\u00e9", "\xc3\xa9"},
		{"ascii", "unknown-handler", "valid", "valid"},
	}
	for _, tt := range tests {
		got, err := vm.encodeText(tt.text, tt.encoding, tt.errors)
		data, ok := got.([]byte)
		if err != nil || !ok || string(data) != tt.want {
			t.Fatalf("encode(%x, %s, %s) = (%v, %v), want %x", tt.text, tt.encoding, tt.errors, got, err, tt.want)
		}
	}
	decodeTests := []struct {
		encoding string
		errors   string
		data     string
		want     string
	}{
		{"utf8", "ignore", "a\xe2\x82b", "ab"},
		{"utf-8", "replace", "a\xe2\x82b", "a\ufffdb"},
		{"utf8", "backslashreplace", "\xe2\x82", `\xe2\x82`},
		{"ascii", "replace", "\x80\xff", "\ufffd\ufffd"},
		{"ascii", "surrogateescape", "\x80\xff", "\xed\xb2\x80\xed\xb3\xbf"},
		{"utf8", "surrogatepass", "\xed\xa0\x80", "\xed\xa0\x80"},
		{"iso_8859_1", "unknown-handler", "\xff", "\u00ff"},
	}
	for _, tt := range decodeTests {
		got, err := vm.decodeText([]byte(tt.data), tt.encoding, tt.errors)
		if err != nil || got != tt.want {
			t.Fatalf("decode(%x, %s, %s) = (%v, %v), want %x", tt.data, tt.encoding, tt.errors, got, err, tt.want)
		}
	}
	got, err := strMethod("\xed\xa0\x80", "encode")(vm, nil, []KwArg{{Name: "errors", Val: "backslashreplace"}})
	if err != nil || !reflect.DeepEqual(got, []byte(`\ud800`)) {
		t.Fatalf("encode keyword errors = (%v, %v)", got, err)
	}
	got, err = bytesMethod([]byte{0xff}, "decode")(vm, []Value{"ascii"}, []KwArg{{Name: "errors", Val: "replace"}})
	if err != nil || got != "\ufffd" {
		t.Fatalf("decode keyword errors = (%v, %v)", got, err)
	}
}

func TestPythonStringIsPrintable(t *testing.T) {
	vm := &VM{}
	for _, test := range []struct {
		text string
		want bool
	}{
		{"", true}, {"plain text", true}, {"\u0301\U0001f600", true},
		{"\n", false}, {"\u00a0", false}, {"\u2028", false},
		{"\u200b", false}, {"\uffff", false}, {pyRuneString(0xd800), false},
	} {
		got, err := strMethod(test.text, "isprintable")(vm, nil, nil)
		if err != nil || got != test.want {
			t.Fatalf("isprintable(%x) = %v, %v; want %v", test.text, got, err, test.want)
		}
	}
}
