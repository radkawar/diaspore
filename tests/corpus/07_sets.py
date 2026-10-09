s = {3, 1, 4, 1, 5}
print(len(s), 3 in s, 9 in s, 9 not in s)
print(sorted(s))
s.add(9)
s.add(3)
print(sorted(s), len(s))
s.remove(9)
s.discard(100)
print(sorted(s))
a = {1, 2, 3, 4}
b = {3, 4, 5, 6}
print(sorted(a.union(b)), sorted(a.intersection(b)), sorted(a.difference(b)))
print(sorted(a | b), sorted(a & b), sorted(a - b), sorted(a ^ b))
print({1, 2}.issubset(a), a.issuperset({1, 2}), {9}.issubset(a))
print(set(), set("hello") == {"h", "e", "l", "o"}, set([1, 1, 2]))
c = a.copy()
c.clear()
print(c, bool(c), bool({0}))
print(sorted(set(x % 3 for x in range(10))))
frozen = {("a", 1), ("b", 2)}
print(sorted(frozen))
print({1, 2} == {2, 1}, {1} != {1, 2})
total = 0
for x in {10, 20, 30}:
    total += x
print(total)
