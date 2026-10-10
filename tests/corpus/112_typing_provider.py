"""Python 3.12 type parameter and alias runtime conformance."""

from types import GenericAlias, UnionType
from typing import (
    Callable,
    Generic,
    NewType,
    ParamSpec,
    ParamSpecArgs,
    ParamSpecKwargs,
    Protocol,
    TypeAliasType,
    TypeVar,
    TypeVarTuple,
    Unpack,
    get_args,
    get_origin,
    runtime_checkable,
)

T = TypeVar("T")
U = TypeVar("U", bound="Target", infer_variance=True)
C = TypeVar("C", str, bytes, covariant=True)
D = TypeVar("D", contravariant=True)
print(T, U, C, D)
print(T.__name__, T.__bound__, T.__constraints__, T.__module__)
print(C.__constraints__ == (str, bytes), C.__covariant__, D.__contravariant__)
print(U.__infer_variance__, U.__bound__.__forward_arg__)
print(T != TypeVar("T"), len({T, T, TypeVar("T")}))
print(get_args(T | int) == (T, int), get_args(str | T) == (str, T))

for factory, options in (
    (TypeVar, {"covariant": True, "contravariant": True}),
    (TypeVar, {"covariant": True, "infer_variance": True}),
    (ParamSpec, {"contravariant": True, "infer_variance": True}),
):
    try:
        factory("Bad", **options)
    except ValueError as error:
        print(type(error).__name__, str(error))

try:
    TypeVar("Bad", int)
except TypeError as error:
    print(str(error))
try:
    TypeVar("Bad", int, str, bound=object)
except TypeError as error:
    print(str(error))
try:
    T.__name__ = "other"
except AttributeError:
    print("readonly name")

P = ParamSpec("P")
Q = ParamSpec("Q", infer_variance=True)
print(P, Q, P.__bound__ is type(None))
print(P.args, P.kwargs, P.args.__origin__ is P, P.kwargs.__origin__ is P)
print(P.args == ParamSpecArgs(P), P.kwargs == ParamSpecKwargs(P))
print(P.args != P.kwargs, P.args is P.args)
print(get_origin(P.args) is P, get_origin(P.kwargs) is P)
callable_type = Callable[P, T]
print(get_args(callable_type[[int, str], bool]) == ([int, str], bool))

Ts = TypeVarTuple("Ts")
print(Ts, Ts.__name__, tuple(Ts) == (Unpack[Ts],))


class Box(Generic[T]):
    def __init__(self, value):
        self.value = value


class Variadic(Generic[*Ts]):
    pass


class Callback(Generic[P]):
    pass


print(Box.__parameters__ == (T,), get_origin(Box[int]) is Box)
print(get_args(Box[int]) == (int,), Box[int](42).value)
print(Box[int](42).__orig_class__ == Box[int])
print(get_args(Variadic[int, str]) == (int, str), get_args(Variadic[()]) == ())
print(get_args(Callback[int, str]) == ((int, str),))
print(get_args(Callback[[int, str]]) == ((int, str),))


@runtime_checkable
class HasValue(Protocol):
    value: int


print(isinstance(Box(1), HasValue), isinstance(1, HasValue))
UserId = NewType("UserId", int)
print(UserId(5), UserId.__supertype__ is int)

Alias = TypeAliasType("Alias", list[T], type_params=(T,))
print(Alias, Alias.__name__, Alias.__module__)
print(Alias.__type_params__ == (T,), Alias.__parameters__ == (T,))
print(Alias.__value__ == list[T], type(Alias[int]) is GenericAlias)
print(get_origin(Alias[int]) is Alias, get_args(Alias[int]) == (int,))
print(type(Alias | str) is UnionType, get_args(Alias | str) == (Alias, str))
print(get_args(str | Alias) == (str, Alias), Alias | Alias is Alias)
print(get_args(Alias | None) == (Alias, type(None)))
VariadicAlias = TypeAliasType("VariadicAlias", tuple[*Ts], type_params=(Ts,))
print(VariadicAlias.__type_params__ == (Ts,))
print(VariadicAlias.__parameters__ == (Unpack[Ts],))
try:
    TypeAliasType("Plain", int)[str]
except TypeError as error:
    print(str(error))
try:
    Alias.__value__ = str
except AttributeError:
    print("readonly alias")
