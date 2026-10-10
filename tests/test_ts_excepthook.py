"""Default exception display uses sandbox streams, tracebacks, chains, and notes."""

import json
import subprocess
from pathlib import Path

import pytest

from diaspore.compiler import compile_bundle
from diaspore.validator import validate_bundle

from .conftest import Result, assert_matches, build, run_cpython

_SOURCE = r"""
import sys


class Sink:
    def __init__(self):
        self.parts = []
        self.flushes = 0

    def write(self, text):
        self.parts.append(text)

    def flush(self):
        self.flushes += 1


sink = Sink()
original = sys.stderr
sys.stderr = sink
print(sys.excepthook is sys.__excepthook__)


def show(value, tb=None, kind=None):
    sink.parts.clear()
    before = sink.flushes
    result = sys.excepthook(kind, value, tb)
    print(result, ''.join(sink.parts), sink.flushes - before)


for error in [ValueError('plain'), ValueError(), KeyError('key'),
              OSError(2, 'missing', 'file'), 42, None, 'not an exception']:
    show(error)


class Named(ValueError):
    pass


Named.__module__ = 'example.errors'
Named.__qualname__ = 'Outer.Named'
show(Named('qualified'))
Named.__module__ = 42
show(Named('unknown module'))


class BadString(ValueError):
    def __str__(self):
        raise RuntimeError('formatting failed')


show(BadString())
error = ValueError('notes')
error.add_note('first')
error.add_note('second\ncontinued')
show(error)
error.__notes__ = ['trailing\n', '', BadString()]
show(error)
error.__notes__ = 'not a sequence of notes'
show(error)
error.__notes__ = None
show(error)

cause = ValueError('cause')
outer = TypeError('outer')
outer.__cause__ = cause
show(outer)
outer.__cause__ = None
outer.__context__ = cause
outer.__suppress_context__ = False
show(outer)
outer.__suppress_context__ = True
show(outer)
cause.__cause__ = outer
outer.__cause__ = cause
show(outer)


def captured():
    try:
        raise ValueError('raised')
    except ValueError as error:
        return error


raised = captured()
show(raised, None, str)
fresh = RuntimeError('fresh')
show(fresh, raised.__traceback__, object())
print(fresh.__traceback__ is raised.__traceback__)
show(RuntimeError('bad traceback ignored'), 42)
sys.tracebacklimit = 0
show(raised)
del sys.tracebacklimit

show(ExceptionGroup('group', [ValueError('one'), TypeError('two')]))
show(ExceptionGroup('outer', [ExceptionGroup('inner', [ValueError('leaf')]),
                              RuntimeError('last')]))
show(SyntaxError('invalid', ('example.py', 3, 5, '    bad +\n', 3, 8)))
show(SyntaxError('invalid', (None, 7, None, None)))

for invoke in [lambda: sys.excepthook(), lambda: sys.excepthook(None, None),
               lambda: sys.excepthook(None, None, None, None),
               lambda: sys.excepthook(exctype=None, value=None, traceback=None)]:
    try:
        invoke()
    except TypeError as error:
        print(type(error).__name__, str(error))

sys.stderr = None
print(sys.excepthook(None, ValueError('silent'), None))
sys.stderr = sink


class BadFlush(Sink):
    def flush(self):
        raise RuntimeError('flush failed')


bad = BadFlush()
sys.stderr = bad
print(sys.excepthook(None, ValueError('flush ignored'), None))
print(''.join(bad.parts))
sys.stderr = original
"""


def test_ts_excepthook(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "exception_display.py"
    entry.write_text(_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "exception_display.dsp.json"
    # Source authority comes from embedded debug metadata, never host filesystem reads.
    compiled = compile_bundle(str(entry), debug=True)
    validate_bundle(compiled)
    bundle.write_text(json.dumps(compiled), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript default exception display")


@pytest.mark.parametrize("hook", ["custom", "failing", "missing"])
def test_ts_uncaught_excepthook(tmp_path: Path, ts_vm: list[str], hook: str) -> None:
    setup = """
import sys


class Stream:
    def write(self, text):
        print(text, end='')

    def flush(self):
        pass


sys.stderr = Stream()
sys.tracebacklimit = 0
"""
    hooks = {
        "custom": """
def hook(kind, value, tb):
    print('custom', kind.__name__, str(value), tb is value.__traceback__)
    print(sys.last_exc is value, sys.last_value is value,
          sys.last_type is kind, sys.last_traceback is tb)


sys.excepthook = hook
""",
        "failing": """
def hook(kind, value, tb):
    raise RuntimeError('hook failed')


sys.excepthook = hook
""",
        "missing": "del sys.excepthook\n",
    }
    entry = tmp_path / f"uncaught_{hook}.py"
    entry.write_text(setup + hooks[hook] + "\nraise ValueError('uncaught')\n", encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 1, expected
    bundle = tmp_path / f"uncaught_{hook}.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 1, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, f"TypeScript uncaught exception hook: {hook}")
