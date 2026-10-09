l = list(range(10))
print(l[2:7], l[:4], l[6:], l[:], l[-3:], l[:-3], l[-7:-2])
print(l[::2], l[1::2], l[::3], l[::-1], l[::-2], l[8:2:-2])
print(l[100:], l[:100], l[-100:3], l[5:2], l[2:2])
s = "hello world"
print(s[0:5], s[-5:], s[::-1], s[::2], s[3:100], s[100:200])
t = tuple(range(6))
print(t[1:4], t[::-1], t[::2])
b = b"binary"
print(b[1:4], b[::-1], b[-3:])
r = range(20)
print(r[2:8], list(r[2:8]), r[::5], list(r[::5]))
print(r[-5:], list(r[-5:]), len(r[3:17:2]))
copy = l[:]
copy[0] = 999
print(l[0], copy[0], copy is l)
print("abcdef"[1:-1], "abcdef"[-2:0:-1])
matrix = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
print([row[1:] for row in matrix])
print(matrix[1:][0], matrix[:2])
words = "the quick brown fox jumps".split()
print(words[1:3], words[-2:], " ".join(words[::2]))
print(l[9:0:-3], l[-1:-8:-2], "palindrome"[::-1])
empty = []
print(empty[:], empty[1:5], empty[::-1])
