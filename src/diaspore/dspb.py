"""Encode and decode the DSPB binary bundle container (docs/dspb.md).

DSPB stores the JSON semantic model using opcode bytes, LEB128 integers, and
an interned string table. Decoding an encoded bundle produces its compact
form, so validation and digests remain independent of the container.
"""

from __future__ import annotations

import io
import sys

from .validator import BundleValidationError, _stdlib_id

MAGIC = b"DSPB01"
MAGIC_LINKED = b"DSPB02"

OPS = [
    "const",
    "name",
    "attr",
    "index",
    "slicelit",
    "starred",
    "call",
    "binop",
    "unary",
    "boolop",
    "compare",
    "cond",
    "list",
    "tuple",
    "set",
    "dict",
    "fstring",
    "func",
    "funcexpr",
    "yield",
    "yieldfrom",
    "namedexpr",
    "expr",
    "assign",
    "augassign",
    "if",
    "while",
    "for",
    "break",
    "continue",
    "pass",
    "return",
    "class",
    "raise",
    "try",
    "with",
    "import",
    "importfrom",
    "del",
]
BIN_KINDS = [
    "add",
    "sub",
    "mul",
    "div",
    "floordiv",
    "mod",
    "pow",
    "lshift",
    "rshift",
    "bitand",
    "bitor",
    "bitxor",
    "matmul",
]
CMP_KINDS = ["eq", "ne", "lt", "le", "gt", "ge", "in", "notin", "is", "isnot"]
UNARY_KINDS = ["neg", "pos", "not", "invert"]
CONST_KINDS = ["int", "float", "str", "bytes", "bool", "none", "ellipsis", "complex"]
CONV_KINDS = [None, "r", "s", "a"]

_OP_IDX = {op: i for i, op in enumerate(OPS)}
_BIN_IDX = {k: i for i, k in enumerate(BIN_KINDS)}
_CMP_IDX = {k: i for i, k in enumerate(CMP_KINDS)}
_UN_IDX = {k: i for i, k in enumerate(UNARY_KINDS)}
_CONST_IDX = {k: i for i, k in enumerate(CONST_KINDS)}
_CONV_IDX = {k: i for i, k in enumerate(CONV_KINDS)}


class DSPBError(ValueError):
    """An invalid DSPB container or unsupported bundle value."""

    pass


class _Writer:
    def __init__(self):
        """Initialize the output buffer and interned string table."""
        self.buf = io.BytesIO()
        self.strings: dict[str, int] = {}
        self.strlist: list[str] = []

    def u(self, n: int) -> None:
        """Write an unsigned LEB128 integer."""
        if n < 0:
            raise DSPBError("negative varint")
        while True:
            b = n & 0x7F
            n >>= 7
            if n:
                self.buf.write(bytes([b | 0x80]))
            else:
                self.buf.write(bytes([b]))
                return

    def byte(self, b: int) -> None:
        """Write one byte."""
        self.buf.write(bytes([b]))

    def s(self, text: str) -> None:
        """Intern a string and write its table index."""
        idx = self.strings.get(text)
        if idx is None:
            idx = len(self.strlist)
            self.strings[text] = idx
            self.strlist.append(text)
        self.u(idx)


_MAX_DEPTH = 8000  # Bound nesting in untrusted input.


def _depth_budget() -> int:
    # A child can add _dec, _dec_inner, _dec_nodes, and a comprehension frame.
    # Leave room for scalar readers and constructing the validation error.
    frame = sys._getframe()
    available = sys.getrecursionlimit() - 32
    while frame is not None:
        available -= 1
        frame = frame.f_back
    return min(_MAX_DEPTH, max(0, available // 4))


class _Reader:
    def __init__(self, data: bytes, pos: int):
        """Read from an initial byte offset with an empty string table."""
        self.data = data
        self.pos = pos
        self.strings: list[str] = []
        self.depth = 0
        self.max_depth = _depth_budget()

    def u(self) -> int:
        """Read an unsigned LEB128 integer."""
        shift = n = 0
        while True:
            if self.pos >= len(self.data):
                raise DSPBError("truncated varint")
            b = self.data[self.pos]
            self.pos += 1
            n |= (b & 0x7F) << shift
            if not (b & 0x80):
                return n
            shift += 7
            if shift > 63:
                raise DSPBError("varint too long")

    def count(self) -> int:
        """Read a length prefix bounded by the remaining byte count.

        Each counted element occupies at least one byte. Check this before
        allocating storage proportional to an untrusted count.
        """
        n = self.u()
        if n > len(self.data) - self.pos:
            raise DSPBError("count exceeds remaining data")
        return n

    def byte(self) -> int:
        """Read one byte."""
        if self.pos >= len(self.data):
            raise DSPBError("truncated")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def s(self) -> str:
        """Read a string reference and resolve it in the table."""
        idx = self.u()
        if idx >= len(self.strings):
            raise DSPBError("bad string ref")
        return self.strings[idx]


# Nodes begin with opcode and flags bytes; bit zero marks a source line.
# Higher bits mark optional fields in the order specified by docs/dspb.md.


def _flags(node: dict, opts: list[str]) -> int:
    f = 1 if node.get("ln") is not None else 0
    for i, key in enumerate(opts):
        if node.get(key) is not None:
            f |= 1 << (i + 1)
    return f


# Optional fields consume flag bits after the source-line flag.
_OPT: dict[str, list[str]] = {
    "slicelit": ["lower", "upper", "step"],
    "yield": ["v"],
    "return": ["v"],
    "raise": ["exc", "cause"],
    "import": ["alias"],
}


def _enc_nodes(w: _Writer, nodes: list) -> None:
    w.u(len(nodes))
    for n in nodes:
        _enc(w, n)


def _enc_strs(w: _Writer, items: list) -> None:
    w.u(len(items))
    for s in items:
        w.s(s)


def _enc_opt(w: _Writer, node) -> None:
    if node is not None:
        _enc(w, node)


def _literal_fields(value: str, key: str) -> dict:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        return {
            key: value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass"),
            "codepoints": [ord(char) for char in value],
        }
    return {key: value}


def _enc(w: _Writer, node: dict) -> None:
    op = node["op"]
    w.byte(_OP_IDX[op])
    w.byte(_flags(node, _OPT.get(op, [])))
    if node.get("ln") is not None:
        w.u(node["ln"])
    match op:
        case "const":
            kind = node["t"]
            w.byte(_CONST_IDX[kind])
            if kind in ("int", "float", "str", "bytes"):
                w.s("".join(map(chr, node["codepoints"]))
                    if kind == "str" and "codepoints" in node else node["v"])
            elif kind == "complex":
                w.s(node["re"])
                w.s(node["im"])
            elif kind == "bool":
                w.byte(1 if node["v"] else 0)
        case "name":
            w.s(node["id"])
        case "attr":
            _enc(w, node["o"])
            w.s(node["n"])
        case "index":
            _enc(w, node["o"])
            _enc(w, node["k"])
        case "slicelit":
            _enc_opt(w, node.get("lower"))
            _enc_opt(w, node.get("upper"))
            _enc_opt(w, node.get("step"))
        case "starred" | "yieldfrom" | "expr":
            _enc(w, node["v"])
        case "call":
            _enc(w, node["f"])
            _enc_nodes(w, node.get("args", []))
            kws = node.get("kw", [])
            w.u(len(kws))
            for kw in kws:
                if kw["n"] is None:
                    w.byte(0)
                else:
                    w.byte(1)
                    w.s(kw["n"])
                _enc(w, kw["v"])
        case "binop" | "augassign":
            w.byte(_BIN_IDX[node["kind"]])
            if op == "binop":
                _enc(w, node["l"])
                _enc(w, node["r"])
            else:
                _enc(w, node["target"])
                _enc(w, node["v"])
        case "unary":
            w.byte(_UN_IDX[node["kind"]])
            _enc(w, node["v"])
        case "boolop":
            w.byte(0 if node["kind"] == "and" else 1)
            _enc_nodes(w, node["values"])
        case "compare":
            _enc(w, node["left"])
            w.u(len(node["ops"]))
            for k in node["ops"]:
                w.byte(_CMP_IDX[k])
            _enc_nodes(w, node["comps"])
        case "cond":
            _enc(w, node["test"])
            _enc(w, node["then"])
            _enc(w, node["else"])
        case "list" | "tuple" | "set":
            _enc_nodes(w, node.get("elts", []))
        case "dict":
            entries = node.get("entries", [])
            w.u(len(entries))
            for e in entries:
                if e["k"] is None:
                    w.byte(0)
                else:
                    w.byte(1)
                    _enc(w, e["k"])
                _enc(w, e["v"])
        case "fstring":
            parts = node["parts"]
            w.u(len(parts))
            for p in parts:
                if "s" in p:
                    w.byte(0)
                    w.s("".join(map(chr, p["codepoints"])) if "codepoints" in p else p["s"])
                else:
                    flag = 1
                    if p.get("spec") is not None:
                        flag |= 2
                    w.byte(flag)
                    _enc(w, p["v"])
                    w.byte(_CONV_IDX[p.get("conv")])
                    if p.get("spec") is not None:
                        _enc(w, p["spec"])
        case "func" | "funcexpr":
            w.s(node["name"])
            w.byte(
                (1 if node.get("gen") else 0)
                | (2 if node.get("coro") else 0)
                | (4 if node.get("asyncgen") else 0)
                | (8 if node.get("annotation_scope") else 0)
            )
            p = node.get("params", {})
            posonly = p.get("posonly", 0)
            if type(posonly) is not int or not 0 <= posonly <= len(p.get("args", [])):
                raise DSPBError("invalid positional-only parameter count")
            pf = (
                (1 if p.get("vararg") else 0) | (2 if p.get("kwarg") else 0) | (4 if posonly else 0)
            )
            w.byte(pf)
            if posonly:
                w.u(posonly)
            _enc_strs(w, p.get("args", []))
            _enc_nodes(w, p.get("defaults", []))
            if p.get("vararg"):
                w.s(p["vararg"])
            _enc_strs(w, p.get("kwonly", []))
            kwd = p.get("kwdefaults", [])
            w.u(len(kwd))
            for d in kwd:
                if d is None:
                    w.byte(0)
                else:
                    w.byte(1)
                    _enc(w, d)
            if p.get("kwarg"):
                w.s(p["kwarg"])
            _enc_strs(w, node.get("locals", []))
            _enc_strs(w, node.get("gdecl", []))
            _enc_strs(w, node.get("ndecl", []))
            _enc_nodes(w, node["body"])
        case "yield" | "return":
            if node.get("v") is not None:
                _enc(w, node["v"])
        case "namedexpr":
            _enc(w, node["target"])
            _enc(w, node["v"])
        case "assign":
            _enc_nodes(w, node["targets"])
            _enc(w, node["v"])
        case "if" | "while":
            _enc(w, node["test"])
            _enc_nodes(w, node["then"] if op == "if" else node["body"])
            _enc_nodes(w, node.get("orelse", []))
        case "for":
            _enc(w, node["target"])
            _enc(w, node["iter"])
            _enc_nodes(w, node["body"])
            _enc_nodes(w, node.get("orelse", []))
        case "break" | "continue" | "pass":
            pass
        case "class":
            w.s(node["name"])
            kwargs = node.get("kwargs", [])
            has_binding = "binding" in node
            w.byte((1 if kwargs else 0) | (2 if has_binding else 0))
            if has_binding:
                if not isinstance(node["binding"], str):
                    raise DSPBError("class binding must be a string")
                w.s(node["binding"])
            if kwargs:
                w.u(len(kwargs))
                for kw in kwargs:
                    if kw["n"] is None:
                        w.byte(0)
                    else:
                        w.byte(1)
                        w.s(kw["n"])
                    _enc(w, kw["v"])
            _enc_nodes(w, node.get("bases", []))
            _enc_nodes(w, node["body"])
        case "raise":
            _enc_opt(w, node.get("exc"))
            _enc_opt(w, node.get("cause"))
        case "try":
            w.byte(1 if node.get("star") else 0)
            _enc_nodes(w, node["body"])
            handlers = node.get("handlers", [])
            w.u(len(handlers))
            for h in handlers:
                hf = (1 if h.get("t") is not None else 0) | (2 if h.get("name") is not None else 0)
                w.byte(hf)
                if h.get("t") is not None:
                    _enc(w, h["t"])
                if h.get("name") is not None:
                    w.s(h["name"])
                _enc_nodes(w, h["body"])
            _enc_nodes(w, node.get("orelse", []))
            _enc_nodes(w, node.get("finalbody", []))
        case "with":
            items = node["items"]
            w.u(len(items))
            for it in items:
                w.byte(1 if it.get("name") is not None else 0)
                _enc(w, it["ctx"])
                if it.get("name") is not None:
                    _enc(w, it["name"])
            _enc_nodes(w, node["body"])
        case "import":
            w.s(node["module"])
            bind = node.get("bind")
            w.byte(0 if bind is None else (1 if bind == "root" else 2))
            if node.get("alias") is not None:
                w.s(node["alias"])
        case "importfrom":
            w.s(node["module"])
            names = node["names"]
            w.u(len(names))
            for nm in names:
                w.s(nm["n"])
                w.s(nm["alias"])
        case "del":
            _enc_nodes(w, node["targets"])
        case _:
            raise DSPBError(f"unknown op {op!r}")


def _enum_at(table, idx: int, what: str):
    if idx >= len(table):
        raise DSPBError(f"bad {what} byte")
    return table[idx]


def _cmp_kind(b: int) -> str:
    return _enum_at(CMP_KINDS, b, "compare-kind")


def _node_dict(op: str, ln) -> dict:
    return {"op": op, "ln": ln} if ln is not None else {"op": op}


def _dec_nodes(r: _Reader) -> list:
    return [_dec(r) for _ in range(r.count())]


def _dec_strs(r: _Reader) -> list:
    return [r.s() for _ in range(r.count())]


def _dec(r: _Reader) -> dict:
    r.depth += 1
    try:
        if r.depth > r.max_depth:
            raise DSPBError("node nesting too deep")
        return _dec_inner(r)
    finally:
        r.depth -= 1


def _dec_inner(r: _Reader) -> dict:
    opb = r.byte()
    if opb >= len(OPS):
        raise DSPBError("bad opcode")
    op = OPS[opb]
    flags = r.byte()
    ln = r.u() if flags & 1 else None
    opts = _OPT.get(op, [])
    present = {key: bool(flags & (1 << (i + 1))) for i, key in enumerate(opts)}
    n = _node_dict(op, None)  # Append source lines after fields for stable ordering.

    match op:
        case "const":
            kind = _enum_at(CONST_KINDS, r.byte(), "const-kind")
            n["t"] = kind
            if kind == "str":
                n.update(_literal_fields(r.s(), "v"))
            elif kind in ("int", "float", "bytes"):
                n["v"] = r.s()
            elif kind == "complex":
                n["re"] = r.s()
                n["im"] = r.s()
            elif kind == "bool":
                n["v"] = r.byte() == 1
        case "name":
            n["id"] = r.s()
        case "attr":
            n["o"] = _dec(r)
            n["n"] = r.s()
        case "index":
            n["o"] = _dec(r)
            n["k"] = _dec(r)
        case "slicelit":
            for key in ("lower", "upper", "step"):
                if present[key]:
                    n[key] = _dec(r)
        case "starred" | "yieldfrom" | "expr":
            n["v"] = _dec(r)
        case "call":
            n["f"] = _dec(r)
            args = _dec_nodes(r)
            if args:
                n["args"] = args
            kws = []
            for _ in range(r.count()):
                named = r.byte() == 1
                kname = r.s() if named else None
                kws.append({"n": kname, "v": _dec(r)})
            if kws:
                n["kw"] = kws
        case "binop" | "augassign":
            n["kind"] = _enum_at(BIN_KINDS, r.byte(), "binop-kind")
            if op == "binop":
                n["l"] = _dec(r)
                n["r"] = _dec(r)
            else:
                n["target"] = _dec(r)
                n["v"] = _dec(r)
        case "unary":
            n["kind"] = _enum_at(UNARY_KINDS, r.byte(), "unary-kind")
            n["v"] = _dec(r)
        case "boolop":
            n["kind"] = "and" if r.byte() == 0 else "or"
            n["values"] = _dec_nodes(r)
        case "compare":
            n["left"] = _dec(r)
            n["ops"] = [_cmp_kind(r.byte()) for _ in range(r.count())]
            n["comps"] = _dec_nodes(r)
        case "cond":
            n["test"] = _dec(r)
            n["then"] = _dec(r)
            n["else"] = _dec(r)
        case "list" | "tuple" | "set":
            elts = _dec_nodes(r)
            if elts:
                n["elts"] = elts
        case "dict":
            entries = []
            for _ in range(r.count()):
                has_k = r.byte() == 1
                k = _dec(r) if has_k else None
                entries.append({"k": k, "v": _dec(r)})
            if entries:
                n["entries"] = entries
        case "fstring":
            parts = []
            for _ in range(r.count()):
                flag = r.byte()
                if flag == 0:
                    parts.append(_literal_fields(r.s(), "s"))
                else:
                    part = {"v": _dec(r)}
                    conv = _enum_at(CONV_KINDS, r.byte(), "conv")
                    if conv is not None:
                        part["conv"] = conv
                    if flag & 2:
                        part["spec"] = _dec(r)
                    parts.append(part)
            n["parts"] = parts
        case "func" | "funcexpr":
            n["name"] = r.s()
            gbyte = r.byte()
            if gbyte & 1:
                n["gen"] = True
            if gbyte & 2:
                n["coro"] = True
            if gbyte & 4:
                n["asyncgen"] = True
            if gbyte & 8:
                n["annotation_scope"] = True
            pf = r.byte()
            p: dict = {}
            posonly = r.u() if pf & 4 else 0
            if posonly:
                p["posonly"] = posonly
            args = _dec_strs(r)
            if posonly > len(args):
                raise DSPBError("invalid positional-only parameter count")
            if args:
                p["args"] = args
            defaults = _dec_nodes(r)
            if defaults:
                p["defaults"] = defaults
            if pf & 1:
                p["vararg"] = r.s()
            kwonly = _dec_strs(r)
            if kwonly:
                p["kwonly"] = kwonly
            kwd = []
            for _ in range(r.count()):
                kwd.append(_dec(r) if r.byte() == 1 else None)
            if kwd:
                p["kwdefaults"] = kwd
            if pf & 2:
                p["kwarg"] = r.s()
            n["params"] = p
            locals_ = _dec_strs(r)
            if locals_:
                n["locals"] = locals_
            gdecl = _dec_strs(r)
            if gdecl:
                n["gdecl"] = gdecl
            ndecl = _dec_strs(r)
            if ndecl:
                n["ndecl"] = ndecl
            n["body"] = _dec_nodes(r)
        case "yield" | "return":
            if present["v"]:
                n["v"] = _dec(r)
        case "namedexpr":
            n["target"] = _dec(r)
            n["v"] = _dec(r)
        case "assign":
            n["targets"] = _dec_nodes(r)
            n["v"] = _dec(r)
        case "if" | "while":
            n["test"] = _dec(r)
            n["then" if op == "if" else "body"] = _dec_nodes(r)
            orelse = _dec_nodes(r)
            if orelse:
                n["orelse"] = orelse
        case "for":
            n["target"] = _dec(r)
            n["iter"] = _dec(r)
            n["body"] = _dec_nodes(r)
            orelse = _dec_nodes(r)
            if orelse:
                n["orelse"] = orelse
        case "break" | "continue" | "pass":
            pass
        case "class":
            n["name"] = r.s()
            class_flags = r.byte()
            if class_flags & ~3:
                raise DSPBError("bad class flags")
            if class_flags & 2:
                n["binding"] = r.s()
            if class_flags & 1:
                kwargs = []
                for _ in range(r.count()):
                    name = r.s() if r.byte() else None
                    kwargs.append({"n": name, "v": _dec(r)})
                n["kwargs"] = kwargs
            bases = _dec_nodes(r)
            if bases:
                n["bases"] = bases
            n["body"] = _dec_nodes(r)
        case "raise":
            if present["exc"]:
                n["exc"] = _dec(r)
            if present["cause"]:
                n["cause"] = _dec(r)
        case "try":
            if r.byte():
                n["star"] = True
            n["body"] = _dec_nodes(r)
            handlers = []
            for _ in range(r.count()):
                hf = r.byte()
                h: dict = {}
                if hf & 1:
                    h["t"] = _dec(r)
                if hf & 2:
                    h["name"] = r.s()
                h["body"] = _dec_nodes(r)
                handlers.append(h)
            if handlers:
                n["handlers"] = handlers
            orelse = _dec_nodes(r)
            if orelse:
                n["orelse"] = orelse
            finalbody = _dec_nodes(r)
            if finalbody:
                n["finalbody"] = finalbody
        case "with":
            items = []
            for _ in range(r.count()):
                has_name = r.byte() == 1
                item = {"ctx": _dec(r)}
                if has_name:
                    item["name"] = _dec(r)
                items.append(item)
            n["items"] = items
            n["body"] = _dec_nodes(r)
        case "import":
            n["module"] = r.s()
            bind = r.byte()
            if bind == 1:
                n["bind"] = "root"
            elif bind == 2:
                n["bind"] = "none"
            if present["alias"]:
                n["alias"] = r.s()
        case "importfrom":
            n["module"] = r.s()
            n["names"] = [{"n": r.s(), "alias": r.s()} for _ in range(r.count())]
        case "del":
            n["targets"] = _dec_nodes(r)

    if ln is not None:
        n["ln"] = ln
    return n


def _check_stdlib_id(value) -> None:
    try:
        _stdlib_id(value, "$.stdlib")
    except BundleValidationError as exc:
        raise DSPBError(str(exc)) from None


def encode(bundle: dict) -> bytes:
    """Encode a bundle as a DSPB container.

    Args:
        bundle: A version-one Diaspore bundle.

    Returns:
        The encoded container bytes.

    Raises:
        DSPBError: The bundle version or an encoded value is unsupported.
    """
    if bundle.get("diaspore") != 1:
        raise DSPBError("not a diaspore v1 bundle")
    w = _Writer()
    w.u(1)  # Keep the semantic version aligned with JSON's "diaspore" field.
    w.s(bundle["main"])
    linked = "stdlib" in bundle
    if linked:
        _check_stdlib_id(bundle["stdlib"])
        w.s(bundle["stdlib"])
    mods = bundle["modules"]
    w.u(len(mods))
    for name, mod in mods.items():
        w.s(name)
        w.s(mod["file"])
        src = mod.get("src")
        if src is None:
            w.byte(0)
        else:
            w.byte(1)
            _enc_strs(w, src)
        _enc_nodes(w, mod["body"])
    body = w.buf.getvalue()
    head = _Writer()
    head.u(len(w.strlist))
    for s in w.strlist:
        raw = s.encode("utf-8", errors="surrogatepass")
        head.u(len(raw))
        head.buf.write(raw)
    return (MAGIC_LINKED if linked else MAGIC) + head.buf.getvalue() + body


def decode(data: bytes) -> dict:
    """Decode a DSPB container into the compact bundle model.

    Args:
        data: Container bytes, including the DSPB magic prefix.

    Returns:
        The decoded bundle.

    Raises:
        DSPBError: The container is malformed or its version is unsupported.
    """
    linked = data.startswith(MAGIC_LINKED)
    if not linked and not data.startswith(MAGIC):
        raise DSPBError("not a DSPB bundle")
    r = _Reader(data, len(MAGIC))
    for _ in range(r.count()):
        length = r.u()
        raw = r.data[r.pos : r.pos + length]
        if len(raw) != length:
            raise DSPBError("truncated string table")
        r.pos += length
        try:
            r.strings.append(raw.decode("utf-8", errors="surrogatepass"))
        except UnicodeDecodeError:
            raise DSPBError("invalid UTF-8 in string table") from None
    if r.u() != 1:
        raise DSPBError("unsupported semantic version")
    main = r.s()
    bundle = {"diaspore": 1, "main": main}
    if linked:
        bundle["stdlib"] = r.s()
        _check_stdlib_id(bundle["stdlib"])
    modules: dict[str, dict] = {}
    for _ in range(r.count()):
        name = r.s()
        if name in modules:
            raise DSPBError("duplicate module in DSPB bundle")
        mod: dict = {"name": name, "file": r.s()}
        if r.byte() == 1:
            mod["src"] = _dec_strs(r)
        mod["body"] = _dec_nodes(r)
        modules[name] = mod
    if r.pos != len(data):
        raise DSPBError("trailing data after DSPB bundle")
    bundle["modules"] = modules
    return bundle
