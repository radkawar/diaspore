pairs = [("b", 2), ("a", 3), ("b", 1), ("a", 1), ("c", 0)]
print(sorted(pairs))
print(sorted(pairs, key=lambda p: p[1]))
print(sorted(pairs, key=lambda p: (p[1], p[0])))
print(sorted(pairs, key=lambda p: p[0], reverse=True))

records = [
    {"name": "eve", "age": 31},
    {"name": "adam", "age": 31},
    {"name": "cy", "age": 25},
]
by_age = sorted(records, key=lambda r: r["age"])
print([r["name"] for r in by_age])
by_both = sorted(records, key=lambda r: (r["age"], r["name"]))
print([r["name"] for r in by_both])

nums = [3, 1.5, 2, 0.5, 2.0]
print(sorted(nums), min(nums), max(nums))
print(sorted([True, False, 2, -1]))
print(sorted(["b", "A", "c", "B"]), sorted(["b", "A", "c", "B"], key=lambda c: c.lower()))

stable = [(1, "first"), (0, "x"), (1, "second"), (0, "y"), (1, "third")]
print(sorted(stable, key=lambda p: p[0]))

print(sorted([[2, 1], [1, 99], [1, 2]]))
print(sorted([(1,), (1, 0), (0, 9)]))
print(min([("z", 1), ("a", 9)]), max([2, 3.5, 1]))
print(min("banana"), max("banana"))

class Money:
    def __init__(self, amount):
        self.amount = amount

    def __lt__(self, other):
        return self.amount < other.amount

    def __repr__(self):
        return f"${self.amount}"


wallet = [Money(5), Money(1), Money(20), Money(1)]
print(sorted(wallet), min(wallet), max(wallet))
print(sorted(wallet, reverse=True))
print(sorted(wallet, key=lambda m: -m.amount))

try:
    sorted([1, "one"])
except TypeError as e:
    print("mixed:", e)

xs = [5, 3, 8]
xs.sort()
print(xs)
xs.sort(reverse=True)
print(xs)
ys = ["ccc", "a", "bb"]
ys.sort(key=len)
print(ys)
