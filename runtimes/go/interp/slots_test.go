package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestPhysicalInstanceSlots(t *testing.T) {
	const source = `import types

named = []
class Naming:
    def __init__(self, label):
        self.label = label
    def __set_name__(self, owner, name):
        named.append((self.label, name))
        if name == 'first':
            owner.second = Naming('replacement')
            owner.added = Naming('added')
        delattr(owner, name)
        setattr(owner, name, self)
class Named:
    first = Naming('original-first')
    second = Naming('original-second')
    third = Naming('original-third')
    fourth = Naming('original-fourth')
assert named == [('original-first', 'first'), ('original-second', 'second'),
                 ('original-third', 'third'), ('original-fourth', 'fourth')]
assert Named.second.label == 'original-second'
assert Named.added.label == 'added'

class Empty:
    __slots__ = ()
empty = Empty()
assert not hasattr(empty, '__dict__')
assert not hasattr(object(), '__dict__')
for receiver in (empty, object()):
    try:
        receiver.extra = 1
    except AttributeError:
        pass
    else:
        raise AssertionError('dictionary-free object accepted attribute')

class DocumentedSlot:
    __slots__ = ('__doc__',)
documented = DocumentedSlot()
assert type(DocumentedSlot.__doc__) is types.MemberDescriptorType
documented.__doc__ = 'instance documentation'
assert documented.__doc__ == 'instance documentation'
try:
    type('ConflictingDoc', (), {'__slots__': ('__doc__',), '__doc__': None})
except ValueError:
    pass
else:
    raise AssertionError('explicit class documentation conflict accepted')
for primitive in (None, True, 1, 1.0, 1j, 'text', b'bytes', (), [], {}, set(), frozenset(), slice(1)):
    assert primitive.__getstate__() is None

class Base:
    __slots__ = ('value', '__hidden')
    def put(self, value):
        self.__hidden = value
    def hidden(self):
        return self.__hidden
base = Base()
assert type(Base.value) is types.MemberDescriptorType
assert Base.value.__objclass__ is Base
assert Base.value.__name__ == 'value'
assert Base.value.__get__(None, Base) is Base.value
assert Base.__dict__['value'] is Base.value
assert not hasattr(base, 'value')
base.value = 12
base.put(34)
assert base.value == 12 and base.hidden() == 34
assert base._Base__hidden == 34
assert not hasattr(base, '__hidden')
del base.value
assert not hasattr(base, 'value')
try:
    Base.value.__get__(empty)
except TypeError:
    pass
else:
    raise AssertionError('descriptor accepted wrong receiver')

class Child(Base):
    __slots__ = ('value', 'other')
child = Child()
Base.value.__set__(child, 'base')
child.value = 'child'
child.other = 5
assert Base.value.__get__(child) == 'base'
assert Child.value.__get__(child) == 'child'
del child.value
assert Base.value.__get__(child) == 'base'
assert not hasattr(child, 'value')

class Mixed(Base):
    __slots__ = ('__dict__', '__weakref__')
mixed = Mixed()
assert type(Mixed.__dict__['__dict__']) is types.GetSetDescriptorType
assert mixed.__weakref__ is None
alias = {'value': 'dictionary', 'extra': 9, 1: 'nonstring'}
mixed.__dict__ = alias
mixed.value = 'slot'
assert mixed.__dict__ is alias
assert mixed.value == 'slot' and alias['value'] == 'dictionary'
alias['extra'] = 10
assert mixed.extra == 10
mixed.extra = 11
assert alias['extra'] == 11
old = mixed.__dict__
del mixed.__dict__
assert mixed.__dict__ is not old and mixed.__dict__ == {}
assert old is alias and mixed.value == 'slot'
try:
    mixed.__dict__ = []
except TypeError:
    pass
else:
    raise AssertionError('non-dictionary assignment accepted')
class Mapping(dict):
    def __getitem__(self, key):
        raise AssertionError('dict subclass hook used by attribute lookup')
    def __setitem__(self, key, value):
        raise AssertionError('dict subclass hook used by attribute assignment')
replacement = Mapping()
mixed.__dict__ = replacement
mixed.normal = 8
assert mixed.normal == 8 and dict.__getitem__(replacement, 'normal') == 8

class Ordinary(Base):
    pass
ordinary = Ordinary()
ordinary.extra = 1
assert ordinary.__dict__ == {'extra': 1}
assert not hasattr(ordinary, 'value')
class Frozen(Ordinary):
    __slots__ = ()
frozen = Frozen()
frozen.extra = 2
assert frozen.__dict__ == {'extra': 2}

class Container(list):
    __slots__ = ('tag',)
container = Container([1, 2])
container.tag = 3
assert list(container) == [1, 2] and container.tag == 3
assert not hasattr(container, '__dict__')
class Number(int):
    __slots__ = ()
assert Number(7) == 7
assert not hasattr(Number(7), '__dict__')

class Left:
    __slots__ = ('left',)
class Right:
    __slots__ = ('right',)
for bases, namespace, expected in [
    ((Left, Right), {}, TypeError),
    ((int,), {'__slots__': ('x',)}, TypeError),
    ((tuple,), {'__slots__': ('x',)}, TypeError),
    ((bytes,), {'__slots__': ('x',)}, TypeError),
    ((object,), {'__slots__': ('not-valid',)}, TypeError),
    ((object,), {'__slots__': (1,)}, TypeError),
    ((object,), {'__slots__': ('x',), 'x': 1}, ValueError),
    ((Ordinary,), {'__slots__': ('__dict__',)}, TypeError),
    ((Mixed,), {'__slots__': ('__weakref__',)}, TypeError),
    ((bool,), {}, TypeError),
]:
    try:
        type('Invalid', bases, namespace)
    except expected:
        pass
    else:
        raise AssertionError('invalid instance layout accepted')

assert object.__new__ is object.__dict__['__new__']
assert object.__new__.__self__ is object
assert int.__new__.__self__ is int
assert Base.__flags__ & 512 and not object.__flags__ & 512
assert object.__init_subclass__() is None
seen = []
class Hook:
    def __init_subclass__(cls, *, marker):
        seen.append(cls)
        super().__init_subclass__()
class HookChild(Hook, marker=True):
    __slots__ = ()
assert seen == [HookChild]
try:
    object.__init_subclass__(marker=True)
except TypeError:
    pass
else:
    raise AssertionError('object init_subclass accepted keyword arguments')
`
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	vm := interp.New(bundle, interp.WithStderr(&stderr), interp.WithSnippetCompiler(dspc.CompileSnippet))
	defer vm.Close()
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d: %s", code, stderr.String())
	}
}
