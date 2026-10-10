package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeAuditHooks(t *testing.T) {
	cases := []struct{ name, source string }{
		{"validation_and_arguments", `import sys
assert sys.audit(42, None) is None
for function, args, kw, message in (
    (sys.audit, (), {}, "audit() missing 1 required positional argument: 'event'"),
    (sys.audit, (), {'event': 'x'}, 'sys.audit() takes no keyword arguments'),
    (sys.addaudithook, (), {}, "addaudithook() missing required argument 'hook' (pos 1)"),
    (sys.addaudithook, (1, 2), {}, 'addaudithook() takes at most 1 argument (2 given)'),
):
    try:
        function(*args, **kw)
    except TypeError as exc:
        assert str(exc) == message
    else:
        raise AssertionError('invalid audit arguments accepted')
seen = []
assert sys.addaudithook(hook=lambda event, args: seen.append((event, args))) is None
token = []
assert sys.audit('probe', token, 7) is None
assert seen == [('probe', (token, 7))]
assert seen[0][1][0] is token
class Event(str):
    def __str__(self):
        raise AssertionError('event conversion must use native Unicode value')
sys.audit(Event('typed'))
assert seen[-1] == ('typed', ())
assert type(seen[-1][0]) is str
sys.audit('prefix\0suffix')
assert seen[-1] == ('prefix', ())
try:
    sys.audit(42)
except TypeError as exc:
    assert str(exc) == "expected str for argument 'event', not int"
else:
    raise AssertionError('non-string event accepted with active hooks')
try:
    sys.audit('a\ud800\ud801z')
except UnicodeEncodeError as exc:
    assert (exc.start, exc.end, exc.encoding, exc.reason) == (1, 3, 'utf-8', 'surrogates not allowed')
else:
    raise AssertionError('surrogate audit event accepted')
`},
		{"live_reentrant_dispatch", `import sys
seen = []
def second(event, args):
    seen.append(('second', event, args))
def first(event, args):
    seen.append(('first', event, args))
    if event == 'outer':
        sys.addaudithook(second)
        sys.audit('nested', 1)
sys.addaudithook(first)
token = []
sys.audit('outer', token)
assert [(name, event) for name, event, args in seen] == [
    ('first', 'outer'), ('first', 'sys.addaudithook'),
    ('first', 'nested'), ('second', 'nested'), ('second', 'outer')]
assert seen[0][2] is seen[-1][2]
assert seen[2][2] is seen[3][2]
assert seen[0][2][0] is token
`},
		{"registration_veto_and_error_propagation", `import sys
seen = []
class Halt(BaseException):
    pass
mode = [ValueError]
def veto(event, args):
    if event == 'sys.addaudithook':
        raise mode[0]('registration rejected')
    if event == 'denied':
        raise LookupError('event rejected')
    seen.append(event)
sys.addaudithook(veto)
assert sys.addaudithook(lambda event, args: seen.append('must not register')) is None
sys.audit('ok')
assert seen == ['ok']
assert sys.exc_info() == (None, None, None)
mode[0] = Halt
try:
    sys.addaudithook(lambda event, args: None)
except Halt as exc:
    assert str(exc) == 'registration rejected'
else:
    raise AssertionError('BaseException veto suppressed')
try:
    sys.audit('denied')
except LookupError as exc:
    assert str(exc) == 'event rejected'
else:
    raise AssertionError('audit exception suppressed')
assert seen == ['ok']
`},
		{"deferred_callable_validation", `import sys
assert sys.addaudithook(42) is None
for attempt in range(2):
    try:
        sys.audit('event')
    except TypeError as exc:
        assert str(exc) == "'int' object is not callable"
    else:
        raise AssertionError('non-callable audit hook accepted during dispatch')
    assert sys.addaudithook(lambda event, args: None) is None
`},
		{"cantrace_attribute_protocol", `import sys
seen = []
class TraceFlag:
    def __bool__(self):
        seen.append('flag')
        return False
def hook(event, args):
    seen.append('hook')
hook.__cantrace__ = TraceFlag()
sys.addaudithook(hook)
sys.audit('event')
assert seen == ['flag', 'hook']
class BadFlag:
    def __bool__(self):
        raise ValueError('flag failed')
hook.__cantrace__ = BadFlag()
try:
    sys.audit('event')
except ValueError as exc:
    assert str(exc) == 'flag failed'
else:
    raise AssertionError('cantrace conversion error ignored')
assert seen == ['flag', 'hook']
`},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			compiled, err := dspc.CompileSource("/src/main.py", test.source, dspc.CompileOptions{NoStdlib: true})
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
		})
	}
}
