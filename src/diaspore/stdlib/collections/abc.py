"""Provide collection ABC names for annotations and explicit inheritance.

Callable and the basic collection protocol names can be imported in type
annotations. This subset has no ABC metaclass protocol: virtual registration,
structural isinstance/issubclass checks, abstract-method enforcement, generic
aliases, and mixin implementations are unsupported. Construction and explicit
registration or subscription requests raise NotImplementedError. A concrete
subclass may supply its own initialization and protocol methods; ordinary
nominal isinstance/issubclass checks then work as for any user-defined class.
"""


class _AnnotationABC:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("collection ABC construction is unsupported")

    @classmethod
    def register(cls, subclass):
        raise NotImplementedError("virtual ABC registration is unsupported")

    @classmethod
    def __class_getitem__(cls, parameters):
        raise NotImplementedError("runtime collection generic aliases are unsupported")

    @classmethod
    def __subclasshook__(cls, subclass):
        raise NotImplementedError("structural ABC checks are unsupported")


class Callable(_AnnotationABC):
    """Name the callable protocol for annotations and explicit inheritance."""

    def __call__(self, *args, **kwargs):
        raise NotImplementedError("Callable requires a concrete __call__")


class Iterable(_AnnotationABC):
    """Name the iterable protocol without structural runtime checks."""

    def __iter__(self):
        raise NotImplementedError("Iterable requires a concrete __iter__")


class Iterator(Iterable):
    """Name the iterator protocol without providing abstract mixins."""

    def __next__(self):
        raise NotImplementedError("Iterator requires a concrete __next__")


class Reversible(Iterable):
    """Name the reversible protocol for annotations."""

    def __reversed__(self):
        raise NotImplementedError("Reversible requires a concrete __reversed__")


class Sized(_AnnotationABC):
    """Name the sized protocol for annotations."""

    def __len__(self):
        raise NotImplementedError("Sized requires a concrete __len__")


class Container(_AnnotationABC):
    """Name the container protocol for annotations."""

    def __contains__(self, value):
        raise NotImplementedError("Container requires a concrete __contains__")


class Hashable(_AnnotationABC):
    """Name the hashable protocol for annotations."""

    def __hash__(self):
        raise NotImplementedError("Hashable requires a concrete __hash__")


class Collection(Sized, Iterable, Container):
    """Name the collection protocol for annotations."""


class Sequence(Collection, Reversible):
    """Name the sequence protocol without providing sequence mixins."""

    def __getitem__(self, index):
        raise NotImplementedError("Sequence requires a concrete __getitem__")


class MutableSequence(Sequence):
    """Name the mutable sequence protocol for annotations."""


class Mapping(Collection):
    """Name the mapping protocol without providing mapping mixins."""

    def __getitem__(self, key):
        raise NotImplementedError("Mapping requires a concrete __getitem__")


class MutableMapping(Mapping):
    """Name the mutable mapping protocol for annotations."""


class Set(Collection):
    """Name the set protocol without providing set mixins."""


class MutableSet(Set):
    """Name the mutable set protocol for annotations."""
