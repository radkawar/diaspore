package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeInstallationMetadata(t *testing.T) {
	const source = `import gettext
import os
import sys
assert sys.prefix == '<stdlib>'
assert sys.exec_prefix == sys.prefix
assert sys.base_prefix == sys.prefix
assert sys.base_exec_prefix == sys.prefix
assert gettext.__file__.startswith(sys.prefix + '/')
assert gettext._default_localedir == os.path.join(sys.base_prefix, 'share', 'locale')
assert gettext.NullTranslations().gettext('original') == 'original'
`
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	vm := interp.New(bundle, interp.WithStderr(&stderr))
	defer vm.Close()
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d: %s", code, stderr.String())
	}
}
