"""Portable runtime type parameters and aliases for Python 3.12 typing.

The public constructors are eager. Compiler factories retain annotation-scope
functions and cache their results only after successful evaluation, as required
by PEP 695. Substitution policy remains in the standard library's typing module.
"""

import builtins
import sys


def _caller():
    return sys._getframe(2).f_globals.get("__name__")


def _check_name(name):
    if not isinstance(name, str):
        raise TypeError("name must be a str")


def _check_variance(covariant, contravariant, infer_variance):
    if covariant and contravariant:
        raise ValueError("Bivariant types are not supported.")
    if infer_variance and (covariant or contravariant):
        raise ValueError("Variance cannot be specified with infer_variance.")


class _Identity:
    def __call__(self, value, /):
        return value


# A callable object, like the C builtin, does not bind when assigned to a class.
_idfunc = _Identity()


class _TypeParameter:
    @property
    def __name__(self):
        return self._name

    def __reduce__(self):
        return self.__name__

    def __mro_entries__(self, bases):
        raise TypeError("Cannot subclass an instance of " + type(self).__name__)


class _VariantParameter(_TypeParameter):
    @property
    def __covariant__(self):
        return self._covariant

    @property
    def __contravariant__(self):
        return self._contravariant

    @property
    def __infer_variance__(self):
        return self._infer_variance

    def __repr__(self):
        if self.__infer_variance__:
            return self.__name__
        if self.__covariant__:
            return "+" + self.__name__
        if self.__contravariant__:
            return "-" + self.__name__
        return "~" + self.__name__

    def __or__(self, other):
        from typing import _make_union

        return _make_union(self, other)

    def __ror__(self, other):
        from typing import _make_union

        return _make_union(other, self)


class TypeVar(_VariantParameter):
    """Represent an identity-based type variable with optional restrictions.

    Args:
        name: Display name of the type variable.
        *constraints: Permitted types, with at least two if specified.
        bound: Optional upper bound.
        covariant: Whether the variable is covariant.
        contravariant: Whether the variable is contravariant.
        infer_variance: Whether a type checker should infer variance.
    """

    __module__ = "typing"

    def __init__(self, name, *constraints, bound=None, covariant=False,
                 contravariant=False, infer_variance=False):
        """Initialize an eagerly evaluated type variable."""
        _check_name(name)
        _check_variance(covariant, contravariant, infer_variance)
        if bound is not None:
            from typing import _type_check

            bound = _type_check(bound, "Bound must be a type.")
        if len(constraints) == 1:
            raise TypeError("A single constraint is not allowed")
        if constraints and bound is not None:
            raise TypeError("Constraints cannot be combined with bound=...")
        self._name = name
        self._covariant = bool(covariant)
        self._contravariant = bool(contravariant)
        self._infer_variance = bool(infer_variance)
        self._bound = bound
        self._constraints = constraints
        self._evaluate_bound = None
        self._evaluate_constraints = None
        self.__module__ = _caller()

    def __init_subclass__(cls, **kwargs):
        raise TypeError("type 'typing.TypeVar' is not an acceptable base type")

    @property
    def __bound__(self):
        if self._evaluate_bound is not None:
            self._bound = self._evaluate_bound()
            self._evaluate_bound = None
        return self._bound

    @property
    def __constraints__(self):
        if self._evaluate_constraints is not None:
            self._constraints = self._evaluate_constraints()
            self._evaluate_constraints = None
        return self._constraints

    def __typing_subst__(self, arg):
        from typing import _typevar_subst

        return _typevar_subst(self, arg)


class ParamSpec(_VariantParameter):
    """Represent the parameter list of a callable.

    Args:
        name: Display name of the parameter specification.
        bound: Optional upper bound.
        covariant: Whether the parameter specification is covariant.
        contravariant: Whether it is contravariant.
        infer_variance: Whether a type checker should infer variance.
    """

    __module__ = "typing"

    def __init__(self, name, *, bound=None, covariant=False, contravariant=False,
                 infer_variance=False):
        """Initialize a parameter specification."""
        from typing import _type_check

        _check_name(name)
        _check_variance(covariant, contravariant, infer_variance)
        self._name = name
        self._covariant = bool(covariant)
        self._contravariant = bool(contravariant)
        self._infer_variance = bool(infer_variance)
        self._bound = _type_check(bound, "Bound must be a type.")
        self.__module__ = _caller()

    def __init_subclass__(cls, **kwargs):
        raise TypeError("type 'typing.ParamSpec' is not an acceptable base type")

    @property
    def __bound__(self):
        return self._bound

    @property
    def args(self):
        """Return a positional-argument marker with this specification as origin."""
        return ParamSpecArgs(self)

    @property
    def kwargs(self):
        """Return a keyword-argument marker with this specification as origin."""
        return ParamSpecKwargs(self)

    def __typing_subst__(self, arg):
        from typing import _paramspec_subst

        return _paramspec_subst(self, arg)

    def __typing_prepare_subst__(self, alias, args):
        from typing import _paramspec_prepare_subst

        return _paramspec_prepare_subst(self, alias, args)


class _ParamSpecAttribute:
    __hash__ = None

    def __init__(self, origin):
        self._origin = origin

    @property
    def __origin__(self):
        return self._origin

    def __eq__(self, other):
        if type(self) is not type(other):
            return NotImplemented
        return self.__origin__ == other.__origin__

    def __repr__(self):
        origin = self.__origin__
        name = origin.__name__ if isinstance(origin, ParamSpec) else repr(origin)
        return name + self._suffix

    def __mro_entries__(self, bases):
        raise TypeError("Cannot subclass an instance of " + type(self).__name__)


class ParamSpecArgs(_ParamSpecAttribute):
    """Represent the positional arguments of a parameter specification.

    Args:
        origin: Parameter specification associated with these arguments.
    """

    __module__ = "typing"
    _suffix = ".args"

    def __init_subclass__(cls, **kwargs):
        raise TypeError("type 'typing.ParamSpecArgs' is not an acceptable base type")


class ParamSpecKwargs(_ParamSpecAttribute):
    """Represent the keyword arguments of a parameter specification.

    Args:
        origin: Parameter specification associated with these arguments.
    """

    __module__ = "typing"
    _suffix = ".kwargs"

    def __init_subclass__(cls, **kwargs):
        raise TypeError("type 'typing.ParamSpecKwargs' is not an acceptable base type")


class TypeVarTuple(_TypeParameter):
    """Represent a variadic sequence of type arguments.

    Args:
        name: Display name of the variadic parameter.
    """

    __module__ = "typing"

    def __init__(self, name):
        """Initialize a variadic parameter."""
        _check_name(name)
        self._name = name
        self.__module__ = _caller()

    def __init_subclass__(cls, **kwargs):
        raise TypeError("type 'typing.TypeVarTuple' is not an acceptable base type")

    def __repr__(self):
        return self.__name__

    def __iter__(self):
        from typing import Unpack

        return iter((Unpack[self],))

    def __typing_subst__(self, arg):
        raise TypeError("Substitution of bare TypeVarTuple is not supported")

    def __typing_prepare_subst__(self, alias, args):
        from typing import _typevartuple_prepare_subst

        return _typevartuple_prepare_subst(self, alias, args)


def _unpack_type_params(type_params):
    from typing import Unpack

    return tuple(Unpack[param] if isinstance(param, TypeVarTuple) else param
                 for param in type_params)


class TypeAliasType:
    """Represent a named, optionally generic type alias.

    Args:
        name: Display name of the alias.
        value: Eager alias value.
        type_params: Tuple of parameters declared by the alias.
    """

    __module__ = "typing"

    def __init__(self, name, value, *, type_params=()):
        """Initialize a named alias without interpreting its value."""
        _check_name(name)
        if not isinstance(type_params, tuple):
            raise TypeError("type_params must be a tuple")
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_type_params", type_params)
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_evaluate_value", None)
        object.__setattr__(self, "__module__", _caller())

    def __init_subclass__(cls, **kwargs):
        raise TypeError("type 'typing.TypeAliasType' is not an acceptable base type")

    def __setattr__(self, name, value):
        raise AttributeError("attribute '" + name + "' of 'typing.TypeAliasType' "
                             "objects is not writable")

    def __delattr__(self, name):
        raise AttributeError("attribute '" + name + "' of 'typing.TypeAliasType' "
                             "objects is not writable")

    @property
    def __name__(self):
        return self._name

    @property
    def __type_params__(self):
        return self._type_params

    @property
    def __parameters__(self):
        if not self._type_params:
            return ()
        return _unpack_type_params(self._type_params)

    @property
    def __value__(self):
        if self._evaluate_value is not None:
            object.__setattr__(self, "_value", self._evaluate_value())
            object.__setattr__(self, "_evaluate_value", None)
        return self._value

    def __repr__(self):
        return self.__name__

    def __reduce__(self):
        return self.__name__

    def __getitem__(self, args):
        from types import GenericAlias

        if not self._type_params:
            raise TypeError("Only generic type aliases are subscriptable")
        return GenericAlias(self, args)

    def __or__(self, other):
        return builtins._typing_union(self, other)

    def __ror__(self, other):
        return builtins._typing_union(other, self)


class Generic:
    """Provide runtime parameterization and initialization of generic classes."""

    __module__ = "typing"
    __slots__ = ()

    @classmethod
    def __class_getitem__(cls, params):
        from typing import _generic_class_getitem

        return _generic_class_getitem(cls, params)

    @classmethod
    def __init_subclass__(cls, *args, **kwargs):
        from typing import _generic_init_subclass

        _generic_init_subclass(cls, *args, **kwargs)


def _typevar(name, bound=None, constraints=None, module=None):
    result = TypeVar(name, infer_variance=True)
    result.__module__ = module
    result._evaluate_bound = bound
    result._evaluate_constraints = constraints
    return result


def _paramspec(name, module=None):
    _check_name(name)
    # Intrinsic creation must not import typing to validate a nonexistent bound.
    result = object.__new__(ParamSpec)
    result._name = name
    result._covariant = False
    result._contravariant = False
    result._infer_variance = True
    result._bound = None
    result.__module__ = module
    return result


def _typevartuple(name, module=None):
    result = TypeVarTuple(name)
    result.__module__ = module
    return result


def _typealias(name, type_params, value, module=None):
    result = TypeAliasType(name, None, type_params=type_params)
    object.__setattr__(result, "_evaluate_value", value)
    object.__setattr__(result, "__module__", module)
    return result


def _generic_base(type_params):
    from typing import _GenericAlias

    return _GenericAlias(Generic, _unpack_type_params(type_params))
