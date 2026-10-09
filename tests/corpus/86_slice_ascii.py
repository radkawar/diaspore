value = slice(1, 9, 2)
print(type(value) is slice, isinstance(value, slice), issubclass(slice, object))
print(slice.__name__, value.start, value.stop, value.step)
print([0, 1, 2, 3, 4][slice(1, 4)])
print("plain".encode("ascii"), b"plain".decode("ascii"))
try:
    "caf\u00e9".encode("ascii")
except UnicodeEncodeError:
    print("non-ASCII text")
try:
    b"\xff".decode("ascii")
except UnicodeDecodeError:
    print("non-ASCII bytes")
