def check(v):
    try:
        if v < 0:
            raise ValueError(f"negative: {v}")
        print("ok", v)
    except ValueError as e:
        print("caught", e)
    else:
        print("no error for", v)
    finally:
        print("checked", v)


check(5)
check(-3)

try:
    try:
        raise KeyError("inner")
    except ValueError:
        print("wrong handler")
except KeyError as e:
    print("outer caught", e)

try:
    x = {"a": 1}["b"]
except (IndexError, KeyError) as e:
    print("tuple handler:", repr(e))

try:
    raise RuntimeError("original")
except RuntimeError as e:
    print("first:", e)
    try:
        raise
    except RuntimeError as e2:
        print("re-raised:", e2)


class AppError(Exception):
    pass


class NotFound(AppError):
    def __init__(self, what):
        super().__init__(f"not found: {what}")
        self.what = what


try:
    raise NotFound("user-42")
except AppError as e:
    print("app error:", e, e.what, e.args)

try:
    raise AppError("plain", 2, 3)
except Exception as e:
    print("multi:", e, e.args)

try:
    raise AppError
except AppError as e:
    print("bare class:", repr(e), str(e) == "")


def failing():
    return 1 / 0


def middle():
    return failing()


try:
    middle()
except ZeroDivisionError as e:
    print("through frames:", e)


def cleanup_order():
    out = []
    try:
        out.append("try")
        raise ValueError("x")
    except ValueError:
        out.append("except")
        return out
    finally:
        out.append("finally")


print(cleanup_order())


def finally_wins():
    try:
        return "from try"
    finally:
        print("finally runs")


print(finally_wins())

for i in range(4):
    try:
        if i == 2:
            break
        print("loop", i)
    finally:
        print("iter cleanup", i)
print("after loop")

err = None
try:
    raise TypeError("keep me")
except TypeError as exc:
    err = exc
print("saved:", err, err.args)
try:
    exc
except NameError as e:
    print("handler var deleted:", e)

print(isinstance(ValueError("x"), Exception), isinstance(KeyError("k"), LookupError))
print(issubclass(ZeroDivisionError, ArithmeticError), issubclass(ValueError, KeyError))
print(repr(ValueError("msg")), repr(KeyError("k")), str(KeyError("k")))
