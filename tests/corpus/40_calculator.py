"""An interpreter running inside the interpreter: a small expression language
(lexer + recursive-descent parser + evaluator with closures) written in the
diaspore subset."""


def tokenize(src):
    tokens = []
    i = 0
    while i < len(src):
        ch = src[i]
        if ch.isspace():
            i += 1
        elif ch.isdigit() or (ch == "." and i + 1 < len(src) and src[i + 1].isdigit()):
            j = i
            while j < len(src) and (src[j].isdigit() or src[j] == "."):
                j += 1
            text = src[i:j]
            tokens.append(("num", float(text) if "." in text else int(text)))
            i = j
        elif ch.isalpha() or ch == "_":
            j = i
            while j < len(src) and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            tokens.append(("kw", word) if word in ("fn", "let") else ("name", word))
            i = j
        elif src[i:i + 2] == "**":
            tokens.append(("op", "**"))
            i += 2
        elif ch in "+-*/%()=,":
            tokens.append(("op", ch))
            i += 1
        else:
            raise ValueError(f"unexpected character {ch!r}")
    tokens.append(("eof", None))
    return tokens


class Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.pos = 0

    def peek(self):
        return self.toks[self.pos]

    def next(self):
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def expect(self, kind, value=None):
        t = self.next()
        if t[0] != kind or (value is not None and t[1] != value):
            raise ValueError(f"expected {value or kind}, got {t[1]!r}")
        return t

    def parse_stmt(self):
        kind, val = self.peek()
        if (kind, val) == ("kw", "let"):
            self.next()
            name = self.expect("name")[1]
            self.expect("op", "=")
            return ("let", name, self.parse_expr())
        if (kind, val) == ("kw", "fn"):
            self.next()
            name = self.expect("name")[1]
            self.expect("op", "(")
            params = []
            while self.peek() != ("op", ")"):
                params.append(self.expect("name")[1])
                if self.peek() == ("op", ","):
                    self.next()
            self.next()
            self.expect("op", "=")
            return ("fn", name, params, self.parse_expr())
        return ("expr", self.parse_expr())

    def parse_expr(self):
        node = self.parse_term()
        while self.peek()[0] == "op" and self.peek()[1] in "+-":
            op = self.next()[1]
            node = ("bin", op, node, self.parse_term())
        return node

    def parse_term(self):
        node = self.parse_power()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/", "%"):
            op = self.next()[1]
            node = ("bin", op, node, self.parse_power())
        return node

    def parse_power(self):
        node = self.parse_atom()
        if self.peek() == ("op", "**"):
            self.next()
            return ("bin", "**", node, self.parse_power())
        return node

    def parse_atom(self):
        kind, val = self.next()
        if kind == "num":
            return ("num", val)
        if kind == "op" and val == "-":
            return ("neg", self.parse_atom())
        if kind == "op" and val == "(":
            inner = self.parse_expr()
            self.expect("op", ")")
            return inner
        if kind == "name":
            if self.peek() == ("op", "("):
                self.next()
                args = []
                while self.peek() != ("op", ")"):
                    args.append(self.parse_expr())
                    if self.peek() == ("op", ","):
                        self.next()
                self.next()
                return ("call", val, args)
            return ("var", val)
        raise ValueError(f"unexpected token {val!r}")


def evaluate(node, env):
    match node:
        case ("num", v):
            return v
        case ("neg", inner):
            return -evaluate(inner, env)
        case ("var", name):
            scope = env
            while scope is not None:
                if name in scope[0]:
                    return scope[0][name]
                scope = scope[1]
            raise ValueError(f"undefined variable {name}")
        case ("bin", op, l, r):
            a, b = evaluate(l, env), evaluate(r, env)
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                return a / b
            if op == "%":
                return a % b
            return a ** b
        case ("call", name, args):
            scope = env
            while scope is not None:
                if name in scope[0]:
                    params, body, closure = scope[0][name]
                    if len(args) != len(params):
                        raise ValueError(f"{name} expects {len(params)} args")
                    local = {}
                    for p, arg in zip(params, args):
                        local[p] = evaluate(arg, env)
                    return evaluate(body, (local, closure))
                scope = scope[1]
            raise ValueError(f"undefined function {name}")
    raise ValueError("bad node")


def run(program):
    env = ({}, None)
    for line in program:
        try:
            stmt = Parser(tokenize(line)).parse_stmt()
            match stmt:
                case ("let", name, expr):
                    env[0][name] = evaluate(expr, env)
                    print(f"{name} = {env[0][name]}")
                case ("fn", name, params, body):
                    env[0][name] = (params, body, env)
                    print(f"fn {name}({', '.join(params)})")
                case ("expr", expr):
                    print(evaluate(expr, env))
        except ValueError as e:
            print(f"error: {e}")


run([
    "1 + 2 * 3",
    "(1 + 2) * 3",
    "2 ** 3 ** 2",
    "-4 + 10 % 3",
    "let x = 10",
    "let y = x * 2 + 1",
    "x + y",
    "fn double(n) = n * 2",
    "fn hyp(a, b) = (a ** 2 + b ** 2) ** 0.5",
    "double(21)",
    "hyp(3, 4)",
    "double(double(x)) + hyp(6, 8)",
    "fn addx(n) = n + x",
    "addx(5)",
    "let x = 100",
    "addx(5)",
    "7 / 2",
    "1 +",
    "oops(1)",
    "let z = q + 1",
    "3 $ 4",
])
