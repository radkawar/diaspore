"""Per-interpreter audit delivery, registration vetoes, and reentrant ordering."""

import json
import subprocess
from pathlib import Path

from .conftest import Result, assert_matches, build, run_cpython

_SOURCE = r"""
import sys

print(sys.audit(123), sys.audit('probe.before', 1))
for invoke in [lambda: sys.audit(), lambda: sys.audit(event='probe.kw'),
               lambda: sys.addaudithook(), lambda: sys.addaudithook(other=None),
               lambda: sys.addaudithook(None, hook=None),
               lambda: sys.addaudithook(hook=None, other=None)]:
    try:
        invoke()
    except TypeError as error:
        print(type(error).__name__)

events = []
mode = None
appended = False


def third(event, args):
    if event.startswith('probe.') or event == 'sys.addaudithook':
        events.append(('third', event, args))


def first(event, args):
    global appended
    if event.startswith('probe.') or event == 'sys.addaudithook':
        events.append(('first', event, args, type(args).__name__, type(event).__name__))
    if event == 'sys.addaudithook' and mode is not None:
        raise mode('veto')
    if event == 'probe.outer':
        sys.audit('probe.inner', 7)
    if event == 'probe.append' and not appended:
        appended = True
        sys.addaudithook(third)
    if event == 'probe.fail':
        raise ValueError('stop delivery')


class TraceFlag:
    def __bool__(self):
        events.append(('trace flag',))
        return False


class Hook:
    __cantrace__ = TraceFlag()

    def __call__(self, event, args):
        if event.startswith('probe.') or event == 'sys.addaudithook':
            events.append(('second', event, args))


print(sys.addaudithook(first), sys.addaudithook(hook=Hook()))
sys.audit('probe.args', 1, None, {'a': [2]})
sys.audit('probe.outer')
sys.audit('probe.append')
print(events)
events.clear()
for failure in [RuntimeError, ValueError, KeyboardInterrupt]:
    mode = failure
    try:
        print('register', failure.__name__, sys.addaudithook(third))
    except BaseException as error:
        print('register raised', type(error).__name__, str(error))
mode = None
sys.audit('probe.after')
print(events)
events.clear()
try:
    sys.audit('probe.fail')
except ValueError as error:
    print(type(error).__name__, str(error))
print(events)
events.clear()


class Event(str):
    def __str__(self):
        raise AssertionError('event __str__ must not run')


sys.audit(Event('probe.subclass'))
sys.audit('probe.nul\x00ignored', 9)
for event in [42, None, b'probe.bytes', 'probe.\ud800']:
    try:
        sys.audit(event)
    except Exception as error:
        print(type(error).__name__, str(error))
print(events)
events.clear()
print('noncallable registration', sys.addaudithook(hook=None))
try:
    sys.audit('probe.noncallable')
except TypeError as error:
    print(type(error).__name__, str(error))
print(events)
"""


def test_ts_audit_hooks(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "audit_hooks.py"
    entry.write_text(_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "audit_hooks.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript audit hooks")
