package interp

import (
	"bytes"
	"encoding/binary"
	"math"
	"math/big"
	"reflect"
	"testing"
)

func bufferTestView(t *testing.T, vm *VM, owner Value) *MemoryView {
	t.Helper()
	value, err := ctorMemoryView(vm, []Value{owner}, nil)
	if err != nil {
		t.Fatal(err)
	}
	view := value.(*MemoryView)
	t.Cleanup(view.release)
	return view
}

func bufferTestCast(t *testing.T, vm *VM, view *MemoryView, args ...Value) *MemoryView {
	t.Helper()
	value, err := vm.memoryViewCast(view, args, nil)
	if err != nil {
		t.Fatal(err)
	}
	cast := value.(*MemoryView)
	t.Cleanup(cast.release)
	return cast
}

func TestNativeBufferSharedTypedStorage(t *testing.T) {
	vm := &VM{}
	owner := &ByteArray{data: make([]byte, 16)}
	root := bufferTestView(t, vm, owner)
	view := bufferTestCast(t, vm, root, "I")
	if err := vm.bufferSet(view, int64(1), int64(0x12345678), false); err != nil {
		t.Fatal(err)
	}
	if got := binary.NativeEndian.Uint32(owner.data[4:8]); got != 0x12345678 {
		t.Fatalf("backing storage: %x", got)
	}
	if view.byteLen() != 16 || view.itemsize != 4 || view.length != 4 || !view.cContiguous() {
		t.Fatal("invalid typed metadata")
	}
	value, err := vm.bufferGet(view, &Slice{Lower: None, Upper: None, Step: int64(-1)})
	if err != nil {
		t.Fatal(err)
	}
	reversed := value.(*MemoryView)
	defer reversed.release()
	if reversed.cContiguous() || reversed.strides[0] != -4 {
		t.Fatal("invalid reverse stride")
	}
	if _, err := contiguousBufferBytes(reversed); err == nil {
		t.Fatal("strided native export accepted")
	}
	if err := vm.bufferSet(reversed, int64(0), int64(37), false); err != nil {
		t.Fatal(err)
	}
	if got := binary.NativeEndian.Uint32(owner.data[12:]); got != 37 {
		t.Fatalf("reversed write: %d", got)
	}
	root.release()
	view.release()
	end := int64(len(owner.data))
	if err := vm.bufferSet(owner, &Slice{Lower: end, Upper: end, Step: None}, []byte{0}, false); err == nil {
		t.Fatal("resize accepted with derived export")
	}
	reversed.release()
	if owner.exports.Load() != 0 {
		t.Fatal("released exports retained")
	}
	if err := vm.bufferSet(owner, &Slice{Lower: end, Upper: end, Step: None}, []byte{0}, false); err != nil {
		t.Fatal(err)
	}
}

func TestNativeBufferShapesAndAssignment(t *testing.T) {
	vm := &VM{}
	owner := &ByteArray{data: make([]byte, 24)}
	root := bufferTestView(t, vm, owner)
	matrix := bufferTestCast(t, vm, root, "i", &List{Items: []Value{int64(2), int64(3)}})
	if !reflect.DeepEqual(matrix.shape, []int{2, 3}) || !reflect.DeepEqual(matrix.strides, []int{12, 4}) {
		t.Fatal("invalid matrix metadata")
	}
	key := &Tuple{Items: []Value{int64(-1), int64(-1)}}
	if err := vm.bufferSet(matrix, key, int64(-17), false); err != nil {
		t.Fatal(err)
	}
	got, err := vm.bufferGet(matrix, key)
	if err != nil || got != int64(-17) {
		t.Fatalf("matrix item: %v %v", got, err)
	}
	if _, err := vm.bufferGet(matrix, int64(0)); err == nil {
		t.Fatal("multidimensional subview accepted")
	}
	if _, err := vm.memoryViewCast(matrix, []Value{"B", &List{Items: []Value{int64(3), int64(8)}}}, nil); err == nil {
		t.Fatal("ND to ND cast accepted")
	}
	if _, err := vm.memoryViewCast(matrix, []Value{"d"}, nil); err == nil {
		t.Fatal("nonbyte to nonbyte cast accepted")
	}
	scalarRoot := bufferTestView(t, vm, []byte{9})
	scalar := bufferTestCast(t, vm, scalarRoot, "B", &Tuple{})
	if _, err := scalar.lenValue(); err == nil {
		t.Fatal("scalar length accepted")
	}
	got, err = vm.bufferGet(scalar, &Tuple{})
	if err != nil || got != int64(9) {
		t.Fatalf("scalar item: %v %v", got, err)
	}
	if _, err := vm.bufferGet(scalar, int64(0)); err == nil {
		t.Fatal("scalar integer indexing accepted")
	}
	atBytes := bufferTestCast(t, vm, root, "@B")
	if err := vm.bufferSet(atBytes, &Slice{Lower: int64(0), Upper: int64(2), Step: None}, []byte{5, 6}, false); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(owner.data[:2], []byte{5, 6}) {
		t.Fatal("equivalent-format assignment failed")
	}
}

func TestNativeBufferNativeScalarBits(t *testing.T) {
	vm := &VM{}
	root := bufferTestView(t, vm, &ByteArray{data: make([]byte, 8)})
	doubles := bufferTestCast(t, vm, root, "d")
	for _, bits := range []uint64{0, 0x8000000000000000, 0x7ff0000000000000, 0x7ff8000000000123, 0x3ff0000000000001} {
		if err := vm.bufferSet(doubles, int64(0), math.Float64frombits(bits), false); err != nil {
			t.Fatal(err)
		}
		if got := binary.NativeEndian.Uint64(root.data); got != bits {
			t.Fatalf("packed bits %x != %x", got, bits)
		}
		value, err := vm.bufferGet(doubles, int64(0))
		if err != nil {
			t.Fatal(err)
		}
		if got := math.Float64bits(value.(float64)); got != bits {
			t.Fatalf("unpacked bits %x != %x", got, bits)
		}
	}
	unsigned := bufferTestCast(t, vm, root, "Q")
	max := new(big.Int).SetUint64(math.MaxUint64)
	if err := vm.bufferSet(unsigned, int64(0), max, false); err != nil {
		t.Fatal(err)
	}
	value, err := vm.bufferGet(unsigned, int64(0))
	if err != nil {
		t.Fatal(err)
	}
	if value.(*big.Int).Cmp(max) != 0 {
		t.Fatal("uint64 truncated")
	}
	if err := vm.bufferSet(unsigned, int64(0), int64(-1), false); err == nil {
		t.Fatal("unsigned negative accepted")
	}
	pointer := bufferTestCast(t, vm, root, "P")
	if err := vm.bufferSet(pointer, int64(0), int64(-1), false); err != nil {
		t.Fatal(err)
	}
}

func TestNativeBufferHalfPrecision(t *testing.T) {
	cases := []struct {
		value float64
		bits  uint16
	}{
		{0, 0}, {math.Copysign(0, -1), 0x8000}, {1, 0x3c00}, {-2, 0xc000},
		{65504, 0x7bff}, {math.Ldexp(1, -24), 1}, {math.Ldexp(1, -25), 0},
		{1 + math.Ldexp(1, -11), 0x3c00}, {1 + 3*math.Ldexp(1, -11), 0x3c02},
		{math.Inf(1), 0x7c00}, {math.Inf(-1), 0xfc00},
	}
	for _, test := range cases {
		got, err := packViewHalf(test.value)
		if err != nil || got != test.bits {
			t.Fatalf("half %g: %04x %v, want %04x", test.value, got, err, test.bits)
		}
	}
	if _, err := packViewHalf(65520); err == nil {
		t.Fatal("half overflow accepted")
	}
	for _, bits := range []uint16{0, 0x8000, 1, 0x3ff, 0x400, 0x3c00, 0x7bff, 0x7c00, 0xfc00} {
		got, err := packViewHalf(unpackViewHalf(bits))
		if err != nil || got != bits {
			t.Fatalf("half round trip %04x: %04x %v", bits, got, err)
		}
	}
}

func TestNativeBufferIntToBytes(t *testing.T) {
	vm := &VM{}
	cases := []struct {
		value  Value
		args   []Value
		signed bool
		want   []byte
	}{
		{int64(65), nil, false, []byte{'A'}},
		{int64(-2), []Value{int64(2), "little"}, true, []byte{0xfe, 0xff}},
		{int64(-1), []Value{int64(0)}, true, []byte{}},
		{new(big.Int).SetUint64(math.MaxUint64), []Value{int64(8), "big"}, false, bytes.Repeat([]byte{0xff}, 8)},
	}
	for _, test := range cases {
		got, err := intToBytes(vm, test.value, test.args, []KwArg{{Name: "signed", Val: test.signed}})
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(got.([]byte), test.want) {
			t.Fatalf("bytes %x != %x", got, test.want)
		}
	}
	if _, err := intToBytes(vm, int64(128), []Value{int64(1)}, []KwArg{{Name: "signed", Val: true}}); err == nil {
		t.Fatal("signed overflow accepted")
	}
}
