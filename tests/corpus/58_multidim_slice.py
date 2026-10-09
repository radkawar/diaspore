class Matrix:
    def __init__(self, data):
        self.data = data
    def __getitem__(self, key):
        if isinstance(key, tuple):
            return ("tuple-key", [type(k).__name__ for k in key], key)
        return ("single", key)

m = Matrix([[1, 2], [3, 4]])
print(m[1, 0])
print(m[1:2, 3])
print(m[:, 0])
print(m[::2, 1:2:1])
print(m[0])
print(m[1:3])
print(slice(5), slice(1, 5), slice(1, 10, 2))
s = slice(1, 10, 2)
print(s.start, s.stop, s.step)
print(list(range(20)[s]))
print("indices:", slice(1, 10, 2).indices(20))
d = {(1, 2): "a", (slice(1, 2), 3): "weird"}
print(d[1, 2])
