"""CPython descriptor semantics without compiled standard-library dependencies."""

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
        """none_type = type(None)
constructor = none_type.__new__
print(None.__new__ is none_type.__new__)
print(constructor is none_type.__new__, constructor is None.__new__)
print(getattr(None, '__new__') is getattr(none_type, '__new__'))
print(constructor(none_type) is None, None.__new__(none_type) is None)
for cls in [object, none_type, bool, int, float, str, bytes, list, tuple, dict, set]:
    first = cls.__new__
    print(cls.__name__, first is cls.__new__, first is getattr(cls, '__new__'))
""",
        """class Root:
    pass
class Leaf(Root):
    pass
class Other:
    pass
print(Root.__new__ is object.__new__)
print(Leaf.__new__ is Root.__new__, Other.__new__ is Root.__new__)
root = Root()
leaf = Leaf()
print(root.__new__ is Root.__new__, leaf.__new__ is Leaf.__new__)
print(object.__new__ is object.__new__)
print(type(Leaf.__new__(Leaf)) is Leaf)
print(type(root.__new__(Other)) is Other)
""",
        """class Meta(type):
    def __instancecheck__(cls, value):
        return getattr(value, 'accepted', False)
class SubMeta(Meta):
    pass
class Subject(metaclass=Meta):
    pass
class SubSubject(Subject, metaclass=SubMeta):
    pass
class Accepted:
    accepted = True
class Rejected:
    accepted = False
for cls in [Subject, SubSubject]:
    print(isinstance(cls, type), isinstance(cls, Meta), isinstance(cls, object))
print(isinstance(Subject, SubMeta), isinstance(SubSubject, SubMeta))
print(isinstance(Subject(), Subject), isinstance(SubSubject(), SubSubject))
print(isinstance(Accepted(), Subject), isinstance(Rejected(), Subject))
print(isinstance(Accepted(), SubSubject), isinstance(Rejected(), SubSubject))
print(isinstance(Subject, (int, Meta)), isinstance(SubSubject, (str, SubMeta)))
""",
        """class Plain:
    pass
class ReprOnly:
    def __repr__(self):
        return 'repr-only'
class ReprChild(ReprOnly):
    pass
class StrOnly:
    def __str__(self):
        return 'str-only'
class StrChild(StrOnly):
    pass
for cls in [Plain, ReprOnly, ReprChild, StrOnly, StrChild]:
    print(cls.__name__, cls.__repr__ is object.__repr__,
          cls.__str__ is object.__str__, cls.__format__ is object.__format__)
plain = Plain()
print(str(plain) == repr(plain), format(plain, '') == str(plain))
for value in [ReprOnly(), ReprChild(), StrOnly(), StrChild()]:
    print(str(value), format(value, ''), object.__format__(value, ''))
    try:
        format(value, '>10')
    except TypeError as error:
        print(type(error).__name__, str(error))
print(object.__str__(ReprChild()))
""",
        """print(int.__str__ is object.__str__)
print(bool.__str__ is object.__str__)
print(float.__str__ is object.__str__)
print(int.__repr__ is object.__repr__)
print(int.__str__(37), object.__str__(37), str(37))
print(int.__str__ is int.__str__, object.__str__ is object.__str__)
class Number(int):
    def __repr__(self):
        return 'number-repr'
class ChildNumber(Number):
    pass
print(Number.__str__ is object.__str__, ChildNumber.__str__ is Number.__str__)
for value in [Number(7), ChildNumber(8)]:
    print(repr(value), str(value), object.__str__(value))
""",
        """class Maker:
    def __new__(cls, value=5):
        print('new', cls.__name__, value)
        instance = object.__new__(cls)
        instance.value = value
        return instance
    def __init__(self, value=5):
        print('init', type(self).__name__, value)
class Child(Maker):
    pass
print(isinstance(Maker.__dict__['__new__'], staticmethod))
print(Maker.__new__ is Maker.__new__, Child.__new__ is Maker.__new__)
first = Maker(9)
second = Child(10)
print(first.value, second.value)
print(first.__new__ is Maker.__new__, second.__new__ is Maker.__new__)
third = first.__new__(Child, 11)
print(type(third).__name__, third.value)
print(Maker.__dict__['__new__'].__func__ is Maker.__new__)
""",
        """class Meta(type):
    def __new__(mcls, name, bases, namespace, *, compact=False, policy='default', **kw):
        print('meta', name, compact, policy, sorted(kw.items()))
        return type.__new__(mcls, name, bases, namespace, **kw)
class Root(metaclass=Meta):
    def __init_subclass__(cls, *, label='unset', **kw):
        print('subclass', cls.__name__, label, sorted(kw.items()))
        cls.label = label
Created = type('Created', (Root,), {'value': 9},
               compact=True, policy='checked', label='forwarded', extra=12)
print(type(Created) is Meta, Created.label, Created.value)
class Ordinary:
    def __init_subclass__(cls, *, setting, **kw):
        print('ordinary', cls.__name__, setting, sorted(kw.items()))
        cls.setting = setting
Simple = type('Simple', (Ordinary,), {}, setting=17, extra=18)
print(Simple.setting)
try:
    type(1, unexpected=True)
except TypeError as error:
    print(type(error).__name__, str(error))
""",
        """for value in [None, NotImplemented, Ellipsis]:
    cls = type(value)
    print(cls.__name__, value.__doc__, cls.__doc__)
    print(value.__doc__ is None, cls.__doc__ is None)
""",
        """for cls in [list, dict, set, tuple, frozenset, str, bytes, bytearray]:
    value = cls()
    print(cls.__name__, cls.__hash__ is None,
          cls.__dict__['__hash__'] is None, value.__hash__ is None)
    print(value.__class__.__hash__ is cls.__hash__)
class Lists(list):
    pass
class Dictionaries(dict):
    pass
class Sets(set):
    pass
class ExplicitHash(list):
    __hash__ = object.__hash__
class Equal:
    def __eq__(self, other):
        return True
for cls in [Lists, Dictionaries, Sets, ExplicitHash, Equal]:
    value = cls()
    print(cls.__name__, cls.__hash__ is None,
          value.__hash__ is None, '__hash__' in cls.__dict__)
    if cls.__hash__ is None:
        try:
            hash(value)
        except TypeError as error:
            print(type(error).__name__, str(error))
print(ExplicitHash.__hash__ is object.__hash__)
print(isinstance(hash(ExplicitHash()), int))
""",
        """class MetaRoot(type):
    def __init__(cls, name, bases, namespace):
        print('root init', name, cls.__name__, type(cls).__name__)
        super().__init__(name, bases, namespace)
        cls.initialized = True
class MetaChild(MetaRoot):
    def __init__(cls, name, bases, namespace):
        print('child before', name)
        super().__init__(name, bases, namespace)
        print('child after', name, cls.initialized)
class First(metaclass=MetaChild):
    pass
class Second(First):
    pass
print(First.initialized, Second.initialized)
print(type(First) is MetaChild, type(Second) is MetaChild)
class Root:
    @classmethod
    def identify(cls):
        return ('root', cls.__name__)
class Middle(Root):
    @classmethod
    def identify(cls):
        return ('middle', cls.__name__, super().identify())
class Leaf(Middle):
    @classmethod
    def identify(cls):
        return ('leaf', cls.__name__, super().identify())
print(Middle.identify(), Leaf.identify())
print(Leaf().identify())
print(super(Leaf, Leaf).identify())
print(super(Middle, Leaf).identify())
print(super(Leaf, Leaf).identify.__self__ is Leaf)
""",
        """for left, right in [
    (list[int], list[int]),
    (list[int], list[str]),
    (list[int], tuple[int]),
    (dict[str, list[int]], dict[str, list[int]]),
    (dict[str, list[int]], dict[str, list[str]]),
    (tuple[int, str], tuple[str, int]),
    (tuple[int, ...], tuple[int, ...]),
]:
    print(left == right, left != right)
    print(type(left).__eq__(left, right), type(left).__ne__(left, right))
    if left == right:
        print(hash(left) == hash(right), {left: 'found'}[right])
    else:
        print(len({left: 'left', right: 'right'}), right in {left: 'only'})
alias = list[int]
print(type(alias).__eq__(alias, int) is NotImplemented,
      type(alias).__ne__(alias, int) is NotImplemented)
print(alias.__eq__ is list.__eq__, alias.__ne__ is list.__ne__)
try:
    alias.__eq__(alias)
except TypeError as error:
    print(type(error).__name__, str(error))
print(alias == int, alias != int)
first = list[[]]
second = list[[]]
print(first == second, first != second, first == list[[1]])
for value in [first, dict[str, []], list[list[[]]]]:
    try:
        hash(value)
    except TypeError as error:
        print(type(error).__name__, str(error))
    try:
        {value: 1}
    except TypeError as error:
        print(type(error).__name__, str(error))
""",
        """class Accessor:
    def __init__(self, action):
        self.action = action
    def __get__(self, instance, owner):
        raise AssertionError('property accessor must not be descriptor-bound')
    def __call__(self, target, *args):
        print(self.action, type(target).__name__, args)
        if self.action == 'get':
            return target.saved
        if self.action == 'set':
            target.saved = args[0]
        else:
            del target.saved
class CallableAccess:
    value = property(Accessor('get'), Accessor('set'), Accessor('delete'))
item = CallableAccess()
item.saved = 1
print(item.value)
item.value = 2
print(item.value, item.saved)
del item.value
print(hasattr(item, 'saved'))
class BoundAccessors:
    def get(self, target):
        print('bound get', self.label, type(target).__name__)
        return target.saved
    def set(self, target, value):
        print('bound set', self.label, value)
        target.saved = value
    def delete(self, target):
        print('bound delete', self.label)
        del target.saved
accessors = BoundAccessors()
accessors.label = 'owner'
class BoundAccess:
    value = property(accessors.get, accessors.set, accessors.delete)
item = BoundAccess()
item.saved = 3
print(item.value)
item.value = 4
print(item.value)
del item.value
print(hasattr(item, 'saved'))
class BuiltinAccess:
    count = property(len)
    def __len__(self):
        return 12
print(BuiltinAccess().count)
class FunctionAccess:
    def read(self):
        return self.saved
    def write(self, value):
        self.saved = value
    def remove(self):
        del self.saved
    value = property(read, write, remove)
item = FunctionAccess()
item.value = 5
print(item.value)
del item.value
print(hasattr(item, 'saved'))
class Meta(type):
    value = property(Accessor('get'), Accessor('set'), Accessor('delete'))
class Target(metaclass=Meta):
    saved = 6
print(Target.value)
Target.value = 7
print(Target.value, Target.saved)
del Target.value
print(hasattr(Target, 'saved'))
""",
    ],
    ids=[
        "builtin-new-identity",
        "inherited-object-new",
        "custom-metaclass-isinstance",
        "object-repr-str-format",
        "numeric-object-str",
        "custom-new-static",
        "type-keyword-forwarding",
        "singleton-documentation",
        "mutable-default-hash-metadata",
        "metaclass-and-classmethod-super",
        "generic-alias-equality-and-hashing",
        "property-callable-accessors",
    ],
)
def test_ts_descriptor_semantics(tmp_path: Path, source: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    entry = tmp_path / "descriptor_case.py"
    entry.write_text(source, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = compile_bundle(str(entry), stdlib=False)
    assert "stdlib" not in bundle
    assert list(bundle["modules"]) == ["descriptor_case"]
    bundle_path = tmp_path / "descriptor_case.dsp.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    process = subprocess.run(
        [
            node,
            "--experimental-strip-types",
            "--no-warnings",
            str(TS_DIR / "src/cli.ts"),
            "run",
            str(bundle_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = process.stderr.strip().splitlines()
    actual = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(actual, expected, "TypeScript descriptor semantics")
