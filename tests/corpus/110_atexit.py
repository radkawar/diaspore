"""Exercise registration, explicit execution, and process-exit callback order."""

import atexit
import socket
import sys

print(socket.AF_INET.name, socket.SOCK_STREAM.name)
print(issubclass(socket.socket, socket.SocketType))
atexit._clear()


def record(label, *, suffix=""):
    print(label + suffix)


print(atexit.register(record, "first") is record)
atexit.register(record, "second", suffix="!")
print(atexit._ncallbacks())
print(atexit._run_exitfuncs(), atexit._ncallbacks())
print(atexit._run_exitfuncs())


def keyword_names(**kwargs):
    print("keyword", kwargs["func"])


atexit.register(keyword_names, func="forwarded")
atexit._run_exitfuncs()


class Callback:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return isinstance(other, Callback) and self.name == other.name

    def __call__(self):
        print(self.name)


atexit.register(Callback("removed"))
atexit.register(Callback("kept"))
atexit.register(Callback("removed"))
print(atexit.unregister(Callback("removed")), atexit._ncallbacks())
atexit._run_exitfuncs()
print(atexit._ncallbacks())
atexit.register(record, "cleared")
print(atexit._clear(), atexit._ncallbacks())
try:
    atexit.register(42)
except TypeError:
    print("callable required")


def failure(exception):
    raise exception


def unraisable(details):
    print("unraisable", details.exc_type.__name__, str(details.exc_value),
          details.err_msg, details.object is failure)


original_hook = sys.unraisablehook
sys.unraisablehook = unraisable
try:
    atexit.register(record, "after failures")
    atexit.register(failure, ValueError("bad callback"))
    atexit.register(failure, SystemExit(5))
    atexit._run_exitfuncs()
finally:
    sys.unraisablehook = original_hook
print("after run", atexit._ncallbacks())


def broken_hook(details):
    print("hook failure", details.exc_type.__name__)
    raise RuntimeError("unraisable hook failed")


sys.unraisablehook = broken_hook
try:
    atexit.register(record, "after hook failure")
    atexit.register(failure, ValueError("reported to broken hook"))
    atexit._run_exitfuncs()
finally:
    sys.unraisablehook = original_hook

atexit.register(record, "exit first", suffix=".")
atexit.register(record, "exit second")


@atexit.register
def decorated():
    print("exit last")


print("main complete")
