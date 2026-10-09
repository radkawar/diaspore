"""Compile Python source into portable JSON IR using the standard library.

Lower comprehensions, lambdas, decorators, and assertions before execution.
"""

from __future__ import annotations

import ast
import base64
import json
import math
import os

DIASPORE_VERSION = 1

NATIVE_MODULES = {"time", "math", "json", "random", "sys", "net", "re", "tasks", "flow"}

# Public names each native module exports (for `from mod import *`); kept in
# sync with make_native_modules in vm.py.
NATIVE_EXPORTS = {
    "sys": ["argv", "exit", "platform", "stderr_write"],
    "math": ["pi", "e", "inf", "nan", "sqrt", "floor", "ceil", "fabs", "pow",
             "log", "log2", "log10", "exp", "sin", "cos", "tan", "hypot",
             "fmod", "isnan", "isinf"],
    "time": ["time", "monotonic", "sleep"],
    "json": ["dumps", "loads"],
    "random": ["seed", "random", "randint", "choice", "shuffle"],
    "re": ["IGNORECASE", "MULTILINE", "DOTALL", "search", "match", "fullmatch",
           "findall", "split", "sub", "escape"],
    "flow": ["suspend"],
    "net": ["listen", "connect", "accept", "recv", "send", "close", "peer"],
    "tasks": ["spawn", "channel", "lock"],
}

# Annotation-only machinery: these imports are compiled away entirely (diaspore
# strips annotations), so fully-typed Python sources compile unchanged.
TYPE_ONLY_MODULES = {"typing", "__future__"}

_BINOP = {
    ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "div",
    ast.FloorDiv: "floordiv", ast.Mod: "mod", ast.Pow: "pow",
    ast.LShift: "lshift", ast.RShift: "rshift",
    ast.BitAnd: "bitand", ast.BitOr: "bitor", ast.BitXor: "bitxor",
    ast.MatMult: "matmul",
}
_UNARY = {ast.USub: "neg", ast.UAdd: "pos", ast.Not: "not", ast.Invert: "invert"}
_CMP = {
    ast.Eq: "eq", ast.NotEq: "ne", ast.Lt: "lt", ast.LtE: "le",
    ast.Gt: "gt", ast.GtE: "ge", ast.In: "in", ast.NotIn: "notin",
    ast.Is: "is", ast.IsNot: "isnot",
}


_TRUE = {"op": "const", "t": "bool", "v": True}
_FALSE = {"op": "const", "t": "bool", "v": False}


def _name(id_: str) -> dict:
    return {"op": "name", "id": id_}


def _int(v: int) -> dict:
    if v < 0:
        return {"op": "unary", "kind": "neg",
                "v": {"op": "const", "t": "int", "v": str(-v)}}
    return {"op": "const", "t": "int", "v": str(v)}


def _cmp(kind: str, l: dict, r: dict) -> dict:
    return {"op": "compare", "left": l, "ops": [kind], "comps": [r]}


def _call(fname: str, args: list[dict]) -> dict:
    return {"op": "call", "f": _name(fname), "args": args, "kw": []}


def _not(v: dict) -> dict:
    return {"op": "unary", "kind": "not", "v": v}


class DiasporeCompileError(Exception):
    """Report a compilation error with its source location."""

    def __init__(self, msg: str, file: str, line: int | None = None):
        """Initialize the error with a message and source location."""
        self.msg, self.file, self.line = msg, file, line
        loc = f"{file}:{line}" if line else file
        super().__init__(f"{loc}: {msg}")


class _Scope:
    def __init__(self, kind: str, locals_: set[str], gdecl: set[str], ndecl: set[str]):
        self.kind = kind  # "module" | "function" | "class"
        self.locals = locals_
        self.gdecl = gdecl
        self.ndecl = ndecl


class _LocalsCollector(ast.NodeVisitor):
    """Collect bindings in a function, excluding global and nonlocal names."""

    def __init__(self):
        self.assigned: set[str] = set()
        self.gdecl: set[str] = set()
        self.ndecl: set[str] = set()

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.assigned.add(node.id)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.assigned.add(node.name)
        for dec in node.decorator_list:
            self.visit(dec)
        for d in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            self.visit(d)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.assigned.add(node.name)
        for dec in node.decorator_list:
            self.visit(dec)
        for b in node.bases:
            self.visit(b)

    def visit_Lambda(self, node):
        for d in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            self.visit(d)

    def _skip_comp(self, node):
        # Comprehensions get their own desugared scope; only the outermost
        # iterable is evaluated in this scope.
        self.visit(node.generators[0].iter)

    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _skip_comp

    def visit_Global(self, node):
        self.gdecl.update(node.names)

    def visit_Nonlocal(self, node):
        self.ndecl.update(node.names)

    def visit_Import(self, node):
        for a in node.names:
            self.assigned.add(a.asname or a.name.split(".")[0])

    def visit_ImportFrom(self, node):
        for a in node.names:
            self.assigned.add(a.asname or a.name)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.assigned.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        if node.name:
            self.assigned.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node):
        if node.name:
            self.assigned.add(node.name)

    def visit_MatchMapping(self, node):
        if node.rest:
            self.assigned.add(node.rest)
        self.generic_visit(node)


class _YieldFinder(ast.NodeVisitor):
    def __init__(self):
        self.found = False

    def visit_Yield(self, node):
        self.found = True

    def visit_YieldFrom(self, node):
        self.found = True

    def visit_FunctionDef(self, node):
        pass

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        pass

    def _skip(self, node):
        pass

    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _skip


def _is_type_checking(test: ast.expr) -> bool:
    """Recognize bare and module-qualified TYPE_CHECKING guards."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING" \
        and isinstance(test.value, ast.Name)


def _is_generator(body: list[ast.stmt]) -> bool:
    f = _YieldFinder()
    for s in body:
        f.visit(s)
    return f.found


class ModuleCompiler:
    """Lower one Python module into portable IR."""

    def __init__(self, name: str, file: str, source: str,
                 package_ctx: str = "", star_resolver=None, is_module=None):
        """Initialize source, package context, and import resolution callbacks."""
        self.name, self.file = name, file
        self.source = source
        self.scopes: list[_Scope] = []
        self.imports: set[str] = set()
        # package context for resolving relative imports ("" = top level);
        # star_resolver(absname) -> list[str] exports; is_module(absname) -> bool
        self.package_ctx = package_ctx
        self.star_resolver = star_resolver
        self.is_module = is_module or (lambda name: False)
        self._tmp = 0

    def _gensym(self, prefix: str) -> str:
        """Generate a dot-prefixed name that cannot collide with source names."""
        self._tmp += 1
        return f".{prefix}{self._tmp}"


    def err(self, msg: str, node=None) -> DiasporeCompileError:
        """Create a compilation error at the optional AST node's location."""
        return DiasporeCompileError(msg, self.file, getattr(node, "lineno", None))


    def compile(self) -> dict:
        """Parse the source and return its compiled module."""
        try:
            tree = ast.parse(self.source, filename=self.file)
        except SyntaxError as e:
            raise DiasporeCompileError(f"syntax error: {e.msg}", self.file, e.lineno) from None
        self.scopes.append(_Scope("module", set(), set(), set()))
        body = self.block(tree.body)
        return {"name": self.name, "file": self.file, "body": body}

    def block(self, stmts: list[ast.stmt]) -> list[dict]:
        """Compile a statement block, omitting its leading docstring."""
        out: list[dict] = []
        for i, s in enumerate(stmts):
            if (i == 0 and isinstance(s, ast.Expr)
                    and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str)):
                continue  # docstring
            r = self.stmt(s)
            if r is None:
                continue
            out.extend(r if isinstance(r, list) else [r])
        return out


    def stmt(self, s: ast.stmt):
        """Lower a statement into IR nodes or omit compile-time declarations."""
        ln = s.lineno
        match s:
            case ast.Expr(value=v):
                return {"op": "expr", "v": self.expr(v), "ln": ln}
            case ast.Assign(targets=ts, value=v):
                return {"op": "assign", "targets": [self.target(t) for t in ts],
                        "v": self.expr(v), "ln": ln}
            case ast.AnnAssign(target=t, value=v):
                if v is None:
                    return None  # declaration only; locals collector saw it
                return {"op": "assign", "targets": [self.target(t)],
                        "v": self.expr(v), "ln": ln}
            case ast.AugAssign(target=t, op=op, value=v):
                kind = _BINOP.get(type(op))
                if kind is None:
                    raise self.err("unsupported augmented operator", s)
                return {"op": "augassign", "target": self.target(t), "kind": kind,
                        "v": self.expr(v), "ln": ln}
            case ast.If(test=t, body=b, orelse=o):
                if _is_type_checking(t):
                    # TYPE_CHECKING guards exclude imports unavailable at runtime.
                    else_body = self.block(o)
                    return else_body or None
                return {"op": "if", "test": self.expr(t), "then": self.block(b),
                        "orelse": self.block(o), "ln": ln}
            case ast.While(test=t, body=b, orelse=o):
                return {"op": "while", "test": self.expr(t), "body": self.block(b),
                        "orelse": self.block(o), "ln": ln}
            case ast.For(target=t, iter=it, body=b, orelse=o):
                return {"op": "for", "target": self.target(t), "iter": self.expr(it),
                        "body": self.block(b), "orelse": self.block(o), "ln": ln}
            case ast.Break():
                return {"op": "break", "ln": ln}
            case ast.Continue():
                return {"op": "continue", "ln": ln}
            case ast.Pass():
                return {"op": "pass", "ln": ln}
            case ast.Return(value=v):
                if self.scopes[-1].kind != "function":
                    raise self.err("'return' outside function", s)
                node = {"op": "return", "ln": ln}
                if v is not None:
                    node["v"] = self.expr(v)
                return node
            case ast.FunctionDef():
                return self.funcdef(s)
            case ast.ClassDef():
                return self.classdef(s)
            case ast.Raise(exc=e, cause=c):
                node = {"op": "raise", "ln": ln}
                if e is not None:
                    node["exc"] = self.expr(e)
                if c is not None:
                    node["cause"] = self.expr(c)
                return node
            case ast.Try(body=b, handlers=hs, orelse=o, finalbody=f):
                handlers = []
                for h in hs:
                    handlers.append({
                        "t": self.expr(h.type) if h.type is not None else None,
                        "name": h.name,
                        "body": self.block(h.body),
                    })
                return {"op": "try", "body": self.block(b), "handlers": handlers,
                        "orelse": self.block(o), "finalbody": self.block(f), "ln": ln}
            case ast.Assert(test=t, msg=m):
                raise_node = {
                    "op": "raise", "ln": ln,
                    "exc": {"op": "call", "f": {"op": "name", "id": "AssertionError"},
                            "args": [self.expr(m)] if m is not None else [], "kw": []},
                }
                return {"op": "if",
                        "test": {"op": "unary", "kind": "not", "v": self.expr(t)},
                        "then": [raise_node], "orelse": [], "ln": ln}
            case ast.With(items=items, body=b):
                out_items = []
                for it in items:
                    out_items.append({
                        "ctx": self.expr(it.context_expr),
                        "name": self.target(it.optional_vars) if it.optional_vars else None,
                    })
                return {"op": "with", "items": out_items, "body": self.block(b), "ln": ln}
            case ast.Import(names=names):
                out = []
                for a in names:
                    if a.name in TYPE_ONLY_MODULES:
                        continue
                    self.imports.add(a.name)
                    if a.asname:
                        out.append({"op": "import", "module": a.name,
                                    "alias": a.asname, "ln": ln})
                    elif "." in a.name:
                        # `import a.b.c` executes the chain, binds the root
                        out.append({"op": "import", "module": a.name,
                                    "alias": a.name.split(".")[0],
                                    "bind": "root", "ln": ln})
                    else:
                        out.append({"op": "import", "module": a.name,
                                    "alias": a.name, "ln": ln})
                return out or None
            case ast.ImportFrom(module=mod, names=names, level=lvl):
                return self.import_from(mod, names, lvl, s, ln)
            case ast.Delete(targets=ts):
                return {"op": "del", "targets": [self.target(t) for t in ts], "ln": ln}
            case ast.Global() | ast.Nonlocal():
                return None  # folded into the enclosing func node by the collector
            case ast.AsyncFunctionDef():
                return self.funcdef(s, coro=True)
            case ast.AsyncFor(target=t, iter=it, body=b, orelse=o):
                return self.async_for(t, it, b, o, ln)
            case ast.AsyncWith(items=items, body=b):
                return self.async_with(items, b, ln)
            case ast.Match():
                return self.match_stmt(s)
            case ast.TryStar(body=b, handlers=hs, orelse=o, finalbody=f):
                handlers = []
                for h in hs:
                    handlers.append({
                        "t": self.expr(h.type) if h.type is not None else None,
                        "name": h.name,
                        "body": self.block(h.body),
                    })
                return {"op": "try", "star": True, "body": self.block(b),
                        "handlers": handlers, "orelse": self.block(o),
                        "finalbody": self.block(f), "ln": ln}
            case _:
                raise self.err(f"unsupported statement: {type(s).__name__}", s)


    def import_from(self, mod, names, level: int, s, ln: int):
        """Resolve and lower a from-import using the current package context."""
        if level:
            ctx = self.package_ctx
            if not ctx:
                raise self.err(
                    "attempted relative import with no known parent package", s)
            for _ in range(level - 1):
                if "." not in ctx:
                    raise self.err(
                        "attempted relative import beyond top-level package", s)
                ctx = ctx.rsplit(".", 1)[0]
            target = f"{ctx}.{mod}" if mod else ctx
        else:
            if mod is None:
                raise self.err("unsupported import form", s)
            target = mod
        if target in TYPE_ONLY_MODULES:
            return None
        if any(a.name == "*" for a in names):
            if len(names) != 1:
                raise self.err("unsupported import form", s)
            if self.star_resolver is None:
                raise self.err("'import *' is not supported here", s)
            exported = self.star_resolver(target, self, s)
            self.imports.add(target)
            return {"op": "importfrom", "module": target,
                    "names": [{"n": n, "alias": n} for n in exported], "ln": ln}
        self.imports.add(target)
        out: list[dict] = []
        for a in names:
            child = f"{target}.{a.name}"
            if self.is_module(child):
                # `from pkg import sub` imports the submodule first, then
                # binds whatever attribute the package exposes (CPython order)
                self.imports.add(child)
                out.append({"op": "import", "module": child, "bind": "none", "ln": ln})
        out.append({"op": "importfrom", "module": target,
                    "names": [{"n": a.name, "alias": a.asname or a.name}
                              for a in names], "ln": ln})
        return out


    def _params(self, a: ast.arguments) -> dict:
        for grp in (a.posonlyargs, a.args, a.kwonlyargs):
            for arg in grp:
                if arg.arg.startswith("."):
                    raise self.err("invalid parameter name", arg)
        return {
            "args": [x.arg for x in a.posonlyargs + a.args],
            "defaults": [self.expr(d) for d in a.defaults],
            "vararg": a.vararg.arg if a.vararg else None,
            "kwonly": [x.arg for x in a.kwonlyargs],
            "kwdefaults": [self.expr(d) if d is not None else None for d in a.kw_defaults],
            "kwarg": a.kwarg.arg if a.kwarg else None,
        }

    def _function_node(self, name: str, a: ast.arguments, body: list[ast.stmt],
                       ln: int, op: str, synthetic_body: list[dict] | None = None,
                       coro: bool = False) -> dict:
        params = self._params(a)  # defaults compile in the *defining* scope
        col = _LocalsCollector()
        for st in body:
            col.visit(st)
        param_names = set(params["args"]) | set(params["kwonly"])
        if params["vararg"]:
            param_names.add(params["vararg"])
        if params["kwarg"]:
            param_names.add(params["kwarg"])
        bad = (col.gdecl | col.ndecl) & param_names
        if bad:
            raise self.err(f"name {bad.pop()!r} is parameter and global/nonlocal")
        locals_ = (col.assigned | param_names) - col.gdecl - col.ndecl
        for n in col.ndecl:
            if not any(sc.kind == "function" and n in sc.locals for sc in self.scopes):
                raise self.err(f"no binding for nonlocal {n!r} found")
        self.scopes.append(_Scope("function", locals_, col.gdecl, col.ndecl))
        try:
            compiled = synthetic_body if synthetic_body is not None else self.block(body)
        finally:
            self.scopes.pop()
        # Await lowers to yield-from; coro preserves the source-level distinction.
        return {
            "op": op, "name": name,
            "gen": _is_generator(body) or coro,
            "coro": coro,
            "params": params,
            "locals": sorted(locals_), "gdecl": sorted(col.gdecl), "ndecl": sorted(col.ndecl),
            "body": compiled, "ln": ln,
        }

    def funcdef(self, s, coro: bool = False):
        """Compile a function definition and apply its decorators."""
        node = self._function_node(s.name, s.args, s.body, s.lineno, "func",
                                   coro=coro)
        if not s.decorator_list:
            return node
        node["op"] = "funcexpr"
        del node["ln"]
        value: dict = node
        for dec in reversed(s.decorator_list):
            value = {"op": "call", "f": self.expr(dec), "args": [value], "kw": []}
        return {"op": "assign", "targets": [{"op": "name", "id": s.name}],
                "v": value, "ln": s.lineno}

    def classdef(self, s: ast.ClassDef):
        """Compile a class body, bases, keywords, and decorators."""
        # keywords carry n=null for `**mapping` expansion; a `metaclass=`
        # keyword (static or via **) is resolved at runtime by the VM
        kwargs = []
        for kw in s.keywords:
            kwargs.append({"n": kw.arg, "v": self.expr(kw.value)})
        col = _LocalsCollector()
        for st in s.body:
            col.visit(st)
        self.scopes.append(_Scope("class", col.assigned, col.gdecl, col.ndecl))
        try:
            body = self.block(s.body)
        finally:
            self.scopes.pop()
        bases = []
        for b in s.bases:
            if isinstance(b, ast.Starred):
                bases.append({"op": "starred", "v": self.expr(b.value)})
            else:
                bases.append(self.expr(b))
        node = {"op": "class", "name": s.name, "bases": bases,
                "body": body, "ln": s.lineno}
        if kwargs:
            node["kwargs"] = kwargs
        if not s.decorator_list:
            return node
        stmts: list[dict] = [node]
        value: dict = {"op": "name", "id": s.name}
        for dec in reversed(s.decorator_list):
            value = {"op": "call", "f": self.expr(dec), "args": [value], "kw": []}
        stmts.append({"op": "assign", "targets": [{"op": "name", "id": s.name}],
                      "v": value, "ln": s.lineno})
        return stmts


    def target(self, t: ast.expr) -> dict:
        """Compile an assignment target."""
        match t:
            case ast.Name(id=i):
                return {"op": "name", "id": i}
            case ast.Attribute(value=v, attr=n):
                return {"op": "attr", "o": self.expr(v), "n": n}
            case ast.Subscript(value=v, slice=k):
                return {"op": "index", "o": self.expr(v), "k": self.subscript(k)}
            case ast.Tuple(elts=es) | ast.List(elts=es):
                return {"op": "tuple", "elts": [self.target(e) for e in es]}
            case ast.Starred(value=v):
                return {"op": "starred", "v": self.target(v)}
            case _:
                raise self.err(f"unsupported assignment target: {type(t).__name__}", t)


    def subscript(self, k: ast.expr) -> dict:
        """Compile an index or slice, including multidimensional slices."""
        if isinstance(k, ast.Slice):
            node: dict = {"op": "slicelit"}
            for field, sub in (("lower", k.lower), ("upper", k.upper), ("step", k.step)):
                if sub is not None:
                    node[field] = self.expr(sub)
            return node
        if isinstance(k, ast.Tuple) and any(isinstance(e, ast.Slice) for e in k.elts):
            # a[1:2, 3] -> a[(slice(1,2), 3)]: build a tuple whose slice
            # elements become slice() objects
            elts = []
            for e in k.elts:
                if isinstance(e, ast.Slice):
                    args = [self.expr(e.lower) if e.lower else {"op": "const", "t": "none"},
                            self.expr(e.upper) if e.upper else {"op": "const", "t": "none"},
                            self.expr(e.step) if e.step else {"op": "const", "t": "none"}]
                    elts.append(_call("slice", args))
                else:
                    elts.append(self.expr(e))
            return {"op": "tuple", "elts": elts}
        return self.expr(k)

    def const(self, v, node) -> dict:
        """Encode a Python literal as an IR constant."""
        if v is None:
            return {"op": "const", "t": "none"}
        if v is Ellipsis:
            return {"op": "const", "t": "ellipsis"}
        if isinstance(v, bool):
            return {"op": "const", "t": "bool", "v": v}
        if isinstance(v, int):
            return {"op": "const", "t": "int", "v": str(v)}
        if isinstance(v, float):
            return {"op": "const", "t": "float", "v": self._floatstr(v)}
        if isinstance(v, str):
            return {"op": "const", "t": "str", "v": v}
        if isinstance(v, bytes):
            return {"op": "const", "t": "bytes", "v": base64.b64encode(v).decode("ascii")}
        if isinstance(v, complex):
            return {"op": "const", "t": "complex",
                    "re": self._floatstr(v.real), "im": self._floatstr(v.imag)}
        raise self.err(f"unsupported constant: {v!r}", node)

    @staticmethod
    def _floatstr(f: float) -> str:
        if math.isinf(f):
            return "inf" if f > 0 else "-inf"
        if math.isnan(f):
            return "nan"
        return repr(f)

    def expr(self, e: ast.expr) -> dict:
        """Lower a Python expression into IR."""
        match e:
            case ast.Constant(value=v):
                return self.const(v, e)
            case ast.Name(id=i):
                return {"op": "name", "id": i}
            case ast.Attribute(value=v, attr=n):
                return {"op": "attr", "o": self.expr(v), "n": n}
            case ast.Subscript(value=v, slice=k):
                return {"op": "index", "o": self.expr(v), "k": self.subscript(k)}
            case ast.BinOp(left=l, op=op, right=r):
                kind = _BINOP.get(type(op))
                if kind is None:
                    raise self.err(f"unsupported operator: {type(op).__name__}", e)
                return {"op": "binop", "kind": kind, "l": self.expr(l), "r": self.expr(r)}
            case ast.UnaryOp(op=op, operand=v):
                return {"op": "unary", "kind": _UNARY[type(op)], "v": self.expr(v)}
            case ast.BoolOp(op=op, values=vs):
                kind = "and" if isinstance(op, ast.And) else "or"
                return {"op": "boolop", "kind": kind, "values": [self.expr(v) for v in vs]}
            case ast.Compare(left=l, ops=ops, comparators=cs):
                return {"op": "compare", "left": self.expr(l),
                        "ops": [_CMP[type(o)] for o in ops],
                        "comps": [self.expr(c) for c in cs]}
            case ast.IfExp(test=t, body=b, orelse=o):
                return {"op": "cond", "test": self.expr(t),
                        "then": self.expr(b), "else": self.expr(o)}
            case ast.Call(func=f, args=args, keywords=kws):
                cargs = []
                for a in args:
                    if isinstance(a, ast.Starred):
                        cargs.append({"op": "starred", "v": self.expr(a.value)})
                    else:
                        cargs.append(self.expr(a))
                ckw = [{"n": kw.arg, "v": self.expr(kw.value)} for kw in kws]
                return {"op": "call", "f": self.expr(f), "args": cargs, "kw": ckw}
            case ast.List(elts=es):
                return {"op": "list", "elts": self._elts(es)}
            case ast.Tuple(elts=es):
                return {"op": "tuple", "elts": self._elts(es)}
            case ast.Set(elts=es):
                return {"op": "set", "elts": self._elts(es)}
            case ast.Dict(keys=ks, values=vs):
                entries = []
                for k, v in zip(ks, vs):
                    entries.append({"k": self.expr(k) if k is not None else None,
                                    "v": self.expr(v)})
                return {"op": "dict", "entries": entries}
            case ast.JoinedStr(values=parts):
                return self.fstring(parts)
            case ast.Lambda(args=a, body=b):
                ret = ast.Return(value=b)
                ast.copy_location(ret, b)
                return self._function_node("<lambda>", a, [ret], e.lineno, "funcexpr")
            case ast.ListComp() | ast.SetComp() | ast.DictComp() | ast.GeneratorExp():
                return self.comprehension(e)
            case ast.Yield(value=v):
                self._check_yield(e)
                node = {"op": "yield"}
                if v is not None:
                    node["v"] = self.expr(v)
                return node
            case ast.YieldFrom(value=v):
                self._check_yield(e)
                return {"op": "yieldfrom", "v": self.expr(v)}
            case ast.NamedExpr(target=t, value=v):
                return {"op": "namedexpr", "target": {"op": "name", "id": t.id},
                        "v": self.expr(v)}
            case ast.Await(value=v):
                if self.scopes[-1].kind != "function":
                    raise self.err("'await' outside async function", e)
                return {"op": "yieldfrom",
                        "v": _call(".await", [self.expr(v)])}
            case ast.Starred():
                raise self.err("starred expression not allowed here", e)
            case _:
                raise self.err(f"unsupported expression: {type(e).__name__}", e)

    def _check_yield(self, e):
        if self.scopes[-1].kind != "function":
            raise self.err("'yield' outside function", e)

    def _elts(self, es: list[ast.expr]) -> list[dict]:
        out = []
        for x in es:
            if isinstance(x, ast.Starred):
                out.append({"op": "starred", "v": self.expr(x.value)})
            else:
                out.append(self.expr(x))
        return out

    def fstring(self, parts: list[ast.expr]) -> dict:
        """Compile literal and formatted f-string components."""
        out = []
        for p in parts:
            if isinstance(p, ast.Constant):
                out.append({"s": p.value})
                continue
            assert isinstance(p, ast.FormattedValue)
            conv = None
            if p.conversion == 114:
                conv = "r"
            elif p.conversion == 115:
                conv = "s"
            elif p.conversion == 97:
                conv = "a"
            elif p.conversion not in (-1, None):
                raise self.err("unsupported f-string conversion", p)
            spec = self.expr(p.format_spec) if p.format_spec is not None else None
            out.append({"v": self.expr(p.value), "conv": conv, "spec": spec})
        return {"op": "fstring", "parts": out}


    def comprehension(self, e) -> dict:
        """Lower a comprehension into a function with an isolated local scope."""
        kind = type(e).__name__
        for gen in e.generators:
            for walrus in ast.walk(gen.iter):
                if isinstance(walrus, ast.NamedExpr):
                    raise self.err(
                        "assignment expression cannot be used in a "
                        "comprehension iterable expression", gen.iter)
        outer_iter = self.expr(e.generators[0].iter)  # enclosing scope

        col = _LocalsCollector()
        for gen in e.generators:
            col.visit(ast.Name(id="_", ctx=ast.Load()))
            self._collect_target_names(gen.target, col.assigned)
        locals_ = col.assigned | {".0", ".r", ".k"}
        self.scopes.append(_Scope("function", locals_, set(), set()))
        try:
            if kind == "GeneratorExp":
                inner: list[dict] = [{"op": "expr", "v": {"op": "yield", "v": self.expr(e.elt)},
                                      "ln": e.lineno}]
            elif kind == "ListComp":
                inner = [self._call_stmt({"op": "attr", "o": {"op": "name", "id": ".r"},
                                          "n": "append"}, [self.expr(e.elt)], e.lineno)]
            elif kind == "SetComp":
                inner = [self._call_stmt({"op": "attr", "o": {"op": "name", "id": ".r"},
                                          "n": "add"}, [self.expr(e.elt)], e.lineno)]
            else:  # DictComp
                # CPython evaluates the key before the value; a plain
                # `.r[key] = value` assign would evaluate value first, so bind
                # the key to a hidden temp before the value expression runs.
                inner = [
                    {"op": "assign", "targets": [{"op": "name", "id": ".k"}],
                     "v": self.expr(e.key), "ln": e.lineno},
                    {"op": "assign",
                     "targets": [{"op": "index", "o": {"op": "name", "id": ".r"},
                                  "k": {"op": "name", "id": ".k"}}],
                     "v": self.expr(e.value), "ln": e.lineno}]

            body = inner
            for i, gen in enumerate(reversed(e.generators)):
                for cond in reversed(gen.ifs):
                    body = [{"op": "if", "test": self.expr(cond), "then": body,
                             "orelse": [], "ln": e.lineno}]
                is_outermost = i == len(e.generators) - 1
                it = {"op": "name", "id": ".0"} if is_outermost else self.expr(gen.iter)
                body = [{"op": "for", "target": self.target(gen.target), "iter": it,
                         "body": body, "orelse": [], "ln": e.lineno}]
        finally:
            self.scopes.pop()

        if kind == "GeneratorExp":
            fbody, gen_flag, name = body, True, "<genexpr>"
        else:
            init = {"ListComp": {"op": "list", "elts": []},
                    "SetComp": {"op": "call", "f": {"op": "name", "id": "set"},
                                "args": [], "kw": []},
                    "DictComp": {"op": "dict", "entries": []}}[kind]
            fbody = [{"op": "assign", "targets": [{"op": "name", "id": ".r"}],
                      "v": init, "ln": e.lineno},
                     *body,
                     {"op": "return", "v": {"op": "name", "id": ".r"}, "ln": e.lineno}]
            gen_flag = False
            name = {"ListComp": "<listcomp>", "SetComp": "<setcomp>",
                    "DictComp": "<dictcomp>"}[kind]

        fn = {"op": "funcexpr", "name": name, "gen": gen_flag, "coro": False,
              "params": {"args": [".0"], "defaults": [], "vararg": None,
                         "kwonly": [], "kwdefaults": [], "kwarg": None},
              "locals": sorted(locals_), "gdecl": [], "ndecl": [],
              "body": fbody, "ln": e.lineno}
        return {"op": "call", "f": fn, "args": [outer_iter], "kw": []}

    def _collect_target_names(self, t: ast.expr, into: set[str]):
        if isinstance(t, ast.Name):
            into.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for x in t.elts:
                self._collect_target_names(x, into)
        elif isinstance(t, ast.Starred):
            self._collect_target_names(t.value, into)
        elif isinstance(t, (ast.Subscript, ast.Attribute)):
            # Attribute and subscript targets mutate objects without binding names.
            pass
        else:
            raise self.err("unsupported comprehension target", t)

    @staticmethod
    def _call_stmt(f: dict, args: list[dict], ln: int) -> dict:
        return {"op": "expr", "v": {"op": "call", "f": f, "args": args, "kw": []}, "ln": ln}

    def _await(self, expr_node: dict) -> dict:
        return {"op": "yieldfrom", "v": _call(".await", [expr_node])}

    def async_for(self, target, it, body, orelse, ln):
        """Lower async iteration into awaited iterator calls."""
        if self.scopes[-1].kind != "function":
            raise self.err("'async for' outside async function", target)
        ait = self._gensym("ai")
        loop = [
            {"op": "assign", "targets": [_name(ait)],
             "v": {"op": "call", "f": {"op": "attr", "o": self.expr(it),
                                       "n": "__aiter__"}, "args": [], "kw": []},
             "ln": ln},
        ]
        broke = self._gensym("brk")
        anext = self._await({"op": "call",
                             "f": {"op": "attr", "o": _name(ait), "n": "__anext__"},
                             "args": [], "kw": []})
        step = {"op": "try",
                "body": [{"op": "assign", "targets": [self.target(target)],
                          "v": anext, "ln": ln}],
                "handlers": [{"t": _name("StopAsyncIteration"), "name": None,
                              "body": [{"op": "assign", "targets": [_name(broke)],
                                        "v": _TRUE, "ln": ln},
                                       {"op": "break", "ln": ln}]}],
                "orelse": [], "finalbody": [], "ln": ln}
        while_body = [step] + self.block(body)
        loop += [
            {"op": "assign", "targets": [_name(broke)], "v": _FALSE, "ln": ln},
            {"op": "while", "test": _TRUE, "body": while_body, "orelse": [], "ln": ln},
        ]
        if orelse:
            loop.append({"op": "if", "test": _not(_name(broke)),
                         "then": self.block(orelse), "orelse": [], "ln": ln})
        return loop

    def async_with(self, items, body, ln):
        """Lower async context managers into awaited entry and exit calls."""
        if self.scopes[-1].kind != "function":
            raise self.err("'async with' outside async function", items[0].context_expr)
        return self._async_with_items(items, 0, body, ln)

    def _async_with_items(self, items, i, body, ln):
        if i == len(items):
            return self.block(body)
        item = items[i]
        mgr = self._gensym("acm")
        ctx = self.expr(item.context_expr)
        aenter = self._await({"op": "call",
                              "f": {"op": "attr", "o": _name(mgr), "n": "__aenter__"},
                              "args": [], "kw": []})
        aexit = self._await({"op": "call",
                             "f": {"op": "attr", "o": _name(mgr), "n": "__aexit__"},
                             "args": [{"op": "const", "t": "none"},
                                      {"op": "const", "t": "none"},
                                      {"op": "const", "t": "none"}], "kw": []})
        out = [{"op": "assign", "targets": [_name(mgr)], "v": ctx, "ln": ln}]
        entered = aenter
        if item.optional_vars is not None:
            enter_stmts = [{"op": "assign", "targets": [self.target(item.optional_vars)],
                            "v": entered, "ln": ln}]
        else:
            enter_stmts = [{"op": "expr", "v": entered, "ln": ln}]
        inner = self._async_with_items(items, i + 1, body, ln)
        out += enter_stmts
        out.append({"op": "try", "body": inner, "handlers": [], "orelse": [],
                    "finalbody": [{"op": "expr", "v": aexit, "ln": ln}], "ln": ln})
        return out


    def match_stmt(self, s: ast.Match) -> list[dict]:
        """Lower pattern matching into a subject binding and guarded branches."""
        ln = s.lineno
        subj = self._gensym("m")
        done = self._gensym("d")
        out = [
            self._set(subj, self.expr(s.subject), ln),
            self._set(done, _FALSE, ln),
        ]
        for case_ in s.cases:
            body = self.block(case_.body)
            success = [self._set(done, _TRUE, case_.pattern.lineno)] + body
            if case_.guard is not None:
                success = [self._if(self.expr(case_.guard), success, case_.guard.lineno)]
            attempt = self.pattern(case_.pattern, _name(subj), success)
            out.append(self._if(_not(_name(done)), attempt, case_.pattern.lineno))
        return out

    def pattern(self, p, subj: dict, inner: list[dict]) -> list[dict]:
        """Run inner statements when the subject matches, binding captures."""
        ln = p.lineno
        match p:
            case ast.MatchValue(value=v):
                return [self._if(_cmp("eq", subj, self.expr(v)), inner, ln)]
            case ast.MatchSingleton(value=v):
                return [self._if(_cmp("is", subj, self.const(v, p)), inner, ln)]
            case ast.MatchAs(pattern=None, name=None):
                return inner
            case ast.MatchAs(pattern=None, name=n):
                return [self._bind(n, subj, ln)] + inner
            case ast.MatchAs(pattern=sub, name=n):
                return self.pattern(sub, subj, [self._bind(n, subj, ln)] + inner)
            case ast.MatchOr(patterns=alts):
                ok = self._gensym("o")
                stmts = [self._set(ok, _FALSE, ln)]
                for alt in alts:
                    hit = self.pattern(alt, subj, [self._set(ok, _TRUE, alt.lineno)])
                    stmts.append(self._if(_not(_name(ok)), hit, alt.lineno))
                stmts.append(self._if(_name(ok), inner, ln))
                return stmts
            case ast.MatchSequence(patterns=pats):
                return self._seq_pattern(pats, subj, inner, ln)
            case ast.MatchMapping(keys=keys, patterns=pats, rest=rest):
                return self._map_pattern(keys, pats, rest, subj, inner, ln)
            case ast.MatchClass(cls=cls, patterns=pos, kwd_attrs=attrs, kwd_patterns=kpats):
                if pos:
                    raise self.err(
                        "positional class patterns are not supported; "
                        "use keyword patterns (e.g. case Point(x=a, y=b))", p)
                chain = inner
                for attr, kp in zip(reversed(attrs), reversed(kpats)):
                    sub = {"op": "attr", "o": subj, "n": attr}
                    chain = [self._if(
                        _call("hasattr", [subj, {"op": "const", "t": "str", "v": attr}]),
                        self.pattern(kp, sub, chain), kp.lineno)]
                return [self._if(_call("isinstance", [subj, self.expr(cls)]), chain, ln)]
            case _:
                raise self.err(f"unsupported match pattern: {type(p).__name__}", p)

    def _seq_pattern(self, pats, subj, inner, ln) -> list[dict]:
        star = next((i for i, q in enumerate(pats) if isinstance(q, ast.MatchStar)), None)
        n = len(pats)
        chain = inner
        order = []  # (pattern-or-star, element expr)
        for i, q in enumerate(pats):
            if isinstance(q, ast.MatchStar):
                after = n - i - 1
                upper = None if after == 0 else _int(-after)
                elem = _call("list", [{
                    "op": "index", "o": subj,
                    "k": {"op": "slicelit", "lower": _int(i),
                          **({"upper": upper} if upper else {})},
                }])
                order.append((q, elem))
            else:
                idx = i if star is None or i < star else i - n  # negative after star
                order.append((q, {"op": "index", "o": subj, "k": _int(idx)}))
        for q, elem in reversed(order):
            if isinstance(q, ast.MatchStar):
                if q.name:
                    chain = [self._bind(q.name, elem, q.lineno)] + chain
            else:
                chain = self.pattern(q, elem, chain)
        length = _call("len", [subj])
        if star is None:
            len_test = _cmp("eq", length, _int(n))
        else:
            len_test = _cmp("ge", length, _int(n - 1))
        seq_test = _call("isinstance", [subj, {
            "op": "tuple", "elts": [_name("list"), _name("tuple")]}])
        return [self._if(seq_test, [self._if(len_test, chain, ln)], ln)]

    def _map_pattern(self, keys, pats, rest, subj, inner, ln) -> list[dict]:
        key_exprs = [self.expr(k) for k in keys]
        chain = inner
        if rest is not None:
            copy = [self._bind(rest, _call("dict", [subj]), ln)]
            copy += [{"op": "del", "targets": [
                {"op": "index", "o": _name(rest), "k": ke}], "ln": ln}
                for ke in key_exprs]
            chain = copy + chain
        for ke, kp in zip(reversed(key_exprs), reversed(pats)):
            sub = {"op": "index", "o": subj, "k": ke}
            chain = [self._if(_cmp("in", ke, subj), self.pattern(kp, sub, chain), kp.lineno)]
        return [self._if(_call("isinstance", [subj, _name("dict")]), chain, ln)]

    def _set(self, hidden: str, value: dict, ln: int) -> dict:
        return {"op": "assign", "targets": [_name(hidden)], "v": value, "ln": ln}

    def _bind(self, user_name: str, value: dict, ln: int) -> dict:
        return {"op": "assign", "targets": [_name(user_name)], "v": value, "ln": ln}

    @staticmethod
    def _if(test: dict, then: list[dict], ln: int) -> dict:
        return {"op": "if", "test": test, "then": then, "orelse": [], "ln": ln}



# Pure-subset stdlib modules shipped with diaspore. They are searched after the
# entry's own directory, so a user module of the same name still wins.
STDLIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stdlib")


def compile_bundle(entry_path: str,
                   extra_modules: set[str] | frozenset[str] = frozenset(),
                   debug: bool = False,
                   unresolved_out: dict | None = None,
                   stdlib: bool = True,
                   search_paths: tuple[str, ...] | list[str] = ()) -> dict:
    """Compile an entry file and its transitive source imports.

    Args:
        entry_path: Path to the entry Python source.
        extra_modules: Host-binding module names accepted during resolution.
        debug: Include source lines for runtime tracebacks.
        unresolved_out: Optional mapping populated with unresolved imports.
        stdlib: Search the bundled standard library after local modules.
        search_paths: Import roots searched after the entry directory and before stdlib.

    Returns:
        A compact bundle containing the entry and resolved source modules.
    """
    entry_path = os.path.abspath(entry_path)
    if not os.path.exists(entry_path):
        raise DiasporeCompileError("entry file not found", entry_path)
    root = os.path.dirname(entry_path)
    entry_name = os.path.splitext(os.path.basename(entry_path))[0]
    known = NATIVE_MODULES | set(extra_modules)

    modules: dict[str, dict] = {}
    exports: dict[str, list[str]] = {}
    in_progress: set[str] = set()
    unresolved: dict[str, str] = {}  # dotted name -> importing file

    search_roots = [root, *(os.path.abspath(path) for path in search_paths)]
    if stdlib and os.path.isdir(STDLIB_DIR):
        search_roots.append(os.path.abspath(STDLIB_DIR))

    def resolve_path(name: str):
        """Find a module or package, preferring local sources and packages."""
        for r in search_roots:
            base = os.path.join(r, *name.split("."))
            init = os.path.join(base, "__init__.py")
            if os.path.isfile(init):
                return init, True
            if os.path.isfile(base + ".py"):
                return base + ".py", False
        return None

    def module_exports(name: str, body_names: set[str], compiled: dict) -> list[str]:
        for stmt in compiled["body"]:
            if stmt["op"] == "assign" and len(stmt["targets"]) == 1 \
                    and stmt["targets"][0] == {"op": "name", "id": "__all__"} \
                    and stmt["v"].get("op") == "list" \
                    and all(e.get("op") == "const" and e.get("t") == "str"
                            for e in stmt["v"]["elts"]):
                return [e["v"] for e in stmt["v"]["elts"]]
        return sorted(n for n in body_names if not n.startswith("_"))

    def star_resolver(target: str, mc: ModuleCompiler, node) -> list[str]:
        if target in NATIVE_EXPORTS:
            return list(NATIVE_EXPORTS[target])
        if target in known:
            raise mc.err(f"'import *' from native module {target!r} is not supported",
                         node)
        if target in in_progress:
            raise mc.err(f"'import *' from {target!r} within an import cycle", node)
        if not ensure(target, mc, node):
            raise mc.err(f"cannot resolve import {target!r} for 'import *'", node)
        return exports[target]

    def ensure(name: str, importer: ModuleCompiler | None, node) -> bool:
        if name in modules or name in in_progress:
            return True
        rp = resolve_path(name)
        if rp is None:
            return False
        if "." in name:
            parent = name.rsplit(".", 1)[0]
            if not ensure(parent, importer, node):
                raise DiasporeCompileError(
                    f"package {parent!r} not found for module {name!r}", rp[0])
        path, is_package = rp
        compile_one(name, path, is_package)
        return True

    def compile_one(name: str, path: str, is_package: bool) -> None:
        with open(path, encoding="utf-8") as f:
            source = f.read()
        rel = os.path.relpath(path)
        pkg_ctx = name if is_package else (
            name.rsplit(".", 1)[0] if "." in name else "")
        mc = ModuleCompiler(name, path if rel.startswith("..") else rel, source,
                            package_ctx=pkg_ctx, star_resolver=star_resolver,
                            is_module=lambda n: resolve_path(n) is not None)
        in_progress.add(name)
        try:
            compiled = mc.compile()
        finally:
            in_progress.discard(name)
        modules[name] = compiled
        if debug:
            compiled["src"] = source.splitlines()
        col = _LocalsCollector()
        for st in ast.parse(source).body:
            col.visit(st)
        exports[name] = module_exports(name, col.assigned, compiled)
        for dep in sorted(mc.imports):
            if dep in modules or dep in in_progress:
                continue
            if ensure(dep, mc, None):
                continue
            if dep.split(".")[0] in known and "." not in dep:
                continue
            # Preserve optional and lazy imports for runtime resolution.
            unresolved.setdefault(dep, mc.file)

    compile_one(entry_name, entry_path, False)
    ordered = {entry_name: modules[entry_name]}
    for name in sorted(k for k in modules if k != entry_name):
        ordered[name] = modules[name]
    if unresolved_out is not None:
        unresolved_out.update(sorted(unresolved.items()))
    return compact_bundle({"diaspore": DIASPORE_VERSION, "main": entry_name,
                           "modules": ordered})


# Readers use defaults for omitted fields (docs/spec.md section 2.2).
_OMIT_DEFAULTS: list[tuple[str, object]] = [
    ("orelse", []), ("finalbody", []), ("handlers", []), ("kw", []),
    ("args", []), ("elts", []), ("entries", []), ("bases", []),
    ("gdecl", []), ("ndecl", []), ("locals", []), ("gen", False), ("coro", False),
    ("defaults", []), ("kwonly", []), ("kwdefaults", []),
    ("vararg", None), ("kwarg", None), ("conv", None), ("spec", None),
    ("t", None), ("name", None),
]


def compact_bundle(bundle: dict) -> dict:
    """Remove default-valued fields recursively and return the same bundle."""

    def strip(node):
        if isinstance(node, list):
            for x in node:
                strip(x)
            return
        if not isinstance(node, dict):
            return
        is_const = node.get("op") == "const"
        for key, default in _OMIT_DEFAULTS:
            if key in node and node[key] == default:
                if is_const and key == "t":
                    continue  # const's "t" is its discriminator, never omitted
                del node[key]
        for value in node.values():
            strip(value)

    for mod in bundle["modules"].values():
        strip(mod["body"])
    return bundle


def dumps(bundle: dict, pretty: bool = False) -> str:
    """Serialize a bundle as UTF-8-compatible JSON, optionally indented."""
    if pretty:
        return json.dumps(bundle, indent=2, ensure_ascii=False)
    return json.dumps(bundle, separators=(",", ":"), ensure_ascii=False)
