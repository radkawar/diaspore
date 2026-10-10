package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestVendoredJSONAccelerator(t *testing.T) {
	const source = `import json
import _json

assert json.decoder.scanstring is _json.scanstring
assert type(json._default_decoder.scan_once) is _json.make_scanner
assert json.encoder.c_make_encoder is _json.make_encoder
assert json.dumps({'z': [1, 2.0, True, None], '\u00e9': '\U0001f600'}, ensure_ascii=False, sort_keys=True, separators=(',', ':')) == '{"z":[1,2.0,true,null],"\u00e9":"\U0001f600"}'
assert json.dumps({'value': {2, 1}}, default=lambda value: sorted(value)) == '{"value": [1, 2]}'
assert json.loads('{"a":1,"a":2}', object_pairs_hook=lambda pairs: pairs) == [('a', 1), ('a', 2)]
assert json.loads('[1, 2.5, NaN]', parse_int=lambda value: ('int', value), parse_float=lambda value: ('float', value), parse_constant=lambda value: ('constant', value)) == [('int', '1'), ('float', '2.5'), ('constant', 'NaN')]
assert json.loads('{"value":1}', object_hook=lambda value: sorted(value.items())) == [('value', 1)]
for text, message, position, line, column in [
    ('{"\u00e9":}', 'Expecting value', 5, 1, 6),
    ('[1,]', 'Expecting value', 3, 1, 4),
    ('"\\x00"', 'Invalid \\escape', 1, 1, 2),
    ('"\n"', 'Invalid control character at', 1, 1, 2),
    ('{"x": 1} extra', 'Extra data', 9, 1, 10),
    ('"\\uZZZZ"', 'Invalid \\uXXXX escape', 2, 1, 3),
    ('"unterminated', 'Unterminated string starting at', 0, 1, 1),
    ('[1\n2]', "Expecting ',' delimiter", 3, 2, 1),
]:
    try:
        json.loads(text)
    except json.JSONDecodeError as error:
        assert (error.msg, error.pos, error.lineno, error.colno, error.doc) == (message, position, line, column, text)
    else:
        raise AssertionError('malformed JSON accepted')
assert [ord(char) for char in json.loads('"\\ud800\\udfff"')] == [66559]
assert _json.scanstring('"\U0001f600"', 1) == ('\U0001f600', 3)
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
