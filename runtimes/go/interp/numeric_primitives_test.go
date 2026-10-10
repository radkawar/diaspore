package interp

import (
	"bytes"
	"math"
	"math/big"
	"testing"
)

func TestNativePythonNumericHashes(t *testing.T) {
	for _, test := range []struct {
		value Value
		want  int64
	}{
		{float64(0), 0}, {math.Copysign(0, -1), 0}, {float64(-1), -2},
		{0.5, 1152921504606846976}, {-0.5, -1152921504606846976},
		{1.5, 1152921504606846977}, {0.1, 230584300921369408},
		{math.Inf(1), 314159}, {math.Inf(-1), -314159},
		{int64(1<<61) - 1, 0}, {int64(1 << 61), 1}, {int64(math.MinInt64), -4},
		{new(big.Int).Lsh(big.NewInt(1), 1000), 16777216},
		{math.Ldexp(1, 1000), 16777216}, {complex(1.5, 2), 1152921504608846983},
	} {
		hash, err := hashValue(test.value)
		if err != nil || hash != test.want {
			t.Fatalf("hash(%v) = %d, %v; want %d", test.value, hash, err, test.want)
		}
	}
	for _, exponent := range []int{-1074, -1022, -1000, -61, -1, 0, 61, 1000} {
		value := math.Ldexp(1, exponent)
		power := exponent % 61
		if power < 0 {
			power += 61
		}
		want := int64(1) << power
		if got := pythonFloatHash(value); got != want {
			t.Fatalf("hash(2**%d)=%d, want %d", exponent, got, want)
		}
	}
	integer := new(big.Int).Lsh(big.NewInt(1), 1000)
	set := NewSet()
	if err := set.Add(integer); err != nil {
		t.Fatal(err)
	}
	if err := set.Add(math.Ldexp(1, 1000)); err != nil {
		t.Fatal(err)
	}
	if set.Len() != 1 {
		t.Fatal("equal wide int and float do not unify")
	}
}

func TestNativeUnicodeDecimalIntegerParsing(t *testing.T) {
	for _, test := range []struct {
		text string
		base int
		want int64
	}{
		{"٣", 10, 3}, {"\u2003-१२_३\u3000", 10, -123},
		{"１２３", 10, 123}, {"𝟙𝟚𝟛", 10, 123}, {"𝟣𝟤𝟥", 10, 123},
		{"٠xＦ", 16, 0}, {"٠x٣f", 16, 63}, {"١٠١", 2, 5},
	} {
		value, err := parsePyInt(test.text, test.base)
		if test.text == "٠xＦ" {
			if _, ok := raisedAs(err, "ValueError"); !ok {
				t.Fatalf("nondigit fullwidth letter accepted: %v", err)
			}
			continue
		}
		if err != nil || value != test.want {
			t.Fatalf("int(%q,%d)=%v,%v", test.text, test.base, value, err)
		}
	}
	for _, invalid := range []string{"²", "Ⅳ", "٣__٤", "٣.٤", "\xed\xa0\x80"} {
		_, err := parsePyInt(invalid, 10)
		if _, ok := raisedAs(err, "ValueError"); !ok {
			t.Fatalf("accepted invalid numeric text %q: %v", invalid, err)
		}
	}
}

func TestNativeBinaryPadding(t *testing.T) {
	vm := acceleratorTestVM()
	for _, test := range []struct {
		method string
		width  int64
		want   string
	}{
		{"ljust", 5, "ab..."}, {"rjust", 5, "...ab"}, {"center", 5, "..ab."},
		{"center", 4, ".ab."}, {"ljust", -1, "ab"},
	} {
		value, err := bytesPad(vm, []byte("ab"), test.method, []Value{test.width, []byte(".")}, nil)
		if err != nil || !bytes.Equal(value.([]byte), []byte(test.want)) {
			t.Fatalf("%s padding=%v,%v", test.method, value, err)
		}
	}
	original := &ByteArray{data: []byte("ab")}
	method, err := vm.getAttr(original, "ljust")
	if err != nil {
		t.Fatal(err)
	}
	value, err := vm.call(method, []Value{int64(1)}, nil)
	if err != nil {
		t.Fatal(err)
	}
	value.(*ByteArray).data[0] = 'z'
	if string(original.data) != "ab" {
		t.Fatal("no-padding bytearray result aliases input")
	}
	_, err = bytesPad(vm, []byte("ab"), "ljust", []Value{int64(1), []byte("xx")}, nil)
	if _, ok := raisedAs(err, "TypeError"); !ok {
		t.Fatalf("fill width was not validated: %v", err)
	}
}

func TestNativeNumericHashDescriptors(t *testing.T) {
	vm := acceleratorTestVM()
	for _, value := range []Value{int64(-1), true, 0.5, complex(1.5, 2), new(big.Int).Lsh(big.NewInt(1), 1000)} {
		bound, err := vm.getAttr(value, "__hash__")
		if err != nil {
			t.Fatal(err)
		}
		actual, err := vm.call(bound, nil, nil)
		expected, hashErr := hashValue(value)
		if err != nil || hashErr != nil || actual != expected {
			t.Fatalf("bound hash(%v) = %v,%v; want %d", value, actual, err, expected)
		}
		unbound, err := vm.getAttr(vm.types[typeName(value)], "__hash__")
		if err != nil {
			t.Fatal(err)
		}
		actual, err = vm.call(unbound, []Value{value}, nil)
		if err != nil || actual != expected {
			t.Fatalf("unbound hash(%v) = %v,%v", value, actual, err)
		}
	}
	class := &Class{Name: "IntHashOverride", NS: map[string]Value{}, Prim: vm.types["int"], Bases: []*Class{objectClass}, vm: vm}
	class.MRO = []*Class{class, objectClass}
	class.NS["__hash__"] = bi("__hash__", func(_ *VM, _ []Value, _ []KwArg) (Value, error) { return int64(99), nil })
	instance := newInstance(class, int64(7))
	if hash, err := hashValue(instance); hash != 99 || err != nil {
		t.Fatalf("subclass hash override: %d %v", hash, err)
	}
	descriptor, err := vm.getAttr(vm.types["int"], "__hash__")
	if err != nil {
		t.Fatal(err)
	}
	if hash, err := vm.call(descriptor, []Value{instance}, nil); hash != int64(7) || err != nil {
		t.Fatalf("explicit native hash called override: %v %v", hash, err)
	}
	namespace, err := vm.getAttr(vm.types["int"], "__dict__")
	if err != nil {
		t.Fatal(err)
	}
	stored, err := vm.getIndex(namespace, "__hash__")
	if err != nil || stored != descriptor {
		t.Fatal("int hash namespace descriptor is not canonical")
	}
	if typeName(descriptor) != "wrapper_descriptor" {
		t.Fatalf("hash descriptor type: %s", typeName(descriptor))
	}
	inherited, err := vm.getAttr(vm.types["bool"], "__hash__")
	if err != nil || inherited != descriptor {
		t.Fatal("bool hash does not inherit canonical int descriptor")
	}
	boolNamespace, err := vm.getAttr(vm.types["bool"], "__dict__")
	if err != nil {
		t.Fatal(err)
	}
	if present, err := vm.contains(boolNamespace, "__hash__"); present || err != nil {
		t.Fatalf("bool namespace overrides inherited hash: %v %v", present, err)
	}
	if _, err := vm.call(descriptor, []Value{0.5}, nil); err == nil {
		t.Fatal("int hash descriptor accepted float")
	}
}
