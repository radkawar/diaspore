package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestExceptionChainingAndReporting(t *testing.T) {
	cases := []struct{ name, source string }{
		{"defaults_and_assignment", `for cls in (BaseException, Exception, ValueError, AttributeError):
    error = cls('message')
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__traceback__ is None
    assert error.__suppress_context__ is False
    cause = RuntimeError('cause')
    error.__context__ = cause
    assert error.__context__ is cause
    assert error.__suppress_context__ is False
    error.__cause__ = cause
    assert error.__cause__ is cause
    assert error.__suppress_context__ is True
    error.__cause__ = None
    assert error.__cause__ is None and error.__suppress_context__ is True
    error.__suppress_context__ = False
    assert error.with_traceback(None) is error
    for name in ('__cause__', '__context__', '__traceback__', '__suppress_context__'):
        try:
            setattr(error, name, 42)
        except TypeError:
            pass
        else:
            raise AssertionError('invalid exception metadata accepted')
        try:
            delattr(error, name)
        except TypeError:
            pass
        else:
            raise AssertionError('exception metadata deletion accepted')
`},
		{"explicit_and_implicit_chains", `first = ValueError('first')
second = TypeError('second')
try:
    try:
        raise first
    except ValueError:
        raise second from first
except TypeError as caught:
    assert caught is second
    assert caught.__cause__ is first
    assert caught.__context__ is first
    assert caught.__suppress_context__ is True
    traceback = caught.__traceback__
    assert caught.with_traceback(None) is caught
    assert caught.__traceback__ is None
    assert caught.with_traceback(traceback) is caught
try:
    try:
        raise first
    except ValueError:
        raise RuntimeError('hidden') from None
except RuntimeError as caught:
    assert caught.__cause__ is None
    assert caught.__context__ is first
    assert caught.__suppress_context__ is True
try:
    raise RuntimeError('class cause') from KeyError
except RuntimeError as caught:
    assert isinstance(caught.__cause__, KeyError)
try:
    raise RuntimeError('bad cause') from 42
except TypeError:
    pass
else:
    raise AssertionError('invalid cause accepted')
try:
    try:
        raise first
    except ValueError:
        missing_chain_name
except NameError as caught:
    assert caught.__context__ is first
    assert caught.__cause__ is None
    assert caught.__suppress_context__ is False
`},
		{"finally_and_reraise", `import sys
first = ValueError('first')
second = TypeError('second')
try:
    try:
        raise first
    finally:
        assert sys.exception() is first
        raise second
except TypeError as caught:
    assert caught is second and caught.__context__ is first
assert sys.exception() is None
try:
    try:
        raise first
    except ValueError:
        try:
            raise second
        except TypeError:
            raise first
except ValueError as caught:
    assert caught is first
    assert first.__context__ is second
    assert second.__context__ is None
try:
    try:
        raise first
    except ValueError:
        raise
except ValueError as caught:
    assert caught is first and first.__context__ is second
`},
		{"unittest_records_failures_and_continues", `import unittest
class Cases(unittest.TestCase):
    def test_error(self):
        try:
            raise ValueError('original cause')
        except ValueError as error:
            raise RuntimeError('reported error') from error
    def test_failure(self):
        self.assertEqual(1, 2)
    def test_pass(self):
        self.assertTrue(True)
result = unittest.TestResult()
suite = unittest.TestSuite([Cases('test_error'), Cases('test_failure'), Cases('test_pass')])
suite.run(result)
assert result.testsRun == 3
assert len(result.errors) == 1
assert len(result.failures) == 1
assert not result.wasSuccessful()
assert 'ValueError: original cause' in result.errors[0][1]
assert 'RuntimeError: reported error' in result.errors[0][1]
assert 'AssertionError' in result.failures[0][1]
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
