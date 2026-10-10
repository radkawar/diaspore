"""Focused checks for the maintained typing primitive implementation."""

from __future__ import annotations

from pathlib import Path

from .conftest import run_cpython, run_diaspore_py

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "corpus" / "112_typing_provider.py"


def test_typing_provider_matches_cpython():
    expected = run_cpython(CORPUS)
    assert expected.code == 0, expected.err_line
    assert run_diaspore_py(CORPUS) == expected


def test_lazy_evaluation_without_typing_import(tmp_path):
    source = '''
import _typing
import sys

calls = []
def bound():
    calls.append("bound")
    return int

def value():
    calls.append("value")
    return None

T = _typing._typevar("T", bound=bound, module="example")
A = _typing._typealias("A", (T,), value, module="example")
P = _typing._paramspec("P", module="example")
assert P.__bound__ is None and P.__infer_variance__
assert P.__module__ == "example"
assert _typing.TypeAliasType("Plain", int).__parameters__ == ()
assert "typing" not in sys.modules
assert T.__name__ == "T" and T.__module__ == "example"
assert A.__name__ == "A" and A.__module__ == "example"
assert calls == []
assert T.__bound__ is int and T.__bound__ is int
assert A.__value__ is None and A.__value__ is None
assert calls == ["bound", "value"]
assert "typing" not in sys.modules
'''
    entry = tmp_path / "main.py"
    entry.write_text(source, encoding="utf-8")
    result = run_diaspore_py(entry)
    assert result.code == 0, result.err_line
    assert result.stdout == ""


def test_provider_lazy_factories(tmp_path):
    source = r'''
import _typing
import typing

assert typing.TypeVar is _typing.TypeVar
assert typing.NewType("UserId", int)(7) == 7
assert typing.ParamSpec("P").__bound__ is type(None)
T = typing.TypeVar("T")
assert T.__module__ == "__main__"
class Box(typing.Generic[T]):
    pass
assert Box.__parameters__ == (T,)
assert typing.get_origin(Box[int]) is Box

calls = []
ready = False
def bound():
    calls.append("bound")
    if not ready:
        raise NameError("later")
    return int

def constraints():
    calls.append("constraints")
    return (str, bytes)

T = _typing._typevar("T", bound=bound, module="example")
assert T.__infer_variance__ and repr(T) == "T"
assert T.__module__ == "example" and calls == []
try:
    T.__bound__
except NameError:
    pass
else:
    raise AssertionError("failed evaluation must propagate")
ready = True
assert T.__bound__ is int and T.__bound__ is int
assert calls == ["bound", "bound"]
U = _typing._typevar("U", constraints=constraints)
assert U.__constraints__ == (str, bytes)
assert U.__constraints__ == (str, bytes)
assert calls == ["bound", "bound", "constraints"]
P = _typing._paramspec("P", module="example")
assert P.__bound__ is None and P.__infer_variance__
Ts = _typing._typevartuple("Ts", module="example")
assert Ts.__module__ == "example"
assert tuple(Ts) == (typing.Unpack[Ts],)
base = _typing._generic_base((T, Ts, P))
assert base.__origin__ is typing.Generic
assert base.__parameters__ == (T, Ts, P)

ready = False
def value():
    calls.append("value")
    if not ready:
        raise NameError("later")
    return list[T]

alias = _typing._typealias("Alias", (T,), value, module="example")
assert alias.__name__ == "Alias" and alias.__module__ == "example"
assert alias.__type_params__ == (T,)
assert calls == ["bound", "bound", "constraints"]
try:
    alias.__value__
except NameError:
    pass
else:
    raise AssertionError("failed evaluation must propagate")
ready = True
assert alias.__value__ == list[T]
assert alias.__value__ is alias.__value__
assert calls == ["bound", "bound", "constraints", "value", "value"]
assert typing.get_origin(alias[int]) is alias
assert typing.get_args(alias[int]) == (int,)
assert typing.TypeAliasType("Plain", None).__value__ is None
'''
    entry = tmp_path / "main.py"
    entry.write_text(source, encoding="utf-8")
    result = run_diaspore_py(entry)
    assert result.code == 0, result.err_line
    assert result.stdout == ""
