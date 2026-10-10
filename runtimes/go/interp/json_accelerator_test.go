package interp

import (
	"math"
	"math/big"
	"reflect"
	"strings"
	"testing"
)

func acceleratorTestVM() *VM {
	return New(map[string]any{
		"diaspore": float64(1), "main": "test",
		"modules": map[string]any{"test": map[string]any{"body": []any{}}},
	})
}

func acceleratorCall(t testing.TB, vm *VM, fn Value, args ...Value) Value {
	t.Helper()
	result, err := vm.call(fn, args, nil)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func acceleratorEncoderArgs(module *NativeModule) []Value {
	return []Value{NewDict(), None, module.NS["encode_basestring_ascii"], None, ": ", ", ", false, false, true}
}

func acceleratorEncode(t testing.TB, vm *VM, encoder, value Value) string {
	t.Helper()
	result := acceleratorCall(t, vm, encoder, value, int64(0))
	chunks, ok := result.(*Tuple)
	if !ok || len(chunks.Items) != 1 {
		t.Fatalf("encoder result = %#v; want one-element tuple", result)
	}
	text, ok := chunks.Items[0].(string)
	if !ok {
		t.Fatalf("encoder chunk = %#v; want string", chunks.Items[0])
	}
	return text
}

func acceleratorError(t *testing.T, err error, class, message string) *Instance {
	t.Helper()
	raised, ok := err.(*Raised)
	if !ok {
		t.Fatalf("error = %v; want %s", err, class)
	}
	instance, ok := raised.Exc.(*Instance)
	if !ok || instance.Class.Name != class {
		t.Fatalf("error = %v; want %s", err, class)
	}
	if message != "" && plainExcStr(instance) != message {
		t.Fatalf("message = %q; want %q", plainExcStr(instance), message)
	}
	return instance
}

func TestJSONAcceleratorEncodeOptions(t *testing.T) {
	vm := acceleratorTestVM()
	module := vm.natives["_json"]
	dict := NewDict()
	dict.SetStr("z", &Tuple{Items: []Value{true, false, None, math.Copysign(0, -1)}})
	dict.SetStr("a", "é😀"+pyRuneString(0xd800))
	for _, test := range []struct {
		name            string
		ascii, sorted   bool
		key, item, want string
	}{
		{"insertion order", true, false, ": ", ", ", `{"z": [true, false, null, -0.0], "a": "\u00e9\ud83d\ude00\ud800"}`},
		{"sorted compact", true, true, ":", ",", `{"a":"\u00e9\ud83d\ude00\ud800","z":[true,false,null,-0.0]}`},
		{"unicode custom separators", false, false, "=", "|", "{\"z\"=[true|false|null|-0.0]|\"a\"=\"é😀" + pyRuneString(0xd800) + "\"}"},
	} {
		t.Run(test.name, func(t *testing.T) {
			args := acceleratorEncoderArgs(module)
			if !test.ascii {
				args[2] = module.NS["encode_basestring"]
			}
			args[4], args[5], args[6] = test.key, test.item, test.sorted
			encoder := acceleratorCall(t, vm, module.NS["make_encoder"], args...)
			if got := acceleratorEncode(t, vm, encoder, dict); got != test.want {
				t.Fatalf("encode = %q; want %q", got, test.want)
			}
			if markers := args[0].(*Dict); markers.Len() != 0 {
				t.Fatal("successful encoding retained circular markers")
			}
		})
	}
}

func TestJSONAcceleratorKeys(t *testing.T) {
	vm := acceleratorTestVM()
	module := vm.natives["_json"]
	dict := NewDict()
	for _, key := range []Value{int64(2), false, None, 1.5, math.Inf(1), &Tuple{Items: []Value{int64(1)}}, "last"} {
		if err := dict.Set(key, int64(0)); err != nil {
			t.Fatal(err)
		}
	}
	args := acceleratorEncoderArgs(module)
	encoder := acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	_, err := vm.call(encoder, []Value{dict, int64(0)}, nil)
	acceleratorError(t, err, "TypeError", "keys must be str, int, float, bool or None, not tuple")
	args[0], args[7] = NewDict(), true
	encoder = acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	want := `{"2": 0, "false": 0, "null": 0, "1.5": 0, "Infinity": 0, "last": 0}`
	if got := acceleratorEncode(t, vm, encoder, dict); got != want {
		t.Fatalf("encode = %q; want %q", got, want)
	}
	args[0], args[6] = NewDict(), true
	encoder = acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	_, err = vm.call(encoder, []Value{dict, int64(0)}, nil)
	acceleratorError(t, err, "TypeError", "") // Sorting precedes skipkeys and key conversion.
	onlySkipped := NewDict()
	if err := onlySkipped.Set(&Tuple{}, None); err != nil {
		t.Fatal(err)
	}
	args[0], args[6] = NewDict(), false
	encoder = acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	if got := acceleratorEncode(t, vm, encoder, onlySkipped); got != "{}" {
		t.Fatalf("skipped keys = %q", got)
	}
}

func TestJSONAcceleratorNumbers(t *testing.T) {
	vm := acceleratorTestVM()
	module := vm.natives["_json"]
	args := acceleratorEncoderArgs(module)
	encoder := acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	large, _ := new(big.Int).SetString("1234567890123456789012345678901234567890", 10)
	value := &List{Items: []Value{large, 1.0, 1e-7, 1e20, math.NaN(), math.Inf(1), math.Inf(-1)}}
	want := `[1234567890123456789012345678901234567890, 1.0, 1e-07, 1e+20, NaN, Infinity, -Infinity]`
	if got := acceleratorEncode(t, vm, encoder, value); got != want {
		t.Fatalf("numbers = %q; want %q", got, want)
	}
	for _, test := range []struct {
		value float64
		text  string
	}{{math.NaN(), "nan"}, {math.Inf(1), "inf"}, {math.Inf(-1), "-inf"}} {
		args[0], args[8] = NewDict(), false
		encoder = acceleratorCall(t, vm, module.NS["make_encoder"], args...)
		_, err := vm.call(encoder, []Value{test.value, int64(0)}, nil)
		acceleratorError(t, err, "ValueError", "Out of range float values are not JSON compliant: "+test.text)
	}
}

func TestJSONAcceleratorDefaultAndCircular(t *testing.T) {
	vm := acceleratorTestVM()
	module := vm.natives["_json"]
	args := acceleratorEncoderArgs(module)
	object := newInstance(objectClass, nil)
	calls := 0
	args[1] = bi("default", func(_ *VM, values []Value, _ []KwArg) (Value, error) {
		calls++
		if values[0] != object {
			t.Fatalf("unexpected default argument: %#v", values[0])
		}
		return &List{Items: []Value{"converted", int64(42)}}, nil
	})
	encoder := acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	if got := acceleratorEncode(t, vm, encoder, &List{Items: []Value{object, object}}); got != `[["converted", 42], ["converted", 42]]` || calls != 2 {
		t.Fatalf("default result = %q, calls = %d", got, calls)
	}
	cyclic := &List{}
	cyclic.Items = []Value{cyclic}
	_, err := vm.call(encoder, []Value{cyclic, int64(0)}, nil)
	acceleratorError(t, err, "ValueError", "Circular reference detected")
	args[0] = NewDict()
	args[1] = bi("default", func(_ *VM, values []Value, _ []KwArg) (Value, error) { return values[0], nil })
	encoder = acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	_, err = vm.call(encoder, []Value{object, int64(0)}, nil)
	acceleratorError(t, err, "ValueError", "Circular reference detected")
	args[0] = NewDict()
	sentinel := newError("RuntimeError", "default failed")
	args[1] = bi("default", func(_ *VM, _ []Value, _ []KwArg) (Value, error) { return nil, sentinel })
	encoder = acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	if _, err = vm.call(encoder, []Value{object, int64(0)}, nil); err != sentinel {
		t.Fatalf("default exception = %v", err)
	}
}

func TestJSONAcceleratorCustomStringEncoder(t *testing.T) {
	vm := acceleratorTestVM()
	module := vm.natives["_json"]
	args := acceleratorEncoderArgs(module)
	var calls []string
	// A matching name must not activate the native encoder identity fast path.
	args[2] = bi("encode_basestring_ascii", func(_ *VM, values []Value, _ []KwArg) (Value, error) {
		text := values[0].(string)
		calls = append(calls, text)
		return "<" + text + ">", nil
	})
	dict := NewDict()
	dict.SetStr("key", &List{Items: []Value{"value", int64(3), false}})
	encoder := acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	if got := acceleratorEncode(t, vm, encoder, dict); got != `{<key>: [<value>, 3, false]}` {
		t.Fatalf("custom encoder = %q", got)
	}
	if !reflect.DeepEqual(calls, []string{"key", "value"}) {
		t.Fatalf("string callbacks = %v", calls)
	}
	args[2] = bi("encoder", func(_ *VM, _ []Value, _ []KwArg) (Value, error) { return int64(1), nil })
	encoder = acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	_, err := vm.call(encoder, []Value{"text", int64(0)}, nil)
	acceleratorError(t, err, "TypeError", "encoder() must return a string, not int")
}

func TestJSONAcceleratorScanstring(t *testing.T) {
	vm := acceleratorTestVM()
	module := vm.natives["_json"]
	for _, test := range []struct {
		name, input, want string
		start, end        int64
		strict            bool
	}{
		{"escapes", `"a\b\f\n\r\t\/\\\""tail`, "a\b\f\n\r\t/\\\"", 1, 19, true},
		{"unicode index", `é"😀\ud800\udc00\udfff"x`, "😀𐀀" + pyRuneString(0xdfff), 2, 22, true},
		{"unpaired surrogates", `"\ud800x\udfff"`, pyRuneString(0xd800) + "x" + pyRuneString(0xdfff), 1, 15, true},
		{"non-strict controls", "\"a\x00\nb\"", "a\x00\nb", 1, 6, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			got := acceleratorCall(t, vm, module.NS["scanstring"], test.input, test.start, test.strict)
			want := &Tuple{Items: []Value{test.want, test.end}}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("scanstring = %#v; want %#v", got, want)
			}
		})
	}
	for _, name := range []string{"encode_basestring", "encode_basestring_ascii"} {
		_, err := vm.call(module.NS[name], []Value{int64(1)}, nil)
		acceleratorError(t, err, "TypeError", "")
	}
}

func acceleratorScannerContext(vm *VM) *Instance {
	class := &Class{Name: "ScannerContext", NS: map[string]Value{}, Bases: []*Class{objectClass}, vm: vm}
	class.MRO = []*Class{class, objectClass}
	instance := newInstance(class, nil)
	instance.dictSet("strict", true)
	instance.dictSet("object_hook", None)
	instance.dictSet("object_pairs_hook", None)
	instance.dictSet("parse_int", vm.builtins["int"])
	instance.dictSet("parse_float", vm.builtins["float"])
	instance.dictSet("parse_constant", bi("parse_constant", func(_ *VM, args []Value, _ []KwArg) (Value, error) { return args[0], nil }))
	return instance
}

func TestJSONAcceleratorScannerNumbersAndBounds(t *testing.T) {
	vm := acceleratorTestVM()
	module := vm.natives["_json"]
	scanner := acceleratorCall(t, vm, module.NS["make_scanner"], acceleratorScannerContext(vm))
	for _, test := range []struct {
		text       string
		start, end int64
		want       Value
	}{
		{"01", 0, 1, int64(0)}, {"-01", 0, 2, int64(0)},
		{"1.", 0, 1, int64(1)}, {"1e+", 0, 1, int64(1)},
		{"1.2e-", 0, 3, 1.2}, {"1.2e+3tail", 0, 6, 1200.0},
		{"é42x", 1, 3, int64(42)}, {"truex", 0, 4, true},
		{"null", 0, 4, None}, {"NaN!", 0, 3, "NaN"},
		{"Infinity!", 0, 8, "Infinity"}, {"-Infinity!", 0, 9, "-Infinity"},
	} {
		got := acceleratorCall(t, vm, scanner, test.text, test.start)
		if want := (&Tuple{Items: []Value{test.want, test.end}}); !reflect.DeepEqual(got, want) {
			t.Fatalf("scan %q = %#v; want %#v", test.text, got, want)
		}
	}
	for _, test := range []struct {
		text  string
		index int64
	}{{"", 0}, {"x", 0}, {" true", 0}, {"-", 0}, {"0", 9}, {"é?", 1}} {
		_, err := vm.call(scanner, []Value{test.text, test.index}, nil)
		exc := acceleratorError(t, err, "StopIteration", "")
		if !reflect.DeepEqual(excArgs(exc), []Value{test.index}) {
			t.Fatalf("StopIteration args = %#v", excArgs(exc))
		}
	}
	_, err := vm.call(scanner, []Value{"0", int64(-1)}, nil)
	acceleratorError(t, err, "ValueError", "idx cannot be negative")
}

func TestJSONAcceleratorScannerHooks(t *testing.T) {
	vm := acceleratorTestVM()
	module := vm.natives["_json"]
	context := acceleratorScannerContext(vm)
	var calls []string
	for _, name := range []string{"parse_int", "parse_float", "parse_constant"} {
		name := name
		context.dictSet(name, bi(name, func(_ *VM, args []Value, _ []KwArg) (Value, error) {
			text := name + ":" + args[0].(string)
			calls = append(calls, text)
			return text, nil
		}))
	}
	context.dictSet("object_hook", bi("object_hook", func(_ *VM, _ []Value, _ []KwArg) (Value, error) {
		t.Fatal("object_hook ran despite object_pairs_hook")
		return None, nil
	}))
	context.dictSet("object_pairs_hook", bi("object_pairs_hook", func(_ *VM, args []Value, _ []KwArg) (Value, error) {
		calls = append(calls, "pairs")
		return args[0], nil
	}))
	scanner := acceleratorCall(t, vm, module.NS["make_scanner"], context)
	text := `{"x":-12,"x":1.25e+2,"nested":{"c":NaN}}`
	got := acceleratorCall(t, vm, scanner, text, int64(0))
	want := &Tuple{Items: []Value{&List{Items: []Value{
		&Tuple{Items: []Value{"x", "parse_int:-12"}},
		&Tuple{Items: []Value{"x", "parse_float:1.25e+2"}},
		&Tuple{Items: []Value{"nested", &List{Items: []Value{&Tuple{Items: []Value{"c", "parse_constant:NaN"}}}}}},
	}}, int64(len(text))}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("hook result = %#v; want %#v", got, want)
	}
	if !reflect.DeepEqual(calls, []string{"parse_int:-12", "parse_float:1.25e+2", "parse_constant:NaN", "pairs", "pairs"}) {
		t.Fatalf("hook order = %v", calls)
	}
	context.dictSet("object_pairs_hook", None)
	context.dictSet("object_hook", bi("object_hook", func(_ *VM, args []Value, _ []KwArg) (Value, error) {
		dict := args[0].(*Dict)
		return int64(dict.Len()), nil
	}))
	scanner = acceleratorCall(t, vm, module.NS["make_scanner"], context)
	got = acceleratorCall(t, vm, scanner, `{"x":1,"x":2}`, int64(0))
	if !reflect.DeepEqual(got, &Tuple{Items: []Value{int64(1), int64(13)}}) {
		t.Fatalf("object hook = %#v", got)
	}
	sentinel := newError("RuntimeError", "parse failed")
	context.dictSet("parse_int", bi("parse_int", func(_ *VM, _ []Value, _ []KwArg) (Value, error) { return nil, sentinel }))
	scanner = acceleratorCall(t, vm, module.NS["make_scanner"], context)
	if _, err := vm.call(scanner, []Value{"[1]", int64(0)}, nil); err != sentinel {
		t.Fatalf("hook exception = %v", err)
	}
	sentinel = newErrorArgs("StopIteration", int64(71))
	for _, text := range []string{"[1]", `{"x":1}`} {
		if _, err := vm.call(scanner, []Value{text, int64(0)}, nil); err != sentinel {
			t.Fatalf("nested StopIteration = %v", err)
		}
	}
}

func BenchmarkJSONAcceleratorScalarArray(b *testing.B) {
	vm := acceleratorTestVM()
	module := vm.natives["_json"]
	encoder := acceleratorCall(b, vm, module.NS["make_encoder"], acceleratorEncoderArgs(module)...)
	items := make([]Value, 4096)
	for i := range items {
		items[i] = "plain text"
	}
	value := &List{Items: items}
	b.ReportAllocs()
	b.SetBytes(int64(len(items) * len(`"plain text", `)))
	for b.Loop() {
		result, err := vm.call(encoder, []Value{value, int64(0)}, nil)
		if err != nil {
			b.Fatal(err)
		}
		if !strings.HasPrefix(result.(*Tuple).Items[0].(string), `["plain text"`) {
			b.Fatal("incorrect encoded array")
		}
	}
}

func TestJSONAcceleratorConstructorsAndKeywords(t *testing.T) {
	vm := acceleratorTestVM()
	module := vm.natives["_json"]
	args := acceleratorEncoderArgs(module)
	names := []string{"markers", "default", "encoder", "indent", "key_separator", "item_separator", "sort_keys", "skipkeys", "allow_nan"}
	kwargs := make([]KwArg, len(names))
	for i, name := range names {
		kwargs[i] = KwArg{Name: name, Val: args[i]}
	}
	encoder, err := vm.call(module.NS["make_encoder"], nil, kwargs)
	if err != nil {
		t.Fatal(err)
	}
	if native, ok := encoder.(*jsonEncoder); !ok || native.fast != 1 {
		t.Fatal("standard string encoder did not activate native fast path")
	}
	if got := acceleratorCall(t, vm, vm.builtins["type"], encoder); got != module.NS["make_encoder"] {
		t.Fatalf("encoder type = %#v; want canonical make_encoder", got)
	}
	result, err := vm.call(encoder, nil, []KwArg{{Name: "obj", Val: &List{Items: []Value{"x"}}}, {Name: "_current_indent_level", Val: int64(0)}})
	if err != nil || !reflect.DeepEqual(result, &Tuple{Items: []Value{`["x"]`}}) {
		t.Fatalf("keyword encoder = %#v, %v", result, err)
	}
	args[0] = int64(3)
	_, err = vm.call(module.NS["make_encoder"], args, nil)
	acceleratorError(t, err, "TypeError", "make_encoder() argument 1 must be dict or None, not int")
	args[0], args[4] = None, int64(3)
	_, err = vm.call(module.NS["make_encoder"], args, nil)
	acceleratorError(t, err, "TypeError", "")
	args[4], args[3] = ": ", "ignored by native encoder"
	encoder = acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	if got := acceleratorEncode(t, vm, encoder, &Tuple{Items: []Value{int64(1), int64(2)}}); got != "[1, 2]" {
		t.Fatalf("native indent behavior = %q", got)
	}
	context := acceleratorScannerContext(vm)
	scanner, err := vm.call(module.NS["make_scanner"], nil, []KwArg{{Name: "context", Val: context}})
	if err != nil {
		t.Fatal(err)
	}
	if got := acceleratorCall(t, vm, vm.builtins["type"], scanner); got != module.NS["make_scanner"] {
		t.Fatalf("scanner type = %#v; want canonical make_scanner", got)
	}
	result, err = vm.call(scanner, nil, []KwArg{{Name: "string", Val: "xfalse"}, {Name: "idx", Val: int64(1)}})
	if err != nil || !reflect.DeepEqual(result, &Tuple{Items: []Value{false, int64(6)}}) {
		t.Fatalf("keyword scanner = %#v, %v", result, err)
	}
	_, err = vm.call(module.NS["scanstring"], []Value{`"x"`, int64(1)}, []KwArg{{Name: "strict", Val: true}})
	acceleratorError(t, err, "TypeError", "")
}

func TestJSONAcceleratorListSubclassIteration(t *testing.T) {
	vm := acceleratorTestVM()
	module := vm.natives["_json"]
	class := &Class{Name: "CustomList", NS: map[string]Value{}}
	class.MRO = []*Class{class, objectClass}
	calls := 0
	class.NS["__iter__"] = bi("__iter__", func(_ *VM, _ []Value, _ []KwArg) (Value, error) {
		calls++
		return &seqIter{items: []Value{int64(7), "iteration"}}, nil
	})
	instance := newInstance(class, &List{})
	encoder := acceleratorCall(t, vm, module.NS["make_encoder"], acceleratorEncoderArgs(module)...)
	if got := acceleratorEncode(t, vm, encoder, instance); got != `[7, "iteration"]` || calls != 1 {
		t.Fatalf("subclass encode = %q, iterator calls = %d", got, calls)
	}
}

func TestJSONAcceleratorMarkersAndStringSubclass(t *testing.T) {
	vm := acceleratorTestVM()
	module := vm.natives["_json"]
	args := acceleratorEncoderArgs(module)
	class := &Class{Name: "CustomString", NS: map[string]Value{}}
	class.MRO = []*Class{class, objectClass}
	args[2] = bi("encoder", func(_ *VM, _ []Value, _ []KwArg) (Value, error) {
		return newInstance(class, `"subclass"`), nil
	})
	encoder := acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	if got := acceleratorEncode(t, vm, encoder, "original"); got != `"subclass"` {
		t.Fatalf("string subclass result = %q", got)
	}
	markers := NewDict()
	args[0] = markers
	sentinel := newError("RuntimeError", "default failed")
	args[1] = bi("default", func(_ *VM, _ []Value, _ []KwArg) (Value, error) { return nil, sentinel })
	object := newInstance(objectClass, nil)
	root := &List{Items: []Value{object}}
	encoder = acceleratorCall(t, vm, module.NS["make_encoder"], args...)
	if _, err := vm.call(encoder, []Value{root, int64(0)}, nil); err != sentinel {
		t.Fatalf("default exception = %v", err)
	}
	if markers.Len() != 2 {
		t.Fatalf("retained markers = %d; want 2", markers.Len())
	}
	for _, value := range []Value{root, object} {
		id := acceleratorCall(t, vm, vm.builtins["id"], value)
		got, found, err := markers.Get(id)
		if err != nil || !found || got != value {
			t.Fatalf("retained marker = %#v, %v, %v", got, found, err)
		}
	}
	_, err := vm.call(encoder, []Value{root, int64(0)}, nil)
	acceleratorError(t, err, "ValueError", "Circular reference detected")
}
