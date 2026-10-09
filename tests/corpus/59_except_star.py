try:
    1 / 0
except* ZeroDivisionError:
    print("caught zde")
else:
    print("unreached")


def handle(group_excs):
    try:
        raise ExceptionGroup("multi", group_excs)
    except* ValueError as eg:
        print("VE:", sorted(str(e) for e in eg.exceptions), eg.message)
    except* (TypeError, KeyError) as eg:
        print("TE/KE:", sorted(type(e).__name__ for e in eg.exceptions))


handle([ValueError("a"), TypeError("t"), KeyError("k"), ValueError("b")])
handle([TypeError("only")])

eg = ExceptionGroup("g", [ValueError(1), OSError(2)])
print(isinstance(eg, ExceptionGroup), isinstance(eg, Exception),
      len(eg.exceptions), eg.message, [type(e).__name__ for e in eg.exceptions])
print(isinstance(BaseExceptionGroup("b", [KeyboardInterrupt()]), BaseException))

try:
    ExceptionGroup("x", [])
except ValueError:
    print("empty rejected")
try:
    ExceptionGroup("x", [1, 2])
except ValueError:
    print("non-exc rejected")

total = []
try:
    raise ExceptionGroup("nums", [ValueError(str(i)) for i in range(3)])
except* ValueError as eg:
    total.extend(int(str(e)) for e in eg.exceptions)
print("sum", sum(total))
print("done")
