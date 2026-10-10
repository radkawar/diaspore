"""Lua class construction and native descriptors follow CPython protocols."""

from pathlib import Path

from .conftest import assert_matches, run_cpython, run_diaspore_lua


def test_lua_class_slot_and_native_descriptors(tmp_path: Path, lua_vm: list[str]) -> None:
    entry = tmp_path / "descriptors.py"
    entry.write_text(
        """
class NS(dict):
    def __init__(self):
        self.recorded = []
    def __setitem__(self, key, value):
        self.recorded.append(key)
        super().__setitem__(key, value)
class Meta(type):
    @classmethod
    def __prepare__(mcls, name, bases):
        mcls.prepared = NS()
        return mcls.prepared
    def __new__(mcls, name, bases, namespace):
        assert namespace is mcls.prepared
        return super().__new__(mcls, name, bases, namespace)
    def __init__(cls, name, bases, namespace):
        assert namespace is Meta.prepared
class Prepared(metaclass=Meta):
    value = 42
assert Prepared.value == 42
assert isinstance(Prepared, Meta)
class S:
    __slots__ = ('x', '__hidden')
s = S()
s.x = 42
s._S__hidden = 9
assert s.x == 42 and s._S__hidden == 9
assert not hasattr(s, '__dict__')
assert type(S.x).__name__ == 'member_descriptor'
assert S.x.__get__(s, S) == 42
S.x.__set__(s, 43)
assert s.x == 43
S.x.__delete__(s)
assert not hasattr(s, 'x')
try:
    s.extra = 1
except AttributeError:
    pass
else:
    raise AssertionError('slot restriction')
class SD(S):
    pass
sd = SD()
sd.x = 2
sd.extra = 3
assert sd.__dict__ == {'extra': 3}
d = {'other': 4}
sd.__dict__ = d
assert sd.__dict__ is d and sd.x == 2
class Int(int):
    pass
assert type(Int.from_bytes(b'*')) is Int
assert type(Int(0).from_bytes(b'*')) is Int
assert int.__abs__(-42) == 42
assert int.__new__.__self__ is int
assert object.__new__.__self__ is object
assert Int.__new__.__self__ is int
class Bytes(bytes):
    pass
assert Bytes(b'').maketrans(b'a',b'b') == bytes.maketrans(b'a',b'b')
assert NS.__hash__ is None
assert list(reversed([1,2])) == [2,1]
class A:
    def method(self):
        return 1
class B(A):
    pass
assert super(B,B).method is A.method
p = property(lambda self: 1)
p.__doc__ = 'changed'
assert p.__doc__ == 'changed'
print('class/slot/native descriptor protocols passed')

assert int.__new__ is int.__new__
assert int.__repr__ is int.__repr__
assert int.__str__ is object.__str__
assert '__new__' in int.__dict__
assert int.__dict__['__new__'] is int.__new__
assert '__str__' not in int.__dict__
assert bool.__hash__ is int.__hash__
assert '__hash__' not in bool.__dict__
assert bool.__bases__ == (int,)
assert (1).__getstate__() is None
print('native descriptor ownership and identities passed')

assert None.__getstate__() is None
assert type(None).__getstate__(None) is None
print('None receiver binding passed')

class DocSlot:
    __slots__ = ('__doc__',)

doc_slot = DocSlot()
doc_slot.__doc__ = 'instance documentation'
assert doc_slot.__doc__ == 'instance documentation'
assert type(DocSlot.__doc__).__name__ == 'member_descriptor'

mro_descriptor = type.__dict__['__mro__']
bases_descriptor = type.__dict__['__bases__']
dict_descriptor = type.__dict__['__dict__']
assert type(mro_descriptor).__name__ == 'getset_descriptor'
assert mro_descriptor.__get__(S) is S.__mro__
assert mro_descriptor.__get__(type) is type.__mro__
assert bases_descriptor.__get__(S) is S.__bases__
assert dict_descriptor.__get__(S)['x'] is S.x
class MaskMeta(type):
    @property
    def __mro__(cls):
        return ('masked',)
class StaticCheck(metaclass=MaskMeta):
    pass
assert StaticCheck.__mro__ == ('masked',)
assert mro_descriptor.__get__(StaticCheck)[0] is StaticCheck
class ReadyMeta(type):
    pass
class Hook:
    def __set_name__(self, owner, name):
        assert type(owner) is ReadyMeta
        self.owner = owner
class HookBase(metaclass=ReadyMeta):
    def __init_subclass__(cls, **kwargs):
        assert type(cls) is ReadyMeta
        super().__init_subclass__(**kwargs)
class HookChild(HookBase):
    field = Hook()
assert HookChild.field.owner is HookChild
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua class descriptors")


def test_lua_recursive_protected_calls_and_generator_frames(
    tmp_path: Path, lua_vm: list[str]
) -> None:
    entry = tmp_path / "recursive_frames.py"
    entry.write_text(
        """
import sys

def descend(n):
    try:
        if n:
            return descend(n - 1)
        current = sys._getframe()
        assert current is sys._getframe()
        return current
    finally:
        pass

frame = descend(300)
count = 0
while frame.f_code.co_name == 'descend':
    assert frame.f_locals['n'] == count
    count += 1
    frame = frame.f_back
assert count == 301

events = []
def relay(n):
    try:
        if n:
            yield from relay(n - 1)
        else:
            current = sys._getframe()
            assert current is sys._getframe()
            assert sys._getframe(1) is current.f_back
            payload = yield current
            try:
                yield payload
            except ValueError as error:
                yield str(error)
    finally:
        events.append(n)

generator = relay(250)
paused = next(generator)
assert paused.f_code.co_name == 'relay'
assert paused.f_back is None
assert generator.send('sent') == 'sent'
assert generator.throw(ValueError('delivered')) == 'delivered'
generator.close()
assert events == list(range(251))
assert paused.f_back is not None

previous = sys.getrecursionlimit()
events.clear()
try:
    sys.setrecursionlimit(200)
    try:
        next(relay(600))
    except RecursionError:
        pass
    else:
        raise AssertionError('generator recursion did not enforce the guest limit')
finally:
    sys.setrecursionlimit(previous)
assert 0 < len(events) < 600
assert events == sorted(events)
assert events[-1] == 600
print('recursive protected calls, live frames and generator relay passed')
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua recursive frames")
