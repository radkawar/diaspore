"""Check portable C-layer state transitions against their CPython providers."""

import importlib.util
import io
import stat
from collections import deque
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_provider(name):
    spec = importlib.util.spec_from_file_location(
        "portable_" + name, ROOT / "src/diaspore/stdlib" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def outcome(function, *args):
    try:
        return function(*args)
    except Exception as error:
        return type(error).__name__, str(error)


@pytest.mark.parametrize("newline", [None, "", "\n", "\r", "\r\n"])
@pytest.mark.parametrize("text", ["alpha\r\nbeta\rgamma\n", "é😀\ud800\n", "", "\r\n\r\n"])
def test_stringio_character_offsets_and_newlines(newline, text):
    portable = load_provider("_io").StringIO(text, newline=newline)
    native = io.StringIO(text, newline=newline)
    for operation, arguments in (
        ("getvalue", ()),
        ("readline", ()),
        ("tell", ()),
        ("seek", (0,)),
        ("readline", (2,)),
        ("tell", ()),
        ("read", (1,)),
        ("tell", ()),
        ("write", ("X\r\nY",)),
        ("getvalue", ()),
        ("tell", ()),
        ("seek", (30,)),
        ("write", ("!",)),
        ("getvalue", ()),
        ("truncate", (4,)),
        ("tell", ()),
        ("getvalue", ()),
        ("seek", (0, 2)),
        ("read", ()),
    ):
        assert outcome(getattr(portable, operation), *arguments) == outcome(
            getattr(native, operation), *arguments
        ), (operation, arguments)
        assert portable.newlines == native.newlines


@pytest.mark.parametrize("newline", [None, "", "\n", "\r", "\r\n"])
def test_stringio_pickle_state(newline):
    portable_class = load_provider("_io").StringIO
    original = io.StringIO("a\r\nb\n", newline=newline)
    original.seek(2)
    state = original.__getstate__()
    restored = portable_class()
    restored.__setstate__(state)
    assert restored.getvalue() == original.getvalue()
    assert restored.tell() == original.tell()
    assert restored.__getstate__() == state


def test_stat_requires_unsigned_mode_integer():
    portable = load_provider("_stat")

    class Indexable:
        def __index__(self):
            return 0o100640

    for value in (Indexable(), -1, 1 << 32, 1 << 64, True, 0o100640):
        for name in ("filemode", "S_IMODE", "S_IFMT", "S_ISREG"):
            assert outcome(getattr(portable, name), value) == outcome(getattr(stat, name), value)


def test_reverse_deque_exhaustion_precedes_mutation():
    portable = load_provider("_collections")
    for factory in (iter, reversed):
        actual, expected = portable.deque([1]), deque([1])
        actual_iterator, expected_iterator = factory(actual), factory(expected)
        assert next(actual_iterator) == next(expected_iterator)
        actual.append(2)
        expected.append(2)
        assert outcome(next, actual_iterator) == outcome(next, expected_iterator)
        assert outcome(next, actual_iterator) == outcome(next, expected_iterator)


def test_stringio_flush_after_close():
    portable, native = load_provider("_io").StringIO("value"), io.StringIO("value")
    portable.close()
    native.close()
    for operation in ("flush", "getvalue", "read", "tell"):
        assert outcome(getattr(portable, operation)) == outcome(getattr(native, operation))


@pytest.mark.parametrize(
    "format,value", [(">e", 65520), (">e", 65520.0), (">f", 10**40), (">f", 1e40), ("P", 1 << 64)]
)
def test_struct_integer_and_float_overflow_categories(format, value):
    import struct

    portable = load_provider("_struct")
    assert outcome(portable.pack, format, value) == outcome(struct.pack, format, value)


def test_environment_name_error_contracts(tmp_path):
    from .conftest import run_cpython, run_diaspore_py

    source = tmp_path / "environment_errors.py"
    source.write_text("""
import posix
for name in ("", "a=b"):
    for operation in (posix.putenv, posix.unsetenv):
        try:
            if operation is posix.putenv:
                operation(name, "value")
            else:
                operation(name)
        except Exception as error:
            print(type(error).__name__, str(error))
""")
    expected = run_cpython(source)
    assert expected.code == 0, expected.err_line
    assert run_diaspore_py(source) == expected


def test_lock_blocking_uses_truth_value():
    portable = load_provider("_thread")
    lock = portable.allocate_lock()
    assert lock.acquire("blocking")
    assert not lock.acquire("")
    lock.release()
    assert lock.acquire([])
    lock.release()
