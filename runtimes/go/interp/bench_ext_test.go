package interp_test

import (
	"encoding/json"
	"io"
	"os"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/interp"
)

// BenchmarkBundle measures loading and running DIASPORE_BENCH_BUNDLE.
func BenchmarkBundle(b *testing.B) {
	path := os.Getenv("DIASPORE_BENCH_BUNDLE")
	if path == "" {
		b.Skip("DIASPORE_BENCH_BUNDLE not set")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		b.Fatal(err)
	}
	var bundle map[string]any
	if err := json.Unmarshal(data, &bundle); err != nil {
		b.Fatal(err)
	}
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		vm := interp.New(bundle, interp.WithStdout(io.Discard), interp.WithStderr(io.Discard))
		if code := vm.Run(); code != 0 {
			b.Fatalf("exit %d", code)
		}
	}
}
