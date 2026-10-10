"""TypeScript object and metaclass protocols match CPython callbacks."""

import json
import subprocess
from pathlib import Path

from .conftest import Result, assert_matches, build, run_cpython

_SOURCE = r"""
import copyreg


def shape(value):
    if isinstance(value, type):
        return ('type', value.__name__)
    if isinstance(value, tuple):
        return tuple(shape(item) for item in value)
    if isinstance(value, list):
        return [shape(item) for item in value]
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if callable(value):
        return ('callable', value.__name__)
    return value


def show(label, value, protocol):
    try:
        result = object.__reduce_ex__(value, protocol)
        if isinstance(result, tuple) and len(result) in (2, 3, 5) and callable(result[0]):
            items = [result[0].__name__, shape(result[1])]
            if len(result) >= 3:
                items.append(shape(result[2]))
            if len(result) == 5:
                items.append(None if result[3] is None else list(result[3]))
                items.append(None if result[4] is None else list(result[4]))
            print(label, items)
        else:
            print(label, result)
    except Exception as error:
        print(label, type(error).__name__, str(error))


class Plain:
    pass


class EmptySlots:
    __slots__ = ()


class Slots:
    __slots__ = ('x', '__private')
    def __init__(self):
        self.x = 4
        self.__private = 5


class Inherited(Slots):
    __slots__ = ('y', '__dict__')
    def __init__(self):
        super().__init__()
        self.y = 6
        self.extra = 7


class CustomState:
    def __getstate__(self):
        return ('custom', 23)


class CustomReduce:
    def __reduce__(self):
        return 'custom_reduce'


class DelegatedReduce:
    def __reduce__(self):
        return object.__reduce__(self)


class NewArgs:
    def __getnewargs__(self):
        return (1, 'two')


class NewArgsEx:
    def __getnewargs_ex__(self):
        return ((3,), {'answer': 4})
    def __getnewargs__(self):
        raise AssertionError('must prefer __getnewargs_ex__')


class EmptyNewArgsEx:
    def __getnewargs_ex__(self):
        return ((9,), {})


class ListValue(list):
    pass


class DictValue(dict):
    pass


class CustomList(list):
    def __iter__(self):
        return iter([7, 8])


class CustomDict(dict):
    def items(self):
        return [('chosen', 9)]


plain = Plain()
plain.answer = 42
values = [Plain(), plain, EmptySlots(), Slots(), Inherited(), CustomState(),
          CustomReduce(), DelegatedReduce(), NewArgs(), NewArgsEx(), EmptyNewArgsEx(),
          ListValue([1, 2]), DictValue(a=3), CustomList([1]), CustomDict(a=1)]
values[-4].label = 'list-state'
values[-3].label = 'dict-state'
for value in values:
    for protocol in [-1, 0, 1, 2, 4, 5]:
        show((type(value).__name__, protocol), value, protocol)

for value in [None, 4, True, 1.5, 'text', b'bytes', (1, 2), [], {}, 1 + 2j,
              set(), frozenset({3})]:
    show(('base', type(value).__name__), value, 2)


class SetValue(set):
    def __iter__(self):
        return iter([7, 8])


class FrozenValue(frozenset):
    def __getstate__(self):
        return ('set-state', 9)


class SlottedSet(set):
    __slots__ = ('tag',)


set_value = SetValue([1])
set_value.tag = 'set-dict'
slotted_set = SlottedSet()
slotted_set.tag = 'set-slot'
for value in [set_value, FrozenValue([3]), slotted_set]:
    for protocol in [0, 2, 5]:
        show(('set', type(value).__name__, protocol), value, protocol)


class HintSet(set):
    mode = 0
    def __iter__(self):
        print('set iter')
        return iter([8])
    def __len__(self):
        print('set len')
        if self.mode == 1:
            raise ValueError('length failed')
        if self.mode == 2:
            raise TypeError('ignored length')
        return 1
    def __length_hint__(self):
        print('set hint')
        return 2


for mode in range(3):
    HintSet.mode = mode
    show(('set-hint', mode), HintSet(), 2)

# Instance shadows do not replace the class's reduce/getnewargs protocol.
shadow = Plain()
shadow.__reduce__ = lambda: 'instance-reduce'
shadow.__getnewargs__ = lambda: (99,)
show('instance-shadow', shadow, 2)
shadow.__getstate__ = lambda: {'instance-state': 8}
show('instance-getstate', shadow, 2)


class Index:
    def __index__(self):
        return 2


class BadIndex:
    def __index__(self):
        return 'bad'


for index in [Index(), BadIndex(), 1.5, '2', None, 2**40, -(2**40), True]:
    show(('protocol', type(index).__name__), CustomReduce(), index)


class BadNewArgs:
    def __getnewargs__(self):
        return [1]


class BadNewArgsEx:
    result = None
    def __getnewargs_ex__(self):
        return self.result


show('bad-newargs', BadNewArgs(), 2)
for result in [None, [], (), ((),), ((), {}, None), ([], {}), ((), [])]:
    BadNewArgsEx.result = result
    show('bad-newargs-ex', BadNewArgsEx(), 2)


class BadSlots:
    __slotnames__ = 42


show('bad-slotnames', BadSlots(), 2)


class MissingSlot:
    __slots__ = ('unset',)


print('missing-slot-state', object.__getstate__(MissingSlot()))
print('default-state', object.__getstate__(plain))
print('slots-state', object.__getstate__(values[4]))
print('direct-getstate', object.__getstate__(CustomState()))

# Slot storage and the instance dictionary can have the same name independently.
collision = Inherited()
collision.__dict__['x'] = 99
print('slot-dict-collision', object.__getstate__(collision))


class ChangedSlots:
    __slots__ = ('x',)
    def __init__(self):
        self.x = 3


changed = ChangedSlots()
ChangedSlots.__slots__ = ('fake',)
print('changed-slots-declaration', object.__getstate__(changed))

state = object.__getstate__(plain)
state['answer'] = 43
print('state-alias', plain.answer)
dict_state, slot_state = object.__getstate__(collision)
dict_state['extra'] = 10
slot_state['x'] = 20
print('state-alias-slots-copy', collision.extra, collision.x)
print('state-dict-identity', state is plain.__dict__, dict_state is collision.__dict__)
replacement = {'assigned': 17}
plain.__dict__ = replacement
print('assigned-dict-identity', object.__getstate__(plain) is replacement, plain.assigned)


class StateError:
    def __getstate__(self):
        raise ValueError('state failed')


class MutatingSlotNames:
    __slotnames__ = ['x']
    @property
    def x(self):
        self.__slotnames__.append('y')
        return 1


class HiddenSlots:
    __slots__ = ('x',)
    __slotnames__ = []


class Disguised:
    @property
    def __class__(self):
        return Plain


show('state-error', StateError(), 2)
show('mutating-slotnames', MutatingSlotNames(), 2)
show('hidden-slots', HiddenSlots(), 2)
show('real-class', Disguised(), 2)


class TupleSubclass(tuple):
    def __iter__(self):
        return iter([99])


class TupleArguments:
    def __getnewargs__(self):
        return TupleSubclass((7, 8))


class HiddenNewArgs:
    def __getattribute__(self, name):
        if name == '__getnewargs__':
            return lambda: (99,)
        return object.__getattribute__(self, name)
    def __getnewargs__(self):
        return (7,)


class DictProperty:
    @property
    def __dict__(self):
        return {'bogus': 99}


dict_property = DictProperty()
dict_property.real = 12
show('tuple-arguments', TupleArguments(), 2)
show('special-lookup', HiddenNewArgs(), 2)
print('real-dict-state', object.__getstate__(dict_property))
"""


def test_ts_object_reduce_protocols(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "object_protocols.py"
    entry.write_text(_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "object_protocols.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript object reduction protocols")


_METACLASS_SOURCE = r"""
class Meta(type):
    def __iter__(cls):
        return iter(cls.values)
    def __len__(cls):
        return len(cls.values)
    def __contains__(cls, value):
        return value == 'chosen'
    def __getitem__(cls, key):
        return ('meta', key)
    def __setitem__(cls, key, value):
        cls.values[key] = value
    def __delitem__(cls, key):
        del cls.values[key]


class Values(metaclass=Meta):
    values = [2, 4, 6]
    # Class attributes are not the class object's protocol methods.
    def __iter__(self):
        raise AssertionError('class shadow')
    def __len__(self):
        raise AssertionError('class shadow')
    def __contains__(self, value):
        raise AssertionError('class shadow')
    def __class_getitem__(cls, key):
        return ('class', key)


a, b, c = Values
print('unpack', a, b, c)
print('iterate', list(Values), tuple(Values), [value + 1 for value in Values])
print('size', len(Values), bool(Values))
print('contains', 'chosen' in Values, 2 in Values)
print('getitem', Values['key'])
Values[1] = 9
del Values[0]
print('mutate', list(Values))


class Child(Values):
    pass


print('inherited', list(Child), len(Child), Child[3])
Values.values = []
print('empty', bool(Values), len(Values))


class BoolMeta(Meta):
    def __bool__(cls):
        return False


class FalseValues(metaclass=BoolMeta):
    values = [1]


print('bool override', bool(FalseValues), len(FalseValues))


class SequenceMeta(type):
    def __getitem__(cls, index):
        if index >= 3:
            raise IndexError(index)
        return index + 10


class Sequence(metaclass=SequenceMeta):
    pass


print('sequence fallback', list(Sequence), 11 in Sequence, 99 in Sequence)


class IteratorMeta(type):
    def __iter__(cls):
        return cls
    def __next__(cls):
        if cls.position >= 3:
            raise StopIteration
        cls.position += 1
        return cls.position


class Iterator(metaclass=IteratorMeta):
    position = 0


print('class iterator', iter(Iterator) is Iterator, list(Iterator))
Iterator.position = 0
print('direct next', next(Iterator), next(Iterator), next(Iterator), next(Iterator, 'end'))


class StaticMeta(type):
    @staticmethod
    def __iter__():
        return iter([7])


class StaticValues(metaclass=StaticMeta):
    pass


print('static method', list(StaticValues))


class ClassMeta(type):
    @classmethod
    def __iter__(meta):
        return iter([meta.__name__])


class ClassValues(metaclass=ClassMeta):
    pass


print('class method', list(ClassValues))


class DisabledMeta(SequenceMeta):
    __iter__ = None


class Disabled(metaclass=DisabledMeta):
    pass


class BadMeta(type):
    def __iter__(cls):
        return [1]


class Bad(metaclass=BadMeta):
    pass


for value in [Disabled, Bad]:
    try:
        iter(value)
    except TypeError:
        print('invalid iterator', value.__name__)


class IterDescriptor:
    def __get__(self, value, owner):
        return lambda: iter([value.__name__, owner.__name__])


class DescriptorMeta(type):
    __iter__ = IterDescriptor()


class DescriptorValues(metaclass=DescriptorMeta):
    pass


print('descriptor', list(DescriptorValues))


class PropertyMeta(type):
    @property
    def __iter__(cls):
        return lambda: iter([3, 5])
    @property
    def __len__(cls):
        return lambda: 2
    @property
    def __bool__(cls):
        return lambda: False
    @property
    def __contains__(cls):
        return lambda value: value == 3
    @property
    def __getitem__(cls):
        return lambda key: ('property', key)


class PropertyValues(metaclass=PropertyMeta):
    pass


print('property protocols', list(PropertyValues), len(PropertyValues), bool(PropertyValues),
      3 in PropertyValues, 5 in PropertyValues, PropertyValues[4])


class Plain:
    def __iter__(self):
        return iter([8])


try:
    iter(Plain)
except TypeError:
    print('instance protocol does not make class iterable')
print('instance unchanged', list(Plain()))
"""


def test_ts_metaclass_protocols(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "metaclass_protocols.py"
    entry.write_text(_METACLASS_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = tmp_path / "metaclass_protocols.dsp.json"
    bundle.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(bundle)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0, process.stderr
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript metaclass protocols")
