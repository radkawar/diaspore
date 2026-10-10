"""Bytearray appends retain identity and avoid copying the accumulated prefix."""

import json
import subprocess
from pathlib import Path

from .conftest import LUA_DIR, assert_matches, build, run_cpython, run_diaspore_lua

_STORAGE_PROGRAM = """
def inplace(value, data):
    value += data
    return value

value = bytearray(b'ab')
alias = value
append = value.append
extend = value.extend
value += b'cd'
append(101)
extend([102, 103])
value.insert(len(value), 104)
value[len(value):] = b'ij'
print(value, value is alias, len(value), bool(value))
copy = value.copy()
value[0] = 65
value[1:3] = b'BC'
value.insert(2, 33)
print(value, copy)
view = memoryview(value)
readonly = view.toreadonly()
print(view.obj is value, view.tolist())
view[0] = 120
print(value, bytes(readonly))
for action in [lambda: value.append(0), lambda: value.extend(b'x'),
               lambda: inplace(value, b'x'), lambda: value.insert(len(value), 0)]:
    try:
        action()
    except BufferError:
        print('BufferError', len(value))
print(inplace(value, b'') is value, value.extend([]), bytes(view))
view.release()
try:
    value.append(0)
except BufferError:
    print('readonly export')
readonly.release()
value += b'!'
print(value, value is alias)
try:
    value += value
except BufferError:
    print('self export', value)
value.extend(value)
print(value)
value.reverse()
print(value)
value.clear()
value += value
append(90)
print(value, len(value), value is alias, copy)
value += bytearray(b'abc')
value += memoryview(b'de')
print(value)
strided = memoryview(b'abcdef')[::2]
released = memoryview(b'x')
released.release()
for source in [strided, released]:
    for action in [lambda: inplace(value, source), lambda: value.extend(source)]:
        try:
            action()
        except TypeError:
            print('invalid buffer', value)
"""


def test_lua_bytearray_storage(tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "storage.py"
    entry.write_text(_STORAGE_PROGRAM, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua bytearray storage")


def test_lua_bytearray_concat_buffers(tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "concat_buffers.py"
    entry.write_text(
        """
value = bytearray(b'ab')
value += b'cd'
view = memoryview(value)
released = memoryview(b'x')
released.release()
for source in [b'xy', bytearray(b'xy'), memoryview(b'xy'), value, view]:
    result = value + source
    print(result, result is value, value)
for source in [memoryview(b'1234')[::2], released]:
    try:
        value + source
    except TypeError as error:
        print(type(error).__name__, str(error), value)
view.release()
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(
        run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua bytearray concatenation"
    )


def test_lua_bytearray_append_budget(tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "append_budget.py"
    entry.write_text(
        "value = bytearray()\n"
        "alias = value\n"
        "for i in range(50000):\n"
        "    value += b'x=1'\n"
        "    assert len(value) == (i + 1) * 3\n"
        "    assert bool(value)\n"
        "print(value is alias, len(value), bytes(value) == b'x=1' * 50000)\n",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "append_budget.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    result = subprocess.run(
        [*lua_vm, "run", str(bundle), "--max-steps", "100000000"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected.stdout
    assert result.stderr == ""


def test_lua_bytearray_deferred_materialization(lua_vm: list[str]) -> None:
    program = r"""
local buffer = require('diaspore.buffer')
local fuel = 0
local I = {fuel_n = function(_, amount) fuel = fuel + amount end}
local value = buffer.bytearray('prefix')
for i = 1, 50000 do
  assert(buffer.append(I, value, 'xyz') == value)
  assert(buffer.length(value) == 6 + i * 3)
  assert(rawget(value, '_data') == nil)
end
assert(fuel <= 100000)
local flat = value.b
assert(#flat == 150006 and flat:sub(1, 6) == 'prefix')
assert(rawget(value, '_chunks') == nil)
assert(value.b == flat)
buffer.append(I, value, '!')
assert(rawget(value, '_data') == nil)
assert(value.b == flat .. '!')
value.b = 'reset'
assert(buffer.length(value) == 5 and value.b == 'reset')
buffer.append(I, value, '?')
assert(value.b == 'reset?')
print('deferred materialization passed')
"""
    script = "package.path=" + repr(str(LUA_DIR / "?.lua") + ";") + "..package.path\n" + program
    result = subprocess.run(
        [lua_vm[0], "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "deferred materialization passed\n"
