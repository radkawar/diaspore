"""Compare lexical private-name spelling and metadata with CPython 3.12."""

import contextlib
import io

import pytest

from diaspore import Machine, compile_bundle
from diaspore.compiler import ModuleCompiler, compact_bundle
from diaspore.dspb import decode, encode

from .test_pep695 import selfhost_compiler
from .test_selfhost import SELFHOST, selfhost_compile

SOURCES = [
    '''
class Slots:
    __slots__ = ('x', '__private')
    def __init__(self):
        self.x = 2
        self.__private = 5
    def change(self):
        self.__private += self.x
        value = self.__private
        del self.__private
        return value
value = Slots()
print(value._Slots__private, value.change())
print(hasattr(value, '_Slots__private'))
''',
    '''
class _Outer:
    __value = 3
    def __method(self, __arg, *__args, __flag=True, **__kwargs):
        return self.__value + __arg, __args, __flag, __kwargs
    def call(self):
        return self.__method(4, 5, _Outer__flag=False, extra=6)
    class __Inner:
        __slots__ = ('__value',)
        def __init__(self): self.__value = 8
        def get(self): return self.__value
    Inner = __Inner
    def factory(self):
        def __function(__value): return __value
        class __Local:
            __value = 9
            def get(self): return self.__value
        return __function, __Local
value = _Outer()
print(value.call())
print(_Outer._Outer__method.__name__, _Outer.Inner.__name__, _Outer.Inner().get())
function, local = value.factory()
print(function.__name__, function(7), local.__name__, local().get())
class ___:
    __value = 10
    def get(self): return self.__value
print(___().__value, ___().get())
''',
    '''
__global = 'outside'
class Scope:
    __values = [__item * 2 for __item in range(3)]
    def run(self):
        global __global
        __global = 'inside'
        __cell = 1
        def __inner():
            nonlocal __cell
            __cell += 2
            return __cell
        try:
            raise ValueError('private')
        except ValueError as __error:
            print(str(__error))
        __walrus = (__assigned := 4)
        return __inner(), __cell, __walrus, __assigned, self.__values
print(Scope().run())
print(__global, _Scope__global)
''',
    '''
class Pattern:
    def run(self, value):
        match value:
            case Pattern(__attr=__private):
                return __private
            case {'value': __capture, **__rest}:
                return __capture, __rest
            case [*__items]:
                return __items
print(Pattern().run({'value': 1, 'other': 2}))
print(Pattern().run([3, 4]))
item = Pattern()
setattr(item, '__attr', 8)
print(Pattern().run(item))
''',
    '''
__External = int
class Owner:
    __External = str
    type __Alias[__T: __External] = tuple[__T, __External]
    def __method[__T: __External](self, __arg: __T) -> __T:
        return __arg, __T
    class __Box[__T: __External](list[__T]):
        value = __T
    Alias = __Alias
    Box = __Box
    method = __method
print(Owner.Alias.__name__, Owner.Alias.__type_params__[0].__name__)
print(Owner.Alias.__type_params__[0].__bound__ is str)
print(Owner.Alias.__value__.__args__[1] is str)
print(Owner.method.__name__, list(Owner.method.__annotations__))
print(Owner().method(7)[1] is Owner.method.__type_params__[0])
print(Owner.Box.__name__, Owner.Box.value.__name__, Owner.Box.value.__bound__ is int)
''',
    '''
from __future__ import annotations
class Future:
    __field: __Type
    def method[__T](self, __arg: __T) -> __T:
        return __arg
print(Future.__annotations__)
print(Future.method.__annotations__)
''',
]


@pytest.fixture(scope="module")
def private_compiler_bundle():
    """Compile the maintained self-hosted compiler once for execution checks."""
    return compile_bundle(str(SELFHOST))


@pytest.mark.parametrize("source", SOURCES)
def test_private_name_execution(source, tmp_path, private_compiler_bundle):
    """Keep lexical bindings separate from source-level metadata and strings."""
    expected = io.StringIO()
    with contextlib.redirect_stdout(expected):
        exec(source, {"__name__": "__main__"})
    native = ModuleCompiler("private", "<private>", source).compile()
    portable = selfhost_compiler()("private", "<private>", source).compile()
    assert portable == native
    entry = tmp_path / "private.py"
    entry.write_text(source)
    bundle = compile_bundle(str(entry))
    assert selfhost_compile(private_compiler_bundle, entry) == bundle
    assert decode(encode(bundle)) == compact_bundle(bundle)
    out, err = io.StringIO(), io.StringIO()
    assert Machine(bundle, stdout=out, stderr=err).run() == 0, err.getvalue()
    assert out.getvalue() == expected.getvalue()


def test_private_import_names():
    """Mangle bare import identifiers but preserve dotted paths and fromlists."""
    source = (
        "class Scope:\n"
        " def load(self):\n"
        "  import __module\n"
        "  import __package.child\n"
        "  from __module import __value as __alias\n"
    )
    native = ModuleCompiler("private", "<private>", source)
    portable = selfhost_compiler()("private", "<private>", source)
    module = native.compile()
    assert portable.compile() == module
    assert native.imports == portable.imports == {"_Scope__module", "__package.child"}
    statements = module["body"][0]["body"][0]["body"]
    assert statements[0]["module"] == "_Scope__module"
    assert statements[0]["alias"] == "_Scope__module"
    assert statements[1]["module"] == "__package.child"
    assert statements[1]["alias"] == "_Scope__package"
    assert statements[2]["names"] == [{"n": "_Scope__value", "alias": "_Scope__alias"}]
