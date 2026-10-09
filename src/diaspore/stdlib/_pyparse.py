"""Tokenizer and parser shared by ast and the self-hosted compiler."""

KEYWORDS = {
    "False", "None", "True", "and", "as", "assert", "async", "await", "break",
    "class", "continue", "def", "del", "elif", "else", "except", "finally",
    "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal",
    "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
}

OPS3 = {"**=", "//=", ">>=", "<<=", "..."}
OPS2 = {"**", "//", ">>", "<<", "<=", ">=", "==", "!=", "->", "+=", "-=",
        "*=", "/=", "%=", "&=", "|=", "^=", "@=", ":="}
OPS1 = "+-*/%@&|^~<>()[]{},:.;="

AUGOPS = {"+=": "add", "-=": "sub", "*=": "mul", "/=": "div", "//=": "floordiv",
          "%=": "mod", "**=": "pow", "<<=": "lshift", ">>=": "rshift",
          "&=": "bitand", "|=": "bitor", "^=": "bitxor", "@=": "matmul"}

CMPOPS = {"<": "lt", ">": "gt", "==": "eq", ">=": "ge", "<=": "le", "!=": "ne"}

VALID_PREFIXES = {"", "r", "b", "u", "f", "rb", "br", "fr", "rf"}


def scan_digits(text, start, digits):
    index = start
    while index < len(text):
        if text[index] in digits:
            index += 1
        elif text[index] == "_" and index + 1 < len(text) and text[index + 1] in digits:
            index += 2
        else:
            break
    return index


def scan_number(text):
    prefix = text[:2].lower()
    if prefix in ("0x", "0o", "0b"):
        digits = {"0x": "0123456789abcdefABCDEF", "0o": "01234567", "0b": "01"}[prefix]
        end = scan_digits(text, 2, digits)
        if end > 2:
            return text[:end]
    end = scan_digits(text, 0, "0123456789")
    if end < len(text) and text[end] == ".":
        end += 1
        if end < len(text) and text[end] in "0123456789":
            end = scan_digits(text, end, "0123456789")
    if end < len(text) and text[end] in "eE":
        exponent = end + 1
        if exponent < len(text) and text[exponent] in "+-":
            exponent += 1
        if exponent < len(text) and text[exponent] in "0123456789":
            end = scan_digits(text, exponent, "0123456789")
    return text[:end]


class CompileError(Exception):
    def __init__(self, msg, file, line):
        self.msg = msg
        self.file = file
        self.line = line


def hexval(s, file, ln):
    v = 0
    for c in s:
        if c in "0123456789":
            v = v * 16 + int(c)
        elif c in "abcdef":
            v = v * 16 + (ord(c) - 87)
        elif c in "ABCDEF":
            v = v * 16 + (ord(c) - 55)
        else:
            raise CompileError("syntax error: invalid hex escape", file, ln)
    return v


def unescape_units(sv, is_bytes, file, ln):
    """Decode extracted string escapes into integer code points."""
    out = []
    i = 0
    n = len(sv)
    while i < n:
        c = sv[i]
        if c != "\\":
            cp = ord(c)
            if is_bytes and cp > 127:
                raise CompileError(
                    "syntax error: bytes can only contain ASCII literal characters",
                    file, ln)
            out.append(cp)
            i += 1
            continue
        i += 1
        if i >= n:
            raise CompileError("syntax error: trailing backslash in string", file, ln)
        e = sv[i]
        if e == "\n":
            i += 1
            continue
        if e == "n":
            out.append(10)
        elif e == "t":
            out.append(9)
        elif e == "r":
            out.append(13)
        elif e == "\\":
            out.append(92)
        elif e == "'":
            out.append(39)
        elif e == "\"":
            out.append(34)
        elif e == "a":
            out.append(7)
        elif e == "b":
            out.append(8)
        elif e == "f":
            out.append(12)
        elif e == "v":
            out.append(11)
        elif e in "01234567":
            j = i
            val = 0
            k = 0
            while j < n and k < 3 and sv[j] in "01234567":
                val = val * 8 + int(sv[j])
                j += 1
                k += 1
            if is_bytes and val > 255:
                raise CompileError("syntax error: bytes escape out of range", file, ln)
            out.append(val)
            i = j
            continue
        elif e == "x":
            hx = sv[i + 1:i + 3]
            if len(hx) < 2:
                raise CompileError("syntax error: truncated \\x escape", file, ln)
            out.append(hexval(hx, file, ln))
            i += 3
            continue
        elif e == "u" and not is_bytes:
            hx = sv[i + 1:i + 5]
            if len(hx) < 4:
                raise CompileError("syntax error: truncated \\u escape", file, ln)
            out.append(hexval(hx, file, ln))
            i += 5
            continue
        elif e == "U" and not is_bytes:
            hx = sv[i + 1:i + 9]
            if len(hx) < 8:
                raise CompileError("syntax error: truncated \\U escape", file, ln)
            out.append(hexval(hx, file, ln))
            i += 9
            continue
        elif e == "N" and not is_bytes:
            raise CompileError("syntax error: \\N escapes are not supported", file, ln)
        else:
            out.append(92)
            cp = ord(e)
            if is_bytes and cp > 127:
                raise CompileError(
                    "syntax error: bytes can only contain ASCII literal characters",
                    file, ln)
            out.append(cp)
        i += 1
    return out


def decode_str(sv, is_raw, file, ln):
    if is_raw:
        return sv
    units = unescape_units(sv, False, file, ln)
    return "".join([chr(u) for u in units])


def decode_bytes(sv, is_raw, file, ln):
    if is_raw:
        units = []
        for c in sv:
            cp = ord(c)
            if cp > 127:
                raise CompileError(
                    "syntax error: bytes can only contain ASCII literal characters",
                    file, ln)
            units.append(cp)
        return bytes(units)
    return bytes(unescape_units(sv, True, file, ln))


def clean_line(lines, li):
    line = lines[li]
    if line.endswith("\r"):
        line = line[:-1]
    return line


def scan_str(lines, li, col, quote, file):
    """Return raw string text and the position after its closing quote."""
    start_ln = li + 1
    triple = len(quote) == 3
    q = quote[0]
    out = []
    line = clean_line(lines, li)
    while True:
        if col >= len(line):
            if not triple:
                raise CompileError("syntax error: unterminated string literal",
                                   file, li + 1)
            li += 1
            if li >= len(lines):
                raise CompileError("syntax error: unterminated triple-quoted string",
                                   file, start_ln)
            out.append("\n")
            line = clean_line(lines, li)
            col = 0
            continue
        c = line[col]
        if c == "\\":
            if col + 1 >= len(line):
                out.append("\\")
                out.append("\n")
                li += 1
                if li >= len(lines):
                    raise CompileError("syntax error: unterminated string literal",
                                       file, start_ln)
                line = clean_line(lines, li)
                col = 0
                continue
            out.append("\\")
            out.append(line[col + 1])
            col += 2
            continue
        if c == q:
            if not triple:
                return ("".join(out), li, col + 1)
            if line[col:col + 3] == quote:
                return ("".join(out), li, col + 3)
            out.append(c)
            col += 1
            continue
        out.append(c)
        col += 1


def tokenize(src, file):
    lines = src.split("\n")
    nlines = len(lines)
    toks = []
    indents = [0]
    depth = 0
    li = 0
    had = False
    cont = False
    while li < nlines:
        line = clean_line(lines, li)
        col = 0
        if depth == 0 and not cont and not had:
            ind = 0
            while col < len(line):
                c = line[col]
                if c == " ":
                    ind += 1
                elif c == "\t":
                    ind = ind // 8 * 8 + 8
                else:
                    break
                col += 1
            if col == len(line) or line[col] == "#":
                li += 1
                continue
            if ind > indents[-1]:
                indents.append(ind)
                toks.append(("ind", "", li + 1))
            else:
                while ind < indents[-1]:
                    indents.pop()
                    toks.append(("ded", "", li + 1))
                if ind != indents[-1]:
                    raise CompileError(
                        "syntax error: unindent does not match any outer "
                        "indentation level", file, li + 1)
        cont = False
        while col < len(line):
            c = line[col]
            if c == " " or c == "\t":
                col += 1
                continue
            if c == "#":
                col = len(line)
                break
            if c == "\\" and col == len(line) - 1:
                cont = True
                col += 1
                break
            ln = li + 1
            seg = line[col:]
            if c == "'" or c == "\"" or c in "rRbBuUfF":
                prefix_end = 0
                while prefix_end < min(3, len(seg)) and seg[prefix_end] in "rRbBuUfF":
                    prefix_end += 1
                if prefix_end < len(seg) and seg[prefix_end] in "'\"":
                    prefix = seg[:prefix_end].lower()
                    quote = seg[prefix_end]
                    if seg[prefix_end:prefix_end + 3] == quote * 3:
                        quote = quote * 3
                    if prefix not in VALID_PREFIXES:
                        raise CompileError("syntax error: invalid string prefix",
                                           file, ln)
                    r = scan_str(lines, li, col + prefix_end + len(quote), quote, file)
                    raw = r[0]
                    is_raw = "r" in prefix
                    if "f" in prefix:
                        toks.append(("fstr", (raw, is_raw), ln))
                    elif "b" in prefix:
                        toks.append(("bytes", decode_bytes(raw, is_raw, file, ln), ln))
                    else:
                        toks.append(("str", decode_str(raw, is_raw, file, ln), ln,
                                     "u" if seg[:prefix_end] == "u" else None))
                    had = True
                    if r[1] != li:
                        li = r[1]
                        line = clean_line(lines, li)
                    col = r[2]
                    continue
            isnum = False
            if c in "0123456789":
                isnum = True
            elif c == "." and col + 1 < len(line) and line[col + 1] in "0123456789":
                isnum = True
            if isnum:
                text = scan_number(seg)
                col += len(text)
                if col < len(line) and line[col] in "jJ" \
                        and text[:2] not in ("0x", "0X", "0o", "0O", "0b", "0B"):
                    text = text + line[col]
                    col += 1
                toks.append(("num", text, ln))
                had = True
                continue
            word = None
            if c == "_" or c.isalpha():
                j = col + 1
                while j < len(line) and (line[j] == "_" or line[j].isalnum()):
                    j += 1
                word = line[col:j]
            if word is not None:
                col += len(word)
                if word in KEYWORDS:
                    toks.append(("kw", word, ln))
                else:
                    toks.append(("nm", word, ln))
                had = True
                continue
            op = None
            if len(seg) >= 3 and seg[:3] in OPS3:
                op = seg[:3]
            elif len(seg) >= 2 and seg[:2] in OPS2:
                op = seg[:2]
            elif c in OPS1:
                op = c
            if op is None:
                raise CompileError("syntax error: unexpected character " + c, file, ln)
            if op == "(" or op == "[" or op == "{":
                depth += 1
            elif op == ")" or op == "]" or op == "}":
                depth -= 1
                if depth < 0:
                    raise CompileError("syntax error: unmatched " + op, file, ln)
            toks.append(("op", op, ln))
            had = True
            col += len(op)
        if cont:
            li += 1
            continue
        if depth == 0 and had:
            toks.append(("nl", "", li + 1))
            had = False
        li += 1
    if had:
        toks.append(("nl", "", nlines))
    while len(indents) > 1:
        indents.pop()
        toks.append(("ded", "", nlines))
    toks.append(("end", "", nlines))
    return toks


def num_const(text, ln, file):
    if text.endswith("j") or text.endswith("J"):
        return {"t": "Const", "kind": "complex",
                "v": float(text[:-1].replace("_", "")), "ln": ln}
    p2 = text[:2]
    if p2 == "0x" or p2 == "0X":
        return {"t": "Const", "kind": "int",
                "v": int(text[2:].replace("_", ""), 16), "ln": ln}
    if p2 == "0o" or p2 == "0O":
        return {"t": "Const", "kind": "int",
                "v": int(text[2:].replace("_", ""), 8), "ln": ln}
    if p2 == "0b" or p2 == "0B":
        return {"t": "Const", "kind": "int",
                "v": int(text[2:].replace("_", ""), 2), "ln": ln}
    if "." in text or "e" in text or "E" in text:
        return {"t": "Const", "kind": "float",
                "v": float(text.replace("_", "")), "ln": ln}
    return {"t": "Const", "kind": "int", "v": int(text.replace("_", "")), "ln": ln}


def fix_lines(nd, delta):
    if isinstance(nd, dict):
        if "ln" in nd and isinstance(nd["ln"], int):
            nd["ln"] = nd["ln"] + delta
        for k in nd:
            fix_lines(nd[k], delta)
    elif isinstance(nd, list):
        for x in nd:
            fix_lines(x, delta)


class Parser:
    def __init__(self, toks, file):
        self.toks = toks
        self.i = 0
        self.file = file

    def kind(self):
        return self.toks[self.i][0]

    def val(self):
        return self.toks[self.i][1]

    def ln(self):
        return self.toks[self.i][2]

    def peek2(self):
        if self.i + 1 < len(self.toks):
            return self.toks[self.i + 1]
        return ("end", "", 0)

    def adv(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def at_op(self, v):
        t = self.toks[self.i]
        return t[0] == "op" and t[1] == v

    def at_kw(self, v):
        t = self.toks[self.i]
        return t[0] == "kw" and t[1] == v

    def eat_op(self, v):
        if self.at_op(v):
            self.i += 1
            return True
        return False

    def eat_kw(self, v):
        if self.at_kw(v):
            self.i += 1
            return True
        return False

    def perr(self, msg):
        return CompileError("syntax error: " + msg, self.file, self.ln())

    def req_op(self, v):
        if not self.eat_op(v):
            raise self.perr("expected '" + v + "'")

    def req_kw(self, v):
        if not self.eat_kw(v):
            raise self.perr("expected '" + v + "'")

    def req_name(self):
        if self.kind() != "nm":
            raise self.perr("expected name")
        return self.adv()[1]

    def req_nl(self):
        if self.kind() != "nl":
            raise self.perr("expected end of line")
        self.adv()

    def starts_expr(self):
        t = self.toks[self.i]
        k = t[0]
        if k in ("nm", "num", "str", "bytes", "fstr"):
            return True
        if k == "kw":
            return t[1] in ("not", "lambda", "None", "True", "False", "yield",
                            "await")
        if k == "op":
            return t[1] in ("(", "[", "{", "*", "-", "+", "~", "...")
        return False

    def parse_module(self):
        body = []
        while self.kind() != "end":
            if self.kind() == "nl":
                self.adv()
                continue
            body.extend(self.parse_line())
        return {"t": "Module", "body": body}

    def parse_line(self):
        k = self.kind()
        v = self.val()
        if k == "kw":
            if v == "if":
                return [self.if_stmt()]
            if v == "while":
                return [self.while_stmt()]
            if v == "for":
                return [self.for_stmt()]
            if v == "try":
                return [self.try_stmt()]
            if v == "with":
                return [self.with_stmt()]
            if v == "def":
                return [self.def_stmt([])]
            if v == "class":
                return [self.class_stmt([])]
            if v == "async":
                return [self.async_stmt([])]
        if k == "op" and v == "@":
            return [self.decorated()]
        if k == "nm" and v == "match" and self.looks_like_match():
            return [self.match_stmt()]
        out = []
        while True:
            out.append(self.small_stmt())
            if self.eat_op(";"):
                if self.kind() == "nl":
                    break
                continue
            break
        self.req_nl()
        return out

    def looks_like_match(self):
        j = self.i + 1
        n = len(self.toks)
        last = None
        while j < n:
            t = self.toks[j]
            if t[0] == "nl" or t[0] == "end":
                break
            last = t
            j += 1
        if last is None or j == self.i + 1:
            return False
        return last[0] == "op" and last[1] == ":"

    def suite(self):
        self.req_op(":")
        if self.kind() == "nl":
            self.adv()
            if self.kind() != "ind":
                raise self.perr("expected an indented block")
            self.adv()
            body = []
            while self.kind() != "ded":
                if self.kind() == "end":
                    raise self.perr("unexpected end of file")
                body.extend(self.parse_line())
            self.adv()
            return body
        out = []
        while True:
            out.append(self.small_stmt())
            if self.eat_op(";"):
                if self.kind() == "nl":
                    break
                continue
            break
        self.req_nl()
        return out

    def if_stmt(self):
        ln = self.ln()
        self.adv()
        test = self.namedexpr_test()
        body = self.suite()
        orelse = []
        if self.at_kw("elif"):
            orelse = [self.if_stmt()]
        elif self.eat_kw("else"):
            orelse = self.suite()
        return {"t": "If", "test": test, "then": body, "orelse": orelse, "ln": ln}

    def while_stmt(self):
        ln = self.ln()
        self.adv()
        test = self.namedexpr_test()
        body = self.suite()
        orelse = []
        if self.eat_kw("else"):
            orelse = self.suite()
        return {"t": "While", "test": test, "body": body, "orelse": orelse, "ln": ln}

    def for_stmt(self):
        ln = self.ln()
        self.adv()
        target = self.for_targets()
        self.req_kw("in")
        it = self.testlist_star()
        body = self.suite()
        orelse = []
        if self.eat_kw("else"):
            orelse = self.suite()
        return {"t": "For", "target": target, "iter": it, "body": body,
                "orelse": orelse, "ln": ln}

    def try_stmt(self):
        ln = self.ln()
        self.adv()
        body = self.suite()
        handlers = []
        star = False
        while self.at_kw("except"):
            self.adv()
            if self.at_op("*"):
                self.adv()
                star = True
            htype = None
            hname = None
            if not self.at_op(":"):
                htype = self.test()
                if self.eat_kw("as"):
                    hname = self.req_name()
            hbody = self.suite()
            handlers.append({"type": htype, "name": hname, "body": hbody})
        orelse = []
        if handlers and self.at_kw("else"):
            self.adv()
            orelse = self.suite()
        final = []
        if self.eat_kw("finally"):
            final = self.suite()
        if not handlers and not final:
            raise self.perr("expected 'except' or 'finally' block")
        return {"t": "Try", "body": body, "handlers": handlers,
                "orelse": orelse, "final": final, "star": star, "ln": ln}

    def with_stmt(self):
        ln = self.ln()
        self.adv()
        items = []
        while True:
            ctx = self.test()
            v = None
            if self.eat_kw("as"):
                v = self.t_item()
            items.append({"ctx": ctx, "vars": v})
            if not self.eat_op(","):
                break
        body = self.suite()
        return {"t": "With", "items": items, "body": body, "ln": ln}

    def decorated(self):
        decs = []
        while self.at_op("@"):
            self.adv()
            decs.append(self.test())
            self.req_nl()
        if self.at_kw("def"):
            return self.def_stmt(decs)
        if self.at_kw("class"):
            return self.class_stmt(decs)
        if self.at_kw("async"):
            return self.async_stmt(decs)
        raise self.perr("expected 'def' or 'class' after decorators")

    def async_stmt(self, decs):
        self.adv()
        if self.at_kw("def"):
            node = self.def_stmt(decs)
            node["coro"] = True
            return node
        if decs:
            raise self.perr("expected 'def' after decorators")
        if self.at_kw("for"):
            node = self.for_stmt()
            node["t"] = "AsyncFor"
            return node
        if self.at_kw("with"):
            node = self.with_stmt()
            node["t"] = "AsyncWith"
            return node
        raise self.perr("expected 'def', 'for' or 'with' after 'async'")

    def def_stmt(self, decs):
        ln = self.ln()
        self.adv()
        name = self.req_name()
        self.req_op("(")
        params = self.param_list(True, ")")
        self.req_op(")")
        returns = None
        if self.eat_op("->"):
            returns = self.test()
        body = self.suite()
        return {"t": "Func", "name": name, "params": params, "body": body,
                "decorators": decs, "coro": False, "returns": returns, "ln": ln}

    def class_stmt(self, decs):
        ln = self.ln()
        self.adv()
        name = self.req_name()
        bases = []
        keywords = []
        if self.eat_op("("):
            while not self.at_op(")"):
                if self.at_op("**"):
                    self.adv()
                    keywords.append({"n": None, "v": self.test()})
                elif self.at_op("*"):
                    sln = self.ln()
                    self.adv()
                    bases.append({"t": "Starred", "v": self.test(), "ln": sln})
                elif self.kind() == "nm" and self.peek2()[0] == "op" \
                        and self.peek2()[1] == "=":
                    kwname = self.adv()[1]
                    self.adv()
                    keywords.append({"n": kwname, "v": self.test()})
                else:
                    bases.append(self.test())
                if not self.eat_op(","):
                    break
            self.req_op(")")
        body = self.suite()
        return {"t": "Class", "name": name, "bases": bases,
                "keywords": keywords, "body": body,
                "decorators": decs, "ln": ln}

    def param_list(self, annotations, endtok):
        args = []
        defaults = []
        vararg = None
        kwonly = []
        kwdefaults = []
        kwarg = None
        kwsec = False
        posonly = 0
        argnodes = {}
        while not self.at_op(endtok):
            if self.eat_op("/"):
                posonly = len(args)
            elif self.at_op("*"):
                self.adv()
                if self.kind() == "nm":
                    vararg = self.adv()[1]
                    argnodes[vararg] = {"annotation": None, "ln": self.toks[self.i - 1][2]}
                    if annotations and self.eat_op(":"):
                        argnodes[vararg]["annotation"] = self.test()
                kwsec = True
            elif self.eat_op("**"):
                kwarg = self.req_name()
                argnodes[kwarg] = {"annotation": None, "ln": self.toks[self.i - 1][2]}
                if annotations and self.eat_op(":"):
                    argnodes[kwarg]["annotation"] = self.test()
            elif self.kind() == "nm":
                nm = self.adv()[1]
                argnodes[nm] = {"annotation": None, "ln": self.toks[self.i - 1][2]}
                if annotations and self.eat_op(":"):
                    argnodes[nm]["annotation"] = self.test()
                d = None
                if self.eat_op("="):
                    d = self.test()
                if kwsec:
                    kwonly.append(nm)
                    kwdefaults.append(d)
                else:
                    args.append(nm)
                    if d is not None:
                        defaults.append(d)
            else:
                raise self.perr("invalid parameter list")
            if not self.eat_op(","):
                break
        return {"args": args, "defaults": defaults, "vararg": vararg,
                "kwonly": kwonly, "kwdefaults": kwdefaults, "kwarg": kwarg,
                "posonly": posonly, "argnodes": argnodes}

    def small_stmt(self):
        k = self.kind()
        v = self.val()
        ln = self.ln()
        if k == "kw":
            if v == "return":
                self.adv()
                if self.starts_expr():
                    return {"t": "Return", "v": self.testlist_star(), "ln": ln}
                return {"t": "Return", "v": None, "ln": ln}
            if v == "pass":
                self.adv()
                return {"t": "Pass", "ln": ln}
            if v == "break":
                self.adv()
                return {"t": "Break", "ln": ln}
            if v == "continue":
                self.adv()
                return {"t": "Continue", "ln": ln}
            if v == "raise":
                self.adv()
                exc = None
                cause = None
                if self.starts_expr():
                    exc = self.test()
                    if self.eat_kw("from"):
                        cause = self.test()
                return {"t": "Raise", "exc": exc, "cause": cause, "ln": ln}
            if v == "global" or v == "nonlocal":
                self.adv()
                names = [self.req_name()]
                while self.eat_op(","):
                    names.append(self.req_name())
                if v == "global":
                    return {"t": "Global", "names": names, "ln": ln}
                return {"t": "Nonlocal", "names": names, "ln": ln}
            if v == "del":
                self.adv()
                targets = [self.test()]
                while self.eat_op(","):
                    if not self.starts_expr():
                        break
                    targets.append(self.test())
                return {"t": "Del", "targets": targets, "ln": ln}
            if v == "assert":
                self.adv()
                test = self.test()
                msg = None
                if self.eat_op(","):
                    msg = self.test()
                return {"t": "Assert", "test": test, "msg": msg, "ln": ln}
            if v == "import":
                self.adv()
                names = []
                while True:
                    mod = self.dotted_name()
                    asname = None
                    if self.eat_kw("as"):
                        asname = self.req_name()
                    names.append({"n": mod, "as": asname})
                    if not self.eat_op(","):
                        break
                return {"t": "Import", "names": names, "ln": ln}
            if v == "from":
                self.adv()
                level = 0
                while True:
                    if self.eat_op("."):
                        level += 1
                    elif self.eat_op("..."):
                        level += 3
                    else:
                        break
                mod = None
                if self.kind() == "nm":
                    mod = self.dotted_name()
                self.req_kw("import")
                names = []
                paren = self.eat_op("(")
                if self.eat_op("*"):
                    names.append({"n": "*", "as": None})
                else:
                    while True:
                        nm = self.req_name()
                        asname = None
                        if self.eat_kw("as"):
                            asname = self.req_name()
                        names.append({"n": nm, "as": asname})
                        if not self.eat_op(","):
                            break
                        if paren and self.at_op(")"):
                            break
                if paren:
                    self.req_op(")")
                return {"t": "ImportFrom", "module": mod, "names": names,
                        "level": level, "ln": ln}
            if v == "yield":
                y = self.yield_expr()
                return {"t": "Expr", "v": y, "ln": ln}
        first = self.testlist_star()
        if self.at_op("="):
            values = [first]
            while self.eat_op("="):
                if self.at_kw("yield"):
                    values.append(self.yield_expr())
                else:
                    values.append(self.testlist_star())
            return {"t": "Assign", "targets": values[:-1], "v": values[-1], "ln": ln}
        if self.kind() == "op" and self.val() in AUGOPS:
            op = self.adv()[1]
            if self.at_kw("yield"):
                rhs = self.yield_expr()
            else:
                rhs = self.testlist_star()
            return {"t": "Aug", "target": first, "kind": AUGOPS[op], "v": rhs, "ln": ln}
        if self.at_op(":"):
            self.adv()
            annotation = self.test()
            v2 = None
            if self.eat_op("="):
                if self.at_kw("yield"):
                    v2 = self.yield_expr()
                else:
                    v2 = self.testlist_star()
            return {"t": "Ann", "target": first, "annotation": annotation,
                    "simple": first["t"] == "Name" and k == "nm", "v": v2, "ln": ln}
        return {"t": "Expr", "v": first, "ln": ln}

    def dotted_name(self):
        parts = [self.req_name()]
        while self.at_op(".") and self.peek2()[0] == "nm":
            self.adv()
            parts.append(self.req_name())
        return ".".join(parts)

    def match_stmt(self):
        ln = self.ln()
        self.adv()
        subject = self.testlist_star()
        self.req_op(":")
        self.req_nl()
        if self.kind() != "ind":
            raise self.perr("expected an indented block")
        self.adv()
        cases = []
        while self.kind() == "nm" and self.val() == "case":
            self.adv()
            pat = self.patterns()
            guard = None
            if self.eat_kw("if"):
                guard = self.namedexpr_test()
            body = self.suite()
            cases.append({"pattern": pat, "guard": guard, "body": body})
        if self.kind() != "ded":
            raise self.perr("expected 'case' block")
        self.adv()
        if not cases:
            raise self.perr("match statement must have at least one case")
        return {"t": "Match", "subject": subject, "cases": cases, "ln": ln}

    def patterns(self):
        first = self.pattern_or_star()
        if not self.at_op(","):
            if first["t"] == "MStar":
                raise self.perr("invalid star pattern")
            return first
        items = [first]
        while self.eat_op(","):
            if self.at_op(":") or self.at_kw("if"):
                break
            items.append(self.pattern_or_star())
        return {"t": "MSeq", "pats": items, "ln": first["ln"]}

    def pattern_or_star(self):
        if self.at_op("*"):
            ln = self.ln()
            self.adv()
            nm = self.req_name()
            if nm == "_":
                return {"t": "MStar", "name": None, "ln": ln}
            return {"t": "MStar", "name": nm, "ln": ln}
        return self.pattern()

    def pattern(self):
        p = self.or_pattern()
        while self.at_kw("as"):
            self.adv()
            nm = self.req_name()
            p = {"t": "MAs", "pat": p, "name": nm, "ln": p["ln"]}
        return p

    def or_pattern(self):
        p = self.closed_pattern()
        if not self.at_op("|"):
            return p
        pats = [p]
        while self.eat_op("|"):
            pats.append(self.closed_pattern())
        return {"t": "MOr", "pats": pats, "ln": p["ln"]}

    def literal_key_expr(self):
        """Parse a literal or dotted name for a mapping key or value pattern."""
        k = self.kind()
        v = self.val()
        ln = self.ln()
        if self.at_op("-"):
            self.adv()
            numtok = self.adv()
            if numtok[0] != "num":
                raise self.perr("invalid pattern")
            c = num_const(numtok[1], numtok[2], self.file)
            return {"t": "Unary", "kind": "neg", "v": c, "ln": ln}
        if k == "num":
            self.adv()
            return num_const(v, ln, self.file)
        if k in ("str", "bytes", "fstr"):
            return self.strings()
        if k == "kw" and v in ("None", "True", "False"):
            self.adv()
            if v == "None":
                return {"t": "Const", "kind": "none", "v": None, "ln": ln}
            return {"t": "Const", "kind": "bool", "v": v == "True", "ln": ln}
        if k == "nm":
            e = {"t": "Name", "id": self.adv()[1], "ln": ln}
            while self.at_op("."):
                self.adv()
                e = {"t": "Attr", "o": e, "n": self.req_name(), "ln": ln}
            return e
        raise self.perr("invalid pattern key")

    def closed_pattern(self):
        k = self.kind()
        v = self.val()
        ln = self.ln()
        if k == "op" and v == "(":
            self.adv()
            if self.eat_op(")"):
                return {"t": "MSeq", "pats": [], "ln": ln}
            first = self.pattern_or_star()
            if self.at_op(","):
                items = [first]
                while self.eat_op(","):
                    if self.at_op(")"):
                        break
                    items.append(self.pattern_or_star())
                self.req_op(")")
                return {"t": "MSeq", "pats": items, "ln": ln}
            self.req_op(")")
            return first
        if k == "op" and v == "[":
            self.adv()
            items = []
            while not self.at_op("]"):
                items.append(self.pattern_or_star())
                if not self.eat_op(","):
                    break
            self.req_op("]")
            return {"t": "MSeq", "pats": items, "ln": ln}
        if k == "op" and v == "{":
            self.adv()
            keys = []
            pats = []
            rest = None
            while not self.at_op("}"):
                if self.eat_op("**"):
                    rest = self.req_name()
                else:
                    keys.append(self.literal_key_expr())
                    self.req_op(":")
                    pats.append(self.pattern())
                if not self.eat_op(","):
                    break
            self.req_op("}")
            return {"t": "MMap", "keys": keys, "pats": pats, "rest": rest, "ln": ln}
        if k == "op" and v == "-":
            e = self.literal_key_expr()
            return {"t": "MVal", "v": e, "ln": ln}
        if k == "num":
            e = self.literal_key_expr()
            return {"t": "MVal", "v": e, "ln": ln}
        if k in ("str", "bytes", "fstr"):
            e = self.strings()
            if e["t"] != "Const":
                raise self.perr("patterns may only match literals")
            return {"t": "MVal", "v": e, "ln": ln}
        if k == "kw" and v in ("None", "True", "False"):
            self.adv()
            if v == "None":
                c = {"t": "Const", "kind": "none", "v": None, "ln": ln}
            else:
                c = {"t": "Const", "kind": "bool", "v": v == "True", "ln": ln}
            return {"t": "MSing", "c": c, "ln": ln}
        if k == "nm":
            nxt = self.peek2()
            if v == "_" and not (nxt[0] == "op" and (nxt[1] == "." or nxt[1] == "(")):
                self.adv()
                return {"t": "MAs", "pat": None, "name": None, "ln": ln}
            if nxt[0] == "op" and (nxt[1] == "." or nxt[1] == "("):
                e = {"t": "Name", "id": self.adv()[1], "ln": ln}
                while self.at_op("."):
                    self.adv()
                    e = {"t": "Attr", "o": e, "n": self.req_name(), "ln": ln}
                if self.at_op("("):
                    self.adv()
                    pos = []
                    attrs = []
                    kpats = []
                    while not self.at_op(")"):
                        if self.kind() == "nm" and self.peek2()[0] == "op" \
                                and self.peek2()[1] == "=":
                            attrs.append(self.adv()[1])
                            self.adv()
                            kpats.append(self.pattern())
                        else:
                            pos.append(self.pattern())
                        if not self.eat_op(","):
                            break
                    self.req_op(")")
                    return {"t": "MCls", "cls": e, "pos": pos, "attrs": attrs,
                            "kpats": kpats, "ln": ln}
                return {"t": "MVal", "v": e, "ln": ln}
            self.adv()
            return {"t": "MAs", "pat": None, "name": v, "ln": ln}
        raise self.perr("invalid match pattern")

    def namedexpr_test(self):
        if self.kind() == "nm":
            nxt = self.peek2()
            if nxt[0] == "op" and nxt[1] == ":=":
                ln = self.ln()
                name = self.adv()[1]
                self.adv()
                return {"t": "Named", "target": {"t": "Name", "id": name, "ln": ln},
                        "v": self.test(), "ln": ln}
        return self.test()

    def testlist_star(self):
        first = self.star_or_test()
        if not self.at_op(","):
            return first
        elts = [first]
        while self.eat_op(","):
            if not self.starts_expr():
                break
            elts.append(self.star_or_test())
        return {"t": "Tuple", "elts": elts, "ln": first["ln"]}

    def star_or_test(self):
        if self.at_op("*"):
            ln = self.ln()
            self.adv()
            return {"t": "Starred", "v": self.or_expr_chain(), "ln": ln}
        return self.test()

    def or_expr_chain(self):
        return self.bitor_()

    def test(self):
        if self.at_kw("lambda"):
            return self.lambda_expr()
        v = self.or_test()
        if self.at_kw("if"):
            self.adv()
            cond = self.or_test()
            self.req_kw("else")
            els = self.test()
            return {"t": "Cond", "test": cond, "then": v, "else": els, "ln": v["ln"]}
        return v

    def lambda_expr(self):
        ln = self.ln()
        self.adv()
        params = self.param_list(False, ":")
        self.req_op(":")
        body = self.test()
        return {"t": "Lambda", "params": params, "body": body, "ln": ln}

    def or_test(self):
        v = self.and_test()
        if not self.at_kw("or"):
            return v
        values = [v]
        while self.eat_kw("or"):
            values.append(self.and_test())
        return {"t": "Bool", "kind": "or", "values": values, "ln": v["ln"]}

    def and_test(self):
        v = self.not_test()
        if not self.at_kw("and"):
            return v
        values = [v]
        while self.eat_kw("and"):
            values.append(self.not_test())
        return {"t": "Bool", "kind": "and", "values": values, "ln": v["ln"]}

    def not_test(self):
        if self.at_kw("not"):
            ln = self.ln()
            self.adv()
            return {"t": "Unary", "kind": "not", "v": self.not_test(), "ln": ln}
        return self.comparison()

    def comparison(self):
        v = self.bitor_()
        ops = []
        comps = []
        while True:
            k = self.kind()
            vv = self.val()
            kindop = None
            if k == "op" and vv in CMPOPS:
                kindop = CMPOPS[vv]
                self.adv()
            elif k == "kw" and vv == "in":
                kindop = "in"
                self.adv()
            elif k == "kw" and vv == "not":
                nxt = self.peek2()
                if nxt[0] == "kw" and nxt[1] == "in":
                    kindop = "notin"
                    self.adv()
                    self.adv()
                else:
                    break
            elif k == "kw" and vv == "is":
                self.adv()
                if self.at_kw("not"):
                    self.adv()
                    kindop = "isnot"
                else:
                    kindop = "is"
            else:
                break
            ops.append(kindop)
            comps.append(self.bitor_())
        if not ops:
            return v
        return {"t": "Cmp", "left": v, "ops": ops, "comps": comps, "ln": v["ln"]}

    def bitor_(self):
        v = self.bitxor_()
        while self.at_op("|"):
            self.adv()
            v = {"t": "BinOp", "kind": "bitor", "l": v, "r": self.bitxor_(),
                 "ln": v["ln"]}
        return v

    def bitxor_(self):
        v = self.bitand_()
        while self.at_op("^"):
            self.adv()
            v = {"t": "BinOp", "kind": "bitxor", "l": v, "r": self.bitand_(),
                 "ln": v["ln"]}
        return v

    def bitand_(self):
        v = self.shift_()
        while self.at_op("&"):
            self.adv()
            v = {"t": "BinOp", "kind": "bitand", "l": v, "r": self.shift_(),
                 "ln": v["ln"]}
        return v

    def shift_(self):
        v = self.arith_()
        while self.at_op("<<") or self.at_op(">>"):
            kind = "lshift" if self.val() == "<<" else "rshift"
            self.adv()
            v = {"t": "BinOp", "kind": kind, "l": v, "r": self.arith_(), "ln": v["ln"]}
        return v

    def arith_(self):
        v = self.term_()
        while self.at_op("+") or self.at_op("-"):
            kind = "add" if self.val() == "+" else "sub"
            self.adv()
            v = {"t": "BinOp", "kind": kind, "l": v, "r": self.term_(), "ln": v["ln"]}
        return v

    def term_(self):
        v = self.factor()
        while True:
            if self.at_op("*"):
                kind = "mul"
            elif self.at_op("/"):
                kind = "div"
            elif self.at_op("//"):
                kind = "floordiv"
            elif self.at_op("%"):
                kind = "mod"
            elif self.at_op("@"):
                kind = "matmul"
            else:
                break
            self.adv()
            v = {"t": "BinOp", "kind": kind, "l": v, "r": self.factor(), "ln": v["ln"]}
        return v

    def factor(self):
        k = self.kind()
        v = self.val()
        if k == "op" and v in ("+", "-", "~"):
            ln = self.ln()
            self.adv()
            kind = "pos" if v == "+" else ("neg" if v == "-" else "invert")
            return {"t": "Unary", "kind": kind, "v": self.factor(), "ln": ln}
        return self.power()

    def power(self):
        if self.at_kw("await"):
            ln = self.ln()
            self.adv()
            b = {"t": "Await", "v": self.postfix(), "ln": ln}
        else:
            b = self.postfix()
        if self.eat_op("**"):
            return {"t": "BinOp", "kind": "pow", "l": b, "r": self.factor(),
                    "ln": b["ln"]}
        return b

    def postfix(self):
        v = self.atom()
        while True:
            if self.at_op("("):
                v = self.call_trailer(v)
            elif self.at_op("."):
                self.adv()
                n = self.req_name()
                v = {"t": "Attr", "o": v, "n": n, "ln": v["ln"]}
            elif self.at_op("["):
                v = self.sub_trailer(v)
            else:
                break
        return v

    def call_trailer(self, f):
        lp_ln = self.ln()
        self.adv()
        args = []
        kw = []
        first = True
        while not self.at_op(")"):
            if self.at_op("*") and not self.at_op("**"):
                sln = self.ln()
                self.adv()
                args.append({"t": "Starred", "v": self.test(), "ln": sln})
            elif self.at_op("**"):
                self.adv()
                kw.append({"n": None, "v": self.test()})
            elif self.kind() == "nm" and self.peek2()[0] == "op" \
                    and self.peek2()[1] == "=":
                name = self.adv()[1]
                self.adv()
                kw.append({"n": name, "v": self.test()})
            else:
                e = self.namedexpr_test()
                if first and not kw and self.at_kw("for"):
                    gens = self.comp_clauses()
                    args.append({"t": "Comp", "kind": "gen", "elt": e,
                                 "gens": gens, "ln": lp_ln})
                    break
                args.append(e)
            first = False
            if not self.eat_op(","):
                break
        self.req_op(")")
        return {"t": "Call", "f": f, "args": args, "kw": kw, "ln": f["ln"]}

    def sub_trailer(self, o):
        lb_ln = self.ln()
        self.adv()
        items = []
        sawcomma = False
        while not self.at_op("]"):
            items.append(self.sub_item())
            if self.eat_op(","):
                sawcomma = True
                continue
            break
        self.req_op("]")
        if not items:
            raise self.perr("empty subscript")
        if len(items) == 1 and not sawcomma:
            k = items[0]
        else:
            k = {"t": "Tuple", "elts": items, "ln": lb_ln}
        return {"t": "Sub", "o": o, "k": k, "ln": o["ln"]}

    def sub_item(self):
        lower = None
        if not self.at_op(":"):
            e = self.namedexpr_test()
            if not self.at_op(":"):
                return e
            lower = e
        ln = self.ln()
        self.req_op(":")
        upper = None
        step = None
        if not (self.at_op(":") or self.at_op("]") or self.at_op(",")):
            upper = self.test()
        if self.eat_op(":"):
            if not (self.at_op("]") or self.at_op(",")):
                step = self.test()
        return {"t": "Slice", "lower": lower, "upper": upper, "step": step, "ln": ln}

    def comp_clauses(self):
        gens = []
        while self.at_kw("for"):
            self.adv()
            tgt = self.for_targets()
            self.req_kw("in")
            it = self.or_test()
            ifs = []
            while self.at_kw("if"):
                self.adv()
                if self.at_kw("lambda"):
                    ifs.append(self.lambda_expr())
                elif self.kind() == "nm" and self.peek2()[0] == "op" \
                        and self.peek2()[1] == ":=":
                    ifs.append(self.namedexpr_test())
                else:
                    ifs.append(self.or_test())
            gens.append({"target": tgt, "iter": it, "ifs": ifs})
        return gens

    def for_targets(self):
        first = self.t_item()
        if not self.at_op(","):
            return first
        elts = [first]
        while self.eat_op(","):
            if not self.starts_expr():
                break
            elts.append(self.t_item())
        return {"t": "Tuple", "elts": elts, "ln": first["ln"]}

    def t_item(self):
        if self.at_op("*"):
            ln = self.ln()
            self.adv()
            return {"t": "Starred", "v": self.postfix(), "ln": ln}
        return self.postfix()

    def yield_expr(self):
        ln = self.ln()
        self.adv()
        if self.eat_kw("from"):
            return {"t": "YieldFrom", "v": self.test(), "ln": ln}
        if self.starts_expr():
            return {"t": "Yield", "v": self.testlist_star(), "ln": ln}
        return {"t": "Yield", "v": None, "ln": ln}

    def paren_item(self):
        if self.at_op("*"):
            ln = self.ln()
            self.adv()
            return {"t": "Starred", "v": self.or_expr_chain(), "ln": ln}
        return self.namedexpr_test()

    def atom(self):
        t = self.toks[self.i]
        k = t[0]
        v = t[1]
        ln = t[2]
        if k == "nm":
            self.adv()
            return {"t": "Name", "id": v, "ln": ln}
        if k == "num":
            self.adv()
            return num_const(v, ln, self.file)
        if k in ("str", "bytes", "fstr"):
            return self.strings()
        if k == "kw":
            if v == "True" or v == "False":
                self.adv()
                return {"t": "Const", "kind": "bool", "v": v == "True", "ln": ln}
            if v == "None":
                self.adv()
                return {"t": "Const", "kind": "none", "v": None, "ln": ln}
            if v == "lambda":
                return self.lambda_expr()
            if v == "await":
                raise self.perr("async/await is not supported")
            raise self.perr("unexpected keyword '" + v + "'")
        if k == "op":
            if v == "(":
                return self.paren_atom()
            if v == "[":
                return self.list_atom()
            if v == "{":
                return self.brace_atom()
            if v == "...":
                self.adv()
                return {"t": "Const", "kind": "ellipsis", "v": None, "ln": ln}
        raise self.perr("unexpected token")

    def paren_atom(self):
        ln0 = self.ln()
        self.adv()
        if self.eat_op(")"):
            return {"t": "Tuple", "elts": [], "ln": ln0}
        if self.at_kw("yield"):
            y = self.yield_expr()
            self.req_op(")")
            return y
        first = self.paren_item()
        if self.at_kw("for"):
            gens = self.comp_clauses()
            self.req_op(")")
            return {"t": "Comp", "kind": "gen", "elt": first, "gens": gens, "ln": ln0}
        if self.at_op(","):
            elts = [first]
            while self.eat_op(","):
                if self.at_op(")"):
                    break
                elts.append(self.paren_item())
            self.req_op(")")
            return {"t": "Tuple", "elts": elts, "ln": ln0}
        self.req_op(")")
        return first

    def list_atom(self):
        ln0 = self.ln()
        self.adv()
        if self.eat_op("]"):
            return {"t": "List", "elts": [], "ln": ln0}
        first = self.paren_item()
        if self.at_kw("for"):
            gens = self.comp_clauses()
            self.req_op("]")
            return {"t": "Comp", "kind": "list", "elt": first, "gens": gens, "ln": ln0}
        elts = [first]
        while self.eat_op(","):
            if self.at_op("]"):
                break
            elts.append(self.paren_item())
        self.req_op("]")
        return {"t": "List", "elts": elts, "ln": ln0}

    def brace_atom(self):
        ln0 = self.ln()
        self.adv()
        if self.eat_op("}"):
            return {"t": "Dict", "entries": [], "ln": ln0}
        if self.at_op("**"):
            self.adv()
            entries = [{"k": None, "v": self.or_expr_chain()}]
            return self.dict_rest(entries, ln0)
        first = self.paren_item()
        if self.at_op(":"):
            self.adv()
            val = self.test()
            if self.at_kw("for"):
                gens = self.comp_clauses()
                self.req_op("}")
                return {"t": "Comp", "kind": "dict", "key": first, "val": val,
                        "gens": gens, "ln": ln0}
            entries = [{"k": first, "v": val}]
            return self.dict_rest(entries, ln0)
        if self.at_kw("for"):
            gens = self.comp_clauses()
            self.req_op("}")
            return {"t": "Comp", "kind": "set", "elt": first, "gens": gens, "ln": ln0}
        elts = [first]
        while self.eat_op(","):
            if self.at_op("}"):
                break
            elts.append(self.paren_item())
        self.req_op("}")
        return {"t": "Set", "elts": elts, "ln": ln0}

    def dict_rest(self, entries, ln0):
        while self.eat_op(","):
            if self.at_op("}"):
                break
            if self.eat_op("**"):
                entries.append({"k": None, "v": self.or_expr_chain()})
            else:
                k = self.test()
                self.req_op(":")
                entries.append({"k": k, "v": self.test()})
        self.req_op("}")
        return {"t": "Dict", "entries": entries, "ln": ln0}

    def strings(self):
        parts = []
        while self.kind() in ("str", "bytes", "fstr"):
            parts.append(self.adv())
        ln = parts[0][2]
        has_f = False
        has_b = False
        has_s = False
        for p in parts:
            if p[0] == "fstr":
                has_f = True
            elif p[0] == "bytes":
                has_b = True
            else:
                has_s = True
        if has_b and (has_f or has_s):
            raise self.perr("cannot mix bytes and nonbytes literals")
        if has_b:
            total = b""
            for p in parts:
                total = total + p[1]
            return {"t": "Const", "kind": "bytes", "v": total, "ln": ln}
        if not has_f:
            return {"t": "Const", "kind": "str",
                    "v": "".join([p[1] for p in parts]), "prefix": parts[0][3], "ln": ln}
        values = []
        buf = []
        for p in parts:
            if p[0] == "str":
                buf.append(p[1])
            else:
                raw = p[1][0]
                is_raw = p[1][1]
                self.fstr_walk(raw, is_raw, p[2], values, buf)
        self.spill(values, buf)
        return {"t": "Fstr", "parts": values, "ln": ln}

    def flush_lit(self, lit, is_raw, ln0, buf):
        if lit:
            s = "".join(lit)
            buf.append(decode_str(s, is_raw, self.file, ln0))

    def spill(self, values, buf):
        text = "".join(buf)
        if text:
            values.append({"lit": text})
        buf.clear()

    def fstr_walk(self, raw, is_raw, ln0, values, buf):
        i = 0
        n = len(raw)
        lit = []
        while i < n:
            c = raw[i]
            if c == "{":
                if i + 1 < n and raw[i + 1] == "{":
                    lit.append("{")
                    i += 2
                    continue
                self.flush_lit(lit, is_raw, ln0, buf)
                self.spill(values, buf)
                lit = []
                i += 1
                start = i
                depth = 0
                while i < n:
                    c2 = raw[i]
                    if c2 in "([{":
                        depth += 1
                        i += 1
                    elif c2 in ")]":
                        depth -= 1
                        i += 1
                    elif c2 == "}":
                        if depth == 0:
                            break
                        depth -= 1
                        i += 1
                    elif c2 == "'" or c2 == "\"":
                        i = self.skip_fexpr_str(raw, i, ln0)
                    elif c2 == "\\":
                        i += 2
                    elif depth == 0 and c2 == "!" and i + 1 < n and raw[i + 1] != "=":
                        break
                    elif depth == 0 and c2 == ":":
                        break
                    else:
                        i += 1
                expr_src = raw[start:i]
                conv = None
                if i < n and raw[i] == "!":
                    if i + 1 >= n:
                        raise CompileError("syntax error: bad f-string conversion",
                                           self.file, ln0)
                    conv = raw[i + 1]
                    i += 2
                spec_node = None
                if i < n and raw[i] == ":":
                    i += 1
                    sstart = i
                    d2 = 0
                    while i < n:
                        c3 = raw[i]
                        if c3 == "{":
                            d2 += 1
                        elif c3 == "}":
                            if d2 == 0:
                                break
                            d2 -= 1
                        i += 1
                    spec_ln = ln0 + raw[:sstart].count("\n")
                    spec_node = self.parse_spec(raw[sstart:i], is_raw, spec_ln)
                if i >= n or raw[i] != "}":
                    raise CompileError("syntax error: expected '}' in f-string",
                                       self.file, ln0)
                i += 1
                if not expr_src.strip():
                    raise CompileError(
                        "syntax error: f-string: valid expression required "
                        "before '}'", self.file, ln0)
                node = self.parse_fexpr(expr_src, ln0 + raw[:start].count("\n"))
                values.append({"t": "FV", "v": node, "conv": conv,
                               "spec": spec_node})
            elif c == "}":
                if i + 1 < n and raw[i + 1] == "}":
                    lit.append("}")
                    i += 2
                    continue
                raise CompileError(
                    "syntax error: f-string: single '}' is not allowed",
                    self.file, ln0)
            else:
                lit.append(c)
                i += 1
        self.flush_lit(lit, is_raw, ln0, buf)

    def skip_fexpr_str(self, raw, i, ln0):
        n = len(raw)
        q = raw[i]
        if raw[i:i + 3] == q * 3:
            close = q * 3
            i += 3
            while i < n:
                if raw[i] == "\\":
                    i += 2
                    continue
                if raw[i:i + 3] == close:
                    return i + 3
                i += 1
        else:
            i += 1
            while i < n:
                if raw[i] == "\\":
                    i += 2
                    continue
                if raw[i] == q:
                    return i + 1
                i += 1
        raise CompileError("syntax error: unterminated string in f-string",
                           self.file, ln0)

    def parse_spec(self, src, is_raw, base_ln):
        values = []
        buf = []
        self.fstr_walk(src, is_raw, base_ln, values, buf)
        text = "".join(buf)
        if text:
            values.append({"lit": text})
        elif values and "lit" not in values[-1]:
            # Match CPython's trailing empty Constant after a replacement field.
            values.append({"lit": ""})
        return {"t": "Fstr", "parts": values, "ln": base_ln}

    def parse_fexpr(self, src, base_ln):
        toks = tokenize("(" + src + ")", self.file)
        p = Parser(toks, self.file)
        node = p.test()
        fix_lines(node, base_ln - 1)
        return node
