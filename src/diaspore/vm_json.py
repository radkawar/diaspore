"""Expose CPython JSON accelerators through sandbox callbacks and exceptions."""

import _json
import json


def make_json_module(interp, module_type):
    """Build the private accelerator used by the unchanged JSON package.

    Args:
        interp: Interpreter owning callback execution and exception values.
        module_type: Constructor for a sandbox native module.

    Returns:
        Native module containing the five CPython JSON accelerator entry points.
    """
    from .vm import BuiltinType, DiasporeError, DiasporeInstance

    def decode_error(error):
        decoder = interp.import_module("json.decoder")
        cls = interp.get_attr(decoder, "JSONDecodeError")
        return DiasporeError(interp.call(cls, [error.msg, error.doc, error.pos], {}))

    def callback(function, primitive=None):
        if primitive is not None and isinstance(function, BuiltinType) \
                and function is interp.types[primitive.__name__]:
            return primitive
        if function is None:
            return None
        return lambda *args: interp.call(function, list(args), {})

    def string_arguments(args):
        if args and isinstance(args[0], DiasporeInstance) \
                and isinstance(args[0].prim, str):
            return (args[0].prim,) + args[1:]
        return args

    def scanstring(*args, **kwargs):
        args = string_arguments(args)
        if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], int):
            interp.fuel_n(max(0, len(args[0]) - args[1]) // 16 + 1)
        try:
            return _json.scanstring(*args, **kwargs)
        except json.JSONDecodeError as error:
            raise decode_error(error) from None

    class ScannerContext:
        """Resolve scanner options exclusively through sandbox attributes."""

        def __init__(self, context):
            """Retain the sandbox decoder context."""
            self.context = context

        def __getattr__(self, name):
            """Adapt scanner hooks without exposing sandbox values to host calls."""
            value = interp.get_attr(self.context, name)
            if name == "strict":
                return interp.truth(value)
            primitive = {"parse_int": int, "parse_float": float}.get(name)
            return callback(value, primitive)

    def make_scanner(*args, **kwargs):
        if args:
            args = (ScannerContext(args[0]),) + args[1:]
        if "context" in kwargs:
            kwargs["context"] = ScannerContext(kwargs["context"])
        scanner = _json.make_scanner(*args, **kwargs)

        def scan_once(*args, **kwargs):
            args = string_arguments(args)
            if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], int):
                interp.fuel_n(max(0, len(args[0]) - args[1]) // 16 + 1)
            try:
                return scanner(*args, **kwargs)
            except json.JSONDecodeError as error:
                raise decode_error(error) from None

        return scan_once

    def make_encoder(*args, **kwargs):
        normalized = {}
        visited = set()
        key_types = {}
        key_originals = {}

        def key_comparison(kind):
            def compare(left, right):
                return interp.compare(
                    kind, key_originals[id(left)], key_originals.get(id(right), right))
            return compare

        def encode_key(value):
            if not isinstance(value, DiasporeInstance):
                return value
            raw = interp._raw(value)
            if value.cls not in key_types:
                namespace = {"__hash__": object.__hash__}
                for kind in ("eq", "ne", "lt", "le", "gt", "ge"):
                    namespace["__" + kind + "__"] = key_comparison(kind)
                base = type(raw) if isinstance(raw, (str, int, float)) else object
                key_types[value.cls] = type(value.cls.name, (base,), namespace)
            key_type = key_types[value.cls]
            key = key_type(raw) if isinstance(raw, (str, int, float)) else key_type()
            key_originals[id(key)] = value
            return key

        def needs_conversion(value):
            raw = value.prim if isinstance(value, DiasporeInstance) else value
            if not isinstance(raw, (dict, list, tuple)) or id(raw) in visited:
                return False
            visited.add(id(raw))
            if isinstance(raw, dict):
                return any(isinstance(key, DiasporeInstance) for key in raw) or any(
                    needs_conversion(item) for item in raw.values())
            return any(needs_conversion(item) for item in raw)

        def prepare(value):
            visited.clear()
            if needs_conversion(value):
                return encode_tree(value)
            return interp._raw(value)

        def encode_tree(value):
            raw = value.prim if isinstance(value, DiasporeInstance) else value
            if not isinstance(raw, (dict, list, tuple)):
                return raw if isinstance(raw, (str, int, float)) else value
            identity = id(value)
            if identity in normalized:
                return normalized[identity]
            if isinstance(raw, dict):
                result = {}
                normalized[identity] = result
                for key, item in raw.items():
                    result[encode_key(key)] = encode_tree(item)
                return result
            result = []
            normalized[identity] = result
            result.extend(encode_tree(item) for item in raw)
            return result

        def adapt(name, value):
            if name == "default":
                def encode_default(obj):
                    if isinstance(obj, DiasporeInstance) and obj.prim is not None:
                        return prepare(obj)
                    return prepare(interp.call(value, [obj], {}))
                return encode_default
            if name == "encoder":
                if value is encode_basestring:
                    return _json.encode_basestring
                if value is encode_basestring_ascii:
                    return _json.encode_basestring_ascii
                return callback(value)
            if name in ("sort_keys", "skipkeys", "allow_nan"):
                return interp.truth(value)
            return value

        names = ("markers", "default", "encoder", "indent", "key_separator",
                 "item_separator", "sort_keys", "skipkeys", "allow_nan")
        args = tuple(adapt(names[index], value) if index < len(names) else value
                     for index, value in enumerate(args))
        kwargs = {name: adapt(name, value) for name, value in kwargs.items()}
        host_encoder = _json.make_encoder(*args, **kwargs)

        def iterencode(*args, **kwargs):
            normalized.clear()
            key_originals.clear()
            if args:
                args = (prepare(args[0]),) + args[1:]
            if "obj" in kwargs:
                kwargs["obj"] = prepare(kwargs["obj"])
            try:
                chunks = host_encoder(*args, **kwargs)
                interp.fuel_n(sum(len(chunk) for chunk in chunks) // 16 + 1)
                return chunks
            finally:
                normalized.clear()
                visited.clear()
                key_originals.clear()

        return iterencode

    def encode_basestring(*args, **kwargs):
        args = string_arguments(args)
        if args and isinstance(args[0], str):
            interp.fuel_n(len(args[0]) // 16 + 1)
        return _json.encode_basestring(*args, **kwargs)

    def encode_basestring_ascii(*args, **kwargs):
        args = string_arguments(args)
        if args and isinstance(args[0], str):
            interp.fuel_n(len(args[0]) // 16 + 1)
        return _json.encode_basestring_ascii(*args, **kwargs)

    return module_type("_json", {
        "scanstring": scanstring,
        "make_scanner": make_scanner,
        "make_encoder": make_encoder,
        "encode_basestring": encode_basestring,
        "encode_basestring_ascii": encode_basestring_ascii,
    })
