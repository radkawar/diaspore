class Vector:
    dims = 2

    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __repr__(self):
        return f"Vector({self.x}, {self.y})"

    def __eq__(self, other):
        return isinstance(other, Vector) and self.x == other.x and self.y == other.y

    def __add__(self, other):
        return Vector(self.x + other.x, self.y + other.y)

    def __sub__(self, other):
        return Vector(self.x - other.x, self.y - other.y)

    def __mul__(self, k):
        return Vector(self.x * k, self.y * k)

    def __rmul__(self, k):
        return self * k

    def __neg__(self):
        return self * -1

    def length2(self):
        return self.x * self.x + self.y * self.y

    def __lt__(self, other):
        return self.length2() < other.length2()


v = Vector(1, 2)
w = Vector(3, 4)
print(v, w, v + w, w - v, v * 3, 2 * v, -v)
print(v == Vector(1, 2), v == w, v != w, v < w, w > v if False else v.length2())
print(sorted([w, v, Vector(0, 0)]))
print(Vector.dims, v.dims)
v.x = 10
print(v, v.length2())


class Bag:
    def __init__(self):
        self.items = {}

    def __len__(self):
        return len(self.items)

    def __getitem__(self, k):
        return self.items[k]

    def __setitem__(self, k, v):
        self.items[k] = v

    def __delitem__(self, k):
        del self.items[k]

    def __contains__(self, k):
        return k in self.items

    def __iter__(self):
        return iter(sorted(self.items))

    def __bool__(self):
        return len(self.items) > 0

    def __str__(self):
        return f"Bag<{len(self)}>"


b = Bag()
print(len(b), bool(b), "x" in b)
b["x"] = 1
b["a"] = 2
print(len(b), b["x"], "x" in b, bool(b), str(b))
print(list(b), [k for k in b])
del b["x"]
print(len(b), "x" in b)


class Greeter:
    def __init__(self, greeting):
        self.greeting = greeting

    def __call__(self, name):
        return f"{self.greeting}, {name}!"


hello = Greeter("Hello")
print(hello("world"), hello("diaspore"))


class Config:
    def __init__(self, data):
        self.data = data

    def __getattr__(self, name):
        if name in self.data:
            return self.data[name]
        raise AttributeError(f"no config key {name}")


cfg = Config({"host": "localhost", "port": 8080})
print(cfg.host, cfg.port)
print(getattr(cfg, "missing", "fallback"))
print(hasattr(cfg, "host"), hasattr(cfg, "nope"))


class Temperature:
    def __init__(self, celsius):
        self.celsius = celsius

    def fahrenheit(self):
        return self.celsius * 9 / 5 + 32


t = Temperature(100)
print(t.fahrenheit(), Temperature(0).fahrenheit())
m = t.fahrenheit
print(m())
print(isinstance(t, Temperature), isinstance(v, Temperature), isinstance(t, object) if False else type(t) is Temperature)
