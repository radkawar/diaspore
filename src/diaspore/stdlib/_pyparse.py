"""Tokenizer and parser shared by ast and the self-hosted compiler.

Node spans use one-based lines and zero-based UTF-8 byte columns. The separate
``ln`` field retains the compiler's diagnostic line without changing emitted IR.
Tokens retain their kind, value, line and string-prefix slots, followed by the
start column, end line and end column.
"""

KEYWORDS = {
    "False",
    "None",
    "True",
    "and",
    "as",
    "assert",
    "async",
    "await",
    "break",
    "class",
    "continue",
    "def",
    "del",
    "elif",
    "else",
    "except",
    "finally",
    "for",
    "from",
    "global",
    "if",
    "import",
    "in",
    "is",
    "lambda",
    "nonlocal",
    "not",
    "or",
    "pass",
    "raise",
    "return",
    "try",
    "while",
    "with",
    "yield",
}

OPS3 = {"**=", "//=", ">>=", "<<=", "..."}
OPS2 = {
    "**",
    "//",
    ">>",
    "<<",
    "<=",
    ">=",
    "==",
    "!=",
    "->",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "&=",
    "|=",
    "^=",
    "@=",
    ":=",
}
OPS1 = "+-*/%@&|^~<>()[]{},:.;="

AUGOPS = {
    "+=": "add",
    "-=": "sub",
    "*=": "mul",
    "/=": "div",
    "//=": "floordiv",
    "%=": "mod",
    "**=": "pow",
    "<<=": "lshift",
    ">>=": "rshift",
    "&=": "bitand",
    "|=": "bitor",
    "^=": "bitxor",
    "@=": "matmul",
}

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
                    "syntax error: bytes can only contain ASCII literal characters", file, ln
                )
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
        elif e == '"':
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
            hx = sv[i + 1 : i + 3]
            if len(hx) < 2:
                raise CompileError("syntax error: truncated \\x escape", file, ln)
            out.append(hexval(hx, file, ln))
            i += 3
            continue
        elif e == "u" and not is_bytes:
            hx = sv[i + 1 : i + 5]
            if len(hx) < 4:
                raise CompileError("syntax error: truncated \\u escape", file, ln)
            out.append(hexval(hx, file, ln))
            i += 5
            continue
        elif e == "U" and not is_bytes:
            hx = sv[i + 1 : i + 9]
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
                    "syntax error: bytes can only contain ASCII literal characters", file, ln
                )
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
                    "syntax error: bytes can only contain ASCII literal characters", file, ln
                )
            units.append(cp)
        return bytes(units)
    return bytes(unescape_units(sv, True, file, ln))


def clean_line(lines, li):
    line = lines[li]
    if line.endswith("\r"):
        line = line[:-1]
    return line


def scan_fstring_field(lines, li, col, file):
    """Scan a replacement field without confusing inner and outer quotes.

    Args:
        lines: Source lines.
        li: Zero-based starting line.
        col: Column immediately after the opening brace.
        file: Filename used for diagnostics.

    Returns:
        Raw field text and the position after its closing brace.
    """
    out = []
    depth = 0
    spec = False
    while li < len(lines):
        line = clean_line(lines, li)
        if col >= len(line):
            out.append("\n")
            li += 1
            col = 0
            continue
        char = line[col]
        if char == "}" and depth == 0:
            return "".join(out), li, col + 1
        if spec:
            if char == "{":
                text, li, col = scan_fstring_field(lines, li, col + 1, file)
                out.extend(["{", text, "}"])
                continue
        elif char in "'\"":
            quote = char * 3 if line[col:col + 3] == char * 3 else char
            prefix_start = col
            while prefix_start and line[prefix_start - 1] in "fFrR":
                prefix_start -= 1
            formatted = "f" in line[prefix_start:col].lower()
            text, li, col = scan_str(lines, li, col + len(quote), quote, file, formatted)
            out.extend([quote, text, quote])
            continue
        elif char == "#":
            out.append(line[col:])
            col = len(line)
            continue
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == ":" and depth == 0:
            spec = True
        out.append(char)
        col += 1
    raise CompileError("syntax error: expected '}' in f-string", file, li)


def scan_str(lines, li, col, quote, file, formatted=False):
    """Return raw string text and the position after its closing quote."""
    start_ln = li + 1
    triple = len(quote) == 3
    q = quote[0]
    out = []
    line = clean_line(lines, li)
    while True:
        if col >= len(line):
            if not triple:
                raise CompileError("syntax error: unterminated string literal", file, li + 1)
            li += 1
            if li >= len(lines):
                raise CompileError(
                    "syntax error: unterminated triple-quoted string", file, start_ln
                )
            out.append("\n")
            line = clean_line(lines, li)
            col = 0
            continue
        c = line[col]
        if formatted and c == "{":
            if line[col:col + 2] == "{{":
                out.append("{{")
                col += 2
            else:
                text, li, col = scan_fstring_field(lines, li, col + 1, file)
                out.extend(["{", text, "}"])
                line = clean_line(lines, li)
            continue
        if c == "\\":
            if col + 1 >= len(line):
                out.append("\\")
                out.append("\n")
                li += 1
                if li >= len(lines):
                    raise CompileError("syntax error: unterminated string literal", file, start_ln)
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
            if line[col : col + 3] == quote:
                return ("".join(out), li, col + 3)
            out.append(c)
            col += 1
            continue
        out.append(c)
        col += 1


def source_positions(text, line=1, column=0):
    """Map character boundaries to line numbers and UTF-8 byte columns."""
    positions = [(line, column)]
    for char in text:
        if char == "\n":
            line += 1
            column = 0
        else:
            code = ord(char)
            column += 1 if code < 128 else (2 if code < 2048 else (3 if code < 65536 else 4))
        positions.append((line, column))
    return positions


def source_span(node, start, end):
    node["lineno"] = start[0]
    node["col_offset"] = start[1]
    node["end_lineno"] = end[0]
    node["end_col_offset"] = end[1]
    return node


def tokenize(src, file):
    lines = src.split("\n")
    columns = [source_positions(line) for line in lines]
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
                toks.append(("ind", "", li + 1, None, 0, li + 1, columns[li][col][1]))
            else:
                while ind < indents[-1]:
                    indents.pop()
                    toks.append(
                        ("ded", "", li + 1, None, columns[li][col][1], li + 1, columns[li][col][1])
                    )
                if ind != indents[-1]:
                    raise CompileError(
                        "syntax error: unindent does not match any outer indentation level",
                        file,
                        li + 1,
                    )
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
            start_col = columns[li][col][1]
            seg = line[col:]
            if c == "'" or c == '"' or c in "rRbBuUfF":
                prefix_end = 0
                while prefix_end < min(3, len(seg)) and seg[prefix_end] in "rRbBuUfF":
                    prefix_end += 1
                if prefix_end < len(seg) and seg[prefix_end] in "'\"":
                    prefix = seg[:prefix_end].lower()
                    quote = seg[prefix_end]
                    if seg[prefix_end : prefix_end + 3] == quote * 3:
                        quote = quote * 3
                    if prefix not in VALID_PREFIXES:
                        raise CompileError("syntax error: invalid string prefix", file, ln)
                    r = scan_str(
                        lines, li, col + prefix_end + len(quote), quote, file, "f" in prefix
                    )
                    raw = r[0]
                    is_raw = "r" in prefix
                    if "f" in prefix:
                        toks.append(
                            (
                                "fstr",
                                (raw, is_raw, start_col + prefix_end + len(quote)),
                                ln,
                                None,
                                start_col,
                                r[1] + 1,
                                columns[r[1]][r[2]][1],
                            )
                        )
                    elif "b" in prefix:
                        toks.append(
                            (
                                "bytes",
                                decode_bytes(raw, is_raw, file, ln),
                                ln,
                                None,
                                start_col,
                                r[1] + 1,
                                columns[r[1]][r[2]][1],
                            )
                        )
                    else:
                        toks.append(
                            (
                                "str",
                                decode_str(raw, is_raw, file, ln),
                                ln,
                                "u" if seg[:prefix_end] == "u" else None,
                                start_col,
                                r[1] + 1,
                                columns[r[1]][r[2]][1],
                            )
                        )
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
                if (
                    col < len(line)
                    and line[col] in "jJ"
                    and text[:2] not in ("0x", "0X", "0o", "0O", "0b", "0B")
                ):
                    text = text + line[col]
                    col += 1
                toks.append(("num", text, ln, None, start_col, ln, columns[li][col][1]))
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
                    toks.append(("kw", word, ln, None, start_col, ln, columns[li][col][1]))
                else:
                    toks.append(("nm", word, ln, None, start_col, ln, columns[li][col][1]))
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
            toks.append(("op", op, ln, None, start_col, ln, start_col + len(op)))
            had = True
            col += len(op)
        if cont:
            li += 1
            continue
        if depth == 0 and had:
            toks.append(("nl", "", li + 1, None, columns[li][col][1], li + 1, columns[li][col][1]))
            had = False
        li += 1
    if had:
        toks.append(("nl", "", nlines, None, columns[-1][-1][1], nlines, columns[-1][-1][1]))
    while len(indents) > 1:
        indents.pop()
        toks.append(("ded", "", nlines, None, columns[-1][-1][1], nlines, columns[-1][-1][1]))
    toks.append(("end", "", nlines, None, columns[-1][-1][1], nlines, columns[-1][-1][1]))
    return toks


def num_const(text, ln, file):
    if text.endswith("j") or text.endswith("J"):
        return {"t": "Const", "kind": "complex", "v": float(text[:-1].replace("_", "")), "ln": ln}
    p2 = text[:2]
    if p2 == "0x" or p2 == "0X":
        return {"t": "Const", "kind": "int", "v": int(text[2:].replace("_", ""), 16), "ln": ln}
    if p2 == "0o" or p2 == "0O":
        return {"t": "Const", "kind": "int", "v": int(text[2:].replace("_", ""), 8), "ln": ln}
    if p2 == "0b" or p2 == "0B":
        return {"t": "Const", "kind": "int", "v": int(text[2:].replace("_", ""), 2), "ln": ln}
    if "." in text or "e" in text or "E" in text:
        return {"t": "Const", "kind": "float", "v": float(text.replace("_", "")), "ln": ln}
    return {"t": "Const", "kind": "int", "v": int(text.replace("_", "")), "ln": ln}


def fix_lines(nd, delta, column=0):
    if isinstance(nd, dict):
        if "ln" in nd and isinstance(nd["ln"], int):
            nd["ln"] = nd["ln"] + delta
        if "lineno" in nd:
            if nd["lineno"] == 1:
                nd["col_offset"] += column
            nd["lineno"] += delta
        if "end_lineno" in nd:
            if nd["end_lineno"] == 1:
                nd["end_col_offset"] += column
            nd["end_lineno"] += delta
        for k in nd:
            fix_lines(nd[k], delta, column)
    elif isinstance(nd, list):
        for x in nd:
            fix_lines(x, delta, column)


class Parser:
    def __init__(self, toks, file):
        self.toks = toks
        self.i = 0
        self.file = file

    def located(self, node, start, end=None):
        if end is None:
            end = self.i
        while end > start:
            token = self.toks[end - 1]
            if token[0] not in ("nl", "ind", "ded", "end"):
                break
            end -= 1
        first = self.toks[start]
        last = self.toks[end - 1]
        node["lineno"] = first[2]
        node["col_offset"] = first[4]
        node["end_lineno"] = last[5]
        node["end_col_offset"] = last[6]
        return node

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
            return t[1] in ("not", "lambda", "None", "True", "False", "yield", "await")
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
        start = self.i
        ln = self.ln()
        self.adv()
        test = self.namedexpr_test()
        body = self.suite()
        orelse = []
        if self.at_kw("elif"):
            orelse = [self.if_stmt()]
        elif self.eat_kw("else"):
            orelse = self.suite()
        return self.located(
            {"t": "If", "test": test, "then": body, "orelse": orelse, "ln": ln}, start
        )

    def while_stmt(self):
        start = self.i
        ln = self.ln()
        self.adv()
        test = self.namedexpr_test()
        body = self.suite()
        orelse = []
        if self.eat_kw("else"):
            orelse = self.suite()
        return self.located(
            {"t": "While", "test": test, "body": body, "orelse": orelse, "ln": ln}, start
        )

    def for_stmt(self):
        start = self.i
        ln = self.ln()
        self.adv()
        target = self.for_targets()
        self.req_kw("in")
        it = self.testlist_star()
        body = self.suite()
        orelse = []
        if self.eat_kw("else"):
            orelse = self.suite()
        return self.located(
            {"t": "For", "target": target, "iter": it, "body": body, "orelse": orelse, "ln": ln},
            start,
        )

    def try_stmt(self):
        start = self.i
        ln = self.ln()
        self.adv()
        body = self.suite()
        handlers = []
        star = False
        while self.at_kw("except"):
            handler_start = self.i
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
            handlers.append(
                self.located({"type": htype, "name": hname, "body": hbody}, handler_start)
            )
        orelse = []
        if handlers and self.at_kw("else"):
            self.adv()
            orelse = self.suite()
        final = []
        if self.eat_kw("finally"):
            final = self.suite()
        if not handlers and not final:
            raise self.perr("expected 'except' or 'finally' block")
        return self.located(
            {
                "t": "Try",
                "body": body,
                "handlers": handlers,
                "orelse": orelse,
                "final": final,
                "star": star,
                "ln": ln,
            },
            start,
        )

    def with_stmt(self):
        start = self.i
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
        return self.located({"t": "With", "items": items, "body": body, "ln": ln}, start)

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
        start = self.i
        self.adv()
        if self.at_kw("def"):
            node = self.def_stmt(decs)
            node["coro"] = True
            return self.located(node, start)
        if decs:
            raise self.perr("expected 'def' after decorators")
        if self.at_kw("for"):
            node = self.for_stmt()
            node["t"] = "AsyncFor"
            return self.located(node, start)
        if self.at_kw("with"):
            node = self.with_stmt()
            node["t"] = "AsyncWith"
            return self.located(node, start)
        raise self.perr("expected 'def', 'for' or 'with' after 'async'")

    def def_stmt(self, decs):
        start = self.i
        ln = self.ln()
        self.adv()
        name = self.req_name()
        type_params = self.type_params()
        self.req_op("(")
        params = self.param_list(True, ")")
        self.req_op(")")
        returns = None
        if self.eat_op("->"):
            returns = self.test()
        body = self.suite()
        return self.located(
            {
                "t": "Func",
                "name": name,
                "params": params,
                "body": body,
                "decorators": decs,
                "coro": False,
                "returns": returns,
                "type_params": type_params,
                "ln": ln,
            },
            start,
        )

    def class_stmt(self, decs):
        start = self.i
        ln = self.ln()
        self.adv()
        name = self.req_name()
        type_params = self.type_params()
        bases = []
        keywords = []
        if self.eat_op("("):
            while not self.at_op(")"):
                item_start = self.i
                if self.at_op("**"):
                    self.adv()
                    keywords.append(self.located({"n": None, "v": self.test()}, item_start))
                elif self.at_op("*"):
                    sln = self.ln()
                    self.adv()
                    bases.append(
                        self.located({"t": "Starred", "v": self.test(), "ln": sln}, item_start)
                    )
                elif self.kind() == "nm" and self.peek2()[0] == "op" and self.peek2()[1] == "=":
                    kwname = self.adv()[1]
                    self.adv()
                    keywords.append(self.located({"n": kwname, "v": self.test()}, item_start))
                else:
                    bases.append(self.test())
                if not self.eat_op(","):
                    break
            self.req_op(")")
        body = self.suite()
        return self.located(
            {
                "t": "Class",
                "name": name,
                "bases": bases,
                "keywords": keywords,
                "body": body,
                "decorators": decs,
                "type_params": type_params,
                "ln": ln,
            },
            start,
        )

    def type_params(self):
        """Parse an optional type parameter list.

        Returns:
            Located TypeVar, ParamSpec, and TypeVarTuple records.
        """
        params = []
        if not self.eat_op("["):
            return params
        names = set()
        while True:
            start = self.i
            tag = "TypeVar"
            if self.eat_op("**"):
                tag = "ParamSpec"
            elif self.eat_op("*"):
                tag = "TypeVarTuple"
            name = self.req_name()
            if name in names:
                raise self.perr("duplicate type parameter '" + name + "'")
            names.add(name)
            bound = None
            if tag == "TypeVar" and self.eat_op(":"):
                bound = self.test()
            params.append(self.located(
                {"t": tag, "name": name, "bound": bound, "ln": self.toks[start][2]}, start
            ))
            if not self.eat_op(",") or self.at_op("]"):
                break
        self.req_op("]")
        return params

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
                    arg_start = self.i
                    vararg = self.adv()[1]
                    argnodes[vararg] = {"annotation": None, "ln": self.toks[self.i - 1][2]}
                    if annotations and self.eat_op(":"):
                        argnodes[vararg]["annotation"] = self.test()
                    self.located(argnodes[vararg], arg_start)
                kwsec = True
            elif self.eat_op("**"):
                arg_start = self.i
                kwarg = self.req_name()
                argnodes[kwarg] = {"annotation": None, "ln": self.toks[self.i - 1][2]}
                if annotations and self.eat_op(":"):
                    argnodes[kwarg]["annotation"] = self.test()
                self.located(argnodes[kwarg], arg_start)
            elif self.kind() == "nm":
                arg_start = self.i
                nm = self.adv()[1]
                argnodes[nm] = {"annotation": None, "ln": self.toks[self.i - 1][2]}
                if annotations and self.eat_op(":"):
                    argnodes[nm]["annotation"] = self.test()
                self.located(argnodes[nm], arg_start)
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
        return {
            "args": args,
            "defaults": defaults,
            "vararg": vararg,
            "kwonly": kwonly,
            "kwdefaults": kwdefaults,
            "kwarg": kwarg,
            "posonly": posonly,
            "argnodes": argnodes,
        }

    def small_stmt(self):
        start = self.i
        k = self.kind()
        v = self.val()
        ln = self.ln()
        if k == "nm" and v == "type" and self.peek2()[0] == "nm":
            self.adv()
            name_start = self.i
            name = self.req_name()
            target = self.located({"t": "Name", "id": name, "ln": ln}, name_start)
            params = self.type_params()
            self.req_op("=")
            value = self.test()
            return self.located(
                {"t": "TypeAlias", "name": target, "type_params": params, "v": value, "ln": ln},
                start,
            )
        if k == "kw":
            if v == "return":
                self.adv()
                if self.starts_expr():
                    return self.located({"t": "Return", "v": self.testlist_star(), "ln": ln}, start)
                return self.located({"t": "Return", "v": None, "ln": ln}, start)
            if v == "pass":
                self.adv()
                return self.located({"t": "Pass", "ln": ln}, start)
            if v == "break":
                self.adv()
                return self.located({"t": "Break", "ln": ln}, start)
            if v == "continue":
                self.adv()
                return self.located({"t": "Continue", "ln": ln}, start)
            if v == "raise":
                self.adv()
                exc = None
                cause = None
                if self.starts_expr():
                    exc = self.test()
                    if self.eat_kw("from"):
                        cause = self.test()
                return self.located({"t": "Raise", "exc": exc, "cause": cause, "ln": ln}, start)
            if v == "global" or v == "nonlocal":
                self.adv()
                names = [self.req_name()]
                while self.eat_op(","):
                    names.append(self.req_name())
                if v == "global":
                    return self.located({"t": "Global", "names": names, "ln": ln}, start)
                return self.located({"t": "Nonlocal", "names": names, "ln": ln}, start)
            if v == "del":
                self.adv()
                targets = [self.test()]
                while self.eat_op(","):
                    if not self.starts_expr():
                        break
                    targets.append(self.test())
                return self.located({"t": "Del", "targets": targets, "ln": ln}, start)
            if v == "assert":
                self.adv()
                test = self.test()
                msg = None
                if self.eat_op(","):
                    msg = self.test()
                return self.located({"t": "Assert", "test": test, "msg": msg, "ln": ln}, start)
            if v == "import":
                self.adv()
                names = []
                while True:
                    alias_start = self.i
                    mod = self.dotted_name()
                    asname = None
                    if self.eat_kw("as"):
                        asname = self.req_name()
                    names.append(self.located({"n": mod, "as": asname}, alias_start))
                    if not self.eat_op(","):
                        break
                return self.located({"t": "Import", "names": names, "ln": ln}, start)
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
                    names.append(self.located({"n": "*", "as": None}, self.i - 1))
                else:
                    while True:
                        alias_start = self.i
                        nm = self.req_name()
                        asname = None
                        if self.eat_kw("as"):
                            asname = self.req_name()
                        names.append(self.located({"n": nm, "as": asname}, alias_start))
                        if not self.eat_op(","):
                            break
                        if paren and self.at_op(")"):
                            break
                if paren:
                    self.req_op(")")
                return self.located(
                    {"t": "ImportFrom", "module": mod, "names": names, "level": level, "ln": ln},
                    start,
                )
            if v == "yield":
                y = self.yield_expr()
                return self.located({"t": "Expr", "v": y, "ln": ln}, start)
        first = self.testlist_star()
        if self.at_op("="):
            values = [first]
            while self.eat_op("="):
                if self.at_kw("yield"):
                    values.append(self.yield_expr())
                else:
                    values.append(self.testlist_star())
            return self.located(
                {"t": "Assign", "targets": values[:-1], "v": values[-1], "ln": ln}, start
            )
        if self.kind() == "op" and self.val() in AUGOPS:
            op = self.adv()[1]
            if self.at_kw("yield"):
                rhs = self.yield_expr()
            else:
                rhs = self.testlist_star()
            return self.located(
                {"t": "Aug", "target": first, "kind": AUGOPS[op], "v": rhs, "ln": ln}, start
            )
        if self.at_op(":"):
            self.adv()
            annotation = self.test()
            v2 = None
            if self.eat_op("="):
                if self.at_kw("yield"):
                    v2 = self.yield_expr()
                else:
                    v2 = self.testlist_star()
            return self.located(
                {
                    "t": "Ann",
                    "target": first,
                    "annotation": annotation,
                    "simple": first["t"] == "Name" and k == "nm",
                    "v": v2,
                    "ln": ln,
                },
                start,
            )
        return self.located({"t": "Expr", "v": first, "ln": ln}, start)

    def dotted_name(self):
        parts = [self.req_name()]
        while self.at_op(".") and self.peek2()[0] == "nm":
            self.adv()
            parts.append(self.req_name())
        return ".".join(parts)

    def match_stmt(self):
        start = self.i
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
        return self.located({"t": "Match", "subject": subject, "cases": cases, "ln": ln}, start)

    def patterns(self):
        start = self.i
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
        return self.located({"t": "MSeq", "pats": items, "ln": first["ln"]}, start)

    def pattern_or_star(self):
        start = self.i
        if self.at_op("*"):
            ln = self.ln()
            self.adv()
            nm = self.req_name()
            if nm == "_":
                return self.located({"t": "MStar", "name": None, "ln": ln}, start)
            return self.located({"t": "MStar", "name": nm, "ln": ln}, start)
        return self.pattern()

    def pattern(self):
        start = self.i
        p = self.or_pattern()
        while self.at_kw("as"):
            self.adv()
            nm = self.req_name()
            p = self.located({"t": "MAs", "pat": p, "name": nm, "ln": p["ln"]}, start)
        return p

    def or_pattern(self):
        start = self.i
        p = self.closed_pattern()
        if not self.at_op("|"):
            return p
        pats = [p]
        while self.eat_op("|"):
            pats.append(self.closed_pattern())
        return self.located({"t": "MOr", "pats": pats, "ln": p["ln"]}, start)

    def literal_key_expr(self):
        """Parse a literal or dotted name for a mapping key or value pattern."""
        start = self.i
        k = self.kind()
        v = self.val()
        ln = self.ln()
        if self.at_op("-"):
            self.adv()
            numtok = self.adv()
            if numtok[0] != "num":
                raise self.perr("invalid pattern")
            c = self.located(num_const(numtok[1], numtok[2], self.file), self.i - 1)
            return self.located({"t": "Unary", "kind": "neg", "v": c, "ln": ln}, start)
        if k == "num":
            self.adv()
            return self.located(num_const(v, ln, self.file), start)
        if k in ("str", "bytes", "fstr"):
            return self.strings()
        if k == "kw" and v in ("None", "True", "False"):
            self.adv()
            if v == "None":
                return self.located({"t": "Const", "kind": "none", "v": None, "ln": ln}, start)
            return self.located({"t": "Const", "kind": "bool", "v": v == "True", "ln": ln}, start)
        if k == "nm":
            e = self.located({"t": "Name", "id": self.adv()[1], "ln": ln}, start)
            while self.at_op("."):
                self.adv()
                e = self.located({"t": "Attr", "o": e, "n": self.req_name(), "ln": ln}, start)
            return e
        raise self.perr("invalid pattern key")

    def closed_pattern(self):
        start = self.i
        k = self.kind()
        v = self.val()
        ln = self.ln()
        if k == "op" and v == "(":
            self.adv()
            if self.eat_op(")"):
                return self.located({"t": "MSeq", "pats": [], "ln": ln}, start)
            first = self.pattern_or_star()
            if self.at_op(","):
                items = [first]
                while self.eat_op(","):
                    if self.at_op(")"):
                        break
                    items.append(self.pattern_or_star())
                self.req_op(")")
                return self.located({"t": "MSeq", "pats": items, "ln": ln}, start)
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
            return self.located({"t": "MSeq", "pats": items, "ln": ln}, start)
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
            return self.located(
                {"t": "MMap", "keys": keys, "pats": pats, "rest": rest, "ln": ln}, start
            )
        if k == "op" and v == "-":
            e = self.literal_key_expr()
            return self.located({"t": "MVal", "v": e, "ln": ln}, start)
        if k == "num":
            e = self.literal_key_expr()
            return self.located({"t": "MVal", "v": e, "ln": ln}, start)
        if k in ("str", "bytes", "fstr"):
            e = self.strings()
            if e["t"] != "Const":
                raise self.perr("patterns may only match literals")
            return self.located({"t": "MVal", "v": e, "ln": ln}, start)
        if k == "kw" and v in ("None", "True", "False"):
            self.adv()
            if v == "None":
                c = self.located({"t": "Const", "kind": "none", "v": None, "ln": ln}, start)
            else:
                c = self.located({"t": "Const", "kind": "bool", "v": v == "True", "ln": ln}, start)
            return self.located({"t": "MSing", "c": c, "ln": ln}, start)
        if k == "nm":
            nxt = self.peek2()
            if v == "_" and not (nxt[0] == "op" and (nxt[1] == "." or nxt[1] == "(")):
                self.adv()
                return self.located({"t": "MAs", "pat": None, "name": None, "ln": ln}, start)
            if nxt[0] == "op" and (nxt[1] == "." or nxt[1] == "("):
                e = self.located({"t": "Name", "id": self.adv()[1], "ln": ln}, start)
                while self.at_op("."):
                    self.adv()
                    e = self.located({"t": "Attr", "o": e, "n": self.req_name(), "ln": ln}, start)
                if self.at_op("("):
                    self.adv()
                    pos = []
                    attrs = []
                    kpats = []
                    while not self.at_op(")"):
                        if (
                            self.kind() == "nm"
                            and self.peek2()[0] == "op"
                            and self.peek2()[1] == "="
                        ):
                            attr = self.adv()[1]
                            if attr in attrs:
                                raise self.perr("attribute name repeated in class pattern: " + attr)
                            attrs.append(attr)
                            self.adv()
                            kpats.append(self.pattern())
                        else:
                            if attrs:
                                raise self.perr("positional patterns follow keyword patterns")
                            pos.append(self.pattern())
                        if not self.eat_op(","):
                            break
                    self.req_op(")")
                    return self.located(
                        {
                            "t": "MCls",
                            "cls": e,
                            "pos": pos,
                            "attrs": attrs,
                            "kpats": kpats,
                            "ln": ln,
                        },
                        start,
                    )
                return self.located({"t": "MVal", "v": e, "ln": ln}, start)
            self.adv()
            return self.located({"t": "MAs", "pat": None, "name": v, "ln": ln}, start)
        raise self.perr("invalid match pattern")

    def namedexpr_test(self):
        start = self.i
        if self.kind() == "nm":
            nxt = self.peek2()
            if nxt[0] == "op" and nxt[1] == ":=":
                ln = self.ln()
                name = self.adv()[1]
                self.adv()
                target = self.located({"t": "Name", "id": name, "ln": ln}, start, start + 1)
                return self.located(
                    {"t": "Named", "target": target, "v": self.test(), "ln": ln}, start
                )
        return self.test()

    def testlist_star(self):
        start = self.i
        first = self.star_or_test()
        if not self.at_op(","):
            return first
        elts = [first]
        while self.eat_op(","):
            if not self.starts_expr():
                break
            elts.append(self.star_or_test())
        return self.located({"t": "Tuple", "elts": elts, "ln": first["ln"]}, start)

    def star_or_test(self):
        start = self.i
        if self.at_op("*"):
            ln = self.ln()
            self.adv()
            return self.located({"t": "Starred", "v": self.or_expr_chain(), "ln": ln}, start)
        return self.test()

    def or_expr_chain(self):
        return self.bitor_()

    def test(self):
        start = self.i
        if self.at_kw("lambda"):
            return self.lambda_expr()
        v = self.or_test()
        if self.at_kw("if"):
            self.adv()
            cond = self.or_test()
            self.req_kw("else")
            els = self.test()
            return self.located(
                {"t": "Cond", "test": cond, "then": v, "else": els, "ln": v["ln"]}, start
            )
        return v

    def lambda_expr(self):
        start = self.i
        ln = self.ln()
        self.adv()
        params = self.param_list(False, ":")
        self.req_op(":")
        body = self.test()
        return self.located({"t": "Lambda", "params": params, "body": body, "ln": ln}, start)

    def or_test(self):
        start = self.i
        v = self.and_test()
        if not self.at_kw("or"):
            return v
        values = [v]
        while self.eat_kw("or"):
            values.append(self.and_test())
        return self.located({"t": "Bool", "kind": "or", "values": values, "ln": v["ln"]}, start)

    def and_test(self):
        start = self.i
        v = self.not_test()
        if not self.at_kw("and"):
            return v
        values = [v]
        while self.eat_kw("and"):
            values.append(self.not_test())
        return self.located({"t": "Bool", "kind": "and", "values": values, "ln": v["ln"]}, start)

    def not_test(self):
        start = self.i
        if self.at_kw("not"):
            ln = self.ln()
            self.adv()
            return self.located(
                {"t": "Unary", "kind": "not", "v": self.not_test(), "ln": ln}, start
            )
        return self.comparison()

    def comparison(self):
        start = self.i
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
        return self.located(
            {"t": "Cmp", "left": v, "ops": ops, "comps": comps, "ln": v["ln"]}, start
        )

    def bitor_(self):
        start = self.i
        v = self.bitxor_()
        while self.at_op("|"):
            self.adv()
            v = self.located(
                {"t": "BinOp", "kind": "bitor", "l": v, "r": self.bitxor_(), "ln": v["ln"]}, start
            )
        return v

    def bitxor_(self):
        start = self.i
        v = self.bitand_()
        while self.at_op("^"):
            self.adv()
            v = self.located(
                {"t": "BinOp", "kind": "bitxor", "l": v, "r": self.bitand_(), "ln": v["ln"]}, start
            )
        return v

    def bitand_(self):
        start = self.i
        v = self.shift_()
        while self.at_op("&"):
            self.adv()
            v = self.located(
                {"t": "BinOp", "kind": "bitand", "l": v, "r": self.shift_(), "ln": v["ln"]}, start
            )
        return v

    def shift_(self):
        start = self.i
        v = self.arith_()
        while self.at_op("<<") or self.at_op(">>"):
            kind = "lshift" if self.val() == "<<" else "rshift"
            self.adv()
            v = self.located(
                {"t": "BinOp", "kind": kind, "l": v, "r": self.arith_(), "ln": v["ln"]}, start
            )
        return v

    def arith_(self):
        start = self.i
        v = self.term_()
        while self.at_op("+") or self.at_op("-"):
            kind = "add" if self.val() == "+" else "sub"
            self.adv()
            v = self.located(
                {"t": "BinOp", "kind": kind, "l": v, "r": self.term_(), "ln": v["ln"]}, start
            )
        return v

    def term_(self):
        start = self.i
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
            v = self.located(
                {"t": "BinOp", "kind": kind, "l": v, "r": self.factor(), "ln": v["ln"]}, start
            )
        return v

    def factor(self):
        start = self.i
        k = self.kind()
        v = self.val()
        if k == "op" and v in ("+", "-", "~"):
            ln = self.ln()
            self.adv()
            kind = "pos" if v == "+" else ("neg" if v == "-" else "invert")
            return self.located({"t": "Unary", "kind": kind, "v": self.factor(), "ln": ln}, start)
        return self.power()

    def power(self):
        start = self.i
        if self.at_kw("await"):
            ln = self.ln()
            self.adv()
            b = self.located({"t": "Await", "v": self.postfix(), "ln": ln}, start)
        else:
            b = self.postfix()
        if self.eat_op("**"):
            return self.located(
                {"t": "BinOp", "kind": "pow", "l": b, "r": self.factor(), "ln": b["ln"]}, start
            )
        return b

    def postfix(self):
        start = self.i
        v = self.atom()
        while True:
            if self.at_op("("):
                v = self.call_trailer(v, start)
            elif self.at_op("."):
                self.adv()
                n = self.req_name()
                v = self.located({"t": "Attr", "o": v, "n": n, "ln": v["ln"]}, start)
            elif self.at_op("["):
                v = self.sub_trailer(v, start)
            else:
                break
        return v

    def call_trailer(self, f, start):
        lp_start = self.i
        generator = None
        lp_ln = self.ln()
        self.adv()
        args = []
        kw = []
        first = True
        while not self.at_op(")"):
            item_start = self.i
            if self.at_op("*") and not self.at_op("**"):
                sln = self.ln()
                self.adv()
                args.append(self.located({"t": "Starred", "v": self.test(), "ln": sln}, item_start))
            elif self.at_op("**"):
                self.adv()
                kw.append(self.located({"n": None, "v": self.test()}, item_start))
            elif self.kind() == "nm" and self.peek2()[0] == "op" and self.peek2()[1] == "=":
                name = self.adv()[1]
                self.adv()
                kw.append(self.located({"n": name, "v": self.test()}, item_start))
            else:
                e = self.namedexpr_test()
                if first and not kw and self.at_kw("for"):
                    gens = self.comp_clauses()
                    generator = {"t": "Comp", "kind": "gen", "elt": e, "gens": gens, "ln": lp_ln}
                    args.append(generator)
                    break
                args.append(e)
            first = False
            if not self.eat_op(","):
                break
        self.req_op(")")
        if generator is not None:
            self.located(generator, lp_start)
        return self.located({"t": "Call", "f": f, "args": args, "kw": kw, "ln": f["ln"]}, start)

    def sub_trailer(self, o, start):
        lb_ln = self.ln()
        self.adv()
        item_start = self.i
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
        if len(items) == 1 and not sawcomma and items[0]["t"] != "Starred":
            k = items[0]
        else:
            k = self.located({"t": "Tuple", "elts": items, "ln": lb_ln}, item_start, self.i - 1)
        return self.located({"t": "Sub", "o": o, "k": k, "ln": o["ln"]}, start)

    def sub_item(self):
        start = self.i
        if self.eat_op("*"):
            value = self.bitor_()
            return self.located(
                {"t": "Starred", "v": value, "ln": self.toks[start][2]}, start
            )
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
        return self.located(
            {"t": "Slice", "lower": lower, "upper": upper, "step": step, "ln": ln}, start
        )

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
                elif self.kind() == "nm" and self.peek2()[0] == "op" and self.peek2()[1] == ":=":
                    ifs.append(self.namedexpr_test())
                else:
                    ifs.append(self.or_test())
            gens.append({"target": tgt, "iter": it, "ifs": ifs})
        return gens

    def for_targets(self):
        start = self.i
        first = self.t_item()
        if not self.at_op(","):
            return first
        elts = [first]
        while self.eat_op(","):
            if not self.starts_expr():
                break
            elts.append(self.t_item())
        return self.located({"t": "Tuple", "elts": elts, "ln": first["ln"]}, start)

    def t_item(self):
        start = self.i
        if self.at_op("*"):
            ln = self.ln()
            self.adv()
            return self.located({"t": "Starred", "v": self.postfix(), "ln": ln}, start)
        return self.postfix()

    def yield_expr(self):
        start = self.i
        ln = self.ln()
        self.adv()
        if self.eat_kw("from"):
            return self.located({"t": "YieldFrom", "v": self.test(), "ln": ln}, start)
        if self.starts_expr():
            return self.located({"t": "Yield", "v": self.testlist_star(), "ln": ln}, start)
        return self.located({"t": "Yield", "v": None, "ln": ln}, start)

    def paren_item(self):
        start = self.i
        if self.at_op("*"):
            ln = self.ln()
            self.adv()
            return self.located({"t": "Starred", "v": self.or_expr_chain(), "ln": ln}, start)
        return self.namedexpr_test()

    def atom(self):
        start = self.i
        t = self.toks[self.i]
        k = t[0]
        v = t[1]
        ln = t[2]
        if k == "nm":
            self.adv()
            return self.located({"t": "Name", "id": v, "ln": ln}, start)
        if k == "num":
            self.adv()
            return self.located(num_const(v, ln, self.file), start)
        if k in ("str", "bytes", "fstr"):
            return self.strings()
        if k == "kw":
            if v == "True" or v == "False":
                self.adv()
                return self.located(
                    {"t": "Const", "kind": "bool", "v": v == "True", "ln": ln}, start
                )
            if v == "None":
                self.adv()
                return self.located({"t": "Const", "kind": "none", "v": None, "ln": ln}, start)
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
                return self.located({"t": "Const", "kind": "ellipsis", "v": None, "ln": ln}, start)
        raise self.perr("unexpected token")

    def paren_atom(self):
        start = self.i
        ln0 = self.ln()
        self.adv()
        if self.eat_op(")"):
            return self.located({"t": "Tuple", "elts": [], "ln": ln0}, start)
        if self.at_kw("yield"):
            y = self.yield_expr()
            self.req_op(")")
            return y
        first = self.paren_item()
        if self.at_kw("for"):
            gens = self.comp_clauses()
            self.req_op(")")
            return self.located(
                {"t": "Comp", "kind": "gen", "elt": first, "gens": gens, "ln": ln0}, start
            )
        if self.at_op(","):
            elts = [first]
            while self.eat_op(","):
                if self.at_op(")"):
                    break
                elts.append(self.paren_item())
            self.req_op(")")
            return self.located({"t": "Tuple", "elts": elts, "ln": ln0}, start)
        self.req_op(")")
        return first

    def list_atom(self):
        start = self.i
        ln0 = self.ln()
        self.adv()
        if self.eat_op("]"):
            return self.located({"t": "List", "elts": [], "ln": ln0}, start)
        first = self.paren_item()
        if self.at_kw("for"):
            gens = self.comp_clauses()
            self.req_op("]")
            return self.located(
                {"t": "Comp", "kind": "list", "elt": first, "gens": gens, "ln": ln0}, start
            )
        elts = [first]
        while self.eat_op(","):
            if self.at_op("]"):
                break
            elts.append(self.paren_item())
        self.req_op("]")
        return self.located({"t": "List", "elts": elts, "ln": ln0}, start)

    def brace_atom(self):
        start = self.i
        ln0 = self.ln()
        self.adv()
        if self.eat_op("}"):
            return self.located({"t": "Dict", "entries": [], "ln": ln0}, start)
        if self.at_op("**"):
            self.adv()
            entries = [{"k": None, "v": self.or_expr_chain()}]
            return self.dict_rest(entries, ln0, start)
        first = self.paren_item()
        if self.at_op(":"):
            self.adv()
            val = self.test()
            if self.at_kw("for"):
                gens = self.comp_clauses()
                self.req_op("}")
                return self.located(
                    {
                        "t": "Comp",
                        "kind": "dict",
                        "key": first,
                        "val": val,
                        "gens": gens,
                        "ln": ln0,
                    },
                    start,
                )
            entries = [{"k": first, "v": val}]
            return self.dict_rest(entries, ln0, start)
        if self.at_kw("for"):
            gens = self.comp_clauses()
            self.req_op("}")
            return self.located(
                {"t": "Comp", "kind": "set", "elt": first, "gens": gens, "ln": ln0}, start
            )
        elts = [first]
        while self.eat_op(","):
            if self.at_op("}"):
                break
            elts.append(self.paren_item())
        self.req_op("}")
        return self.located({"t": "Set", "elts": elts, "ln": ln0}, start)

    def dict_rest(self, entries, ln0, start):
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
        return self.located({"t": "Dict", "entries": entries, "ln": ln0}, start)

    def strings(self):
        start = self.i
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
            return self.located({"t": "Const", "kind": "bytes", "v": total, "ln": ln}, start)
        if not has_f:
            return self.located(
                {
                    "t": "Const",
                    "kind": "str",
                    "v": "".join([p[1] for p in parts]),
                    "prefix": parts[0][3],
                    "ln": ln,
                },
                start,
            )
        values = []
        for p in parts:
            if p[0] == "str":
                self.append_literal(values, p[1], (p[2], p[4]), (p[5], p[6]), p[3])
            else:
                raw = p[1][0]
                is_raw = p[1][1]
                self.fstr_walk(raw, is_raw, p[2], values, p[1][2])
        return self.located({"t": "Fstr", "parts": values, "ln": ln}, start)

    def append_literal(self, values, text, start, end, prefix=None):
        if not text:
            return
        if values and "lit" in values[-1]:
            values[-1]["lit"] += text
            values[-1]["end_lineno"] = end[0]
            values[-1]["end_col_offset"] = end[1]
        else:
            values.append(source_span({"lit": text, "prefix": prefix}, start, end))

    def flush_lit(self, lit, is_raw, values, start, end):
        if lit:
            text = decode_str("".join(lit), is_raw, self.file, start[0])
            self.append_literal(values, text, start, end)

    def fstr_walk(self, raw, is_raw, ln0, values, column, in_spec=False):
        positions = source_positions(raw, ln0, column)
        i = 0
        n = len(raw)
        lit = []
        lit_start = 0
        while i < n:
            c = raw[i]
            if c == "{":
                if i + 1 < n and raw[i + 1] == "{" and not in_spec:
                    lit.append("{")
                    self.flush_lit(lit, is_raw, values, positions[lit_start], positions[i + 2])
                    lit = []
                    i += 2
                    lit_start = i
                    continue
                self.flush_lit(lit, is_raw, values, positions[lit_start], positions[i])
                lit = []
                field_start = i
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
                    elif c2 == "'" or c2 == '"':
                        i = self.skip_fexpr_str(raw, i, ln0)
                    elif c2 == "\\":
                        i += 2
                    elif depth == 0 and c2 == "!" and i + 1 < n and raw[i + 1] != "=":
                        break
                    elif depth == 0 and c2 == ":":
                        break
                    elif (
                        depth == 0
                        and c2 == "="
                        and (i == start or raw[i - 1] not in "!<>=:")
                        and (i + 1 == n or raw[i + 1] != "=")
                    ):
                        break
                    else:
                        i += 1
                expr_src = raw[start:i]
                debug = i < n and raw[i] == "="
                if debug:
                    i += 1
                    while i < n and raw[i] in " \t\r\n":
                        i += 1
                    self.append_literal(values, raw[start:i], positions[start], positions[i])
                conv = None
                if i < n and raw[i] == "!":
                    if i + 1 >= n:
                        raise CompileError("syntax error: bad f-string conversion", self.file, ln0)
                    conv = raw[i + 1]
                    i += 2
                spec_node = None
                if i < n and raw[i] == ":":
                    colon = i
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
                        elif c3 in "'\"" and d2:
                            i = self.skip_fexpr_str(raw, i, ln0)
                            continue
                        i += 1
                    spec_node = self.parse_spec(
                        raw[sstart:i], is_raw, positions[sstart], positions[colon], positions[i]
                    )
                if debug and conv is None and spec_node is None:
                    conv = "r"
                if i >= n or raw[i] != "}":
                    raise CompileError("syntax error: expected '}' in f-string", self.file, ln0)
                i += 1
                if not expr_src.strip():
                    raise CompileError(
                        "syntax error: f-string: valid expression required before '}'",
                        self.file,
                        ln0,
                    )
                node = self.parse_fexpr(expr_src, positions[start][0], positions[start][1])
                values.append(
                    source_span(
                        {"t": "FV", "v": node, "conv": conv, "spec": spec_node},
                        positions[field_start],
                        positions[i],
                    )
                )
                lit_start = i
            elif c == "}":
                if i + 1 < n and raw[i + 1] == "}" and not in_spec:
                    lit.append("}")
                    self.flush_lit(lit, is_raw, values, positions[lit_start], positions[i + 2])
                    lit = []
                    i += 2
                    lit_start = i
                    continue
                raise CompileError(
                    "syntax error: f-string: single '}' is not allowed", self.file, ln0
                )
            else:
                lit.append(c)
                i += 1
        self.flush_lit(lit, is_raw, values, positions[lit_start], positions[n])

    def skip_fexpr_str(self, raw, i, ln0):
        n = len(raw)
        q = raw[i]
        if raw[i : i + 3] == q * 3:
            close = q * 3
            i += 3
            while i < n:
                if raw[i] == "\\":
                    i += 2
                    continue
                if raw[i : i + 3] == close:
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
        raise CompileError("syntax error: unterminated string in f-string", self.file, ln0)

    def parse_spec(self, src, is_raw, origin, start, end):
        values = []
        self.fstr_walk(src, is_raw, origin[0], values, origin[1], True)
        if values and "lit" not in values[-1]:
            # The tokenizer emits an empty middle token after the last field.
            values.append(source_span({"lit": ""}, end, end))
        return source_span({"t": "Fstr", "parts": values, "ln": origin[0]}, start, end)

    def parse_fexpr(self, src, base_ln, base_col):
        toks = tokenize("(" + src + ")", self.file)
        p = Parser(toks, self.file)
        node = p.test()
        p.req_nl()
        if p.kind() != "end":
            raise p.perr("unexpected token in f-string expression")
        if node["lineno"] == 1 and node["col_offset"] == 0:
            # The grouping parentheses are tokenizer scaffolding, not source.
            p.located(node, 1, len(toks) - 3)
        fix_lines(node, base_ln - 1, base_col - 1)
        return node


def lower_class_pattern(cls, pos, attrs, kpats, subj, inner, ln, gensym, pattern):
    """Lower class matching identically for the native and self-hosted compilers."""

    def name(value):
        return {"op": "name", "id": value}

    def builtin(value):
        return {"op": "attr", "o": name(".builtins"), "n": value}

    def call(value, args):
        return {"op": "call", "f": builtin(value), "args": args, "kw": []}

    def text(value):
        return {"op": "const", "t": "str", "v": value}

    def number(value):
        return {"op": "const", "t": "int", "v": str(value)}

    def compare(kind, left, right):
        return {"op": "compare", "left": left, "ops": [kind], "comps": [right]}

    def assign(target, value):
        return {"op": "assign", "targets": [target], "v": value, "ln": ln}

    def branch(test, body, otherwise=None):
        return {
            "op": "if",
            "test": test,
            "then": body,
            "orelse": [] if otherwise is None else otherwise,
            "ln": ln,
        }

    def attempt(body, missing, success):
        return {
            "op": "try",
            "body": body,
            "handlers": [{"t": builtin("AttributeError"), "name": None, "body": missing}],
            "orelse": success,
            "finalbody": [],
            "ln": ln,
        }

    def fail(parts):
        formatted = []
        for part in parts:
            if isinstance(part, str):
                formatted.append({"s": part})
            else:
                formatted.append({"v": part, "conv": None, "spec": None})
        return {
            "op": "raise",
            "exc": call("TypeError", [{"op": "fstring", "parts": formatted}]),
            "ln": ln,
        }

    subject = name(gensym("cs"))
    klass = name(gensym("cc"))
    class_name = {"op": "attr", "o": klass, "n": "__name__"}
    match_args = name(gensym("ca")) if pos else None
    match_self = name(gensym("cm")) if pos else None
    allowed = name(gensym("cn")) if pos else None
    seen = []
    values = []
    accesses = []
    for i in range(len(pos) + len(attrs)):
        value = name(gensym("cv"))
        values.append(value)
        positional = i < len(pos)
        attr = name(gensym("ck")) if positional else text(attrs[i - len(pos)])
        steps = []
        if positional:
            steps.append(assign(attr, {"op": "index", "o": match_args, "k": number(i)}))
            steps.append(
                branch(
                    compare("isnot", call("type", [attr]), builtin("str")),
                    [
                        fail(
                            [
                                "__match_args__ elements must be strings (got ",
                                {"op": "attr", "o": call("type", [attr]), "n": "__name__"},
                                ")",
                            ]
                        ),
                    ],
                )
            )
        if seen:
            steps.append(
                branch(
                    compare("in", attr, {"op": "tuple", "elts": seen[:]}),
                    [
                        fail(
                            [
                                class_name,
                                "() got multiple sub-patterns for attribute ",
                                call("repr", [attr]),
                            ]
                        ),
                    ],
                )
            )
        accesses.append((positional, attr, value, steps))
        seen.append(attr)

    # Extract every attribute before matching subpatterns, as MATCH_CLASS does.
    chain = inner
    pats = pos + kpats
    for i in range(len(pats) - 1, -1, -1):
        chain = pattern(pats[i], values[i], chain)
    for positional, attr, value, steps in accesses[::-1]:
        value_expr = call("getattr", [subject, attr])
        if positional:
            steps = [branch(match_self, [assign(attr, {"op": "const", "t": "none"})], steps)]
            value_expr = {"op": "cond", "test": match_self, "then": subject, "else": value_expr}
        lookup = attempt([assign(value, value_expr)], [{"op": "pass", "ln": ln}], chain)
        chain = steps + [lookup]

    setup = []
    if pos:
        # Missing __match_args__ inherits the builtin match-self behavior.
        self_types = (
            "bool",
            "bytearray",
            "bytes",
            "dict",
            "float",
            "frozenset",
            "int",
            "list",
            "set",
            "str",
            "tuple",
        )
        setup = [
            assign(match_self, {"op": "const", "t": "bool", "v": False}),
            attempt(
                [assign(match_args, call("getattr", [klass, text("__match_args__")]))],
                [
                    assign(match_args, {"op": "tuple", "elts": []}),
                    assign(
                        match_self,
                        call(
                            "issubclass",
                            [
                                klass,
                                {"op": "tuple", "elts": [builtin(t) for t in self_types]},
                            ],
                        ),
                    ),
                ],
                [],
            ),
            branch(
                compare("isnot", call("type", [match_args]), builtin("tuple")),
                [
                    fail(
                        [
                            class_name,
                            ".__match_args__ must be a tuple (got ",
                            {"op": "attr", "o": call("type", [match_args]), "n": "__name__"},
                            ")",
                        ]
                    ),
                ],
            ),
            assign(
                allowed,
                {
                    "op": "cond",
                    "test": match_self,
                    "then": number(1),
                    "else": call("len", [match_args]),
                },
            ),
            branch(
                compare("lt", allowed, number(len(pos))),
                [
                    fail(
                        [
                            class_name,
                            "() accepts ",
                            allowed,
                            " positional sub-pattern",
                            {
                                "op": "cond",
                                "test": compare("eq", allowed, number(1)),
                                "then": text(""),
                                "else": text("s"),
                            },
                            " (" + str(len(pos)) + " given)",
                        ]
                    ),
                ],
            ),
        ]
    return [
        assign(subject, subj),
        assign(klass, cls),
        branch(
            {
                "op": "unary",
                "kind": "not",
                "v": call(
                    "issubclass",
                    [
                        call("type", [klass]),
                        builtin("type"),
                    ],
                ),
            },
            [fail(["called match pattern must be a class"])],
        ),
        branch(call("isinstance", [subject, klass]), setup + chain),
    ]


_NATIVE_ENCODING_NAMES = {
    "utf8",
    "utf_8",
    "u8",
    "utf",
    "cp65001",
    "ascii",
    "us_ascii",
    "646",
    "latin1",
    "latin_1",
    "latin",
    "iso8859_1",
    "iso_8859_1",
    "iso_8859_1_1987",
    "cp819",
    "l1",
    "ibm819",
    "8859",
}
_NATIVE_ENCODING_ERRORS = {
    "strict",
    "ignore",
    "replace",
    "backslashreplace",
    "surrogateescape",
}


def _implicit_argument(call, index, keyword):
    """Find a call argument; None means omitted and False means an expansion."""
    expanded = False
    for item in call.get("kw", []):
        if item["n"] == keyword:
            return item["v"]
        if item["n"] is None:
            expanded = True
    for position, item in enumerate(call.get("args", [])):
        if item["op"] == "starred":
            return False
        if position == index:
            return item
    return False if expanded else None


def _static_codec_name(value):
    if value is None or value is False or value["op"] != "const" or value["t"] != "str":
        return None
    chars = []
    punctuation = False
    for char in value["v"].lower():
        if char.isalnum() or char == ".":
            if punctuation and chars:
                chars.append("_")
            if char.isascii():
                chars.append(char)
            punctuation = False
        else:
            punctuation = True
    return "".join(chars)


def _needs_codec_argument(value, encoding):
    if value is None:
        return False
    if value is False or value["op"] != "const" or value["t"] != "str":
        return True
    if encoding:
        return _static_codec_name(value) not in _NATIVE_ENCODING_NAMES
    return value["v"] not in _NATIVE_ENCODING_ERRORS


def implicit_call_modules(call):
    """Select source providers needed by a call beyond native bootstrap behavior."""
    function = call["f"]
    op = function["op"]
    builtin_name = None
    if op == "name":
        builtin_name = function["id"]
    elif (
        op == "attr"
        and function["o"]["op"] == "name"
        and function["o"]["id"] in ("builtins", ".builtins")
    ):
        builtin_name = function["n"]
    name = function["n"] if op == "attr" else builtin_name
    if op == "attr" and name in ("__reduce__", "__reduce_ex__"):
        return ("copyreg",)
    if name == "namereplace_errors":
        return ("unicodedata",)
    if op == "call":
        factory = function["f"]
        factory_name = factory.get("n") if factory["op"] == "attr" else factory.get("id")
        handler = _implicit_argument(function, 0, "name")
        if (
            factory_name == "lookup_error"
            and handler is not None
            and handler is not False
            and handler["op"] == "const"
            and handler["t"] == "str"
            and handler["v"] == "namereplace"
        ):
            return ("unicodedata",)
        return ()
    lookup = name in (
        "lookup", "getencoder", "getdecoder", "getincrementalencoder",
        "getincrementaldecoder", "getreader", "getwriter",
    )
    if lookup:
        encoding_index = 0
        errors_index = None
    elif name in ("encode", "decode", "iterencode", "iterdecode"):
        receiver = function.get("o")
        receiver_name = None
        if receiver is not None:
            receiver_name = receiver.get("id") if receiver["op"] == "name" else receiver.get("n")
        unbound = (
            op == "name"
            or name in ("iterencode", "iterdecode")
            or receiver_name in ("str", "bytes", "bytearray", "codecs", "_codecs")
        )
        encoding_index = 1 if unbound else 0
        errors_index = encoding_index + 1
    elif builtin_name in ("str", "bytes", "bytearray"):
        encoding_index = 1
        errors_index = 2
    elif builtin_name == "open":
        encoding_index = 3
        errors_index = 4
    elif builtin_name == "compile":
        flags = _implicit_argument(call, 3, "flags")
        if flags is None:
            return ()
        if flags is False:
            return ("_ast",)
        sign = 1
        if flags["op"] == "unary" and flags["kind"] in ("pos", "neg"):
            sign = -1 if flags["kind"] == "neg" else 1
            flags = flags["v"]
        if flags["op"] != "const" or flags["t"] not in ("int", "bool"):
            return ("_ast",)
        return ("_ast",) if (sign * int(flags["v"])) & 1024 else ()
    else:
        return ()
    encoding = _implicit_argument(call, encoding_index, "encoding")
    errors = None if errors_index is None else _implicit_argument(call, errors_index, "errors")
    modules = []
    if _needs_codec_argument(encoding, True) or _needs_codec_argument(errors, False):
        modules.append("codecs")
        codec = _static_codec_name(encoding)
        if codec is not None and codec not in _NATIVE_ENCODING_NAMES:
            from encodings.aliases import aliases

            provider = aliases.get(codec) or aliases.get(codec.replace(".", "_")) or codec
            if provider and "." not in provider:
                modules.append("encodings." + provider)
        if (
            errors is not None
            and errors is not False
            and errors["op"] == "const"
            and errors["t"] == "str"
            and errors["v"] == "namereplace"
        ):
            modules.append("unicodedata")
    return tuple(modules)


def lower_type_scope(name, parameters, node, decorators, annotations, gensym, ln, error, binding):
    """Lower a PEP 695 annotation scope using closures and provider factories.

    Args:
        name: Declaration name.
        parameters: Type parameter records with already lowered bounds.
        node: Function, class, or lazy alias value IR.
        decorators: Decorator expressions evaluated in the enclosing scope.
        annotations: Function annotation dictionary IR, or None.
        gensym: Fresh internal name generator.
        ln: Source line.
        error: Create a located compiler error from its message.
        binding: Lexical destination, which can differ from the declaration name.

    Returns:
        An assignment whose value constructs the declaration in its own scope.
    """
    def check(value, context):
        if isinstance(value, list):
            for item in value:
                check(item, context)
        elif isinstance(value, dict):
            op = value.get("op")
            if op in ("namedexpr", "yield", "yieldfrom"):
                label = "named expression" if op == "namedexpr" else "yield or await expression"
                raise error(label + " cannot be used within " + context)
            if op == "funcexpr" and value["name"] == "<lambda>":
                check(value["params"], context)
            else:
                for item in value.values():
                    check(item, context)

    names = set()
    for parameter in parameters:
        if parameter["name"] in names:
            raise error("duplicate type parameter '" + parameter["name"] + "'")
        names.add(parameter["name"])
        check(parameter["bound"], "a TypeVar bound")
    if node["op"] == "func":
        check(annotations, "an annotation")
    elif node["op"] == "class":
        check(node["bases"], "a generic definition")
        check(node.get("kwargs", []), "a generic definition")
    else:
        check(node, "a type alias")

    def ref(value):
        return {"op": "name", "id": value}

    def text(value):
        return {"op": "const", "t": "str", "v": value}

    def assign(target, value):
        return {"op": "assign", "targets": [target], "v": value, "ln": ln}

    def function(label, body, locals_, args):
        return {
            "op": "funcexpr", "name": label, "gen": False,
            "annotation_scope": True,
            "params": {"args": args, "posonly": 0, "defaults": [],
                       "vararg": None, "kwonly": [], "kwdefaults": [], "kwarg": None},
            "locals": locals_, "gdecl": [], "ndecl": [], "body": body,
        }

    def lazy(label, value):
        return function(label, [{"op": "return", "v": value, "ln": ln}], [], [])

    provider = gensym("typing")
    result = gensym("generic")
    bindings = [p["binding"] for p in parameters]
    arguments = []
    values = []

    def capture(value):
        binding = gensym("outer")
        arguments.append(binding)
        values.append(value)
        return ref(binding)

    # Decorators precede defaults, and neither can see the new type parameters.
    decs = [capture(dec) for dec in decorators]
    if node["op"] == "func":
        params = node["params"]
        params["defaults"] = [capture(value) for value in params["defaults"]]
        params["kwdefaults"] = [
            capture(value) if value is not None else None for value in params["kwdefaults"]
        ]

    def factory(method, args, kw):
        return {"op": "call", "f": {"op": "attr", "o": ref(provider), "n": method},
                "args": args, "kw": kw}

    module = [{"n": "module", "v": ref("__name__")}]
    body = [{"op": "import", "module": "_typing", "alias": provider, "ln": ln}]
    for param in parameters:
        tag = param["t"]
        method = "_typevar"
        kwargs = module[:]
        if tag == "ParamSpec":
            method = "_paramspec"
        elif tag == "TypeVarTuple":
            method = "_typevartuple"
        elif param["bound"] is not None:
            field = "constraints" if param["constraints"] else "bound"
            kwargs.append({"n": field, "v": lazy(param["name"], param["bound"])})
        body.append(assign(ref(param["binding"]), factory(method, [text(param["name"])], kwargs)))
    type_params = {"op": "tuple", "elts": [ref(p["binding"]) for p in parameters]}
    if node["op"] == "func":
        node["op"] = "funcexpr"
        node.pop("ln", None)
        body.append(assign(ref(result), node))
        body.append(assign({"op": "attr", "o": ref(result), "n": "__type_params__"}, type_params))
        if annotations is not None:
            body.append(assign({"op": "attr", "o": ref(result), "n": "__annotations__"},
                               annotations))
    elif node["op"] == "class":
        node["bases"].append(factory("_generic_base", [type_params], []))
        node["body"].insert(0, assign(ref("__type_params__"), type_params))
        body.append(node)
        class_binding = node.get("binding", name)
        body.append(assign(ref(result), ref(class_binding)))
        # The class name belongs to the enclosing scope, not the annotation scope.
        body.append({"op": "del", "targets": [ref(class_binding)], "ln": ln})
    else:
        body.append(assign(ref(result), factory(
            "_typealias", [text(name), type_params, lazy(name, node)], module
        )))
    value = ref(result)
    for dec in decs[::-1]:
        value = {"op": "call", "f": dec, "args": [value], "kw": []}
    body.append({"op": "return", "v": value, "ln": ln})
    scope = function(gensym("typeparams"), body, sorted(bindings + arguments), arguments)
    return assign(ref(binding), {"op": "call", "f": scope, "args": values, "kw": []})


def module_dependencies(module):
    """Collect a compiled module's reachable source-provider imports.

    Args:
        module: Ordinary compiled module IR from the immutable stdlib store.

    Returns:
        Sorted dependency names, including statically selected implicit providers.
    """
    dependencies = set()

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            op = value.get("op")
            if op in ("import", "importfrom"):
                dependencies.add(value["module"])
            elif op == "call":
                for dependency in implicit_call_modules(value):
                    dependencies.add(dependency)
            for item in value.values():
                visit(item)

    visit(module["body"])
    if "_abc" in dependencies:
        dependencies.add("_weakref")
    return sorted(dependencies)


def mangle_private_name(name, class_name):
    """Apply Python's lexical private-name spelling within a class.

    Args:
        name: Source identifier or attribute.
        class_name: Lexically enclosing class name.

    Returns:
        The mangled identifier, or the original name for public and special names.
    """
    if not name.startswith("__") or name.endswith("__") or "." in name:
        return name
    private = class_name.lstrip("_")
    return "_" + private + name if private else name
