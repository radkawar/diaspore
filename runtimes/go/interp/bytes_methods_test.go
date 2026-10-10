package interp

import (
	"bytes"
	"math/big"
	"testing"
)

func TestNativeBytesMethodsAndDescriptors(t *testing.T) {
	vm := acceleratorTestVM()
	maketrans, err := vm.getAttr(vm.types["bytes"], "maketrans")
	if err != nil {
		t.Fatal(err)
	}
	tableValue, err := vm.call(maketrans, []Value{[]byte("ab"), &ByteArray{data: []byte("XY")}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	table := tableValue.([]byte)
	if len(table) != 256 || table['a'] != 'X' || table['b'] != 'Y' || table['c'] != 'c' {
		t.Fatal("invalid byte translation table")
	}
	translated, err := bytesTranslate(vm, []byte("abcab"), []Value{table}, []KwArg{{Name: "delete", Val: []byte("b")}})
	if err != nil || !bytes.Equal(translated.([]byte), []byte("XcX")) {
		t.Fatalf("translate = %v, %v", translated, err)
	}
	translated, err = bytesTranslate(vm, []byte("abc"), []Value{None, []byte("b")}, nil)
	if err != nil || !bytes.Equal(translated.([]byte), []byte("ac")) {
		t.Fatalf("delete = %v, %v", translated, err)
	}
	for _, invalid := range []Value{[]byte{}, []byte("short")} {
		_, err := bytesTranslate(vm, []byte("a"), []Value{invalid}, nil)
		if _, ok := raisedAs(err, "ValueError"); !ok {
			t.Fatalf("invalid translation table: %v", err)
		}
	}
	viewValue, err := ctorMemoryView(vm, []Value{[]byte("cd")}, nil)
	if err != nil {
		t.Fatal(err)
	}
	view := viewValue.(*MemoryView)
	defer view.release()
	join, err := vm.getAttr([]byte("-"), "join")
	if err != nil {
		t.Fatal(err)
	}
	joined, err := vm.call(join, []Value{&List{Items: []Value{[]byte("a"), &ByteArray{data: []byte("b")}, view}}}, nil)
	if err != nil || !bytes.Equal(joined.([]byte), []byte("a-b-cd")) {
		t.Fatalf("join = %v, %v", joined, err)
	}
	_, err = vm.call(join, []Value{&List{Items: []Value{[]byte("a"), "bad"}}}, nil)
	if _, ok := raisedAs(err, "TypeError"); !ok {
		t.Fatalf("join accepted str: %v", err)
	}
	hex, err := vm.getAttr([]byte{0, 1, 2, 255}, "hex")
	if err != nil {
		t.Fatal(err)
	}
	text, err := vm.call(hex, []Value{"-", int64(2)}, nil)
	if err != nil || text != "0001-02ff" {
		t.Fatalf("hex = %v, %v", text, err)
	}
	copyMethod, err := vm.getAttr(vm.types["bytearray"], "copy")
	if err != nil {
		t.Fatal(err)
	}
	original := &ByteArray{data: []byte("abc")}
	copied, err := vm.call(copyMethod, []Value{original}, nil)
	if err != nil {
		t.Fatal(err)
	}
	copy := copied.(*ByteArray)
	copy.data[0] = 'z'
	if !bytes.Equal(original.data, []byte("abc")) || !bytes.Equal(copy.data, []byte("zbc")) {
		t.Fatal("bytearray copy aliases storage")
	}
	arrayJoin, err := vm.getAttr(original, "join")
	if err != nil {
		t.Fatal(err)
	}
	arrayResult, err := vm.call(arrayJoin, []Value{&Tuple{Items: []Value{[]byte("1"), []byte("2")}}}, nil)
	if err != nil || !bytes.Equal(arrayResult.(*ByteArray).data, []byte("1abc2")) {
		t.Fatalf("bytearray join: %v %v", arrayResult, err)
	}
}

func TestNativeIntegerBoundIndexAndAbsolute(t *testing.T) {
	vm := acceleratorTestVM()
	wide := new(big.Int).Lsh(big.NewInt(1), 200)
	for _, value := range []Value{int64(2), true, wide} {
		method, err := vm.getAttr(value, "__index__")
		if err != nil {
			t.Fatal(err)
		}
		result, err := vm.call(method, nil, nil)
		if err != nil || numCmp(result, value) != 0 {
			t.Fatalf("bound index: %v %v", result, err)
		}
	}
	absolute, err := vm.getAttr(vm.types["int"], "__abs__")
	if err != nil {
		t.Fatal(err)
	}
	negative := new(big.Int).Neg(wide)
	result, err := vm.call(absolute, []Value{negative}, nil)
	if err != nil || numCmp(result, wide) != 0 {
		t.Fatalf("unbound abs: %v %v", result, err)
	}
	_, err = vm.call(absolute, []Value{float64(-2)}, nil)
	if _, ok := raisedAs(err, "TypeError"); !ok {
		t.Fatalf("int absolute accepted float: %v", err)
	}
}
