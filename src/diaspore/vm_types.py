"""Sandbox-owned metadata and formatting adapters for the reference VM.

These objects describe interpreted code and bindings, never Python host frames.
Attribute access remains subject to the VM's explicit allowlists.
"""

from __future__ import annotations

import _string
import string
from collections import namedtuple
from collections.abc import MutableMapping

VersionInfo = namedtuple("version_info", "major minor micro releaselevel serial")


class ClassNamespace(MutableMapping):
    """Adapt a sandbox mapping for interpreter frame binding operations."""

    def __init__(self, interp, mapping):
        """Retain the sandbox interpreter and mapping value."""
        self.interp = interp
        self.mapping = mapping

    def __getitem__(self, name):
        """Read a binding through the sandbox mapping protocol."""
        return self.interp.get_index(self.mapping, name)

    def __setitem__(self, name, value):
        """Write a binding through the sandbox mapping protocol."""
        self.interp.set_index(self.mapping, name, value)

    def __delitem__(self, name):
        """Delete a binding through the sandbox mapping protocol."""
        self.interp.call_dunder(self.mapping, "__delitem__", [name])

    def __iter__(self):
        """Iterate the sandbox mapping's keys."""
        return self.interp.as_host_iter(self.mapping)

    def __len__(self):
        """Return the sandbox mapping's size."""
        return self.interp.diaspore_len(self.mapping)

    def __contains__(self, name):
        """Check whether the sandbox mapping contains a key."""
        return self.interp.contains(self.mapping, name)


class CellNamespace(dict):
    """Keep FunctionType-created closures linked to their original cells."""

    def __init__(self, bindings):
        """Index the live cells by their lexical names."""
        super().__init__()
        self.cells = dict(bindings)

    def __contains__(self, name):
        """Return whether a cell currently contains a value."""
        return name in self.cells and self.cells[name].name in self.cells[name].namespace

    def __getitem__(self, name):
        """Read a live cell, rejecting unknown bindings."""
        if name not in self.cells:
            raise KeyError(name)
        return self.cells[name].cell_contents

    def __setitem__(self, name, value):
        """Replace the value held by a live cell."""
        self.cells[name].cell_contents = value

    def __delitem__(self, name):
        """Clear the value held by a live cell."""
        del self.cells[name].cell_contents


class Cell:
    """Reference one live lexical binding."""

    def __init__(self, namespace=None, name="value"):
        """Link a cell to its namespace and binding name."""
        self.namespace = {} if namespace is None else namespace
        self.name = name

    @property
    def cell_contents(self):
        """Return the current value, rejecting an empty cell."""
        if self.name not in self.namespace:
            raise ValueError("Cell is empty")
        return self.namespace[self.name]

    @cell_contents.setter
    def cell_contents(self, value):
        self.namespace[self.name] = value

    @cell_contents.deleter
    def cell_contents(self):
        self.namespace.pop(self.name, None)


def free_bindings(fn):
    """Find lexical references in IR without consulting host Python code."""
    names = set(fn.ndecl)

    def visit(value, bound):
        if isinstance(value, list):
            for item in value:
                visit(item, bound)
        elif isinstance(value, dict):
            op = value.get("op")
            if op == "name" and value["id"] not in bound:
                names.add(value["id"])
            elif op in ("func", "funcexpr"):
                visit(value.get("body", []), bound | set(value.get("locals", ())))
                visit(value.get("params", {}).get("defaults", []), bound)
            else:
                for item in value.values():
                    if isinstance(item, (dict, list)):
                        visit(item, bound)

    visit(fn.node.get("body", []), fn.localset | fn.gdecl)
    result = []
    for name in sorted(names):
        frame = fn.closure
        while frame is not None:
            if frame.localset is not None and name in frame.localset:
                result.append((name, Cell(frame.vars, name)))
                break
            frame = frame.parent
    return result


class Code:
    """Describe executable VM IR with Python-compatible code metadata."""

    def __init__(self, fn):
        """Derive code metadata from the function's executable IR."""
        self.node = fn.node
        self.co_name = fn.name
        self.co_qualname = fn.metadata.get("__qualname__", fn.name)
        self.co_filename = (
            fn.module.frame.vars.get("__file__", "<string>") if fn.module else "<string>")
        self.co_firstlineno = fn.node.get("ln", 1)
        self.co_argcount = len(fn.params["args"])
        self.co_posonlyargcount = fn.node.get("params", {}).get("posonly", 0)
        self.co_kwonlyargcount = len(fn.params["kwonly"])
        params = list(fn.params["args"]) + list(fn.params["kwonly"])
        params += [name for name in (fn.params["vararg"], fn.params["kwarg"]) if name]
        self.co_varnames = tuple(params + sorted(fn.localset - set(params)))
        self.co_nlocals = len(self.co_varnames)
        self.co_freevars = tuple(name for name, _ in fn.cells)
        self.co_flags = 3 | (4 if fn.params["vararg"] else 0) | (8 if fn.params["kwarg"] else 0)
        if fn.node.get("asyncgen"):
            self.co_flags |= 512
        elif fn.node.get("coro"):
            self.co_flags |= 128
        elif fn.gen:
            self.co_flags |= 32
        if fn.closure and fn.closure.localset is not None:
            self.co_flags |= 16

    def replace(self, **kwargs):
        """Copy metadata with a compatible iterable-coroutine flag change."""
        result = object.__new__(Code)
        result.__dict__.update(self.__dict__)
        for name, value in kwargs.items():
            if name not in self.__dict__ or not name.startswith("co_"):
                raise TypeError(f"'{name}' is an invalid keyword argument for replace()")
            if name != "co_flags" or not isinstance(value, int) \
                    or (value ^ self.co_flags) & ~0x100:
                raise NotImplementedError(
                    "IR code replacement supports only the iterable-coroutine flag")
            setattr(result, name, value)
        return result


class FrameView:
    """Expose only a sandbox frame's own namespaces and source location."""

    def __init__(self, frame, code=None, back=None):
        """Expose one interpreter frame and its caller link."""
        self.frame = frame
        self.f_code = code
        self.f_back = back
        self.f_lasti = -1
        self.f_trace = None

    @property
    def f_locals(self):
        """Return the frame's live local namespace."""
        return self.frame.vars

    @property
    def f_globals(self):
        """Return the frame's global namespace."""
        return self.frame.globals

    @property
    def f_lineno(self):
        """Return the current IR statement's source line."""
        return self.frame.line


class Traceback:
    """Link sandbox exception locations without exposing host tracebacks."""

    def __init__(self, next_, frame, lasti, lineno):
        """Store a traceback location and the next sandbox frame."""
        self.tb_next = next_
        self.tb_frame = frame
        self.tb_lasti = lasti
        self.tb_lineno = lineno


class Descriptor:
    """Bind an explicitly approved primitive operation or metadata accessor."""

    def __init__(self, name, owner, call=None, get=None, kind="method_descriptor",
                 set=None, delete=None):
        """Describe a primitive method or a managed metadata accessor."""
        self.name = name
        self.owner = owner
        self.call = call
        self.get = get
        self.kind = kind
        self.set = set
        self.delete = delete

    def __call__(self, *args, **kwargs):
        """Invoke the approved primitive callable."""
        if self.call is None:
            raise TypeError(f"'{self.kind}' object is not callable")
        return self.call(*args, **kwargs)


class GenericAlias:
    """Retain a parameterized type's origin and arguments."""

    def __init__(self, origin, args):
        """Store the origin and normalized type arguments."""
        self.__origin__ = origin
        self.__args__ = args if isinstance(args, tuple) else (args,)
        self.__parameters__ = tuple(
            arg for arg in self.__args__ if hasattr(arg, "__typing_subst__"))

    def __eq__(self, other):
        """Compare origins and arguments structurally."""
        return (isinstance(other, GenericAlias) and self.__origin__ == other.__origin__
                and self.__args__ == other.__args__)

    def __hash__(self):
        """Hash the origin and normalized type arguments."""
        return hash((self.__origin__, self.__args__))


class Union:
    """Store flattened, identity-deduplicated union operands."""

    def __init__(self, left, right):
        """Flatten nested unions while preserving the first occurrence order."""
        args = []
        for value in (left, right):
            for arg in value.__args__ if isinstance(value, Union) else (value,):
                if not any(arg is old or arg == old for old in args):
                    args.append(arg)
        self.__args__ = tuple(args)
        self.__parameters__ = ()

    def __eq__(self, other):
        """Compare union members without regard to their order."""
        return isinstance(other, Union) and frozenset(self.__args__) == frozenset(other.__args__)

    def __hash__(self):
        """Hash the union's unordered member set."""
        return hash(frozenset(self.__args__))


class NoPositionals:
    """Reject positional fields in str.format_map without sharing mutable state."""

    def __getitem__(self, key):
        """Reject positional lookup in a mapping-only format operation."""
        raise ValueError("Format string contains positional fields")


class Formatter(string.Formatter):
    """Route format-field traversal through sandbox attribute checks."""

    def __init__(self, interp):
        """Retain the interpreter used for formatting protocol dispatch."""
        self.interp = interp

    def _vformat(self, format_string, args, kwargs, used_args, recursion_depth,
                 auto_arg_index=0):
        if recursion_depth < 0:
            raise ValueError("Max string recursion exceeded")
        result = []
        for literal, field_name, format_spec, conversion in self.parse(format_string):
            result.append(literal)
            if field_name is None:
                continue
            first, _ = _string.formatter_field_name_split(field_name)
            if first == "":
                if auto_arg_index is False:
                    raise ValueError(
                        "cannot switch from manual field specification "
                        "to automatic field numbering")
                field_name = str(auto_arg_index) + field_name
                auto_arg_index += 1
            elif isinstance(first, int):
                if auto_arg_index:
                    raise ValueError(
                        "cannot switch from automatic field numbering "
                        "to manual field specification")
                auto_arg_index = False
            value, used = self.get_field(field_name, args, kwargs)
            used_args.add(used)
            value = self.convert_field(value, conversion)
            format_spec, auto_arg_index = self._vformat(
                format_spec, args, kwargs, used_args, recursion_depth - 1, auto_arg_index)
            result.append(self.format_field(value, format_spec))
        return "".join(result), auto_arg_index

    def get_field(self, field_name, args, kwargs):
        """Resolve field traversal using sandbox attributes and indices."""
        first, rest = _string.formatter_field_name_split(field_name)
        obj = self.get_value(first, args, kwargs)
        for attr, key in rest:
            self.interp.fuel()
            obj = self.interp.get_attr(obj, key) if attr else self.interp.get_index(obj, key)
        return obj, first

    def get_value(self, key, args, kwargs):
        """Read a positional argument or mapping field."""
        if isinstance(key, int):
            try:
                return args[key]
            except IndexError:
                raise IndexError(
                    f"Replacement index {key} out of range for positional args tuple") from None
        return self.interp.get_index(kwargs, key)

    def format_field(self, value, format_spec):
        """Dispatch a value's sandbox formatting protocol."""
        return self.interp.format_value(value, format_spec)

    def convert_field(self, value, conversion):
        """Apply the requested string conversion inside the sandbox."""
        if conversion == "s":
            return self.interp.diaspore_str(value)
        if conversion == "r":
            return self.interp.diaspore_repr(value)
        if conversion == "a":
            return self.interp.diaspore_ascii(value)
        return super().convert_field(value, conversion)


class PercentValue:
    """Adapt percent-format conversions without exposing host object reprs."""

    def __init__(self, interp, value):
        """Retain a value and the interpreter used for its conversions."""
        self.interp = interp
        self.value = value

    def __str__(self):
        """Return the value's sandbox string form."""
        return self.interp.diaspore_str(self.value)

    def __repr__(self):
        """Return the value's sandbox representation."""
        return self.interp.diaspore_repr(self.value)

    def __int__(self):
        """Invoke the sandbox integer conversion."""
        return self.interp.call(self.interp.builtins["int"], [self.value], {})

    def __index__(self):
        """Invoke the sandbox integer-index protocol."""
        return self.interp.call_dunder(self.value, "__index__", [])

    def __float__(self):
        """Invoke the sandbox floating-point conversion."""
        return self.interp.call(self.interp.builtins["float"], [self.value], {})

    def __bytes__(self):
        """Invoke the sandbox byte conversion."""
        return self.interp.call_dunder(self.value, "__bytes__", [])

    def __getitem__(self, key):
        """Resolve and adapt a mapping field."""
        return percent_value(self.interp, self.interp.get_index(self.value, key))


def percent_value(interp, value):
    """Adapt non-primitive percent-format values to sandbox conversions."""
    if value is None or isinstance(
            value, (str, bytes, bytearray, memoryview, int, float, complex, bool)):
        return value
    if isinstance(value, tuple):
        return tuple(percent_value(interp, item) for item in value)
    return PercentValue(interp, value)
