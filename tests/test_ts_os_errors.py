"""Structured POSIX OSError construction, subclass selection, and formatting."""

import json
import subprocess
from pathlib import Path

from .conftest import Result, assert_matches, build, run_cpython

_SOURCE = """
def show(error):
    print(type(error).__name__, error.args, error.errno, error.strerror,
          error.filename, error.filename2, str(error))


for args in [(), ('message',), (22, 'invalid'), (None, None),
             (22, 'invalid', None), (22, 'invalid', 'a'),
             (22, 'invalid', b'a', 100), (22, 'invalid', 'a', 100, 'b'),
             (22, 'invalid', None, 100, 'b'), tuple(range(6))]:
    show(OSError(*args))
for number in [1, 2, 3, 4, 10, 11, 13, 17, 20, 21, 22, 32, 103, 104, 108, 110, 111, 114, 115]:
    show(OSError(number, 'error'))
show(OSError('2', 'string errno'))
show(OSError(2.0, 'float errno'))
show(PermissionError(2, 'preserve explicit type', 'file'))


class Plain(OSError):
    pass


class Custom(OSError):
    def __init__(self, message, **kwargs):
        super().__init__(22, message, kwargs.get('filename', 'source'))


class NewAndInit(OSError):
    def __new__(cls, message):
        return super().__new__(cls, 13, message, 'new-file')
    def __init__(self, message):
        super().__init__(2, 'ignored', 'ignored-file')


show(Plain(2, 'no automatic subclass'))
show(Custom('custom', filename='from-keyword'))
show(NewAndInit('new'))
error = Custom('first')
OSError.__init__(error, 2, 'second')
show(error)
error = OSError(22, 'first')
error.__init__(2, 'ignored')
show(error)
error.filename = None
show(error)
error.filename2 = 'next'
show(error)
del error.filename
show(error)
error.errno = 'changed'
error.strerror = 'detail'
show(error)
del error.errno
show(error)

for amount in [-1, 0, 3, True, 1.5, 2**100]:
    try:
        error = BlockingIOError(11, 'blocked', amount)
        print(error.args, error.filename,
              getattr(error, 'characters_written', 'absent'), str(error))
    except Exception as error:
        print(type(error).__name__, str(error))
error = BlockingIOError(11, 'blocked', 3)
error.characters_written = 7
print(error.characters_written)
del error.characters_written
print(hasattr(error, 'characters_written'))


class Index:
    def __index__(self):
        return 9


error = BlockingIOError(11, 'blocked', Index())
print(error.characters_written, error.filename, str(error))
try:
    OSError(errno=22)
except TypeError as error:
    print(type(error).__name__, str(error))
"""


def test_ts_oserror_protocol(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "oserror_protocol.py"
    entry.write_text(_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "oserror_protocol.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript OSError protocol")
