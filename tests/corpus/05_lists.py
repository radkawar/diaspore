l = [3, 1, 4, 1, 5, 9, 2, 6]
print(l, len(l), l[0], l[-1], l[2:5], l[::-1], l[::2])
l.append(5)
l.extend([3, 5])
l.insert(0, 0)
print(l)
print(l.pop(), l.pop(0), l)
l.remove(5)
print(l, l.index(4), l.count(1), l.count(5))
c = l.copy()
c.reverse()
print(c, l)
c.sort()
print(c)
c.sort(reverse=True)
print(c)
words = ["banana", "Apple", "cherry"]
words.sort(key=lambda w: w.lower())
print(words)
print(sorted([5, 2, 8, 1]), sorted("cab"), sorted([3, 1, 2], reverse=True))
print([1, 2] + [3], [0] * 4, [[1, 2]] * 2)
print([1, 2, 3] < [1, 2, 4], [1, 2] < [1, 2, 0], [2] > [1, 99])
print(2 in l, 99 not in l)
m = [[i * j for j in range(3)] for i in range(3)]
print(m, m[1][2])
a = [1, 2, 3]
b = a
b.append(4)
print(a, a is b, a == [1, 2, 3, 4])
print(list("abc"), list(range(4)), list((9, 8)))
n = [10, 20, 30]
n[1] = 99
print(n)
del n[0]
print(n)
n.clear()
print(n, bool(n), bool([0]))
print(max([3, 1, 4]), min([3, 1, 4]), sum([1, 2, 3], 10))
