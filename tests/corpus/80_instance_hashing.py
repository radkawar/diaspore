class Key:
    def __init__(self, value):
        self.value = value

    def __hash__(self):
        return 7

    def __eq__(self, other):
        if isinstance(other, Key):
            return self.value == other.value
        return NotImplemented


first = Key("first")
equal = Key("first")
second = Key("second")
print(hash(first), first == equal, first == second)
mapping = {first: 1, second: 2}
print(mapping[equal], len(mapping), equal in mapping)
mapping[equal] = 3
print(mapping[first], len(mapping))
print(mapping.get(Key("second")), mapping.setdefault(equal, 9))
print(mapping.pop(equal), len(mapping))
mapping.update({equal: 4})
print(mapping[first], len(mapping))
del mapping[Key("second")]
print(len(mapping))
keys = {first, equal, second}
print(len(keys), Key("second") in keys)
keys.remove(equal)
print(first in keys, len(keys))
keys.add(equal)
print(len(keys), len(keys | {Key("second")}))
print(len({(first,): 1, (equal,): 2}), len({(first,), (equal,)}))


class Number:
    def __hash__(self):
        return 42

    def __eq__(self, other):
        return other == 42


number = Number()
print({42: "integer"}[number], {number: "object"}[42])
print(len({42, number}), len({number, 42}))


class Unhashable:
    __hash__ = None


class EqualityOnly:
    def __eq__(self, other):
        return self is other


class Inherited(EqualityOnly):
    pass


for obj in [Unhashable(), EqualityOnly(), Inherited()]:
    print(type(obj).__name__, type(obj).__hash__ is None)
    try:
        hash(obj)
    except TypeError as exc:
        print(str(exc))
    try:
        {obj: 1}
    except TypeError as exc:
        print(str(exc))
    try:
        {obj}
    except TypeError as exc:
        print(str(exc))


class Identity(EqualityOnly):
    __hash__ = object.__hash__


identity = Identity()
print(hash(identity) == hash(identity), {identity: 3}[identity])


class BadHash:
    def __hash__(self):
        return "bad"


try:
    hash(BadHash())
except TypeError as exc:
    print(str(exc))


class NegativeHash:
    def __hash__(self):
        return -1


print(hash(NegativeHash()))
