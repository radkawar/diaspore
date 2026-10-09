"""Render parser expressions with CPython 3.12 ast.unparse normalization."""


_BINOPS = {
    "bitor": ("|", 9), "bitxor": ("^", 10), "bitand": ("&", 11),
    "lshift": ("<<", 12), "rshift": (">>", 12),
    "add": ("+", 13), "sub": ("-", 13),
    "mul": ("*", 14), "matmul": ("@", 14), "div": ("/", 14),
    "mod": ("%", 14), "floordiv": ("//", 14), "pow": ("**", 16),
}
_UNARY = {"not": ("not ", 7), "neg": ("-", 15),
          "pos": ("+", 15), "invert": ("~", 15)}
_COMPARE = {"eq": "==", "ne": "!=", "lt": "<", "le": "<=", "gt": ">",
            "ge": ">=", "in": "in", "notin": "not in", "is": "is", "isnot": "is not"}
_QUOTES = ["'", '"', '"""', "'''"]


def _parenthesize(text, precedence, required):
    if precedence < required:
        return "(" + text + ")"
    return text


def _items(elts):
    text = ", ".join([_expr(e, 4) for e in elts])
    if len(elts) == 1:
        text += ","
    return text


def _parameters(params):
    parts = []
    first_default = len(params["args"]) - len(params["defaults"])
    for index, name in enumerate(params["args"]):
        text = name
        if index >= first_default:
            text += "=" + _expr(params["defaults"][index - first_default], 4)
        parts.append(text)
        if index + 1 == params["posonly"]:
            parts.append("/")
    if params["vararg"] is not None:
        parts.append("*" + params["vararg"])
    elif params["kwonly"]:
        parts.append("*")
    for index, name in enumerate(params["kwonly"]):
        text = name
        default = params["kwdefaults"][index]
        if default is not None:
            text += "=" + _expr(default, 4)
        parts.append(text)
    if params["kwarg"] is not None:
        parts.append("**" + params["kwarg"])
    return ", ".join(parts)


def _literal_quotes(text, quotes):
    # repr of one character supplies Python's non-printable/backslash escaping
    # without requiring the unicode_escape codec in the compiler runtime.
    escaped = "".join([c if c in "'\"" else repr(c)[1:-1] for c in text])
    possible = [q for q in quotes if q not in escaped]
    if not possible:
        value = repr(text)
        quote = value[0]
        for candidate in quotes:
            if value[0] in candidate:
                quote = candidate
                break
        return value[1:-1], [quote]
    if escaped:
        possible.sort(key=lambda q: q[0] == escaped[-1])
        if possible[0][0] == escaped[-1]:
            escaped = escaped[:-1] + "\\" + escaped[-1]
    return escaped, possible


def _formatted(part):
    text = _expr(part["v"], 5)
    if text.startswith("{"):
        text = " " + text
    if part["conv"] is not None:
        text += "!" + part["conv"]
    if part["spec"] is not None:
        text += ":" + _format_spec(part["spec"])
    return "{" + text + "}"


def _format_spec(node):
    parts = []
    for part in node["parts"]:
        if "lit" in part:
            text = part["lit"].replace("{", "{{").replace("}", "}}")
            text = text.replace("\\", "\\\\").replace("'", "\\'")
            parts.append(text.replace('"', '\\"').replace("\n", "\\n"))
        else:
            parts.append(_formatted(part))
    return "".join(parts)


def _fstring(node):
    parts = []
    for part in node["parts"]:
        if "lit" in part:
            parts.append((part["lit"].replace("{", "{{").replace("}", "}}"), True))
        else:
            parts.append((_formatted(part), False))
    quotes = _QUOTES[:]
    rendered = []
    fallback = False
    for text, literal in parts:
        if literal:
            text, possible = _literal_quotes(text, quotes)
            if not any(q in quotes for q in possible):
                fallback = True
                break
            quotes = possible
        elif "\n" in text:
            quotes = [q for q in quotes if len(q) == 3]
        rendered.append(text)
    if fallback:
        quotes = ["'''"]
        rendered = []
        for text, literal in parts:
            if literal:
                text = repr('"' + text)[2:-1]
            rendered.append(text)
    return "f" + quotes[0] + "".join(rendered) + quotes[0]


def _expr(node, required):
    tag = node["t"]
    if tag == "Name":
        return node["id"]
    if tag == "Const":
        kind = node["kind"]
        if kind == "ellipsis":
            return "..."
        value = node["v"]
        if kind == "complex":
            value = complex(0, value)
        text = repr(value)
        if kind in ("float", "complex"):
            text = text.replace("inf", "1e309").replace("nan", "(1e309-1e309)")
        if node.get("prefix") == "u":
            text = "u" + text
        return text
    if tag == "Attr":
        obj = node["o"]
        text = _expr(obj, 18)
        if obj["t"] == "Const" and obj["kind"] in ("int", "bool"):
            text += " "
        return text + "." + node["n"]
    if tag == "Sub":
        key = node["k"]
        if key["t"] == "Tuple" and key["elts"]:
            text = _items(key["elts"])
        else:
            text = _expr(key, 4)
        return _expr(node["o"], 18) + "[" + text + "]"
    if tag == "Slice":
        lower = "" if node["lower"] is None else _expr(node["lower"], 4)
        upper = "" if node["upper"] is None else _expr(node["upper"], 4)
        text = lower + ":" + upper
        if node["step"] is not None:
            text += ":" + _expr(node["step"], 4)
        return text
    if tag == "Starred":
        return "*" + _expr(node["v"], 9)
    if tag == "Tuple":
        if not node["elts"]:
            return "()"
        return _parenthesize(_items(node["elts"]), 2, required)
    if tag in ("List", "Set"):
        text = ", ".join([_expr(e, 4) for e in node["elts"]])
        if tag == "List":
            return "[" + text + "]"
        return "{" + text + "}" if text else "{*()}"
    if tag == "Dict":
        parts = []
        for item in node["entries"]:
            if item["k"] is None:
                parts.append("**" + _expr(item["v"], 9))
            else:
                parts.append(_expr(item["k"], 4) + ": " + _expr(item["v"], 4))
        return "{" + ", ".join(parts) + "}"
    if tag == "BinOp":
        operator, level = _BINOPS[node["kind"]]
        left = level + 1 if node["kind"] == "pow" else level
        right = level if node["kind"] == "pow" else level + 1
        text = _expr(node["l"], left) + " " + operator + " " + _expr(node["r"], right)
        return _parenthesize(text, level, required)
    if tag == "Unary":
        operator, level = _UNARY[node["kind"]]
        return _parenthesize(operator + _expr(node["v"], level), level, required)
    if tag == "Bool":
        level = 5 if node["kind"] == "or" else 6
        parts = []
        # CPython increases the required precedence for each operand, including
        # later operands of a flattened BoolOp rather than just its first child.
        for index, value in enumerate(node["values"]):
            parts.append(_expr(value, min(level + index + 1, 18)))
        return _parenthesize((" " + node["kind"] + " ").join(parts), level, required)
    if tag == "Cmp":
        parts = [_expr(node["left"], 9)]
        for index, kind in enumerate(node["ops"]):
            parts.append(_COMPARE[kind])
            parts.append(_expr(node["comps"][index], 9))
        return _parenthesize(" ".join(parts), 8, required)
    if tag == "Cond":
        text = (_expr(node["then"], 5) + " if " + _expr(node["test"], 5)
                + " else " + _expr(node["else"], 4))
        return _parenthesize(text, 4, required)
    if tag == "Call":
        parts = [_expr(arg, 4) for arg in node["args"]]
        for kw in node["kw"]:
            prefix = "**" if kw["n"] is None else kw["n"] + "="
            parts.append(prefix + _expr(kw["v"], 4))
        return _expr(node["f"], 18) + "(" + ", ".join(parts) + ")"
    if tag == "Named":
        text = _expr(node["target"], 18) + " := " + _expr(node["v"], 18)
        return _parenthesize(text, 1, required)
    if tag == "Lambda":
        params = _parameters(node["params"])
        text = "lambda" + (" " + params if params else "") + ": " + _expr(node["body"], 4)
        return _parenthesize(text, 4, required)
    if tag == "Fstr":
        return _fstring(node)
    if tag == "Comp":
        kind = node["kind"]
        if kind == "dict":
            text = _expr(node["key"], 4) + ": " + _expr(node["val"], 4)
        else:
            text = _expr(node["elt"], 4)
        for gen in node["gens"]:
            text += " async for " if gen.get("async", 0) else " for "
            text += _expr(gen["target"], 2) + " in " + _expr(gen["iter"], 5)
            for cond in gen["ifs"]:
                text += " if " + _expr(cond, 5)
        if kind == "list":
            return "[" + text + "]"
        if kind == "gen":
            return "(" + text + ")"
        return "{" + text + "}"
    if tag in ("Await", "Yield", "YieldFrom"):
        text = {"Await": "await", "Yield": "yield", "YieldFrom": "yield from"}[tag]
        if node["v"] is not None:
            text += " " + _expr(node["v"], 18)
        return _parenthesize(text, 17 if tag == "Await" else 3, required)
    raise ValueError("unsupported annotation expression: " + tag)


def unparse_annotation(node):
    """Return the normalized expression text used for postponed annotations."""
    return _expr(node, 4)
