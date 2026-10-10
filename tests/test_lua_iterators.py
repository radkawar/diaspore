"""Lua iteration dispatches through an object's type and preserves its identity."""

from pathlib import Path

from diaspore.compiler import compile_bundle

from .conftest import run_cpython
from .test_lua_class_binding import lua_binary as lua_binary
from .test_lua_class_binding import run_bundle


def test_lua_metaclass_iterators(lua_binary: str, tmp_path: Path) -> None:
    entry = tmp_path / "metaclass_iterators.py"
    entry.write_text(
        """
class IterableMeta(type):
    def __iter__(cls):
        return iter((3, 5, 8))
class Sequence(metaclass=IterableMeta):
    pass
print(list(Sequence), tuple(Sequence), sum(Sequence))
a, b, c = Sequence
print(a, b, c)
class CursorMeta(type):
    def __iter__(cls):
        return cls
    def __next__(cls):
        cls.position += 1
        if cls.position == 2:
            raise ValueError("recover")
        if cls.position == 4:
            raise StopIteration("finished")
        return cls.position
class Cursor(metaclass=CursorMeta):
    position = 0
print(iter(Cursor) is Cursor, next(Cursor))
try:
    next(Cursor, "default")
except ValueError as error:
    print(str(error))
print(next(Cursor), next(Cursor, "end"), next(Cursor))
class InvalidMeta(type):
    def __iter__(cls):
        return []
class Invalid(metaclass=InvalidMeta):
    pass
try:
    iter(Invalid)
except TypeError as error:
    print(str(error))
class InstanceOnly:
    def __iter__(self):
        return iter(())
try:
    iter(InstanceOnly)
except TypeError as error:
    print(type(error).__name__)
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    result = run_bundle(lua_binary, compile_bundle(str(entry), stdlib=False), tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected.stdout
