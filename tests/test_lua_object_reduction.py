"""Default object reduction preserves state, constructor arguments, and overrides."""

from pathlib import Path

import pytest

from diaspore.compiler import compile_bundle

from .conftest import run_cpython
from .test_lua_class_binding import lua_binary as lua_binary
from .test_lua_class_binding import run_bundle


@pytest.mark.parametrize(
    "source",
    [
        """
import copyreg
class Plain:
    pass
p = Plain()
p.answer = 42
for protocol in range(6):
    reduced = p.__reduce_ex__(protocol)
    print(protocol, reduced[0].__name__, reduced[1], reduced[2], len(reduced))
print(p.__getstate__() is p.__dict__)
class Custom:
    def __reduce__(self):
        return "custom"
print(Custom().__reduce_ex__(5))
class Args:
    def __getnewargs__(self):
        return (1, 2)
    def __getstate__(self):
        return {"state": 3}
result = Args().__reduce_ex__(5)
print(result[0].__name__, result[1], result[2:])
class KwArgs:
    def __getnewargs_ex__(self):
        return ((1,), {"key": 2})
result = KwArgs().__reduce_ex__(2)
print(result[0].__name__, result[1], result[2:])
for obj in (None, [1, 2], {"a": 3}):
    result = object.__reduce_ex__(obj, 5)
    print(result[0].__name__, result[1], result[2],
          list(result[3]) if result[3] is not None else None,
          list(result[4]) if result[4] is not None else None)
""",
        """
import copyreg
class Slots:
    __slots__ = ("first", "__hidden", "__dict__")
    def __init__(self):
        self.first = 3
        self.__hidden = 4
        self.extra = 5
s = Slots()
print(s.__getstate__())
print(s.__reduce_ex__(4)[2])
class SlotsOnly:
    __slots__ = ("value",)
x = SlotsOnly()
print(x.__getstate__())
x.value = False
print(x.__getstate__())
for protocol in (0, 1):
    try:
        x.__reduce_ex__(protocol)
    except TypeError as error:
        print(str(error))
class Bad:
    def __getnewargs_ex__(self):
        return ([], {})
try:
    Bad().__reduce_ex__(2)
except TypeError as error:
    print(str(error))
class InvalidSlots:
    __slotnames__ = ()
try:
    InvalidSlots().__getstate__()
except TypeError as error:
    print(str(error))
""",
        """
import copyreg
class Build:
    def __new__(cls, value, *, marker):
        obj = object.__new__(cls)
        obj.value = value
        obj.marker = marker
        return obj
    def __getnewargs_ex__(self):
        return ((self.value,), {"marker": self.marker})
original = Build(7, marker="x")
for protocol in (2, 4, 5):
    reduced = original.__reduce_ex__(protocol)
    restored = reduced[0](*reduced[1])
    print(type(restored) is Build, restored.value, restored.marker)
class BadArgs:
    def __getnewargs__(self):
        return []
class BadEx:
    def __getnewargs_ex__(self):
        return ((),)
class BadKeywords:
    def __getnewargs_ex__(self):
        return ((), [])
for obj in (BadArgs(), BadEx(), BadKeywords()):
    try:
        obj.__reduce_ex__(2)
    except (TypeError, ValueError) as error:
        print(type(error).__name__, str(error))
class StateError:
    def __getstate__(self):
        raise ValueError("state failure")
try:
    StateError().__reduce_ex__(2)
except ValueError as error:
    print(str(error))
""",
    ],
    ids=["protocols_and_constructors", "slots_and_validation", "reconstruction_and_errors"],
)
def test_lua_object_reduction(lua_binary: str, tmp_path: Path, source: str) -> None:
    entry = tmp_path / "reduction.py"
    entry.write_text(source, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = compile_bundle(str(entry))
    result = run_bundle(lua_binary, bundle, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected.stdout
