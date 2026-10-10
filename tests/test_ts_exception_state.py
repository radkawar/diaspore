"""CPython exception metadata, explicit chaining, notes, and traceback state."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from diaspore import compile_bundle

from .conftest import TS_DIR, Result, assert_matches, run_cpython


@pytest.mark.parametrize(
    "source",
    [
        """for name in ['__cause__', '__context__', '__suppress_context__', '__traceback__']:
    error = ValueError('message')
    print(name, getattr(error, name), error.__dict__)
    for value in [None, ValueError('cause'), ValueError, 0, False, True, 'text', []]:
        try:
            setattr(error, name, value)
            print('set', type(value).__name__, getattr(error, name) is value,
                  error.__suppress_context__)
        except TypeError as caught:
            print('invalid', type(value).__name__, type(caught).__name__, str(caught))
    try:
        delattr(error, name)
    except TypeError as caught:
        print('delete', type(caught).__name__, str(caught))
error = ValueError('message')
error.__cause__ = None
print(error.__cause__, error.__suppress_context__)
error.__suppress_context__ = False
error.__context__ = TypeError('context')
print(type(error.__context__).__name__, error.__suppress_context__, error.__dict__)
for name in ['__cause__', '__context__', '__suppress_context__', '__traceback__']:
    error = ValueError()
    error.__dict__[name] = 'shadow'
    print(name, getattr(error, name), error.__dict__[name])
""",
        """class Text(str):
    pass
class Notes(list):
    def append(self, note):
        raise AssertionError('append override must not run')
for note in ['plain', Text('subclass'), None, 1, b'bytes']:
    error = ValueError()
    print('initial', hasattr(error, '__notes__'))
    try:
        print('return', error.add_note(note))
        print(error.__notes__, error.__notes__[0] is note,
              type(error.__notes__).__name__, error.__dict__['__notes__'] is error.__notes__)
    except TypeError as caught:
        print(type(caught).__name__, str(caught), hasattr(error, '__notes__'))
for notes in [None, (), ['old'], Notes(['old']), 1]:
    error = ValueError()
    error.__notes__ = notes
    try:
        print(error.add_note('new'), error.__notes__, error.__notes__ is notes)
    except TypeError as caught:
        print(type(caught).__name__, str(caught))
error = ValueError()
error.add_note('first')
old = error.__notes__
del error.__notes__
print(hasattr(error, '__notes__'), old)
error.add_note('second')
print(error.__notes__, error.__notes__ is old)
for method in ['add_note', 'with_traceback']:
    for args, kwargs in [((), {}), (('a', 'b'), {}), ((), {'note': 'a'})]:
        try:
            getattr(error, method)(*args, **kwargs)
        except TypeError as caught:
            print(method, type(caught).__name__, str(caught))
""",
        """root = ValueError('root')
implicit = TypeError('implicit')
try:
    try:
        raise root
    except ValueError:
        raise implicit
except TypeError as caught:
    print(caught is implicit, caught.__context__ is root,
          caught.__cause__, caught.__suppress_context__)
explicit = RuntimeError('explicit')
cause = KeyError('cause')
try:
    try:
        raise root
    except ValueError:
        raise explicit from cause
except RuntimeError as caught:
    print(caught is explicit, caught.__context__ is root,
          caught.__cause__ is cause, caught.__suppress_context__)
try:
    try:
        raise root
    except ValueError:
        raise TypeError('silent') from None
except TypeError as caught:
    print(caught.__context__ is root, caught.__cause__, caught.__suppress_context__)
try:
    raise RuntimeError('class cause') from ValueError
except RuntimeError as caught:
    print(type(caught.__cause__).__name__, caught.__cause__.args,
          caught.__context__, caught.__suppress_context__)
try:
    raise ValueError('invalid cause') from 42
except TypeError as caught:
    print(type(caught).__name__, str(caught))
""",
        """first = ValueError('first')
second = TypeError('second')
try:
    try:
        raise first
    except ValueError:
        raise second
except TypeError:
    pass
print(first.__context__, second.__context__ is first)
try:
    try:
        raise second
    except TypeError:
        raise first
except ValueError:
    pass
print(first.__context__ is second, second.__context__)
try:
    try:
        raise first
    except ValueError as active:
        raise
except ValueError as caught:
    print(caught is first, caught.__context__ is second, second.__context__)
self_linked = RuntimeError('self')
self_linked.__cause__ = self_linked
self_linked.__context__ = self_linked
try:
    raise self_linked
except RuntimeError as caught:
    print(caught is self_linked, caught.__cause__ is caught,
          caught.__context__ is caught, caught.__suppress_context__)
""",
        """def capture():
    try:
        raise ValueError('captured')
    except ValueError as error:
        return error, error.__traceback__
original, traceback = capture()
print(traceback is not None, original.__traceback__ is traceback)
replacement = TypeError('replacement')
print(replacement.with_traceback(traceback) is replacement,
      replacement.__traceback__ is traceback)
replacement.__traceback__ = None
print(replacement.__traceback__)
replacement.__traceback__ = traceback
print(replacement.__traceback__ is traceback)
print(replacement.with_traceback(None) is replacement, replacement.__traceback__)
try:
    raise replacement.with_traceback(traceback)
except TypeError as caught:
    print(caught is replacement, caught.__traceback__ is not traceback,
          caught.__traceback__.tb_next is traceback)
for invalid in [False, 0, ValueError(), [], 'traceback']:
    try:
        replacement.with_traceback(invalid)
    except TypeError as caught:
        print(type(caught).__name__, str(caught))
""",
        """for cls in [ExceptionGroup, BaseExceptionGroup]:
    for children in [[ValueError('one')], [ValueError('one'), TypeError('two')]]:
        group = cls('message', children)
        print(type(group).__name__, str(group), group.message, len(group.exceptions))
        print(group.exceptions[0] is children[0])
    try:
        cls('empty', [])
    except ValueError as error:
        print(type(error).__name__, str(error))
for children in [[KeyboardInterrupt()], [KeyboardInterrupt(), ValueError('one')]]:
    group = BaseExceptionGroup('base message', children)
    print(type(group).__name__, str(group), len(group.exceptions))
class CustomGroup(ExceptionGroup):
    def __str__(self):
        return 'custom group: ' + self.message
class InheritedGroup(CustomGroup):
    pass
for cls in [CustomGroup, InheritedGroup]:
    group = cls('details', [ValueError('child')])
    print(type(group).__name__, str(group))
    print(BaseExceptionGroup.__str__(group))
class NestedGroup(ExceptionGroup):
    pass
group = NestedGroup('outer', [ExceptionGroup('inner', [ValueError('leaf')])])
print(str(group), str(group.exceptions[0]))
""",
        """for protocol in range(6):
    error = ValueError('message', 2)
    result = error.__reduce_ex__(protocol)
    print(protocol, len(result), result[0] is ValueError, result[1])
    error.__cause__ = TypeError('cause')
    error.__context__ = RuntimeError('context')
    result = error.__reduce_ex__(protocol)
    print('native state', len(result), result[1])
    dictionary = error.__dict__
    result = error.__reduce_ex__(protocol)
    print('allocated', len(result), result[2] is dictionary, result[2])
    error.add_note('note')
    error.extra = 4
    result = error.__reduce_ex__(protocol)
    print('user state', result[2] is dictionary, result[2])
    restored = result[0](*result[1])
    print(restored.__setstate__(result[2]), restored.args,
          restored.__notes__, restored.extra, restored.__cause__, restored.__context__)
class CustomState(dict):
    def items(self):
        raise AssertionError('state items override must not run')
class Tracked(Exception):
    def __setattr__(self, name, value):
        print('setattr', name, value)
        super().__setattr__(name, value)
error = Tracked('original')
print(error.__setstate__(CustomState(extra=1, args=['changed', 2])))
print(error.args, error.extra, error.__dict__)
for state in [None, [], 1, {'args': ['new']}, {'first': 1, 3: 4, 'last': 2}]:
    error = ValueError('old')
    try:
        print('setstate', error.__setstate__(state), error.args, error.__dict__)
    except TypeError as caught:
        print(type(caught).__name__, str(caught), error.args, error.__dict__)
error = ValueError('direct')
print(error.__reduce__()[0] is ValueError, error.__reduce__()[1])
state = {}
error.__dict__ = state
print(error.__reduce__()[2] is state)
""",
    ],
    ids=[
        "metadata-descriptors",
        "exception-notes",
        "raise-chaining",
        "repeated-raise-context-cycles",
        "traceback-reuse",
        "exception-group-string",
        "exception-reduction-state",
    ],
)
def test_ts_exception_state_matches_cpython(tmp_path: Path, source: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    entry = tmp_path / "exception_state.py"
    entry.write_text(source, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    path = tmp_path / "exception_state.dsp.json"
    path.write_text(json.dumps(compile_bundle(str(entry), stdlib=False)), encoding="utf-8")
    process = subprocess.run(
        [
            node,
            "--experimental-strip-types",
            "--no-warnings",
            str(TS_DIR / "src/cli.ts"),
            "run",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = process.stderr.strip().splitlines()
    actual = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(actual, expected, "TypeScript exception state")
