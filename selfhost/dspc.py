"""Compile Python-subset source to Diaspore JSON IR using the Diaspore subset."""

import json
import sys

from _pyparse import CompileError, Parser, tokenize
from _pyunparse import unparse_annotation

DIASPORE_VERSION = 1

NATIVE_MODULES = {"time", "math", "json", "random", "sys", "net", "re", "tasks", "flow", "_os"}
TYPE_ONLY_MODULES = {"typing", "__future__"}
# Bundles name stdlib modules "<stdlib>/name.py", independent of the build host.
STDLIB_FILE_PREFIX = "<stdlib>/"

# Keep star-import exports synchronized with make_native_modules in vm.py.
NATIVE_EXPORTS = {
    "_os": ["getcwd", "kind", "listdir", "realpath"],
    "sys": ["argv", "exit", "platform", "stderr_write", "stdin", "stdout", "stderr",
            "getrecursionlimit", "setrecursionlimit"],
    "math": ["pi", "e", "inf", "nan", "sqrt", "floor", "ceil", "fabs", "pow",
             "log", "log2", "log10", "exp", "sin", "cos", "tan", "hypot",
             "fmod", "isnan", "isinf"],
    "time": ["time", "monotonic", "sleep"],
    "json": ["dumps", "loads"],
    "random": ["seed", "random", "randint", "choice", "shuffle", "Random"],
    "re": ["IGNORECASE", "MULTILINE", "DOTALL", "search", "match", "fullmatch",
           "findall", "split", "sub", "escape"],
    "flow": ["suspend"],
    "net": ["listen", "connect", "accept", "recv", "send", "close", "peer"],
    "tasks": ["spawn", "channel", "lock"],
}


B64CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def b64encode(bs):
    out = []
    i = 0
    n = len(bs)
    while i + 2 < n:
        a = bs[i]
        b = bs[i + 1]
        c = bs[i + 2]
        out.append(B64CHARS[a >> 2])
        out.append(B64CHARS[((a & 3) << 4) | (b >> 4)])
        out.append(B64CHARS[((b & 15) << 2) | (c >> 6)])
        out.append(B64CHARS[c & 63])
        i += 3
    rem = n - i
    if rem == 1:
        a = bs[i]
        out.append(B64CHARS[a >> 2])
        out.append(B64CHARS[(a & 3) << 4])
        out.append("==")
    elif rem == 2:
        a = bs[i]
        b = bs[i + 1]
        out.append(B64CHARS[a >> 2])
        out.append(B64CHARS[((a & 3) << 4) | (b >> 4)])
        out.append(B64CHARS[(b & 15) << 2])
        out.append("=")
    return "".join(out)


class Col:
    """Collect local assignments, excluding declarations and nested scopes."""

    def __init__(self):
        self.assigned = set()
        self.gdecl = set()
        self.ndecl = set()

    def stmts(self, body):
        for s in body:
            self.stmt(s)

    def stmt(self, s):
        t = s["t"]
        if t == "Expr":
            self.expr(s["v"])
        elif t == "Assign":
            for tg in s["targets"]:
                self.tgt(tg)
            self.expr(s["v"])
        elif t == "Ann":
            self.tgt(s["target"])
            self.expr(s["annotation"])
            if s["v"] is not None:
                self.expr(s["v"])
        elif t == "Aug":
            self.tgt(s["target"])
            self.expr(s["v"])
        elif t == "If" or t == "While":
            self.expr(s["test"])
            self.stmts(s["then"] if t == "If" else s["body"])
            self.stmts(s["orelse"])
        elif t == "For" or t == "AsyncFor":
            self.tgt(s["target"])
            self.expr(s["iter"])
            self.stmts(s["body"])
            self.stmts(s["orelse"])
        elif t == "Return":
            if s["v"] is not None:
                self.expr(s["v"])
        elif t == "Func":
            self.assigned.add(s["name"])
            for d in s["decorators"]:
                self.expr(d)
            for d in s["params"]["defaults"]:
                self.expr(d)
            for d in s["params"]["kwdefaults"]:
                if d is not None:
                    self.expr(d)
        elif t == "Class":
            self.assigned.add(s["name"])
            for d in s["decorators"]:
                self.expr(d)
            for b in s["bases"]:
                self.expr(b)
        elif t == "Raise":
            if s["exc"] is not None:
                self.expr(s["exc"])
            if s["cause"] is not None:
                self.expr(s["cause"])
        elif t == "Try":
            self.stmts(s["body"])
            for h in s["handlers"]:
                if h["type"] is not None:
                    self.expr(h["type"])
                if h["name"] is not None:
                    self.assigned.add(h["name"])
                self.stmts(h["body"])
            self.stmts(s["orelse"])
            self.stmts(s["final"])
        elif t == "Assert":
            self.expr(s["test"])
            if s["msg"] is not None:
                self.expr(s["msg"])
        elif t == "With" or t == "AsyncWith":
            for it in s["items"]:
                self.expr(it["ctx"])
                if it["vars"] is not None:
                    self.tgt(it["vars"])
            self.stmts(s["body"])
        elif t == "Import":
            for a in s["names"]:
                if a["as"] is not None:
                    self.assigned.add(a["as"])
                else:
                    self.assigned.add(a["n"].split(".")[0])
        elif t == "ImportFrom":
            for a in s["names"]:
                self.assigned.add(a["as"] if a["as"] is not None else a["n"])
        elif t == "Global":
            self.gdecl = self.gdecl | set(s["names"])
        elif t == "Nonlocal":
            self.ndecl = self.ndecl | set(s["names"])
        elif t == "Del":
            for tg in s["targets"]:
                self.tgt(tg)
        elif t == "Match":
            self.expr(s["subject"])
            for c in s["cases"]:
                self.pat(c["pattern"])
                if c["guard"] is not None:
                    self.expr(c["guard"])
                self.stmts(c["body"])

    def tgt(self, e):
        t = e["t"]
        if t == "Name":
            self.assigned.add(e["id"])
        elif t == "Tuple" or t == "List":
            for x in e["elts"]:
                self.tgt(x)
        elif t == "Starred":
            self.tgt(e["v"])
        elif t == "Attr":
            self.expr(e["o"])
        elif t == "Sub":
            self.expr(e["o"])
            self.sub_k(e["k"])
        else:
            self.expr(e)

    def sub_k(self, k):
        if k["t"] == "Slice":
            if k["lower"] is not None:
                self.expr(k["lower"])
            if k["upper"] is not None:
                self.expr(k["upper"])
            if k["step"] is not None:
                self.expr(k["step"])
        elif k["t"] == "Tuple":
            for x in k["elts"]:
                self.sub_k(x)
        else:
            self.expr(k)

    def expr(self, e):
        t = e["t"]
        if t == "Const" or t == "Name":
            return
        if t == "Attr":
            self.expr(e["o"])
        elif t == "Sub":
            self.expr(e["o"])
            self.sub_k(e["k"])
        elif t == "BinOp":
            self.expr(e["l"])
            self.expr(e["r"])
        elif t == "Unary":
            self.expr(e["v"])
        elif t == "Bool":
            for v in e["values"]:
                self.expr(v)
        elif t == "Cmp":
            self.expr(e["left"])
            for c in e["comps"]:
                self.expr(c)
        elif t == "Cond":
            self.expr(e["test"])
            self.expr(e["then"])
            self.expr(e["else"])
        elif t == "Call":
            self.expr(e["f"])
            for a in e["args"]:
                self.expr(a)
            for kwe in e["kw"]:
                self.expr(kwe["v"])
        elif t == "List" or t == "Tuple" or t == "Set":
            for x in e["elts"]:
                self.expr(x)
        elif t == "Dict":
            for en in e["entries"]:
                if en["k"] is not None:
                    self.expr(en["k"])
                self.expr(en["v"])
        elif t == "Fstr":
            for p in e["parts"]:
                if "lit" not in p:
                    self.expr(p["v"])
                    if p["spec"] is not None:
                        self.expr(p["spec"])
        elif t == "Lambda":
            for d in e["params"]["defaults"]:
                self.expr(d)
            for d in e["params"]["kwdefaults"]:
                if d is not None:
                    self.expr(d)
        elif t == "Comp":
            self.expr(e["gens"][0]["iter"])
        elif t == "Yield":
            if e["v"] is not None:
                self.expr(e["v"])
        elif t == "YieldFrom" or t == "Await":
            self.expr(e["v"])
        elif t == "Named":
            self.assigned.add(e["target"]["id"])
            self.expr(e["v"])
        elif t == "Starred":
            self.expr(e["v"])
        elif t == "Slice":
            self.sub_k(e)

    def pat(self, p):
        t = p["t"]
        if t == "MVal":
            self.expr(p["v"])
        elif t == "MAs":
            if p["name"] is not None:
                self.assigned.add(p["name"])
            if p["pat"] is not None:
                self.pat(p["pat"])
        elif t == "MOr" or t == "MSeq":
            for q in p["pats"]:
                self.pat(q)
        elif t == "MStar":
            if p["name"] is not None:
                self.assigned.add(p["name"])
        elif t == "MMap":
            if p["rest"] is not None:
                self.assigned.add(p["rest"])
            for k in p["keys"]:
                self.expr(k)
            for q in p["pats"]:
                self.pat(q)
        elif t == "MCls":
            self.expr(p["cls"])
            for q in p["pos"]:
                self.pat(q)
            for q in p["kpats"]:
                self.pat(q)


def is_type_checking(test):
    """Match TYPE_CHECKING names and supported qualified forms."""
    if test["t"] == "Name":
        return test["id"] == "TYPE_CHECKING"
    return test["t"] == "Attr" and test["n"] == "TYPE_CHECKING" \
        and test["o"]["t"] == "Name"


def yield_in_stmts(body):
    for s in body:
        if yield_in_stmt(s):
            return True
    return False


def yield_in_stmt(s):
    t = s["t"]
    if t == "Func":
        return False
    if t == "Expr":
        return yield_in_expr(s["v"])
    if t == "Assign":
        for tg in s["targets"]:
            if yield_in_expr(tg):
                return True
        return yield_in_expr(s["v"])
    if t == "Ann":
        if yield_in_expr(s["target"]):
            return True
        return s["v"] is not None and yield_in_expr(s["v"])
    if t == "Aug":
        return yield_in_expr(s["target"]) or yield_in_expr(s["v"])
    if t == "If":
        return (yield_in_expr(s["test"]) or yield_in_stmts(s["then"])
                or yield_in_stmts(s["orelse"]))
    if t == "While":
        return (yield_in_expr(s["test"]) or yield_in_stmts(s["body"])
                or yield_in_stmts(s["orelse"]))
    if t == "For" or t == "AsyncFor":
        return (yield_in_expr(s["target"]) or yield_in_expr(s["iter"])
                or yield_in_stmts(s["body"]) or yield_in_stmts(s["orelse"]))
    if t == "Return":
        return s["v"] is not None and yield_in_expr(s["v"])
    if t == "Class":
        for d in s["decorators"]:
            if yield_in_expr(d):
                return True
        for b in s["bases"]:
            if yield_in_expr(b):
                return True
        for kw in s["keywords"]:
            if yield_in_expr(kw["v"]):
                return True
        return yield_in_stmts(s["body"])
    if t == "Raise":
        if s["exc"] is not None and yield_in_expr(s["exc"]):
            return True
        return s["cause"] is not None and yield_in_expr(s["cause"])
    if t == "Try":
        if yield_in_stmts(s["body"]) or yield_in_stmts(s["orelse"]) \
                or yield_in_stmts(s["final"]):
            return True
        for h in s["handlers"]:
            if h["type"] is not None and yield_in_expr(h["type"]):
                return True
            if yield_in_stmts(h["body"]):
                return True
        return False
    if t == "Assert":
        if yield_in_expr(s["test"]):
            return True
        return s["msg"] is not None and yield_in_expr(s["msg"])
    if t == "With" or t == "AsyncWith":
        for it in s["items"]:
            if yield_in_expr(it["ctx"]):
                return True
            if it["vars"] is not None and yield_in_expr(it["vars"]):
                return True
        return yield_in_stmts(s["body"])
    if t == "Del":
        for tg in s["targets"]:
            if yield_in_expr(tg):
                return True
        return False
    if t == "Match":
        if yield_in_expr(s["subject"]):
            return True
        for c in s["cases"]:
            if c["guard"] is not None and yield_in_expr(c["guard"]):
                return True
            if yield_in_stmts(c["body"]):
                return True
        return False
    return False


def yield_in_expr(e):
    t = e["t"]
    if t == "Yield" or t == "YieldFrom":
        return True
    if t == "Lambda" or t == "Comp":
        return False
    if t == "Await":
        return yield_in_expr(e["v"])
    if t == "Const" or t == "Name":
        return False
    if t == "Attr":
        return yield_in_expr(e["o"])
    if t == "Sub":
        return yield_in_expr(e["o"]) or yield_in_k(e["k"])
    if t == "Slice":
        return yield_in_k(e)
    if t == "BinOp":
        return yield_in_expr(e["l"]) or yield_in_expr(e["r"])
    if t == "Unary" or t == "Starred":
        return yield_in_expr(e["v"])
    if t == "Bool":
        for v in e["values"]:
            if yield_in_expr(v):
                return True
        return False
    if t == "Cmp":
        if yield_in_expr(e["left"]):
            return True
        for c in e["comps"]:
            if yield_in_expr(c):
                return True
        return False
    if t == "Cond":
        return (yield_in_expr(e["test"]) or yield_in_expr(e["then"])
                or yield_in_expr(e["else"]))
    if t == "Call":
        if yield_in_expr(e["f"]):
            return True
        for a in e["args"]:
            if yield_in_expr(a):
                return True
        for kwe in e["kw"]:
            if yield_in_expr(kwe["v"]):
                return True
        return False
    if t == "List" or t == "Tuple" or t == "Set":
        for x in e["elts"]:
            if yield_in_expr(x):
                return True
        return False
    if t == "Dict":
        for en in e["entries"]:
            if en["k"] is not None and yield_in_expr(en["k"]):
                return True
            if yield_in_expr(en["v"]):
                return True
        return False
    if t == "Fstr":
        for p in e["parts"]:
            if "lit" not in p:
                if yield_in_expr(p["v"]):
                    return True
                if p["spec"] is not None and yield_in_expr(p["spec"]):
                    return True
        return False
    if t == "Named":
        return yield_in_expr(e["v"])
    return False


def yield_in_k(k):
    if k["t"] == "Slice":
        if k["lower"] is not None and yield_in_expr(k["lower"]):
            return True
        if k["upper"] is not None and yield_in_expr(k["upper"]):
            return True
        return k["step"] is not None and yield_in_expr(k["step"])
    return yield_in_expr(k)


def ir_name(id_):
    return {"op": "name", "id": id_}


def ir_int(v):
    if v < 0:
        return {"op": "unary", "kind": "neg",
                "v": {"op": "const", "t": "int", "v": str(-v)}}
    return {"op": "const", "t": "int", "v": str(v)}


def ir_cmp(kind, l, r):
    return {"op": "compare", "left": l, "ops": [kind], "comps": [r]}


def ir_call(fname, args):
    return {"op": "call", "f": ir_name(fname), "args": args, "kw": []}


def ir_not(v):
    return {"op": "unary", "kind": "not", "v": v}


def ir_true():
    return {"op": "const", "t": "bool", "v": True}


def ir_false():
    return {"op": "const", "t": "bool", "v": False}


def ir_if(test, then, ln):
    return {"op": "if", "test": test, "then": then, "orelse": [], "ln": ln}


def has_annotations(body):
    for stmt in body:
        if stmt["t"] == "Ann":
            return True
        if stmt["t"] in ("Func", "Class"):
            continue
        for field in ("body", "then", "orelse", "final"):
            if has_annotations(stmt.get(field, [])):
                return True
        for field in ("handlers", "cases"):
            for branch in stmt.get(field, []):
                if has_annotations(branch["body"]):
                    return True
    return False


def annotation_setup(ln):
    # Bind a fresh local dictionary, never an enclosing scope's annotations.
    return {"op": "assign", "targets": [ir_name("__annotations__")],
            "v": {"op": "dict", "entries": []}, "ln": ln}


class Scope:
    def __init__(self, kind, locals_, gdecl, ndecl, class_name=""):
        self.kind = kind
        self.locals = locals_
        self.gdecl = gdecl
        self.ndecl = ndecl
        self.class_name = class_name


class ModuleCompiler:
    def __init__(self, name, file, source, package_ctx="", bundler=None):
        self.name = name
        self.file = file
        self.source = source
        self.scopes = []
        self.imports = set()
        # Empty package context denotes a top-level module.
        self.package_ctx = package_ctx
        self.bundler = bundler
        self.tree_body = []
        self.tmp = 0
        self.future_annotations = False

    def is_module(self, name):
        if self.bundler is None:
            return False
        return self.bundler.is_module(name)

    def gensym(self, prefix):
        self.tmp += 1
        return "." + prefix + str(self.tmp)

    def err(self, msg, ln):
        return CompileError(msg, self.file, ln)

    def compile(self):
        toks = tokenize(self.source, self.file)
        tree = Parser(toks, self.file).parse_module()
        self.tree_body = tree["body"]
        for s in tree["body"]:
            if s["t"] == "ImportFrom" and s["level"] == 0 and s["module"] == "__future__":
                for alias in s["names"]:
                    if alias["n"] == "annotations":
                        self.future_annotations = True
        self.scopes.append(Scope("module", set(), set(), set()))
        body = [annotation_setup(tree["body"][0]["ln"])] if has_annotations(tree["body"]) else []
        body.extend(self.block(tree["body"]))
        return {"name": self.name, "file": self.file, "body": body}

    def block(self, stmts):
        out = []
        for i, s in enumerate(stmts):
            if (i == 0 and s["t"] == "Expr" and s["v"]["t"] == "Const"
                    and s["v"]["kind"] == "str"):
                continue
            r = self.stmt(s)
            if r is None:
                continue
            if isinstance(r, list):
                out.extend(r)
            else:
                out.append(r)
        return out

    def stmt(self, s):
        t = s["t"]
        ln = s["ln"]
        if t == "Expr":
            return {"op": "expr", "v": self.expr(s["v"]), "ln": ln}
        if t == "Assign":
            return {"op": "assign", "targets": [self.target(x) for x in s["targets"]],
                    "v": self.expr(s["v"]), "ln": ln}
        if t == "Ann":
            return self.annassign(s)
        if t == "Aug":
            return {"op": "augassign", "target": self.target(s["target"]),
                    "kind": s["kind"], "v": self.expr(s["v"]), "ln": ln}
        if t == "If":
            if is_type_checking(s["test"]):
                # Type-only imports must not become runtime dependencies.
                else_body = self.block(s["orelse"])
                if else_body:
                    return else_body
                return None
            return {"op": "if", "test": self.expr(s["test"]),
                    "then": self.block(s["then"]),
                    "orelse": self.block(s["orelse"]), "ln": ln}
        if t == "While":
            return {"op": "while", "test": self.expr(s["test"]),
                    "body": self.block(s["body"]),
                    "orelse": self.block(s["orelse"]), "ln": ln}
        if t == "For":
            return {"op": "for", "target": self.target(s["target"]),
                    "iter": self.expr(s["iter"]), "body": self.block(s["body"]),
                    "orelse": self.block(s["orelse"]), "ln": ln}
        if t == "Break":
            return {"op": "break", "ln": ln}
        if t == "Continue":
            return {"op": "continue", "ln": ln}
        if t == "Pass":
            return {"op": "pass", "ln": ln}
        if t == "Return":
            if self.scopes[-1].kind != "function":
                raise self.err("'return' outside function", ln)
            node = {"op": "return", "ln": ln}
            if s["v"] is not None:
                node["v"] = self.expr(s["v"])
            return node
        if t == "Func":
            return self.funcdef(s)
        if t == "Class":
            return self.classdef(s)
        if t == "Raise":
            node = {"op": "raise", "ln": ln}
            if s["exc"] is not None:
                node["exc"] = self.expr(s["exc"])
            if s["cause"] is not None:
                node["cause"] = self.expr(s["cause"])
            return node
        if t == "Try":
            handlers = []
            for h in s["handlers"]:
                handlers.append({
                    "t": self.expr(h["type"]) if h["type"] is not None else None,
                    "name": h["name"],
                    "body": self.block(h["body"]),
                })
            node = {"op": "try", "body": self.block(s["body"]),
                    "handlers": handlers, "orelse": self.block(s["orelse"]),
                    "finalbody": self.block(s["final"]), "ln": ln}
            if s.get("star"):
                node["star"] = True
            return node
        if t == "Assert":
            raise_node = {
                "op": "raise", "ln": ln,
                "exc": {"op": "call", "f": {"op": "name", "id": "AssertionError"},
                        "args": [self.expr(s["msg"])] if s["msg"] is not None else [],
                        "kw": []},
            }
            return {"op": "if",
                    "test": {"op": "unary", "kind": "not", "v": self.expr(s["test"])},
                    "then": [raise_node], "orelse": [], "ln": ln}
        if t == "With":
            out_items = []
            for it in s["items"]:
                out_items.append({
                    "ctx": self.expr(it["ctx"]),
                    "name": self.target(it["vars"]) if it["vars"] is not None else None,
                })
            return {"op": "with", "items": out_items,
                    "body": self.block(s["body"]), "ln": ln}
        if t == "Import":
            out = []
            for a in s["names"]:
                if a["n"] in TYPE_ONLY_MODULES:
                    continue
                self.imports.add(a["n"])
                if a["as"] is not None:
                    out.append({"op": "import", "module": a["n"],
                                "alias": a["as"], "ln": ln})
                elif "." in a["n"]:
                    out.append({"op": "import", "module": a["n"],
                                "alias": a["n"].split(".")[0],
                                "bind": "root", "ln": ln})
                else:
                    out.append({"op": "import", "module": a["n"],
                                "alias": a["n"], "ln": ln})
            if out:
                return out
            return None
        if t == "ImportFrom":
            return self.import_from(s, ln)
        if t == "Del":
            return {"op": "del", "targets": [self.target(x) for x in s["targets"]],
                    "ln": ln}
        if t == "Global" or t == "Nonlocal":
            return None
        if t == "AsyncFor":
            return self.async_for(s["target"], s["iter"], s["body"],
                                  s["orelse"], ln)
        if t == "AsyncWith":
            return self.async_with(s["items"], s["body"], ln)
        if t == "Match":
            return self.match_stmt(s)
        raise self.err("unsupported statement: " + t, ln)

    def annotation_subscript(self, key, ln):
        if key["t"] == "Slice":
            return [{"op": "expr", "v": self.expr(part), "ln": ln}
                    for part in (key["lower"], key["upper"], key["step"])
                    if part is not None]
        if key["t"] == "Tuple":
            out = []
            for part in key["elts"]:
                out.extend(self.annotation_subscript(part, ln))
            return out
        return [{"op": "expr", "v": self.expr(key), "ln": ln}]

    def annassign(self, s):
        out = []
        target = s["target"]
        ln = s["ln"]
        if s["v"] is not None:
            out.append({"op": "assign", "targets": [self.target(target)],
                        "v": self.expr(s["v"]), "ln": ln})
        elif target["t"] in ("Attr", "Sub"):
            # Declarations evaluate base/index expressions, not the target load.
            out.append({"op": "expr", "v": self.expr(target["o"]), "ln": ln})
            if target["t"] == "Sub":
                out.extend(self.annotation_subscript(target["k"], ln))
        if self.scopes[-1].kind in ("module", "class"):
            if s["simple"] and target["t"] == "Name":
                if self.future_annotations:
                    annotation = {"op": "const", "t": "str",
                                  "v": unparse_annotation(s["annotation"])}
                else:
                    annotation = self.expr(s["annotation"])
                name = target["id"]
                class_name = self.scopes[-1].class_name.lstrip("_")
                if class_name and name.startswith("__") and not name.endswith("__"):
                    name = "_" + class_name + name
                out.append({"op": "assign", "targets": [{
                    "op": "index", "o": ir_name("__annotations__"),
                    "k": {"op": "const", "t": "str", "v": name}}],
                    "v": annotation, "ln": ln})
            elif not self.future_annotations:
                out.append({"op": "expr", "v": self.expr(s["annotation"]), "ln": ln})
        return out

    def import_from(self, s, ln):
        if s["level"]:
            ctx = self.package_ctx
            if not ctx:
                raise self.err(
                    "attempted relative import with no known parent package", ln)
            k = 0
            while k < s["level"] - 1:
                if "." not in ctx:
                    raise self.err(
                        "attempted relative import beyond top-level package", ln)
                ctx = ctx.rsplit(".", 1)[0]
                k += 1
            if s["module"]:
                target = ctx + "." + s["module"]
            else:
                target = ctx
        else:
            if s["module"] is None:
                raise self.err("unsupported import form", ln)
            target = s["module"]
        if target in TYPE_ONLY_MODULES:
            return None
        has_star = False
        for a in s["names"]:
            if a["n"] == "*":
                has_star = True
        if has_star:
            if len(s["names"]) != 1:
                raise self.err("unsupported import form", ln)
            if self.bundler is None:
                raise self.err("'import *' is not supported here", ln)
            exported = self.bundler.star_resolver(target, self, ln)
            self.imports.add(target)
            return {"op": "importfrom", "module": target,
                    "names": [{"n": n2, "alias": n2} for n2 in exported],
                    "ln": ln}
        self.imports.add(target)
        out = []
        for a in s["names"]:
            child = target + "." + a["n"]
            if self.is_module(child):
                # CPython imports the submodule before reading the package attribute.
                self.imports.add(child)
                out.append({"op": "import", "module": child, "bind": "none",
                            "ln": ln})
        out.append({"op": "importfrom", "module": target,
                    "names": [{"n": a["n"],
                               "alias": a["as"] if a["as"] is not None else a["n"]}
                              for a in s["names"]],
                    "ln": ln})
        return out

    def params(self, pa):
        return {
            "args": pa["args"],
            "defaults": [self.expr(d) for d in pa["defaults"]],
            "vararg": pa["vararg"],
            "kwonly": pa["kwonly"],
            "kwdefaults": [self.expr(d) if d is not None else None
                           for d in pa["kwdefaults"]],
            "kwarg": pa["kwarg"],
        }

    def function_node(self, name, pa, body, ln, op, coro=False):
        params = self.params(pa)
        col = Col()
        for st in body:
            col.stmt(st)
        param_names = set(params["args"]) | set(params["kwonly"])
        if params["vararg"] is not None:
            param_names.add(params["vararg"])
        if params["kwarg"] is not None:
            param_names.add(params["kwarg"])
        bad = (col.gdecl | col.ndecl) & param_names
        if bad:
            raise self.err("name '" + min(bad) + "' is parameter and global/nonlocal",
                           None)
        locals_ = (col.assigned | param_names) - col.gdecl - col.ndecl
        for n2 in col.ndecl:
            found = False
            for sc in self.scopes:
                if sc.kind == "function" and n2 in sc.locals:
                    found = True
                    break
            if not found:
                raise self.err("no binding for nonlocal '" + n2 + "' found", None)
        self.scopes.append(Scope("function", locals_, col.gdecl, col.ndecl))
        try:
            compiled = self.block(body)
        finally:
            self.scopes.pop()
        # Await lowers to yield-from; coro preserves coroutine identity for the VM.
        return {
            "op": op, "name": name,
            "gen": yield_in_stmts(body) or coro,
            "coro": coro,
            "params": params,
            "locals": sorted(locals_), "gdecl": sorted(col.gdecl),
            "ndecl": sorted(col.ndecl), "body": compiled, "ln": ln,
        }

    def funcdef(self, s):
        node = self.function_node(s["name"], s["params"], s["body"], s["ln"],
                                  "func", s["coro"])
        if not s["decorators"]:
            return node
        node["op"] = "funcexpr"
        del node["ln"]
        value = node
        for dec in s["decorators"][::-1]:
            value = {"op": "call", "f": self.expr(dec), "args": [value], "kw": []}
        return {"op": "assign", "targets": [{"op": "name", "id": s["name"]}],
                "v": value, "ln": s["ln"]}

    def classdef(self, s):
        # A null keyword name denotes **mapping; the VM resolves metaclasses.
        kwargs = []
        for kw in s["keywords"]:
            kwargs.append({"n": kw["n"], "v": self.expr(kw["v"])})
        col = Col()
        for st in s["body"]:
            col.stmt(st)
        self.scopes.append(Scope("class", col.assigned, col.gdecl, col.ndecl, s["name"]))
        try:
            body = [annotation_setup(s["ln"])] if has_annotations(s["body"]) else []
            body.extend(self.block(s["body"]))
        finally:
            self.scopes.pop()
        bases = []
        for b in s["bases"]:
            if b["t"] == "Starred":
                bases.append({"op": "starred", "v": self.expr(b["v"])})
            else:
                bases.append(self.expr(b))
        node = {"op": "class", "name": s["name"],
                "bases": bases, "body": body, "ln": s["ln"]}
        if kwargs:
            node["kwargs"] = kwargs
        if not s["decorators"]:
            return node
        stmts = [node]
        value = {"op": "name", "id": s["name"]}
        for dec in s["decorators"][::-1]:
            value = {"op": "call", "f": self.expr(dec), "args": [value], "kw": []}
        stmts.append({"op": "assign", "targets": [{"op": "name", "id": s["name"]}],
                      "v": value, "ln": s["ln"]})
        return stmts

    def target(self, t):
        k = t["t"]
        if k == "Name":
            return {"op": "name", "id": t["id"]}
        if k == "Attr":
            return {"op": "attr", "o": self.expr(t["o"]), "n": t["n"]}
        if k == "Sub":
            return {"op": "index", "o": self.expr(t["o"]),
                    "k": self.subscript(t["k"])}
        if k == "Tuple" or k == "List":
            return {"op": "tuple", "elts": [self.target(e) for e in t["elts"]]}
        if k == "Starred":
            return {"op": "starred", "v": self.target(t["v"])}
        raise self.err("unsupported assignment target: " + k, t["ln"])

    def subscript(self, k):
        if k["t"] == "Slice":
            node = {"op": "slicelit"}
            if k["lower"] is not None:
                node["lower"] = self.expr(k["lower"])
            if k["upper"] is not None:
                node["upper"] = self.expr(k["upper"])
            if k["step"] is not None:
                node["step"] = self.expr(k["step"])
            return node
        if k["t"] == "Tuple":
            has_slice = False
            for e in k["elts"]:
                if e["t"] == "Slice":
                    has_slice = True
            if has_slice:
                # Slices inside an index tuple require explicit slice objects.
                elts = []
                for e in k["elts"]:
                    if e["t"] == "Slice":
                        args = [
                            self.expr(e["lower"]) if e["lower"] is not None
                            else {"op": "const", "t": "none"},
                            self.expr(e["upper"]) if e["upper"] is not None
                            else {"op": "const", "t": "none"},
                            self.expr(e["step"]) if e["step"] is not None
                            else {"op": "const", "t": "none"}]
                        elts.append(ir_call("slice", args))
                    else:
                        elts.append(self.expr(e))
                return {"op": "tuple", "elts": elts}
        return self.expr(k)

    def const(self, c):
        kind = c["kind"]
        if kind == "none":
            return {"op": "const", "t": "none"}
        if kind == "ellipsis":
            return {"op": "const", "t": "ellipsis"}
        if kind == "bool":
            return {"op": "const", "t": "bool", "v": c["v"]}
        if kind == "int":
            return {"op": "const", "t": "int", "v": str(c["v"])}
        if kind == "float":
            return {"op": "const", "t": "float", "v": str(c["v"])}
        if kind == "complex":
            return {"op": "const", "t": "complex", "re": str(0.0),
                    "im": str(c["v"])}
        if kind == "str":
            return {"op": "const", "t": "str", "v": c["v"]}
        return {"op": "const", "t": "bytes", "v": b64encode(c["v"])}

    def expr(self, e):
        t = e["t"]
        if t == "Const":
            return self.const(e)
        if t == "Name":
            return {"op": "name", "id": e["id"]}
        if t == "Attr":
            return {"op": "attr", "o": self.expr(e["o"]), "n": e["n"]}
        if t == "Sub":
            return {"op": "index", "o": self.expr(e["o"]),
                    "k": self.subscript(e["k"])}
        if t == "BinOp":
            return {"op": "binop", "kind": e["kind"], "l": self.expr(e["l"]),
                    "r": self.expr(e["r"])}
        if t == "Unary":
            return {"op": "unary", "kind": e["kind"], "v": self.expr(e["v"])}
        if t == "Bool":
            return {"op": "boolop", "kind": e["kind"],
                    "values": [self.expr(v) for v in e["values"]]}
        if t == "Cmp":
            return {"op": "compare", "left": self.expr(e["left"]),
                    "ops": e["ops"],
                    "comps": [self.expr(c) for c in e["comps"]]}
        if t == "Cond":
            return {"op": "cond", "test": self.expr(e["test"]),
                    "then": self.expr(e["then"]), "else": self.expr(e["else"])}
        if t == "Call":
            cargs = []
            for a in e["args"]:
                if a["t"] == "Starred":
                    cargs.append({"op": "starred", "v": self.expr(a["v"])})
                else:
                    cargs.append(self.expr(a))
            ckw = [{"n": kwe["n"], "v": self.expr(kwe["v"])} for kwe in e["kw"]]
            return {"op": "call", "f": self.expr(e["f"]), "args": cargs, "kw": ckw}
        if t == "List":
            return {"op": "list", "elts": self.elts(e["elts"])}
        if t == "Tuple":
            return {"op": "tuple", "elts": self.elts(e["elts"])}
        if t == "Set":
            return {"op": "set", "elts": self.elts(e["elts"])}
        if t == "Dict":
            entries = []
            for en in e["entries"]:
                entries.append({"k": self.expr(en["k"]) if en["k"] is not None
                                else None,
                                "v": self.expr(en["v"])})
            return {"op": "dict", "entries": entries}
        if t == "Fstr":
            return self.fstring(e["parts"])
        if t == "Lambda":
            ret = {"t": "Return", "v": e["body"], "ln": e["body"]["ln"]}
            return self.function_node("<lambda>", e["params"], [ret], e["ln"],
                                      "funcexpr")
        if t == "Comp":
            return self.comprehension(e)
        if t == "Yield":
            self.check_yield(e)
            node = {"op": "yield"}
            if e["v"] is not None:
                node["v"] = self.expr(e["v"])
            return node
        if t == "YieldFrom":
            self.check_yield(e)
            return {"op": "yieldfrom", "v": self.expr(e["v"])}
        if t == "Named":
            return {"op": "namedexpr",
                    "target": {"op": "name", "id": e["target"]["id"]},
                    "v": self.expr(e["v"])}
        if t == "Await":
            if self.scopes[-1].kind != "function":
                raise self.err("'await' outside async function", e["ln"])
            return {"op": "yieldfrom",
                    "v": ir_call(".await", [self.expr(e["v"])])}
        if t == "Starred":
            raise self.err("starred expression not allowed here", e["ln"])
        raise self.err("unsupported expression: " + t, e["ln"])

    def check_yield(self, e):
        if self.scopes[-1].kind != "function":
            raise self.err("'yield' outside function", e["ln"])

    def elts(self, es):
        out = []
        for x in es:
            if x["t"] == "Starred":
                out.append({"op": "starred", "v": self.expr(x["v"])})
            else:
                out.append(self.expr(x))
        return out

    def fstring(self, parts):
        out = []
        for p in parts:
            if "lit" in p:
                out.append({"s": p["lit"]})
                continue
            conv = None
            if p["conv"] == "r":
                conv = "r"
            elif p["conv"] == "s":
                conv = "s"
            elif p["conv"] == "a":
                conv = "a"
            elif p["conv"] is not None:
                raise self.err("unsupported f-string conversion", p["v"]["ln"])
            spec = self.expr(p["spec"]) if p["spec"] is not None else None
            out.append({"v": self.expr(p["v"]), "conv": conv, "spec": spec})
        return {"op": "fstring", "parts": out}

    def comprehension(self, e):
        kind = e["kind"]
        gens = e["gens"]
        ln = e["ln"]
        outer_iter = self.expr(gens[0]["iter"])

        assigned = set()
        for gen in gens:
            self.collect_target_names(gen["target"], assigned)
        locals_ = assigned | {".0", ".r", ".k"}
        self.scopes.append(Scope("function", locals_, set(), set()))
        try:
            if kind == "gen":
                inner = [{"op": "expr",
                          "v": {"op": "yield", "v": self.expr(e["elt"])},
                          "ln": ln}]
            elif kind == "list":
                inner = [self.call_stmt({"op": "attr",
                                         "o": {"op": "name", "id": ".r"},
                                         "n": "append"},
                                        [self.expr(e["elt"])], ln)]
            elif kind == "set":
                inner = [self.call_stmt({"op": "attr",
                                         "o": {"op": "name", "id": ".r"},
                                         "n": "add"},
                                        [self.expr(e["elt"])], ln)]
            else:
                # Assignment evaluates values first; bind the key to preserve
                # dictionary comprehension evaluation order.
                inner = [
                    {"op": "assign", "targets": [{"op": "name", "id": ".k"}],
                     "v": self.expr(e["key"]), "ln": ln},
                    {"op": "assign",
                     "targets": [{"op": "index", "o": {"op": "name", "id": ".r"},
                                  "k": {"op": "name", "id": ".k"}}],
                     "v": self.expr(e["val"]), "ln": ln}]

            body = inner
            i = 0
            for gen in gens[::-1]:
                for cond in gen["ifs"][::-1]:
                    body = [{"op": "if", "test": self.expr(cond), "then": body,
                             "orelse": [], "ln": ln}]
                is_outermost = i == len(gens) - 1
                if is_outermost:
                    it = {"op": "name", "id": ".0"}
                else:
                    it = self.expr(gen["iter"])
                body = [{"op": "for", "target": self.target(gen["target"]),
                         "iter": it, "body": body, "orelse": [], "ln": ln}]
                i += 1
        finally:
            self.scopes.pop()

        if kind == "gen":
            fbody = body
            gen_flag = True
            name = "<genexpr>"
        else:
            if kind == "list":
                init = {"op": "list", "elts": []}
                name = "<listcomp>"
            elif kind == "set":
                init = {"op": "call", "f": {"op": "name", "id": "set"},
                        "args": [], "kw": []}
                name = "<setcomp>"
            else:
                init = {"op": "dict", "entries": []}
                name = "<dictcomp>"
            fbody = [{"op": "assign", "targets": [{"op": "name", "id": ".r"}],
                      "v": init, "ln": ln}]
            fbody.extend(body)
            fbody.append({"op": "return", "v": {"op": "name", "id": ".r"},
                          "ln": ln})
            gen_flag = False

        fn = {"op": "funcexpr", "name": name, "gen": gen_flag, "coro": False,
              "params": {"args": [".0"], "defaults": [], "vararg": None,
                         "kwonly": [], "kwdefaults": [], "kwarg": None},
              "locals": sorted(locals_), "gdecl": [], "ndecl": [],
              "body": fbody, "ln": ln}
        return {"op": "call", "f": fn, "args": [outer_iter], "kw": []}

    def collect_target_names(self, t, into):
        k = t["t"]
        if k == "Name":
            into.add(t["id"])
        elif k == "Tuple" or k == "List":
            for x in t["elts"]:
                self.collect_target_names(x, into)
        elif k == "Starred":
            self.collect_target_names(t["v"], into)
        elif k == "Sub" or k == "Attr":
            # Attribute and subscript stores mutate objects, not local bindings.
            pass
        else:
            raise self.err("unsupported comprehension target", t["ln"])

    def call_stmt(self, f, args, ln):
        return {"op": "expr", "v": {"op": "call", "f": f, "args": args, "kw": []},
                "ln": ln}

    def await_(self, expr_node):
        return {"op": "yieldfrom", "v": ir_call(".await", [expr_node])}

    def async_for(self, target, it, body, orelse, ln):
        if self.scopes[-1].kind != "function":
            raise self.err("'async for' outside async function", target["ln"])
        ait = self.gensym("ai")
        loop = [
            {"op": "assign", "targets": [ir_name(ait)],
             "v": {"op": "call", "f": {"op": "attr", "o": self.expr(it),
                                       "n": "__aiter__"}, "args": [], "kw": []},
             "ln": ln},
        ]
        broke = self.gensym("brk")
        anext = self.await_({"op": "call",
                             "f": {"op": "attr", "o": ir_name(ait),
                                   "n": "__anext__"},
                             "args": [], "kw": []})
        step = {"op": "try",
                "body": [{"op": "assign", "targets": [self.target(target)],
                          "v": anext, "ln": ln}],
                "handlers": [{"t": ir_name("StopAsyncIteration"), "name": None,
                              "body": [{"op": "assign",
                                        "targets": [ir_name(broke)],
                                        "v": ir_true(), "ln": ln},
                                       {"op": "break", "ln": ln}]}],
                "orelse": [], "finalbody": [], "ln": ln}
        while_body = [step] + self.block(body)
        loop.append({"op": "assign", "targets": [ir_name(broke)],
                     "v": ir_false(), "ln": ln})
        loop.append({"op": "while", "test": ir_true(), "body": while_body,
                     "orelse": [], "ln": ln})
        if orelse:
            loop.append({"op": "if", "test": ir_not(ir_name(broke)),
                         "then": self.block(orelse), "orelse": [], "ln": ln})
        return loop

    def async_with(self, items, body, ln):
        if self.scopes[-1].kind != "function":
            raise self.err("'async with' outside async function",
                           items[0]["ctx"]["ln"])
        return self.async_with_items(items, 0, body, ln)

    def async_with_items(self, items, i, body, ln):
        if i == len(items):
            return self.block(body)
        item = items[i]
        mgr = self.gensym("acm")
        ctx = self.expr(item["ctx"])
        aenter = self.await_({"op": "call",
                              "f": {"op": "attr", "o": ir_name(mgr),
                                    "n": "__aenter__"},
                              "args": [], "kw": []})
        aexit = self.await_({"op": "call",
                             "f": {"op": "attr", "o": ir_name(mgr),
                                   "n": "__aexit__"},
                             "args": [{"op": "const", "t": "none"},
                                      {"op": "const", "t": "none"},
                                      {"op": "const", "t": "none"}], "kw": []})
        out = [{"op": "assign", "targets": [ir_name(mgr)], "v": ctx, "ln": ln}]
        if item["vars"] is not None:
            enter_stmts = [{"op": "assign",
                            "targets": [self.target(item["vars"])],
                            "v": aenter, "ln": ln}]
        else:
            enter_stmts = [{"op": "expr", "v": aenter, "ln": ln}]
        inner = self.async_with_items(items, i + 1, body, ln)
        out.extend(enter_stmts)
        out.append({"op": "try", "body": inner, "handlers": [], "orelse": [],
                    "finalbody": [{"op": "expr", "v": aexit, "ln": ln}],
                    "ln": ln})
        return out

    def match_stmt(self, s):
        ln = s["ln"]
        subj = self.gensym("m")
        done = self.gensym("d")
        out = [
            self.set_(subj, self.expr(s["subject"]), ln),
            self.set_(done, ir_false(), ln),
        ]
        for case_ in s["cases"]:
            body = self.block(case_["body"])
            pln = case_["pattern"]["ln"]
            success = [self.set_(done, ir_true(), pln)] + body
            if case_["guard"] is not None:
                success = [ir_if(self.expr(case_["guard"]), success,
                                 case_["guard"]["ln"])]
            attempt = self.pattern(case_["pattern"], ir_name(subj), success)
            out.append(ir_if(ir_not(ir_name(done)), attempt, pln))
        return out

    def pattern(self, p, subj, inner):
        t = p["t"]
        ln = p["ln"]
        if t == "MVal":
            return [ir_if(ir_cmp("eq", subj, self.expr(p["v"])), inner, ln)]
        if t == "MSing":
            return [ir_if(ir_cmp("is", subj, self.const(p["c"])), inner, ln)]
        if t == "MAs":
            if p["pat"] is None and p["name"] is None:
                return inner
            if p["pat"] is None:
                return [self.bind(p["name"], subj, ln)] + inner
            return self.pattern(p["pat"], subj,
                                [self.bind(p["name"], subj, ln)] + inner)
        if t == "MOr":
            ok = self.gensym("o")
            stmts = [self.set_(ok, ir_false(), ln)]
            for alt in p["pats"]:
                hit = self.pattern(alt, subj,
                                   [self.set_(ok, ir_true(), alt["ln"])])
                stmts.append(ir_if(ir_not(ir_name(ok)), hit, alt["ln"]))
            stmts.append(ir_if(ir_name(ok), inner, ln))
            return stmts
        if t == "MSeq":
            return self.seq_pattern(p["pats"], subj, inner, ln)
        if t == "MMap":
            return self.map_pattern(p["keys"], p["pats"], p["rest"], subj,
                                    inner, ln)
        if t == "MCls":
            if p["pos"]:
                raise self.err(
                    "positional class patterns are not supported; "
                    "use keyword patterns (e.g. case Point(x=a, y=b))", ln)
            chain = inner
            attrs = p["attrs"]
            kpats = p["kpats"]
            j = len(attrs) - 1
            while j >= 0:
                attr = attrs[j]
                kp = kpats[j]
                sub = {"op": "attr", "o": subj, "n": attr}
                chain = [ir_if(
                    ir_call("hasattr",
                            [subj, {"op": "const", "t": "str", "v": attr}]),
                    self.pattern(kp, sub, chain), kp["ln"])]
                j -= 1
            return [ir_if(ir_call("isinstance", [subj, self.expr(p["cls"])]),
                          chain, ln)]
        raise self.err("unsupported match pattern: " + t, ln)

    def seq_pattern(self, pats, subj, inner, ln):
        star = None
        for i, q in enumerate(pats):
            if q["t"] == "MStar":
                star = i
                break
        n = len(pats)
        chain = inner
        order = []
        for i, q in enumerate(pats):
            if q["t"] == "MStar":
                after = n - i - 1
                upper = None if after == 0 else ir_int(-after)
                sl = {"op": "slicelit", "lower": ir_int(i)}
                if upper:
                    sl["upper"] = upper
                elem = ir_call("list", [{"op": "index", "o": subj, "k": sl}])
                order.append((q, elem))
            else:
                if star is None or i < star:
                    idx = i
                else:
                    idx = i - n
                order.append((q, {"op": "index", "o": subj, "k": ir_int(idx)}))
        for pair in order[::-1]:
            q = pair[0]
            elem = pair[1]
            if q["t"] == "MStar":
                if q["name"] is not None:
                    chain = [self.bind(q["name"], elem, q["ln"])] + chain
            else:
                chain = self.pattern(q, elem, chain)
        length = ir_call("len", [subj])
        if star is None:
            len_test = ir_cmp("eq", length, ir_int(n))
        else:
            len_test = ir_cmp("ge", length, ir_int(n - 1))
        seq_test = ir_call("isinstance", [subj, {
            "op": "tuple", "elts": [ir_name("list"), ir_name("tuple")]}])
        return [ir_if(seq_test, [ir_if(len_test, chain, ln)], ln)]

    def map_pattern(self, keys, pats, rest, subj, inner, ln):
        key_exprs = [self.expr(k) for k in keys]
        chain = inner
        if rest is not None:
            copy = [self.bind(rest, ir_call("dict", [subj]), ln)]
            for ke in key_exprs:
                copy.append({"op": "del", "targets": [
                    {"op": "index", "o": ir_name(rest), "k": ke}], "ln": ln})
            chain = copy + chain
        j = len(key_exprs) - 1
        while j >= 0:
            ke = key_exprs[j]
            kp = pats[j]
            sub = {"op": "index", "o": subj, "k": ke}
            chain = [ir_if(ir_cmp("in", ke, subj),
                           self.pattern(kp, sub, chain), kp["ln"])]
            j -= 1
        return [ir_if(ir_call("isinstance", [subj, ir_name("dict")]), chain, ln)]

    def set_(self, hidden, value, ln):
        return {"op": "assign", "targets": [ir_name(hidden)], "v": value, "ln": ln}

    def bind(self, user_name, value, ln):
        return {"op": "assign", "targets": [ir_name(user_name)], "v": value,
                "ln": ln}


def try_read(path):
    try:
        f = open(path)
        s = f.read()
        f.close()
        return s
    except OSError:
        return None


def fexists(path):
    try:
        f = open(path)
        f.close()
        return True
    except OSError:
        return False


def find_cwd_prefix(path, content):
    """Find the host cwd prefix by matching relative paths against file content."""
    if not path.startswith("/"):
        return None
    parts = path.split("/")
    k = len(parts) - 1
    while k >= 1:
        cand = "/".join(parts[k:])
        s2 = try_read(cand)
        if s2 is not None and s2 == content:
            return "/".join(parts[:k])
        k -= 1
    return None


def rel_file(path, prefix):
    if prefix is None or not path.startswith(prefix + "/"):
        return path
    return path[len(prefix) + 1:]


def path_join(root, name):
    if root:
        return root.rstrip("/") + "/" + name
    return name


def module_exports(assigned, compiled):
    """Return literal __all__ exports, or sorted public top-level names."""
    for st in compiled["body"]:
        if st["op"] == "assign" and len(st["targets"]) == 1 \
                and st["targets"][0] == {"op": "name", "id": "__all__"} \
                and st["v"]["op"] == "list":
            ok = True
            for e in st["v"]["elts"]:
                if not (e["op"] == "const" and e.get("t") == "str"):
                    ok = False
                    break
            if ok:
                return [e["v"] for e in st["v"]["elts"]]
    return sorted([n for n in assigned if not n.startswith("_")])


class Bundler:
    def __init__(self, roots, prefix, debug, stdlib_root=None):
        self.roots = roots
        if stdlib_root is not None:
            self.roots = roots + [stdlib_root]
        self.stdlib_root = stdlib_root
        self.prefix = prefix
        self.debug = debug
        self.modules = {}
        self.exports = {}
        self.in_progress = set()
        self.unresolved = {}  # Map dotted module names to importing files.

    def resolve_path(self, name):
        """Resolve a module to (path, is_package), preferring packages."""
        for root in self.roots:
            base = path_join(root, "/".join(name.split(".")))
            if fexists(base + "/__init__.py"):
                return (base + "/__init__.py", True)
            if fexists(base + ".py"):
                return (base + ".py", False)
        return None

    def is_module(self, name):
        return self.resolve_path(name) is not None

    def star_resolver(self, target, mc, ln):
        if target in NATIVE_EXPORTS:
            return list(NATIVE_EXPORTS[target])
        if target in NATIVE_MODULES:
            raise mc.err("'import *' from native module '" + target
                         + "' is not supported", ln)
        if target in self.in_progress:
            raise mc.err("'import *' from '" + target
                         + "' within an import cycle", ln)
        if not self.ensure(target):
            raise mc.err("cannot resolve import '" + target + "' for 'import *'",
                         ln)
        return self.exports[target]

    def ensure(self, name):
        if name in self.modules or name in self.in_progress:
            return True
        rp = self.resolve_path(name)
        if rp is None:
            return False
        if "." in name:
            parent = name.rsplit(".", 1)[0]
            if not self.ensure(parent):
                raise CompileError("package '" + parent + "' not found for "
                                   "module '" + name + "'", rp[0], None)
        self.compile_one(name, rp[0], rp[1])
        return True

    def compile_one(self, name, path, is_package):
        source = try_read(path)
        if source is None:
            raise CompileError("cannot read module file", path, None)
        if is_package:
            pkg_ctx = name
        elif "." in name:
            pkg_ctx = name.rsplit(".", 1)[0]
        else:
            pkg_ctx = ""
        if self.stdlib_root is not None and path.startswith(self.stdlib_root + "/"):
            file = STDLIB_FILE_PREFIX + path[len(self.stdlib_root) + 1:]
        else:
            if self.prefix is None or not path.startswith(self.prefix + "/"):
                prefix = find_cwd_prefix(path, source)
                if prefix is not None:
                    self.prefix = prefix
            file = rel_file(path, self.prefix)
        mc = ModuleCompiler(name, file, source, pkg_ctx, self)
        self.in_progress.add(name)
        try:
            compiled = mc.compile()
        finally:
            self.in_progress.discard(name)
        self.modules[name] = compiled
        if self.debug:
            compiled["src"] = source.splitlines()
        col = Col()
        for st in mc.tree_body:
            col.stmt(st)
        self.exports[name] = module_exports(col.assigned, compiled)
        for dep in sorted(mc.imports):
            if dep in self.modules or dep in self.in_progress:
                continue
            if self.ensure(dep):
                continue
            if dep.split(".")[0] in NATIVE_MODULES and "." not in dep:
                continue
            # Defer ImportError until execution to preserve optional and lazy imports.
            self.unresolved.setdefault(dep, mc.file)


# Omit default-valued fields; readers restore their defaults (spec 2.2).
OMIT_DEFAULTS = [
    ("orelse", []), ("finalbody", []), ("handlers", []), ("kw", []),
    ("args", []), ("elts", []), ("entries", []), ("bases", []),
    ("gdecl", []), ("ndecl", []), ("locals", []), ("gen", False), ("coro", False),
    ("defaults", []), ("kwonly", []), ("kwdefaults", []),
    ("vararg", None), ("kwarg", None), ("conv", None), ("spec", None),
    ("t", None), ("name", None),
]


def compact_strip(node):
    if isinstance(node, list):
        for x in node:
            compact_strip(x)
        return
    if not isinstance(node, dict):
        return
    is_const = node.get("op") == "const"
    for key, default in OMIT_DEFAULTS:
        if key in node and node[key] == default:
            if is_const and key == "t":
                continue  # Constants require their type discriminator.
            del node[key]
    for value in node.values():
        compact_strip(value)


def compact_bundle(bundle):
    """Remove default-valued fields in place without changing bundle semantics."""
    for mod in bundle["modules"].values():
        compact_strip(mod["body"])
    return bundle


def compile_bundle(entry_path, debug, search_paths, stdlib_root=None):
    slash = entry_path.rfind("/")
    if slash >= 0:
        root = entry_path[:slash]
        base = entry_path[slash + 1:]
    else:
        root = ""
        base = entry_path
    dot = base.rfind(".")
    if dot > 0:
        entry_name = base[:dot]
    else:
        entry_name = base

    entry_src = try_read(entry_path)
    if entry_src is None:
        raise CompileError("entry file not found", entry_path, None)
    prefix = find_cwd_prefix(entry_path, entry_src)

    b = Bundler([root] + search_paths, prefix, debug, stdlib_root)
    b.compile_one(entry_name, entry_path, False)
    ordered = {}
    ordered[entry_name] = b.modules[entry_name]
    for name in sorted([k for k in b.modules if k != entry_name]):
        ordered[name] = b.modules[name]
    return compact_bundle({"diaspore": DIASPORE_VERSION, "main": entry_name,
                           "modules": ordered})


def main():
    args = []
    debug = False
    search_paths = []
    stdlib_root = None
    pending = None
    for a in sys.argv[1:]:
        if pending == "--path":
            search_paths.append(a.rstrip("/") or "/")
            pending = None
        elif pending == "--stdlib":
            stdlib_root = a.rstrip("/") or "/"
            pending = None
        elif a == "--path" or a == "--stdlib":
            pending = a
        elif a == "--debug":
            debug = True
        else:
            args.append(a)
    if len(args) != 1 or pending is not None:
        sys.stderr_write("usage: dspc [--debug] [--path DIR ...] [--stdlib DIR] entry.py\n")
        exit(2)
    try:
        bundle = compile_bundle(args[0], debug, search_paths, stdlib_root)
    except CompileError as e:
        if e.line:
            loc = e.file + ":" + str(e.line)
        else:
            loc = e.file
        sys.stderr_write("diaspore: compile error: " + loc + ": " + e.msg + "\n")
        exit(2)
    print(json.dumps(bundle))


main()
