print([x * 2 for x in range(6)])
print([x for x in range(20) if x % 3 == 0 if x % 2 == 0])
print([(a, b) for a in range(3) for b in range(a)])
print([[r * c for c in range(1, 4)] for r in range(1, 4)])
print([c.upper() for c in "hello"])
print(sorted({x % 4 for x in range(10)}))
print({w: len(w) for w in ["ada", "grace", "alan"]})
print({k: v for k, v in [("a", 1), ("b", 2)] if v > 1})
g = (n * n for n in range(5))
print(next(g), next(g), list(g))
print(sum(i * i for i in range(10)))
print(any(c == "z" for c in "xyz"), all(n > 0 for n in [1, 2, 3]))
print(max(len(w) for w in ["hi", "there", "you"]))
matrix = [[1, 2, 3], [4, 5, 6]]
print([v for row in matrix for v in row])
print([v * 10 for row in matrix for v in row if v % 2 == 0])
pairs = [(n, w) for n, w in enumerate("abc")]
print(pairs)
x = "leak-check"
print([x for x in range(3)], x)
outer_var = 5
print([outer_var + i for i in range(3)])
print(list(zip(*[(1, "a"), (2, "b"), (3, "c")])))
words = ["banana", "kiwi", "apple"]
print(sorted(w for w in words if "a" in w))
nested = [y for y in (x * 2 for x in range(4)) if y > 2]
print(nested)
