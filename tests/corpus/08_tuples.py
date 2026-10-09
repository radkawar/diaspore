t = (3, 1, 4, 1, 5)
print(t, len(t), t[0], t[-1], t[1:3], t[::-1])
print(t.count(1), t.index(4))
print((1,), (), (1, 2) + (3,), (0,) * 3)
print((1, 2) < (1, 3), (1, 2) == (1, 2), (2,) > (1, 99))
a, b = 1, 2
a, b = b, a
print(a, b)
x, y, z = [10, 20, 30]
print(x, y, z)
first, *rest = (1, 2, 3, 4)
print(first, rest)
*init, last = "abcd"
print(init, last)
head, *mid, tail = range(6)
print(head, mid, tail)
(p, q), r = (1, 2), 3
print(p, q, r)
pairs = [(1, "one"), (2, "two")]
for num, word in pairs:
    print(num, word)
d = {(0, 0): "origin", (1, 0): "east"}
print(d[(0, 0)], d[1, 0])
print(tuple("abc"), tuple([1, 2]), tuple(range(3)))
nested = ((1, 2), (3, (4, 5)))
print(nested[1][1][0], nested)
def multi():
    return 1, "two", [3]
m = multi()
print(m, type(m) is tuple)
one, two, three = multi()
print(one, two, three)
