"""Differential fuzzing: generate random, deterministic diaspore-subset programs.

Generated programs compare stdout and caught exception messages across CPython
and the Diaspore runtimes. Loops are bounded and avoid nondeterministic inputs.
Integer assignments are reduced modulo a prime to keep products within int64.
Formatting uses expressions shared by all runtimes.
"""

from __future__ import annotations

import random

BOUND = 100003  # prime; keeps int vars < 2**17 so depth-3 products fit int64

STR_POOL = ['"diaspore"', '"abc"', '"Hello, World"', '"x,y,z"', '""', '"padded  "',
            '"MiXeD case"', '"12345"']
STR_METHOD_POOL = [".upper()", ".lower()", ".strip()", ".title()",
                   '.replace("a", "_")', '.split(",")', ".swapcase()",
                   '.count("l")', '.find("o")', ".capitalize()"]


class Gen:
    def __init__(self, seed: int):
        self.r = random.Random(seed)
        self.seed = seed
        self.vars: dict[str, str] = {}   # name -> kind
        self.funcs: list[tuple[str, int]] = []  # name, arity
        self.lines: list[str] = []
        self.frozen: set[str] = set()  # lists being iterated: no growth
        self.tmp = 0

    def fresh(self, kind: str) -> str:
        self.tmp += 1
        name = f"{kind[0]}{self.tmp}"
        self.vars[name] = kind
        return name

    def pick_var(self, kind: str) -> str | None:
        names = [n for n, k in self.vars.items() if k == kind]
        return self.r.choice(names) if names else None

    def emit(self, line: str, indent: int = 0) -> None:
        self.lines.append("    " * indent + line)

    def int_expr(self, depth: int = 0) -> str:
        r = self.r
        choices = ["lit", "lit"]
        if self.pick_var("int"):
            choices += ["var", "var"]
        if depth < 3:
            choices += ["bin", "bin", "len", "unary", "cond"]
        match r.choice(choices):
            case "lit":
                return str(r.randint(0, 60))
            case "var":
                return self.pick_var("int")
            case "len":
                return f"len({self.str_expr(3)})"
            case "unary":
                return f"(-{self.int_expr(depth + 1)})"
            case "cond":
                return (f"({self.int_expr(depth + 1)} if {self.bool_expr(depth + 1)}"
                        f" else {self.int_expr(depth + 1)})")
            case _:
                op = r.choice(["+", "-", "*", "//", "%", "+", "-"])
                lhs = self.int_expr(depth + 1)
                rhs = (str(r.randint(1, 19)) if op in ("//", "%")
                       else self.int_expr(depth + 1))
                return f"({lhs} {op} {rhs})"

    def float_expr(self, depth: int = 0) -> str:
        r = self.r
        choices = ["lit"]
        if self.pick_var("float"):
            choices += ["var", "var"]
        if depth < 2:
            choices += ["bin", "div", "round"]
        match r.choice(choices):
            case "lit":
                return r.choice(["0.5", "2.25", "1.5", "0.1", "3.75", "10.0"])
            case "var":
                return self.pick_var("float")
            case "div":
                return f"({self.int_expr(2)} / {r.randint(1, 9)})"
            case "round":
                return f"round({self.float_expr(depth + 1)}, {r.randint(0, 2)})"
            case _:
                op = r.choice(["+", "-", "*"])
                return f"({self.float_expr(depth + 1)} {op} {self.float_expr(depth + 1)})"

    def str_expr(self, depth: int = 0) -> str:
        r = self.r
        choices = ["lit", "lit"]
        if self.pick_var("str"):
            choices += ["var", "var"]
        if depth < 3:
            choices += ["concat", "method", "fstr", "slice", "conv", "mul"]
        match r.choice(choices):
            case "lit":
                return r.choice(STR_POOL)
            case "var":
                return self.pick_var("str")
            case "concat":
                return f"({self.str_expr(depth + 1)} + {self.str_expr(depth + 1)})"
            case "mul":
                return f"({r.choice(STR_POOL)} * {r.randint(0, 3)})"
            case "method":
                base = self.str_expr(depth + 1)
                method = r.choice(STR_METHOD_POOL)
                if ".split" in method:
                    return f"str({base}{method})"
                if ".count" in method or ".find" in method:
                    return f"str({base}{method})"
                return f"{base}{method}"
            case "fstr":
                inner = r.choice([self.int_expr(2), self.float_expr(1),
                                  self.str_expr(depth + 1)])
                spec = r.choice(["", "", ":>10", ":<8", ""])
                return f'f"v={{{inner}{spec}}}!"'
            case "slice":
                lo, hi = r.randint(0, 3), r.randint(0, 8)
                step = r.choice(["", "", "::2", "::-1"])
                if step:
                    return f"{self.str_expr(depth + 1)}[{step}]"
                return f"{self.str_expr(depth + 1)}[{lo}:{hi}]"
            case _:
                return f"str({self.int_expr(2)})"

    def bool_expr(self, depth: int = 0) -> str:
        r = self.r
        if depth >= 3:
            return r.choice(["True", "False"])
        match r.choice(["cmp", "cmp", "cmp", "chain", "not", "andor", "in", "lit"]):
            case "cmp":
                op = r.choice(["==", "!=", "<", "<=", ">", ">="])
                kind = r.choice(["int", "float", "str"])
                e = {"int": self.int_expr, "float": self.float_expr,
                     "str": self.str_expr}[kind]
                return f"({e(depth + 1)} {op} {e(depth + 1)})"
            case "chain":
                return (f"({self.int_expr(depth + 1)} < {self.int_expr(depth + 1)}"
                        f" <= {self.int_expr(depth + 1)})")
            case "not":
                return f"(not {self.bool_expr(depth + 1)})"
            case "andor":
                op = self.r.choice(["and", "or"])
                return f"({self.bool_expr(depth + 1)} {op} {self.bool_expr(depth + 1)})"
            case "in":
                return f'({self.str_expr(depth + 1)} in {self.str_expr(depth + 1)})'
            case _:
                return r.choice(["True", "False"])

    def list_expr(self, depth: int = 0) -> str:
        r = self.r
        choices = ["lit"]
        if self.pick_var("list"):
            choices += ["var", "var"]
        if depth < 2:
            choices += ["comp", "sorted", "slice", "concat"]
        match r.choice(choices):
            case "lit":
                n = r.randint(0, 5)
                return "[" + ", ".join(self.int_expr(2) for _ in range(n)) + "]"
            case "var":
                return self.pick_var("list")
            case "comp":
                src = f"range({r.randint(1, 7)})"
                body = r.choice([f"_c * {r.randint(1, 5)}", "_c + 1", "_c * _c"])
                cond = r.choice(["", f" if _c % {r.randint(2, 3)} == 0",
                                 f" if _c > {r.randint(0, 3)}"])
                return f"[{body} for _c in {src}{cond}]"
            case "sorted":
                rev = r.choice(["", ", reverse=True"])
                return f"sorted({self.list_expr(depth + 1)}{rev})"
            case "slice":
                return f"{self.list_expr(depth + 1)}[{r.randint(0, 2)}:{r.randint(2, 6)}]"
            case _:
                return f"({self.list_expr(depth + 1)} + {self.list_expr(depth + 1)})"

    def any_expr(self) -> str:
        kind = self.r.choice(["int", "float", "str", "bool", "list"])
        return {"int": self.int_expr, "float": self.float_expr, "str": self.str_expr,
                "bool": self.bool_expr, "list": self.list_expr}[kind]()

    def stmt_assign(self, indent: int) -> None:
        kind = self.r.choice(["int", "int", "float", "str", "bool", "list"])
        # Generate the RHS before registering its target to avoid unbound names.
        expr = {"int": lambda: f"({self.int_expr()}) % {BOUND}",
                "float": self.float_expr, "str": self.str_expr,
                "bool": self.bool_expr, "list": self.list_expr}[kind]()
        existing = self.pick_var(kind)
        name = existing if (existing and self.r.random() < 0.4) else self.fresh(kind)
        self.emit(f"{name} = {expr}", indent)

    def stmt_aug(self, indent: int) -> None:
        v = self.pick_var("int")
        if v is None:
            return self.stmt_assign(indent)
        op = self.r.choice(["+=", "-=", "//=", "%="])
        rhs = str(self.r.randint(1, 30)) if op in ("//=", "%=") else self.int_expr(2)
        self.emit(f"{v} {op} {rhs}", indent)
        self.emit(f"{v} %= {BOUND}", indent)

    def stmt_print(self, indent: int) -> None:
        if self.r.random() < 0.3:
            self.emit("try:", indent)
            self.emit(f"print({self.any_expr()})", indent + 1)
            self.emit("except Exception as _e:", indent)
            self.emit('print("E:", _e)', indent + 1)
        else:
            args = ", ".join(self.any_expr() for _ in range(self.r.randint(1, 3)))
            self.emit(f"print({args})", indent)

    def stmt_if(self, indent: int, budget: int) -> None:
        self.emit(f"if {self.bool_expr()}:", indent)
        self.scoped_block(indent + 1, min(budget, 2))
        if self.r.random() < 0.6:
            self.emit("else:", indent)
            self.scoped_block(indent + 1, min(budget, 2))

    def stmt_for(self, indent: int, budget: int) -> None:
        it = self.r.choice([f"range({self.r.randint(1, 6)})",
                            self.list_expr(1), self.str_expr(2)])
        v = self.fresh("int")
        self.vars[v] = "any"
        self.emit(f"{v} = 0  # stays bound even if the iterable is empty", indent)
        self.emit(f"for {v} in {it}:", indent)
        self.emit(f"print('loop', {v})", indent + 1)
        was_frozen = it in self.frozen
        if it in self.vars:
            self.frozen.add(it)  # iterating this list: body must not grow it
        try:
            self.scoped_block(indent + 1, min(budget, 1))
        finally:
            if it in self.vars and not was_frozen:
                self.frozen.discard(it)

    def stmt_while(self, indent: int, budget: int) -> None:
        # Keep the loop counter private so generated statements cannot reset it.
        self.tmp += 1
        v = f"w{self.tmp}"
        self.emit(f"{v} = 0", indent)
        self.emit(f"while {v} < {self.r.randint(1, 5)}:", indent)
        self.emit(f"{v} += 1", indent + 1)
        self.scoped_block(indent + 1, min(budget, 1))

    def stmt_def(self, indent: int) -> None:
        name = f"fn{len(self.funcs)}"
        arity = self.r.randint(1, 2)
        params = ", ".join(f"p{i}" for i in range(arity))
        saved = dict(self.vars)
        for i in range(arity):
            self.vars[f"p{i}"] = "int"
        self.emit(f"def {name}({params}):", indent)
        self.emit(f"return ({self.int_expr()}) % {BOUND}", indent + 1)
        self.vars = saved
        self.funcs.append((name, arity))

    def stmt_call(self, indent: int) -> None:
        if not self.funcs:
            return self.stmt_print(indent)
        name, arity = self.r.choice(self.funcs)
        args = ", ".join(self.int_expr(2) for _ in range(arity))
        self.emit(f"print({name}({args}))", indent)

    def stmt_try(self, indent: int) -> None:
        r = self.r
        risky = r.choice([
            f"print(({self.int_expr(2)}) // ({self.int_expr(2)} - {self.int_expr(2)}))",
            f"print({self.list_expr(1)}[{r.randint(0, 9)}])",
            f"print(int({self.str_expr(2)}))",
            f"print({self.int_expr(2)} % ({self.int_expr(2)} - {self.int_expr(2)}))",
        ])
        self.emit("try:", indent)
        self.emit(risky, indent + 1)
        if r.random() < 0.5:
            self.emit("except (ZeroDivisionError, IndexError, ValueError) as _x:", indent)
        else:
            self.emit("except Exception as _x:", indent)
        self.emit('print("caught:", _x)', indent + 1)
        if r.random() < 0.4:
            self.emit("finally:", indent)
            self.emit('print("fin")', indent + 1)

    def stmt_class(self, indent: int) -> None:
        cname = f"C{len(self.funcs)}{self.tmp}"
        self.tmp += 1
        self.emit(f"class {cname}:", indent)
        self.emit("def __init__(self, v):", indent + 1)
        self.emit("self.v = v % 1000", indent + 2)
        self.emit("def calc(self, w):", indent + 1)
        saved = dict(self.vars)
        self.vars["w"] = "int"
        body2 = self.int_expr(2)
        self.vars = saved
        self.emit(f"return (self.v + w + {body2}) % {BOUND}", indent + 2)
        self.emit("def __repr__(self):", indent + 1)
        self.emit(f'return f"{cname}({{self.v}})"', indent + 2)
        obj = self.fresh("int")
        self.vars[obj] = "any"
        self.emit(f"{obj} = {cname}({self.int_expr(2)})", indent)
        self.emit(f"print({obj}, {obj}.calc({self.r.randint(0, 50)}))", indent)

    def stmt_gen(self, indent: int) -> None:
        gname = f"g{self.tmp}"
        self.tmp += 1
        n = self.r.randint(1, 6)
        self.emit(f"def {gname}():", indent)
        self.emit(f"for _gi in range({n}):", indent + 1)
        self.emit(f"yield (_gi * {self.r.randint(1, 9)} + {self.r.randint(0, 5)})",
                  indent + 2)
        use = self.r.choice([f"print(list({gname}()))",
                             f"print(sum({gname}()))",
                             f"print(max({gname}(), default=-1))",
                             f"print([x for x in {gname}() if x % 2 == 0])"])
        self.emit(use, indent)

    def stmt_match(self, indent: int) -> None:
        r = self.r
        subject = r.choice([self.int_expr(2), self.str_expr(2), self.list_expr(1)])
        self.emit(f"match {subject}:", indent)
        self.emit(f"case {r.randint(0, 5)} | {r.randint(6, 12)}:", indent + 1)
        self.emit('print("m: small")', indent + 2)
        self.emit(f"case int() as _mi if _mi > {r.randint(10, 40)}:", indent + 1)
        self.emit('print("m: big", _mi)', indent + 2)
        self.emit("case str() as _ms:", indent + 1)
        self.emit('print("m: str", _ms)', indent + 2)
        self.emit("case [_mx, *_mrest]:", indent + 1)
        self.emit('print("m: seq", _mx, len(_mrest))', indent + 2)
        self.emit("case _:", indent + 1)
        self.emit('print("m: other")', indent + 2)

    def stmt_listop(self, indent: int) -> None:
        v = self.pick_var("list")
        if v is None:
            return self.stmt_assign(indent)
        if v in self.frozen:  # never grow a list that an enclosing for iterates
            self.emit(f"print(len({v}), sum({v}))", indent)
            return
        op = self.r.choice([f"{v}.append({self.int_expr(2)})",
                            f"{v}.reverse()", f"{v}.sort()",
                            f"print(len({v}), {v})",
                            f"print(sum({v}))"])
        self.emit(op, indent)

    def block(self, indent: int, budget: int) -> None:
        for _ in range(self.r.randint(1, max(1, budget))):
            self.stmt(indent, budget - 1)

    def scoped_block(self, indent: int, budget: int) -> None:
        """Names first bound inside a conditionally-executed block must not be
        referenced after it (the branch may not run)."""
        saved = dict(self.vars)
        self.block(indent, budget)
        self.vars = saved

    def stmt(self, indent: int, budget: int = 3) -> None:
        r = self.r.random()
        if r < 0.26:
            self.stmt_assign(indent)
        elif r < 0.38:
            self.stmt_print(indent)
        elif r < 0.46 and budget > 0:
            self.stmt_if(indent, budget)
        elif r < 0.53 and budget > 0 and indent < 2:
            self.stmt_for(indent, budget)
        elif r < 0.57 and budget > 0 and indent < 2:
            self.stmt_while(indent, budget)
        elif r < 0.63 and indent == 0:
            self.stmt_def(indent)
        elif r < 0.69:
            self.stmt_call(indent)
        elif r < 0.75:
            self.stmt_listop(indent)
        elif r < 0.80:
            self.stmt_aug(indent)
        elif r < 0.86 and indent < 2:
            self.stmt_try(indent)
        elif r < 0.90 and indent == 0:
            self.stmt_class(indent)
        elif r < 0.95 and indent == 0:
            self.stmt_gen(indent)
        elif indent < 2:
            self.stmt_match(indent)
        else:
            self.stmt_print(indent)

    def generate(self) -> str:
        self.emit(f"# fuzz seed {self.seed}")
        for _ in range(self.r.randint(18, 34)):
            self.stmt(0)
        self.emit('print("end")')
        return "\n".join(self.lines) + "\n"


def generate(seed: int) -> str:
    return Gen(seed).generate()


if __name__ == "__main__":
    import sys

    print(generate(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
