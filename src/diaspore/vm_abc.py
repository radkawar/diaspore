"""Abstract-class primitives with native callback frame semantics.

ABC metaclass callbacks execute in their caller's sandbox frame, matching the
CPython C layer. In particular, protocol checks can distinguish abc's internal
subclass checks from a user's explicit issubclass call.
"""

from weakref import WeakSet


class _ABCData:
    __slots__ = ("registry", "positive", "negative", "version")

    def __init__(self, version):
        self.registry = WeakSet()
        self.positive = WeakSet()
        self.negative = WeakSet()
        self.version = version


def make_abc_module(interp, module_type):
    """Build an interpreter-local implementation of the native _abc API.

    Args:
        interp: Interpreter used for sandbox attribute access and callbacks.
        module_type: Native module constructor.

    Returns:
        A native module with abstract-method, registration, and cache operations.
    """
    from .vm import BuiltinType, DiasporeClass

    class_types = (BuiltinType, DiasporeClass)
    token = 0
    getattr_ = interp.builtins["getattr"]
    issubclass_ = interp.builtins["issubclass"]
    state_type = BuiltinType("_abc_data", (_ABCData,), lambda *args, **kwargs: _ABCData(token))
    state_type.ns["__module__"] = "_abc"
    interp.host_types[_ABCData] = state_type

    def data(cls):
        value = interp.get_attr(cls, "_abc_impl")
        if not isinstance(value, _ABCData):
            raise TypeError("_abc_impl is set to a wrong type")
        return value

    def abstract(value):
        return interp.truth(getattr_(value, "__isabstractmethod__", False))

    def init(cls, /):
        namespace = interp.get_attr(cls, "__dict__")
        names = {name for name, value in tuple(namespace.items()) if abstract(value)}
        for base in interp.get_attr(cls, "__bases__"):
            for name in getattr_(base, "__abstractmethods__", ()):
                if abstract(getattr_(cls, name, None)):
                    names.add(name)
        interp.set_attr(cls, "__abstractmethods__", frozenset(names))
        interp.set_attr(cls, "_abc_impl", _ABCData(token))
        if isinstance(cls, class_types) and "__abc_tpflags__" in cls.ns:
            flags = cls.ns.pop("__abc_tpflags__")
            if type(flags) is int:
                if flags & 96 == 96:
                    raise TypeError("__abc_tpflags__ cannot be both "
                                    "Py_TPFLAGS_SEQUENCE and Py_TPFLAGS_MAPPING")
                cls.abc_flags = flags & 96

    def register(cls, subclass, /):
        nonlocal token
        if not isinstance(subclass, class_types):
            raise TypeError("Can only register classes")
        if issubclass_(subclass, cls):
            return subclass
        if issubclass_(cls, subclass):
            raise RuntimeError("Refusing to create an inheritance cycle")
        data(cls).registry.add(subclass)
        token += 1
        flags = getattr(cls, "abc_flags", 0)
        if flags:
            pending = [subclass]
            while pending:
                current = pending.pop()
                if isinstance(current, BuiltinType) or getattr(current, "abc_flags", 0) == flags:
                    continue
                current.abc_flags = flags
                pending.extend(current.subclasses)
        return subclass

    def instancecheck(cls, instance, /):
        state = data(cls)
        subclass = interp.get_attr(instance, "__class__")
        if subclass in state.positive:
            return True
        subtype = interp.type_of(instance)
        if subtype is subclass and state.version == token and subclass in state.negative:
            return False
        check = interp.get_attr(cls, "__subclasscheck__")
        result = interp.call(check, [subclass], {})
        if subtype is subclass or interp.truth(result):
            return result
        return interp.call(check, [subtype], {})

    def subclasscheck(cls, subclass, /):
        if not isinstance(subclass, class_types):
            raise TypeError("issubclass() arg 1 must be a class")
        state = data(cls)
        if subclass in state.positive:
            return True
        if state.version < token:
            state.negative.clear()
            state.version = token
        elif subclass in state.negative:
            return False
        result = interp.call(interp.get_attr(cls, "__subclasshook__"), [subclass], {})
        if result is True:
            state.positive.add(subclass)
            return True
        if result is False:
            state.negative.add(subclass)
            return False
        if result is not NotImplemented:
            raise AssertionError(
                "__subclasshook__ must return either False, True, or NotImplemented")
        if cls in subclass.mro:
            state.positive.add(subclass)
            return True
        if subclass in state.registry:
            return True
        for registered in tuple(state.registry):
            if issubclass_(subclass, registered):
                state.positive.add(subclass)
                return True
        children = interp.call(interp.get_attr(cls, "__subclasses__"), [], {})
        if not isinstance(children, list):
            raise TypeError("__subclasses__() must return a list")
        for child in children:
            if issubclass_(subclass, child):
                state.positive.add(subclass)
                return True
        state.negative.add(subclass)
        return False

    def dump(cls, /):
        state = data(cls)
        references = interp.get_attr(interp.import_module("_weakref"), "ref")

        def refs(values):
            return {interp.call(references, [value], {}) for value in values}

        return refs(state.registry), refs(state.positive), refs(state.negative), state.version

    def reset_registry(cls, /):
        data(cls).registry.clear()

    def reset_caches(cls, /):
        state = data(cls)
        state.positive.clear()
        state.negative.clear()

    return module_type("_abc", {
        "_abc_init": init,
        "_abc_register": register,
        "_abc_instancecheck": instancecheck,
        "_abc_subclasscheck": subclasscheck,
        "_get_dump": dump,
        "_reset_registry": reset_registry,
        "_reset_caches": reset_caches,
        "get_cache_token": lambda: token,
    })
