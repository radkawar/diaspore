"""Provide dataclass initialization, repr, equality, hashing, and freezing.

Fields, defaults, factories, field init/repr/compare/hash flags, fields(),
is_dataclass(), and replace() are supported for non-inherited classes with
runtime __annotations__. Existing init/repr/eq methods are preserved.
Inheritance, ClassVar, InitVar, ordering, slots, keyword-only fields, custom
metadata, unsafe_hash, and recursive conversion helpers are unsupported and
raise NotImplementedError. Frozen classes require object attribute mutators
and normal __setattr__/__delattr__ dispatch from the runtime.
"""


class _Missing:
    pass


MISSING = _Missing()


class FrozenInstanceError(AttributeError):
    """Report an attempted mutation of a frozen dataclass."""


class Field:
    """Describe a declared dataclass field."""

    def __init__(self, default, default_factory, init, repr, hash, compare):
        self.name = None
        self.type = None
        self.default = default
        self.default_factory = default_factory
        self.init = init
        self.repr = repr
        self.hash = hash
        self.compare = compare
        self.metadata = {}
        self.kw_only = False


def field(*, default=MISSING, default_factory=MISSING, init=True, repr=True,
          hash=None, compare=True, metadata=None, kw_only=MISSING):
    """Declare a field, optionally with a default or zero-argument factory."""
    if default is not MISSING and default_factory is not MISSING:
        raise ValueError("cannot specify both default and default_factory")
    if metadata is not None or (kw_only is not MISSING and kw_only):
        raise NotImplementedError("field metadata and keyword-only fields are unsupported")
    return Field(default, default_factory, init, repr, hash, compare)


_repr_running = set()


def _special_annotation(annotation):
    if isinstance(annotation, str):
        name = annotation.replace(" ", "").split("[", 1)[0]
        return name in (
            "ClassVar", "typing.ClassVar", "InitVar", "dataclasses.InitVar",
            "KW_ONLY", "dataclasses.KW_ONLY",
        )
    name = getattr(annotation, "__name__", "")
    origin = getattr(annotation, "__origin__", None)
    return name in ("ClassVar", "InitVar") or getattr(origin, "__name__", "") == "ClassVar"


def _argument_names(names):
    quoted = [repr(name) for name in names]
    if len(quoted) == 1:
        return quoted[0]
    if len(quoted) == 2:
        return quoted[0] + " and " + quoted[1]
    return ", ".join(quoted[:-1]) + ", and " + quoted[-1]


def dataclass(cls=None, *, init=True, repr=True, eq=True, order=False,
              unsafe_hash=False, frozen=False, match_args=True, kw_only=False,
              slots=False, weakref_slot=False):
    """Decorate a class whose ordered field annotations survive compilation."""
    if order and not eq:
        raise ValueError("eq must be true if order is true")
    if order or unsafe_hash or kw_only or slots or weakref_slot:
        raise NotImplementedError(
            "ordering, unsafe_hash, keyword-only fields, and slots are unsupported"
        )

    def decorate(cls):
        namespace = cls.__dict__
        explicit_hash = "__hash__" in namespace and not (
            namespace["__hash__"] is None and "__eq__" in namespace
        )
        if cls.__bases__ != (object,):
            raise NotImplementedError("dataclass inheritance is unsupported")
        if "__annotations__" not in namespace:
            raise NotImplementedError("dataclass requires runtime class __annotations__")
        annotations = namespace["__annotations__"]
        declared = {}
        seen_default = None
        for name, annotation in annotations.items():
            if _special_annotation(annotation):
                raise NotImplementedError("ClassVar, InitVar, and KW_ONLY are unsupported")
            default = namespace.get(name, MISSING)
            if isinstance(default, Field):
                item = default
            else:
                if hasattr(default, "__get__"):
                    raise NotImplementedError("descriptor fields are unsupported")
                item = field(default=default)
            item.name = name
            item.type = annotation
            mutable = (
                isinstance(item.default, (list, dict, set))
                or getattr(type(item.default), "__hash__", MISSING) is None
            )
            if item.default is not MISSING and mutable:
                raise ValueError(
                    "mutable default " + str(type(item.default)) + " for field " + name
                    + " is not allowed: use default_factory"
                )
            if item.init:
                has_default = item.default is not MISSING or item.default_factory is not MISSING
                if seen_default is not None and not has_default:
                    raise TypeError(
                        "non-default argument " + builtins_repr(name)
                        + " follows default argument " + builtins_repr(seen_default)
                    )
                if has_default:
                    seen_default = name
            declared[name] = item
        for name, value in namespace.items():
            if isinstance(value, Field) and name not in declared:
                raise TypeError(builtins_repr(name) + " is a field but has no type annotation")
        if frozen:
            for name in ("__setattr__", "__delattr__"):
                if name in namespace:
                    raise TypeError(
                        "Cannot overwrite attribute " + name + " in class " + cls.__name__
                    )
            if not hasattr(object, "__setattr__") or not hasattr(object, "__delattr__"):
                raise NotImplementedError("frozen dataclasses require object attribute mutators")
        for name, item in declared.items():
            if isinstance(namespace.get(name), Field):
                if item.default is MISSING:
                    delattr(cls, name)
                else:
                    setattr(cls, name, item.default)
        cls.__dataclass_fields__ = declared
        initial = [item for item in declared.values() if item.init]
        display_fields = [item for item in declared.values() if item.repr]
        compare_fields = [item for item in declared.values() if item.compare]
        hash_fields = [
            item for item in declared.values()
            if (item.compare if item.hash is None else item.hash)
        ]
        qualified = cls.__qualname__

        def initialize(*args, **kwargs):
            self = args[0]
            positional_count = len(args) - 1
            prefix = qualified + ".__init__()"
            if positional_count > len(initial):
                minimum = 1 + sum(
                    item.default is MISSING and item.default_factory is MISSING
                    for item in initial
                )
                maximum = len(initial) + 1
                counts = (
                    str(maximum) if minimum == maximum
                    else "from " + str(minimum) + " to " + str(maximum)
                )
                raise TypeError(
                    prefix + " takes " + counts + " positional arguments but "
                    + str(len(args)) + " were given"
                )
            values = {}
            for index in range(positional_count):
                value = args[index + 1]
                name = initial[index].name
                if name in kwargs:
                    raise TypeError(
                        prefix + " got multiple values for argument " + builtins_repr(name)
                    )
                values[name] = value
            for name, value in kwargs.items():
                if name not in declared or not declared[name].init:
                    raise TypeError(
                        prefix + " got an unexpected keyword argument " + builtins_repr(name)
                    )
                values[name] = value
            missing = [
                item.name for item in initial
                if item.name not in values and item.default is MISSING
                and item.default_factory is MISSING
            ]
            if missing:
                count = len(missing)
                plural = "s" if count != 1 else ""
                raise TypeError(
                    prefix + " missing " + str(count) + " required positional argument"
                    + plural + ": " + _argument_names(missing)
                )
            for item in declared.values():
                if item.name in values:
                    value = values[item.name]
                elif item.default_factory is not MISSING:
                    value = item.default_factory()
                elif item.init:
                    value = item.default
                else:
                    continue
                if frozen:
                    object.__setattr__(self, item.name, value)
                else:
                    setattr(self, item.name, value)
            if hasattr(self, "__post_init__"):
                self.__post_init__()

        def representation(self):
            key = id(self)
            if key in _repr_running:
                return "..."
            _repr_running.add(key)
            try:
                content = ", ".join(
                    item.name + "=" + builtins_repr(getattr(self, item.name))
                    for item in display_fields
                )
                return type(self).__qualname__ + "(" + content + ")"
            finally:
                _repr_running.remove(key)

        def equal(self, other):
            if type(self) is not type(other):
                return NotImplemented
            return tuple(getattr(self, item.name) for item in compare_fields) == tuple(
                getattr(other, item.name) for item in compare_fields
            )

        def frozen_set(self, name, value):
            if type(self) is cls or name in declared:
                raise FrozenInstanceError("cannot assign to field " + builtins_repr(name))
            object.__setattr__(self, name, value)

        def frozen_delete(self, name):
            if type(self) is cls or name in declared:
                raise FrozenInstanceError("cannot delete field " + builtins_repr(name))
            object.__delattr__(self, name)

        def hashed(self):
            return hash(tuple(getattr(self, item.name) for item in hash_fields))

        if init and "__init__" not in namespace:
            cls.__init__ = initialize
        if repr and "__repr__" not in namespace:
            cls.__repr__ = representation
        if eq and "__eq__" not in namespace:
            cls.__eq__ = equal
        if eq and not explicit_hash:
            cls.__hash__ = hashed if frozen else None
        if frozen:
            cls.__setattr__ = frozen_set
            cls.__delattr__ = frozen_delete
        if match_args and "__match_args__" not in namespace:
            cls.__match_args__ = tuple(item.name for item in initial)
        return cls

    return decorate if cls is None else decorate(cls)


builtins_repr = repr


def fields(class_or_instance):
    """Return the declared Field objects in annotation order."""
    if not is_dataclass(class_or_instance):
        raise TypeError("must be called with a dataclass type or instance")
    return tuple(class_or_instance.__dataclass_fields__.values())


def is_dataclass(obj):
    """Return whether an object is a dataclass class or instance."""
    cls = obj if isinstance(obj, type) else type(obj)
    return hasattr(cls, "__dataclass_fields__")


def replace(obj, **changes):
    """Construct a new instance with selected initialized fields replaced."""
    if isinstance(obj, type) or not is_dataclass(obj):
        raise TypeError("replace() should be called on dataclass instances")
    for item in fields(obj):
        if not item.init:
            if item.name in changes:
                raise ValueError(
                    "field " + item.name
                    + " is declared with init=False, it cannot be specified with replace()"
                )
        elif item.name not in changes:
            changes[item.name] = getattr(obj, item.name)
    return type(obj)(**changes)


def asdict(obj, *, dict_factory=dict):
    """Reject recursive dataclass conversion, which is outside this subset."""
    raise NotImplementedError("asdict is unsupported")


def astuple(obj, *, tuple_factory=tuple):
    """Reject recursive dataclass conversion, which is outside this subset."""
    raise NotImplementedError("astuple is unsupported")


def make_dataclass(*args, **kwargs):
    """Reject dynamic dataclass declarations, which are outside this subset."""
    raise NotImplementedError("make_dataclass is unsupported")
