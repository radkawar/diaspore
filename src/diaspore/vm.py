"""Execute portable Diaspore bundles using the Python host.

Host operators and allowlisted methods implement primitive operations.
Sandbox values, capabilities, and control flow remain interpreter-managed.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import json as _hostjson
import math as _hostmath
import operator as _op
import os
import queue
import random as _hostrandom
import socket as _socket
import sys as _hostsys
import threading
import time as _hosttime
import weakref


class BreakSig(Exception):
    """Signal a sandbox loop break without exposing it as an exception."""

    pass


class ContinueSig(Exception):
    """Signal a sandbox loop continuation."""

    pass


class ReturnSig(Exception):
    """Carry a sandbox function's return value."""

    def __init__(self, value):
        """Store the returned value."""
        self.value = value


class DiasporeError(Exception):
    """Carry a sandbox exception instance and its interpreter traceback."""

    def __init__(self, value, tb=None):
        """Store the exception value and optional traceback."""
        self.value = value
        self.tb = tb or []


class DiasporeTrap(Exception):
    """Abort execution without allowing sandbox exception handlers to catch it."""

    def __init__(self, msg, code=3):
        """Initialize an execution trap and process exit code."""
        super().__init__(msg)
        self.code = code


class DiasporeException(Exception):
    """Expose an uncaught sandbox exception to the embedding host."""

    def __init__(self, type_name: str, message: str):
        """Store the sandbox exception type and message."""
        self.type_name = type_name
        self.message = message
        super().__init__(f"{type_name}: {message}" if message else type_name)


class SuspendSig(Exception):
    """Suspend a durable run without executing sandbox cleanup blocks."""

    def __init__(self, payload):
        """Store the suspension payload."""
        self.payload = payload



_OBJECT = None  # Bootstrap exception classes before the universal base exists.


class DiasporeClass:
    """Represent a sandbox class with C3 method resolution."""

    def __init__(self, name, bases, ns, prim=None):
        """Initialize class bases, namespace, and optional primitive backing."""
        self.name = name
        # Primitive bases supply backing values without entering the sandbox MRO.
        self.bases = [b for b in bases if isinstance(b, DiasporeClass)]
        self.prim = prim or next(
            (b.prim for b in self.bases if b.prim), None)
        for b in bases:
            if not isinstance(b, DiasporeClass):
                self.prim = b
        self.ns = ns
        self.mro = c3_mro(self)
        if _OBJECT is not None and _OBJECT not in self.mro:
            self.mro.append(_OBJECT)

    def lookup(self, name):
        """Look up an attribute through the class method resolution order."""
        for c in self.mro:
            if name in c.ns:
                return c.ns[name]
        return _MISSING

    def issub(self, other):
        """Return whether this class inherits from the given class."""
        return other in self.mro


_MISSING = object()


def c3_mro(cls):
    """Compute a class's C3 method resolution order."""
    seqs = [[cls]] + [list(b.mro) for b in cls.bases] + [list(cls.bases)]
    out = []
    while any(seqs):
        for seq in seqs:
            head = seq[0]
            if not any(head in s[1:] for s in seqs if s):
                break
        else:
            raise DiasporeError(make_exc("TypeError", "cannot create a consistent MRO"))
        out.append(head)
        for seq in seqs:
            if seq and seq[0] is head:
                del seq[0]
        seqs = [s for s in seqs if s]
    return out


class DiasporeInstance:
    """Store a sandbox instance's class, attributes, and primitive backing."""

    __slots__ = ("cls", "d", "prim")

    def __init__(self, cls):
        """Initialize an instance with an empty attribute dictionary."""
        self.cls = cls
        self.prim = None
        self.d = {}


class DiasporeFunction:
    """Represent a compiled function with its defining scope and defaults."""

    def __init__(self, interp, node, closure, module):
        """Capture a function's interpreter, IR, closure, and module."""
        self.interp = interp
        self.node = node
        self.name = node["name"]
        p = node["params"]
        self.params = {"args": p.get("args", []), "defaults": p.get("defaults", []),
                       "vararg": p.get("vararg"), "kwonly": p.get("kwonly", []),
                       "kwdefaults": p.get("kwdefaults", []), "kwarg": p.get("kwarg")}
        self.localset = frozenset(node.get("locals", ()))
        self.gdecl = frozenset(node.get("gdecl", ()))
        self.ndecl = frozenset(node.get("ndecl", ()))
        self.gen = node.get("gen", False)
        self.closure = closure
        self.module = module
        self.defclass = None
        self.defaults = None      # filled at definition time
        self.kwdefaults = None
        self.attrs = {}

    def __call__(self, *args, **kwargs):
        """Invoke the sandbox function from host code."""
        return self.interp.call(self, list(args), dict(kwargs))


class BoundMethod:
    """Pair a sandbox function with its bound receiver."""

    __slots__ = ("fn", "obj")

    def __init__(self, fn, obj):
        """Store the function and receiver."""
        self.fn = fn
        self.obj = obj

    def __call__(self, *args, **kwargs):
        """Invoke the method through its interpreter."""
        return self.fn.interp.call(self, list(args), dict(kwargs))


class Property:
    """Represent a sandbox property descriptor."""

    __slots__ = ("fget", "fset", "fdel", "doc")

    def __init__(self, fget=None, fset=None, fdel=None, doc=None):
        """Store property accessors and documentation."""
        self.fget = fget
        self.fset = fset
        self.fdel = fdel
        self.doc = doc


class StaticMethod:
    """Wrap a function that does not bind a receiver."""

    __slots__ = ("fn",)

    def __init__(self, fn):
        """Store the wrapped function."""
        self.fn = fn


class ClassMethod:
    """Wrap a function that binds the owning class."""

    __slots__ = ("fn",)

    def __init__(self, fn):
        """Store the wrapped function."""
        self.fn = fn


class SuperObject:
    """Represent a superclass lookup bound to a class and receiver."""

    __slots__ = ("cls", "obj")

    def __init__(self, cls, obj):
        """Store the starting class and receiver."""
        self.cls = cls
        self.obj = obj


class DiasporeModule:
    """Represent a compiled module and its global frame."""

    def __init__(self, name, frame):
        """Store the module name and global frame."""
        self.name = name
        self.frame = frame


class NativeModule:
    """Expose a named host-provided namespace to the sandbox."""

    def __init__(self, name, ns):
        """Store the module name and exported namespace."""
        self.name = name
        self.ns = ns


class _CodeObject:
    """Store compiled nodes for sandbox compile, exec, and eval."""

    __slots__ = ("nodes", "mode", "filename")

    def __init__(self, nodes, mode, filename):
        """Store the compiled nodes, execution mode, and source filename."""
        self.nodes = nodes
        self.mode = mode
        self.filename = filename


def _scope_module():
    from .compiler import _Scope
    return _Scope("module", set(), set(), set())


class DiasporeFile:
    """Wrap a host file with a restricted sandbox method surface."""

    METHODS = {"read", "readline", "write", "close"}

    def __init__(self, f):
        """Store the host file."""
        self.f = f


class NativeObject:
    """Expose a host object through explicit method and property tables."""

    __slots__ = ("kind", "methods", "props")

    def __init__(self, kind: str, methods: dict, props: dict | None = None):
        """Store the object kind and its exposed members."""
        self.kind = kind
        self.methods = methods
        self.props = props or {}


class Frame:
    """Track bindings, lexical scope, and execution context."""

    __slots__ = ("vars", "parent", "localset", "gdecl", "ndecl", "is_class",
                 "globals", "gen", "line", "fname", "module_name", "defclass", "self_")

    def __init__(self, vars, parent, localset, gdecl, ndecl, is_class,
                 globals_, fname, module_name):
        """Initialize bindings, scope declarations, and source context."""
        self.vars = vars
        self.parent = parent
        self.localset = localset      # None => non-strict scope (module/class)
        self.gdecl = gdecl
        self.ndecl = ndecl
        self.is_class = is_class
        self.globals = globals_
        self.gen = None
        self.line = 0
        self.fname = fname
        self.module_name = module_name
        self.defclass = None
        self.self_ = None


class _StopIter(StopIteration):
    """Carry a generator return value using host StopIteration conventions."""

    def __init__(self, value):
        """Store the return value using host exception argument conventions."""
        if value is None:
            super().__init__()
        else:
            super().__init__(value)
        self.value = value


class _GenClose(Exception):
    """Unwind an abandoned generator while running its cleanup blocks."""


class _GenChannel:
    """Exchange generator values without retaining the generator object.

    Collection of the generator must be able to stop its waiting thread.
    """

    __slots__ = ("in_q", "out_q", "state")

    def __init__(self):
        """Create bounded channels and shared generator lifecycle state."""
        self.in_q = queue.Queue(1)
        self.out_q = queue.Queue(1)
        self.state = {"started": False, "done": False, "closing": False}

    def _yield(self, value):
        self.out_q.put(("yield", value))
        token = self.in_q.get()
        if token == "close":
            self.state["closing"] = True
            raise _GenClose()
        kind, payload = token
        if kind == "throw":
            raise DiasporeError(payload)
        if kind == "genexit":
            raise DiasporeError(payload)
        return payload  # value from send() / None from next()


def _gen_body(interp, body, frame, chan: _GenChannel):
    if chan.in_q.get() == "close":
        return
    try:
        interp.exec_block(body, frame)
        chan.out_q.put(("return", None))
    except ReturnSig as r:
        chan.out_q.put(("return", r.value))
    except _GenClose:
        pass
    except BaseException as e:
        if chan.state["closing"]:
            return  # no consumer; discard errors raised while unwinding
        chan.out_q.put(("exc", e))


class DiasporeGenerator:
    """Run a sandbox generator through a synchronized host thread."""

    def __init__(self, interp, fn, frame):
        """Create the generator channel, thread, and abandonment finalizer."""
        self.fn = fn
        self.chan = _GenChannel()
        frame.gen = self.chan
        self._thread = threading.Thread(
            target=_gen_body, args=(interp, fn.node["body"], frame, self.chan),
            daemon=True)
        # Collection must wake the thread so abandoned generators do not leak.
        weakref.finalize(self, _close_generator, self.chan)

    def __iter__(self):
        """Return this generator as its own iterator."""
        return self

    def __next__(self):
        """Advance to the next yielded value."""
        return self._advance(("send", None))

    def send(self, value):
        """Resume the generator with a value."""
        st = self.chan.state
        if not st["started"] and value is not None:
            raise DiasporeError(make_exc(
                "TypeError",
                "can't send non-None value to a just-started generator"))
        return self._advance(("send", value))

    def throw(self, exc):
        """Raise a sandbox exception at the suspended yield."""
        return self._advance(("throw", exc))

    def close(self):
        """Close the generator and run its cleanup blocks."""
        st = self.chan.state
        if st["done"] or not st["started"]:
            st["done"] = True
            return None
        ge = make_exc("GeneratorExit")
        try:
            self._advance(("genexit", ge))
        except StopIteration:
            return None
        except DiasporeError as e:
            if e.value is ge or self_exc_isa(e.value, "GeneratorExit"):
                return None
            raise
        raise DiasporeError(make_exc("RuntimeError", "generator ignored GeneratorExit"))

    def _advance(self, token):
        st = self.chan.state
        if st["done"]:
            raise StopIteration
        if not st["started"]:
            st["started"] = True
            self._thread.start()
        self.chan.in_q.put(token)
        kind, v = self.chan.out_q.get()
        if kind == "yield":
            return v
        st["done"] = True
        if kind == "return":
            raise _StopIter(v)
        raise v


def self_exc_isa(v, name: str) -> bool:
    """Return whether a value is an instance of a named sandbox exception."""
    return isinstance(v, DiasporeInstance) and v.cls.issub(EXC_CLASSES[name])


def _close_generator(chan: _GenChannel):
    if chan.state["started"] and not chan.state["done"]:
        try:
            chan.in_q.put_nowait("close")
        except queue.Full:
            pass


class _InstanceIter:
    """Adapt a sandbox object's iteration protocol to a host iterator."""

    def __init__(self, interp, obj):
        """Store the interpreter and sandbox iterator."""
        self.interp = interp
        self.obj = obj

    def __iter__(self):
        """Return this adapter as its own iterator."""
        return self

    def __next__(self):
        """Advance the sandbox iterator and translate iteration completion."""
        try:
            return self.interp.call_dunder(self.obj, "__next__", [])
        except DiasporeError as e:
            if (isinstance(e.value, DiasporeInstance)
                    and self.interp.exc_isa(e.value, "StopIteration")):
                args = e.value.d.get("args", ())
                raise (StopIteration(args[0]) if args else StopIteration()) from None
            raise



EXC_TREE = [
    ("BaseException", None),
    ("SystemExit", "BaseException"),
    ("KeyboardInterrupt", "BaseException"),
    ("GeneratorExit", "BaseException"),
    ("BaseExceptionGroup", "BaseException"),
    ("Exception", "BaseException"),
    ("ArithmeticError", "Exception"),
    ("ZeroDivisionError", "ArithmeticError"),
    ("OverflowError", "ArithmeticError"),
    ("AssertionError", "Exception"),
    ("AttributeError", "Exception"),
    ("ImportError", "Exception"),
    ("ModuleNotFoundError", "ImportError"),
    ("LookupError", "Exception"),
    ("IndexError", "LookupError"),
    ("KeyError", "LookupError"),
    ("NameError", "Exception"),
    ("UnboundLocalError", "NameError"),
    ("OSError", "Exception"),
    ("PermissionError", "OSError"),
    ("RuntimeError", "Exception"),
    ("NotImplementedError", "RuntimeError"),
    ("RecursionError", "RuntimeError"),
    ("StopIteration", "Exception"),
    ("StopAsyncIteration", "Exception"),
    ("TypeError", "Exception"),
    ("ValueError", "Exception"),
    ("SyntaxError", "Exception"),
    ("IndentationError", "SyntaxError"),
    ("TabError", "IndentationError"),
    ("SystemError", "Exception"),
    ("ReferenceError", "Exception"),
    ("BufferError", "Exception"),
    ("MemoryError", "Exception"),
    ("FloatingPointError", "ArithmeticError"),
    ("UnicodeError", "ValueError"),
    ("UnicodeDecodeError", "UnicodeError"),
    ("UnicodeEncodeError", "UnicodeError"),
    ("BlockingIOError", "OSError"),
    ("ChildProcessError", "OSError"),
    ("ConnectionError", "OSError"),
    ("BrokenPipeError", "ConnectionError"),
    ("ConnectionResetError", "ConnectionError"),
    ("ConnectionAbortedError", "ConnectionError"),
    ("ConnectionRefusedError", "ConnectionError"),
    ("FileExistsError", "OSError"),
    ("FileNotFoundError", "OSError"),
    ("InterruptedError", "OSError"),
    ("IsADirectoryError", "OSError"),
    ("NotADirectoryError", "OSError"),
    ("ProcessLookupError", "OSError"),
    ("TimeoutError", "OSError"),
]

EXC_CLASSES: dict[str, DiasporeClass] = {}


def _exc_init(self, *args):
    self.d["args"] = tuple(args)


OBJECT = DiasporeClass("object", [], {})
_OBJECT = OBJECT


def _build_exc_classes():
    for name, parent in EXC_TREE:
        bases = [EXC_CLASSES[parent]] if parent else []
        cls = DiasporeClass(name, bases, {})
        EXC_CLASSES[name] = cls
    EXC_CLASSES["BaseException"].ns["__init__"] = _exc_init


_build_exc_classes()
EXC_CLASSES["ExceptionGroup"] = DiasporeClass(
    "ExceptionGroup",
    [EXC_CLASSES["BaseExceptionGroup"], EXC_CLASSES["Exception"]], {})
BASE_EXC = EXC_CLASSES["BaseException"]


def make_group(message, excs, base=False):
    """Create an exception group with the appropriate sandbox base class."""
    name = "BaseExceptionGroup" if base and any(
        not e.cls.issub(EXC_CLASSES["Exception"]) for e in excs) else "ExceptionGroup"
    inst = DiasporeInstance(EXC_CLASSES[name])
    inst.d["args"] = (message, list(excs))
    inst.d["message"] = message
    inst.d["exceptions"] = tuple(excs)
    return inst


def _group_init(self, *args):
    if len(args) != 2:
        raise DiasporeError(make_exc(
            "TypeError", "BaseExceptionGroup() takes exactly 2 arguments"))
    message, excs = args
    if not isinstance(message, str):
        raise DiasporeError(make_exc("TypeError", "argument 1 must be str, not "
                                 + type_name(message)))
    try:
        items = list(excs)
    except TypeError:
        raise DiasporeError(make_exc(
            "TypeError", "second argument (exceptions) must be a sequence")) from None
    if not items:
        raise DiasporeError(make_exc(
            "ValueError", "second argument (exceptions) must be a non-empty sequence"))
    for e in items:
        if not (isinstance(e, DiasporeInstance) and e.cls.issub(EXC_CLASSES["BaseException"])):
            raise DiasporeError(make_exc(
                "ValueError",
                "second argument (exceptions) must be a sequence of exceptions"))
    self.d["args"] = (message, items)
    self.d["message"] = message
    self.d["exceptions"] = tuple(items)


EXC_CLASSES["BaseExceptionGroup"].ns["__init__"] = _group_init


def make_exc(name, *args):
    """Create a named sandbox exception with positional arguments."""
    inst = DiasporeInstance(EXC_CLASSES[name])
    inst.d["args"] = tuple(args)
    return inst


def host_exc_to_diaspore(e: BaseException) -> DiasporeError:
    """Translate a host exception into the sandbox exception hierarchy."""
    if isinstance(e, OSError):
        return DiasporeError(make_exc(_map_exc_name(type(e)), str(e)))
    return DiasporeError(make_exc(_map_exc_name(type(e)), *e.args))


def _map_exc_name(t: type) -> str:
    for c in t.__mro__:
        if c.__name__ in EXC_CLASSES:
            return c.__name__
    return "RuntimeError"



STR_METHODS = {"upper", "lower", "title", "capitalize", "casefold", "strip", "lstrip",
               "rstrip", "split", "rsplit", "splitlines", "join", "replace", "find",
               "rfind", "index", "count", "startswith", "endswith", "isdigit",
               "isalpha", "isalnum", "isspace", "zfill", "ljust", "rjust", "encode",
               "partition", "rpartition", "removeprefix", "removesuffix", "center",
               "islower", "isupper", "swapcase"}
LIST_METHODS = {"append", "extend", "insert", "pop", "remove", "clear", "index",
                "count", "reverse", "sort", "copy"}
DICT_METHODS = {"get", "keys", "values", "items", "pop", "update", "setdefault",
                "clear", "copy"}
SET_METHODS = {"add", "remove", "discard", "clear", "copy", "union", "intersection",
               "difference", "symmetric_difference", "issubset", "issuperset"}
TUPLE_METHODS = {"count", "index"}
BYTES_METHODS = {"decode", "find", "split", "strip", "lstrip", "rstrip", "replace",
                 "startswith", "endswith", "upper", "lower"}

_BINOPS = {
    "add": _op.add, "sub": _op.sub, "mul": _op.mul, "div": _op.truediv,
    "floordiv": _op.floordiv, "mod": _op.mod, "pow": _op.pow,
    "lshift": _op.lshift, "rshift": _op.rshift,
    "bitand": _op.and_, "bitor": _op.or_, "bitxor": _op.xor,
    "matmul": _op.matmul,
}
_IBINOPS = {
    "add": _op.iadd, "sub": _op.isub, "mul": _op.imul, "div": _op.itruediv,
    "floordiv": _op.ifloordiv, "mod": _op.imod, "pow": _op.ipow,
    "lshift": _op.ilshift, "rshift": _op.irshift,
    "bitand": _op.iand, "bitor": _op.ior, "bitxor": _op.ixor,
    "matmul": _op.imatmul,
}
_DUNDER_BIN = {
    "add": ("__add__", "__radd__"), "sub": ("__sub__", "__rsub__"),
    "mul": ("__mul__", "__rmul__"), "div": ("__truediv__", "__rtruediv__"),
    "matmul": ("__matmul__", "__rmatmul__"),
    "floordiv": ("__floordiv__", "__rfloordiv__"), "mod": ("__mod__", "__rmod__"),
    "pow": ("__pow__", "__rpow__"), "lshift": ("__lshift__", "__rlshift__"),
    "rshift": ("__rshift__", "__rrshift__"), "bitand": ("__and__", "__rand__"),
    "bitor": ("__or__", "__ror__"), "bitxor": ("__xor__", "__rxor__"),
    "divmod": ("__divmod__", "__rdivmod__"),
}
_CMPOPS = {"eq": _op.eq, "ne": _op.ne, "lt": _op.lt, "le": _op.le,
           "gt": _op.gt, "ge": _op.ge}
_CMP_DUNDER = {"eq": "__eq__", "ne": "__ne__", "lt": "__lt__", "le": "__le__",
               "gt": "__gt__", "ge": "__ge__"}
_CMP_SYM = {"lt": "<", "le": "<=", "gt": ">", "ge": ">="}


class BuiltinType:
    """Represent a primitive type as a first-class sandbox value."""

    def __init__(self, name, host_types, ctor):
        """Store the type name, matching host types, and constructor."""
        self.name = name
        self.host_types = host_types
        self.ctor = ctor

    def __repr__(self):
        """Return the Python-style type representation."""
        return f"<class '{self.name}'>"


SUPER_MARKER = object()


def type_name(v) -> str:
    """Return the sandbox-visible type name of a value."""
    if isinstance(v, DiasporeInstance):
        return v.cls.name
    if isinstance(v, DiasporeClass) or isinstance(v, BuiltinType):
        return "type"
    if isinstance(v, DiasporeFunction):
        return "function"
    if isinstance(v, BoundMethod):
        return "method"
    if isinstance(v, DiasporeGenerator):
        return "generator"
    if isinstance(v, DiasporeModule) or isinstance(v, NativeModule):
        return "module"
    if isinstance(v, DiasporeFile):
        return "file"
    if isinstance(v, NativeObject):
        return v.kind
    if v is None:
        return "NoneType"
    return type(v).__name__



class Interp:
    """Execute bundle IR with explicit capabilities and resource budgets."""

    def __init__(self, bundle, argv=None, bindings=None, caps=None,
                 max_steps=None, max_depth=1000, stdout=None, stderr=None,
                 durable=False, journal=None, deterministic=False, policy=None):
        """Initialize execution state, host bindings, and optional replay."""
        if bundle.get("diaspore") != 1:
            raise ValueError("not a diaspore v1 bundle")
        self.bundle = bundle
        self.argv = list(argv) if argv else [bundle["main"]]
        self.caps = set(caps or ())
        self.policy = dict(policy or {})
        self.durable = durable
        self.deterministic = deterministic
        if durable and self.caps:
            raise ValueError(
                "durable mode forbids capabilities; route I/O through host bindings")
        if deterministic and self.caps:
            raise ValueError("deterministic mode forbids capabilities")
        self.journal: list[dict] = list(journal) if journal else []
        self.replay_cursor = 0
        self.replaying = bool(journal)
        self.bindings = {
            name: {f: self.journaled(name, f, fn) if callable(fn) else fn
                   for f, fn in fns.items()}
            for name, fns in dict(bindings or {}).items()
        }
        self.max_steps = max_steps
        self.max_depth = max_depth
        self.steps = 0
        self.depth = 0
        self.stdout = stdout or _hostsys.stdout
        self.stderr = stderr or _hostsys.stderr
        self.modules: dict[str, object] = {}
        self.exc_stack: list[DiasporeInstance] = []
        self._repring: set[int] = set()
        self.builtins = make_builtins(self)
        self.natives = make_native_modules(self)
        _hostsys.setrecursionlimit(max(200000, _hostsys.getrecursionlimit()))


    def replay_next(self, mod: str, fname: str):
        """Consume a replay result, returning whether an entry was available."""
        if not self.replaying:
            return False, None
        entry = self.journal[self.replay_cursor]
        if entry["m"] != mod or entry["f"] != fname:
            raise DiasporeTrap(
                f"diaspore: journal divergence at entry {self.replay_cursor} "
                f"(recorded {entry['m']}.{entry['f']}, replay reached {mod}.{fname}); "
                f"durable programs must be deterministic", code=5)
        self.replay_cursor += 1
        if self.replay_cursor >= len(self.journal):
            self.replaying = False
        return True, entry["r"]

    def journal_value(self, v, what: str):
        """Normalize a journal value to canonical JSON types for stable replay."""
        if v is None or isinstance(v, (str, bool)):
            return v
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
        if isinstance(v, list):
            return [self.journal_value(x, what) for x in v]
        if isinstance(v, dict):
            for k in v:
                if not isinstance(k, str):
                    raise DiasporeError(make_exc(
                        "TypeError", f"durable {what} results must have str keys"))
            return {k: self.journal_value(x, what) for k, x in v.items()}
        raise DiasporeError(make_exc(
            "TypeError",
            f"durable {what} results must be JSON-serializable "
            f"(got {type_name(v)}); tuples are not allowed; return lists"))

    def effect(self, mod: str, fname: str, fn, *args):
        """Replay or execute an effect and record its result."""
        replayed, r = self.replay_next(mod, fname)
        if replayed:
            return r
        r = fn(*args)
        self.journal.append({"m": mod, "f": fname,
                             "r": self.journal_value(r, f"{mod}.{fname}")})
        return r

    def journaled(self, mod: str, fname: str, fn):
        """Wrap a callable with effect journaling when durability is enabled."""
        if not self.durable:
            return fn
        def wrapper(*args):
            return self.effect(mod, fname, fn, *args)
        return wrapper


    def fuel(self):
        """Charge one execution step and enforce the step budget."""
        self.steps += 1
        if self.max_steps is not None and self.steps > self.max_steps:
            raise DiasporeTrap(f"diaspore: step budget exceeded ({self.max_steps})")

    def fuel_n(self, n: int):
        """Charge bulk host work so iteration cannot bypass the step budget."""
        self.steps += n
        if self.max_steps is not None and self.steps > self.max_steps:
            raise DiasporeTrap(f"diaspore: step budget exceeded ({self.max_steps})")


    def run(self) -> int:
        """Execute the entry module and return its process exit code."""
        try:
            self.import_module(self.bundle["main"])
            self.stdout.flush()
            return 0
        except DiasporeError as e:
            self.stdout.flush()
            if self.exc_isa(e.value, "SystemExit"):
                args = e.value.d.get("args", ())
                code = args[0] if args else 0
                if code is None:
                    return 0
                if isinstance(code, int):
                    return code
                self.stderr.write(self.diaspore_str(code) + "\n")
                return 1
            self.print_traceback(e)
            return 1
        except DiasporeTrap as t:
            self.stdout.flush()
            self.stderr.write(str(t) + "\n")
            return t.code
        except RecursionError:
            self.stdout.flush()
            self.stderr.write("RecursionError: maximum recursion depth exceeded\n")
            return 1
        except KeyboardInterrupt:
            self.stdout.flush()
            return 130

    def print_traceback(self, e: DiasporeError):
        """Write a sandbox exception traceback to standard error."""
        w = self.stderr.write
        w("Traceback (most recent call last):\n")
        for mod, fname, line in reversed(e.tb):
            m = self.bundle["modules"].get(mod, {})
            w(f'  File "{m.get("file", mod)}", line {line}, in {fname}\n')
            src = m.get("src")
            if src and 1 <= line <= len(src):
                w(f"    {src[line - 1].strip()}\n")
        v = e.value
        name = v.cls.name if isinstance(v, DiasporeInstance) else type_name(v)
        msg = self.exc_str(v) if isinstance(v, DiasporeInstance) else self.diaspore_str(v)
        w(f"{name}: {msg}\n" if msg else f"{name}\n")


    def import_module(self, name):
        """Load and cache a compiled or native module."""
        if name in self.modules:
            return self.modules[name]
        if name in self.bundle["modules"]:
            parent = None
            if "." in name:  # packages: ancestors execute first
                parent = self.import_module(name.rsplit(".", 1)[0])
            m = self.bundle["modules"][name]
            frame = Frame({}, None, None, frozenset(), frozenset(), False,
                          None, "<module>", name)
            frame.globals = frame.vars
            frame.vars["__name__"] = "__main__" if name == self.bundle["main"] else name
            mod = DiasporeModule(name, frame)
            self.modules[name] = mod
            try:
                self.exec_block(m["body"], frame)
            except DiasporeError as e:
                e.tb.append((name, "<module>", frame.line))
                raise
            if isinstance(parent, DiasporeModule):
                parent.frame.vars[name.rsplit(".", 1)[1]] = mod
            return mod
        if name in self.bindings:
            mod = NativeModule(name, dict(self.bindings[name]))
            self.modules[name] = mod
            return mod
        if name in self.natives:
            mod = self.natives[name]
            self.modules[name] = mod
            return mod
        raise DiasporeError(make_exc("ModuleNotFoundError", f"No module named '{name}'"))


    def load_name(self, fr: Frame, id_: str):
        """Resolve a name according to lexical and declaration scope."""
        if id_ in fr.gdecl:
            if id_ in fr.globals:
                return fr.globals[id_]
        elif fr.localset is not None and id_ in fr.localset:
            if id_ in fr.vars:
                return fr.vars[id_]
            raise DiasporeError(make_exc(
                "UnboundLocalError",
                f"cannot access local variable '{id_}' where it is not associated with a value"))
        else:
            f = fr
            while f is not None:
                if id_ in f.vars:
                    return f.vars[id_]
                f = f.parent
            if fr.globals is not None and id_ in fr.globals:
                return fr.globals[id_]
        if id_ in self.builtins:
            return self.builtins[id_]
        raise DiasporeError(make_exc("NameError", f"name '{id_}' is not defined"))

    def store_name(self, fr: Frame, id_: str, v):
        """Store a name in its declared scope."""
        if id_ in fr.gdecl:
            fr.globals[id_] = v
        elif id_ in fr.ndecl:
            f = fr.parent
            while f is not None:
                if f.localset is not None and id_ in f.localset:
                    f.vars[id_] = v
                    return
                f = f.parent
            raise DiasporeError(make_exc("RuntimeError", f"nonlocal binding for '{id_}' not found"))
        else:
            fr.vars[id_] = v

    def tb_entry(self, fr: Frame):
        """Return a traceback entry for the current frame."""
        return [(fr.fname if fr.module_name == "?" else fr.module_name, fr.fname, fr.line)]


    def exec_block(self, stmts, fr: Frame):
        """Execute a statement block while charging fuel and tracking lines."""
        for s in stmts:
            self.fuel()
            fr.line = s.get("ln", fr.line)
            self.STMTS[s["op"]](self, s, fr)

    def ex_expr(self, s, fr):
        """Execute an expression statement."""
        self.ev(s["v"], fr)

    def ex_assign(self, s, fr):
        """Evaluate an assignment value and store it in each target."""
        v = self.ev(s["v"], fr)
        for t in s["targets"]:
            self.assign(t, v, fr)

    def assign(self, t, v, fr):
        """Store a value in an IR assignment target."""
        op = t["op"]
        if op == "name":
            self.store_name(fr, t["id"], v)
        elif op == "attr":
            self.set_attr(self.ev(t["o"], fr), t["n"], v)
        elif op == "index":
            self.set_index(self.ev(t["o"], fr), self.ev_key(t["k"], fr), v)
        elif op == "tuple":
            self.unpack(t["elts"], v, fr)
        else:
            raise DiasporeError(make_exc("TypeError", "cannot assign to this target"))

    def unpack(self, elts, v, fr):
        """Unpack a value into targets, including a starred target."""
        star = next((i for i, e in enumerate(elts) if e["op"] == "starred"), None)
        try:
            items = list(self.as_host_iter(v))
        except DiasporeError as e:
            if self.exc_isa(e.value, "TypeError"):
                raise DiasporeError(make_exc(
                    "TypeError", f"cannot unpack non-iterable {type_name(v)} object")) from None
            raise
        if star is None:
            if len(items) < len(elts):
                raise DiasporeError(make_exc(
                    "ValueError",
                    f"not enough values to unpack (expected {len(elts)}, got {len(items)})"))
            if len(items) > len(elts):
                raise DiasporeError(make_exc(
                    "ValueError", f"too many values to unpack (expected {len(elts)})"))
            for e, x in zip(elts, items):
                self.assign(e, x, fr)
        else:
            after = len(elts) - star - 1
            if len(items) < len(elts) - 1:
                raise DiasporeError(make_exc(
                    "ValueError",
                    f"not enough values to unpack (expected at least "
                    f"{len(elts) - 1}, got {len(items)})"))
            for e, x in zip(elts[:star], items[:star]):
                self.assign(e, x, fr)
            self.assign(elts[star]["v"], list(items[star:len(items) - after]), fr)
            for e, x in zip(elts[star + 1:], items[len(items) - after:]):
                self.assign(e, x, fr)

    def ex_augassign(self, s, fr):
        """Execute an augmented assignment."""
        t = s["target"]
        kind = s["kind"]
        rhs = self.ev(s["v"], fr)
        if t["op"] == "name":
            cur = self.load_name(fr, t["id"])
            self.store_name(fr, t["id"], self.binop_inplace(kind, cur, rhs))
        elif t["op"] == "attr":
            o = self.ev(t["o"], fr)
            cur = self.get_attr(o, t["n"])
            self.set_attr(o, t["n"], self.binop_inplace(kind, cur, rhs))
        elif t["op"] == "index":
            o = self.ev(t["o"], fr)
            k = self.ev_key(t["k"], fr)
            cur = self.get_index(o, k)
            self.set_index(o, k, self.binop_inplace(kind, cur, rhs))
        else:
            raise DiasporeError(make_exc("TypeError", "illegal augmented assignment target"))

    def ex_if(self, s, fr):
        """Execute the selected conditional branch."""
        if self.truth(self.ev(s["test"], fr)):
            self.exec_block(s["then"], fr)
        else:
            self.exec_block(s.get("orelse", ()), fr)

    def ex_while(self, s, fr):
        """Execute a while loop and its optional else block."""
        broke = False
        while self.truth(self.ev(s["test"], fr)):
            self.fuel()
            try:
                self.exec_block(s["body"], fr)
            except BreakSig:
                broke = True
                break
            except ContinueSig:
                continue
        if not broke:
            self.exec_block(s.get("orelse", ()), fr)

    def ex_for(self, s, fr):
        """Execute a for loop and its optional else block."""
        it = self.as_host_iter(self.ev(s["iter"], fr))
        broke = False
        while True:
            self.fuel()
            try:
                x = next(it)
            except StopIteration:
                break
            except (DiasporeError, DiasporeTrap, SuspendSig):
                raise
            except Exception as e:
                raise host_exc_to_diaspore(e) from None
            self.assign(s["target"], x, fr)
            try:
                self.exec_block(s["body"], fr)
            except BreakSig:
                broke = True
                break
            except ContinueSig:
                continue
        if not broke:
            self.exec_block(s.get("orelse", ()), fr)

    def ex_break(self, s, fr):
        """Signal a break from the enclosing loop."""
        raise BreakSig()

    def ex_continue(self, s, fr):
        """Signal continuation of the enclosing loop."""
        raise ContinueSig()

    def ex_pass(self, s, fr):
        """Execute a pass statement without side effects."""
        pass

    def ex_return(self, s, fr):
        """Return the optional value from the enclosing function."""
        raise ReturnSig(self.ev(s["v"], fr) if "v" in s else None)

    def ex_func(self, s, fr):
        """Bind a compiled function in the current scope."""
        self.store_name(fr, s["name"], self.make_function(s, fr))

    def make_function(self, node, fr: Frame):
        """Capture a function closure and evaluate its default arguments."""
        closure = fr
        while closure is not None and closure.is_class:
            closure = closure.parent
        module = self.modules.get(fr.module_name)
        fn = DiasporeFunction(self, node, closure, module)
        p = fn.params  # normalized: absent fields are their defaults
        fn.defaults = [self.ev(d, fr) for d in p["defaults"]]
        fn.kwdefaults = {k: self.ev(d, fr) for k, d in zip(p["kwonly"], p["kwdefaults"])
                         if d is not None}
        return fn

    def _check_base(self, bv):
        if isinstance(bv, DiasporeClass):
            return bv
        if isinstance(bv, BuiltinType) and bv.name in self._PRIM_EMPTY:
            return bv
        raise DiasporeError(make_exc(
            "TypeError", f"cannot subclass {self.diaspore_repr(bv)} in diaspore"))

    def ex_class(self, s, fr):
        """Evaluate a class definition and bind the resulting class."""
        bases = []
        for b in s.get("bases", ()):
            if b["op"] == "starred":
                for bv in self.as_host_iter(self.ev(b["v"], fr)):
                    bases.append(self._check_base(bv))
            else:
                bases.append(self._check_base(self.ev(b, fr)))
        merged = {}
        for kw in s.get("kwargs", ()):
            if kw["n"] is None:
                d = self.ev(kw["v"], fr)
                if not isinstance(d, dict):
                    raise DiasporeError(make_exc(
                        "TypeError", "argument after ** must be a mapping"))
                for k, v in d.items():
                    if not isinstance(k, str):
                        raise DiasporeError(make_exc("TypeError", "keywords must be strings"))
                    merged[k] = v
            else:
                merged[kw["n"]] = self.ev(kw["v"], fr)
        metaclass = merged.pop("metaclass", None)
        cf = Frame({}, fr, None, frozenset(), frozenset(), True,
                   fr.globals, s["name"], fr.module_name)
        self.exec_block(s["body"], cf)
        cls = self.build_class(s["name"], bases, cf.vars,
                               metaclass=metaclass, kwargs=merged)
        self.store_name(fr, s["name"], cls)

    def build_class(self, name, bases, ns, metaclass=None, kwargs=None):
        """Create a class and dispatch metaclass or subclass hooks."""
        def make(cls_name, cls_bases, cls_ns):
            made = DiasporeClass(cls_name, list(cls_bases), dict(cls_ns))
            for v in made.ns.values():
                if isinstance(v, DiasporeFunction) and v.defclass is None:
                    v.defclass = made
                elif isinstance(v, (StaticMethod, ClassMethod)) \
                        and v.fn.defclass is None:
                    v.fn.defclass = made
            return made

        plain_meta = (
            metaclass is None
            or (isinstance(metaclass, BuiltinType) and metaclass.name == "type")
            or (isinstance(metaclass, DiasporeClass)
                and metaclass.lookup("__new__") is _MISSING
                and metaclass.lookup("__call__") is _MISSING
                and metaclass.lookup("__init__") is _MISSING))
        if not plain_meta:
            result = self.call(metaclass, [name, tuple(bases), dict(ns)],
                               dict(kwargs or {}))
            if not isinstance(result, DiasporeClass):
                raise DiasporeError(make_exc(
                    "TypeError", f"metaclass returned {type_name(result)}, "
                                 f"not a class"))
            return result
        cls = make(name, bases, ns)
        if kwargs:
            for base in bases:
                init_sub = base.lookup("__init_subclass__")
                if init_sub is not _MISSING:
                    fn = init_sub.fn if isinstance(init_sub, ClassMethod) else init_sub
                    self.call(BoundMethod(fn, cls), [], dict(kwargs))
                    return cls
            raise DiasporeError(make_exc(
                "TypeError",
                f"{name}.__init_subclass__() takes no keyword arguments"))
        return cls

    def ex_raise(self, s, fr):
        """Raise an explicit exception or reraise the active exception."""
        if "exc" not in s:
            if not self.exc_stack:
                raise DiasporeError(make_exc("RuntimeError", "No active exception to re-raise"))
            raise DiasporeError(self.exc_stack[-1])
        v = self.ev(s["exc"], fr)
        if "cause" in s:
            self.ev(s["cause"], fr)
        if isinstance(v, DiasporeClass):
            if not v.issub(BASE_EXC):
                raise DiasporeError(make_exc("TypeError",
                                         "exceptions must derive from BaseException"))
            v = self.instantiate(v, [], {})
        if not (isinstance(v, DiasporeInstance) and v.cls.issub(BASE_EXC)):
            raise DiasporeError(make_exc("TypeError", "exceptions must derive from BaseException"))
        raise DiasporeError(v)

    def ex_try(self, s, fr):
        """Execute exception handlers and applicable cleanup blocks."""
        # Traps and durable suspension abandon execution without sandbox cleanup.
        if s.get("star"):
            return self.ex_try_star(s, fr)
        try:
            try:
                self.exec_block(s["body"], fr)
            except DiasporeError as e:
                for h in s.get("handlers", ()):
                    if not self.handler_matches(h, e.value, fr):
                        continue
                    if h.get("name"):
                        fr.vars[h["name"]] = e.value
                    self.exc_stack.append(e.value)
                    try:
                        self.exec_block(h["body"], fr)
                    finally:
                        self.exc_stack.pop()
                        if h.get("name"):
                            fr.vars.pop(h["name"], None)
                    break
                else:
                    raise
            else:
                self.exec_block(s.get("orelse", ()), fr)
        except (DiasporeTrap, SuspendSig):
            raise
        except BaseException:
            self.exec_block(s.get("finalbody", ()), fr)
            raise
        else:
            self.exec_block(s.get("finalbody", ()), fr)

    def ex_try_star(self, s, fr):
        """Execute exception-group handlers and propagate unmatched exceptions."""
        try:
            pending = None
            try:
                self.exec_block(s["body"], fr)
            except DiasporeError as e:
                pending = e.value
            if pending is not None:
                for h in s.get("handlers", ()):
                    match, rest = self._split_group(pending, h, fr)
                    if match is None:
                        continue
                    if h.get("name"):
                        fr.vars[h["name"]] = match
                    self.exc_stack.append(match)
                    try:
                        self.exec_block(h["body"], fr)
                    finally:
                        self.exc_stack.pop()
                        if h.get("name"):
                            fr.vars.pop(h["name"], None)
                    pending = rest
                    if pending is None:
                        break
                if pending is not None:
                    raise DiasporeError(pending)
            else:
                self.exec_block(s.get("orelse", ()), fr)
        except (DiasporeTrap, SuspendSig):
            raise
        except BaseException:
            self.exec_block(s.get("finalbody", ()), fr)
            raise
        else:
            self.exec_block(s.get("finalbody", ()), fr)

    def _split_group(self, exc, h, fr):
        """Split an exception into matching and residual handler subgroups."""
        t = self.ev(h["t"], fr)
        classes = list(t) if isinstance(t, tuple) else [t]

        def matches(e):
            return any(isinstance(c, DiasporeClass) and e.cls.issub(c) for c in classes)

        if isinstance(exc, DiasporeInstance) and exc.cls.issub(EXC_CLASSES["BaseExceptionGroup"]):
            members = list(exc.d.get("exceptions", ()))
            matched = [e for e in members if isinstance(e, DiasporeInstance) and matches(e)]
            unmatched = [e for e in members if not (isinstance(e, DiasporeInstance) and matches(e))]
            if not matched:
                return None, exc
            msg = exc.d.get("message", "")
            mg = make_group(msg, matched)
            rest = make_group(msg, unmatched) if unmatched else None
            return mg, rest
        # bare exception: matches as a singleton group
        if isinstance(exc, DiasporeInstance) and matches(exc):
            return make_group("", [exc]), None
        return None, exc

    def handler_matches(self, h, exc, fr) -> bool:
        """Return whether a handler accepts the given exception."""
        if h.get("t") is None:
            return True
        t = self.ev(h["t"], fr)
        classes = list(t) if isinstance(t, tuple) else [t]
        for c in classes:
            if not (isinstance(c, DiasporeClass) and c.issub(BASE_EXC)):
                raise DiasporeError(make_exc(
                    "TypeError",
                    "catching classes that do not inherit from BaseException is not allowed"))
            if exc.cls.issub(c):
                return True
        return False

    def ex_with(self, s, fr):
        """Execute nested context managers around a statement block."""
        self._with_items(s["items"], 0, s["body"], fr)

    def _with_items(self, items, i, body, fr):
        if i == len(items):
            self.exec_block(body, fr)
            return
        item = items[i]
        ctx = self.ev(item["ctx"], fr)
        if isinstance(ctx, DiasporeFile):
            enter = lambda: ctx
            exit_ = lambda *a: (ctx.f.close(), False)[1]
        elif isinstance(ctx, NativeObject) and "__enter__" in ctx.methods:
            enter = ctx.methods["__enter__"]
            exit_ = ctx.methods["__exit__"]
        elif isinstance(ctx, DiasporeInstance):
            if ctx.cls.lookup("__enter__") is _MISSING or ctx.cls.lookup("__exit__") is _MISSING:
                raise DiasporeError(make_exc(
                    "TypeError",
                    f"'{ctx.cls.name}' object does not support the context manager protocol"))
            enter = lambda: self.call_dunder(ctx, "__enter__", [])
            exit_ = lambda *a: self.call_dunder(ctx, "__exit__", list(a))
        else:
            raise DiasporeError(make_exc(
                "TypeError",
                f"'{type_name(ctx)}' object does not support the context manager protocol"))
        entered = enter()
        if item.get("name") is not None:
            self.assign(item["name"], entered, fr)
        try:
            self._with_items(items, i + 1, body, fr)
        except DiasporeError as e:
            if self.truth(exit_(e.value.cls, e.value, None)):
                return
            raise
        except (DiasporeTrap, SuspendSig):
            raise  # abandonment: no further diaspore code, __exit__ included
        except BaseException:
            exit_(None, None, None)  # return/break/continue unwinding (CPython runs it)
            raise
        else:
            exit_(None, None, None)

    def ex_import(self, s, fr):
        """Import a module and apply the requested binding rule."""
        mod = self.import_module(s["module"])
        bind = s.get("bind")
        if bind == "none":
            return
        if bind == "root":
            self.store_name(fr, s["alias"],
                            self.modules[s["module"].split(".")[0]])
        else:
            self.store_name(fr, s["alias"], mod)

    def ex_importfrom(self, s, fr):
        """Import named attributes from a module."""
        mod = self.import_module(s["module"])
        for spec in s["names"]:
            try:
                v = self.get_attr(mod, spec["n"])
            except DiasporeError:
                raise DiasporeError(make_exc(
                    "ImportError",
                    f"cannot import name '{spec['n']}' from '{s['module']}'")) from None
            self.store_name(fr, spec["alias"], v)

    def ex_del(self, s, fr):
        """Delete names, attributes, or indexed values."""
        for t in s["targets"]:
            op = t["op"]
            if op == "name":
                id_ = t["id"]
                if id_ in fr.vars:
                    del fr.vars[id_]
                elif fr.localset is not None and id_ in fr.localset:
                    raise DiasporeError(make_exc(
                        "UnboundLocalError",
                        f"cannot access local variable '{id_}' "
                        f"where it is not associated with a value"))
                elif fr.globals is not None and id_ in fr.globals:
                    del fr.globals[id_]
                else:
                    raise DiasporeError(make_exc("NameError", f"name '{id_}' is not defined"))
            elif op == "index":
                o = self.ev(t["o"], fr)
                k = self.ev_key(t["k"], fr)
                if isinstance(o, DiasporeInstance):
                    if o.cls.lookup("__delitem__") is _MISSING and o.prim is not None:
                        try:
                            del o.prim[k]
                        except Exception as e:
                            raise host_exc_to_diaspore(e) from None
                    else:
                        self.call_dunder_or_err(
                            o, "__delitem__", [k],
                            f"'{o.cls.name}' object doesn't support item deletion")
                else:
                    try:
                        del o[k]
                    except DiasporeError:
                        raise
                    except Exception as e:
                        raise host_exc_to_diaspore(e) from None
            elif op == "attr":
                o = self.ev(t["o"], fr)
                if isinstance(o, DiasporeInstance) and t["n"] in o.d:
                    del o.d[t["n"]]
                else:
                    raise DiasporeError(make_exc(
                        "AttributeError", f"'{type_name(o)}' object has no attribute '{t['n']}'"))
            else:
                raise DiasporeError(make_exc("TypeError", "cannot delete this target"))

    STMTS = {}


    def ev(self, e, fr: Frame):
        """Evaluate an IR expression in the given frame."""
        return self.EXPRS[e["op"]](self, e, fr)

    def ev_key(self, k, fr):
        """Evaluate an index key or slice."""
        if k["op"] == "slicelit":
            return slice(
                self.ev(k["lower"], fr) if "lower" in k else None,
                self.ev(k["upper"], fr) if "upper" in k else None,
                self.ev(k["step"], fr) if "step" in k else None,
            )
        return self.ev(k, fr)

    def e_const(self, e, fr):
        """Decode an IR constant into a runtime value."""
        t = e["t"]
        if t == "none":
            return None
        if t == "ellipsis":
            return Ellipsis
        if t == "complex":
            return complex(float(e["re"]), float(e["im"]))
        v = e["v"]
        if t == "int":
            return int(v)
        if t == "float":
            return float(v)
        if t == "str" or t == "bool":
            return v
        if t == "bytes":
            return base64.b64decode(v)
        raise DiasporeTrap(f"diaspore: bad const type {t!r}", code=4)

    def e_name(self, e, fr):
        """Evaluate a name lookup."""
        return self.load_name(fr, e["id"])

    def e_attr(self, e, fr):
        """Evaluate an attribute lookup."""
        return self.get_attr(self.ev(e["o"], fr), e["n"])

    def e_index(self, e, fr):
        """Evaluate an indexed lookup."""
        return self.get_index(self.ev(e["o"], fr), self.ev_key(e["k"], fr))

    def e_binop(self, e, fr):
        """Evaluate a binary expression."""
        return self.binop(e["kind"], self.ev(e["l"], fr), self.ev(e["r"], fr))

    def binop(self, kind, l, r, inplace=False):
        """Apply a binary operator with sandbox protocol dispatch."""
        if kind == "mod" and isinstance(l, (str, bytes)):
            raise DiasporeError(make_exc(
                "TypeError", "diaspore does not support %-formatting; use f-strings"))
        if kind == "mul":  # Charge sequence growth before allocating its result.
            seq, n = (l, r) if isinstance(r, int) else (r, l)
            if isinstance(n, int) and isinstance(seq, (str, bytes, list, tuple)) \
                    and n > 0:
                self.fuel_n(len(seq) * n // 16 + 1)
        if isinstance(l, DiasporeInstance) or isinstance(r, DiasporeInstance):
            names = _DUNDER_BIN.get(kind)
            if names:
                left, right = names
                if inplace and isinstance(l, DiasporeInstance):
                    iname = "__i" + left[2:]
                    if l.cls.lookup(iname) is not _MISSING:
                        return self.call_dunder(l, iname, [r])
                if isinstance(l, DiasporeInstance) and l.cls.lookup(left) is not _MISSING:
                    return self.call_dunder(l, left, [r])
                if (right and isinstance(r, DiasporeInstance)
                        and r.cls.lookup(right) is not _MISSING):
                    return self.call_dunder(r, right, [l])
            sym = {"add": "+", "sub": "-", "mul": "*", "div": "/", "floordiv": "//",
                   "mod": "%", "pow": "**", "lshift": "<<", "rshift": ">>",
                   "bitand": "&", "bitor": "|", "bitxor": "^",
                   "matmul": "@"}[kind]
            raise DiasporeError(make_exc(
                "TypeError",
                f"unsupported operand type(s) for {sym}: '{type_name(l)}' and '{type_name(r)}'"))
        try:
            return (_IBINOPS if inplace else _BINOPS)[kind](l, r)
        except DiasporeError:
            raise
        except Exception as ex:
            raise host_exc_to_diaspore(ex) from None

    def binop_inplace(self, kind, l, r):
        """Apply an in-place binary operator."""
        return self.binop(kind, l, r, inplace=True)

    def e_unary(self, e, fr):
        """Evaluate a unary expression."""
        kind = e["kind"]
        v = self.ev(e["v"], fr)
        if kind == "not":
            return not self.truth(v)
        if isinstance(v, DiasporeInstance):
            if kind == "neg" and v.cls.lookup("__neg__") is not _MISSING:
                return self.call_dunder(v, "__neg__", [])
            sym = {"neg": "-", "pos": "+", "invert": "~"}[kind]
            raise DiasporeError(make_exc(
                "TypeError", f"bad operand type for unary {sym}: '{v.cls.name}'"))
        try:
            return {"neg": _op.neg, "pos": _op.pos, "invert": _op.invert}[kind](v)
        except Exception as ex:
            raise host_exc_to_diaspore(ex) from None

    def e_boolop(self, e, fr):
        """Evaluate a short-circuit Boolean expression."""
        vals = e["values"]
        if e["kind"] == "and":
            for i, sub in enumerate(vals):
                v = self.ev(sub, fr)
                if i == len(vals) - 1 or not self.truth(v):
                    return v
        else:
            for i, sub in enumerate(vals):
                v = self.ev(sub, fr)
                if i == len(vals) - 1 or self.truth(v):
                    return v
        return v

    def e_compare(self, e, fr):
        """Evaluate a chained comparison."""
        left = self.ev(e["left"], fr)
        for kind, comp in zip(e["ops"], e["comps"]):
            right = self.ev(comp, fr)
            if not self.compare(kind, left, right):
                return False
            left = right
        return True

    def compare(self, kind, l, r) -> bool:
        """Compare values using sandbox comparison and membership protocols."""
        if kind == "is":
            return l is r
        if kind == "isnot":
            return l is not r
        if kind == "in":
            return self.contains(r, l)
        if kind == "notin":
            return not self.contains(r, l)
        if kind == "eq":
            return self.diaspore_eq(l, r)
        if kind == "ne":
            if isinstance(l, DiasporeInstance) and l.cls.lookup("__ne__") is not _MISSING:
                return self.truth(self.call_dunder(l, "__ne__", [r]))
            return not self.diaspore_eq(l, r)
        if isinstance(l, DiasporeInstance) or isinstance(r, DiasporeInstance):
            d = _CMP_DUNDER[kind]
            if isinstance(l, DiasporeInstance) and l.cls.lookup(d) is not _MISSING:
                return self.truth(self.call_dunder(l, d, [r]))
            raise DiasporeError(make_exc(
                "TypeError",
                f"'{_CMP_SYM[kind]}' not supported between instances of "
                f"'{type_name(l)}' and '{type_name(r)}'"))
        try:
            return bool(_CMPOPS[kind](l, r))
        except Exception as ex:
            raise host_exc_to_diaspore(ex) from None

    def diaspore_eq(self, l, r) -> bool:
        """Compare values structurally while honoring sandbox equality methods."""
        if isinstance(l, DiasporeInstance):
            if l.cls.lookup("__eq__") is not _MISSING:
                return self.truth(self.call_dunder(l, "__eq__", [r]))
            if isinstance(r, DiasporeInstance) and r.cls.lookup("__eq__") is not _MISSING:
                return self.truth(self.call_dunder(r, "__eq__", [l]))
            return l is r
        if isinstance(r, DiasporeInstance):
            return self.diaspore_eq(r, l)
        # Host equality would ignore sandbox __eq__ methods inside containers.
        # Container elements use identity-implies-equal, unlike top-level NaN.
        if isinstance(l, (list, tuple)) and type(l) is type(r):
            return len(l) == len(r) and all(self._eq_elem(a, b) for a, b in zip(l, r))
        if isinstance(l, dict) and isinstance(r, dict):
            if len(l) != len(r):
                return False
            for k, v in l.items():
                if k not in r or not self._eq_elem(v, r[k]):
                    return False
            return True
        try:
            return bool(l == r)
        except Exception as ex:
            raise host_exc_to_diaspore(ex) from None

    def _eq_elem(self, a, b) -> bool:
        return a is b or self.diaspore_eq(a, b)

    def contains(self, container, item) -> bool:
        """Test membership using sandbox iteration and equality protocols."""
        if isinstance(container, DiasporeInstance):
            if container.cls.lookup("__contains__") is _MISSING \
                    and container.prim is not None:
                return self.contains(container.prim, item)
            if container.cls.lookup("__contains__") is not _MISSING:
                return self.truth(self.call_dunder(container, "__contains__", [item]))
            for x in self.as_host_iter(container):
                if self.diaspore_eq(x, item):
                    return True
            return False
        if isinstance(container, (list, tuple)):
            return any(self._eq_elem(x, item) for x in container)
        try:
            return item in container
        except Exception as ex:
            raise host_exc_to_diaspore(ex) from None

    def e_cond(self, e, fr):
        """Evaluate the selected branch of a conditional expression."""
        return self.ev(e["then"], fr) if self.truth(self.ev(e["test"], fr)) \
            else self.ev(e["else"], fr)

    def _shadowed(self, fr):
        """Collect bindings that shadow frame-aware builtins."""
        s = set()
        f = fr
        while f is not None:
            s |= set(f.vars.keys())
            f = f.parent
        if fr.globals is not None:
            s |= set(fr.globals.keys())
        return s

    def e_call(self, e, fr):
        """Evaluate a call with argument expansion and frame-aware builtins."""
        fn = e["f"]
        if fn.get("op") == "name" and not e.get("args") and not e.get("kw"):
            if fn["id"] == "globals" and "globals" not in self._shadowed(fr):
                return fr.globals if fr.globals is not None else fr.vars
            if fn["id"] == "locals" and "locals" not in self._shadowed(fr):
                if fr.localset is None:
                    return fr.vars
                return dict(fr.vars)
        # exec and eval inherit the caller's namespace when none is supplied.
        if fn.get("op") == "name" and fn["id"] in ("exec", "eval") \
                and fn["id"] not in self._shadowed(fr):
            cargs = [self.ev(a["v"], fr) if a.get("op") == "starred"
                     else self.ev(a, fr) for a in e.get("args", ())]
            if len(cargs) == 1:
                gns = fr.globals if fr.globals is not None else fr.vars
                lns = fr.vars
                return self.exec_snippet(cargs[0], gns, None if lns is gns else lns,
                                         mode="eval" if fn["id"] == "eval" else "exec")
        f = self.ev(e["f"], fr)
        args = []
        for a in e.get("args", ()):
            if a["op"] == "starred":
                args.extend(self.as_host_iter(self.ev(a["v"], fr)))
            else:
                args.append(self.ev(a, fr))
        kwargs = {}
        for kw in e.get("kw", ()):
            if kw["n"] is None:
                d = self.ev(kw["v"], fr)
                if not isinstance(d, dict):
                    raise DiasporeError(make_exc(
                        "TypeError", "argument after ** must be a mapping"))
                for k, v in d.items():
                    if not isinstance(k, str):
                        raise DiasporeError(make_exc("TypeError", "keywords must be strings"))
                    kwargs[k] = v
            else:
                kwargs[kw["n"]] = self.ev(kw["v"], fr)
        if f is SUPER_MARKER:
            return self.make_super(args, fr)
        return self.call(f, args, kwargs)

    def e_list(self, e, fr):
        """Evaluate a list display."""
        return self._eval_elts(e.get("elts", ()), fr)

    def e_tuple(self, e, fr):
        """Evaluate a tuple display."""
        return tuple(self._eval_elts(e.get("elts", ()), fr))

    def e_set(self, e, fr):
        """Evaluate a set display."""
        return set(self._eval_elts(e.get("elts", ()), fr))

    def _eval_elts(self, elts, fr):
        out = []
        for x in elts:
            if x["op"] == "starred":
                out.extend(self.as_host_iter(self.ev(x["v"], fr)))
            else:
                out.append(self.ev(x, fr))
        return out

    def e_dict(self, e, fr):
        """Evaluate a dictionary display and mapping expansions."""
        out = {}
        for entry in e.get("entries", ()):
            if entry["k"] is None:
                v = self.ev(entry["v"], fr)
                if not isinstance(v, dict):
                    raise DiasporeError(make_exc(
                        "TypeError", "argument after ** must be a mapping"))
                out.update(v)
            else:
                out[self.hashable(self.ev(entry["k"], fr))] = self.ev(entry["v"], fr)
        return out

    def hashable(self, k):
        """Require a hashable value and return it."""
        try:
            hash(k)
        except Exception:
            raise DiasporeError(make_exc(
                "TypeError", f"unhashable type: '{type_name(k)}'")) from None
        return k

    def e_fstring(self, e, fr):
        """Evaluate and format f-string components."""
        out = []
        for p in e["parts"]:
            if "s" in p:
                out.append(p["s"])
                continue
            v = self.ev(p["v"], fr)
            conv = p.get("conv")
            if conv == "r":
                v = self.diaspore_repr(v)
            elif conv == "s":
                v = self.diaspore_str(v)
            elif conv == "a":
                v = self.diaspore_ascii(v)
            spec = self.ev(p["spec"], fr) if p.get("spec") is not None else ""
            out.append(self.format_value(v, spec))
        return "".join(out)

    def diaspore_ascii(self, v) -> str:
        """Return a representation with non-ASCII characters escaped."""
        return self.diaspore_repr(v).encode("ascii", "backslashreplace").decode("ascii")

    def format_value(self, v, spec) -> str:
        """Format a runtime value with a Python format specification."""
        if isinstance(v, (DiasporeInstance, DiasporeClass, DiasporeFunction, BoundMethod,
                          DiasporeModule, NativeModule, DiasporeGenerator, BuiltinType)):
            v = self.diaspore_str(v)
        if spec == "":
            return self.diaspore_str(v)
        try:
            return format(v, spec)
        except Exception as ex:
            raise host_exc_to_diaspore(ex) from None

    def e_funcexpr(self, e, fr):
        """Evaluate an anonymous function expression."""
        return self.make_function(e, fr)

    def e_yield(self, e, fr):
        """Yield a value and return the next sent value."""
        gen = self._gen_of(fr)
        v = self.ev(e["v"], fr) if "v" in e else None
        return gen._yield(v)  # value delivered by send(), None from next()

    def e_yieldfrom(self, e, fr):
        """Delegate iteration and generator control to a subiterator."""
        gen = self._gen_of(fr)
        it = self.as_host_iter(self.ev(e["v"], fr))
        delegating = isinstance(it, DiasporeGenerator)
        try:
            y = next(it)
        except StopIteration as s:
            return s.value
        while True:
            try:
                sent = gen._yield(y)
            except DiasporeError as ex:
                # forward throw/close into the subgenerator (PEP 380)
                if delegating:
                    if self.exc_isa(ex.value, "GeneratorExit"):
                        it.close()
                        raise
                    try:
                        y = it.throw(ex.value)
                        continue
                    except StopIteration as s:
                        return s.value
                raise
            try:
                y = it.send(sent) if delegating else next(it)
            except StopIteration as s:
                return s.value

    def _gen_of(self, fr):
        if fr.gen is None:
            raise DiasporeTrap("diaspore: yield outside generator frame", code=4)
        return fr.gen

    def e_namedexpr(self, e, fr):
        """Bind and return an assignment-expression value."""
        v = self.ev(e["v"], fr)
        self.store_name(fr, e["target"]["id"], v)
        return v

    EXPRS = {}


    def call(self, f, args, kwargs):
        """Invoke a sandbox or host callable with explicit arguments."""
        self.fuel()
        if isinstance(f, DiasporeFunction):
            return self.call_function(f, args, kwargs)
        if isinstance(f, BoundMethod):
            return self.call(f.fn, [f.obj] + args, kwargs)
        if isinstance(f, DiasporeClass):
            return self.instantiate(f, args, kwargs)
        if isinstance(f, BuiltinType):
            try:
                return f.ctor(*args, **kwargs)
            except (DiasporeError, DiasporeTrap):
                raise
            except BaseException as e:
                raise host_exc_to_diaspore(e) from None
        if isinstance(f, DiasporeInstance):
            if f.cls.lookup("__call__") is not _MISSING:
                return self.call_dunder(f, "__call__", args, kwargs)
            raise DiasporeError(make_exc("TypeError", f"'{f.cls.name}' object is not callable"))
        if f is SUPER_MARKER:
            raise DiasporeError(make_exc("RuntimeError",
                                     "super(): no arguments and no enclosing method"))
        if callable(f):
            try:
                return f(*args, **kwargs)
            except (DiasporeError, DiasporeTrap, SuspendSig, ReturnSig, BreakSig, ContinueSig):
                raise
            except StopIteration as s:
                raise DiasporeError(make_exc("StopIteration", *s.args)) from None
            except BaseException as e:
                raise host_exc_to_diaspore(e) from None
        raise DiasporeError(make_exc("TypeError", f"'{type_name(f)}' object is not callable"))

    def call_function(self, fn: DiasporeFunction, args, kwargs):
        """Invoke a compiled function in a new frame."""
        frame_vars = self.bind_args(fn, args, kwargs)
        module = fn.module or self.modules.get(self.bundle["main"])
        fr = Frame(frame_vars, fn.closure, fn.localset, fn.gdecl, fn.ndecl, False,
                   fn.closure.globals if fn.closure else None,
                   fn.name, module.name if isinstance(module, DiasporeModule) else "?")
        fr.defclass = fn.defclass
        p = fn.params["args"]
        fr.self_ = frame_vars.get(p[0]) if p else None
        if fn.gen:
            return DiasporeGenerator(self, fn, fr)
        if self.depth >= self.max_depth:
            raise DiasporeError(make_exc("RecursionError", "maximum recursion depth exceeded"))
        self.depth += 1
        try:
            self.exec_block(fn.node["body"], fr)
            return None
        except ReturnSig as r:
            return r.value
        except DiasporeError as e:
            e.tb.append((fr.module_name, fn.name, fr.line))
            raise
        finally:
            self.depth -= 1

    def bind_args(self, fn: DiasporeFunction, args, kwargs):
        """Bind positional and keyword arguments to a function's parameters."""
        p = fn.params
        names = p["args"]
        out = {}
        if len(args) > len(names):
            if p["vararg"]:
                out[p["vararg"]] = tuple(args[len(names):])
                pos = args[:len(names)]
            else:
                n = len(names)
                have_defaults = len(fn.defaults) > 0
                took = (f"from {n - len(fn.defaults)} to {n}" if have_defaults else str(n))
                raise DiasporeError(make_exc(
                    "TypeError",
                    f"{fn.name}() takes {took} positional argument"
                    f"{'s' if n != 1 or have_defaults else ''} but {len(args)} "
                    f"{'were' if len(args) != 1 else 'was'} given"))
        else:
            pos = args
            if p["vararg"]:
                out[p["vararg"]] = ()
        for name, v in zip(names, pos):
            out[name] = v
        extra = {}
        for k, v in kwargs.items():
            if k in names:
                if k in out:
                    raise DiasporeError(make_exc(
                        "TypeError", f"{fn.name}() got multiple values for argument '{k}'"))
                out[k] = v
            elif k in p["kwonly"]:
                out[k] = v
            elif p["kwarg"]:
                extra[k] = v
            else:
                raise DiasporeError(make_exc(
                    "TypeError", f"{fn.name}() got an unexpected keyword argument '{k}'"))
        ndefaults = len(fn.defaults)
        required = names[:len(names) - ndefaults] if ndefaults else names
        missing = [n for n in required if n not in out]
        if missing:
            raise DiasporeError(make_exc(
                "TypeError",
                f"{fn.name}() missing {len(missing)} required positional argument"
                f"{'s' if len(missing) > 1 else ''}: {self._name_list(missing)}"))
        for i, name in enumerate(names[len(names) - ndefaults:]):
            if name not in out:
                out[name] = fn.defaults[i]
        miss_kw = []
        for name in p["kwonly"]:
            if name not in out:
                if name in fn.kwdefaults:
                    out[name] = fn.kwdefaults[name]
                else:
                    miss_kw.append(name)
        if miss_kw:
            raise DiasporeError(make_exc(
                "TypeError",
                f"{fn.name}() missing {len(miss_kw)} required keyword-only argument"
                f"{'s' if len(miss_kw) > 1 else ''}: {self._name_list(miss_kw)}"))
        if p["kwarg"]:
            out[p["kwarg"]] = extra
        return out

    @staticmethod
    def _name_list(names):
        quoted = [f"'{n}'" for n in names]
        if len(quoted) == 1:
            return quoted[0]
        if len(quoted) == 2:
            return f"{quoted[0]} and {quoted[1]}"
        return ", ".join(quoted[:-1]) + f", and {quoted[-1]}"

    _PRIM_EMPTY = {"dict": dict, "list": list, "set": set,
                   "str": str, "bytes": bytes, "int": int, "float": float,
                   "tuple": tuple, "bool": bool}

    def instantiate(self, cls: DiasporeClass, args, kwargs):
        """Construct a sandbox instance and invoke its initializer."""
        inst = DiasporeInstance(cls)
        if cls.prim is not None:
            base = cls.prim.name
            # immutable primitives take their value from the constructor args
            # (like __new__); mutable ones start empty and __init__ fills them
            if base in ("str", "bytes", "int", "float", "tuple", "bool"):
                inst.prim = cls.prim.ctor(*args, **kwargs) if (args or kwargs) \
                    else self._PRIM_EMPTY[base]()
            else:
                inst.prim = self._PRIM_EMPTY[base]()
        init = cls.lookup("__init__")
        if init is _MISSING:
            if (args or kwargs) and cls.prim is None:
                raise DiasporeError(make_exc("TypeError", f"{cls.name}() takes no arguments"))
            if cls.prim is not None and cls.prim.name in ("dict", "list", "set") \
                    and (args or kwargs):
                inst.prim = cls.prim.ctor(*args, **kwargs)
            return inst
        r = self.call(self.bind_attr(init, inst, cls), args, kwargs)
        if r is not None:
            raise DiasporeError(make_exc(
                "TypeError", f"__init__() should return None, not '{type_name(r)}'"))
        return inst

    def _prim(self, v):
        """Underlying builtin value of a builtin-subclass instance, else None."""
        if isinstance(v, DiasporeInstance) and v.prim is not None:
            return v.prim
        return None

    def _raw(self, obj):
        """Raw builtin value of obj (unwrap a builtin-subclass instance)."""
        if isinstance(obj, DiasporeInstance) and obj.prim is not None:
            return obj.prim
        return obj

    def _builtin_unbound(self, tname, method):
        """Expose a primitive base method that bypasses subclass overrides."""
        raw = self._raw
        dunder = {
            "__getitem__": lambda o, k: self.get_index(raw(o), k),
            "__setitem__": lambda o, k, v: self.set_index(raw(o), k, v),
            "__len__": lambda o: len(raw(o)),
            "__contains__": lambda o, x: self.contains(raw(o), x),
            "__iter__": lambda o: iter(raw(o)),
        }
        if method in dunder:
            return dunder[method]
        allowed = {"dict": DICT_METHODS, "list": LIST_METHODS, "set": SET_METHODS,
                   "str": STR_METHODS, "tuple": TUPLE_METHODS,
                   "bytes": BYTES_METHODS}.get(tname)
        if allowed and method in allowed:
            return lambda o, *a, **kw: self.host_attr(raw(o), method)(*a, **kw)
        return None

    def call_dunder(self, obj: DiasporeInstance, name, args, kwargs=None):
        """Look up and call a special method on a sandbox instance."""
        fn = obj.cls.lookup(name)
        if fn is _MISSING:
            raise DiasporeError(make_exc(
                "AttributeError", f"'{obj.cls.name}' object has no attribute '{name}'"))
        return self.call(self.bind_attr(fn, obj, obj.cls), args, kwargs or {})

    def call_dunder_or_err(self, obj, name, args, errmsg):
        """Call a special method or raise the supplied type error."""
        if obj.cls.lookup(name) is _MISSING:
            raise DiasporeError(make_exc("TypeError", errmsg))
        return self.call_dunder(obj, name, args)

    def make_super(self, args, fr: Frame):
        """Build a super object from arguments or the current method frame."""
        if not args:
            if fr.defclass is None or fr.self_ is None:
                raise DiasporeError(make_exc(
                    "RuntimeError", "super(): __class__ cell not found"))
            return SuperObject(fr.defclass, fr.self_)
        if len(args) == 2 and isinstance(args[0], DiasporeClass):
            return SuperObject(args[0], args[1])
        raise DiasporeError(make_exc("TypeError", "super() argument error"))


    def bind_attr(self, v, inst, cls):
        """Bind a descriptor to its instance or class."""
        if isinstance(v, DiasporeFunction):
            return BoundMethod(v, inst)
        if isinstance(v, StaticMethod):
            return v.fn
        if isinstance(v, ClassMethod):
            return BoundMethod(v.fn, cls)
        if callable(v) and not isinstance(v, (DiasporeClass, BuiltinType)):
            return BoundMethod(_HostFn(self, v), inst)
        return v

    def get_attr(self, o, n):
        """Resolve a sandbox-visible attribute."""
        if isinstance(o, DiasporeInstance):
            if n == "__class__":
                return o.cls
            if n in o.d:
                return o.d[n]
            if n in ("exceptions", "message") and o.cls.issub(
                    EXC_CLASSES["BaseExceptionGroup"]):
                return o.d.get(n)
            v = o.cls.lookup(n)
            if isinstance(v, Property):
                if v.fget is None:
                    raise DiasporeError(make_exc(
                        "AttributeError", f"property '{n}' of "
                        f"'{o.cls.name}' object has no getter"))
                return self.call(self.bind_attr(v.fget, o, o.cls), [], {})
            if v is not _MISSING:
                return self.bind_attr(v, o, o.cls)
            if o.prim is not None:
                allowed = {dict: DICT_METHODS, list: LIST_METHODS, set: SET_METHODS,
                           str: STR_METHODS, tuple: TUPLE_METHODS,
                           bytes: BYTES_METHODS}.get(type(o.prim))
                if allowed and n in allowed:
                    return self.host_attr(o.prim, n)
            ga = o.cls.lookup("__getattr__")
            if ga is not _MISSING:
                return self.call(self.bind_attr(ga, o, o.cls), [n], {})
            raise DiasporeError(make_exc(
                "AttributeError", f"'{o.cls.name}' object has no attribute '{n}'"))
        if isinstance(o, BuiltinType):
            if n == "__name__":
                return o.name
            unbound = self._builtin_unbound(o.name, n)
            if unbound is not None:
                return unbound
            raise DiasporeError(make_exc(
                "AttributeError", f"type object '{o.name}' has no attribute '{n}'"))
        if isinstance(o, DiasporeClass):
            v = o.lookup(n)
            if v is _MISSING:
                if n == "__name__":
                    return o.name
                if n == "__mro__":
                    return tuple(o.mro)
                if n == "__bases__":
                    return tuple(o.bases) if o.bases else (OBJECT,)
                if n == "__dict__":
                    return dict(o.ns)
                if n == "__qualname__":
                    return o.name
                raise DiasporeError(make_exc(
                    "AttributeError", f"type object '{o.name}' has no attribute '{n}'"))
            if isinstance(v, ClassMethod):
                return BoundMethod(v.fn, o)
            if isinstance(v, StaticMethod):
                return v.fn
            return v
        if isinstance(o, SuperObject):
            mro = o.obj.cls.mro if isinstance(o.obj, DiasporeInstance) else o.obj.mro
            try:
                start = mro.index(o.cls) + 1
            except ValueError:
                start = 0
            for c in mro[start:]:
                if n in c.ns:
                    return self.bind_attr(c.ns[n], o.obj, c)
            raise DiasporeError(make_exc(
                "AttributeError", f"'super' object has no attribute '{n}'"))
        if isinstance(o, DiasporeModule):
            if n in o.frame.vars:
                return o.frame.vars[n]
            raise DiasporeError(make_exc(
                "AttributeError", f"module '{o.name}' has no attribute '{n}'"))
        if isinstance(o, NativeModule):
            if n in o.ns:
                return o.ns[n]
            raise DiasporeError(make_exc(
                "AttributeError", f"module '{o.name}' has no attribute '{n}'"))
        if isinstance(o, DiasporeFile):
            if n in DiasporeFile.METHODS:
                return getattr(o.f, n)
            raise DiasporeError(make_exc(
                "AttributeError", f"'file' object has no attribute '{n}'"))
        if isinstance(o, Property):
            if n == "setter":
                return lambda f: Property(o.fget, f, o.fdel, o.doc)
            if n == "getter":
                return lambda f: Property(f, o.fset, o.fdel, o.doc)
            if n == "deleter":
                return lambda f: Property(o.fget, o.fset, f, o.doc)
            if n == "fget":
                return o.fget
            if n == "fset":
                return o.fset
            raise DiasporeError(make_exc(
                "AttributeError", f"'property' object has no attribute '{n}'"))
        if isinstance(o, DiasporeFunction):
            if n in o.attrs:
                return o.attrs[n]
            if n == "__name__":
                return o.name
            if n == "__doc__":
                return o.attrs.get("__doc__")
            if n == "__dict__":
                return o.attrs
            raise DiasporeError(make_exc(
                "AttributeError", f"'function' object has no attribute '{n}'"))
        if isinstance(o, BoundMethod):
            if n == "__name__":
                return o.fn.name
            if n == "__self__":
                return o.obj
            if n == "__func__":
                return o.fn
            return self.get_attr(o.fn, n)
        if isinstance(o, slice):
            if n in ("start", "stop", "step"):
                return getattr(o, n)
            if n == "indices":
                return o.indices
            raise DiasporeError(make_exc(
                "AttributeError", f"'slice' object has no attribute '{n}'"))
        if isinstance(o, complex):
            if n == "real":
                return o.real
            if n == "imag":
                return o.imag
            if n == "conjugate":
                return lambda: o.conjugate()
            raise DiasporeError(make_exc(
                "AttributeError", f"'complex' object has no attribute '{n}'"))
        if isinstance(o, (int, float)) and n in ("real", "imag", "conjugate", "bit_length",
                                                 "numerator", "denominator", "is_integer"):
            if n == "real":
                return o
            if n == "imag":
                return 0 if isinstance(o, int) else 0.0
            if n == "conjugate":
                return lambda: o
            if n == "bit_length" and isinstance(o, int):
                return o.bit_length
            if n == "is_integer" and isinstance(o, float):
                return o.is_integer
            if n == "numerator" and isinstance(o, int):
                return o
            if n == "denominator" and isinstance(o, int):
                return 1
        if isinstance(o, DiasporeGenerator):
            if n == "send":
                return o.send
            if n == "close":
                return o.close
            if n == "throw":
                def _throw(exc):
                    if isinstance(exc, DiasporeClass) and exc.issub(BASE_EXC):
                        exc = self.instantiate(exc, [], {})
                    if not (isinstance(exc, DiasporeInstance) and exc.cls.issub(BASE_EXC)):
                        raise DiasporeError(make_exc(
                            "TypeError",
                            "exceptions must derive from BaseException"))
                    return o.throw(exc)
                return _throw
            raise DiasporeError(make_exc(
                "AttributeError", f"'generator' object has no attribute '{n}'"))
        if isinstance(o, NativeObject):
            if not n.startswith(".") and not n.startswith("__"):
                if n in o.props:
                    return o.props[n]
                if n in o.methods:
                    return o.methods[n]
            raise DiasporeError(make_exc(
                "AttributeError", f"'{o.kind}' object has no attribute '{n}'"))
        return self.host_attr(o, n)

    def host_attr(self, o, n):
        """Resolve an allowlisted attribute on a host primitive."""
        t = type(o)
        allowed = {str: STR_METHODS, list: LIST_METHODS, dict: DICT_METHODS,
                   set: SET_METHODS, tuple: TUPLE_METHODS, bytes: BYTES_METHODS}.get(t)
        if allowed and n in allowed:
            if t is dict and n in ("keys", "values", "items"):
                return {"keys": lambda: list(o.keys()),
                        "values": lambda: list(o.values()),
                        "items": lambda: [tuple(kv) for kv in o.items()]}[n]
            if t is list and n == "sort":
                return functools.partial(self._list_sort, o)
            if t in (list, tuple) and n in ("index", "count", "remove"):
                return functools.partial(self._seq_search, o, n)
            return getattr(o, n)
        raise DiasporeError(make_exc(
            "AttributeError", f"'{type_name(o)}' object has no attribute '{n}'"))

    def set_attr(self, o, n, v):
        """Assign a sandbox-visible attribute."""
        if isinstance(o, DiasporeFunction):
            if n == "__name__":
                o.name = v
            else:
                o.attrs[n] = v
            return
        if isinstance(o, DiasporeInstance):
            prop = o.cls.lookup(n)
            if isinstance(prop, Property):
                if prop.fset is None:
                    raise DiasporeError(make_exc(
                        "AttributeError", f"property '{n}' of "
                        f"'{o.cls.name}' object has no setter"))
                self.call(self.bind_attr(prop.fset, o, o.cls), [v], {})
                return
            o.d[n] = v
            return
        if isinstance(o, DiasporeClass):
            o.ns[n] = v
            return
        if isinstance(o, DiasporeModule):
            o.frame.vars[n] = v
            return
        raise DiasporeError(make_exc(
            "AttributeError", f"'{type_name(o)}' object has no attribute '{n}'"))


    def get_index(self, o, k):
        """Read an indexed value through sandbox protocols."""
        if isinstance(o, DiasporeInstance):
            if o.cls.lookup("__getitem__") is _MISSING and o.prim is not None:
                if isinstance(o.prim, dict) and k not in o.prim \
                        and o.cls.lookup("__missing__") is not _MISSING:
                    return self.call_dunder(o, "__missing__", [k])
                return self.get_index(o.prim, k)
            return self.call_dunder_or_err(
                o, "__getitem__", [k], f"'{o.cls.name}' object is not subscriptable")
        try:
            if isinstance(o, dict) and not isinstance(k, slice):
                self.hashable(k)
            return o[k]
        except DiasporeError:
            raise
        except Exception as e:
            raise host_exc_to_diaspore(e) from None

    def set_index(self, o, k, v):
        """Write an indexed value through sandbox protocols."""
        if isinstance(o, DiasporeInstance):
            if o.cls.lookup("__setitem__") is _MISSING and o.prim is not None:
                self.set_index(o.prim, k, v)
                return
            self.call_dunder_or_err(
                o, "__setitem__", [k, v],
                f"'{o.cls.name}' object does not support item assignment")
            return
        try:
            if isinstance(o, dict) and not isinstance(k, slice):
                self.hashable(k)
            o[k] = v
        except DiasporeError:
            raise
        except Exception as e:
            raise host_exc_to_diaspore(e) from None


    def truth(self, v) -> bool:
        """Evaluate truth using sandbox Boolean and length protocols."""
        if isinstance(v, DiasporeInstance):
            if v.cls.lookup("__bool__") is _MISSING and v.cls.lookup("__len__") is _MISSING \
                    and v.prim is not None:
                return bool(v.prim)
            if v.cls.lookup("__bool__") is not _MISSING:
                r = self.call_dunder(v, "__bool__", [])
                if not isinstance(r, bool):
                    raise DiasporeError(make_exc(
                        "TypeError", f"__bool__ should return bool, returned {type_name(r)}"))
                return r
            if v.cls.lookup("__len__") is not _MISSING:
                return self.diaspore_len(v) != 0
            return True
        if isinstance(v, (DiasporeClass, DiasporeFunction, BoundMethod, DiasporeModule,
                          NativeModule, DiasporeGenerator, BuiltinType, DiasporeFile,
                          NativeObject)):
            return True
        return bool(v)

    def diaspore_len(self, v) -> int:
        """Return a value's length through the sandbox protocol."""
        if isinstance(v, DiasporeInstance):
            if v.cls.lookup("__len__") is _MISSING and v.prim is not None:
                return len(v.prim)
            r = self.call_dunder_or_err(v, "__len__", [],
                                        f"object of type '{v.cls.name}' has no len()")
            if not isinstance(r, int) or isinstance(r, bool):
                raise DiasporeError(make_exc("TypeError", "__len__ should return an int"))
            return r
        try:
            return len(v)
        except Exception:
            raise DiasporeError(make_exc(
                "TypeError", f"object of type '{type_name(v)}' has no len()")) from None

    def as_host_iter(self, v):
        """Adapt a sandbox iterable to a host iterator."""
        if isinstance(v, DiasporeGenerator) or isinstance(v, _InstanceIter):
            return v
        if isinstance(v, NativeObject):
            if v.kind == "tasks.Channel":
                return _ChannelIter(v)
            raise DiasporeError(make_exc(
                "TypeError", f"'{v.kind}' object is not iterable"))
        if isinstance(v, DiasporeInstance):
            if v.cls.lookup("__iter__") is _MISSING and v.cls.lookup("__next__") is _MISSING \
                    and v.prim is not None:
                return iter(v.prim)
            if v.cls.lookup("__iter__") is not _MISSING:
                it = self.call_dunder(v, "__iter__", [])
                if isinstance(it, (DiasporeGenerator, _InstanceIter)):
                    return it
                if isinstance(it, DiasporeInstance):
                    return _InstanceIter(self, it)
                return self.as_host_iter(it)
            if v.cls.lookup("__next__") is not _MISSING:
                return _InstanceIter(self, v)
            raise DiasporeError(make_exc(
                "TypeError", f"'{v.cls.name}' object is not iterable"))
        try:
            return iter(v)
        except TypeError:
            raise DiasporeError(make_exc(
                "TypeError", f"'{type_name(v)}' object is not iterable")) from None


    def exc_isa(self, v, clsname) -> bool:
        """Return whether a value matches a named sandbox exception class."""
        return isinstance(v, DiasporeInstance) and v.cls.issub(EXC_CLASSES[clsname])

    def exc_str(self, v: DiasporeInstance) -> str:
        """Render a sandbox exception message."""
        if v.cls.lookup("__str__") is not _MISSING:
            return self.diaspore_str(v)
        args = v.d.get("args", ())
        if v.cls.issub(EXC_CLASSES["KeyError"]) and len(args) == 1:
            return self.diaspore_repr(args[0])
        if not args:
            return ""
        if len(args) == 1:
            return self.diaspore_str(args[0])
        return self.diaspore_repr(tuple(args))

    def diaspore_str(self, v) -> str:
        """Convert a runtime value to its sandbox string form."""
        if isinstance(v, str):
            return v
        if isinstance(v, DiasporeInstance):
            if v.cls.issub(BASE_EXC) and v.cls.lookup("__str__") is _MISSING:
                return self.exc_str(v)
            for name in ("__str__", "__repr__"):
                if v.cls.lookup(name) is not _MISSING:
                    r = self.call_dunder(v, name, [])
                    if not isinstance(r, str):
                        raise DiasporeError(make_exc(
                            "TypeError", f"{name} returned non-string ({type_name(r)})"))
                    return r
            if v.prim is not None:
                return self.diaspore_str(v.prim)
            return f"<{v.cls.name} object>"
        return self.diaspore_repr(v)

    def diaspore_repr(self, v) -> str:
        """Return a Python-style representation of a runtime value."""
        if isinstance(v, complex):
            return repr(v)  # CPython's complex repr is canonical: (3+4j), 1j
        if isinstance(v, (int, float, str, bytes, bool, range)) or v is None \
                or v is Ellipsis:
            return repr(v)
        if isinstance(v, (list, tuple, dict)):
            if id(v) in self._repring:
                return {list: "[...]", tuple: "(...)", dict: "{...}"}[type(v)]
            self._repring.add(id(v))
            try:
                if isinstance(v, list):
                    return "[" + ", ".join(self.diaspore_repr(x) for x in v) + "]"
                if isinstance(v, tuple):
                    if len(v) == 1:
                        return "(" + self.diaspore_repr(v[0]) + ",)"
                    return "(" + ", ".join(self.diaspore_repr(x) for x in v) + ")"
                return "{" + ", ".join(f"{self.diaspore_repr(k)}: {self.diaspore_repr(x)}"
                                       for k, x in v.items()) + "}"
            finally:
                self._repring.discard(id(v))
        if isinstance(v, set):
            if not v:
                return "set()"
            return "{" + ", ".join(self.diaspore_repr(x) for x in v) + "}"
        if isinstance(v, slice):
            parts = [
                self.diaspore_repr(v.start),
                self.diaspore_repr(v.stop),
                self.diaspore_repr(v.step),
            ]
            return f"slice({', '.join(parts)})"
        if isinstance(v, DiasporeInstance):
            if v.cls.issub(BASE_EXC) and v.cls.lookup("__repr__") is _MISSING:
                args = v.d.get("args", ())
                return f"{v.cls.name}({', '.join(self.diaspore_repr(a) for a in args)})"
            if v.cls.lookup("__repr__") is _MISSING and v.prim is not None:
                return self.diaspore_repr(v.prim)
            if v.cls.lookup("__repr__") is not _MISSING:
                r = self.call_dunder(v, "__repr__", [])
                if not isinstance(r, str):
                    raise DiasporeError(make_exc("TypeError", "__repr__ returned non-string"))
                return r
            return f"<{v.cls.name} object>"
        if isinstance(v, DiasporeClass):
            return f"<class '{v.name}'>"
        if isinstance(v, BuiltinType):
            return f"<class '{v.name}'>"
        if isinstance(v, DiasporeFunction):
            return f"<function {v.name}>"
        if isinstance(v, BoundMethod):
            return f"<bound method {v.fn.name}>"
        if isinstance(v, DiasporeGenerator):
            return f"<generator {v.fn.name}>"
        if isinstance(v, (DiasporeModule, NativeModule)):
            return f"<module '{v.name}'>"
        if isinstance(v, DiasporeFile):
            return "<file>"
        if isinstance(v, NativeObject):
            return f"<{v.kind}>"
        if callable(v):
            return f"<builtin {getattr(v, '__name__', 'function')}>"
        return f"<{type_name(v)}>"


    def _list_sort(self, lst, key=None, reverse=False):
        if key is None:
            lst.sort(key=functools.cmp_to_key(self._cmp), reverse=bool(reverse))
        else:
            decorated = [(self.call(key, [x], {}), x) for x in lst]
            decorated.sort(key=functools.cmp_to_key(lambda a, b: self._cmp(a[0], b[0])),
                           reverse=bool(reverse))
            lst[:] = [x for _, x in decorated]
        return None

    def _seq_search(self, seq, op, item, *rest):
        """Search sequences while honoring sandbox element equality."""
        if op == "count":
            return sum(1 for x in seq if self._eq_elem(x, item))
        lo, hi = 0, len(seq)
        if op == "index" and rest:
            lo = rest[0] if rest[0] >= 0 else max(0, len(seq) + rest[0])
            if len(rest) > 1:
                hi = rest[1] if rest[1] >= 0 else max(0, len(seq) + rest[1])
        for i in range(lo, min(hi, len(seq))):
            if self._eq_elem(seq[i], item):
                if op == "index":
                    return i
                del seq[i]
                return None
        if op == "remove":
            raise DiasporeError(make_exc("ValueError", "list.remove(x): x not in list"))
        if isinstance(seq, tuple):
            raise DiasporeError(make_exc("ValueError", "tuple.index(x): x not in tuple"))
        raise DiasporeError(make_exc("ValueError", f"{self.diaspore_repr(item)} is not in list"))

    def _cmp(self, a, b) -> int:
        if self.compare("lt", a, b):
            return -1
        if self.compare("lt", b, a):
            return 1
        return 0

    def require_cap(self, cap: str):
        """Require a capability before performing a host operation."""
        if cap not in self.caps:
            raise DiasporeError(make_exc(
                "PermissionError",
                f"diaspore: capability '{cap}' not granted (run with --allow-{cap})"))

    @staticmethod
    def _addr_match(pattern: str, host: str, port: int) -> bool:
        ph, _, pp = pattern.rpartition(":")
        if not ph:
            return False
        host_ok = ph == "*" or ph.lower() == host.lower()
        port_ok = pp == "*" or pp == str(port)
        return host_ok and port_ok

    def require_net(self, kind: str, host: str, port: int):
        """Require a network grant matching the operation, host, and port."""
        self.require_cap("net")
        pol = self.policy.get("net")
        if pol is None:
            return
        if any(self._addr_match(p, host, port) for p in pol.get(kind, [])):
            return
        raise DiasporeError(make_exc(
            "PermissionError",
            f"diaspore: net.{kind} {host}:{port} not permitted by policy"))

    def require_fs(self, kind: str, path: str) -> str:
        """Require a read or write grant and return the resolved real path."""
        self.require_cap("fs")
        real = os.path.realpath(path)
        pol = self.policy.get("fs")
        if pol is None:
            return real
        for root in pol.get(kind, []):
            rroot = os.path.realpath(root)
            if real == rroot or real.startswith(rroot.rstrip(os.sep) + os.sep):
                return real
        raise DiasporeError(make_exc(
            "PermissionError",
            f"diaspore: fs.{kind} {path} not permitted by policy"))

    def compile_snippet(self, source, filename, mode):
        """Compile source for sandbox exec or eval."""
        import ast as _ast

        from .compiler import ModuleCompiler
        if isinstance(source, _CodeObject):
            return source
        if not isinstance(source, str):
            raise DiasporeError(make_exc(
                "TypeError", "compile() arg 1 must be a string"))
        mc = ModuleCompiler("<exec>", filename, source)
        try:
            tree = _ast.parse(source, filename=filename, mode="eval"
                              if mode == "eval" else "exec")
        except SyntaxError as e:
            raise DiasporeError(make_exc("SyntaxError", e.msg)) from None
        try:
            mc.scopes.append(_scope_module())
            if mode == "eval":
                nodes = [{"op": "expr", "v": mc.expr(tree.body), "ln": 1}]
            else:
                nodes = mc.block(tree.body)
        except Exception as e:
            raise DiasporeError(make_exc("SyntaxError", str(e))) from None
        return _CodeObject(nodes, mode, filename)

    def exec_snippet(self, code, glob, loc, mode):
        """Execute compiled or source code in explicit namespaces."""
        co = self.compile_snippet(code, "<string>", mode) \
            if isinstance(code, str) else code
        if not isinstance(co, _CodeObject):
            raise DiasporeError(make_exc("TypeError", "exec/eval arg must be str or code"))
        if glob is None:
            glob = dict(self.modules[self.bundle["main"]].frame.vars) \
                if self.bundle["main"] in self.modules else {}
        ns = glob if loc is None else loc
        fr = Frame(ns, None, None, frozenset(), frozenset(), False,
                   glob, "<exec>", self.bundle["main"])
        if mode == "eval":
            return self.ev(co.nodes[0]["v"], fr)
        try:
            self.exec_block(co.nodes, fr)
        except ReturnSig:
            pass
        return None

    def call_in(self, module: str, func: str, *args):
        """Call a module function from the host, translating escaped errors."""
        try:
            mod = self.import_module(module)
            f = self.get_attr(mod, func)
            return self.call(f, list(args), {})
        except DiasporeError as e:
            v = e.value
            name = v.cls.name if isinstance(v, DiasporeInstance) else type_name(v)
            msg = self.exc_str(v) if isinstance(v, DiasporeInstance) else self.diaspore_str(v)
            raise DiasporeException(name, msg) from None


class _HostFn:
    """Adapt a host callable to sandbox function binding."""

    def __init__(self, interp, fn):
        """Store the host callable and its interpreter."""
        self.interp = interp
        self.fn = fn
        self.name = getattr(fn, "__name__", "builtin")
        self.defclass = None

    def __call__(self, *args, **kwargs):
        """Invoke the wrapped host callable."""
        return self.fn(*args, **kwargs)


Interp.STMTS = {
    "expr": Interp.ex_expr, "assign": Interp.ex_assign, "augassign": Interp.ex_augassign,
    "if": Interp.ex_if, "while": Interp.ex_while, "for": Interp.ex_for,
    "break": Interp.ex_break, "continue": Interp.ex_continue, "pass": Interp.ex_pass,
    "return": Interp.ex_return, "func": Interp.ex_func, "class": Interp.ex_class,
    "raise": Interp.ex_raise, "try": Interp.ex_try, "with": Interp.ex_with,
    "import": Interp.ex_import, "importfrom": Interp.ex_importfrom, "del": Interp.ex_del,
}
Interp.EXPRS = {
    "const": Interp.e_const, "name": Interp.e_name, "attr": Interp.e_attr,
    "index": Interp.e_index, "binop": Interp.e_binop, "unary": Interp.e_unary,
    "boolop": Interp.e_boolop, "compare": Interp.e_compare, "cond": Interp.e_cond,
    "call": Interp.e_call, "list": Interp.e_list, "tuple": Interp.e_tuple,
    "set": Interp.e_set, "dict": Interp.e_dict, "fstring": Interp.e_fstring,
    "funcexpr": Interp.e_funcexpr, "func": Interp.e_funcexpr,
    "yield": Interp.e_yield, "yieldfrom": Interp.e_yieldfrom,
    "namedexpr": Interp.e_namedexpr,
}



def make_builtins(interp: Interp):
    """Build the sandbox builtin namespace for an interpreter."""
    I = interp

    def _print(*args, sep=" ", end="\n"):
        if I.replaying:
            for a in args:
                I.diaspore_str(a)  # replay must still evaluate (and may raise)
            return
        # Preserve earlier output if converting a later argument raises.
        try:
            for i, a in enumerate(args):
                if i:
                    I.stdout.write(sep)
                I.stdout.write(I.diaspore_str(a))
            I.stdout.write(end)
        finally:
            I.stdout.flush()

    def _sorted(it, key=None, reverse=False):
        _charge_sized(it)
        items = list(I.as_host_iter(it))
        I.fuel_n(len(items))
        if key is not None:
            dec = [(I.call(key, [x], {}), x) for x in items]
            dec.sort(key=functools.cmp_to_key(lambda a, b: I._cmp(a[0], b[0])),
                     reverse=bool(reverse))
            return [x for _, x in dec]
        items.sort(key=functools.cmp_to_key(I._cmp), reverse=bool(reverse))
        return items

    def _minmax(name, pick, args, key, default):
        if len(args) == 1:
            _charge_sized(args[0])
            items = list(I.as_host_iter(args[0]))
            I.fuel_n(len(items))
            if not items:
                if default is not _MISSING:
                    return default
                raise DiasporeError(make_exc("ValueError", f"{name}() iterable argument is empty"))
        else:
            items = list(args)
        best = items[0]
        bestk = I.call(key, [best], {}) if key else best
        for x in items[1:]:
            xk = I.call(key, [x], {}) if key else x
            if pick(xk, bestk):
                best, bestk = x, xk
        return best

    def _min(*args, key=None, default=_MISSING):
        if not args:
            raise DiasporeError(make_exc("TypeError", "min expected at least 1 argument, got 0"))
        return _minmax("min", lambda a, b: I.compare("lt", a, b), args, key, default)

    def _max(*args, key=None, default=_MISSING):
        if not args:
            raise DiasporeError(make_exc("TypeError", "max expected at least 1 argument, got 0"))
        return _minmax("max", lambda a, b: I.compare("lt", b, a), args, key, default)

    def _sum(it, start=0):
        total = start
        for x in I.as_host_iter(it):
            I.fuel()
            total = I.binop("add", total, x)
        return total

    def _any(it):
        for x in I.as_host_iter(it):
            I.fuel()
            if I.truth(x):
                return True
        return False

    def _all(it):
        for x in I.as_host_iter(it):
            I.fuel()
            if not I.truth(x):
                return False
        return True

    def _map(f, *its):
        return map(lambda *xs: I.call(f, list(xs), {}), *(I.as_host_iter(x) for x in its))

    def _filter(f, it):
        if f is None:
            return filter(I.truth, I.as_host_iter(it))
        return filter(lambda x: I.truth(I.call(f, [x], {})), I.as_host_iter(it))

    def _zip(*its):
        return zip(*(I.as_host_iter(x) for x in its))

    def _enumerate(it, start=0):
        return enumerate(I.as_host_iter(it), start)

    def _reversed(seq):
        if isinstance(seq, (list, tuple, str, range, bytes)):
            return reversed(seq)
        raise DiasporeError(make_exc("TypeError", f"'{type_name(seq)}' object is not reversible"))

    def _next(it, *default):
        try:
            if isinstance(it, DiasporeInstance):
                return _InstanceIter(I, it).__next__()
            return next(it)
        except StopIteration as s:
            if default:
                return default[0]
            args = s.args if s.args else ()
            raise DiasporeError(make_exc("StopIteration", *args)) from None
        except TypeError:
            raise DiasporeError(make_exc(
                "TypeError", f"'{type_name(it)}' object is not an iterator")) from None

    def _isinstance(v, cls):
        classes = list(cls) if isinstance(cls, tuple) else [cls]
        for c in classes:
            if c is OBJECT:
                return True
            if isinstance(c, DiasporeClass):
                if isinstance(v, DiasporeInstance) and v.cls.issub(c):
                    return True
            elif isinstance(c, BuiltinType):
                if c.name == "type":
                    if isinstance(v, (DiasporeClass, BuiltinType)):
                        return True
                elif isinstance(v, DiasporeInstance) and v.prim is not None:
                    if v.cls.prim is not None and (v.cls.prim.name == c.name
                            or (c.name == "int" and v.cls.prim.name == "bool")):
                        return True
                    if c.host_types and isinstance(v.prim, c.host_types):
                        return True
                elif not isinstance(v, DiasporeInstance) and c.host_types \
                        and isinstance(v, c.host_types):
                    return True  # Host types preserve bool's int subclass relationship.
            else:
                raise DiasporeError(make_exc(
                    "TypeError", "isinstance() arg 2 must be a type or tuple of types"))
        return False

    def _issubclass(a, b):
        classes = list(b) if isinstance(b, tuple) else [b]
        if not isinstance(a, (DiasporeClass, BuiltinType)):
            raise DiasporeError(make_exc("TypeError", "issubclass() arg 1 must be a class"))
        if OBJECT in classes:
            return True
        for c in classes:
            if isinstance(a, DiasporeClass) and isinstance(c, DiasporeClass) and a.issub(c):
                return True
            if isinstance(a, BuiltinType) and isinstance(c, BuiltinType):
                if a.name == c.name or (a.name == "bool" and c.name == "int"):
                    return True
        return False

    def _type(v, *rest):
        if rest:  # type(name, bases, ns): dynamic class creation
            if len(rest) != 2:
                raise DiasporeError(make_exc(
                    "TypeError", "type() takes 1 or 3 arguments"))
            bases, ns = rest
            if not isinstance(bases, tuple) or not isinstance(ns, dict) \
                    or not isinstance(v, str):
                raise DiasporeError(make_exc(
                    "TypeError", "type() argument types: (str, tuple, dict)"))
            checked = []
            for b in bases:
                if not isinstance(b, DiasporeClass):
                    raise DiasporeError(make_exc(
                        "TypeError", f"cannot subclass {I.diaspore_repr(b)} in diaspore"))
                checked.append(b)
            return I.build_class(v, checked, dict(ns))
        if isinstance(v, DiasporeInstance):
            return v.cls
        if isinstance(v, (DiasporeClass, BuiltinType)):
            return TYPES["type"]
        if isinstance(v, bool):
            return TYPES["bool"]
        if isinstance(v, int):
            return TYPES["int"]
        if isinstance(v, float):
            return TYPES["float"]
        if isinstance(v, str):
            return TYPES["str"]
        if isinstance(v, bytes):
            return TYPES["bytes"]
        if isinstance(v, list):
            return TYPES["list"]
        if isinstance(v, tuple):
            return TYPES["tuple"]
        if isinstance(v, dict):
            return TYPES["dict"]
        if isinstance(v, set):
            return TYPES["set"]
        if isinstance(v, range):
            return TYPES["range"]
        if isinstance(v, complex):
            return TYPES["complex"]
        if v is None:
            return TYPES["NoneType"]
        return BuiltinType(type_name(v), (), lambda *a: None)

    def _getattr(o, n, *default):
        try:
            return I.get_attr(o, n)
        except DiasporeError as e:
            if default and I.exc_isa(e.value, "AttributeError"):
                return default[0]
            raise

    def _hasattr(o, n):
        try:
            I.get_attr(o, n)
            return True
        except DiasporeError as e:
            if I.exc_isa(e.value, "AttributeError"):
                return False
            raise

    def _callable(v):
        return isinstance(v, (DiasporeFunction, BoundMethod, DiasporeClass, BuiltinType)) or \
            (isinstance(v, DiasporeInstance) and v.cls.lookup("__call__") is not _MISSING) or \
            (not isinstance(v, DiasporeInstance) and callable(v))

    def _hash(v):
        if isinstance(v, DiasporeInstance):
            return id(v) % (2**61)
        return hash(v)

    def _open(path, mode="r"):
        if mode not in ("r", "w", "a", "rb", "wb"):
            raise DiasporeError(make_exc("ValueError", f"invalid mode: '{mode}'"))
        real = I.require_fs("read" if mode in ("r", "rb") else "write", path)
        if "b" in mode:
            return DiasporeFile(open(real, mode))
        return DiasporeFile(open(real, mode, encoding="utf-8"))

    def _exit(code=0):
        raise DiasporeError(make_exc("SystemExit", code))

    def _compile(source, filename="<string>", mode="exec", *a, **k):
        return I.compile_snippet(source, filename, mode)

    def _exec(code, glob=None, loc=None):
        return I.exec_snippet(code, glob, loc, mode="exec")

    def _eval(code, glob=None, loc=None):
        return I.exec_snippet(code, glob, loc, mode="eval")

    def _vars(o=None):
        if o is None:
            raise DiasporeError(make_exc("TypeError", "vars() with no argument unsupported here"))
        if isinstance(o, DiasporeInstance):
            return dict(o.d)
        if isinstance(o, DiasporeModule):
            return dict(o.frame.vars)
        if isinstance(o, DiasporeClass):
            return dict(o.ns)
        raise DiasporeError(make_exc("TypeError", "vars() argument must have __dict__"))

    def _dir(o=None):
        if o is None:
            return []
        if isinstance(o, DiasporeInstance):
            names = set(o.d)
            for c in o.cls.mro:
                names |= set(c.ns)
            return sorted(names)
        if isinstance(o, DiasporeModule):
            return sorted(o.frame.vars)
        if isinstance(o, DiasporeClass):
            names = set()
            for c in o.mro:
                names |= set(c.ns)
            return sorted(names)
        return []

    def _charge_sized(v):
        if isinstance(v, (range, list, tuple, str, bytes, dict, set)):
            I.fuel_n(len(v))

    def _list(v=None):
        if v is None:
            return []
        _charge_sized(v)
        return list(I.as_host_iter(v))

    def _tuple(v=None):
        if v is None:
            return ()
        _charge_sized(v)
        return tuple(I.as_host_iter(v))

    def _set(v=None):
        if v is None:
            return set()
        _charge_sized(v)
        return set(I.as_host_iter(v))

    def _dict(*args, **kwargs):
        out = {}
        if len(args) > 1:
            raise DiasporeError(make_exc(
                "TypeError", f"dict expected at most 1 argument, got {len(args)}"))
        if args:
            v = args[0]
            raw = v.prim if isinstance(v, DiasporeInstance) and v.prim is not None else v
            if isinstance(raw, dict):
                out.update(raw)
            elif isinstance(v, DiasporeInstance) and v.cls.lookup("keys") is not _MISSING:
                for k in I.call_dunder(v, "keys", []):
                    out[I.hashable(k)] = I.get_index(v, k)
            else:
                for pair in I.as_host_iter(v):
                    k, val = list(I.as_host_iter(pair))
                    out[I.hashable(k)] = val
        out.update(kwargs)
        return out

    def _str(v="", *a, **k):
        if a or k:
            raise DiasporeError(make_exc("TypeError", "diaspore str() takes one argument"))
        return I.diaspore_str(v)

    def _repr(v):
        return I.diaspore_repr(v)

    def _bool(v=False):
        return I.truth(v)

    def _int(v=0, *base):
        if isinstance(v, DiasporeInstance):
            for d in ("__int__", "__index__", "__trunc__"):
                if v.cls.lookup(d) is not _MISSING:
                    return I.call_dunder(v, d, [])
            if v.prim is not None and isinstance(v.prim, (int, float, str)):
                return int(v.prim, *base) if base else int(v.prim)
            raise DiasporeError(make_exc(
                "TypeError",
                f"int() argument must be a string or a real number, not '{v.cls.name}'"))
        return int(v, *base) if base else int(v)

    def _float(v=0.0):
        if isinstance(v, DiasporeInstance):
            if v.cls.lookup("__float__") is not _MISSING:
                return I.call_dunder(v, "__float__", [])
            if v.prim is not None and isinstance(v.prim, (int, float)):
                return float(v.prim)
            raise DiasporeError(make_exc(
                "TypeError",
                f"float() argument must be a string or a real number, not '{v.cls.name}'"))
        return float(v)

    def _complex(re=None, im=None):
        try:
            if re is None and im is None:
                return 0j
            if isinstance(re, str) and im is None:
                return complex(re)
            if im is None:
                return complex(re)
            return complex(re, im)
        except (ValueError, TypeError) as ex:
            raise host_exc_to_diaspore(ex) from None

    def _bytes(v=b"", *a):
        if isinstance(v, str):
            if not a:
                raise DiasporeError(make_exc("TypeError", "string argument without an encoding"))
            return v.encode(a[0])
        if isinstance(v, (bytes, int)):
            return bytes(v)
        return bytes(list(I.as_host_iter(v)))

    def _len(v):
        return I.diaspore_len(v)

    def _iter(v):
        return I.as_host_iter(v)

    def _range(*a):
        return range(*a)

    def _slice(*a):
        if len(a) == 1:
            return slice(a[0])
        if len(a) in (2, 3):
            return slice(*a)
        raise DiasporeError(make_exc("TypeError", "slice expected 1-3 arguments"))

    def _input_base(prompt=""):
        if prompt and not I.replaying:
            I.stdout.write(I.diaspore_str(prompt))
            I.stdout.flush()
        line = _hostsys.stdin.readline()
        if not line:
            raise DiasporeError(make_exc("RuntimeError", "EOF when reading a line"))
        return line.rstrip("\n")

    def _input(prompt=""):
        replayed, r = I.replay_next("builtins", "input") if I.durable else (False, None)
        if replayed:
            return r
        v = _input_base(prompt)
        if I.durable:
            I.journal.append({"m": "builtins", "f": "input", "r": v})
        return v

    TYPES = {
        "int": BuiltinType("int", (int,), _int),
        "float": BuiltinType("float", (float,), _float),
        "bool": BuiltinType("bool", (bool,), _bool),
        "str": BuiltinType("str", (str,), _str),
        "bytes": BuiltinType("bytes", (bytes,), _bytes),
        "list": BuiltinType("list", (list,), _list),
        "tuple": BuiltinType("tuple", (tuple,), _tuple),
        "dict": BuiltinType("dict", (dict,), _dict),
        "set": BuiltinType("set", (set,), _set),
        "range": BuiltinType("range", (range,), _range),
        "type": BuiltinType("type", (), _type),
        "NoneType": BuiltinType("NoneType", (type(None),), lambda: None),
        "complex": BuiltinType("complex", (complex,), _complex),
    }

    b = {
        "print": _print, "len": _len, "range": _range, "enumerate": _enumerate,
        "zip": _zip, "map": _map, "filter": _filter, "reversed": _reversed,
        "sorted": _sorted, "sum": _sum, "min": _min, "max": _max,
        "any": _any, "all": _all, "abs": abs, "round": round, "divmod": divmod,
        "pow": pow, "ord": ord, "chr": chr, "hex": hex, "bin": bin, "oct": oct,
        "repr": _repr, "isinstance": _isinstance, "issubclass": _issubclass,
        "callable": _callable, "hash": _hash, "id": id, "iter": _iter, "next": _next,
        "getattr": _getattr, "setattr": lambda o, n, v: I.set_attr(o, n, v),
        "hasattr": _hasattr, "input": _input, "open": _open, "exit": _exit,
        "staticmethod": StaticMethod, "classmethod": ClassMethod,
        "property": lambda fget=None, fset=None, fdel=None, doc=None:
            Property(fget, fset, fdel, doc),
        "super": SUPER_MARKER, "type": _type, "object": OBJECT, "slice": _slice,
        "format": lambda v, spec="": I.format_value(v, spec),
        "ascii": I.diaspore_ascii,
        "vars": lambda o=None: (_vars(o)),
        "dir": lambda o=None: (_dir(o)),
        "compile": _compile, "exec": _exec, "eval": _eval,
        "Ellipsis": Ellipsis,
    }

    def _await_helper(x):
        if isinstance(x, DiasporeGenerator):
            return x
        if isinstance(x, DiasporeInstance) and x.cls.lookup("__await__") is not _MISSING:
            r = I.call_dunder(x, "__await__", [])
            if isinstance(r, DiasporeGenerator):
                return r
            raise DiasporeError(make_exc(
                "TypeError", "__await__() must return a coroutine or generator"))
        raise DiasporeError(make_exc(
            "TypeError",
            f"object {type_name(x)} can't be used in 'await' expression"))

    b[".await"] = _await_helper
    for name, t in TYPES.items():
        if name != "NoneType":
            b[name] = t
    b.update(EXC_CLASSES)
    return b



def make_native_modules(interp: Interp):
    """Build native modules using the interpreter's capabilities and journal."""
    I = interp

    if I.deterministic:
        _clock = [0.0]

        def _time_base():
            _clock[0] = round(_clock[0] + 0.001, 6)
            return _clock[0]

        def _sleep_base(secs):
            _clock[0] = round(_clock[0] + secs, 6)
        _mono_base = _time_base
    else:
        _time_base = _hosttime.time
        _mono_base = _hosttime.monotonic
        _sleep_base = _hosttime.sleep

    time_mod = NativeModule("time", {
        "time": I.journaled("time", "time", _time_base),
        "monotonic": I.journaled("time", "monotonic", _mono_base),
        "sleep": I.journaled("time", "sleep", _sleep_base),
    })

    def _floor(x):
        return _hostmath.floor(x)

    def _ceil(x):
        return _hostmath.ceil(x)

    math_mod = NativeModule("math", {
        "pi": _hostmath.pi, "e": _hostmath.e, "inf": _hostmath.inf, "nan": _hostmath.nan,
        "sqrt": _hostmath.sqrt, "floor": _floor, "ceil": _ceil, "fabs": _hostmath.fabs,
        "pow": _hostmath.pow, "log": _hostmath.log, "log2": _hostmath.log2,
        "log10": _hostmath.log10, "exp": _hostmath.exp, "sin": _hostmath.sin,
        "cos": _hostmath.cos, "tan": _hostmath.tan, "hypot": _hostmath.hypot,
        "fmod": _hostmath.fmod, "isnan": _hostmath.isnan, "isinf": _hostmath.isinf,
    })

    def _check_jsonable(v, seen=None):
        if isinstance(v, dict):
            for k in v:
                if not isinstance(k, str):
                    raise DiasporeError(make_exc("TypeError", "json keys must be str"))
            for x in v.values():
                _check_jsonable(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                _check_jsonable(x)
        elif v is None or isinstance(v, (str, int, float, bool)):
            pass
        else:
            raise DiasporeError(make_exc(
                "TypeError", f"Object of type {type_name(v)} is not JSON serializable"))

    def _dumps(v, indent=None):
        _check_jsonable(v)
        return _hostjson.dumps(v, indent=indent)

    def _loads(s):
        try:
            return _hostjson.loads(s)
        except _hostjson.JSONDecodeError as e:
            raise DiasporeError(make_exc("ValueError", f"invalid JSON: {e.msg}")) from None

    json_mod = NativeModule("json", {"dumps": _dumps, "loads": _loads})

    rng = _hostrandom.Random(0 if I.deterministic else None)

    def _shuffle(lst):
        if not isinstance(lst, list):
            raise DiasporeError(make_exc("TypeError", "shuffle() requires a list"))
        # journal the resulting order so replay reproduces the mutation
        order = I.effect("random", "shuffle",
                         lambda: (rng.shuffle(lst), list(lst))[1]) \
            if I.durable else (rng.shuffle(lst), lst)[1]
        lst[:] = order

    random_mod = NativeModule("random", {
        "seed": I.journaled("random", "seed", lambda n=None: rng.seed(n)),
        "random": I.journaled("random", "random", rng.random),
        "randint": I.journaled("random", "randint", rng.randint),
        "choice": I.journaled("random", "choice", lambda seq: rng.choice(list(seq))),
        "shuffle": _shuffle,
    })

    def _sys_exit(code=0):
        raise DiasporeError(make_exc("SystemExit", code))

    def _stderr_write(s):
        if not I.replaying:
            I.stderr.write(s)

    sys_mod = NativeModule("sys", {
        "argv": list(I.argv),
        "exit": _sys_exit,
        "platform": "diaspore-py",
        "stderr_write": _stderr_write,
    })

    sockets: dict[int, _socket.socket] = {}
    next_id = [1]

    def _reg(sock) -> int:
        sid = next_id[0]
        next_id[0] += 1
        sockets[sid] = sock
        return sid

    def _get_sock(sid):
        if sid not in sockets:
            raise DiasporeError(make_exc("OSError", f"bad socket id: {sid}"))
        return sockets[sid]

    def _listen(host, port):
        I.require_net("listen", host, port)
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.listen(64)
        return _reg(s)

    def _connect(host, port):
        I.require_net("connect", host, port)
        return _reg(_socket.create_connection((host, port)))

    def _accept(lid):
        I.require_cap("net")
        conn, _addr = _get_sock(lid).accept()
        return _reg(conn)

    def _recv(cid, n):
        I.require_cap("net")
        return _get_sock(cid).recv(n)

    def _send(cid, data):
        I.require_cap("net")
        if not isinstance(data, bytes):
            raise DiasporeError(make_exc("TypeError", "net.send() requires bytes"))
        _get_sock(cid).sendall(data)
        return len(data)

    def _close(sid):
        I.require_cap("net")
        _get_sock(sid).close()
        del sockets[sid]

    def _peer(cid):
        I.require_cap("net")
        host, port = _get_sock(cid).getpeername()
        return f"{host}:{port}"

    net_mod = NativeModule("net", {
        "listen": _listen, "connect": _connect, "accept": _accept,
        "recv": _recv, "send": _send, "close": _close, "peer": _peer,
    })

    def _flow_suspend(payload=None):
        if not I.durable:
            raise DiasporeError(make_exc(
                "RuntimeError",
                "flow.suspend() requires a durable run (diaspore run --durable)"))
        replayed, r = I.replay_next("flow", "suspend")
        if replayed:
            return r
        raise SuspendSig(I.journal_value(payload, "flow.suspend"))

    flow_mod = NativeModule("flow", {"suspend": _flow_suspend})

    return {"time": time_mod, "math": math_mod, "json": json_mod,
            "random": random_mod, "sys": sys_mod, "net": net_mod,
            "flow": flow_mod,
            "re": make_re_module(I), "tasks": make_tasks_module(I)}


# Reject constructs outside the regex subset (docs/spec.md section 11.8).
def _re_validate(pattern: str) -> None:
    i, n = 0, len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            if i + 1 < n and pattern[i + 1] in "123456789":
                raise DiasporeError(make_exc(
                    "ValueError", "diaspore re: backreferences are not supported"))
            if i + 1 < n and pattern[i + 1] in "AZ":
                raise DiasporeError(make_exc(
                    "ValueError", "diaspore re: \\A/\\Z are not supported (use ^ and $)"))
            i += 2
            continue
        if pattern.startswith("(?", i):
            j = i + 2
            flags = ""
            while j < n and pattern[j] in "ims":
                flags += pattern[j]
                j += 1
            ok = (j < n and pattern[j] == ":") or (flags and j < n and pattern[j] == ")")
            if not ok:
                raise DiasporeError(make_exc(
                    "ValueError",
                    "diaspore re: only (?:...) groups and (?ims) flags are supported"))
            i = j + 1
            continue
        if ch in "*+?" and i + 1 < n and pattern[i + 1] == "+":
            raise DiasporeError(make_exc(
                "ValueError", "diaspore re: possessive quantifiers are not supported"))
        i += 1


def make_re_module(I: Interp) -> NativeModule:
    """Build the native regular-expression module with subset validation."""
    import re as hre

    IGNORECASE, MULTILINE, DOTALL = 2, 8, 16
    _cache: dict[tuple[str, int], object] = {}

    def _compile(pattern, flags=0):
        if not isinstance(pattern, str):
            raise DiasporeError(make_exc("TypeError", "re pattern must be a string"))
        key = (pattern, flags)
        if key not in _cache:
            _re_validate(pattern)
            host_flags = ((hre.IGNORECASE if flags & IGNORECASE else 0)
                          | (hre.MULTILINE if flags & MULTILINE else 0)
                          | (hre.DOTALL if flags & DOTALL else 0))
            try:
                _cache[key] = hre.compile(pattern, host_flags)
            except hre.error as e:
                raise DiasporeError(make_exc("ValueError",
                                         f"diaspore re: invalid pattern: {e.msg}")) from None
        return _cache[key]

    def _wrap(m):
        if m is None:
            return None
        return NativeObject("re.Match", {
            "group": lambda n=0: m.group(n),
            "groups": lambda: m.groups(),
            "start": lambda n=0: m.start(n),
            "end": lambda n=0: m.end(n),
            "span": lambda n=0: tuple(m.span(n)),
        })

    def _check_repl(repl):
        i = 0
        while i < len(repl):
            if repl[i] == "\\":
                if i + 1 < len(repl) and repl[i + 1] == "g":
                    raise DiasporeError(make_exc(
                        "ValueError", "diaspore re: \\g<...> replacements are not supported"))
                i += 2
            else:
                i += 1

    def _sub(pattern, repl, s, count=0, flags=0):
        if not isinstance(repl, str):
            raise DiasporeError(make_exc("TypeError", "diaspore re: repl must be a string"))
        _check_repl(repl)
        return _compile(pattern, flags).sub(repl, s, count)

    return NativeModule("re", {
        "IGNORECASE": IGNORECASE, "MULTILINE": MULTILINE, "DOTALL": DOTALL,
        "search": lambda p, s, flags=0: _wrap(_compile(p, flags).search(s)),
        "match": lambda p, s, flags=0: _wrap(_compile(p, flags).match(s)),
        "fullmatch": lambda p, s, flags=0: _wrap(_compile(p, flags).fullmatch(s)),
        "findall": lambda p, s, flags=0: _compile(p, flags).findall(s),
        "split": lambda p, s, maxsplit=0, flags=0: _compile(p, flags).split(s, maxsplit),
        "sub": _sub,
        "escape": hre.escape,
    })


class _ClosedChannel(Exception):
    pass


def make_tasks_module(I: Interp) -> NativeModule:
    """Build thread-backed tasks, channels, and locks with GIL semantics."""

    def _spawn(fn, *args):
        I.require_cap("spawn")
        state = {"done": False, "value": None, "error": None}
        finished = threading.Event()

        def _run():
            try:
                state["value"] = I.call(fn, list(args), {})
            except (DiasporeError, DiasporeTrap) as e:
                state["error"] = e
            except BaseException as e:
                state["error"] = host_exc_to_diaspore(e)
            finally:
                state["done"] = True
                finished.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        def _join():
            finished.wait()
            if state["error"] is not None:
                raise state["error"]
            return state["value"]

        return NativeObject("tasks.Task", {
            "join": _join,
            "done": lambda: state["done"],
        })

    def _channel(capacity=0):
        I.require_cap("spawn")
        if not isinstance(capacity, int) or capacity < 0:
            raise DiasporeError(make_exc("ValueError", "channel capacity must be >= 0"))
        items = []
        lock = threading.Lock()
        not_empty = threading.Condition(lock)
        not_full = threading.Condition(lock)
        closed = [False]

        def _send(v):
            with lock:
                while capacity and len(items) >= capacity and not closed[0]:
                    not_full.wait()
                if closed[0]:
                    raise DiasporeError(make_exc("RuntimeError", "send on closed channel"))
                items.append(v)
                not_empty.notify()

        def _recv():
            with lock:
                while not items and not closed[0]:
                    not_empty.wait()
                if items:
                    v = items.pop(0)
                    not_full.notify()
                    return v
                raise _ClosedChannel()

        def _recv_diaspore():
            try:
                return _recv()
            except _ClosedChannel:
                raise DiasporeError(make_exc(
                    "RuntimeError", "recv on closed channel")) from None

        def _close():
            with lock:
                closed[0] = True
                not_empty.notify_all()
                not_full.notify_all()

        def _len():
            with lock:
                return len(items)

        return NativeObject("tasks.Channel", {
            "send": _send,
            "recv": _recv_diaspore,
            "close": _close,
            "len": _len,
            ".raw_recv": _recv,  # unspellable: iteration hook
        })

    def _lock():
        I.require_cap("spawn")
        hl = threading.Lock()
        return NativeObject("tasks.Lock", {
            "acquire": lambda: (hl.acquire(), None)[1],
            "release": lambda: (hl.release(), None)[1],
            "__enter__": lambda: (hl.acquire(), None)[1],
            "__exit__": lambda *a: (hl.release(), False)[1],
        })

    return NativeModule("tasks", {
        "spawn": _spawn, "channel": _channel, "lock": _lock,
    })


class _ChannelIter:
    """Iterate over channel values until the channel closes."""

    def __init__(self, chan: NativeObject):
        """Store the channel's internal receive operation."""
        self.recv = chan.methods[".raw_recv"]

    def __iter__(self):
        """Return this channel iterator."""
        return self

    def __next__(self):
        """Receive a value or stop when the channel closes."""
        try:
            return self.recv()
        except _ClosedChannel:
            raise StopIteration from None



def bundle_digest(bundle: dict) -> str:
    """Return the canonical bundle SHA-256 digest (docs/spec.md section 14)."""
    text = _hostjson.dumps(bundle, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Completed:
    """A durable run finished normally."""

    def __init__(self, code: int):
        """Store the completed run's exit code."""
        self.code = code

    def __repr__(self):
        """Return a representation containing the exit code."""
        return f"Completed(code={self.code})"


class Suspended:
    """A durable run paused at flow.suspend(); resume with Machine.resume(snapshot)."""

    def __init__(self, payload, snapshot: dict):
        """Store the suspension payload and portable snapshot."""
        self.payload = payload
        self.snapshot = snapshot

    def __repr__(self):
        """Return a representation containing the suspension payload."""
        return f"Suspended(payload={self.payload!r})"


class Machine:
    """Embed a Diaspore sandbox with validated bundles and explicit authority."""

    def __init__(self, bundle, argv=None, bindings=None, caps=None,
                 max_steps=None, max_depth=1000, stdout=None, stderr=None,
                 validate=True, durable=False, deterministic=False, policy=None):
        """Configure the sandbox and optionally validate its bundle.

        Args:
            bundle: Decoded bundle to execute.
            argv: Arguments exposed through the sandbox sys module.
            bindings: Host modules and callables exposed to the sandbox.
            caps: Granted capability names.
            max_steps: Optional execution step budget.
            max_depth: Maximum sandbox call depth.
            stdout: Output stream, defaulting to host standard output.
            stderr: Error stream, defaulting to host standard error.
            validate: Whether to validate the bundle structure.
            durable: Whether to enable effect journaling and suspension.
            deterministic: Whether to require deterministic native behavior.
            policy: Optional scoped capability grants.
        """
        if validate:
            from .validator import validate_bundle
            validate_bundle(bundle)
        self._kw = dict(bundle=bundle, argv=argv, bindings=bindings, caps=caps,
                        max_steps=max_steps, max_depth=max_depth,
                        stdout=stdout, stderr=stderr, durable=durable,
                        deterministic=deterministic, policy=policy)
        self.interp = self._make_interp()

    def _make_interp(self, journal=None) -> Interp:
        k = self._kw
        return Interp(k["bundle"], argv=k["argv"], bindings=k["bindings"],
                      caps=k["caps"], max_steps=k["max_steps"],
                      max_depth=k["max_depth"], stdout=k["stdout"],
                      stderr=k["stderr"], durable=k["durable"],
                      deterministic=k["deterministic"], policy=k["policy"],
                      journal=journal)

    def run(self) -> int:
        """Execute the entry module and return its process exit code."""
        return self.interp.run()

    def call(self, module: str, func: str, *args):
        """Call a sandbox module function from the host."""
        return self.interp.call_in(module, func, *args)

    @property
    def steps(self) -> int:
        """Return the execution steps charged by the current interpreter."""
        return self.interp.steps


    def run_durable(self) -> Completed | Suspended:
        """Start a durable run (requires Machine(durable=True))."""
        self.interp = self._make_interp()
        return self._drive(self.interp)

    def resume(self, snapshot: dict, send=None) -> Completed | Suspended:
        """Replay a snapshot and resume execution with a supplied value.

        Args:
            snapshot: Portable snapshot from a suspended durable run.
            send: Value returned by the pending flow.suspend call.

        Returns:
            A completed result or the next suspension.
        """
        if not isinstance(snapshot, dict) or snapshot.get("diaspore_flow") != 1:
            raise ValueError("not a diaspore flow snapshot")
        journal = snapshot.get("journal")
        if not isinstance(journal, list) or not all(
                isinstance(e, dict) and isinstance(e.get("m"), str)
                and isinstance(e.get("f"), str) and "r" in e for e in journal):
            raise ValueError("malformed snapshot: journal must be a list of "
                             "{m, f, r} effect records")
        argv = snapshot.get("argv")
        if argv is not None and not (isinstance(argv, list)
                                     and all(isinstance(a, str) for a in argv)):
            raise ValueError("malformed snapshot: argv must be a list of strings")
        expect = snapshot.get("bundle_sha256")
        actual = bundle_digest(self._kw["bundle"])
        if expect and expect != actual:
            raise ValueError(
                f"snapshot was taken from a different bundle "
                f"(snapshot {expect[:12]}..., this bundle {actual[:12]}...)")
        journal = list(snapshot["journal"])
        journal.append({"m": "flow", "f": "suspend",
                        "r": None if send is None else send})
        self.interp = self._make_interp(journal=journal)
        return self._drive(self.interp)

    def _drive(self, interp: Interp) -> Completed | Suspended:
        if not self._kw["durable"]:
            raise ValueError("durable API requires Machine(durable=True)")
        try:
            code = interp.run()
        except SuspendSig as s:
            interp.stdout.flush()
            snapshot = {
                "diaspore_flow": 1,
                "bundle_sha256": bundle_digest(self._kw["bundle"]),
                "argv": list(interp.argv),
                "journal": list(interp.journal),
            }
            return Suspended(s.payload, snapshot)
        return Completed(code)
