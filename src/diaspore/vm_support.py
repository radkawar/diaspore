"""Install the reference VM's explicitly allowlisted builtin descriptors."""

from types import MappingProxyType, SimpleNamespace

from .vm_types import (
    Cell,
    CellNamespace,
    Code,
    Descriptor,
    FrameView,
    GenericAlias,
    Traceback,
    Union,
    VersionInfo,
)


def install_types(interp, types):
    """Populate sandbox types without publishing Python host type objects."""
    from .vm import (
        _MISSING,
        BYTEARRAY_METHODS,
        BYTES_METHODS,
        DICT_METHODS,
        FROZENSET_METHODS,
        LIST_METHODS,
        MEMORYVIEW_METHODS,
        OBJECT,
        SET_METHODS,
        STR_METHODS,
        TUPLE_METHODS,
        BoundMethod,
        BuiltinType,
        ClassMethod,
        DiasporeClass,
        DiasporeFunction,
        DiasporeGenerator,
        DiasporeInstance,
        DiasporeModule,
        Frame,
        NativeModule,
        Property,
        StaticMethod,
    )

    def unavailable(name):
        def construct(*args, **kwargs):
            raise TypeError(f"cannot create '{name}' instances")

        return construct

    def cell(value=_MISSING):
        result = Cell()
        if value is not _MISSING:
            result.cell_contents = value
        return result

    def function(code, globals, name=None, argdefs=None, closure=None):
        if not isinstance(code, Code) or not isinstance(globals, dict):
            raise TypeError("function() requires a code object and a globals dictionary")
        if argdefs is not None and not isinstance(argdefs, tuple):
            raise TypeError("arg 4 (defaults) must be None or tuple")
        if closure is not None and not isinstance(closure, tuple):
            raise TypeError("arg 5 (closure) must be None or tuple")
        cells = closure or ()
        if len(cells) != len(code.co_freevars) or any(not isinstance(c, Cell) for c in cells):
            raise ValueError("closure must match code free variables")
        frame = Frame(
            CellNamespace(zip(code.co_freevars, cells)),
            None,
            frozenset(code.co_freevars),
            frozenset(),
            frozenset(),
            False,
            globals,
            code.co_name,
            globals.get("__name__", "?"),
        )
        fn = DiasporeFunction(interp, code.node, frame, None)
        fn.name = code.co_name if name is None else name
        fn.defaults = argdefs if argdefs is not None else ()
        fn.kwdefaults = {}
        fn.cells = list(zip(code.co_freevars, cells))
        fn.closure_value = cells or None
        fn.code = code
        fn.metadata.update(
            __module__=globals.get("__name__"),
            __qualname__=code.co_qualname,
            __defaults__=argdefs,
            __annotations__={},
        )
        return fn

    extra = {
        "function": ((DiasporeFunction,), function),
        "method": ((BoundMethod,), lambda fn, obj: BoundMethod(fn, obj)),
        "code": ((Code,), unavailable("code")),
        "cell": ((Cell,), cell),
        "frame": ((FrameView,), unavailable("frame")),
        "traceback": ((Traceback,), Traceback),
        "mappingproxy": ((MappingProxyType,), MappingProxyType),
        "SimpleNamespace": ((SimpleNamespace,), SimpleNamespace),
        "GenericAlias": ((GenericAlias,), GenericAlias),
        "UnionType": ((Union,), unavailable("UnionType")),
        "module": (
            (DiasporeModule, NativeModule),
            lambda name, doc=None: NativeModule(
                name, {"__name__": name, "__doc__": doc, "__package__": None, "__spec__": None}
            ),
        ),
        "generator": ((DiasporeGenerator,), unavailable("generator")),
        "coroutine": ((), unavailable("coroutine")),
        "async_generator": ((), unavailable("async_generator")),
        "builtin_function_or_method": ((), unavailable("builtin_function_or_method")),
        "staticmethod": ((StaticMethod,), StaticMethod),
        "classmethod": ((ClassMethod,), ClassMethod),
        "property": ((Property,), Property),
        "ellipsis": ((type(Ellipsis),), lambda: Ellipsis),
        "NotImplementedType": ((type(NotImplemented),), lambda: NotImplemented),
    }
    for name in (
        "wrapper_descriptor",
        "method-wrapper",
        "method_descriptor",
        "classmethod_descriptor",
        "getset_descriptor",
        "member_descriptor",
    ):
        extra[name] = ((), unavailable(name))
    for name, (hosts, constructor) in extra.items():
        types[name] = BuiltinType(name, hosts, constructor)
    interp.types = types
    interp.host_types = {host: typ for typ in types.values() for host in typ.host_types}
    types["bool"].bases = [types["int"]]
    types["bool"].mro = [types["bool"], types["int"], OBJECT]
    version_type = BuiltinType("sys.version_info", (VersionInfo,), unavailable("sys.version_info"))
    version_type.bases = [types["tuple"]]
    version_type.mro = [version_type, *types["tuple"].mro]
    interp.host_types[VersionInfo] = version_type

    def descriptor(typ, name, function, kind="method_descriptor"):
        typ.ns[name] = Descriptor(name, typ, call=function, kind=kind)

    def object_new(cls, /, *args, **kwargs):
        if not isinstance(cls, DiasporeClass):
            raise TypeError("object.__new__ requires a type")
        return DiasporeInstance(cls)

    def object_init(obj, /, *args, **kwargs):
        if (args or kwargs) and obj.cls.lookup("__new__") is OBJECT.ns["__new__"]:
            raise TypeError(f"{obj.cls.name}() takes no arguments")

    def object_ne(obj, other):
        owner = obj.interp if isinstance(obj, DiasporeInstance) else interp
        method = owner.type_of(obj).lookup("__eq__")
        result = owner.call(owner.bind_attr(method, obj, owner.type_of(obj)), [other], {})
        return NotImplemented if result is NotImplemented else not owner.truth(result)

    def object_format(obj, spec):
        if not isinstance(spec, str):
            raise TypeError("format_spec must be a string")
        owner = obj.interp if isinstance(obj, DiasporeInstance) else interp
        if spec:
            raise TypeError(
                f"unsupported format string passed to {owner.type_of(obj).name}.__format__")
        return owner.diaspore_str(obj)

    def object_getstate(obj):
        namespace = dict(obj.d)
        slots = {}
        for base in obj.cls.mro:
            names = base.ns.get("__slots__", ())
            if isinstance(names, str):
                names = (names,)
            for name in names:
                if name in ("__dict__", "__weakref__"):
                    continue
                if name.startswith("__") and not name.endswith("__"):
                    name = "_" + base.name.lstrip("_") + name
                if name in namespace:
                    slots[name] = namespace.pop(name)
        state = namespace or None
        return (state, slots) if slots else state

    def object_reduce_ex(obj, protocol):
        owner = obj.interp
        reduce = obj.cls.lookup("__reduce__")
        if reduce is not OBJECT.ns["__reduce__"]:
            return owner.call(owner.bind_attr(reduce, obj, obj.cls), [], {})
        return object_reduce(obj, protocol)

    def object_reduce(obj, protocol=0):
        owner = obj.interp
        copyreg = owner.import_module("copyreg")
        if protocol < 2:
            return owner.call(owner.get_attr(copyreg, "_reduce_ex"), [obj, protocol], {})
        getnewargs = owner.builtins["getattr"](obj, "__getnewargs_ex__", None)
        if getnewargs is not None:
            args, kwargs = owner.call(getnewargs, [], {})
            if not isinstance(args, tuple) or not isinstance(kwargs, dict):
                raise TypeError("__getnewargs_ex__ must return a tuple and a dict")
        else:
            getnewargs = owner.builtins["getattr"](obj, "__getnewargs__", None)
            args = owner.call(getnewargs, [], {}) if getnewargs is not None else ()
            kwargs = {}
            if not isinstance(args, tuple):
                raise TypeError("__getnewargs__ should return a tuple")
        factory = owner.get_attr(copyreg, "__newobj_ex__" if kwargs else "__newobj__")
        newargs = (obj.cls, args, kwargs) if kwargs else (obj.cls, *args)
        state = owner.call(owner.get_attr(obj, "__getstate__"), [], {})
        raw = owner._raw(obj)
        listitems = owner.as_host_iter(obj) if isinstance(raw, list) else None
        dictitems = iter(raw.items()) if isinstance(raw, dict) else None
        return factory, newargs, state, listitems, dictitems

    descriptor(OBJECT, "__format__", object_format)
    descriptor(OBJECT, "__getstate__", object_getstate)
    descriptor(OBJECT, "__reduce_ex__", object_reduce_ex)
    descriptor(OBJECT, "__reduce__", object_reduce)

    descriptor(OBJECT, "__eq__", lambda obj, other: True if obj is other else NotImplemented,
               "wrapper_descriptor")
    descriptor(OBJECT, "__ne__", object_ne, "wrapper_descriptor")
    for name in ("__lt__", "__le__", "__gt__", "__ge__"):
        descriptor(OBJECT, name, lambda obj, other: NotImplemented, "wrapper_descriptor")

    descriptor(OBJECT, "__new__", object_new, "wrapper_descriptor")
    descriptor(OBJECT, "__init__", object_init, "wrapper_descriptor")
    descriptor(OBJECT, "__str__", lambda obj: obj.interp.diaspore_repr(obj), "wrapper_descriptor")
    descriptor(OBJECT, "__repr__", lambda obj: f"<{obj.cls.name} object>", "wrapper_descriptor")
    OBJECT.ns["__subclasshook__"] = ClassMethod(lambda cls, subclass: NotImplemented)
    OBJECT.ns["__init_subclass__"] = ClassMethod(lambda cls: None)
    OBJECT.ns["__doc__"] = object.__doc__
    descriptor(types["type"], "__new__", interp.new_class, "builtin_function_or_method")
    for attribute in ("__mro__", "__bases__", "__dict__", "__name__", "__qualname__"):
        types["type"].ns[attribute] = Descriptor(
            attribute, types["type"],
            get=lambda cls, _name=attribute: interp.get_attr(cls, _name, True),
            kind="getset_descriptor",
        )
    descriptor(types["type"], "__init__",
               lambda cls, name, bases, ns, **kwargs: None, "wrapper_descriptor")
    types["type"].ns["__prepare__"] = ClassMethod(lambda cls, name, bases, **kwargs: {})
    descriptor(
        types["type"],
        "__call__",
        lambda cls, /, *args, **kwargs: interp.instantiate(cls, list(args), kwargs),
        "wrapper_descriptor",
    )
    descriptor(
        types["type"], "__getattribute__", lambda cls, name: interp.get_attr(cls, name, True)
    )
    descriptor(types["type"], "__setattr__", interp.type_setattr)
    descriptor(types["type"], "__delattr__", lambda cls, name: cls.ns.__delitem__(name))
    descriptor(
        OBJECT,
        "__getattribute__",
        lambda obj, name: obj.interp.get_attr(obj, name, True),
        "wrapper_descriptor",
    )

    def property_init(obj, fget=None, fset=None, fdel=None, doc=None):
        value = interp._raw(obj)
        value.fget, value.fset, value.fdel = fget, fset, fdel
        value.doc_from_getter = doc is None
        value.doc = interp.builtins["getattr"](fget, "__doc__", None) if doc is None else doc
        if isinstance(obj, DiasporeInstance):
            obj.d["__doc__"] = value.doc

    def property_create(fget=None, fset=None, fdel=None, doc=None):
        value = Property()
        property_init(value, fget, fset, fdel, doc)
        return value

    def property_copy(obj, index, function):
        value = interp._raw(obj)
        args = [value.fget, value.fset, value.fdel, None if value.doc_from_getter else value.doc]
        args[index] = function
        return interp.call(interp.type_of(obj), args, {})

    def property_access(obj, instance, value=_MISSING, delete=False):
        prop = interp._raw(obj)
        function = prop.fdel if delete else prop.fget if value is _MISSING else prop.fset
        if function is None:
            action = "deleter" if delete else "getter" if value is _MISSING else "setter"
            raise AttributeError(f"property has no {action}")
        return interp.call(
            function, [instance] if delete or value is _MISSING else [instance, value], {}
        )

    types["property"].ctor = property_create
    descriptor(types["property"], "__init__", property_init)
    descriptor(
        types["property"],
        "__get__",
        lambda obj, instance, owner=None: (
            obj if instance is None else property_access(obj, instance)
        ),
    )
    descriptor(
        types["property"],
        "__set__",
        lambda obj, instance, value: property_access(obj, instance, value),
    )
    descriptor(
        types["property"],
        "__delete__",
        lambda obj, instance: property_access(obj, instance, delete=True),
    )
    for index, name in enumerate(("getter", "setter", "deleter")):
        descriptor(
            types["property"],
            name,
            lambda obj, function, _index=index: property_copy(obj, _index, function),
        )
    for name, field in (("fget", "fget"), ("fset", "fset"), ("fdel", "fdel"), ("__doc__", "doc")):
        types["property"].ns[name] = Descriptor(
            name,
            types["property"],
            get=lambda obj, _field=field: getattr(interp._raw(obj), _field),
            kind="member_descriptor",
        )
    types["property"].ns["__isabstractmethod__"] = Descriptor(
        "__isabstractmethod__",
        types["property"],
        get=lambda obj: any(
            interp.truth(interp.builtins["getattr"](fn, "__isabstractmethod__", False))
            for fn in (interp._raw(obj).fget, interp._raw(obj).fset, interp._raw(obj).fdel)
            if fn is not None
        ),
        kind="getset_descriptor",
    )
    for name in ("property", "classmethod", "staticmethod"):
        typ = types[name]
        descriptor(
            typ,
            "__new__",
            lambda cls, /, *args, _type=typ, **kwargs: interp.primitive_new(
                _type, cls, *args, **kwargs
            ),
        )
        if name != "property":
            descriptor(typ, "__init__", lambda obj, fn: setattr(interp._raw(obj), "fn", fn))
            descriptor(
                typ,
                "__get__",
                lambda obj, instance, owner=None: interp.bind_attr(
                    interp._raw(obj), instance, owner or interp.type_of(instance)
                ),
            )
            for field in ("__func__", "__wrapped__"):
                typ.ns[field] = Descriptor(
                    field, typ, get=lambda obj: interp._raw(obj).fn, kind="member_descriptor"
                )
            typ.ns["__isabstractmethod__"] = Descriptor(
                "__isabstractmethod__",
                typ,
                get=lambda obj: interp.builtins["getattr"](
                    interp._raw(obj).fn, "__isabstractmethod__", False
                ),
                kind="getset_descriptor",
            )

    def mutable_init(base, obj, args, kwargs):
        value = base.ctor(*args, **kwargs)
        raw = interp._raw(obj)
        if base.name == "dict":
            raw.update(value)
        elif base.name == "set":
            raw.clear()
            raw.update(value)
        else:
            raw[:] = value

    def primitive_operator(operation, name):
        def apply(obj, /, *args, **kwargs):
            if operation is object.__str__:
                return interp.diaspore_repr(obj)
            raw = interp._raw(obj)
            if name == "__repr__" and isinstance(raw, (dict, list, tuple, set, frozenset)):
                return interp.diaspore_repr(raw)
            if isinstance(raw, dict) and name in {
                    "__getitem__", "__setitem__", "__delitem__", "__contains__"}:
                return operation(raw, *args, **kwargs)
            if isinstance(raw, (list, tuple, set, frozenset)) and name == "__contains__":
                return operation(raw, *args, **kwargs)
            if isinstance(raw, (list, tuple, str, bytes, bytearray)) and args:
                if name in ("__getitem__", "__delitem__", "__setitem__"):
                    key = args[0]
                    if (isinstance(key, DiasporeInstance)
                            and key.cls.lookup("__index__") is not _MISSING):
                        key = interp.index_value(key)
                    if name == "__setitem__" and len(args) == 2:
                        value = args[1]
                        if isinstance(key, slice):
                            value = interp.as_host_iter(value)
                        elif isinstance(raw, bytearray):
                            value = interp.index_value(value)
                        return operation(raw, key, value, **kwargs)
                    return operation(raw, key, *args[1:], **kwargs)
            if name == "__iadd__" and isinstance(raw, list) and len(args) == 1:
                result = operation(raw, interp.as_host_iter(args[0]), **kwargs)
            else:
                result = operation(raw, *(interp._raw(value) for value in args), **kwargs)
            if (isinstance(raw, (list, dict, set, bytearray))
                    and name.startswith("__i") and result is raw):
                return obj
            return result

        return apply

    allowed = {
        "str": STR_METHODS,
        "bytes": BYTES_METHODS,
        "bytearray": BYTEARRAY_METHODS,
        "memoryview": MEMORYVIEW_METHODS,
        "list": LIST_METHODS,
        "tuple": TUPLE_METHODS,
        "dict": DICT_METHODS,
        "set": SET_METHODS,
        "frozenset": FROZENSET_METHODS,
    }
    operators = {
        "__add__",
        "__radd__",
        "__mul__",
        "__rmul__",
        "__sub__",
        "__rsub__",
        "__truediv__",
        "__rtruediv__",
        "__floordiv__",
        "__rfloordiv__",
        "__mod__",
        "__rmod__",
        "__pow__",
        "__rpow__",
        "__divmod__",
        "__rdivmod__",
        "__and__",
        "__rand__",
        "__or__",
        "__ror__",
        "__xor__",
        "__rxor__",
        "__lshift__",
        "__rlshift__",
        "__rshift__",
        "__rrshift__",
        "__iadd__",
        "__imul__",
        "__isub__",
        "__itruediv__",
        "__ifloordiv__",
        "__imod__",
        "__ipow__",
        "__iand__",
        "__ior__",
        "__ixor__",
        "__ilshift__",
        "__irshift__",
        "__neg__",
        "__round__",
        "__trunc__",
        "__floor__",
        "__ceil__",
        "__pos__",
        "__abs__",
        "__invert__",
        "__eq__",
        "__ne__",
        "__lt__",
        "__le__",
        "__gt__",
        "__ge__",
        "__hash__",
        "__len__",
        "__contains__",
        "__iter__",
        "__next__",
        "__reversed__",
        "__getitem__",
        "__setitem__",
        "__delitem__",
        "__index__",
        "__int__",
        "__float__",
        "__bool__",
        "__repr__",
        "__str__",
        "__format__",
    }
    for name in (
        "int",
        "float",
        "bool",
        "complex",
        "str",
        "bytes",
        "bytearray",
        "memoryview",
        "list",
        "tuple",
        "dict",
        "set",
        "frozenset",
        "range",
        "slice",
        "dict_keys",
        "dict_values",
        "dict_items",
    ):
        typ = types[name]
        host = typ.host_types[0]
        for method in operators:
            operation = getattr(host, method, None)
            if operation is not None:
                descriptor(
                    typ,
                    method,
                    primitive_operator(operation, method),
                    "wrapper_descriptor",
                )
        for method in allowed.get(name, ()):
            if hasattr(host, method):
                descriptor(
                    typ,
                    method,
                    lambda obj, *args, _n=method, **kwargs: interp.host_attr(interp._raw(obj), _n)(
                        *args, **kwargs
                    ),
                )
        descriptor(
            typ,
            "__new__",
            lambda cls, /, *args, _t=typ, **kwargs: interp.primitive_new(_t, cls, *args, **kwargs),
        )
        if name in ("list", "dict", "set", "bytearray"):
            descriptor(
                typ,
                "__init__",
                lambda obj, /, *args, _t=typ, **kwargs: mutable_init(_t, obj, args, kwargs),
            )
        else:
            descriptor(typ, "__init__", lambda obj, /, *args, **kwargs: None)
        if name in ("list", "tuple", "dict", "set", "frozenset"):
            typ.ns["__class_getitem__"] = ClassMethod(lambda cls, args: GenericAlias(cls, args))
        typ.ns["__doc__"] = host.__doc__
        if getattr(host, "__hash__", None) is None:
            typ.ns["__hash__"] = None

    def dict_item(obj, key):
        raw = interp._raw(obj)
        if (
            key not in raw
            and isinstance(obj, DiasporeInstance)
            and obj.cls.lookup("__missing__") is not _MISSING
        ):
            return interp.call_dunder(obj, "__missing__", [key])
        return raw[key]

    descriptor(types["dict"], "__getitem__", dict_item)
    descriptor(
        types["dict"],
        "fromkeys",
        lambda cls, keys, value=None: interp.call(
            cls, [{key: value for key in interp.as_host_iter(keys)}], {}
        ),
        "classmethod_descriptor",
    )
    types["str"].ns["maketrans"] = StaticMethod(str.maketrans)
    for name in ("bytes", "bytearray"):
        host = types[name].host_types[0]
        types[name].ns["fromhex"] = StaticMethod(host.fromhex)
        types[name].ns["maketrans"] = StaticMethod(host.maketrans)
    types["int"].ns["from_bytes"] = StaticMethod(
        lambda value, byteorder="big", *, signed=False: int.from_bytes(
            interp._raw(value)
            if isinstance(interp._raw(value), (bytes, bytearray, memoryview))
            else interp.as_host_iter(value),
            byteorder,
            signed=signed,
        )
    )
    descriptor(
        types["int"],
        "to_bytes",
        lambda value, length=1, byteorder="big", *, signed=False: int.to_bytes(
            interp._raw(value), length, byteorder, signed=signed
        ),
    )
    for name in ("__code__", "__closure__", "__defaults__", "__kwdefaults__", "__globals__"):
        types["function"].ns[name] = Descriptor(
            name,
            types["function"],
            get=lambda obj, _n=name: interp.get_attr(obj, _n),
            kind="member_descriptor" if name == "__globals__" else "getset_descriptor",
        )
    for name in ("__origin__", "__args__", "__parameters__"):
        types["GenericAlias"].ns[name] = Descriptor(
            name,
            types["GenericAlias"],
            get=lambda obj, _n=name: getattr(interp._raw(obj), _n),
            kind="member_descriptor",
        )
    descriptor(
        types["GenericAlias"],
        "__new__",
        lambda cls, origin, args: interp.primitive_new(types["GenericAlias"], cls, origin, args),
    )
    descriptor(types["GenericAlias"], "__init__", lambda obj, *args, **kwargs: None)
    return {name: types[name] for name in ("staticmethod", "classmethod", "property")}
