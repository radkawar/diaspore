print(issubclass(EOFError, Exception))
try:
    raise EOFError("empty")
except Exception as exc:
    print(type(exc).__name__, str(exc), exc.args)
try:
    input("prompt:")
except EOFError as exc:
    print(type(exc).__name__, str(exc))
try:
    input()
except EOFError:
    print("still at EOF")
