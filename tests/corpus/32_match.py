def kind(v):
    match v:
        case 0:
            return "zero"
        case 1 | 2 | 3:
            return "small"
        case int() if v < 0:
            return "negative"
        case int():
            return "big int"
        case float():
            return "float"
        case str() if len(v) > 5:
            return "long str"
        case str():
            return "str"
        case []:
            return "empty list"
        case [x]:
            return f"one: {x}"
        case [x, y]:
            return f"pair: {x},{y}"
        case [first, *rest]:
            return f"list head={first} rest={rest}"
        case {"cmd": c, **extra}:
            return f"cmd={c} extra={sorted(extra.items())}"
        case None:
            return "none"
        case _:
            return "other"


for v in [0, 2, -7, 99, 2.5, "hi", "lengthy", [], [10], [1, 2], [1, 2, 3, 4],
          {"cmd": "go", "n": 1}, None, {1, 2}]:
    print(kind(v))


def http_route(request):
    match request:
        case {"method": "GET", "path": "/"}:
            return "index"
        case {"method": "GET", "path": path} if path.startswith("/static/"):
            return f"static:{path[8:]}"
        case {"method": "POST", "path": "/users", "body": {"name": name}}:
            return f"create:{name}"
        case {"method": m, "path": p}:
            return f"404:{m} {p}"
        case _:
            return "bad request"


print(http_route({"method": "GET", "path": "/"}))
print(http_route({"method": "GET", "path": "/static/app.css"}))
print(http_route({"method": "POST", "path": "/users", "body": {"name": "ada"}}))
print(http_route({"method": "DELETE", "path": "/users"}))
print(http_route({"nope": 1}))


class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class Circle:
    def __init__(self, center, r):
        self.center = center
        self.r = r


def describe(shape):
    match shape:
        case Circle(center=Point(x=0, y=0), r=r):
            return f"circle at origin r={r}"
        case Circle(center=Point(x=x, y=y)):
            return f"circle at {x},{y}"
        case Point(x=x, y=y) if x == y:
            return f"diagonal point {x}"
        case Point(x=x):
            return f"point x={x}"
        case _:
            return "unknown"


print(describe(Circle(Point(0, 0), 5)))
print(describe(Circle(Point(1, 2), 3)))
print(describe(Point(4, 4)))
print(describe(Point(7, 1)))
print(describe("nope"))

match (1, 2, 3, 4):
    case (a, *mid, z):
        print(a, mid, z, type(mid) is list)

match "ab":
    case [p, q]:
        print("string matched sequence?!")
    case _:
        print("str is not a sequence pattern")

match {"a": 1, "b": 2}:
    case {"a": got}:
        print("partial mapping ok:", got)

n = 5
match n:
    case big if big > 10:
        print("big")
    case small:
        print("captured:", small, "leaked guard name:", big)

match [1, [2, 3], {"deep": (4, 5)}]:
    case [one, [two, three], {"deep": (four, five)}]:
        print("nested:", one, two, three, four, five)

status = 404
match status:
    case 200 | 201 | 204 as ok:
        print("success", ok)
    case 400 | 404 as err:
        print("client error", err)
    case _:
        print("other")


def missing_attr_fails():
    match Point(1, 2):
        case Circle(r=_):
            return "impossible"
        case Point(z=_):
            return "no z attr"
        case Point(x=x):
            return f"fell through to x={x}"


print(missing_attr_fails())
