// Native type documentation from CPython 3.12.12, the pinned stdlib release.
// Source: https://github.com/python/cpython/tree/v3.12.12/Objects
// PSF license and notices: src/diaspore/cpython/LICENSE.
// Per-instance function/property documentation remains attached to its object.
package interp

var nativeTypeDocumentation = map[string]Value{
	"GenericAlias":       "Represent a PEP 585 generic type\n\nE.g. for t = list[int], t.__origin__ is list and t.__args__ is (int,).",
	"NoneType":           None,
	"NotImplementedType": None,
	"UnionType":          "Represent a PEP 604 union type\n\nE.g. for int | str",
	"async_generator":    None,
	"bool":               "bool(x) -> bool\n\nReturns True when the argument x is true, False otherwise.\nThe builtins True and False are the only two instances of the class bool.\nThe class bool is a subclass of the class int, and cannot be subclassed.",
	"bytearray":          "bytearray(iterable_of_ints) -> bytearray\nbytearray(string, encoding[, errors]) -> bytearray\nbytearray(bytes_or_buffer) -> mutable copy of bytes_or_buffer\nbytearray(int) -> bytes array of size given by the parameter initialized with null bytes\nbytearray() -> empty bytes array\n\nConstruct a mutable bytearray object from:\n  - an iterable yielding integers in range(256)\n  - a text string encoded using the specified encoding\n  - a bytes or a buffer object\n  - any object implementing the buffer API.\n  - an integer",
	"bytes":              "bytes(iterable_of_ints) -> bytes\nbytes(string, encoding[, errors]) -> bytes\nbytes(bytes_or_buffer) -> immutable copy of bytes_or_buffer\nbytes(int) -> bytes object of size given by the parameter initialized with null bytes\nbytes() -> empty bytes object\n\nConstruct an immutable array of bytes from:\n  - an iterable yielding integers in range(256)\n  - a text string encoded using the specified encoding\n  - any object implementing the buffer API.\n  - an integer",
	"cell":               "Create a new cell object.\n\n  contents\n    the contents of the cell. If not specified, the cell will be empty,\n    and \n further attempts to access its cell_contents attribute will\n    raise a ValueError.",
	"classmethod":        "classmethod(function) -> method\n\nConvert a function to be a class method.\n\nA class method receives the class as implicit first argument,\njust like an instance method receives the instance.\nTo declare a class method, use this idiom:\n\n  class C:\n      @classmethod\n      def f(cls, arg1, arg2, argN):\n          ...\n\nIt can be called either on the class (e.g. C.f()) or on an instance\n(e.g. C().f()).  The instance is ignored except for its class.\nIf a class method is called for a derived class, the derived class\nobject is passed as the implied first argument.\n\nClass methods are different than C++ or Java static methods.\nIf you want those, see the staticmethod builtin.",
	"code":               "Create a code object.  Not for the faint of heart.",
	"complex":            "Create a complex number from a string or numbers.\n\nIf a string is given, parse it as a complex number.\nIf a single number is given, convert it to a complex number.\nIf the 'real' or 'imag' arguments are given, create a complex number\nwith the specified real and imaginary components.",
	"coroutine":          None,
	"dict":               "dict() -> new empty dictionary\ndict(mapping) -> new dictionary initialized from a mapping object's\n    (key, value) pairs\ndict(iterable) -> new dictionary initialized as if via:\n    d = {}\n    for k, v in iterable:\n        d[k] = v\ndict(**kwargs) -> new dictionary initialized with the name=value pairs\n    in the keyword argument list.  For example:  dict(one=1, two=2)",
	"ellipsis":           None,
	"float":              "Convert a string or number to a floating-point number, if possible.",
	"frame":              None,
	"frozenset":          "frozenset() -> empty frozenset object\nfrozenset(iterable) -> frozenset object\n\nBuild an immutable unordered collection of unique elements.",
	"generator":          None,
	"int":                "int([x]) -> integer\nint(x, base=10) -> integer\n\nConvert a number or string to an integer, or return 0 if no arguments\nare given.  If x is a number, return x.__int__().  For floating-point\nnumbers, this truncates towards zero.\n\nIf x is not a number or if base is given, then x must be a string,\nbytes, or bytearray instance representing an integer literal in the\ngiven base.  The literal can be preceded by '+' or '-' and be surrounded\nby whitespace.  The base defaults to 10.  Valid bases are 0 and 2-36.\nBase 0 means to interpret the base from the string as an integer literal.\n>>> int('0b100', base=0)\n4",
	"list":               "Built-in mutable sequence.\n\nIf no argument is given, the constructor creates a new empty list.\nThe argument must be an iterable if specified.",
	"mappingproxy":       None,
	"memoryview":         "Create a new memoryview object which references the given object.",
	"object":             "The base class of the class hierarchy.\n\nWhen called, it accepts no arguments and returns a new featureless\ninstance that has no instance attributes and cannot be given any.\n",
	"property":           "Property attribute.\n\n  fget\n    function to be used for getting an attribute value\n  fset\n    function to be used for setting an attribute value\n  fdel\n    function to be used for del'ing an attribute\n  doc\n    docstring\n\nTypical use is to define a managed attribute x:\n\nclass C(object):\n    def getx(self): return self._x\n    def setx(self, value): self._x = value\n    def delx(self): del self._x\n    x = property(getx, setx, delx, \"I'm the 'x' property.\")\n\nDecorators make defining new properties or modifying existing ones easy:\n\nclass C(object):\n    @property\n    def x(self):\n        \"I am the 'x' property.\"\n        return self._x\n    @x.setter\n    def x(self, value):\n        self._x = value\n    @x.deleter\n    def x(self):\n        del self._x",
	"range":              "range(stop) -> range object\nrange(start, stop[, step]) -> range object\n\nReturn an object that produces a sequence of integers from start (inclusive)\nto stop (exclusive) by step.  range(i, j) produces i, i+1, i+2, ..., j-1.\nstart defaults to 0, and stop is omitted!  range(4) produces 0, 1, 2, 3.\nThese are exactly the valid indices for a list of 4 elements.\nWhen step is given, it specifies the increment (or decrement).",
	"set":                "set() -> new empty set object\nset(iterable) -> new set object\n\nBuild an unordered collection of unique elements.",
	"slice":              "slice(stop)\nslice(start, stop[, step])\n\nCreate a slice object.  This is used for extended slicing (e.g. a[0:10:2]).",
	"staticmethod":       "staticmethod(function) -> method\n\nConvert a function to be a static method.\n\nA static method does not receive an implicit first argument.\nTo declare a static method, use this idiom:\n\n     class C:\n         @staticmethod\n         def f(arg1, arg2, argN):\n             ...\n\nIt can be called either on the class (e.g. C.f()) or on an instance\n(e.g. C().f()). Both the class and the instance are ignored, and\nneither is passed implicitly as the first argument to the method.\n\nStatic methods in Python are similar to those found in Java or C++.\nFor a more advanced concept, see the classmethod builtin.",
	"str":                "str(object='') -> str\nstr(bytes_or_buffer[, encoding[, errors]]) -> str\n\nCreate a new string object from the given object. If encoding or\nerrors is specified, then the object must expose a data buffer\nthat will be decoded using the given encoding and error handler.\nOtherwise, returns the result of object.__str__() (if defined)\nor repr(object).\nencoding defaults to sys.getdefaultencoding().\nerrors defaults to 'strict'.",
	"traceback":          "TracebackType(tb_next, tb_frame, tb_lasti, tb_lineno)\n--\n\nCreate a new traceback object.",
	"tuple":              "Built-in immutable sequence.\n\nIf no argument is given, the constructor returns an empty tuple.\nIf iterable is specified the tuple is initialized from iterable's items.\n\nIf the argument is a tuple, the return value is the same object.",
	"type":               "type(object) -> the object's type\ntype(name, bases, dict, **kwds) -> a new type",
}

// Native callable declarations from CPython 3.12.12 Modules/timemodule.c.
var nativeFunctionDocumentation = map[string]map[string]Value{
	"time": {
		"time":         "time() -> floating-point number\n\nReturn the current time in seconds since the Epoch.\nFractions of a second may be present if the system clock provides them.",
		"monotonic":    "monotonic() -> float\n\nMonotonic clock, cannot go backward.",
		"perf_counter": "perf_counter() -> float\n\nPerformance counter for benchmarking.",
		"sleep":        "sleep(seconds)\n\nDelay execution for a given number of seconds.  The argument may be\na floating-point number for subsecond precision.",
	},
}

func nativeTypeDoc(name string) Value {
	if doc, declared := nativeTypeDocumentation[name]; declared {
		return doc
	}
	// A native type with no documentation declaration still owns __doc__:
	// like a null tp_doc in CPython, its value is None, not a missing attribute.
	return None
}

func nativeDocumentation(value Value) (Value, bool) {
	var name string
	switch object := value.(type) {
	case *BuiltinType:
		name = object.Name
	case *Class:
		if object != objectClass {
			return nil, false
		}
		name = "object"
	case *Instance:
		if object.Class != objectClass {
			return nil, false
		}
		name = "object"
	case *Module, *NativeModule, *Property, *StaticMethod, *ClassMethod, *Function, *BoundMethod, *Builtin, *metadataDescriptor, *GenericAlias:
		return nil, false
	default:
		name = typeName(value)
	}
	return nativeTypeDoc(name), true
}
