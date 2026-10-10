package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestCodePositionsTrackExecutedStatements(t *testing.T) {
	const source = `import sys
import traceback
def fail():
    """A docstring is not an executed statement."""
    if True:
        return 1 / 0
try:
    fail()
except ZeroDivisionError as error:
    tb = error.__traceback__
    frames = 0
    while tb is not None:
        positions = list(tb.tb_frame.f_code.co_positions())
        assert tb.tb_lasti >= 0 and tb.tb_lasti % 2 == 0
        position = positions[tb.tb_lasti // 2]
        assert position == (tb.tb_lineno, None, None, None)
        if tb.tb_frame.f_code.co_name == 'fail':
            assert tb.tb_lineno == 6
        elif tb.tb_frame.f_code.co_name == '<module>':
            assert tb.tb_lineno == 8
        frames += 1
        tb = tb.tb_next
    assert frames == 2
    formatted = ''.join(traceback.format_exception(error))
    assert 'line 6, in fail' in formatted
    assert 'ZeroDivisionError' in formatted
try:
    class Broken:
        raise ValueError('class body')
except ValueError as error:
    tb = error.__traceback__
    while tb.tb_next is not None:
        tb = tb.tb_next
    assert tb.tb_frame.f_code.co_name == 'Broken'
    assert list(tb.tb_frame.f_code.co_positions())[tb.tb_lasti // 2][0] == tb.tb_lineno
frame = sys._getframe()
assert list(frame.f_code.co_positions())[frame.f_lasti // 2][0] == frame.f_lineno
first = fail.__code__.co_positions()
second = fail.__code__.co_positions()
assert first is not second
assert list(first) == list(second)
assert list(first) == []
for invoke in (lambda: fail.__code__.co_positions(1), lambda: fail.__code__.co_positions(extra=1)):
    try:
        invoke()
    except TypeError:
        pass
    else:
        raise AssertionError('co_positions accepted arguments')
`
	compiled, err := dspc.CompileSource("/src/positions.py", source, dspc.CompileOptions{Debug: true})
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
}

func TestCodePositionsWithoutDebugAndInSnippets(t *testing.T) {
	const source = `import traceback
def fail():
    raise ValueError('stripped')
assert len(list(fail.__code__.co_positions())) == 1
assert list(fail.__code__.co_positions()) == [(None, None, None, None)]
for operation in (fail, lambda: exec("raise ValueError('exec')"), lambda: eval('1 / 0')):
    try:
        operation()
    except Exception as error:
        tb = error.__traceback__
        count = 0
        while tb is not None:
            positions = list(tb.tb_frame.f_code.co_positions())
            assert 0 <= tb.tb_lasti // 2 < len(positions)
            assert len(positions[tb.tb_lasti // 2]) == 4
            count += 1
            tb = tb.tb_next
        assert count >= 2
        assert type(error).__name__ in ''.join(traceback.format_exception(error))
`
	compiled, err := dspc.CompileSource("/src/positions.py", source, dspc.CompileOptions{})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	// Debug controls source text, not line fields; exercise an actually stripped bundle.
	var stripLines func(any)
	stripLines = func(value any) {
		switch node := value.(type) {
		case map[string]any:
			delete(node, "ln")
			for _, child := range node {
				stripLines(child)
			}
		case []any:
			for _, child := range node {
				stripLines(child)
			}
		}
	}
	stripLines(bundle)
	var stderr bytes.Buffer
	vm := interp.New(bundle, interp.WithStderr(&stderr), interp.WithSnippetCompiler(dspc.CompileSnippet))
	defer vm.Close()
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d: %s", code, stderr.String())
	}
}
