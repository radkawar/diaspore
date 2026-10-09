data = {"a": 1, "b": 2}
keys = data.keys()
values = data.values()
items = data.items()
print(type(keys).__name__, type(values).__name__, type(items).__name__)
print(keys, values, items)
print(len(keys), len(values), len(items))
data["c"] = 3
data["a"] = 9
print(list(keys), list(values), list(items))
print(list(reversed(keys)), list(reversed(values)), list(reversed(items)))
print("a" in keys, 9 in values, ("a", 9) in items, ("a", 1) in items)
print(keys == {"c", "b", "a"}, keys == ["a", "b", "c"])
print(items == {("a", 9), ("b", 2), ("c", 3)})
print(values == values, values == data.values())
print(sorted(keys - ["b"]), sorted(["x", "a"] - keys))
print(sorted(keys & ["b", "x"]), sorted(["b", "x"] & keys))
print(sorted(keys | ["b", "x"]), sorted(["b", "x"] | keys))
print(sorted(keys ^ ["b", "x"]), sorted(["b", "x"] ^ keys))
print(sorted(items - [("a", 9)]))
print(keys.isdisjoint(["x"]), items.isdisjoint([("a", 1)]))
print(keys <= {"a", "b", "c", "d"}, keys > {"a"})
del data["b"]
print(keys, values, items)
print({"x": [1]}.items() == {"x": [1]}.items())
try:
    hash(keys)
except TypeError as exc:
    print(str(exc))
try:
    hash(items)
except TypeError as exc:
    print(str(exc))
