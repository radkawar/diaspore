"""Compile Python-subset source to Diaspore JSON IR using the Diaspore subset."""

import json
import re
import sys

DIASPORE_VERSION = 1

NATIVE_MODULES = {"time", "math", "json", "random", "sys", "net", "re", "tasks", "flow"}
TYPE_ONLY_MODULES = {"typing", "__future__"}

# Keep star-import exports synchronized with make_native_modules in vm.py.
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

STR_START = "([rRbBuUfF]{0,3})('''|\"\"\"|'|\")"
NAME_RE = "[A-Za-z_][A-Za-z0-9_]*"
NUM_RE = ("0[xX](_?[0-9a-fA-F])+|0[oO](_?[0-7])+|0[bB](_?[01])+"
          "|([0-9](_?[0-9])*)?\\.[0-9](_?[0-9])*([eE][-+]?[0-9](_?[0-9])*)?"
          "|[0-9](_?[0-9])*\\.([0-9](_?[0-9])*)?([eE][-+]?[0-9](_?[0-9])*)?"
          "|[0-9](_?[0-9])*[eE][-+]?[0-9](_?[0-9])*"
          "|[0-9](_?[0-9])*")

VALID_PREFIXES = {"", "r", "b", "u", "f", "rb", "br", "fr", "rf"}

B64CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


class CompileError(Exception):
    def __init__(self, msg, file, line):
        self.msg = msg
        self.file = file
        self.line = line


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
                m = re.match(STR_START, seg)
                if m is not None:
                    prefix = m.group(1).lower()
                    quote = m.group(2)
                    if prefix not in VALID_PREFIXES:
                        raise CompileError("syntax error: invalid string prefix",
                                           file, ln)
                    r = scan_str(lines, li, col + len(m.group(0)), quote, file)
                    raw = r[0]
                    is_raw = "r" in prefix
                    if "f" in prefix:
                        toks.append(("fstr", (raw, is_raw), ln))
                    elif "b" in prefix:
                        toks.append(("bytes", decode_bytes(raw, is_raw, file, ln), ln))
                    else:
                        toks.append(("str", decode_str(raw, is_raw, file, ln), ln))
                    had = True
                    if r[1] != li:
                        li = r[1]
                        line = clean_line(lines, li)
                    col = r[2]
                    continue
            isnum = False
            if c.isdigit():
                isnum = True
            elif c == "." and col + 1 < len(line) and line[col + 1].isdigit():
                isnum = True
            if isnum:
                m = re.match(NUM_RE, seg)
                text = m.group(0)
                col += len(text)
                if col < len(line) and line[col] in "jJ" \
                        and text[:2] not in ("0x", "0X", "0o", "0O", "0b", "0B"):
                    text = text + line[col]
                    col += 1
                toks.append(("num", text, ln))
                had = True
                continue
            word = None
            m = re.match(NAME_RE, seg)
            if m is not None:
                word = m.group(0)
                # NAME_RE matches only ASCII; identifiers may continue in Unicode.
                j = col + len(word)
                while j < len(line) and (line[j] == "_" or line[j].isalnum()):
                    j += 1
                word = line[col:j]
            elif c == "_" or c.isalpha():
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
        if self.eat_op("->"):
            self.test()
        body = self.suite()
        return {"t": "Func", "name": name, "params": params, "body": body,
                "decorators": decs, "coro": False, "ln": ln}

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
        while not self.at_op(endtok):
            if self.eat_op("/"):
                pass
            elif self.at_op("*"):
                self.adv()
                if self.kind() == "nm":
                    vararg = self.adv()[1]
                    if annotations and self.eat_op(":"):
                        self.test()
                kwsec = True
            elif self.eat_op("**"):
                kwarg = self.req_name()
                if annotations and self.eat_op(":"):
                    self.test()
            elif self.kind() == "nm":
                nm = self.adv()[1]
                if annotations and self.eat_op(":"):
                    self.test()
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
                "kwonly": kwonly, "kwdefaults": kwdefaults, "kwarg": kwarg}

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
            self.test()
            v2 = None
            if self.eat_op("="):
                if self.at_kw("yield"):
                    v2 = self.yield_expr()
                else:
                    v2 = self.testlist_star()
            return {"t": "Ann", "target": first, "v": v2, "ln": ln}
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
                    "v": "".join([p[1] for p in parts]), "ln": ln}
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


class Scope:
    def __init__(self, kind, locals_, gdecl, ndecl):
        self.kind = kind
        self.locals = locals_
        self.gdecl = gdecl
        self.ndecl = ndecl



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
        self.scopes.append(Scope("module", set(), set(), set()))
        body = self.block(tree["body"])
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
            if s["v"] is None:
                return None
            return {"op": "assign", "targets": [self.target(s["target"])],
                    "v": self.expr(s["v"]), "ln": ln}
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
        self.scopes.append(Scope("class", col.assigned, col.gdecl, col.ndecl))
        try:
            body = self.block(s["body"])
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
    def __init__(self, roots, prefix, debug):
        self.roots = roots
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
        if self.prefix is None or not path.startswith(self.prefix + "/"):
            prefix = find_cwd_prefix(path, source)
            if prefix is not None:
                self.prefix = prefix
        mc = ModuleCompiler(name, rel_file(path, self.prefix), source,
                            pkg_ctx, self)
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


def compile_bundle(entry_path, debug, search_paths):
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

    b = Bundler([root] + search_paths, prefix, debug)
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
    needs_path = False
    for a in sys.argv[1:]:
        if needs_path:
            search_paths.append(a.rstrip("/") or "/")
            needs_path = False
        elif a == "--path":
            needs_path = True
        elif a == "--debug":
            debug = True
        else:
            args.append(a)
    if len(args) != 1 or needs_path:
        sys.stderr_write("usage: dspc [--debug] [--path DIR ...] entry.py\n")
        exit(2)
    try:
        bundle = compile_bundle(args[0], debug, search_paths)
    except CompileError as e:
        if e.line:
            loc = e.file + ":" + str(e.line)
        else:
            loc = e.file
        sys.stderr_write("diaspore: compile error: " + loc + ": " + e.msg + "\n")
        exit(2)
    print(json.dumps(bundle))


main()
