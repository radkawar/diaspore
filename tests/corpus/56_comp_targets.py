def f():
    l = [None]
    return [1 for (l[0], l) in [[1, 2]]]
try:
    print(f())
except UnboundLocalError:
    print("UnboundLocalError as expected")
row = [0, 0, 0]
result = [row[i] for i in range(3) for row[i] in [i * 10]]
print(result, row)
obj = type("O", (), {})()
print([getattr(obj, "x", None) for obj.x in [1, 2, 3]], obj.x)
