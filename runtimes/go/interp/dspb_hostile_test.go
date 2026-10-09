package interp

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// The DSPB decoder is a trust boundary: crafted bytes must fail fast with a
// clean error - never hang, never allocate proportional to an invalid count,
// never panic (these tests are deliberately recover-free: a panic anywhere
// fails the suite).

func encodedSmoke(t *testing.T) []byte {
	t.Helper()
	if _, err := exec.LookPath("uv"); err != nil {
		t.Skip("uv not available")
	}
	dst := filepath.Join(t.TempDir(), "smoke.json")
	cmd := exec.Command("uv", "run", "diaspore", "build", "tests/corpus/smoke.py", "-o", dst)
	cmd.Dir = filepath.Join("..", "..", "..")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("uv run diaspore build: %v\n%s", err, out)
	}
	data, err := os.ReadFile(dst)
	if err != nil {
		t.Fatal(err)
	}
	var bundle map[string]any
	if err := json.Unmarshal(data, &bundle); err != nil {
		t.Fatal(err)
	}
	enc, err := EncodeDSPB(bundle, nil)
	if err != nil {
		t.Fatal(err)
	}
	return enc
}

// TestDSPBTruncationsNeverCrash checks rejection of truncated containers.
func TestDSPBTruncationsNeverCrash(t *testing.T) {
	data := encodedSmoke(t)
	cuts := make([]int, 0, 42)
	for i := 0; i < 40; i++ {
		cuts = append(cuts, i)
	}
	cuts = append(cuts, len(data)/2, len(data)-1)
	for _, cut := range cuts {
		if _, err := DecodeDSPB(data[:cut]); err == nil {
			t.Fatalf("truncation at %d decoded without error", cut)
		}
	}
}

// TestDSPBBitflipsFailCleanly checks that mutated containers decode or fail without panicking.
func TestDSPBBitflipsFailCleanly(t *testing.T) {
	data := encodedSmoke(t)
	end := len(data)
	if end > 220 {
		end = 220
	}
	for pos := 6; pos < end; pos++ {
		corrupted := append([]byte{}, data...)
		corrupted[pos] ^= 0xFF
		// Clean rejection or accidental-valid decode are both fine; a hang
		// or panic is not (a panic fails the test on its own).
		_, _ = DecodeDSPB(corrupted)
	}
}

// TestDSPBLiedModuleCountRejected checks rejection of counts larger than the input.
func TestDSPBLiedModuleCountRejected(t *testing.T) {
	w := &dspbWriter{strings: map[string]int{}}
	w.u(0)       // empty string table
	w.u(1)       // semver
	w.u(0)       // invalid main string reference
	w.u(1 << 60) // count exceeds the remaining input
	blob := append(append([]byte{}, dspbMagic...), w.buf.Bytes()...)
	_, err := DecodeDSPB(blob)
	if err == nil {
		t.Fatal("lied module count decoded without error")
	}
	if !strings.Contains(err.Error(), "count exceeds remaining") &&
		!strings.Contains(err.Error(), "bad string ref") {
		t.Fatalf("unexpected error: %v", err)
	}
}

// TestDSPBDeepNestingBounded checks the decoder nesting limit.
func TestDSPBDeepNestingBounded(t *testing.T) {
	unaryOp, _ := indexOf(dspbOps, "unary")
	constOp, _ := indexOf(dspbOps, "const")
	var body []byte
	for i := 0; i < 50_000; i++ {
		body = append(body, byte(unaryOp), 0, 0) // op, flags, kind=neg
	}
	body = append(body, byte(constOp), 0, 5) // none const
	head := &dspbWriter{strings: map[string]int{}}
	head.u(1) // strtab: one entry, "m"
	head.u(1)
	head.buf.WriteByte('m')
	head.u(1)    // semver
	head.u(0)    // main -> "m"
	head.u(1)    // module count
	head.u(0)    // name
	head.u(0)    // file
	head.byte(0) // no src
	head.u(1)    // body: one node (the unary chain)
	blob := append(append(append([]byte{}, dspbMagic...), head.buf.Bytes()...), body...)
	_, err := DecodeDSPB(blob)
	if err == nil {
		t.Fatal("50k-deep nesting decoded without error")
	}
	if !strings.Contains(err.Error(), "nesting too deep") {
		t.Fatalf("unexpected error: %v", err)
	}
}

// TestDSPBBadEnumBytes checks rejection of invalid enum tags.
func TestDSPBBadEnumBytes(t *testing.T) {
	craft := func(nodeBytes ...byte) []byte {
		head := &dspbWriter{strings: map[string]int{}}
		head.u(1)
		head.u(1)
		head.buf.WriteByte('m')
		head.u(1)
		head.u(0)
		head.u(1)
		head.u(0)
		head.u(0)
		head.byte(0)
		head.u(1)
		blob := append(append([]byte{}, dspbMagic...), head.buf.Bytes()...)
		return append(blob, nodeBytes...)
	}
	unaryOp, _ := indexOf(dspbOps, "unary")
	constOp, _ := indexOf(dspbOps, "const")
	cases := map[string][]byte{
		"bad opcode":     craft(200, 0),
		"bad const kind": craft(byte(constOp), 0, 99),
		"bad unary kind": craft(byte(unaryOp), 0, 77, byte(constOp), 0, 5),
	}
	for label, blob := range cases {
		if _, err := DecodeDSPB(blob); err == nil {
			t.Fatalf("%s decoded without error", label)
		}
	}
}
