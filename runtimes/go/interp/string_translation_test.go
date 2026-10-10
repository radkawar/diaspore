package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestStringTranslationAndObjectEqualitySlots(t *testing.T) {
	const source = `assert str.maketrans is ''.maketrans
assert str.__dict__['maketrans'].__func__ is str.maketrans
class StaticTranslation:
    maketrans = str.maketrans
assert StaticTranslation().maketrans('a', 'b') == {97: 98}
assert str.maketrans('abc', 'XYZ', 'c') == {97: 88, 98: 89, 99: None}
assert 'abcabc'.translate(str.maketrans('abc', 'XYZ', 'c')) == 'XYXY'
assert str.translate('abc', {97: 'expanded', 98: None}) == 'expandedc'
assert 'unchanged'.translate({}) == 'unchanged'
assert ''.translate(None) == ''
assert '\ud800\udfff'.translate({0xd800: 0xdfff, 0xdfff: '\ud800'}) == '\udfff\ud800'
assert str.maketrans('\ud800', '\udfff') == {0xd800: 0xdfff}
assert 'a'.translate({97: True}) == '\x01'
marker = object()
assert str.maketrans({'a': marker})[97] is marker

class Text(str):
    pass
assert 'a'.translate({97: Text('bc')}) == 'bc'
assert str.maketrans(Text('a'), Text('b')) == {97: 98}

class DictSubclass(dict):
    pass
subclass = DictSubclass()
subclass['a'] = 'b'
try:
    str.maketrans(subclass)
except TypeError:
    pass
else:
    raise AssertionError('maketrans accepted a non-exact dict')

for table, error in [({'ab': 'x'}, ValueError), ({(): 'x'}, TypeError)]:
    try:
        str.maketrans(table)
    except error:
        pass
    else:
        raise AssertionError('invalid maketrans key accepted')
try:
    str.maketrans('ab', 'x')
except ValueError:
    pass
else:
    raise AssertionError('unequal mapping strings accepted')

class IndexOnly:
    def __index__(self):
        raise AssertionError('translation must not invoke __index__')
for replacement, error in [(-1, ValueError), (0x110000, ValueError),
                           (10 ** 100, ValueError), (IndexOnly(), TypeError),
                           (1.0, TypeError)]:
    try:
        'a'.translate({97: replacement})
    except error:
        pass
    else:
        raise AssertionError('invalid translation result accepted')

class Mapping:
    def __init__(self, values):
        self.values = values
        self.calls = []
    def __getitem__(self, ordinal):
        self.calls.append(ordinal)
        return self.values.get(ordinal, ordinal)
identity = Mapping({})
assert 'aba'.translate(identity) == 'aba'
assert identity.calls == [97, 98]
expanded = Mapping({97: 'XY'})
assert 'aa'.translate(expanded) == 'XYXY'
assert expanded.calls == [97, 97, 97]
deleted = Mapping({233: None})
assert '\xe9x'.translate(deleted) == 'x'
assert deleted.calls == [233, 120, 120]

class Missing:
    def __getitem__(self, ordinal):
        raise LookupError('missing')
assert 'abc'.translate(Missing()) == 'abc'
class Broken:
    def __getitem__(self, ordinal):
        raise RuntimeError('lookup failed')
try:
    'a'.translate(Broken())
except RuntimeError as error:
    assert str(error) == 'lookup failed'
else:
    raise AssertionError('mapping failure was swallowed')

left = object()
right = object()
assert object.__eq__(left, left) is True
assert object.__eq__(left, right) is NotImplemented
assert object.__ne__(left, left) is False
assert object.__ne__(left, right) is NotImplemented
class Equality:
    def __eq__(self, other):
        return True
    def __getattribute__(self, name):
        if name == '__eq__':
            raise AssertionError('instance lookup used for equality slot')
        return object.__getattribute__(self, name)
instance = Equality()
instance.__dict__['__eq__'] = lambda other: False
assert Equality.__ne__ is object.__ne__
assert object.__ne__(instance, right) is False
class Declines:
    def __eq__(self, other):
        return NotImplemented
assert object.__ne__(Declines(), right) is NotImplemented
class PropertyEquality:
    @property
    def __eq__(self):
        return lambda other: False
assert object.__ne__(PropertyEquality(), right) is True
class StaticEquality:
    __eq__ = staticmethod(lambda other: False)
assert object.__ne__(StaticEquality(), right) is True
class EqualityDescriptor:
    def __get__(self, instance, owner):
        return lambda other: False
class DescriptorEquality:
    __eq__ = EqualityDescriptor()
assert object.__ne__(DescriptorEquality(), right) is True
assert object.__ne__(1, 1.0) is False
assert object.__ne__(1, '1') is NotImplemented
assert str.__eq__('a', right) is NotImplemented
assert dict.__ne__({}, right) is NotImplemented
assert int.__format__(42, '04d') == '0042'
assert float.__format__(1.5, '.2f') == '1.50'
assert str.__format__('x', '>3') == '  x'
assert list.__format__ is object.__format__
assert '__format__' not in list.__dict__
assert str.__dict__['__format__'] is str.__format__
class Printable:
    def __str__(self):
        return 'printable'
assert object.__format__(Printable(), '') == 'printable'
for spec in ['>4', 1]:
    try:
        object.__format__(Printable(), spec)
    except TypeError:
        pass
    else:
        raise AssertionError('object format accepted invalid specifier')
`
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v, %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	vm := interp.New(bundle, interp.WithStderr(&stderr))
	defer vm.Close()
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d: %s", code, stderr.String())
	}
}

func TestNativeStringAndMetaclassProtocols(t *testing.T) {
	const source = `import types

assert 'a:b:c'.split(sep=':', maxsplit=1) == ['a', 'b:c']
assert 'a:b:c'.rsplit(maxsplit=1, sep=':') == ['a:b', 'c']
assert '  a b  '.split(maxsplit=0) == ['a b  ']
class SplitCount:
    def __index__(self):
        return 1
assert 'a:b:c'.split(':', maxsplit=SplitCount()) == ['a', 'b:c']
for invoke in (lambda: 'a:b'.split(':', sep=':'),
               lambda: 'a:b'.split(unknown=1)):
    try:
        invoke()
    except TypeError:
        pass
    else:
        raise AssertionError('invalid split arguments accepted')
for text, expected in [('', False), ('0', True), ('\u0662', True),
                       ('\u00b2', False), ('\u2163', False), ('1a', False)]:
    assert text.isdecimal() is expected
assert repr('\x80\xa0\u200b\U000e0001') == "'\\x80\\xa0\\u200b\\U000e0001'"
assert repr('ÿ') == "'ÿ'"
mapping = {'first': 1}
proxy = types.MappingProxyType(mapping)
assert str(proxy) == "{'first': 1}"
assert repr(proxy) == "mappingproxy({'first': 1})"
mapping['second'] = 2
assert str(proxy) == str(mapping)

calls = []
class RootMeta(type):
    def __instancecheck__(cls, instance):
        calls.append('root-instance')
        return super().__instancecheck__(instance)
    def __subclasscheck__(cls, derived):
        calls.append('root-subclass')
        return super().__subclasscheck__(derived)
class Meta(RootMeta):
    def __instancecheck__(cls, instance):
        calls.append('instance')
        return super().__instancecheck__(instance)
    def __subclasscheck__(cls, derived):
        calls.append('subclass')
        return super().__subclasscheck__(derived)
class Base(metaclass=Meta):
    pass
class Child(Base):
    pass
assert isinstance(Child(), Base)
assert calls == ['instance', 'root-instance']
calls.clear()
assert issubclass(Child, Base)
assert calls == ['subclass', 'root-subclass']
calls.clear()
assert type.__instancecheck__(Base, Child())
assert type.__subclasscheck__(Base, Child)
assert calls == []
assert not type.__instancecheck__(Base, object())
assert not type.__subclasscheck__(Base, object)
class Spoof:
    @property
    def __class__(self):
        return Child
assert type.__instancecheck__(Base, Spoof())
assert type.__instancecheck__(int, True)
assert type.__subclasscheck__(int, bool)
assert type(type.__dict__['__instancecheck__']) is types.MethodDescriptorType
`
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v, %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	vm := interp.New(bundle, interp.WithStderr(&stderr))
	defer vm.Close()
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d: %s", code, stderr.String())
	}
}

func TestNativeStringExpandTabs(t *testing.T) {
	const source = `assert 'a\tb'.expandtabs() == 'a       b'
assert 'abc\tdef'.expandtabs(tabsize=4) == 'abc def'
assert 'abcd\t'.expandtabs(4) == 'abcd    '
assert '\r\tA\n\tB\v\tC\f\tD'.expandtabs(4) == '\r    A\n    B\v  C\f  D'
assert 'é界\ud800\tX'.expandtabs(4) == 'é界\ud800 X'
assert '\t\t'.expandtabs(0) == ''
assert '\tA\t'.expandtabs(-2147483648) == 'A'
assert 'a\tb'.expandtabs(True) == 'a b'
assert str.expandtabs('x\t', tabsize=3) == 'x  '
assert '\t'.expandtabs(129) == ' ' * 129
class Text(str):
    pass
assert type(Text('x\t').expandtabs()) is str
calls = []
class Index:
    def __index__(self):
        calls.append('index')
        return 4
assert 'a\tb'.expandtabs(Index()) == 'a   b'
assert calls == ['index']
class IntOnly:
    def __int__(self):
        raise AssertionError('__int__ must not be called')
class BadIndex:
    def __index__(self):
        return 2.0
for value in (None, 1.5, IntOnly(), BadIndex()):
    try:
        'without tabs'.expandtabs(value)
    except TypeError:
        pass
    else:
        raise AssertionError('invalid index accepted')
for value in (-2147483649, 2147483648, 2**100):
    try:
        ''.expandtabs(value)
    except OverflowError as exc:
        assert str(exc) == 'Python int too large to convert to C int'
    else:
        raise AssertionError('C int overflow accepted')
for args, kw, message in (
    ((4,), {'tabsize': 2}, 'expandtabs() takes at most 1 argument (2 given)'),
    ((), {'other': 2}, "'other' is an invalid keyword argument for expandtabs()"),
):
    try:
        'x\t'.expandtabs(*args, **kw)
    except TypeError as exc:
        assert str(exc) == message
    else:
        raise AssertionError('invalid arguments accepted')
`
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{NoStdlib: true})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v, %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	vm := interp.New(bundle, interp.WithStderr(&stderr))
	defer vm.Close()
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d: %s", code, stderr.String())
	}
}
