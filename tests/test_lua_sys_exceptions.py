"""The Lua default exception hook renders sandbox exceptions through live streams."""

import subprocess
import sys
from pathlib import Path

import pytest

from diaspore.compiler import compile_bundle

from .conftest import ROOT, run_cpython
from .test_lua_class_binding import lua_binary as lua_binary
from .test_lua_class_binding import run_bundle

_CAPTURE = '''
import sys
class Sink:
    def __init__(self):
        self.parts = []
    def write(self, text):
        self.parts.append(text)
        return len(text)
    def flush(self):
        pass
saved_stderr = sys.stderr
sink = Sink()
sys.stderr = sink
'''

_CASES = {
    "values_and_notes": '''
assert sys.excepthook is sys.__excepthook__
class Problem(Exception):
    __module__ = "example.errors"
    __qualname__ = "Outer.Problem"
e = Problem("message")
assert e.add_note("first\\nsecond") is None
e.add_note("last\\n")
assert e.with_traceback(None) is e
assert sys.excepthook(int, e, None) is None
for invalid in (42, float("nan"), None, {}, []):
    assert sys.excepthook(None, invalid, None) is None
assert sys.excepthook(None, ValueError(), None) is None
class Broken(Exception):
    def __str__(self):
        raise ValueError("bad string")
sys.excepthook(None, Broken(), None)
for operation in (lambda: e.add_note(), lambda: e.add_note(3),
                  lambda: e.add_note("x", "y"), lambda: e.add_note(note="x"),
                  lambda: e.with_traceback(), lambda: e.with_traceback(3),
                  lambda: e.with_traceback(tb=None), lambda: sys.excepthook(),
                  lambda: sys.excepthook(type=ValueError, value=e, traceback=None)):
    try:
        operation()
    except TypeError as error:
        print(str(error))
e.__notes__ = ()
try:
    e.add_note("invalid")
except TypeError as error:
    print(str(error))
''',
    "chains_and_groups": '''
inner = KeyError("inner")
outer = ValueError("outer")
outer.__context__ = inner
sys.excepthook(None, outer, None)
outer.__cause__ = TypeError("cause")
sys.excepthook(None, outer, None)
outer.__cause__ = None
outer.__suppress_context__ = True
sys.excepthook(None, outer, None)
outer.__cause__ = outer
sys.excepthook(None, outer, None)
group = ExceptionGroup(
    "outer group", [ValueError("one"), ExceptionGroup("nested", [TypeError("two")])]
)
group.add_note("group note")
group.args = ("changed args", [])
print(str(group), group.args)
sys.excepthook(None, group, None)
class BadNotes(Exception):
    @property
    def __notes__(self):
        raise ValueError("notes unavailable")
sys.excepthook(None, BadNotes("bad notes"), None)
''',
    "tracebacks_and_limits": '''
try:
    raise ValueError("captured")
except ValueError as error:
    captured = error
    tb = error.__traceback__
replacement = TypeError("replacement")
sys.excepthook(ValueError, replacement, tb)
print(replacement.__traceback__ is tb)
assert captured.with_traceback(None) is captured
print(captured.__traceback__ is None)
sys.tracebacklimit = 0
sys.excepthook(None, replacement, None)
sys.stderr = None
sys.excepthook(None, ValueError("hidden"), None)
sys.stderr = sink
''',
    "syntax_errors": '''
e = SyntaxError("bad syntax", ("dir/example.py", 3, 5, "    x =\\n", 3, 6))
print(str(e), e.args)
e.add_note("syntax note")
sys.excepthook(None, e, None)
for args in ((), ("bad",), ("bad", [None, None, None, None]),
             ("bad", ["path/example.py", 7, 1, "x"]),
             ("bad", [42, "line", "offset", None])):
    error = SyntaxError(*args)
    fields = ("msg", "filename", "lineno", "offset", "text",
              "end_lineno", "end_offset", "print_file_and_line")
    print(str(error), [getattr(error, name) for name in fields])
for details in ((1, 2, 3), (1, 2, 3, 4, 5), (1, 2, 3, 4, 5, 6, 7), None):
    try:
        SyntaxError("bad", details)
    except TypeError as error:
        print(str(error))
for operation in (lambda: SyntaxError.__init__(), lambda: SyntaxError.__init__(1),
                  lambda: SyntaxError.__init__(ValueError()), lambda: SyntaxError(a=1)):
    try:
        operation()
    except TypeError as error:
        print(str(error))
''',
    "metadata_defaults": '''
e = BaseException.__new__(ValueError)
print(e.args, str(e), e.__traceback__ is None, e.__cause__ is None,
      e.__context__ is None, e.__suppress_context__)
cause = TypeError("cause")
context = KeyError("context")
e.__cause__ = cause
e.__context__ = context
e.add_note("remember")
e.__init__("new")
print(e.args, str(e), e.__cause__ is cause, e.__context__ is context,
      e.__suppress_context__, e.__notes__)
try:
    1 / 0
except ZeroDivisionError as error:
    print(error.__traceback__ is not None, error.__cause__ is None,
          error.__context__ is None, error.__suppress_context__)
syntax = SyntaxError.__new__(SyntaxError)
print(syntax.args, syntax.msg, syntax.filename, syntax.offset)
''',
    "system_exit": '''
raw = SystemExit.__new__(SystemExit)
print(raw.args, raw.code)
for args in ((), (7,), (False,), ("message",), (1, 2)):
    error = SystemExit(*args)
    print(error.args, error.code)
    error.__init__()
    print(error.args, error.code)
    error.__init__(1, 2)
    error.args = (42,)
    print(error.args, error.code)
    error.code = None
    print(error.code)
for args in ((), (None,), (False,), (7,), ("message",)):
    try:
        sys.exit(*args)
    except SystemExit as error:
        print(error.args, error.code)
for operation in (lambda: SystemExit.__init__(), lambda: SystemExit.__init__(1),
                  lambda: SystemExit.__init__(ValueError()), lambda: SystemExit(a=1),
                  lambda: sys.exit(status=1)):
    try:
        operation()
    except TypeError as error:
        print(str(error))
''',
    "exception_protocols": '''import sys

def frames(error):
    result = []
    tb = error.__traceback__
    while tb is not None:
        result.append(tb.tb_frame.f_code.co_name)
        tb = tb.tb_next
    return result

def fail():
    raise ValueError('inner')

def capture():
    try:
        fail()
    except ValueError as error:
        print('caught frames', frames(error))
        assert frames(error) == ['capture', 'fail']
        assert error.__cause__ is None and error.__context__ is None
        return error

original = capture()
try:
    raise original
except ValueError as error:
    print('explicit frames', frames(error))
    assert frames(error) == ['<module>', 'capture', 'fail']

try:
    try:
        raise ValueError('context')
    except ValueError as active:
        try:
            1 / 0
        except ZeroDivisionError as implicit:
            assert implicit.__context__ is active
            assert implicit.__cause__ is None
            assert not implicit.__suppress_context__
        raise RuntimeError('outer') from KeyError('cause')
except RuntimeError as error:
    print('explicit chain', type(error.__context__).__name__,
          type(error.__cause__).__name__, error.__suppress_context__)

try:
    try:
        raise ValueError('hidden')
    except ValueError:
        raise RuntimeError('outer') from None
except RuntimeError as error:
    print('suppressed', type(error.__context__).__name__,
          error.__cause__, error.__suppress_context__)

try:
    raise RuntimeError('outer') from ValueError
except RuntimeError as error:
    assert type(error.__cause__) is ValueError
    print('cause class', error.__cause__.args)

try:
    try:
        raise LookupError('pending')
    finally:
        assert type(sys.exception()) is LookupError
        1 / 0
except ZeroDivisionError as error:
    print('finally context', type(error.__context__).__name__)
assert sys.exception() is None

class Manager:
    def __enter__(self):
        return self
    def __exit__(self, cls, error, tb):
        assert sys.exception() is error
        assert tb is error.__traceback__
        raise RuntimeError('exit')
try:
    with Manager():
        raise ValueError('body')
except RuntimeError as error:
    print('exit context', type(error.__context__).__name__, frames(error))
assert sys.exception() is None

try:
    try:
        raise ValueError('matching')
    except 42:
        pass
except TypeError as error:
    print('matcher context', type(error.__context__).__name__)

def reraiser():
    raise

def reraise_outer():
    try:
        fail()
    except ValueError:
        reraiser()
try:
    reraise_outer()
except ValueError as error:
    print('bare frames', frames(error))
    assert frames(error) == ['<module>', 'reraise_outer', 'reraise_outer', 'fail']

first = ValueError('first')
second = TypeError('second')
first.__context__ = second
try:
    raise first
except ValueError:
    try:
        raise second
    except TypeError as error:
        assert error.__context__ is first
        assert first.__context__ is None
print('cycle broken')

for name, invalid in [('__cause__', 1), ('__context__', 'bad'),
                      ('__traceback__', 1), ('__suppress_context__', 1)]:
    error = Exception()
    try:
        setattr(error, name, invalid)
    except TypeError as invalid_error:
        print('invalid', name, str(invalid_error))
    else:
        raise AssertionError(name)
    try:
        delattr(error, name)
    except TypeError as invalid_error:
        print('delete', name, str(invalid_error))
    else:
        raise AssertionError(name)
print('exception protocols passed')
''',
    "derived_group_metadata": '''
first = ValueError("first")
second = TypeError("second")
original = ExceptionGroup("root", [first, second])
original.__cause__ = KeyError("cause")
original.__context__ = RuntimeError("context")
original.add_note("root note")
matched_groups = []

def record(group, child, note):
    matched_groups.append(group)
    print("subgroup", group is original, group.exceptions[0] is child,
          group.__traceback__ is original.__traceback__,
          group.__cause__ is original.__cause__,
          group.__context__ is original.__context__,
          group.__suppress_context__, sys.exception() is group)
    print("notes", group.__notes__, group.__notes__ is original.__notes__)
    group.add_note(note)
    print("after note", group.__notes__, original.__notes__)

try:
    raise original
except* ValueError as group:
    record(group, first, "value note")
except* TypeError as group:
    record(group, second, "type note")
print("independent notes", matched_groups[0].__notes__ is matched_groups[1].__notes__,
      original.__notes__)

whole = ExceptionGroup("whole", [first])
whole.add_note("whole note")
try:
    raise whole
except* ValueError as group:
    print("whole identity", group is whole,
          group.__traceback__ is whole.__traceback__,
          group.__notes__ is whole.__notes__)
''',
    "exit_traceback_lines": '''
def locations(error):
    result = []
    tb = error.__traceback__
    while tb is not None:
        result.append((tb.tb_frame.f_code.co_name, tb.tb_lineno))
        tb = tb.tb_next
    return result

class Manager:
    def __enter__(self):
        return self
    def __exit__(self, kind, error, tb):
        self.body_error = error
        self.body_tb = tb
        self.body_locations = locations(error)
        raise RuntimeError("exit")

def body():
    raise ValueError("body")

manager = Manager()
try:
    with manager:
        body()
except RuntimeError as error:
    print("exit locations", locations(error))
    print("body locations", locations(manager.body_error))
    print("body preserved", manager.body_tb is manager.body_error.__traceback__,
          manager.body_locations == locations(manager.body_error),
          error.__context__ is manager.body_error)
''',
}


@pytest.mark.parametrize("case", _CASES)
def test_lua_sys_excepthook(lua_binary: str, tmp_path: Path, case: str) -> None:
    entry = tmp_path / "exception_hook.py"
    entry.write_text(
        _CAPTURE + _CASES[case] + '\nsys.stderr = saved_stderr\nprint(repr("".join(sink.parts)))\n',
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    result = run_bundle(lua_binary, compile_bundle(str(entry), debug=True), tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("hook_exit", [None, "integer", "subclass", "mutated", "multiple"])
@pytest.mark.parametrize("inside_handler", [False, True])
def test_lua_uncaught_exception_hook(
    lua_binary: str, tmp_path: Path, hook_exit: str | None, inside_handler: bool
) -> None:
    entry = tmp_path / "uncaught_hook.py"
    exit_source = {
        None: "",
        "integer": "    raise SystemExit(7)\n",
        "subclass": "    raise SystemExit(Status(7))\n",
        "mutated": (
            "    error = SystemExit(1, 2)\n"
            "    error.code = Status(7)\n"
            "    error.args = (9,)\n"
            "    raise error\n"
        ),
        "multiple": "    raise SystemExit(1, 2)\n",
    }[hook_exit]
    entry.write_text(
        '''import sys
class Status(int):
    def __int__(self):
        raise AssertionError("exit status must not call __int__")
    def __str__(self):
        return "wrong"
def hook(kind, value, tb):
    print(kind is ValueError, str(value), tb is value.__traceback__)
    print(sys.last_exc is value, sys.last_value is value,
          sys.last_type is kind, sys.last_traceback is tb)
    print(sys.exc_info() == (None, None, None), sys.exception() is None)
'''
        + exit_source
        + "sys.excepthook = hook\n"
        + (
            (
                "try:\n    raise KeyError('handled')\n"
                "except KeyError:\n    raise ValueError('uncaught')\n"
            )
            if inside_handler
            else "raise ValueError('uncaught')\n"
        ),
        encoding="utf-8",
    )
    expected = subprocess.run(
        [sys.executable, str(entry)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=30,
    )
    result = run_bundle(lua_binary, compile_bundle(str(entry)), tmp_path)
    assert result.returncode == expected.returncode
    assert result.stdout == expected.stdout
    assert result.stderr == expected.stderr
