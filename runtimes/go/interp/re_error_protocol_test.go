package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeRegexErrorProtocols(t *testing.T) {
	cases := []struct{ name, source, stdout string }{
		{"maintained_unclosed_pattern", `import re
try:
    match = re.search('(unclosed', 'x')
except re.error as error:
    print('error', repr(error.msg), repr(error.pos))
`, "error 'missing ), unterminated subpattern' 0\n"},
		{"pattern_diagnostics", `import re
assert re.error is re.PatternError
assert issubclass(re.error, Exception)
assert not issubclass(re.error, ValueError)
cases = [
    ('(', 'missing ), unterminated subpattern', 0),
    ('é\n(', 'missing ), unterminated subpattern', 2),
    (b'\xff(', 'missing ), unterminated subpattern', 1),
    ('[ab', 'unterminated character set', 0),
    (')', 'unbalanced parenthesis', 0),
    (r'\q', r'bad escape \q', 0),
    (r'\x1z', r'incomplete escape \x1', 0),
    ('\\', 'bad escape (end of pattern)', 0),
    (r'\777', r'octal escape value \777 outside of range 0-0o377', 0),
    (r'(a)\2', 'invalid group reference 2', 4),
    (r'(a\1)', 'cannot refer to an open group', 2),
    ('a**', 'multiple repeat', 2),
    ('*a', 'nothing to repeat', 0),
    ('a{3,2}', 'min repeat greater than max repeat', 2),
    ('[z-a]', 'bad character range z-a', 1),
    (r'[a-\d]', r'bad character range a-\d', 1),
    ('(?q)', 'unknown extension ?q', 1),
    ('(?P<name>x)(?P<name>y)', "redefinition of group name 'name' as group 2; was group 1", 15),
]
for pattern, message, position in cases:
    try:
        re.compile(pattern)
    except re.error as error:
        assert type(error) is re.error
        assert error.msg == message, (pattern, error.msg, message)
        assert error.pattern == pattern
        assert error.pos == position, (pattern, error.pos, position)
        newline = b'\n' if isinstance(pattern, bytes) else '\n'
        line = pattern.count(newline, 0, position) + 1
        column = position - pattern.rfind(newline, 0, position)
        assert (error.lineno, error.colno) == (line, column)
        formatted = message + ' at position ' + str(position)
        if newline in pattern:
            formatted += ' (line ' + str(line) + ', column ' + str(column) + ')'
        assert error.args == (formatted,)
        assert str(error) == formatted
    else:
        raise AssertionError(('invalid pattern accepted', pattern))
try:
    re.compile('(?<=a+)b')
except re.error as error:
    assert error.msg == 'look-behind requires fixed-width pattern'
    assert error.pattern is None and error.pos is None
    assert error.lineno is None and error.colno is None
else:
    raise AssertionError('variable lookbehind accepted')
`, ""},
		{"replacement_diagnostics", `import re
cases = [
    (r'\2', 'invalid group reference 2', 1),
    (r'\g<2>', 'invalid group reference 2', 3),
    (r'\g<', 'missing group name', 3),
    (r'\g<name', 'missing >, unterminated name', 3),
    (r'\g', 'missing <', 2),
    (r'\q', r'bad escape \q', 0),
    ('\\', 'bad escape (end of pattern)', 0),
    (r'\777', r'octal escape value \777 outside of range 0-0o377', 0),
    ('é\n\\q', r'bad escape \q', 2),
    (b'\xff\\q', r'bad escape \q', 1),
]
for replacement, message, position in cases:
    pattern = b'a' if isinstance(replacement, bytes) else 'a'
    for operation in ('sub', 'subn', 'expand'):
        try:
            if operation == 'expand':
                re.match(pattern, pattern).expand(replacement)
            else:
                getattr(re, operation)(pattern, replacement, pattern)
        except re.error as error:
            assert type(error) is re.error
            assert (error.msg, error.pattern, error.pos) == (message, replacement, position)
        else:
            raise AssertionError(('invalid replacement accepted', replacement))
try:
    re.sub('a', r'\g<missing>', 'a')
except IndexError as error:
    assert str(error) == "unknown group name 'missing'"
else:
    raise AssertionError('unknown replacement group accepted')
`, ""},
		{"constructor_and_callback_identity", `import re
error = re.error('problem')
assert (error.msg, error.pattern, error.pos, error.lineno, error.colno) == ('problem', None, None, None, None)
assert error.args == ('problem',) and str(error) == 'problem'
error = re.error(msg='problem', pattern='a\néx', pos=3)
assert (error.lineno, error.colno) == (2, 2)
assert error.args == ('problem at position 3 (line 2, column 2)',)
assert str(error) == error.args[0]
error = re.error(42)
assert error.msg == 42 and error.args == (42,) and str(error) == '42'
class Derived(re.error):
    pass
error = Derived('derived', b'a\n\xffx', 3)
assert isinstance(error, re.error) and error.msg == 'derived'
assert (error.lineno, error.colno) == (2, 2)
for failure in (ValueError('callback'), re.error('callback regex')):
    def replacement(match):
        raise failure
    try:
        re.sub('a', replacement, 'a')
    except Exception as error:
        assert error is failure
    else:
        raise AssertionError('callback exception swallowed')
try:
    re.compile(re.compile('a'), re.I)
except re.error:
    raise AssertionError('argument ValueError was wrapped as re.error')
except ValueError:
    pass
else:
    raise AssertionError('compiled pattern flags accepted')
`, ""},
		{"canonical_class_survives_namespace_replacement", `import re
canonical = re.error
compiled = re.compile('a')
re.error = ValueError
re.PatternError = RuntimeError
for operation in (lambda: re.compile('('), lambda: compiled.sub(r'\2', 'a'), lambda: re.sub('a', r'\2', 'a')):
    try:
        operation()
    except canonical as error:
        assert type(error) is canonical and isinstance(error.msg, str)
    else:
        raise AssertionError('canonical regex error class lost')
`, ""},
		{"class_attributes_are_mutable", `import re
assert not hasattr(re.error, 'regex_error_leak_marker')
re.error.regex_error_leak_marker = 42
assert re.error.regex_error_leak_marker == 42
`, ""},
		{"class_attributes_do_not_leak_between_vms", `import re
assert not hasattr(re.error, 'regex_error_leak_marker')
`, ""},
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
			var stdout, stderr bytes.Buffer
			vm := interp.New(bundle, interp.WithStdout(&stdout), interp.WithStderr(&stderr))
			defer vm.Close()
			if code := vm.Run(); code != 0 {
				t.Fatalf("Run() = %d: %s", code, stderr.String())
			}
			if stdout.String() != test.stdout {
				t.Fatalf("stdout = %q; want %q", stdout.String(), test.stdout)
			}
		})
	}
}
