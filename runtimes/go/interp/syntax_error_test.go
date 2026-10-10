package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestSyntaxErrorProtocols(t *testing.T) {
	cases := []struct{ name, source string }{
		{"native_members_and_subclasses", `fields = ('msg', 'filename', 'lineno', 'offset', 'text', 'end_lineno', 'end_offset', 'print_file_and_line')
for cls in (SyntaxError, IndentationError, TabError):
    value = cls()
    assert value.args == () and str(value) == 'None'
    for name in fields:
        assert getattr(value, name) is None
        assert name not in value.__dict__
        setattr(value, name, 42)
        assert getattr(value, name) == 42
        delattr(value, name)
        assert getattr(value, name) is None
        delattr(value, name)
    value = cls('bad', ('/tmp/example.py', 3, 4, 'line', 5, 6))
    assert (value.msg, value.filename, value.lineno, value.offset, value.text, value.end_lineno, value.end_offset) == ('bad', '/tmp/example.py', 3, 4, 'line', 5, 6)
    assert str(value) == 'bad (example.py, line 3)'
    assert value.args == ('bad', ('/tmp/example.py', 3, 4, 'line', 5, 6))
assert SyntaxError.msg.__objclass__ is SyntaxError
class Custom(SyntaxError):
    __slots__ = ('extra',)
value = Custom('custom', ('file', 2, None, None))
value.extra = 42
assert value.msg == 'custom' and value.extra == 42
assert str(value) == 'custom (file, line 2)'
class Shadows(SyntaxError):
    @property
    def msg(self):
        return 'attribute override'
value = Shadows('native message')
assert value.msg == 'attribute override' and str(value) == 'native message'
`},
		{"args_and_reinitialization", `details = ['first.py', 2, 3, 'text', 4, 5]
value = SyntaxError('first', details)
assert value.args[1] is details
assert value.filename == 'first.py'
details[0] = 'changed.py'
assert value.args[1][0] == 'changed.py' and value.filename == 'first.py'
value.__init__()
assert value.args == () and value.msg == 'first'
assert value.filename == 'first.py' and value.end_lineno == 4
value.__init__('second')
assert value.args == ('second',) and value.msg == 'second'
assert value.end_lineno == 4
value.__init__('third', 42, 'extra')
assert value.args == ('third', 42, 'extra') and value.msg == 'third'
assert value.filename == 'first.py'
try:
    value.__init__('conversion', None)
except TypeError:
    pass
else:
    raise AssertionError('noniterable details accepted')
assert value.args == ('conversion', None) and value.msg == 'conversion'
assert value.end_lineno == 4 and value.end_offset == 5
for details in ((), (1, 2, 3), (1, 2, 3, 4, 5, 6, 7)):
    try:
        value.__init__('arity', details)
    except TypeError as error:
        bound = 'at least 4' if len(details) < 4 else 'at most 6'
        assert str(error) == 'function takes ' + bound + ' arguments (' + str(len(details)) + ' given)'
    else:
        raise AssertionError('invalid details arity accepted')
    assert value.msg == 'arity' and value.filename == 'first.py'
    assert value.end_lineno is None and value.end_offset is None
try:
    value.__init__('five', ('five.py', 8, 9, 'line', 10))
except TypeError as error:
    assert str(error) == 'end_offset must be provided when end_lineno is provided'
else:
    raise AssertionError('five details accepted')
assert value.filename == 'five.py' and value.end_lineno == 10 and value.end_offset is None
value.__init__('four', iter(('four.py', 1, 2, 'line')))
assert value.filename == 'four.py' and value.end_lineno is None and value.end_offset is None
value.args = ('independent',)
assert value.msg == 'four' and str(value) == 'four (four.py, line 1)'
try:
    SyntaxError(msg='keyword')
except TypeError:
    pass
else:
    raise AssertionError('keyword accepted')
`},
		{"string_formatting", `class Integer(int):
    pass
class Filename(str):
    def __str__(self):
        raise AssertionError('filename override called')
for filename, lineno, expected in ((None, None, 'message'), (None, 4, 'message (line 4)'), ('/tmp/file.py', None, 'message (file.py)'), ('/tmp/file.py', 4, 'message (file.py, line 4)'), ('', 0, 'message (, line 0)'), ('file', True, 'message (file)'), ('file', Integer(4), 'message (file)'), ('file', 1 << 100, 'message (file, line -1)'), (Filename('/tmp/file'), -2, 'message (file, line -2)'), (b'file', 7, 'message (line 7)')):
    value = SyntaxError('message', (filename, lineno, None, None))
    assert str(value) == expected
    assert SyntaxError.__str__(value) == expected
value = SyntaxError(42)
assert value.msg == 42 and str(value) == '42'
del value.msg
assert str(value) == 'None'
`},
		{"native_parser_and_vendored_tokenizer", `import ast
import tokenize
try:
    ast.parse('def f(: int)\n', filename='rejected.py')
except SyntaxError as error:
    assert isinstance(error.msg, str) and error.msg
    assert hasattr(error, 'lineno')
else:
    raise AssertionError('invalid function accepted')
lines = iter([b'# coding: unknown-diaspore-codec\n'])
try:
    tokenize.detect_encoding(lambda: next(lines, b''))
except SyntaxError as error:
    assert error.msg.startswith('unknown encoding:')
    assert str(error) == error.msg
else:
    raise AssertionError('unknown encoding accepted')
`},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			compiled, err := dspc.CompileSource("/src/main.py", test.source, dspc.CompileOptions{})
			if err != nil || compiled.Code != 0 {
				t.Fatalf("compile: %v %s", err, compiled.Stderr)
			}
			bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
			if err != nil {
				t.Fatal(err)
			}
			var stderr bytes.Buffer
			vm := interp.New(bundle, interp.WithStderr(&stderr), interp.WithSnippetCompiler(dspc.CompileSnippet))
			defer vm.Close()
			if code := vm.Run(); code != 0 {
				t.Fatalf("Run() = %d: %s", code, stderr.String())
			}
		})
	}
}
