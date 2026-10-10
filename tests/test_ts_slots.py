"""CPython slot layouts, descriptor storage, and instance dictionary boundaries."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from diaspore import compile_bundle

from .conftest import TS_DIR, Result, assert_matches, run_cpython


@pytest.fixture
def node_command() -> list[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    return [node, "--experimental-strip-types", "--no-warnings"]


@pytest.mark.parametrize(
    "source",
    [
        """class Slots:
    __slots__ = ('z', 'x')
value = Slots()
descriptor = Slots.x
print(type(descriptor).__name__, descriptor.__name__, descriptor.__qualname__)
print(descriptor.__objclass__ is Slots, descriptor.__doc__ is None)
print(descriptor.__get__(None, Slots) is descriptor)
for operation in ['get', 'delete', 'dict', 'other']:
    try:
        if operation == 'get':
            print(value.x)
        elif operation == 'delete':
            del value.x
        elif operation == 'dict':
            print(value.__dict__)
        else:
            value.other = 1
    except AttributeError as error:
        print(operation, type(error).__name__, str(error))
value.x = None
print(value.x, descriptor.__get__(value))
descriptor.__set__(value, 17)
print(value.x)
descriptor.__delete__(value)
print(hasattr(value, 'x'))
for method, args in [(descriptor.__get__, (object(),)),
                     (descriptor.__set__, (object(), 1)),
                     (descriptor.__get__, (None,))]:
    try:
        method(*args)
    except TypeError as error:
        print(type(error).__name__, str(error))
""",
        """class Parent:
    __slots__ = ('x', '__private')
class Child(Parent):
    __slots__ = ('x', '__private')
value = Child()
Parent.x.__set__(value, 10)
value.x = 20
value._Parent__private = 'parent'
value._Child__private = 'child'
print(Parent.x is Child.x, Parent.x.__objclass__ is Parent, Child.x.__objclass__ is Child)
print(Parent.x.__get__(value), Child.x.__get__(value), value.x)
print(value._Parent__private, value._Child__private)
print(Parent._Parent__private.__name__, Child._Child__private.__name__)
del value.x
print(hasattr(value, 'x'), Parent.x.__get__(value))
Child.x.__set__(value, 30)
Parent.x.__delete__(value)
print(value.x)
try:
    Parent.x.__get__(value)
except AttributeError as error:
    print(type(error).__name__, str(error))
class StillSlotted(Parent):
    __slots__ = ()
class WithDict(Parent):
    pass
print(hasattr(StillSlotted(), '__dict__'), hasattr(WithDict(), '__dict__'))
plain = WithDict()
plain.extra = 42
plain.x = 43
print(plain.__dict__, plain.x)
""",
        """class Mixed:
    __slots__ = ('x', '__dict__', '__weakref__')
value = Mixed()
value.x = 'slot'
dictionary = value.__dict__
dictionary['x'] = 'shadow'
dictionary[1] = 'non-string key'
print(value.__dict__ is dictionary, value.x, dictionary['x'], dictionary[1])
value.extra = 'attribute'
print(dictionary['extra'], value.__weakref__ is None)
print(type(Mixed.__dict__['__dict__']).__name__, type(Mixed.__dict__['__weakref__']).__name__)
replacement = {'new': 3}
value.__dict__ = replacement
print(value.__dict__ is replacement, value.new, value.x)
replacement['later'] = 4
print(value.later)
del value.__dict__
print(value.__dict__, replacement, value.x, value.__dict__ is replacement)
class CustomDict(dict):
    pass
custom = CustomDict()
custom['custom'] = 5
value.__dict__ = custom
print(value.__dict__ is custom, value.custom)
value.written = 6
print(custom['written'])
for action in ['set-weak', 'delete-weak', 'bad-dict']:
    try:
        if action == 'set-weak':
            value.__weakref__ = None
        elif action == 'delete-weak':
            del value.__weakref__
        else:
            value.__dict__ = []
    except (AttributeError, TypeError) as error:
        print(action, type(error).__name__, str(error))
""",
        """for slots in ['single', ['b', 'a'], ('same', 'same'),
              {'b': 'doc', 'a': 'other'}, iter(['z', 'a'])]:
    cls = type('Declared', (), {'__slots__': slots})
    value = cls()
    names = sorted(name for name in cls.__dict__ if not name.startswith('__'))
    print(names, cls.__slots__ is slots)
    for index, name in enumerate(names):
        setattr(value, name, index)
    print([getattr(value, name) for name in names])
class ___Private:
    __slots__ = '__field'
private = ___Private()
private._Private__field = 8
print(private._Private__field, ___Private._Private__field.__name__)
class ___:
    __slots__ = '__field'
private = ___()
private.__field = 9
print(private.__field)
class Documented:
    __slots__ = ('__doc__',)
value = Documented()
value.__doc__ = 'instance documentation'
print(value.__doc__, type(Documented.__doc__).__name__)
""",
        """for slots, attributes in [
    (('bad name',), {}), ((1,), {}), (None, {}),
    (('__dict__', '__dict__'), {}), (('__weakref__', '__weakref__'), {}),
    (('x',), {'x': 1}), (('__x',), {'_Bad__x': 1}),
]:
    try:
        type('Bad', (), dict(attributes, __slots__=slots))
    except (TypeError, ValueError) as error:
        print(type(error).__name__, str(error))
class DictionaryBase:
    pass
for slot in ['__dict__', '__weakref__']:
    try:
        type('Bad', (DictionaryBase,), {'__slots__': (slot,)})
    except TypeError as error:
        print(type(error).__name__, str(error))
for base in [int, tuple, bytes, type]:
    print(type('Empty', (base,), {'__slots__': ()}).__slots__)
    try:
        type('Bad', (base,), {'__slots__': ('x',)})
    except TypeError as error:
        print(type(error).__name__, str(error))
try:
    type('Bad', (bool,), {})
except TypeError as error:
    print(type(error).__name__, str(error))
class Integer(int):
    pass
try:
    type('Bad', (Integer,), {'__slots__': ('x',)})
except TypeError as error:
    print(type(error).__name__, str(error))
for base in [str, float, list, dict, set, frozenset]:
    cls = type('Supported', (base,), {'__slots__': ('x',)})
    value = cls()
    value.x = 7
    print(base.__name__, value.x, hasattr(value, '__dict__'))
""",
        """class A:
    __slots__ = ('a',)
class B:
    __slots__ = ('b',)
try:
    class Conflict(A, B):
        pass
except TypeError as error:
    print(type(error).__name__, str(error))
class Empty:
    __slots__ = ()
class Combined(A, Empty):
    __slots__ = ('c',)
value = Combined()
value.a = 1
value.c = 2
print(value.a, value.c, hasattr(value, '__dict__'))
class Left(A):
    __slots__ = ()
class Right(A):
    __slots__ = ()
class Diamond(Left, Right):
    __slots__ = ('d',)
value = Diamond()
value.a = 3
value.d = 4
print(value.a, value.d, Diamond.a is A.a)
class Dictionary:
    pass
class Mixed(A, Dictionary):
    __slots__ = ('m',)
value = Mixed()
value.a = 5
value.m = 6
value.extra = 7
print(value.a, value.m, value.__dict__)
class ListLeft(list):
    __slots__ = ('left',)
class ListRight(list):
    __slots__ = ('right',)
try:
    class ListConflict(ListLeft, ListRight):
        pass
except TypeError as error:
    print(type(error).__name__, str(error))
""",
    ],
    ids=[
        "descriptor-storage",
        "inherited-private-slots",
        "dict-weakref-slots",
        "slot-declarations",
        "invalid-and-builtin-layouts",
        "multiple-inheritance",
    ],
)
def test_ts_slots_match_cpython(tmp_path: Path, node_command: list[str], source: str) -> None:
    entry = tmp_path / "slot_case.py"
    entry.write_text(source, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = compile_bundle(str(entry), stdlib=False)
    path = tmp_path / "slots.dsp.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    process = subprocess.run(
        [*node_command, str(TS_DIR / "src/cli.ts"), "run", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = process.stderr.strip().splitlines()
    actual = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(actual, expected, "TypeScript slot layouts")


def test_ts_dictless_slot_storage_does_not_allocate_instance_dict(
    tmp_path: Path,
    node_command: list[str],
) -> None:
    entry = tmp_path / "slot_storage.py"
    entry.write_text(
        "class Parent:\n    __slots__ = ('x', 'x')\n"
        "class Child(Parent):\n    __slots__ = ('x',)\n"
        "value = Child()\nParent.x.__set__(value, 1)\nvalue.x = 2\n",
        encoding="utf-8",
    )
    path = tmp_path / "slots.dsp.json"
    path.write_text(json.dumps(compile_bundle(str(entry), stdlib=False)), encoding="utf-8")
    source = (
        'import assert from "node:assert/strict";\n'
        'import {readFileSync} from "node:fs";\n'
        f"import {{Interp}} from {json.dumps((TS_DIR / 'src/interp.ts').as_uri())};\n"
        "import {instanceHasDict, instanceSlotCount} "
        f"from {json.dumps((TS_DIR / 'src/slots.ts').as_uri())};\n"
        'const bundle = JSON.parse(readFileSync(process.argv[1], "utf8"));\n'
        "const runtime = new Interp(bundle);\n"
        "assert.equal(runtime.run(), 0);\n"
        "const module = runtime.modules.get(bundle.main);\n"
        'const value = module.frame.vars.get("value");\n'
        'const parent = module.frame.vars.get("Parent").ns.get("x");\n'
        'const child = module.frame.vars.get("Child").ns.get("x");\n'
        "assert.equal(instanceHasDict(value.cls), false);\n"
        "assert.equal(instanceSlotCount(value.cls), 3);\n"
        "assert.equal(value.peekDict(), undefined);\n"
        "assert.equal(parent.get(value), 1n);\n"
        "assert.equal(child.get(value), 2n);\n"
        "parent.delete(value);\n"
        "assert.equal(child.get(value), 2n);\n"
        "assert.equal(value.peekDict(), undefined);\n"
        "runtime.close();\n"
    )
    process = subprocess.run(
        [*node_command, "--input-type=module", "-e", source, str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0, process.stderr
