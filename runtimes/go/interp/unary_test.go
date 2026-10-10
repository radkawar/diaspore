package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestUnarySpecialMethods(t *testing.T) {
	const source = `import types
import weakref

class Number(float):
    pass
value = Number(8)
proxy = weakref.proxy(value)
assert -value == -proxy == -8.0
assert +value == +proxy == 8.0
assert abs(value) == abs(proxy) == 8.0
assert type(-value) is float and type(+value) is float
class Integer(int):
    pass
assert ~Integer(8) == -9 and type(~Integer(8)) is int

class Parent:
    def __neg__(self):
        return 'negative'
    def __pos__(self):
        return 'positive'
    def __invert__(self):
        return 'inverted'
    def __abs__(self):
        return 'absolute'
class Child(Parent):
    def __getattribute__(self, name):
        if name in ('__neg__', '__pos__', '__invert__', '__abs__'):
            raise AssertionError('special lookup used instance attribute access')
        return object.__getattribute__(self, name)
child = Child()
child.__dict__['__neg__'] = lambda: 'shadow'
assert -child == 'negative' and +child == 'positive' and ~child == 'inverted'
assert abs(child) == 'absolute'

class Absolute(Integer):
    def __abs__(self):
        return super().__abs__() + 1
assert abs(Absolute(-8)) == 9
assert int.__abs__(Absolute(-8)) == 8
class AbsoluteDescriptor:
    def __get__(self, instance, owner):
        return lambda: 'descriptor absolute'
class WithAbsoluteDescriptor:
    __abs__ = AbsoluteDescriptor()
assert abs(WithAbsoluteDescriptor()) == 'descriptor absolute'
class AbsoluteMeta(type):
    def __abs__(cls):
        return cls.__name__
class AbsoluteClass(metaclass=AbsoluteMeta):
    pass
assert abs(AbsoluteClass) == 'AbsoluteClass'

class Override(Integer):
    def __neg__(self):
        return super().__neg__() - 1
    def __pos__(self):
        return super().__pos__() + 1
    def __invert__(self):
        return super().__invert__() - 1
assert -Override(8) == -9 and +Override(8) == 9 and ~Override(8) == -10
assert int.__neg__(Override(8)) == -8

seen = []
class Descriptor:
    def __get__(self, instance, owner):
        seen.append((instance, owner))
        return lambda: owner.__name__
class DescriptorBase:
    __neg__ = Descriptor()
    __pos__ = property(lambda self: lambda: 'property')
    __invert__ = staticmethod(lambda: 'static')
class DescriptorChild(DescriptorBase):
    pass
object = DescriptorChild()
assert -object == 'DescriptorChild'
assert seen == [(object, DescriptorChild)]
assert +object == 'property' and ~object == 'static'
class ClassMethod:
    @classmethod
    def __pos__(cls):
        return cls
assert +ClassMethod() is ClassMethod
class Meta(type):
    def __neg__(cls):
        return cls.__name__
class MetaObject(metaclass=Meta):
    pass
assert -MetaObject == 'MetaObject'

class Declines(int):
    __neg__ = None
    def __pos__(self):
        return NotImplemented
try:
    -Declines(1)
except TypeError:
    pass
else:
    raise AssertionError('noncallable override fell back to primitive')
assert +Declines(1) is NotImplemented
class Fails:
    def __invert__(self):
        raise LookupError('unary failure')
try:
    ~Fails()
except LookupError as error:
    assert str(error) == 'unary failure'
else:
    raise AssertionError('unary exception lost')

for name in ('__neg__', '__pos__', '__invert__'):
    descriptor = getattr(int, name)
    assert type(descriptor) is types.WrapperDescriptorType
    assert int.__dict__[name] is descriptor
    assert getattr(bool, name) is descriptor
    assert name not in bool.__dict__
assert not hasattr(float, '__invert__')
assert (~True, -True, +True) == (-2, -1, 1)
assert -(1 << 1000) == -1 * (1 << 1000)
assert -complex(1, 2) == complex(-1, -2)
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
	vm := interp.New(bundle, interp.WithStderr(&stderr))
	defer vm.Close()
	if code := vm.Run(); code != 0 {
		t.Fatalf("Run() = %d: %s", code, stderr.String())
	}
}
