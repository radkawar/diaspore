package interp_test

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestFunctionReconstruction(t *testing.T) {
	const source = `import types
import sys

assert __import__('sys') is sys
assert __import__('sys', fromlist=('version',)) is sys
try:
    __import__('absent_module')
except ModuleNotFoundError:
    pass
else:
    raise AssertionError('unbundled module imported')

def inspect_frame():
    frame = sys._getframe()
    assert frame.f_globals is globals()
    assert frame.f_code is inspect_frame.__code__
    assert frame.f_back.f_code.co_name == '<module>'
    assert sys._getframe(-1) is frame
    try:
        sys._getframe(100)
    except ValueError:
        pass
    else:
        raise AssertionError('invalid frame depth accepted')

inspect_frame()

def factory():
    value = 7
    def read():
        return value
    def write(new):
        nonlocal value
        value = new
    def erase():
        nonlocal value
        del value
    return read, write, erase

read, write, erase = factory()
clone = types.FunctionType(read.__code__, globals(), closure=read.__closure__)
assert clone.__closure__ is read.__closure__
assert clone.__globals__ is globals()
assert clone() == 7
clone_write = types.FunctionType(write.__code__, globals(), closure=read.__closure__)
clone_write(11)
assert read() == clone() == 11

def nested():
    unused = 0
    other = 4
    def middle():
        def replacement():
            return other * 2
        return replacement
    return middle()

clone.__code__ = nested().__code__
assert clone() == 22
assert clone.__closure__ is read.__closure__
clone_erase = types.FunctionType(erase.__code__, globals(), closure=read.__closure__)
clone_erase()
try:
    read()
except NameError:
    pass
else:
    raise AssertionError('deleted closure binding survived')
clone_write(6)
assert clone() == 12

def template(step=2):
    global counter
    counter += step
    return counter

namespace = {'counter': 10}
custom = types.FunctionType(template.__code__, namespace, 'custom', (3,))
assert custom.__globals__ is namespace
assert custom() == 13
assert namespace['counter'] == 13
namespace['counter'] = 20
assert custom(5) == 25

def outer():
    def inner():
        return counter
    return inner

custom.__code__ = outer.__code__
inner = custom()
assert inner() == 25
assert inner.__globals__ is namespace
namespace['counter'] = 30
assert inner() == 30
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

func TestStaticTypeDescriptorsAndInspection(t *testing.T) {
	const source = `import inspect
import types

mro_descriptor = type.__dict__['__mro__']
dict_descriptor = type.__dict__['__dict__']
bases_descriptor = type.__dict__['__bases__']
base_descriptor = type.__dict__['__base__']
for descriptor in (mro_descriptor, dict_descriptor, bases_descriptor):
    assert type(descriptor) is types.GetSetDescriptorType
    assert descriptor.__objclass__ is type
assert type(base_descriptor) is types.MemberDescriptorType
assert mro_descriptor.__get__(None, type) is mro_descriptor
assert mro_descriptor.__get__(bool) == (bool, int, object)
assert bool.__mro__ == (bool, int, object)
assert bases_descriptor.__get__(bool) == (int,)
assert bool.__bases__ == (int,)
assert base_descriptor.__get__(bool) is int
assert base_descriptor.__get__(object) is None
assert bases_descriptor.__get__(object) == ()
assert mro_descriptor.__get__(type) == (type, object)

class Meta(type):
    @property
    def __mro__(cls):
        raise AssertionError('dynamic MRO getter called')
    @property
    def __dict__(cls):
        raise AssertionError('dynamic namespace getter called')
class Base(metaclass=Meta):
    marker = 4
class Child(Base):
    pass
assert mro_descriptor.__get__(Child) == (Child, Base, object)
assert bases_descriptor.__get__(Child) == (Base,)
assert base_descriptor.__get__(Child) is Base
namespace = dict_descriptor.__get__(Child)
Child.added = 7
assert namespace['added'] == 7
# inspect deliberately skips classes whose metaclass shadows __dict__.
# The direct type descriptors above remain available for that case.
try:
    inspect.getattr_static(Child, 'marker')
except AttributeError:
    pass
else:
    raise AssertionError('static inspection ignored shadowed class namespace')
class OrdinaryBase:
    marker = 4
class OrdinaryChild(OrdinaryBase):
    pass
assert inspect.getattr_static(OrdinaryChild, 'marker') == 4
assert inspect.getattr_static(OrdinaryChild, 'missing', 9) == 9

class Record:
    @property
    def field(self):
        raise AssertionError('property evaluated by static inspection')
record = Record()
record.__dict__['field'] = 8
assert inspect.getattr_static(record, 'field') is Record.__dict__['field']
class Slotted:
    __slots__ = ('field',)
slotted = Slotted()
slotted.field = 5
assert inspect.getattr_static(slotted, 'field') is Slotted.__dict__['field']
assert type(property.__dict__['__get__']) is types.WrapperDescriptorType
assert '__set__' in types.GetSetDescriptorType.__dict__
assert '__get__' in types.MemberDescriptorType.__dict__
try:
    mro_descriptor.__get__(record)
except TypeError:
    pass
else:
    raise AssertionError('type descriptor accepted non-type receiver')
for descriptor in (mro_descriptor, dict_descriptor, base_descriptor):
    try:
        descriptor.__set__(Child, None)
    except AttributeError:
        pass
    else:
        raise AssertionError('read-only type descriptor accepted assignment')
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

func TestMainModuleDataclassMetadata(t *testing.T) {
	const source = `from __future__ import annotations
import sys
from dataclasses import dataclass, fields

assert __name__ == '__main__'
entry = sys.modules[__name__]
assert __import__('__main__') is entry
marker = object()
assert entry.marker is marker
@dataclass
class Record:
    name: str
    count: int = 2
assert Record.__module__ == '__main__'
assert entry.Record is Record
assert Record('one') == Record('one', 2)
assert repr(Record('one')) == "Record(name='one', count=2)"
assert [field.name for field in fields(Record)] == ['name', 'count']
assert fields(Record)[0].type == 'str'
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

func TestFilesystemMetadataErrors(t *testing.T) {
	const source = `import errno
import os
import sys
from pathlib import Path

missing = sys.argv[1]
entry = Path(missing)
assert not entry.exists()
assert not entry.is_file()
assert not entry.is_dir()
assert not os.path.exists(missing)
for operation in (os.stat, os.lstat, os.listdir):
    try:
        operation(missing)
    except FileNotFoundError as error:
        assert isinstance(error, OSError)
        assert error.errno == errno.ENOENT
        assert error.strerror == 'No such file or directory'
        assert error.filename == missing
        assert error.args == (errno.ENOENT, error.strerror)
        assert str(error) == "[Errno 2] No such file or directory: " + repr(missing)
    else:
        raise AssertionError('missing path did not raise')
`
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	for _, overlay := range []bool{false, true} {
		name := "host"
		if overlay {
			name = "overlay"
		}
		t.Run(name, func(t *testing.T) {
			var stderr bytes.Buffer
			options := []interp.Option{
				interp.WithStderr(&stderr), interp.WithCaps("fs"),
				interp.WithArgs([]string{"main.py", t.TempDir() + "/absent"}),
				interp.WithSnippetCompiler(dspc.CompileSnippet),
			}
			if overlay {
				options = append(options, interp.WithFSOverlay(map[string]string{}))
			}
			vm := interp.New(bundle, options...)
			defer vm.Close()
			if code := vm.Run(); code != 0 {
				t.Fatalf("Run() = %d: %s", code, stderr.String())
			}
		})
	}
}

func TestMutableBuiltinHashMetadata(t *testing.T) {
	const source = `from dataclasses import dataclass, field

for builtin in (list, dict, set, bytearray):
    assert builtin.__hash__ is None
    assert builtin.__dict__['__hash__'] is None
    value = builtin()
    assert value.__hash__ is None
    try:
        hash(value)
    except TypeError:
        pass
    else:
        raise AssertionError('mutable builtin was hashable')
    try:
        @dataclass
        class Invalid:
            item: object = value
    except ValueError:
        pass
    else:
        raise AssertionError('dataclass accepted mutable default')
    class Inherited(builtin):
        pass
    assert Inherited.__hash__ is None
    assert Inherited().__hash__ is None
    assert '__hash__' not in Inherited.__dict__
    class Hashable(builtin):
        __hash__ = object.__hash__
    assert Hashable.__hash__ is object.__hash__
    assert callable(Hashable().__hash__)

@dataclass
class Valid:
    items: list = field(default_factory=list)
first, second = Valid(), Valid()
first.items.append(1)
assert first.items == [1] and second.items == []

class list:
    pass
assert list.__hash__ is object.__hash__
assert callable(list().__hash__)
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

func TestNativeTypeDocumentationDefaults(t *testing.T) {
	const source = `views = ({}.keys(), {}.values(), {}.items())
for view in views:
    cls = type(view)
    assert cls.__doc__ is None
    assert cls.__dict__['__doc__'] is None
    assert view.__doc__ is None
for value in (1, 1.0, True, '', b'', bytearray(), [], {}, set(), (), range(0)):
    cls = type(value)
    assert cls.__doc__ == cls.__dict__['__doc__']
    assert value.__doc__ == cls.__doc__
class Undocumented(list):
    pass
assert Undocumented.__doc__ is None
assert Undocumented.__dict__['__doc__'] is None
assert Undocumented().__doc__ is None
class Documented(list):
    "User documentation."
assert Documented.__doc__ == 'User documentation.'
assert Documented().__doc__ == 'User documentation.'
def documented():
    "Function documentation."
assert documented.__doc__ == 'Function documentation.'
assert property(documented).__doc__ == 'Function documentation.'
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

func TestReferenceBuiltinTypeInstallation(t *testing.T) {
	const source = `from diaspore.vm import Interp

runtime = Interp({'diaspore': 1, 'main': 'main',
                  'modules': {'main': {'name': 'main', 'file': 'main.py', 'body': []}}})
try:
    for name in ('dict_keys', 'dict_values', 'dict_items'):
        native = runtime.types[name]
        assert native.ns['__doc__'] is None
        assert native.host_types[0].__doc__ is None
    for name in ('list', 'dict', 'str', 'int'):
        native = runtime.types[name]
        assert native.ns['__doc__'] == native.host_types[0].__doc__
finally:
    runtime.close()
`
	entry := filepath.Join(t.TempDir(), "main.py")
	if err := os.WriteFile(entry, []byte(source), 0600); err != nil {
		t.Fatal(err)
	}
	sourceRoot, err := filepath.Abs("../../../src")
	if err != nil {
		t.Fatal(err)
	}
	compiled, err := dspc.CompileFile(entry, dspc.CompileOptions{SearchPaths: []string{sourceRoot}})
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

func TestNativeCallableMetadata(t *testing.T) {
	const source = `import math
import time

for function, name in [(len, 'len'), (math.sqrt, 'sqrt'),
                       (time.time, 'time'), (time.monotonic, 'monotonic'),
                       (time.perf_counter, 'perf_counter'), (time.sleep, 'sleep')]:
    assert function.__name__ == name
    assert function.__qualname__ == name
assert time.monotonic.__doc__ == 'monotonic() -> float\n\nMonotonic clock, cannot go backward.'
assert time.perf_counter.__doc__ == 'perf_counter() -> float\n\nPerformance counter for benchmarking.'
assert time.time.__doc__ == 'time() -> floating-point number\n\nReturn the current time in seconds since the Epoch.\nFractions of a second may be present if the system clock provides them.'
assert time.sleep.__doc__ == 'sleep(seconds)\n\nDelay execution for a given number of seconds.  The argument may be\na floating-point number for subsecond precision.'
assert object.__new__.__name__ == '__new__'
assert object.__new__.__qualname__ == 'object.__new__'
class Owner:
    clock = time.perf_counter
assert Owner().clock is time.perf_counter
assert Owner().clock.__name__ == 'perf_counter'
`
	compiled, err := dspc.CompileSource("/src/main.py", source, dspc.CompileOptions{})
	if err != nil || compiled.Code != 0 {
		t.Fatalf("compile: %v %s", err, compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"real", "deterministic", "durable"} {
		t.Run(mode, func(t *testing.T) {
			var stderr bytes.Buffer
			options := []interp.Option{interp.WithStderr(&stderr)}
			switch mode {
			case "deterministic":
				options = append(options, interp.WithDeterministic())
			case "durable":
				options = append(options, interp.WithDurable())
			}
			vm := interp.New(bundle, options...)
			defer vm.Close()
			if code := vm.Run(); code != 0 {
				t.Fatalf("Run() = %d: %s", code, stderr.String())
			}
		})
	}
}

func TestBytearrayNativeConstructionProtocols(t *testing.T) {
	const source = `import types

initializer = bytearray.__dict__['__init__']
assert initializer is bytearray.__init__
assert type(initializer) is types.WrapperDescriptorType
assert initializer.__objclass__ is bytearray
assert bytearray.__new__(bytearray, b'not initialized') == bytearray()
value = bytearray(b'old')
alias = value
assert initializer(value, b'new') is None
assert alias == bytearray(b'new')
class Plain(bytearray):
    pass
assert type(Plain(b'AB')) is Plain
assert Plain(b'AB') == bytearray(b'AB')
assert Plain.fromhex('4142') == Plain(b'AB')

seen = []
class CustomNew(bytearray):
    def __new__(cls, value):
        instance = super().__new__(cls, value)
        seen.append(bytes(instance))
        return instance
instance = CustomNew(b'AB')
assert seen == [b'']
assert type(instance) is CustomNew and instance == bytearray(b'AB')

class CustomInit(bytearray):
    def __init__(self, value):
        seen.append(bytes(self))
        super().__init__(value)
seen.clear()
instance = CustomInit.fromhex('4344')
assert seen == [b'']
assert type(instance) is CustomInit and instance == bytearray(b'CD')

class SuppressedInit(bytearray):
    def __init__(self, value):
        pass
assert SuppressedInit(b'not initialized') == bytearray()

class Alternate(bytearray):
    def __new__(cls, value):
        return ('alternate', value)
    def __init__(self, value):
        raise AssertionError('initializer called for unrelated result')
assert Alternate.fromhex('41') == ('alternate', b'A')
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
