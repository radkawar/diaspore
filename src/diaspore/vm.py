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
import sys as _hostsys
import threading
import time as _hosttime
import weakref
from types import MappingProxyType, SimpleNamespace

from .vm_async import AsyncGeneratorOperation, AsyncYield
from .vm_types import (
    Cell,
    CellNamespace,
    ClassNamespace,
    Code,
    Descriptor,
    Formatter,
    FrameView,
    GenericAlias,
    NoPositionals,
    Traceback,
    Union,
    VersionInfo,
    free_bindings,
    percent_value,
)

_DICT_KEYS = type({}.keys())
_DICT_VALUES = type({}.values())
_DICT_ITEMS = type({}.items())
_DICT_VIEWS = (_DICT_KEYS, _DICT_VALUES, _DICT_ITEMS)
_STRUCTSEQ_FIELDS = {
    type(_hostsys.float_info): (
        "max", "max_exp", "max_10_exp", "min", "min_exp", "min_10_exp",
        "dig", "mant_dig", "epsilon", "radix", "rounds"),
    type(_hostsys.int_info): (
        "bits_per_digit", "sizeof_digit", "default_max_str_digits", "str_digits_check_threshold"),
    type(_hostsys.hash_info): (
        "width", "modulus", "inf", "nan", "imag", "algorithm", "hash_bits", "seed_bits", "cutoff"),
}


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
        self.qualname = ns.pop("__qualname__", name)
        self.bases = list(bases) if bases else ([_OBJECT] if _OBJECT is not None else [])
        self.prim = prim or next(
            (b if hasattr(b, "host_types") and b.name != "type" else b.prim
             for b in self.bases if getattr(b, "prim", None) is not None
             or hasattr(b, "host_types") and b.name != "type"), None)
        self.metaclass = None
        self.subclasses = []
        self.ns = ns
        self.has_dict = True
        if "__eq__" in ns and "__hash__" not in ns:
            ns["__hash__"] = None
        self.mro = c3_mro(self)
        for base in self.bases:
            base.subclasses.append(self)

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

    __slots__ = ("cls", "d", "prim", "interp", "slots")

    def __init__(self, cls, interp=None):
        """Initialize an instance with an empty attribute dictionary."""
        self.cls = cls
        self.prim = None
        self.d = {}
        self.slots = None
        self.interp = interp

    def __hash__(self):
        """Dispatch sandbox hashing for host-backed dictionaries and sets."""
        method = self.cls.lookup("__hash__")
        if method is None:
            raise DiasporeError(make_exc(
                "TypeError", f"unhashable type: '{self.cls.name}'"))
        if method is _MISSING or method is object.__hash__ or self.interp is None:
            return object.__hash__(self)
        value = self.interp.call_dunder(self, "__hash__", [])
        if not isinstance(value, int):
            raise DiasporeError(make_exc("TypeError", "__hash__ method should return an integer"))
        return hash(value)

    def __eq__(self, other):
        """Dispatch sandbox equality when host containers compare keys."""
        if self.interp is None:
            return self is other
        return self.interp.diaspore_eq(self, other)


class DiasporeFunction:
    """Represent a compiled function with its defining scope and defaults."""

    def __init__(self, interp, node, closure, module):
        """Capture a function's interpreter, IR, closure, and module."""
        self.interp = interp
        self.name = node["name"]
        self.install_node(node)
        self.closure = closure
        self.module = module
        self.defclass = None
        self.defaults = None      # filled at definition time
        self.kwdefaults = None
        self.attrs = {}
        self.metadata = {"__doc__": None, "__module__": None, "__qualname__": self.name,
                         "__annotations__": {}, "__defaults__": None, "__kwdefaults__": None}
        self.cells = free_bindings(self)
        self.closure_value = tuple(cell for _, cell in self.cells) or None
        self.code = None

    def install_node(self, node):
        """Update the execution metadata when installing compatible IR code."""
        self.node = node
        p = node["params"]
        self.params = {"args": p.get("args", []), "defaults": p.get("defaults", []),
                       "vararg": p.get("vararg"), "kwonly": p.get("kwonly", []),
                       "kwdefaults": p.get("kwdefaults", []), "kwarg": p.get("kwarg"),
                       "posonly": p.get("posonly", 0)}
        self.localset = frozenset(node.get("locals", ()))
        self.gdecl = frozenset(node.get("gdecl", ()))
        self.ndecl = frozenset(node.get("ndecl", ()))
        self.gen = node.get("gen", False) or node.get("coro", False) or node.get("asyncgen", False)


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

    __slots__ = ("fget", "fset", "fdel", "doc", "doc_from_getter")

    def __init__(self, fget=None, fset=None, fdel=None, doc=None):
        """Store property accessors and documentation."""
        self.fget = fget
        self.fset = fset
        self.fdel = fdel
        self.doc = doc
        self.doc_from_getter = doc is None


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
        ns.setdefault("__name__", name)
        ns.setdefault("__doc__", None)


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

    __slots__ = ("kind", "methods", "props", "__weakref__")

    def __init__(self, kind: str, methods: dict, props: dict | None = None):
        """Store the object kind and its exposed members."""
        self.kind = kind
        self.methods = methods
        self.props = props or {}


class Frame:
    """Track bindings, lexical scope, and execution context."""

    __slots__ = ("vars", "parent", "localset", "gdecl", "ndecl", "is_class",
                 "globals", "gen", "line", "fname", "module_name", "defclass", "self_",
                 "back", "code", "temps", "annotation_scope")

    def __init__(self, vars, parent, localset, gdecl, ndecl, is_class,
                 globals_, fname, module_name):
        """Initialize bindings, scope declarations, and source context."""
        self.vars = vars
        self.parent = parent
        self.localset = localset      # None => non-strict scope (module/class)
        self.gdecl = gdecl
        self.ndecl = ndecl
        self.is_class = is_class
        self.annotation_scope = False
        self.globals = globals_
        self.gen = None
        self.line = 0
        self.fname = fname
        self.module_name = module_name
        self.defclass = None
        self.self_ = None
        self.back = None
        self.code = None
        self.temps = {}


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

    __slots__ = ("in_q", "out_q", "state", "interp")

    def __init__(self):
        """Create bounded channels and shared generator lifecycle state."""
        self.in_q = queue.Queue(1)
        self.out_q = queue.Queue(1)
        self.state = {"started": False, "done": False, "closing": False}

    def _yield(self, value):
        self.out_q.put(("yield", value))
        token = self.in_q.get()
        self.interp.frame_state.ident = self.state["ident"]
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
    token = chan.in_q.get()
    if token == "close":
        return
    interp.frame_state.ident = chan.state["ident"]
    try:
        if token[0] in ("throw", "genexit"):
            raise DiasporeError(token[1])
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
        """Create the generator channel and abandonment finalizer."""
        self.fn = fn
        self.chan = _GenChannel()
        self.chan.interp = interp
        frame.gen = self.chan
        self._frame = frame
        self._thread = None
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
            self._frame = None
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
        interp = self.fn.interp
        st["ident"] = getattr(interp.frame_state, "ident", threading.get_ident())
        if not st["started"]:
            thread = threading.Thread(
                target=_gen_body, args=(interp, self.fn.node["body"], self._frame, self.chan),
                daemon=True)
            thread.start()
            self._thread = thread
            self._frame = None
            st["started"] = True
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
    ("EOFError", "Exception"),
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
    ("UnicodeTranslateError", "UnicodeError"),
    ("Warning", "Exception"),
    ("UserWarning", "Warning"),
    ("DeprecationWarning", "Warning"),
    ("PendingDeprecationWarning", "Warning"),
    ("SyntaxWarning", "Warning"),
    ("RuntimeWarning", "Warning"),
    ("FutureWarning", "Warning"),
    ("ImportWarning", "Warning"),
    ("UnicodeWarning", "Warning"),
    ("BytesWarning", "Warning"),
    ("ResourceWarning", "Warning"),
    ("EncodingWarning", "Warning"),
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


def _exc_init(self, /, *args, **kwargs):
    self.d["args"] = tuple(args)
    if self.cls.issub(EXC_CLASSES["StopIteration"]):
        self.d["value"] = args[0] if args else None
    if self.cls.issub(EXC_CLASSES["SyntaxError"]):
        syntax = SyntaxError(*args)
        for name in ("msg", "filename", "lineno", "offset", "text",
                     "end_lineno", "end_offset", "print_file_and_line"):
            self.d[name] = getattr(syntax, name)
    if self.cls.name in ("UnicodeEncodeError", "UnicodeDecodeError") and len(args) == 5:
        self.d.update(zip(("encoding", "object", "start", "end", "reason"), args))
    elif self.cls.name == "UnicodeTranslateError" and len(args) == 4:
        self.d.update(zip(("object", "start", "end", "reason"), args))
    if self.cls.issub(EXC_CLASSES["OSError"]):
        self.d.update(errno=args[0] if len(args) > 1 else None,
                      strerror=args[1] if len(args) > 1 else None,
                      filename=args[2] if len(args) > 2 else None,
                      filename2=args[4] if len(args) > 4 else None)
        if len(args) > 2:
            self.d["args"] = tuple(args[:2])
    allowed = ("name", "path") if self.cls.issub(EXC_CLASSES["ImportError"]) else (
        ("name", "obj") if self.cls.issub(EXC_CLASSES["AttributeError"]) else
        ("name",) if self.cls.issub(EXC_CLASSES["NameError"]) else ())
    for key in allowed:
        self.d[key] = kwargs.pop(key, None)
    if kwargs:
        raise TypeError(f"{self.cls.name}() takes no keyword arguments")


OBJECT = DiasporeClass("object", [], {})
_OBJECT = OBJECT
_OBJECT_SETATTR = object()
_OBJECT_DELATTR = object()
OBJECT.ns["__setattr__"] = _OBJECT_SETATTR
OBJECT.ns["__delattr__"] = _OBJECT_DELATTR
OBJECT.ns["__hash__"] = object.__hash__
OBJECT.has_dict = False


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
    _exc_init(inst, *args)
    return inst


def host_exc_to_diaspore(e: BaseException) -> DiasporeError:
    """Translate a host exception into the sandbox exception hierarchy."""
    value = make_exc(_map_exc_name(type(e)), *e.args)
    if isinstance(e, OSError):
        for name in ("errno", "strerror", "filename", "filename2"):
            value.d[name] = getattr(e, name, None)
    return DiasporeError(value)


def _map_exc_name(t: type) -> str:
    for c in t.__mro__:
        if c.__name__ in EXC_CLASSES:
            return c.__name__
    return "RuntimeError"



STR_METHODS = {"upper", "lower", "title", "capitalize", "casefold", "strip", "lstrip",
               "rstrip", "split", "rsplit", "splitlines", "join", "replace", "find",
               "rfind", "index", "rindex", "count", "startswith", "endswith", "isdigit",
               "isalpha", "isalnum", "isspace", "zfill", "ljust", "rjust", "encode",
               "partition", "rpartition", "removeprefix", "removesuffix", "center",
               "islower", "isupper", "swapcase", "isascii", "isdecimal", "isnumeric",
               "isidentifier", "isprintable", "istitle", "expandtabs", "translate",
               "maketrans", "format", "format_map"}
LIST_METHODS = {"append", "extend", "insert", "pop", "remove", "clear", "index",
                "count", "reverse", "sort", "copy"}
DICT_METHODS = {"get", "keys", "values", "items", "pop", "popitem", "update", "setdefault",
                "clear", "copy", "fromkeys"}
SET_METHODS = {"add", "remove", "discard", "clear", "copy", "union", "intersection",
               "difference", "symmetric_difference", "issubset", "issuperset",
               "isdisjoint", "pop", "update", "intersection_update", "difference_update",
               "symmetric_difference_update"}
FROZENSET_METHODS = {"copy", "union", "intersection", "difference",
                     "symmetric_difference", "issubset", "issuperset", "isdisjoint"}
TUPLE_METHODS = {"count", "index"}
BYTES_METHODS = (STR_METHODS - {"encode", "format", "format_map", "isidentifier",
                              "isdecimal", "isnumeric", "isprintable", "casefold"}) | {
    "decode", "hex", "fromhex"}
BYTEARRAY_METHODS = BYTES_METHODS | {"append", "extend", "insert", "pop", "remove",
                                    "clear", "reverse", "copy"}
MEMORYVIEW_METHODS = {"tobytes", "tolist", "hex", "cast", "toreadonly", "release",
                      "__enter__", "__exit__"}
MEMORYVIEW_ATTRS = {"obj", "nbytes", "readonly", "format", "itemsize", "ndim",
                    "shape", "strides", "suboffsets", "c_contiguous", "f_contiguous",
                    "contiguous"}
_HOST_METHODS = {
    str: STR_METHODS, list: LIST_METHODS, dict: DICT_METHODS, set: SET_METHODS,
    frozenset: FROZENSET_METHODS, tuple: TUPLE_METHODS, bytes: BYTES_METHODS,
    bytearray: BYTEARRAY_METHODS, memoryview: MEMORYVIEW_METHODS,
    MappingProxyType: {"keys", "values", "items", "get", "copy"},
    _DICT_KEYS: {"isdisjoint"}, _DICT_ITEMS: {"isdisjoint"},
}
_FAST_CODECS = {
    "utf-8": "utf-8", "utf8": "utf-8", "utf": "utf-8", "u8": "utf-8",
    "cp65001": "utf-8", "ascii": "ascii", "us-ascii": "ascii", "646": "ascii",
    "latin-1": "latin-1", "latin1": "latin-1", "latin": "latin-1",
    "l1": "latin-1", "iso8859-1": "latin-1", "iso-8859-1": "latin-1",
    "iso-8859-1-1987": "latin-1", "cp819": "latin-1", "ibm819": "latin-1",
    "8859": "latin-1",
}

_BINOPS = {
    "add": _op.add, "sub": _op.sub, "mul": _op.mul, "div": _op.truediv,
    "floordiv": _op.floordiv, "mod": _op.mod, "pow": _op.pow,
    "lshift": _op.lshift, "rshift": _op.rshift,
    "bitand": _op.and_, "bitor": _op.or_, "bitxor": _op.xor,
    "matmul": _op.matmul, "divmod": divmod,
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
_CMP_SYM = {"lt": "<", "le": "<=", "gt": ">", "ge": ">="}


class BuiltinType:
    """Represent a primitive type as a first-class sandbox value."""

    def __init__(self, name, host_types, ctor):
        """Store the type name, matching host types, and constructor."""
        self.name = name
        self.qualname = name
        self.host_types = host_types
        self.ctor = ctor
        self.ns = {}
        self.prim = None
        self.bases = [OBJECT] if name != "object" else []
        self.mro = [self] + self.bases
        self.subclasses = []

    def lookup(self, name):
        """Resolve only attributes installed in the safe type namespace."""
        for cls in self.mro:
            if name in cls.ns:
                return cls.ns[name]
        return _MISSING

    def issub(self, other):
        """Return whether this builtin type inherits from another type."""
        return other in self.mro
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
        self.stdlib_id = bundle.get("stdlib")
        self.stdlib_ir = {}
        if "stdlib" in bundle:
            _check_stdlib_reference(self.stdlib_id)
            from .stdlib_store import get_stdlib_id
            installed_id = get_stdlib_id()
            if self.stdlib_id != installed_id:
                raise ValueError(
                    f"stdlib mismatch: bundle references {self.stdlib_id}, "
                    f"installed store is {installed_id}")
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
        self.recursion_limit = max_depth
        self.steps = 0
        self.depth = 0
        self.stdout = stdout or _hostsys.stdout
        self.stderr = stderr or _hostsys.stderr
        self.modules: dict[str, object] = {}
        self.exc_stack: list[DiasporeInstance] = []
        self._repring: set[int] = set()
        self.native_types = {}
        self.builtins = make_builtins(self)
        self.frame_state = threading.local()
        self.main_ident = threading.get_ident()
        self.pending_interrupt = False
        self.closed = False
        self.suspended = False
        self.formatter = Formatter(self)
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


    def _check_interrupt(self):
        if self.pending_interrupt and getattr(
                self.frame_state, "ident", threading.get_ident()) == self.main_ident:
            self.pending_interrupt = False
            raise DiasporeError(make_exc("KeyboardInterrupt"))

    def fuel(self):
        """Charge one execution step and enforce the step budget."""
        if self.pending_interrupt:
            self._check_interrupt()
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

    def close(self):
        """Finish a process by joining sandbox threads and running exit callbacks."""
        if self.closed:
            return
        self.closed = True
        try:
            if self.suspended:
                return
            for module_name, hook_name in (
                    ("threading", "_shutdown"), ("atexit", "_run_exitfuncs")):
                module = self.modules.get(module_name)
                if module is None:
                    continue
                namespace = module.frame.vars if isinstance(module, DiasporeModule) else module.ns
                hook = namespace.get(hook_name)
                if hook is not None:
                    try:
                        self.call(hook, [], {})
                    except DiasporeError as error:
                        self.print_traceback(error)
        finally:
            self.close_files()
            self.close_sockets()


    def print_traceback(self, e: DiasporeError):
        """Write a sandbox exception traceback to standard error."""
        w = self.stderr.write
        w("Traceback (most recent call last):\n")
        for mod, fname, line in reversed(e.tb):
            m = self._module_ir(mod) or {}
            w(f'  File "{m.get("file", mod)}", line {line}, in {fname}\n')
            src = m.get("src")
            if src and 1 <= line <= len(src):
                w(f"    {src[line - 1].strip()}\n")
        v = e.value
        name = self._exception_type_name(v.cls) if isinstance(v, DiasporeInstance) else type_name(v)
        msg = self.exc_str(v) if isinstance(v, DiasporeInstance) else self.diaspore_str(v)
        w(f"{name}: {msg}\n" if msg else f"{name}\n")


    def _exception_type_name(self, cls):
        module = cls.ns.get("__module__", "builtins")
        return (f"{module}.{cls.qualname}" if module not in ("builtins", "__main__")
                else cls.qualname)

    def _module_ir(self, name):
        module = self.bundle["modules"].get(name)
        if module is not None or self.stdlib_id is None:
            return module
        if name not in self.stdlib_ir:
            from .stdlib_store import get_stdlib_module
            self.stdlib_ir[name] = get_stdlib_module(name)
        return self.stdlib_ir[name]

    def import_module(self, name):
        """Load and cache a compiled or native module."""
        if name in self.modules:
            return self.modules[name]
        m = self._module_ir(name)
        if m is not None:
            parent = None
            if "." in name:  # packages: ancestors execute first
                parent = self.import_module(name.rsplit(".", 1)[0])
            frame = Frame({}, None, None, frozenset(), frozenset(), False,
                          None, "<module>", name)
            frame.globals = frame.vars
            frame.vars["__name__"] = "__main__" if name == self.bundle["main"] else name
            frame.vars["__doc__"] = None
            frame.vars["__package__"] = name.rpartition(".")[0]
            frame.vars["__builtins__"] = self.builtins
            frame.vars["__file__"] = m["file"]
            if m["file"].replace("\\", "/").endswith("/__init__.py"):
                frame.vars["__package__"] = name
                frame.vars["__path__"] = [m["file"].rsplit("/", 1)[0]]
            frame.vars["__loader__"] = None
            frame.vars["__spec__"] = None
            mod = DiasporeModule(name, frame)
            self.modules[name] = mod
            if name == self.bundle["main"]:
                self.modules["__main__"] = mod
            try:
                self.exec_block(m["body"], frame)
            except DiasporeError as e:
                self.modules.pop(name, None)
                if name == self.bundle["main"]:
                    self.modules.pop("__main__", None)
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
        if id_ == ".builtins":
            return self.natives["builtins"]
        if id_.startswith("."):
            scope = fr
            while scope is not None:
                if id_ in scope.temps:
                    return scope.temps[id_]
                scope = scope.parent
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
                if (f is fr or not f.is_class or fr.annotation_scope) and id_ in f.vars:
                    return f.vars[id_]
                f = f.parent
            if fr.globals is not None and id_ in fr.globals:
                return fr.globals[id_]
        if id_ in self.builtins:
            return self.builtins[id_]
        raise DiasporeError(make_exc("NameError", f"name '{id_}' is not defined"))

    def store_name(self, fr: Frame, id_: str, v):
        """Store a name in its declared scope."""
        if id_.startswith("."):
            fr.temps[id_] = v
            return
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
        previous = getattr(self.frame_state, "frame", None)
        self.frame_state.frame = fr
        try:
            for s in stmts:
                self.fuel()
                fr.line = s.get("ln", fr.line)
                try:
                    self.STMTS[s["op"]](self, s, fr)
                except DiasporeError as exc:
                    exc.value.interp = self
                    trace = exc.value.d.get("__traceback__")
                    if trace is None or trace.tb_frame.frame is not fr:
                        exc.value.d["__traceback__"] = Traceback(
                            trace, self.frame_view(fr), -1, fr.line)
                    raise
        finally:
            self.frame_state.frame = previous

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
        while closure is not None and closure.is_class and not node.get("annotation_scope"):
            closure = closure.parent
        module = self.modules.get(fr.module_name)
        fn = DiasporeFunction(self, node, closure, module)
        p = fn.params  # normalized: absent fields are their defaults
        fn.defaults = tuple(self.ev(d, fr) for d in p["defaults"])
        fn.kwdefaults = {k: self.ev(d, fr) for k, d in zip(p["kwonly"], p["kwdefaults"])
                         if d is not None}
        fn.metadata["__module__"] = fr.globals.get("__name__", fr.module_name)
        fn.metadata["__qualname__"] = node["name"]
        fn.metadata["__defaults__"] = fn.defaults or None
        fn.metadata["__kwdefaults__"] = fn.kwdefaults or None
        body = node.get("body", ())
        first = body[0] if body else {}
        doc = first.get("v", {}) if first.get("op") == "expr" else {}
        fn.metadata["__doc__"] = (
            doc.get("v") if doc.get("op") == "const" and doc.get("t") == "str" else None)
        fn.code = Code(fn)
        return fn

    def _check_base(self, bv):
        if isinstance(bv, DiasporeClass):
            return bv
        if isinstance(bv, BuiltinType):
            return bv
        raise DiasporeError(make_exc(
            "TypeError", f"cannot subclass {self.diaspore_repr(bv)} in diaspore"))

    def ex_class(self, s, fr):
        """Evaluate a class definition and bind the resulting class."""
        bases = []
        for b in s.get("bases", ()):
            if b["op"] == "starred":
                for bv in self.as_host_iter(self.ev(b["v"], fr)):
                    bases.append(bv)
            else:
                bases.append(self.ev(b, fr))
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
        original_bases = tuple(bases)
        bases = []
        bases_changed = False
        for base in original_bases:
            if isinstance(base, (BuiltinType, DiasporeClass)):
                bases.append(base)
                continue
            entries = self.builtins["getattr"](base, "__mro_entries__", None)
            if entries is None:
                bases.append(self._check_base(base))
                continue
            resolved = self.call(entries, [original_bases], {})
            if not isinstance(resolved, tuple):
                raise DiasporeError(make_exc("TypeError", "__mro_entries__ must return a tuple"))
            bases.extend(self._check_base(item) for item in resolved)
            bases_changed = True
        metaclass = self.select_metaclass(merged.pop("metaclass", None), bases)
        prepare = metaclass.lookup("__prepare__") if isinstance(
            metaclass, (BuiltinType, DiasporeClass)) else _MISSING
        namespace = {} if prepare is _MISSING else self.call(
            self.get_attr(metaclass, "__prepare__"), [s["name"], tuple(bases)], merged)
        original_namespace = namespace
        if isinstance(namespace, DiasporeInstance):
            namespace = ClassNamespace(self, namespace)
        namespace["__module__"] = fr.globals.get("__name__", fr.module_name)
        namespace["__qualname__"] = s["name"]
        cf = Frame(namespace, fr, None, frozenset(), frozenset(), True,
                   fr.globals, s["name"], fr.module_name)
        self.exec_block(s["body"], cf)
        if bases_changed:
            namespace["__orig_bases__"] = original_bases
        cls = self.build_class(s["name"], bases, original_namespace,
                               metaclass=metaclass, kwargs=merged)
        if isinstance(cls, DiasporeClass):
            cf.vars = cls.ns
        self.store_name(fr, s.get("binding", s["name"]), cls)

    def select_metaclass(self, metaclass, bases):
        """Choose the most-derived compatible metaclass."""
        winner = metaclass or (self.type_of(bases[0]) if bases else self.types["type"])
        if not isinstance(winner, (BuiltinType, DiasporeClass)):
            return winner
        for base in bases:
            candidate = self.type_of(base)
            if winner.issub(candidate):
                continue
            if candidate.issub(winner):
                winner = candidate
                continue
            raise DiasporeError(make_exc("TypeError", "metaclass conflict"))
        return winner

    def build_class(self, name, bases, ns, metaclass=None, kwargs=None):
        """Create a class through its selected metaclass."""
        meta = self.select_metaclass(metaclass, bases)
        if meta is self.types["type"]:
            return self.new_class(meta, name, bases, ns, **(kwargs or {}))
        return self.call(meta, [name, tuple(bases), ns], kwargs or {})

    def new_class(self, meta, name, bases, namespace, **kwargs):
        """Implement type.__new__ and descriptor/subclass initialization."""
        bases = [self._check_base(base) for base in bases]
        if isinstance(namespace, DiasporeInstance):
            namespace = ClassNamespace(self, namespace)
        cls = DiasporeClass(name, bases, dict(namespace))
        cls.metaclass = meta
        slots = cls.ns.get("__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        if "__slots__" in cls.ns:
            cls.has_dict = "__dict__" in slots or any(
                getattr(base, "has_dict", False) for base in bases)
        for slot in slots:
            if not isinstance(slot, str):
                raise DiasporeError(make_exc("TypeError", "__slots__ items must be strings"))
            if not slot.isidentifier():
                raise DiasporeError(make_exc("TypeError", "__slots__ must be identifiers"))
            if slot in ("__dict__", "__weakref__"):
                continue
            if slot.startswith("__") and not slot.endswith("__") and name.lstrip("_"):
                slot = "_" + name.lstrip("_") + slot
            if slot in cls.ns:
                raise DiasporeError(make_exc(
                    "ValueError", f"'{slot}' in __slots__ conflicts with class variable"))
            key = (cls, slot)
            cls.ns[slot] = Descriptor(
                slot, cls, kind="member_descriptor",
                get=lambda obj, _key=key: self._slot_get(obj, _key),
                set=lambda obj, value, _key=key: self._slot_set(obj, _key, value),
                delete=lambda obj, _key=key: self._slot_delete(obj, _key),
            )
        cls.ns.setdefault("__doc__", None)
        for key, value in list(cls.ns.items()):
            fn = value.fn if isinstance(value, (ClassMethod, StaticMethod)) else value
            if isinstance(fn, DiasporeFunction):
                if fn.defclass is None:
                    fn.defclass = cls
                if key in ("__init_subclass__", "__class_getitem__") \
                        and isinstance(value, DiasporeFunction):
                    cls.ns[key] = ClassMethod(value)
                elif key == "__new__" and isinstance(value, DiasporeFunction):
                    cls.ns[key] = StaticMethod(value)
            if isinstance(value, Property):
                for accessor in (value.fget, value.fset, value.fdel):
                    if isinstance(accessor, DiasporeFunction) and accessor.defclass is None:
                        accessor.defclass = cls
            if isinstance(value, DiasporeInstance) \
                    and value.cls.lookup("__set_name__") is not _MISSING:
                self.call_dunder(value, "__set_name__", [cls, key])
        for base in cls.mro[1:]:
            hook = base.ns.get("__init_subclass__", _MISSING)
            if hook is not _MISSING:
                self.call(self.bind_attr(hook, cls, cls), [], kwargs)
                break
        else:
            if kwargs:
                raise DiasporeError(make_exc(
                    "TypeError", f"{name}.__init_subclass__() takes no keyword arguments"))
        return cls

    def _slot_get(self, obj, key):
        if obj.slots is None or key not in obj.slots:
            raise DiasporeError(make_exc(
                "AttributeError", f"'{obj.cls.name}' object has no attribute '{key[1]}'"))
        return obj.slots[key]

    def _slot_set(self, obj, key, value):
        if obj.slots is None:
            obj.slots = {}
        obj.slots[key] = value

    def _slot_delete(self, obj, key):
        self._slot_get(obj, key)
        del obj.slots[key]

    def type_of(self, value):
        """Return a stable sandbox type without exposing a host type object."""
        if isinstance(value, NativeObject):
            if value.kind not in self.native_types:
                def unavailable(*args, **kwargs):
                    raise TypeError(f"cannot create '{value.kind}' instances")
                self.native_types[value.kind] = BuiltinType(value.kind, (), unavailable)
            return self.native_types[value.kind]
        if isinstance(value, DiasporeInstance):
            return value.cls
        if isinstance(value, DiasporeClass):
            return value.metaclass or self.types["type"]
        if isinstance(value, BuiltinType):
            return self.types["type"]
        if isinstance(value, Descriptor):
            return self.types[value.kind]
        if isinstance(value, BoundMethod) and isinstance(value.fn, Descriptor):
            name = ("method-wrapper" if value.fn.kind == "wrapper_descriptor"
                    else "builtin_function_or_method")
            return self.types[name]
        if isinstance(value, DiasporeGenerator):
            name = "async_generator" if value.fn.code.co_flags & 512 else (
                "coroutine" if value.fn.node.get("coro") else "generator")
            return self.types[name]
        host = type(value)
        result = self.host_types.get(host)
        if result is not None:
            return result
        if callable(value):
            return self.types["builtin_function_or_method"]
        def unavailable(*args, **kwargs):
            raise TypeError(f"cannot create '{host.__name__}' instances")
        result = BuiltinType(host.__name__, (host,), unavailable)
        self.host_types[host] = result
        if host in _STRUCTSEQ_FIELDS:
            result.bases = [self.types["tuple"]]
            result.mro = [result, *self.types["tuple"].mro]
            result.ns["__module__"] = "sys"
            for field in _STRUCTSEQ_FIELDS[host]:
                result.ns[field] = Descriptor(
                    field, result, get=lambda obj, _field=field: getattr(obj, _field),
                    kind="member_descriptor")
        for name in ("__iter__", "__next__", "__len__", "__contains__", "__reversed__"):
            if hasattr(host, name):
                result.ns[name] = Descriptor(name, result,
                    call=lambda obj, *a, _n=name: getattr(obj, _n)(*a))
        return result

    def frame_view(self, frame):
        """Construct an inspectable view of an interpreted frame."""
        code = frame.code
        if code is None:
            node = {"name": frame.fname, "params": {}, "body": [], "ln": frame.line}
            fn = DiasporeFunction(self, node, None, self.modules.get(frame.module_name))
            code = Code(fn)
        return FrameView(frame, code, self.frame_view(frame.back) if frame.back else None)

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
        elif isinstance(ctx, memoryview):
            enter, exit_ = ctx.__enter__, ctx.__exit__
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
        """Import names using the module's live runtime namespace."""
        mod = self.import_module(s["module"])
        for spec in s["names"]:
            if spec["n"] == "*":
                try:
                    exports = self.get_attr(mod, "__all__")
                except DiasporeError as exc:
                    if not self.exc_isa(exc.value, "AttributeError"):
                        raise
                    namespace = self.get_attr(mod, "__dict__")
                    exports = [name for name in namespace if not name.startswith(("_", "."))]
                index = 0
                while True:
                    try:
                        name = self.get_index(exports, index)
                    except DiasporeError as exc:
                        if self.exc_isa(exc.value, "IndexError"):
                            break
                        raise
                    if not isinstance(name, str):
                        raise DiasporeError(make_exc(
                            "TypeError", f"Item in {s['module']}.__all__ must be str, "
                            f"not {type_name(name)}"))
                    self.store_name(fr, name, self.get_attr(mod, name))
                    index += 1
                continue
            try:
                value = self.get_attr(mod, spec["n"])
            except DiasporeError as exc:
                if not self.exc_isa(exc.value, "AttributeError"):
                    raise
                child = s["module"] + "." + spec["n"]
                if self._module_ir(child) is not None:
                    value = self.import_module(child)
                else:
                    raise DiasporeError(make_exc(
                        "ImportError", f"cannot import name '{spec['n']}' "
                        f"from '{s['module']}'")) from None
            self.store_name(fr, spec["alias"], value)

    def ex_del(self, s, fr):
        """Delete names, attributes, or indexed values."""
        for t in s["targets"]:
            op = t["op"]
            if op == "name":
                id_ = t["id"]
                if id_ in fr.temps:
                    del fr.temps[id_]
                    continue
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
                self.del_attr(self.ev(t["o"], fr), t["n"])
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
        if t == "str" and "codepoints" in e:
            return "".join(map(chr, e["codepoints"]))
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
        # Exact integers cannot override numeric protocols or grow sequences.
        if type(l) is int and type(r) is int:
            try:
                return (_IBINOPS if inplace else _BINOPS)[kind](l, r)
            except Exception as exc:
                raise host_exc_to_diaspore(exc) from None
        if kind == "mod" and isinstance(l, (str, bytes, bytearray)):
            try:
                return l % percent_value(self, r)
            except DiasporeError:
                raise
            except Exception as exc:
                raise host_exc_to_diaspore(exc) from None
        if kind == "bitor" and isinstance(l, (DiasporeClass, BuiltinType, GenericAlias, Union)) \
                and (r is None or isinstance(r, (DiasporeClass, BuiltinType, GenericAlias, Union))):
            return Union(l, self.types["NoneType"] if r is None else r)
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
                        result = self.call_dunder(l, iname, [r])
                        if result is not NotImplemented:
                            return result
                right_first = (
                    isinstance(l, DiasporeInstance) and isinstance(r, DiasporeInstance)
                    and r.cls is not l.cls and r.cls.issub(l.cls)
                    and r.cls.lookup(right) is not l.cls.lookup(right))
                if right_first and r.cls.lookup(right) is not _MISSING:
                    result = self.call_dunder(r, right, [l])
                    if result is not NotImplemented:
                        return result
                if isinstance(l, DiasporeInstance) and l.cls.lookup(left) is not _MISSING:
                    result = self.call_dunder(l, left, [r])
                    if result is not NotImplemented:
                        return result
                if (not right_first and isinstance(r, DiasporeInstance)
                        and (not isinstance(l, DiasporeInstance) or l.cls is not r.cls)
                        and r.cls.lookup(right) is not _MISSING):
                    result = self.call_dunder(r, right, [l])
                    if result is not NotImplemented:
                        return result
            sym = {"add": "+", "sub": "-", "mul": "*", "div": "/", "floordiv": "//",
                   "mod": "%", "pow": "**", "lshift": "<<", "rshift": ">>",
                   "bitand": "&", "bitor": "|", "bitxor": "^",
                   "matmul": "@", "divmod": "divmod()"}[kind]
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
            method = {"neg": "__neg__", "pos": "__pos__", "invert": "__invert__"}[kind]
            if v.cls.lookup(method) is not _MISSING:
                return self.call_dunder(v, method, [])
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
            if isinstance(l, DiasporeInstance) or isinstance(r, DiasporeInstance):
                result = self._rich_compare(l, r, kind)
                return l is not r if result is NotImplemented else self.truth(result)
            return not self.diaspore_eq(l, r)
        if isinstance(l, DiasporeInstance) or isinstance(r, DiasporeInstance):
            result = self._rich_compare(l, r, kind)
            if result is not NotImplemented:
                return self.truth(result)
            raise DiasporeError(make_exc(
                "TypeError",
                f"'{_CMP_SYM[kind]}' not supported between instances of "
                f"'{type_name(l)}' and '{type_name(r)}'"))
        try:
            return bool(_CMPOPS[kind](l, r))
        except Exception as ex:
            raise host_exc_to_diaspore(ex) from None

    def _rich_compare(self, l, r, kind):
        reflected = {"eq": "eq", "ne": "ne", "lt": "gt", "le": "ge",
                     "gt": "lt", "ge": "le"}[kind]
        pairs = [(l, r, kind), (r, l, reflected)]
        if (isinstance(l, DiasporeInstance) and isinstance(r, DiasporeInstance)
                and r.cls is not l.cls and r.cls.issub(l.cls)):
            pairs.reverse()
        for obj, other, op in pairs:
            if not isinstance(obj, DiasporeInstance):
                continue
            method = "__" + op + "__"
            if obj.cls.lookup(method) is not _MISSING:
                result = self.call_dunder(obj, method, [other])
            elif op == "ne" and obj.cls.lookup("__eq__") is not _MISSING:
                result = self.call_dunder(obj, "__eq__", [other])
                if result is not NotImplemented:
                    return not self.truth(result)
            else:
                continue
            if result is not NotImplemented:
                return result
        return NotImplemented

    def diaspore_eq(self, l, r) -> bool:
        """Compare values structurally while honoring sandbox equality methods."""
        if isinstance(l, DiasporeInstance) or isinstance(r, DiasporeInstance):
            result = self._rich_compare(l, r, "eq")
            return l is r if result is NotImplemented else self.truth(result)
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
        if isinstance(container, DiasporeClass):
            meta = self.type_of(container)
            method = meta.lookup("__contains__")
            if method is not _MISSING:
                return self.truth(self.call(self.bind_attr(method, container, meta), [item], {}))
            return any(self.diaspore_eq(item, value) for value in self.as_host_iter(container))
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
        try:
            return set(self._eval_elts(e.get("elts", ()), fr))
        except TypeError as exc:
            raise host_exc_to_diaspore(exc) from None

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
                key = self.ev(entry["k"], fr)
                value = self.ev(entry["v"], fr)
                try:
                    out[key] = value
                except TypeError as exc:
                    raise host_exc_to_diaspore(exc) from None
        return out


    def e_fstring(self, e, fr):
        """Evaluate and format f-string components."""
        out = []
        for p in e["parts"]:
            if "s" in p:
                out.append("".join(map(chr, p["codepoints"])) if "codepoints" in p else p["s"])
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
        if not isinstance(spec, str):
            raise DiasporeError(make_exc("TypeError", "format() argument 2 must be str"))
        if isinstance(v, (DiasporeInstance, DiasporeClass, DiasporeFunction, BoundMethod,
                          DiasporeModule, NativeModule, DiasporeGenerator, BuiltinType)):
            typ = self.type_of(v)
            method = typ.lookup("__format__")
            if method is not _MISSING:
                result = self.call(self.bind_attr(method, v, typ), [spec], {})
                if not isinstance(result, str):
                    raise DiasporeError(make_exc(
                        "TypeError", f"__format__ must return a str, not {type_name(result)}"))
                return result
            if spec:
                raise DiasporeError(make_exc(
                    "TypeError", f"unsupported format string passed to {typ.name}.__format__"))
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
        if fr.code is not None and fr.code.co_flags & 512:
            v = AsyncYield(v)
        return gen._yield(v)  # value delivered by send(), None from next()

    def e_yieldfrom(self, e, fr):
        """Delegate iteration and generator control to a subiterator."""
        gen = self._gen_of(fr)
        it = self.as_host_iter(self.ev(e["v"], fr))
        delegating = isinstance(it, (DiasporeGenerator, AsyncGeneratorOperation))
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
        if isinstance(f, StaticMethod):
            return self.call(f.fn, args, kwargs)
        if isinstance(f, GenericAlias):
            return self.call(f.__origin__, args, kwargs)
        if isinstance(f, BoundMethod):
            return self.call(f.fn, [f.obj] + args, kwargs)
        if isinstance(f, DiasporeClass):
            meta = f.metaclass
            hook = meta.lookup("__call__") if meta is not None else _MISSING
            if hook is not _MISSING:
                return self.call(self.bind_attr(hook, f, meta), args, kwargs)
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
                result = f(*args, **kwargs)
                self._check_interrupt()
                if isinstance(result, DiasporeInstance) and result.interp is None:
                    result.interp = self
                return result
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
        fr.annotation_scope = fn.node.get("annotation_scope", False)
        fr.defclass = fn.defclass
        p = fn.params["args"]
        fr.self_ = frame_vars.get(p[0]) if p else None
        fr.code = fn.code
        fr.back = getattr(self.frame_state, "frame", None)
        if fn.gen:
            return DiasporeGenerator(self, fn, fr)
        if self.depth >= min(self.max_depth, self.recursion_limit):
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
        if (not kwargs and len(args) == len(names) and not p["kwonly"]
                and not p["vararg"] and not p["kwarg"]):
            return dict(zip(names, args))
        posonly = names[:p["posonly"]]
        bad = [name for name in posonly if name in kwargs]
        if bad and not p["kwarg"]:
            raise DiasporeError(make_exc(
                "TypeError", f"{fn.name}() got some positional-only arguments "
                f"passed as keyword arguments: '{', '.join(bad)}'"))
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
            if k in names and k not in posonly:
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

    def instantiate(self, cls: DiasporeClass, args, kwargs):
        """Construct an instance through __new__, then initialize it."""
        abstract = cls.ns.get("__abstractmethods__")
        if abstract:
            raise DiasporeError(make_exc(
                "TypeError", f"Can't instantiate abstract class {cls.name} "
                f"with abstract methods {', '.join(sorted(abstract))}"))
        new = cls.lookup("__new__")
        if new is not _MISSING:
            new = new.fn if isinstance(new, StaticMethod) else new
            inst = self.call(new, [cls] + args, kwargs)
        else:
            inst = DiasporeInstance(cls, self)
            if cls.prim is not None:
                inst.prim = cls.prim.ctor(*args, **kwargs)
        if isinstance(inst, DiasporeInstance):
            inst.interp = self
        if not isinstance(inst, (DiasporeInstance, DiasporeClass)):
            return inst
        if not self.type_of(inst).issub(cls):
            return inst
        init = cls.lookup("__init__")
        if init is not _MISSING:
            result = self.call(self.bind_attr(init, inst, cls), args, kwargs)
            if result is not None:
                raise DiasporeError(make_exc(
                    "TypeError", f"__init__() should return None, not '{type_name(result)}'"))
        elif (args or kwargs) and cls.prim is None:
            raise DiasporeError(make_exc("TypeError", f"{cls.name}() takes no arguments"))
        return inst

    def primitive_new(self, base, cls, /, *args, **kwargs):
        """Allocate a primitive subclass without running __init__."""
        value = self.call(base, [], {}) if base.name in ("dict", "list", "set", "bytearray") \
            else self.call(base, list(args), kwargs)
        if cls is base:
            return value
        if not isinstance(cls, DiasporeClass) or not cls.issub(base):
            raise DiasporeError(make_exc("TypeError", f"{base.name}.__new__: invalid subtype"))
        instance = DiasporeInstance(cls, self)
        instance.prim = value
        return instance

    def _raw(self, obj):
        """Raw builtin value of obj (unwrap a builtin-subclass instance)."""
        if isinstance(obj, DiasporeInstance) and obj.prim is not None:
            return obj.prim
        return obj

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
        if len(args) == 2 and isinstance(args[0], (DiasporeClass, BuiltinType)):
            return SuperObject(args[0], args[1])
        raise DiasporeError(make_exc("TypeError", "super() argument error"))


    def bind_attr(self, v, inst, cls):
        """Bind a descriptor to its instance or class."""
        if isinstance(v, Descriptor):
            if v.get is not None:
                return v if inst is None else v.get(inst)
            if v.kind == "classmethod_descriptor":
                return BoundMethod(v, cls)
            if inst is None or v.name == "__new__":
                return v
            return BoundMethod(v, cls if v.kind == "classmethod_descriptor" else inst)
        if isinstance(v, DiasporeInstance) and v.cls.lookup("__get__") is not _MISSING:
            return self.call_dunder(v, "__get__", [inst, cls])
        if isinstance(v, Property):
            if inst is None:
                return v
            if v.fget is None:
                raise DiasporeError(make_exc("AttributeError", "property has no getter"))
            return self.call(v.fget, [inst], {})
        if v is _OBJECT_SETATTR:
            return lambda n, value: self.object_setattr(inst, n, value)
        if v is _OBJECT_DELATTR:
            return lambda n: self.object_delattr(inst, n)
        if isinstance(v, DiasporeFunction):
            return v if inst is None else BoundMethod(v, inst)
        if isinstance(v, StaticMethod):
            return v.fn
        if isinstance(v, ClassMethod):
            return BoundMethod(v.fn, cls)
        if callable(v) and not isinstance(v, (DiasporeClass, BuiltinType)):
            return v if inst is None else BoundMethod(_HostFn(self, v), inst)
        return v

    def get_attr(self, o, n, raw=False):
        """Resolve a sandbox-visible attribute."""
        if isinstance(o, AsyncGeneratorOperation) and n in (
                "send", "throw", "close", "__await__", "__iter__", "__next__"):
            return getattr(o, n)
        if not isinstance(n, str):
            raise DiasporeError(make_exc("TypeError", "attribute name must be string"))
        # Only exact host primitives use this path; sandbox descriptors still bind below.
        allowed = _HOST_METHODS.get(type(o))
        if allowed is not None and n in allowed:
            return self.host_attr(o, n)
        if not raw and isinstance(o, DiasporeInstance):
            hook = o.cls.lookup("__getattribute__")
            if hook is not _MISSING and hook is not OBJECT.ns.get("__getattribute__"):
                try:
                    return self.call(self.bind_attr(hook, o, o.cls), [n], {})
                except DiasporeError as exc:
                    if not self.exc_isa(exc.value, "AttributeError"):
                        raise
                    fallback = o.cls.lookup("__getattr__")
                    if fallback is _MISSING:
                        raise
                    return self.call(self.bind_attr(fallback, o, o.cls), [n], {})
        if not raw and isinstance(o, DiasporeClass):
            meta = self.type_of(o)
            hook = meta.lookup("__getattribute__")
            if hook is not _MISSING and hook is not self.types["type"].ns.get("__getattribute__"):
                return self.call(self.bind_attr(hook, o, meta), [n], {})
        if isinstance(o, VersionInfo) and n in VersionInfo._fields:
            return getattr(o, n)
        if type(o) in _STRUCTSEQ_FIELDS and n in _STRUCTSEQ_FIELDS[type(o)]:
            return getattr(o, n)
        if isinstance(o, SimpleNamespace):
            if n == "__dict__":
                return o.__dict__
            if n in o.__dict__:
                return o.__dict__[n]
        if isinstance(o, Code) and (n.startswith("co_") or n == "replace"):
            if hasattr(o, n):
                return getattr(o, n)
        if isinstance(o, Cell) and n == "cell_contents":
            try:
                return o.cell_contents
            except ValueError as exc:
                raise host_exc_to_diaspore(exc) from None
        if isinstance(o, FrameView) and n in (
                "f_code", "f_back", "f_lasti", "f_trace", "f_locals", "f_globals", "f_lineno"):
            return getattr(o, n)
        if isinstance(o, Traceback) and n in ("tb_next", "tb_frame", "tb_lasti", "tb_lineno"):
            return getattr(o, n)
        if isinstance(o, Descriptor):
            if n in ("__name__", "__qualname__"):
                return o.name
            if n == "__objclass__":
                return o.owner
            if n == "__get__":
                return lambda obj, owner=None: self.bind_attr(o, obj, owner or self.type_of(obj))
            if n == "__set__" and o.set is not None:
                return o.set
            if n == "__delete__" and o.delete is not None:
                return o.delete
        if isinstance(o, (StaticMethod, ClassMethod)):
            if n in ("__func__", "__wrapped__"):
                return o.fn
            if n == "__isabstractmethod__":
                return self.builtins["getattr"](o.fn, n, False)
            if n == "__get__":
                return lambda obj, owner=None: self.bind_attr(o, obj, owner or self.type_of(obj))
        if isinstance(o, (GenericAlias, Union)):
            if n == "__class__":
                return self.type_of(o)
            if n in ("__args__", "__parameters__") \
                    or n == "__origin__" and isinstance(o, GenericAlias):
                return getattr(o, n)
            if n == "__mro_entries__" and isinstance(o, GenericAlias):
                return lambda bases: (o.__origin__,)
            if isinstance(o, GenericAlias):
                return self.get_attr(o.__origin__, n)
        if isinstance(o, memoryview) and n in MEMORYVIEW_ATTRS:
            return getattr(o, n)
        if isinstance(o, int) and n == "to_bytes":
            return lambda length=1, byteorder="big", *, signed=False: o.to_bytes(
                length, byteorder, signed=signed)
        if isinstance(o, float) and n in ("hex", "as_integer_ratio"):
            return getattr(o, n)
        if n == "__class__" and (
                not isinstance(o, DiasporeInstance) or o.cls.lookup(n) is _MISSING):
            return self.type_of(o)
        if isinstance(o, DiasporeInstance):
            if n == "__dict__" and o.cls.has_dict:
                return o.d
            v = o.cls.lookup(n)
            data_descriptor = isinstance(v, Property) or (
                isinstance(v, Descriptor) and v.get is not None) or (
                isinstance(v, DiasporeInstance) and (
                    v.cls.lookup("__set__") is not _MISSING
                    or v.cls.lookup("__delete__") is not _MISSING))
            if data_descriptor:
                return self.bind_attr(v, o, o.cls)
            if n in o.d:
                return o.d[n]
            if o.cls.issub(BASE_EXC) and n in (
                    "__traceback__", "__cause__", "__context__", "__suppress_context__"):
                return False if n == "__suppress_context__" else None
            if o.cls.issub(BASE_EXC) and n == "with_traceback":
                return lambda tb: (self.object_setattr(o, "__traceback__", tb), o)[1]
            if v is not _MISSING:
                return self.bind_attr(v, o, o.cls)
            if o.prim is not None:
                allowed = {dict: DICT_METHODS, list: LIST_METHODS, set: SET_METHODS,
                           str: STR_METHODS, tuple: TUPLE_METHODS,
                           bytes: BYTES_METHODS}.get(type(o.prim))
                if allowed and n in allowed:
                    return self.host_attr(o.prim, n)
            ga = o.cls.lookup("__getattr__")
            if ga is not _MISSING and not raw:
                return self.call(self.bind_attr(ga, o, o.cls), [n], {})
            raise DiasporeError(make_exc(
                "AttributeError", f"'{o.cls.name}' object has no attribute '{n}'"))
        if isinstance(o, (BuiltinType, DiasporeClass)):
            if n == "__name__":
                return o.name
            if n == "__qualname__":
                return o.qualname
            if n == "__module__":
                return o.ns.get(n, "builtins")
            if n == "__mro__":
                return tuple(o.mro)
            if n == "__bases__":
                return tuple(o.bases)
            if n == "__base__":
                return o.bases[0] if o.bases else None
            if n == "__dict__":
                return MappingProxyType(o.ns)
            if n == "__subclasses__":
                return lambda: list(o.subclasses)
            if n == "mro":
                return lambda: list(o.mro)
            meta = self.type_of(o)
            mv = meta.lookup(n)
            if isinstance(mv, Property) or isinstance(mv, DiasporeInstance) \
                    and mv.cls.lookup("__set__") is not _MISSING:
                return self.bind_attr(mv, o, meta)
            value = o.lookup(n)
            if value is not _MISSING:
                if value is _OBJECT_SETATTR:
                    return self.object_setattr
                if value is _OBJECT_DELATTR:
                    return self.object_delattr
                return self.bind_attr(value, None, o)
            if mv is not _MISSING:
                return self.bind_attr(mv, o, meta)
            fallback = meta.lookup("__getattr__")
            if fallback is not _MISSING and not raw:
                return self.call(self.bind_attr(fallback, o, meta), [n], {})
            raise DiasporeError(make_exc(
                "AttributeError", f"type object '{o.name}' has no attribute '{n}'"))
        if isinstance(o, SuperObject):
            owner = self.type_of(o.obj)
            mro = owner.mro if o.cls in owner.mro else o.obj.mro
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
            if n == "__dict__":
                return o.frame.vars
            if n in o.frame.vars:
                return o.frame.vars[n]
            fallback = o.frame.vars.get("__getattr__")
            if fallback is not None:
                return self.call(fallback, [n], {})
            raise DiasporeError(make_exc(
                "AttributeError", f"module '{o.name}' has no attribute '{n}'"))
        if isinstance(o, NativeModule):
            if n == "__dict__":
                return o.ns
            if n == "__name__":
                return o.name
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
            descriptor = self.types["property"].lookup(n)
            if descriptor is not _MISSING:
                return self.bind_attr(descriptor, o, self.types["property"])
            raise DiasporeError(make_exc(
                "AttributeError", f"'property' object has no attribute '{n}'"))
        if isinstance(o, DiasporeFunction):
            if n in o.metadata:
                return o.metadata[n]
            if n == "__name__":
                return o.name
            if n == "__dict__":
                return o.attrs
            if n == "__code__":
                return o.code
            if n == "__closure__":
                return o.closure_value
            if n == "__globals__":
                return o.closure.globals if o.closure else {}
            if n == "__get__":
                return lambda obj, owner=None: o if obj is None else BoundMethod(o, obj)
            if n in o.attrs:
                return o.attrs[n]
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
            if n in ("gi_code", "cr_code", "ag_code"):
                return o.fn.code
            if n in ("__name__", "__qualname__"):
                return o.fn.name
            if n in ("gi_running", "cr_running", "ag_running"):
                return o.chan.state["started"] and not o.chan.state["done"] and o.chan.out_q.empty()
            if o.fn.node.get("asyncgen"):
                if n == "__aiter__":
                    return lambda: o
                if n == "__anext__":
                    return lambda: AsyncGeneratorOperation(o, ("send", None))
                if n == "asend":
                    return lambda value: AsyncGeneratorOperation(o, ("send", value))
                if n == "athrow":
                    def async_throw(exc):
                        if isinstance(exc, DiasporeClass) and exc.issub(BASE_EXC):
                            exc = self.instantiate(exc, [], {})
                        if not isinstance(exc, DiasporeInstance) or not exc.cls.issub(BASE_EXC):
                            raise DiasporeError(make_exc(
                                "TypeError", "exceptions must derive from BaseException"))
                        return AsyncGeneratorOperation(o, ("throw", exc))
                    return async_throw
                if n == "aclose":
                    return lambda: AsyncGeneratorOperation(
                        o, ("genexit", make_exc("GeneratorExit")), True)
                raise DiasporeError(make_exc(
                    "AttributeError", f"'async_generator' object has no attribute '{n}'"))
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
            if not n.startswith("."):
                if n in o.props:
                    return o.props[n]
                if n in o.methods:
                    return o.methods[n]
            raise DiasporeError(make_exc(
                "AttributeError", f"'{o.kind}' object has no attribute '{n}'"))
        return self.host_attr(o, n)

    def codec(self, value, encoding="utf-8", errors="strict", decode=False):
        """Use deterministic primitives or a codec explicitly included in the bundle."""
        if not isinstance(encoding, str) or not isinstance(errors, str):
            raise TypeError("encoding and errors must be strings")
        normalized = encoding.lower().replace("_", "-").replace(" ", "-")
        native = _FAST_CODECS.get(normalized)
        if native is not None and errors in (
                "strict", "ignore", "replace", "backslashreplace", "surrogateescape"):
            return str(value, native, errors) if decode else value.encode(native, errors)
        if self._module_ir("codecs") is None:
            raise LookupError(
                f"unknown encoding: {encoding} "
                f"(bundle it with `import encodings.{normalized.replace('-', '_')}`)")
        module = self.import_module("codecs")
        result = self.call(self.get_attr(module, "decode" if decode else "encode"),
                           [value, encoding, errors], {})
        expected = str if decode else bytes
        if not isinstance(result, expected):
            raise TypeError(
                f"'{encoding}' codec returned {type_name(result)}, expected {expected.__name__}")
        return result

    def _update_mapping(self, target, value):
        raw = self._raw(value)
        if isinstance(raw, (dict, MappingProxyType)):
            target.update(raw)
        elif isinstance(value, DiasporeInstance) and value.cls.lookup("keys") is not _MISSING:
            for key in self.as_host_iter(self.call_dunder(value, "keys", [])):
                target[key] = self.get_index(value, key)
        else:
            target.update(self.as_host_iter(pair) for pair in self.as_host_iter(value))

    def _dict_update(self, target, /, *args, **kwargs):
        if len(args) > 1:
            raise TypeError(f"update expected at most 1 argument, got {len(args)}")
        if args:
            self._update_mapping(target, args[0])
        target.update(kwargs)

    def host_attr(self, o, n):
        """Resolve an allowlisted attribute on a host primitive."""
        t = type(o)
        allowed = _HOST_METHODS.get(t)
        if allowed and n in allowed:
            if t is str and n == "encode":
                return lambda encoding="utf-8", errors="strict": self.codec(o, encoding, errors)
            if t in (bytes, bytearray) and n == "decode":
                return lambda encoding="utf-8", errors="strict": self.codec(
                    o, encoding, errors, True)
            if t is str and n == "format":
                return lambda *args, **kwargs: self.formatter.vformat(o, args, kwargs)
            if t is str and n == "format_map":
                return lambda mapping: self.formatter.vformat(o, NoPositionals(), mapping)
            if t is list and n == "sort":
                return functools.partial(self._list_sort, o)
            if t in (list, tuple) and n in ("index", "count", "remove"):
                return functools.partial(self._seq_search, o, n)
            if t is list and n == "extend":
                return lambda values, /: o.extend(self.as_host_iter(values))
            if t is dict and n == "update":
                return functools.partial(self._dict_update, o)
            if t in (set, frozenset) and n in {
                    "union", "intersection", "difference", "symmetric_difference",
                    "isdisjoint", "issubset", "issuperset", "update", "intersection_update",
                    "difference_update", "symmetric_difference_update"}:
                method = getattr(o, n)
                return lambda *values: method(*(self.as_host_iter(value) for value in values))
            return getattr(o, n)
        if n.startswith("__") and n.endswith("__"):
            typ = self.type_of(o)
            value = typ.lookup(n)
            if value is not _MISSING:
                return self.bind_attr(value, o, typ)
        raise DiasporeError(make_exc(
            "AttributeError", f"'{type_name(o)}' object has no attribute '{n}'"))

    def set_attr(self, o, n, v):
        """Assign a sandbox-visible attribute."""
        if isinstance(o, SimpleNamespace):
            o.__dict__[n] = v
            return
        if isinstance(o, Property) and n == "__doc__":
            o.doc = v
            o.doc_from_getter = False
            return
        if isinstance(o, Cell) and n == "cell_contents":
            o.cell_contents = v
            return
        if isinstance(o, DiasporeFunction):
            if n == "__name__":
                o.name = v
            elif n == "__code__":
                if not isinstance(v, Code):
                    raise DiasporeError(make_exc("TypeError", "__code__ must be a code object"))
                if len(v.co_freevars) != len(o.cells):
                    raise DiasporeError(make_exc(
                        "ValueError", "code requires a different number of free variables"))
                cells = tuple(cell for _, cell in o.cells)
                o.cells = list(zip(v.co_freevars, cells))
                o.closure = Frame(CellNamespace(o.cells), None, frozenset(v.co_freevars),
                                  frozenset(), frozenset(), False, o.closure.globals,
                                  v.co_name, o.closure.module_name)
                o.code = v
                o.install_node(v.node)
            elif n == "__defaults__":
                if v is not None and not isinstance(v, tuple):
                    raise DiasporeError(make_exc(
                        "TypeError", "__defaults__ must be set to a tuple object"))
                o.defaults = v if v is not None else ()
                o.metadata[n] = v
            elif n == "__kwdefaults__":
                if v is not None and not isinstance(v, dict):
                    raise DiasporeError(make_exc(
                        "TypeError", "__kwdefaults__ must be set to a dict object"))
                o.kwdefaults = v if v is not None else {}
                o.metadata[n] = v
            elif n in o.metadata:
                o.metadata[n] = v
            else:
                o.attrs[n] = v
            return
        if isinstance(o, DiasporeInstance):
            hook = o.cls.lookup("__setattr__")
            if hook is not _MISSING and hook is not _OBJECT_SETATTR:
                self.call(self.bind_attr(hook, o, o.cls), [n, v], {})
                return
            self.object_setattr(o, n, v)
            return
        if isinstance(o, DiasporeClass):
            meta = self.type_of(o)
            hook = meta.lookup("__setattr__")
            if hook is not _MISSING and hook is not self.types["type"].ns.get("__setattr__"):
                self.call(self.bind_attr(hook, o, meta), [n, v], {})
                return
            self.type_setattr(o, n, v)
            return
        if isinstance(o, DiasporeModule):
            o.frame.vars[n] = v
            return
        if isinstance(o, NativeModule):
            o.ns[n] = v
            return
        raise DiasporeError(make_exc(
            "AttributeError", f"'{type_name(o)}' object has no attribute '{n}'"))

    def type_setattr(self, cls, name, value):
        """Assign class metadata without inserting type descriptors into its namespace."""
        if name in ("__name__", "__qualname__"):
            if not isinstance(value, str):
                raise DiasporeError(make_exc(
                    "TypeError",
                    f"can only assign string to {cls.name}.{name}, not '{type_name(value)}'"))
            if name == "__name__":
                if "\x00" in value:
                    raise DiasporeError(make_exc(
                        "ValueError", "type name must not contain null characters"))
                cls.name = value
            else:
                cls.qualname = value
            return
        cls.ns[name] = value

    def object_setattr(self, o, n, v):
        """Assign an instance attribute without invoking its override."""
        if not isinstance(o, DiasporeInstance):
            raise DiasporeError(make_exc("TypeError", "object.__setattr__ requires an instance"))
        prop = o.cls.lookup(n)
        if isinstance(prop, DiasporeInstance) and prop.cls.lookup("__set__") is not _MISSING:
            self.call_dunder(prop, "__set__", [o, v])
            return
        if isinstance(prop, Property):
            if prop.fset is None:
                raise DiasporeError(make_exc(
                    "AttributeError", f"property '{n}' of '{o.cls.name}' object has no setter"))
            self.call(self.bind_attr(prop.fset, o, o.cls), [v], {})
            return
        if isinstance(prop, Descriptor) and prop.get is not None:
            if prop.set is not None:
                prop.set(o, v)
                return
            raise DiasporeError(make_exc("AttributeError", f"attribute '{n}' is not writable"))
        if not o.cls.has_dict:
            raise DiasporeError(make_exc(
                "AttributeError", f"'{o.cls.name}' object has no attribute '{n}'"))
        if n == "__dict__":
            if not isinstance(v, dict):
                raise TypeError(f"__dict__ must be set to a dictionary, not a '{type_name(v)}'")
            o.d = v
            return
        if o.cls.issub(BASE_EXC):
            if n == "__traceback__" and v is not None and not isinstance(v, Traceback):
                raise DiasporeError(make_exc(
                    "TypeError", "__traceback__ must be a traceback or None"))
            if n in ("__cause__", "__context__") and v is not None and not (
                    isinstance(v, DiasporeInstance) and v.cls.issub(BASE_EXC)):
                raise DiasporeError(make_exc(
                    "TypeError", "exception cause and context must be exceptions or None"))
            if n == "__suppress_context__" and not isinstance(v, bool):
                raise DiasporeError(make_exc("TypeError", "__suppress_context__ must be a bool"))
            if n == "__cause__":
                o.d["__suppress_context__"] = True
        o.d[n] = v

    def object_delattr(self, o, n):
        """Delete an instance attribute without invoking its override."""
        if not isinstance(o, DiasporeInstance):
            raise DiasporeError(make_exc("TypeError", "object.__delattr__ requires an instance"))
        prop = o.cls.lookup(n)
        if isinstance(prop, DiasporeInstance) and prop.cls.lookup("__delete__") is not _MISSING:
            self.call_dunder(prop, "__delete__", [o])
            return
        if isinstance(prop, Property):
            if prop.fdel is None:
                raise DiasporeError(make_exc(
                    "AttributeError", f"property '{n}' of '{o.cls.name}' object has no deleter"))
            self.call(self.bind_attr(prop.fdel, o, o.cls), [], {})
            return
        if isinstance(prop, Descriptor) and prop.get is not None:
            if prop.delete is not None:
                prop.delete(o)
                return
            raise DiasporeError(make_exc("AttributeError", f"attribute '{n}' is not writable"))
        if n not in o.d:
            raise DiasporeError(make_exc(
                "AttributeError", f"'{o.cls.name}' object has no attribute '{n}'"))
        del o.d[n]

    def del_attr(self, o, n):
        """Delete an attribute through the instance hook or namespace."""
        if isinstance(o, Cell) and n == "cell_contents":
            del o.cell_contents
            return
        if isinstance(o, DiasporeInstance):
            hook = o.cls.lookup("__delattr__")
            if hook is not _MISSING and hook is not _OBJECT_DELATTR:
                self.call(self.bind_attr(hook, o, o.cls), [n], {})
                return
            self.object_delattr(o, n)
            return
        if isinstance(o, DiasporeClass):
            meta = self.type_of(o)
            hook = meta.lookup("__delattr__")
            if hook is not _MISSING and hook is not self.types["type"].ns.get("__delattr__"):
                self.call(self.bind_attr(hook, o, meta), [n], {})
                return
            namespace = o.ns
        elif isinstance(o, DiasporeFunction):
            namespace = o.attrs
        elif isinstance(o, DiasporeModule):
            namespace = o.frame.vars
        else:
            namespace = {}
        if n not in namespace:
            raise DiasporeError(make_exc(
                "AttributeError", f"'{type_name(o)}' object has no attribute '{n}'"))
        del namespace[n]


    def get_index(self, o, k):
        """Read an indexed value through sandbox protocols."""
        if isinstance(o, NativeObject) and "__getitem__" in o.methods:
            return self.call(o.methods["__getitem__"], [k], {})
        if isinstance(o, (DiasporeClass, BuiltinType)):
            meta = self.type_of(o)
            method = meta.lookup("__getitem__")
            if method is not _MISSING:
                return self.call(self.bind_attr(method, o, meta), [k], {})
            method = o.lookup("__class_getitem__")
            if method is not _MISSING:
                return self.call(self.bind_attr(method, None, o), [k], {})
            raise DiasporeError(make_exc("TypeError", f"type '{o.name}' is not subscriptable"))
        if isinstance(o, DiasporeInstance):
            if o.cls.lookup("__getitem__") is _MISSING and o.prim is not None:
                if isinstance(o.prim, dict) and k not in o.prim \
                        and o.cls.lookup("__missing__") is not _MISSING:
                    return self.call_dunder(o, "__missing__", [k])
                return self.get_index(o.prim, k)
            return self.call_dunder_or_err(
                o, "__getitem__", [k], f"'{o.cls.name}' object is not subscriptable")
        try:
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
            o[k] = v
        except DiasporeError:
            raise
        except Exception as e:
            raise host_exc_to_diaspore(e) from None


    def truth(self, v) -> bool:
        """Evaluate truth using sandbox Boolean and length protocols."""
        if isinstance(v, DiasporeClass):
            meta = self.type_of(v)
            method = meta.lookup("__bool__")
            if method is not _MISSING:
                return self.truth(self.call(self.bind_attr(method, v, meta), [], {}))
            if meta.lookup("__len__") is not _MISSING:
                return self.diaspore_len(v) != 0
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

    def index_value(self, value, /):
        """Apply the exact integer-index protocol without numeric coercion."""
        raw = self._raw(value)
        if isinstance(raw, int):
            return int.__index__(raw)
        if not isinstance(value, DiasporeInstance) or value.cls.lookup("__index__") is _MISSING:
            raise DiasporeError(make_exc(
                "TypeError", f"'{type_name(value)}' object cannot be interpreted as an integer"))
        result = self.call_dunder(value, "__index__", [])
        raw = self._raw(result)
        if not isinstance(raw, int):
            raise DiasporeError(make_exc(
                "TypeError", f"__index__ returned non-int (type {type_name(result)})"))
        return int.__index__(raw)

    def diaspore_len(self, v) -> int:
        """Return a value's length through the sandbox protocol."""
        if isinstance(v, DiasporeClass):
            meta = self.type_of(v)
            method = meta.lookup("__len__")
            if method is not _MISSING:
                return self.call(self.bind_attr(method, v, meta), [], {})
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

    def iter_value(self, value):
        """Return the Python-visible iterator without replacing its identity."""
        if isinstance(value, DiasporeGenerator) and value.fn.node.get("asyncgen"):
            raise DiasporeError(make_exc("TypeError", "'async_generator' object is not iterable"))
        if isinstance(value, (DiasporeInstance, DiasporeClass)):
            cls = self.type_of(value)
            method = cls.lookup("__iter__")
            if method is not _MISSING:
                result = self.call(self.bind_attr(method, value, cls), [], {})
                valid = (result.cls.lookup("__next__") is not _MISSING
                         if isinstance(result, DiasporeInstance)
                         else hasattr(result, "__next__"))
                if not valid:
                    raise DiasporeError(make_exc(
                        "TypeError", f"iter() returned non-iterator of type '{type_name(result)}'"))
                return result
            if isinstance(value, DiasporeInstance) and value.prim is not None:
                return iter(value.prim)
        if isinstance(value, NativeObject) and value.kind == "tasks.Channel":
            return _ChannelIter(value)
        try:
            return iter(value)
        except TypeError:
            raise DiasporeError(make_exc(
                "TypeError", f"'{type_name(value)}' object is not iterable")) from None

    def as_host_iter(self, value):
        """Adapt a Python-visible iterator only at the host execution boundary."""
        iterator = self.iter_value(value)
        return _InstanceIter(self, iterator) if isinstance(iterator, DiasporeInstance) else iterator


    def exc_isa(self, v, clsname) -> bool:
        """Return whether a value matches a named sandbox exception class."""
        return isinstance(v, DiasporeInstance) and v.cls.issub(EXC_CLASSES[clsname])

    def exc_str(self, v: DiasporeInstance) -> str:
        """Render a sandbox exception message."""
        if v.cls.lookup("__str__") not in (_MISSING, OBJECT.ns.get("__str__")):
            return self.diaspore_str(v)
        args = v.d.get("args", ())
        if v.cls.issub(EXC_CLASSES["SyntaxError"]):
            syntax = SyntaxError(*args)
            for name in ("msg", "filename", "lineno", "offset", "text",
                         "end_lineno", "end_offset", "print_file_and_line"):
                setattr(syntax, name, v.d[name])
            return str(syntax)
        if v.cls.issub(EXC_CLASSES["OSError"]) and v.d.get("errno") is not None:
            message = f"[Errno {v.d['errno']}] {v.d['strerror']}"
            if v.d.get("filename") is not None:
                message += ": " + self.diaspore_repr(v.d["filename"])
            if v.d.get("filename2") is not None:
                message += " -> " + self.diaspore_repr(v.d["filename2"])
            return message
        unicode_types = {"UnicodeDecodeError": UnicodeDecodeError,
                         "UnicodeEncodeError": UnicodeEncodeError,
                         "UnicodeTranslateError": UnicodeTranslateError}
        if v.cls.name in unicode_types:
            return str(unicode_types[v.cls.name](*args))
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
        if isinstance(v, MappingProxyType):
            return self._mapping_repr(v)
        if isinstance(v, DiasporeInstance):
            if v.cls.issub(BASE_EXC) \
                    and v.cls.lookup("__str__") in (_MISSING, OBJECT.ns.get("__str__")):
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

    def _mapping_repr(self, value):
        if id(value) in self._repring:
            return "{...}"
        self._repring.add(id(value))
        try:
            return "{" + ", ".join(
                f"{self.diaspore_repr(key)}: {self.diaspore_repr(item)}"
                for key, item in value.items()) + "}"
        finally:
            self._repring.discard(id(value))

    def diaspore_repr(self, v) -> str:
        """Return a Python-style representation of a runtime value."""
        if isinstance(v, GenericAlias):
            origin = (self.get_attr(v.__origin__, "__qualname__")
                      if isinstance(v.__origin__, (BuiltinType, DiasporeClass))
                      else self.diaspore_repr(v.__origin__))
            return origin + "[" + ", ".join(
                self.get_attr(arg, "__qualname__") if isinstance(arg, (BuiltinType, DiasporeClass))
                else self.diaspore_repr(arg) for arg in v.__args__) + "]"
        if isinstance(v, Union):
            return " | ".join("None" if arg is self.types["NoneType"] else
                self.get_attr(arg, "__qualname__") if isinstance(arg, (BuiltinType, DiasporeClass))
                else self.diaspore_repr(arg) for arg in v.__args__)
        if isinstance(v, SimpleNamespace):
            return "namespace(" + ", ".join(
                f"{key}={self.diaspore_repr(value)}" for key, value in v.__dict__.items()) + ")"
        if isinstance(v, complex):
            return repr(v)  # CPython's complex repr is canonical: (3+4j), 1j
        if isinstance(v, (int, float, str, bytes, bytearray, bool, range)) or v is None \
                or v is Ellipsis or v is NotImplemented:
            return repr(v)
        if type(v) in _STRUCTSEQ_FIELDS:
            return repr(v)
        if isinstance(v, MappingProxyType):
            return "mappingproxy(" + self._mapping_repr(v) + ")"
        if isinstance(v, dict):
            return self._mapping_repr(v)
        if isinstance(v, (list, tuple)):
            if id(v) in self._repring:
                return "[...]" if isinstance(v, list) else "(...)"
            self._repring.add(id(v))
            try:
                if isinstance(v, list):
                    return "[" + ", ".join(self.diaspore_repr(x) for x in v) + "]"
                if isinstance(v, tuple):
                    if len(v) == 1:
                        return "(" + self.diaspore_repr(v[0]) + ",)"
                    return "(" + ", ".join(self.diaspore_repr(x) for x in v) + ")"
            finally:
                self._repring.discard(id(v))
        if isinstance(v, _DICT_VIEWS):
            if id(v) in self._repring:
                return "..."
            self._repring.add(id(v))
            try:
                return type(v).__name__ + "([" + ", ".join(
                    self.diaspore_repr(x) for x in v) + "])"
            finally:
                self._repring.discard(id(v))
        if isinstance(v, set):
            if not v:
                return "set()"
            return "{" + ", ".join(self.diaspore_repr(x) for x in v) + "}"
        if isinstance(v, frozenset):
            if not v:
                return "frozenset()"
            return "frozenset({" + ", ".join(self.diaspore_repr(x) for x in v) + "})"
        if isinstance(v, slice):
            parts = [
                self.diaspore_repr(v.start),
                self.diaspore_repr(v.stop),
                self.diaspore_repr(v.step),
            ]
            return f"slice({', '.join(parts)})"
        if isinstance(v, DiasporeInstance):
            if v.cls.issub(BASE_EXC) \
                    and v.cls.lookup("__repr__") in (_MISSING, OBJECT.ns.get("__repr__")):
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

    def _print(*args, sep=" ", end="\n", file=None, flush=False):
        sep = " " if sep is None else sep
        end = "\n" if end is None else end
        if not isinstance(sep, str) or not isinstance(end, str):
            raise TypeError("sep and end must be None or a string")
        if I.replaying:
            for value in args:
                I.diaspore_str(value)
            return
        writer = I.stdout.write if file is None else lambda text: I.call(
            I.get_attr(file, "write"), [text], {})
        try:
            for index, value in enumerate(args):
                if index:
                    writer(sep)
                writer(I.diaspore_str(value))
            writer(end)
        finally:
            if file is None:
                I.stdout.flush()
            elif flush:
                I.call(I.get_attr(file, "flush"), [], {})

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

    def _zip(*its, strict=False):
        return zip(*(I.as_host_iter(x) for x in its), strict=strict)

    def _enumerate(it, start=0):
        return enumerate(I.as_host_iter(it), start)

    def _reversed(seq):
        if isinstance(seq, DiasporeInstance):
            if seq.cls.lookup("__reversed__") is not _MISSING:
                return I.call_dunder(seq, "__reversed__", [])
            if seq.cls.lookup("__getitem__") is not _MISSING:
                return (I.get_index(seq, index) for index in range(I.diaspore_len(seq) - 1, -1, -1))
        if isinstance(seq, (list, tuple, str, range, bytes, bytearray, memoryview)) \
                or isinstance(seq, _DICT_VIEWS):
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

    def _isinstance(value, classes):
        if isinstance(classes, Union):
            classes = classes.__args__
        if isinstance(classes, tuple):
            return any(_isinstance(value, cls) for cls in classes)
        if not isinstance(classes, (DiasporeClass, BuiltinType)):
            raise TypeError("isinstance() arg 2 must be a type, a tuple of types, or a union")
        if I.type_of(value).issub(classes):
            return True
        meta = I.type_of(classes)
        hook = meta.lookup("__instancecheck__")
        if hook is not _MISSING:
            return I.truth(I.call(I.bind_attr(hook, classes, meta), [value], {}))
        return False

    def _issubclass(value, classes):
        if isinstance(classes, Union):
            classes = classes.__args__
        if isinstance(classes, tuple):
            return any(_issubclass(value, cls) for cls in classes)
        if not isinstance(value, (DiasporeClass, BuiltinType)):
            raise TypeError("issubclass() arg 1 must be a class")
        if not isinstance(classes, (DiasporeClass, BuiltinType)):
            raise TypeError("issubclass() arg 2 must be a class, a tuple of classes, or a union")
        meta = I.type_of(classes)
        hook = meta.lookup("__subclasscheck__")
        if hook is not _MISSING:
            return I.truth(I.call(I.bind_attr(hook, classes, meta), [value], {}))
        return value.issub(classes)

    def _type(value, *rest, **kwargs):
        if rest:
            if len(rest) != 2:
                raise TypeError("type() takes 1 or 3 arguments")
            bases, namespace = rest
            if not isinstance(value, str) or not isinstance(bases, tuple) \
                    or not isinstance(namespace, dict):
                raise TypeError("type() argument types: (str, tuple, dict)")
            return I.build_class(value, bases, namespace, kwargs=kwargs)
        if kwargs:
            raise TypeError("type() takes no keyword arguments")
        return I.type_of(value)

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
        return isinstance(v, (DiasporeFunction, BoundMethod, DiasporeClass,
                              BuiltinType, StaticMethod, GenericAlias)) or \
            (isinstance(v, DiasporeInstance) and v.cls.lookup("__call__") is not _MISSING) or \
            (not isinstance(v, DiasporeInstance) and callable(v))

    def _hash(v):
        return hash(v)

    def _open(path, mode="r", *, encoding=None):
        if mode not in ("r", "w", "a", "rb", "wb"):
            raise DiasporeError(make_exc("ValueError", f"invalid mode: '{mode}'"))
        if encoding is not None:
            if "b" in mode:
                raise DiasporeError(make_exc(
                    "ValueError", "binary mode doesn't take an encoding argument"))
            if encoding.lower().replace("_", "-") not in ("utf8", "utf-8"):
                raise DiasporeError(make_exc("NotImplementedError", "only UTF-8 text is supported"))
        real = I.require_fs("read" if mode in ("r", "rb") else "write", path)
        if "b" in mode:
            return DiasporeFile(open(real, mode))
        return DiasporeFile(open(real, mode, encoding="utf-8"))

    def _exit(code=0):
        raise DiasporeError(make_exc("SystemExit", code))

    def _compile(source, filename="<string>", mode="exec", flags=0,
                 dont_inherit=False, optimize=-1, *, _feature_version=-1):
        if flags & 1024:
            if I._module_ir("_ast") is None:
                raise ModuleNotFoundError(
                    "AST support is not bundled (bundle it with `import ast`)")
            module = I.import_module("_ast")
            return I.call(I.get_attr(module, "_compile"), [source, filename, mode], {
                "flags": flags, "dont_inherit": dont_inherit, "optimize": optimize,
                "_feature_version": _feature_version})
        return I.compile_snippet(source, filename, mode)

    def _exec(code, glob=None, loc=None):
        return I.exec_snippet(code, glob, loc, mode="exec")

    def _eval(code, glob=None, loc=None):
        return I.exec_snippet(code, glob, loc, mode="eval")

    def _vars(o=None):
        if o is None:
            frame = getattr(I.frame_state, "frame", None)
            return frame.vars if frame else {}
        return I.get_attr(o, "__dict__")

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
        if isinstance(o, NativeModule):
            return sorted(o.ns)
        if isinstance(o, BuiltinType):
            return sorted(o.ns)
        return []

    def _charge_sized(v):
        if isinstance(v, (range, list, tuple, str, bytes, dict, set, frozenset)):
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

    def _frozenset(v=()):
        if isinstance(v, frozenset):
            return v
        _charge_sized(v)
        return frozenset(I.as_host_iter(v))

    def _dict(*args, **kwargs):
        out = {}
        if len(args) > 1:
            raise DiasporeError(make_exc(
                "TypeError", f"dict expected at most 1 argument, got {len(args)}"))
        if args:
            I._update_mapping(out, args[0])
        out.update(kwargs)
        return out

    def _str(v="", *args, **kwargs):
        if args or kwargs:
            def decode(value, encoding="utf-8", errors="strict"):
                return I.codec(value, encoding, errors, True)
            return decode(I._raw(v), *args, **kwargs)
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

    def encode_string(value, encoding, errors="strict"):
        return I.codec(value, encoding, errors)

    def _bytes(v=b"", *args, **kwargs):
        if isinstance(v, DiasporeInstance):
            if v.cls.lookup("__bytes__") is not _MISSING:
                return I.call_dunder(v, "__bytes__", [])
            v = I._raw(v) if v.prim is not None else I.as_host_iter(v)
        if isinstance(v, str):
            return encode_string(v, *args, **kwargs)
        return bytes(v, *args, **kwargs)

    def _bytearray(v=b"", *args, **kwargs):
        if isinstance(v, DiasporeInstance):
            v = I._raw(v) if v.prim is not None else I.as_host_iter(v)
        if isinstance(v, str):
            return bytearray(encode_string(v, *args, **kwargs))
        return bytearray(v, *args, **kwargs)

    def _len(v):
        return I.diaspore_len(v)

    def _iter(v, *sentinel):
        if sentinel:
            if len(sentinel) != 1 or not _callable(v):
                raise TypeError("iter(v, w): v must be callable")
            return iter(lambda: I.call(v, [], {}), sentinel[0])
        return I.iter_value(v)

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
            raise DiasporeError(make_exc("EOFError", "EOF when reading a line"))
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
        "bytearray": BuiltinType("bytearray", (bytearray,), _bytearray),
        "memoryview": BuiltinType(
            "memoryview", (memoryview,), lambda value: memoryview(I._raw(value))),
        "list": BuiltinType("list", (list,), _list),
        "tuple": BuiltinType("tuple", (tuple,), _tuple),
        "dict": BuiltinType("dict", (dict,), _dict),
        "set": BuiltinType("set", (set,), _set),
        "frozenset": BuiltinType("frozenset", (frozenset,), _frozenset),
        "slice": BuiltinType("slice", (slice,), _slice),
        "dict_keys": BuiltinType("dict_keys", (_DICT_KEYS,), _DICT_KEYS),
        "dict_values": BuiltinType("dict_values", (_DICT_VALUES,), _DICT_VALUES),
        "dict_items": BuiltinType("dict_items", (_DICT_ITEMS,), _DICT_ITEMS),
        "range": BuiltinType("range", (range,), _range),
        "type": BuiltinType("type", (), _type),
        "NoneType": BuiltinType("NoneType", (type(None),), lambda: None),
        "complex": BuiltinType("complex", (complex,), _complex),
    }

    def _typing_union(left, right):
        provider = I.modules.get("_typing")
        alias_type = provider.frame.vars.get("TypeAliasType") if provider is not None else None
        for value in (left, right):
            if value is None or isinstance(
                    value, (BuiltinType, DiasporeClass, GenericAlias, Union)):
                continue
            if isinstance(value, DiasporeInstance) and value.cls is alias_type:
                continue
            return NotImplemented
        left = TYPES["NoneType"] if left is None else left
        right = TYPES["NoneType"] if right is None else right
        result = Union(left, right)
        return result.__args__[0] if len(result.__args__) == 1 else result

    def _abs(value, /):
        if isinstance(value, DiasporeInstance):
            if value.cls.lookup("__abs__") is not _MISSING:
                return I.call_dunder(value, "__abs__", [])
            raise TypeError(f"bad operand type for abs(): '{type_name(value)}'")
        return abs(value)

    def _round(number, ndigits=None):
        if isinstance(number, DiasporeInstance):
            if number.cls.lookup("__round__") is not _MISSING:
                return I.call_dunder(number, "__round__", [] if ndigits is None else [ndigits])
            raise TypeError(f"type {type_name(number)} doesn't define __round__ method")
        return round(number, ndigits)

    def _pow(base, exp, mod=None):
        if mod is None:
            return I.binop("pow", base, exp)
        if isinstance(base, DiasporeInstance) and base.cls.lookup("__pow__") is not _MISSING:
            result = I.call_dunder(base, "__pow__", [exp, mod])
            if result is not NotImplemented:
                return result
        return pow(I._raw(base), I._raw(exp), I._raw(mod))

    b = {
        "print": _print, "len": _len, "range": _range, "enumerate": _enumerate,
        "zip": _zip, "map": _map, "filter": _filter, "reversed": _reversed,
        "sorted": _sorted, "sum": _sum, "min": _min, "max": _max,
        "any": _any, "all": _all, "abs": _abs, "round": _round,
        "divmod": lambda left, right, /: I.binop("divmod", left, right),
        "pow": _pow, "ord": ord, "chr": lambda value: chr(I.index_value(value)),
        "hex": lambda value: hex(I.index_value(value)),
        "bin": lambda value: bin(I.index_value(value)),
        "oct": lambda value: oct(I.index_value(value)),
        "repr": _repr, "isinstance": _isinstance, "issubclass": _issubclass,
        "callable": _callable, "hash": _hash, "id": id, "iter": _iter, "next": _next,
        "getattr": _getattr, "setattr": lambda o, n, v: I.set_attr(o, n, v),
        "delattr": I.del_attr,
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
        "NotImplemented": NotImplemented,
        "_typing_union": _typing_union,
    }

    def _await_helper(x):
        if isinstance(x, AsyncGeneratorOperation):
            return x
        if isinstance(x, DiasporeGenerator) and not x.fn.node.get("asyncgen"):
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
        if name not in ("NoneType", "dict_keys", "dict_values", "dict_items"):
            b[name] = t
    b.update(EXC_CLASSES)
    b["IOError"] = b["EnvironmentError"] = EXC_CLASSES["OSError"]
    from .vm_support import install_types
    b.update(install_types(I, TYPES))
    b["globals"] = lambda: I.frame_state.frame.globals
    b["locals"] = lambda: I.frame_state.frame.vars
    b["__debug__"] = True
    def _import(name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            package = (globals or {}).get("__package__", "")
            if not package:
                raise ImportError("attempted relative import with no known parent package")
            parts = package.split(".")
            if level > len(parts):
                raise ImportError("attempted relative import beyond top-level package")
            name = ".".join(parts[:len(parts) - level + 1] + ([name] if name else []))
        module = I.import_module(name)
        if fromlist:
            for child in fromlist:
                full = name + "." + child
                if child != "*" and I._module_ir(full) is not None:
                    I.import_module(full)
            return module
        return I.modules.get(name.split(".")[0], module)
    b["__import__"] = _import
    return b



def make_native_modules(interp: Interp):
    """Build native modules using the interpreter's capabilities and journal."""
    I = interp

    def _os_getcwd():
        I.require_cap("fs")
        return os.getcwd()

    def _os_kind(path):
        real = I.require_fs("read", path)
        if os.path.isfile(real):
            return "file"
        if os.path.isdir(real):
            return "dir"
        return "other" if os.path.exists(real) else None

    os_mod = NativeModule("_os", {
        "getcwd": _os_getcwd,
        "kind": _os_kind,
        "listdir": lambda path: sorted(os.listdir(I.require_fs("read", path))),
        "realpath": lambda path: I.require_fs("read", path),
    })
    from .vm_native import filesystem_primitives
    os_mod.ns.update(filesystem_primitives(I))

    if I.deterministic:
        _clock = [0.0]

        def _time_base():
            _clock[0] = round(_clock[0] + 0.001, 6)
            return _clock[0]

        def _sleep_base(secs):
            _clock[0] = round(_clock[0] + secs, 6)
        _mono_base = _time_base
        _perf_base = _time_base
    else:
        _time_base = _hosttime.time
        _mono_base = _hosttime.monotonic
        _perf_base = _hosttime.perf_counter
        _sleep_base = _hosttime.sleep

    time_mod = NativeModule("time", {
        "time": I.journaled("time", "time", _time_base),
        "monotonic": I.journaled("time", "monotonic", _mono_base),
        "perf_counter": I.journaled("time", "perf_counter", _perf_base),
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
    for name in ("copysign", "frexp", "ldexp", "acos", "asin", "atan", "atan2",
                 "tau", "isfinite", "lgamma", "gamma", "expm1", "log1p", "trunc",
                 "degrees", "radians", "factorial", "gcd", "lcm", "comb", "perm",
                 "prod", "fsum", "remainder", "ulp", "nextafter", "isclose", "dist"):
        math_mod.ns[name] = getattr(_hostmath, name)

    def _sys_exit(code=0):
        raise DiasporeError(make_exc("SystemExit", code))

    def _stderr_write(s):
        if not I.replaying:
            I.stderr.write(s)

    def _stream_write(stream, text):
        if not isinstance(text, str):
            raise DiasporeError(make_exc("TypeError", "write() argument must be str"))
        if not I.replaying:
            stream.write(text)
        return len(text)

    def _setrecursionlimit(limit):
        if not isinstance(limit, int):
            raise DiasporeError(make_exc("TypeError", "an integer is required"))
        if limit < 1:
            raise DiasporeError(make_exc(
                "ValueError", "recursion limit must be greater or equal than 1"))
        if limit <= I.depth:
            raise DiasporeError(make_exc("RecursionError", "recursion limit is too low"))
        I.recursion_limit = min(limit, I.max_depth)

    sys_mod = NativeModule("sys", {
        "argv": list(I.argv),
        "exit": _sys_exit,
        "platform": "diaspore-py",
        "stderr_write": _stderr_write,
        "stdout": NativeObject("TextIOWrapper", {
            "write": lambda text: _stream_write(I.stdout, text),
            "flush": lambda: I.stdout.flush(),
        }),
        "stderr": NativeObject("TextIOWrapper", {
            "write": lambda text: _stream_write(I.stderr, text),
            "flush": lambda: I.stderr.flush(),
        }),
        "stdin": NativeObject("TextIOWrapper", {
            "read": lambda size=-1: _hostsys.stdin.read(size),
            "readline": lambda size=-1: _hostsys.stdin.readline(size),
        }),
        "getrecursionlimit": lambda: I.recursion_limit,
        "setrecursionlimit": _setrecursionlimit,
        "float_info": _hostsys.float_info,
        "int_info": _hostsys.int_info,
        "hash_info": _hostsys.hash_info,
    })
    version = VersionInfo(3, 12, 12, "final", 0)
    interned = {}

    def _intern(value):
        if not isinstance(value, str):
            raise TypeError("intern() argument must be str")
        return interned.setdefault(value, value)

    def _exc_info():
        value = I.exc_stack[-1] if I.exc_stack else None
        return ((value.cls, value, value.d.get("__traceback__"))
                if value is not None else (None, None, None))

    def _getframe(depth=0):
        if depth < 0:
            depth = 0
        frame = getattr(I.frame_state, "frame", None)
        while depth and frame is not None:
            frame = frame.back
            depth -= 1
        if frame is None:
            raise ValueError("call stack is not deep enough")
        return I.frame_view(frame)

    audit_hooks = []

    def _audit(event, *args):
        if not isinstance(event, str):
            raise TypeError("audit() argument 1 must be str")
        for hook in audit_hooks:
            I.call(hook, [event, args], {})

    def _addaudithook(hook):
        try:
            _audit("sys.addaudithook")
        except DiasporeError as error:
            if I.exc_isa(error.value, "RuntimeError"):
                return
            raise
        audit_hooks.append(hook)

    sys_mod.ns.update({
        "byteorder": _hostsys.byteorder, "maxsize": (1 << 63) - 1, "maxunicode": 0x10ffff,
        "version_info": version, "version": "3.12.12 (Diaspore)", "hexversion": 0x030c0cf0,
        "implementation": SimpleNamespace(name="diaspore", cache_tag="diaspore-312",
                                           version=version, hexversion=0x030c0cf0),
        "flags": SimpleNamespace(debug=0, inspect=0, interactive=0, optimize=0,
            dont_write_bytecode=1, no_user_site=1, no_site=1, ignore_environment=1,
            verbose=0, bytes_warning=0, quiet=0, hash_randomization=0, isolated=1,
            dev_mode=False, utf8_mode=1, warn_default_encoding=0, safe_path=True,
            int_max_str_digits=0),
        "modules": I.modules, "intern": _intern, "exc_info": _exc_info,
        "exception": lambda: I.exc_stack[-1] if I.exc_stack else None,
        "_getframe": _getframe, "getdefaultencoding": lambda: "utf-8",
        "getfilesystemencoding": lambda: "utf-8",
        "getfilesystemencodeerrors": lambda: "surrogateescape",
        "warnoptions": [], "path": [], "meta_path": [], "path_hooks": [],
        "path_importer_cache": {}, "executable": "", "prefix": "", "base_prefix": "",
        "exec_prefix": "", "base_exec_prefix": "", "dont_write_bytecode": True,
        "is_finalizing": lambda: I.closed,
        "audit": _audit, "addaudithook": _addaudithook,
    })

    def _exception_write(text):
        if not I.replaying:
            I.call(I.get_attr(sys_mod.ns["stderr"], "write"), [text], {})

    def _excepthook(exc_type, value, traceback):
        if traceback is not None:
            _exception_write("Traceback (most recent call last):\n")
            while traceback is not None:
                frame = I.get_attr(traceback, "tb_frame")
                code = I.get_attr(frame, "f_code")
                line = I.get_attr(traceback, "tb_lineno")
                _exception_write(
                    f'  File "{I.get_attr(code, "co_filename")}", line {line}, '
                    f'in {I.get_attr(code, "co_name")}\n')
                traceback = I.get_attr(traceback, "tb_next")
        name = I._exception_type_name(exc_type)
        message = I.diaspore_str(value)
        _exception_write(f"{name}: {message}\n" if message else f"{name}\n")

    def _unraisablehook(record):
        message = I.builtins["getattr"](record, "err_msg", None) or "Exception ignored in"
        value = I.get_attr(record, "exc_value")
        target = I.builtins["getattr"](record, "object", None)
        _exception_write(f"{message}: {I.diaspore_repr(target)}\n")
        _excepthook(I.get_attr(record, "exc_type"), value,
                     I.get_attr(record, "exc_traceback"))

    sys_mod.ns["unraisablehook"] = _unraisablehook
    sys_mod.ns["__unraisablehook__"] = _unraisablehook
    sys_mod.ns["excepthook"] = _excepthook
    sys_mod.ns["__excepthook__"] = _excepthook

    sockets = {}
    next_id = [1]

    def _close_sockets():
        for sock in tuple(sockets.values()):
            sock.close()
        sockets.clear()

    I.close_sockets = _close_sockets

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
        import socket as _socket
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.listen(64)
        return _reg(s)

    def _connect(host, port):
        I.require_net("connect", host, port)
        import socket as _socket
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

    def _select(read_ids, write_ids, error_ids, timeout=None):
        I.require_cap("net")
        import select

        groups = []
        identifiers = {}
        for values in (read_ids, write_ids, error_ids):
            group = []
            for value in I.as_host_iter(values):
                sid = I.index_value(value)
                if sid not in sockets:
                    raise OSError(9, "Bad file descriptor")
                sock = sockets[sid]
                identifiers[sock] = sid
                group.append(sock)
            groups.append(group)
        ready = select.select(*groups, timeout)
        return tuple([identifiers[sock] for sock in group] for group in ready)

    net_mod = NativeModule("net", {
        "listen": _listen, "connect": _connect, "accept": _accept,
        "recv": _recv, "send": _send, "close": _close, "peer": _peer, "select": _select,
    })

    def _flow_suspend(payload=None):
        if not I.durable:
            raise DiasporeError(make_exc(
                "RuntimeError",
                "flow.suspend() requires a durable run (diaspore run --durable)"))
        replayed, r = I.replay_next("flow", "suspend")
        if replayed:
            return r
        payload = I.journal_value(payload, "flow.suspend")
        I.suspended = True
        raise SuspendSig(payload)

    flow_mod = NativeModule("flow", {"suspend": _flow_suspend})
    from .vm_abc import make_abc_module
    from .vm_hash import make_hash_module
    from .vm_json import make_json_module

    def stdlib_id():
        from .stdlib_store import get_stdlib_id
        return get_stdlib_id()

    def stdlib_names():
        from .stdlib_store import stdlib_module_names
        return stdlib_module_names()

    def stdlib_module(name):
        from .stdlib_store import get_stdlib_module

        return get_stdlib_module(name)

    modules = {"time": time_mod, "math": math_mod,
               "sys": sys_mod, "net": net_mod,
               "flow": flow_mod, "_os": os_mod, "builtins": NativeModule("builtins", I.builtins),
               "re": make_re_module(I), "tasks": make_tasks_module(I),
               "_json": make_json_module(I, NativeModule),
               "_abc": make_abc_module(I, NativeModule),
               "_diaspore_hash": make_hash_module(I, NativeModule, NativeObject),
               "_operator": NativeModule("_operator", {
                   "index": I.index_value,
                   "add": lambda left, right, /: I.binop("add", left, right)}),
               "_diaspore_stdlib": NativeModule("_diaspore_stdlib", {
                   "get_id": stdlib_id, "module_names": stdlib_names,
                   "get_module": stdlib_module})}
    sys_mod.ns["builtin_module_names"] = tuple(sorted(set(modules) | {"posix"}))
    return modules


def make_re_module(I: Interp) -> NativeModule:
    """Expose host regex values through explicit sandbox pattern surfaces."""
    import re as hre

    patterns = weakref.WeakKeyDictionary()
    cache = {}

    def error_init(obj, msg, pattern=None, pos=None):
        error = hre.error(msg, pattern, pos)
        obj.d.update(args=error.args, msg=error.msg, pattern=error.pattern, pos=error.pos,
                     lineno=error.lineno, colno=error.colno)

    error_type = DiasporeClass("error", [EXC_CLASSES["Exception"]], {
        "__module__": "re", "__init__": error_init,
    })

    def wrap_match(match):
        if match is None:
            return None
        return NativeObject("re.Match", {
            "group": match.group, "groups": match.groups, "groupdict": match.groupdict,
            "start": match.start, "end": match.end, "span": match.span,
            "expand": match.expand, "__getitem__": match.__getitem__,
        }, {"string": match.string, "pos": match.pos, "endpos": match.endpos,
            "lastindex": match.lastindex, "lastgroup": match.lastgroup,
            "regs": match.regs, "re": compile_pattern(match.re.pattern, match.re.flags)})

    def replacement(value):
        if isinstance(value, (str, bytes)):
            return value
        if not I.builtins["callable"](value):
            raise TypeError("replacement must be a string or callable")
        return lambda match: I.call(value, [wrap_match(match)], {})

    def compile_pattern(pattern, flags=0):
        if isinstance(pattern, NativeObject) and pattern in patterns:
            if flags:
                raise ValueError("cannot process flags argument with a compiled pattern")
            return pattern
        if not isinstance(pattern, (str, bytes)):
            raise TypeError("first argument must be string or compiled pattern")
        key = (pattern, flags)
        if key in cache:
            return cache[key]
        try:
            host = hre.compile(pattern, flags)
        except hre.error as error:
            value = I.instantiate(error_type, [error.msg, error.pattern, error.pos], {})
            raise DiasporeError(value) from None
        def finditer(string, pos=0, endpos=_hostsys.maxsize):
            for match in host.finditer(string, pos, endpos):
                I.fuel()
                yield wrap_match(match)
        result = NativeObject("re.Pattern", {
            "match": lambda string, pos=0, endpos=_hostsys.maxsize: wrap_match(
                host.match(string, pos, endpos)),
            "search": lambda string, pos=0, endpos=_hostsys.maxsize: wrap_match(
                host.search(string, pos, endpos)),
            "fullmatch": lambda string, pos=0, endpos=_hostsys.maxsize: wrap_match(
                host.fullmatch(string, pos, endpos)),
            "findall": host.findall, "finditer": finditer, "split": host.split,
            "sub": lambda repl, string, count=0: host.sub(replacement(repl), string, count),
            "subn": lambda repl, string, count=0: host.subn(replacement(repl), string, count),
        }, {"pattern": host.pattern, "flags": host.flags, "groups": host.groups,
            "groupindex": dict(host.groupindex)})
        patterns[result] = host
        if len(cache) >= 512:
            cache.pop(next(iter(cache)))
        cache[key] = result
        return result

    def method(name, pattern, *args, flags=0):
        return compile_pattern(pattern, flags).methods[name](*args)

    namespace = {
        "compile": compile_pattern, "escape": hre.escape, "purge": cache.clear,
        "error": error_type,
        "match": lambda pattern, string, flags=0: method("match", pattern, string, flags=flags),
        "search": lambda pattern, string, flags=0: method("search", pattern, string, flags=flags),
        "fullmatch": lambda pattern, string, flags=0: method(
            "fullmatch", pattern, string, flags=flags),
        "findall": lambda pattern, string, flags=0: method("findall", pattern, string, flags=flags),
        "finditer": lambda pattern, string, flags=0: method(
            "finditer", pattern, string, flags=flags),
        "split": lambda pattern, string, maxsplit=0, flags=0: method(
            "split", pattern, string, maxsplit, flags=flags),
        "sub": lambda pattern, repl, string, count=0, flags=0: method(
            "sub", pattern, repl, string, count, flags=flags),
        "subn": lambda pattern, repl, string, count=0, flags=0: method(
            "subn", pattern, repl, string, count, flags=flags),
    }
    # CPython 3.12 flag values, fixed here so the sandbox does not change with
    # the host interpreter (3.13 removed TEMPLATE).
    flags = {"NOFLAG": 0, "TEMPLATE": 1, "IGNORECASE": 2, "LOCALE": 4, "MULTILINE": 8,
             "DOTALL": 16, "UNICODE": 32, "VERBOSE": 64, "DEBUG": 128, "ASCII": 256}
    for name, short in (("TEMPLATE", "T"), ("IGNORECASE", "I"), ("LOCALE", "L"),
                        ("MULTILINE", "M"), ("DOTALL", "S"), ("UNICODE", "U"),
                        ("VERBOSE", "X"), ("ASCII", "A")):
        flags[short] = flags[name]
    namespace.update(flags)
    namespace["Pattern"] = I.type_of(compile_pattern(""))
    namespace["Match"] = I.type_of(wrap_match(hre.match("", "")))
    return NativeModule("re", namespace)


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
                try:
                    context_module = I.modules.get("_contextvars")
                    if isinstance(context_module, DiasporeModule):
                        cleanup = context_module.frame.vars.get("_clear_thread_context")
                        if cleanup is not None:
                            ident = getattr(I.frame_state, "ident", threading.get_ident())
                            I.call(cleanup, [ident], {})
                except BaseException as error:
                    if state["error"] is None:
                        state["error"] = error if isinstance(
                            error, (DiasporeError, DiasporeTrap)) else host_exc_to_diaspore(error)
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
        hl = threading.Lock()
        return NativeObject("tasks.Lock", {
            "acquire": lambda blocking=True, timeout=-1: hl.acquire(blocking, timeout),
            "locked": hl.locked,
            "release": lambda: (hl.release(), None)[1],
            "__enter__": lambda: (hl.acquire(), None)[1],
            "__exit__": lambda *a: (hl.release(), False)[1],
        })

    def _interrupt_main(signum=2):
        if signum != 2:
            raise ValueError("only SIGINT is supported")
        I.pending_interrupt = True

    return NativeModule("tasks", {
        "spawn": _spawn, "channel": _channel, "lock": _lock,
        "get_ident": lambda: getattr(I.frame_state, "ident", threading.get_ident()),
        "main_ident": lambda: I.main_ident,
        "interrupt_main": _interrupt_main,
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



def _check_stdlib_reference(value, source="stdlib"):
    if (not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in value[7:])):
        raise ValueError(f"{source} reference must be sha256:<64 lowercase hexadecimal digits>")


def bundle_digest(bundle: dict) -> str:
    """Return the canonical bundle SHA-256 digest (docs/spec.md section 14)."""
    text = _hostjson.dumps(bundle, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8", "backslashreplace")).hexdigest()


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

    def close(self):
        """Join sandbox threads, run exit callbacks, and release owned files."""
        self.interp.close()

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
        snapshot_stdlib = snapshot.get("stdlib")
        if "stdlib" in snapshot:
            _check_stdlib_reference(snapshot_stdlib, "snapshot stdlib")
        bundle_stdlib = self._kw["bundle"].get("stdlib")
        if snapshot_stdlib != bundle_stdlib:
            raise ValueError(
                f"snapshot stdlib mismatch: snapshot references {snapshot_stdlib}, "
                f"this bundle references {bundle_stdlib}")
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
            if interp.stdlib_id is not None:
                snapshot["stdlib"] = interp.stdlib_id
            return Suspended(s.payload, snapshot)
        return Completed(code)
