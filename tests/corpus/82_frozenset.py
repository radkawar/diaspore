empty = frozenset()
values = frozenset([3, 1, 3, 2])
print(repr(empty), bool(empty), len(values), sorted(values))
print(type(values) is frozenset, isinstance(values, frozenset), isinstance(values, set))
print(frozenset(values) is values, values.copy() is values)
print(values == {1, 2, 3}, {1, 2, 3} == values)
print(2 in values, 9 in values, values < {1, 2, 3, 4})
for result in [values | {4}, values & {2, 4}, values - {2}, values ^ {2, 4}]:
    print(type(result).__name__, sorted(result))
print(type({4} | values).__name__)
print(sorted(values.union([4, 5])), sorted(values.intersection([1, 4])))
print(sorted(values.difference([2])), sorted(values.symmetric_difference([3, 4])))
print(values.issubset([1, 2, 3, 4]), values.issuperset([1, 2]), values.isdisjoint([4]))
print(hash(values) == hash(frozenset([2, 3, 1])))
print({values: "present"}[frozenset([1, 2, 3])])
print(len({values, frozenset([3, 2, 1])}))
print(len(frozenset([frozenset([1]), frozenset([1])])))
try:
    values.add(4)
except AttributeError:
    print("immutable")
try:
    frozenset([[1]])
except TypeError as exc:
    print(str(exc))
try:
    frozenset(None)
except TypeError as exc:
    print(str(exc))
