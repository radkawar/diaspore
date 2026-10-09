l = list(range(10))
l[2:5] = [90, 91]
print(l)
l[0:0] = [-1, -2]
print(l)
l[-2:] = []
print(l)
l[:] = ["reset"]
print(l)
m = [1, 2, 3, 4, 5, 6]
del m[1:3]
print(m)
del m[::2]
print(m)
n = list("abcdef")
n[1:5] = ["X"]
print(n)
n[len(n):] = ["end"]
print(n)
grid = [[0] * 3 for _ in range(3)]
grid[1][1:] = [7, 8]
print(grid)
stack = [1, 2, 3]
stack[0:1] = stack[0:1] * 3
print(stack)
