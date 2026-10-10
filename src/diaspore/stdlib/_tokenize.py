"""Incremental CPython 3.12 tokenizer backend for the public tokenize module.

The parser lexer intentionally discards trivia and decodes literals. This lexer
instead preserves the C tokenizer's physical source spans and f-string modes.
Encoding detection and ENCODING tokens belong to tokenize, not this backend.
"""

from token import EXACT_TOKEN_TYPES

_UNSET = object()
_PREFIXES = ("r", "b", "u", "f", "br", "rb", "fr", "rf")
_DIGITS = "0123456789"


def _name_start(c):
    return bool(c) and (c == "_" or "a" <= c <= "z" or "A" <= c <= "Z" or ord(c) >= 128)


def _name_char(c):
    return _name_start(c) or bool(c) and c in _DIGITS


class TokenizerIter:
    """Yield five-element token tuples from a text or encoded-bytes readline.

    Args:
        readline: Zero-argument callback; empty input or StopIteration ends input.
        encoding: If supplied, decode bytes with this codec and replacement errors.
        extra_tokens: Preserve comments, non-logical newlines and operator spelling.
    """

    def __init__(self, readline, /, *, encoding=_UNSET, extra_tokens=False):
        if encoding is not _UNSET:
            if not isinstance(encoding, str):
                name = "None" if encoding is None else type(encoding).__name__
                raise TypeError("tokenizeriter() argument 'encoding' must be str, not " + name)
            if "\x00" in encoding:
                raise ValueError("embedded null character")
        self.readline = readline
        self.encoding = encoding
        self.extra = bool(extra_tokens)
        self.lines = []
        self.original = []
        self.base_row = 1
        self.line = ""
        self.row = 0
        self.col = 0
        self.eof = False
        self.done = False
        self.atbol = True
        self.blank = False
        self.indents = [(0, 0)]
        self.pending = []
        self.parens = []
        self.modes = []
        self.last_row = 0
        self.last_line = None

    def __iter__(self):
        return self

    def _read(self):
        if self.eof:
            return False
        try:
            line = self.readline()
        except StopIteration:
            self.eof = True
            return False
        if self.encoding is _UNSET:
            if not isinstance(line, str):
                raise TypeError("readline() returned a non-string object")
        else:
            if not isinstance(line, bytes):
                raise TypeError("readline() returned a non-bytes object")
            line = line.decode(self.encoding, "replace")
        # The C reader obtains a strict UTF-8 buffer even for text callbacks.
        line.encode("utf-8")
        if not line:
            self.eof = True
            return False
        self.original.append(line)
        if not line.endswith("\n"):
            line += "\n"
        self.lines.append(line)
        self.line = line
        self.row += 1
        self.col = 0
        if "\x00" in line:
            self._syntax("source code cannot contain null bytes", 0, text=line.split("\x00")[0])
        return True

    def _peek(self):
        if self.col >= len(self.line):
            if not self._read():
                return ""
        return self.line[self.col]

    def _position(self):
        return (self.row, self.col)

    def _slice(self, start, end):
        first = start[0] - self.base_row
        last = end[0] - self.base_row
        if first == last:
            return self.lines[first][start[1] : end[1]]
        return (
            self.lines[first][start[1] :]
            + "".join(self.lines[first + 1 : last])
            + self.lines[last][: end[1]]
        )

    def _token(self, kind, start, end=None, text=None):
        if end is None:
            end = self._position()
        if text is None:
            text = self._slice(start, end)
        if self.last_row == end[0]:
            line = self.last_line
        else:
            first = start[0] - self.base_row
            last = end[0] - self.base_row
            line = "".join(self.lines[first:last]) + self.original[last]
            self.last_line = line
        self.last_row = start[0]
        if self.extra and 6 < kind < 55:
            kind = 55
        return (kind, text, start, end, line)

    def _syntax(self, message, offset=None, row=None, end=None, text=None):
        if row is None:
            row = self.row
        if offset is None:
            offset = self.col
        if end is None:
            end = offset
        if text is None:
            text = self.lines[row - self.base_row].split("\n")[0]
        raise SyntaxError(message, ("<string>", row, offset, text, row, end))

    def _error(self, message, cls=SyntaxError):
        line = self.line[:-1]
        if self.modes:
            line = "".join(self.lines[self.modes[0]["start"][0] - self.base_row :])[:-1]
        raise cls(message, ("<string>", self.row, len(line) + 1, line, None, None))

    def _eof_error(self, offset=0):
        error = SyntaxError("unexpected EOF in multi-line statement")
        error.filename = "<string>"
        error.lineno = self.row
        error.offset = offset
        error.end_lineno = self.row
        raise error

    def _continuation(self, indentation=False):
        self.col += 1
        c = self._peek()
        if c == "\r":
            self.col += 1
            c = self._peek()
        if c != "\n":
            self._error("unexpected character after line continuation character")
        self.col += 1
        offset = 0 if indentation else len(self.line.encode("utf-8"))
        if self.modes:
            offset = len(
                "".join(self.lines[self.modes[0]["start"][0] - self.base_row :]).encode("utf-8")
            )
        if not self._peek():
            self._eof_error(offset)

    def _indent(self):
        self.atbol = False
        width = 0
        alternate = 0
        continued = 0
        c = self._peek()
        while c and c in " \t\f\\":
            if c == " ":
                width += 1
                alternate += 1
                self.col += 1
            elif c == "\t":
                width = (width // 8 + 1) * 8
                alternate += 1
                self.col += 1
            elif c == "\f":
                width = 0
                alternate = 0
                self.col += 1
            else:
                if not continued:
                    continued = width
                self._continuation(indentation=True)
            c = self._peek()
        self.blank = bool(c) and c in "#\r\n"
        if not c or self.blank or self.parens:
            return
        if continued:
            width = continued
            alternate = continued
        previous = self.indents[-1]
        kinds = []
        if width == previous[0]:
            if alternate != previous[1]:
                self._error("inconsistent use of tabs and spaces in indentation", TabError)
        elif width > previous[0]:
            if len(self.indents) >= 100:
                self._error("too many levels of indentation", IndentationError)
            if alternate <= previous[1]:
                self._error("inconsistent use of tabs and spaces in indentation", TabError)
            self.indents.append((width, alternate))
            kinds.append(5)
        else:
            while width < self.indents[-1][0]:
                self.indents.pop()
                kinds.append(6)
            if width != self.indents[-1][0]:
                self._error("unindent does not match any outer indentation level", IndentationError)
            if alternate != self.indents[-1][1]:
                self._error("inconsistent use of tabs and spaces in indentation", TabError)
        for kind in kinds:
            if self.extra:
                start = (self.row, 0 if kind == 5 else self.col)
                self.pending.append(self._token(kind, start))
            else:
                self.last_row = self.row
                self.last_line = self.original[-1]
                self.pending.append((kind, "", (self.row, -1), (self.row, -1), self.last_line))

    def _digits(self, digits, kind):
        while self.col < len(self.line):
            c = self.line[self.col]
            if c in digits:
                self.col += 1
            elif c == "_":
                self.col += 1
                if self.col >= len(self.line) or self.line[self.col] not in digits:
                    c = self.line[self.col : self.col + 1]
                    if kind in ("binary", "octal") and c and c in _DIGITS:
                        self.col += 1
                        self._syntax("invalid digit '" + c + "' in " + kind + " literal")
                    self._syntax("invalid " + kind + " literal")
            else:
                break

    def _number_end(self, kind):
        c = self.line[self.col : self.col + 1]
        if self.extra or not c or ord(c) >= 128 or not _name_char(c):
            return
        word_end = self.col
        while word_end < len(self.line) and _name_char(self.line[word_end]):
            word_end += 1
        word = self.line[self.col : word_end]
        if word in ("and", "else", "for", "or", "not") or word.startswith(("if", "in", "is")):
            self._warn("invalid " + kind + " literal")
        else:
            self._syntax("invalid " + kind + " literal")

    def _warn(self, message, offset=None):
        import warnings

        try:
            warnings.warn_explicit(message, SyntaxWarning, "<string>", self.row)
        except SyntaxWarning:
            self._syntax(message, offset)

    def _number(self, start):
        beginning = self.col
        prefix = self.line[self.col : self.col + 2].lower()
        if prefix in ("0x", "0o", "0b"):
            digits, kind = {
                "0x": ("0123456789abcdefABCDEF", "hexadecimal"),
                "0o": ("01234567", "octal"),
                "0b": ("01", "binary"),
            }[prefix]
            self.col += 2
            if self.line[self.col : self.col + 1] == "_":
                self.col += 1
            c = self.line[self.col : self.col + 1]
            if not c or c not in digits:
                if c and c in _DIGITS:
                    self.col += 1
                    self._syntax("invalid digit '" + c + "' in " + kind + " literal")
                self._syntax("invalid " + kind + " literal")
            self._digits(digits, kind)
            c = self.line[self.col : self.col + 1]
            if kind != "hexadecimal" and c and c in _DIGITS:
                self.col += 1
                self._syntax("invalid digit '" + c + "' in " + kind + " literal")
            self._number_end(kind)
            return self._token(2, start)
        self._digits(_DIGITS, "decimal")
        integer_end = self.col
        floating = False
        if self.line[self.col : self.col + 1] == ".":
            floating = True
            self.col += 1
            if self.line[self.col : self.col + 1] in _DIGITS:
                self._digits(_DIGITS, "decimal")
        if self.line[self.col : self.col + 1] in ("e", "E"):
            exponent = self.col
            self.col += 1
            c = self.line[self.col : self.col + 1]
            if c in ("+", "-"):
                self.col += 1
                c = self.line[self.col : self.col + 1]
                if not c or c not in _DIGITS:
                    self._syntax("invalid decimal literal")
            if c and c in _DIGITS:
                floating = True
                self._digits(_DIGITS, "decimal")
            else:
                self.col = exponent
                self._number_end("decimal")
                return self._token(2, start)
        kind = "decimal"
        if self.line[self.col : self.col + 1] in ("j", "J"):
            self.col += 1
            floating = True
            kind = "imaginary"
        if not floating and not self.extra and self.line[beginning] == "0":
            zero_end = beginning
            while zero_end < integer_end and self.line[zero_end] in "0_":
                zero_end += 1
            if zero_end < integer_end:
                first = len(self.line[: start[1]].encode("utf-8")) + 1
                last = len(self.line[:zero_end].encode("utf-8")) + 1
                self._syntax(
                    "leading zeros in decimal integer literals are not permitted; "
                    "use an 0o prefix for octal integers",
                    first,
                    end=last,
                )
        self._number_end(kind)
        return self._token(2, start)

    def _string(self, start, prefix):
        quote = self.line[self.col]
        if self.line[self.col : self.col + 3] == quote * 3:
            quote *= 3
        self.col += len(quote)
        if "f" in prefix.lower():
            if len(self.modes) >= 149:
                self._syntax("too many nested f-strings")
            self.modes.append(
                {
                    "quote": quote,
                    "raw": "r" in prefix.lower(),
                    "start": start,
                    "literal": True,
                    "depth": 0,
                    "field": -1,
                    "format": False,
                }
            )
            return self._token(61, start)
        while True:
            c = self._peek()
            if not c or c == "\n" and len(quote) == 1:
                message = "unterminated "
                if self.modes and self.modes[-1]["quote"] == quote:
                    message = "f-string: expecting '}'"
                else:
                    if len(quote) == 3:
                        message += "triple-quoted "
                    message += "string literal (detected at line " + str(self.row) + ")"
                self._syntax(message, start[1] + 1, start[0])
            if self.line[self.col : self.col + len(quote)] == quote:
                self.col += len(quote)
                return self._token(3, start)
            self.col += 1
            if c == "\\":
                escaped = self._peek()
                if escaped:
                    self.col += 1
                    if escaped == "\r" and self._peek():
                        self.col += 1

    def _fstring(self):
        mode = self.modes[-1]
        start = self._position()
        quote = mode["quote"]
        if self.line[self.col : self.col + 1] == "{" and self.line[self.col : self.col + 2] != "{{":
            mode["field"] += 1
            if mode["field"] >= 3:
                self._syntax("f-string: expressions nested too deeply")
            mode["literal"] = False
            return None
        if self.line[self.col : self.col + len(quote)] == quote:
            self.col += len(quote)
            self.modes.pop()
            return self._token(63, start)
        unicode_escape = False
        while True:
            c = self._peek()
            in_format = mode["format"] and mode["field"] >= 0
            if not c or c == "\n" and len(quote) == 1:
                if c == "\n" and in_format:
                    mode["literal"] = False
                    mode["format"] = False
                    return self._token(62, start)
                message = "unterminated "
                if len(quote) == 3:
                    message += "triple-quoted "
                message += "f-string literal (detected at line " + str(self.row) + ")"
                self._syntax(message, mode["start"][1] + 1, mode["start"][0])
            if self.line[self.col : self.col + len(quote)] == quote:
                return self._token(62, start)
            self.col += 1
            if c == "{":
                if self._peek() != "{" or in_format:
                    self.col -= 1
                    mode["field"] += 1
                    if mode["field"] >= 3:
                        self._syntax("f-string: expressions nested too deeply")
                    mode["literal"] = False
                    mode["format"] = False
                    return self._token(62, start)
                end = self._position()
                self.col += 1
                return self._token(62, start, end)
            if c == "}":
                if unicode_escape:
                    return self._token(62, start)
                if self._peek() == "}" and not in_format and mode["depth"] == 0:
                    end = self._position()
                    self.col += 1
                    return self._token(62, start, end)
                self.col -= 1
                mode["literal"] = False
                return self._token(62, start)
            if c == "\\":
                escaped = self._peek()
                if escaped == "\r":
                    self.col += 1
                    escaped = self._peek()
                if escaped in ("{", "}"):
                    if not mode["raw"]:
                        self._warn("invalid escape sequence '\\" + escaped + "'", self.col + 1)
                    continue
                if escaped:
                    self.col += 1
                if not mode["raw"] and escaped == "N" and self._peek() == "{":
                    self.col += 1
                    unicode_escape = True

    def _operator(self, start):
        c = self.line[self.col]
        operator_column = self.col
        if ord(c) >= 128:
            # A bare CR consumes one C byte before punctuation dispatch.
            raw = self.line[start[1] : self.col].encode("utf-8") + c.encode("utf-8")[:1]
            raw.decode("utf-8")
        self.col += 1
        mode = self.modes[-1] if self.modes else None
        if mode is not None and c == ":" and mode["depth"] - 1 == mode["field"]:
            mode["literal"] = True
            mode["format"] = True
            return self._token(11, start)
        for size in (3, 2):
            op = self.line[operator_column : operator_column + size]
            if len(op) == size and op in EXACT_TOKEN_TYPES:
                self.col = operator_column + size
                return self._token(EXACT_TOKEN_TYPES[op], start)
        if c in "([{":
            if len(self.parens) >= 200:
                self._syntax("too many nested parentheses")
            self.parens.append((c, self.row))
            if mode is not None:
                mode["depth"] += 1
        elif c in ")]}":
            if mode is not None and not mode["depth"] and c == "}":
                self._syntax("f-string: single '}' is not allowed")
            if not self.extra and not self.parens:
                self._syntax("unmatched '" + c + "'")
            if self.parens:
                opening, row = self.parens.pop()
                if not self.extra and opening + c not in ("()", "[]", "{}"):
                    if mode is not None and opening == "{" and mode["depth"] - 1 == mode["field"]:
                        self._syntax("f-string: unmatched '" + c + "'")
                    message = (
                        "closing parenthesis '"
                        + c
                        + "' does not match opening parenthesis '"
                        + opening
                        + "'"
                    )
                    if row != self.row:
                        message += " on line " + str(row)
                    self._syntax(message)
            if mode is not None:
                mode["depth"] -= 1
                if mode["depth"] < 0:
                    self._syntax("f-string: unmatched '" + c + "'")
                if c == "}" and mode["depth"] == mode["field"]:
                    mode["field"] -= 1
                    mode["literal"] = True
                    mode["format"] = False
        if not c.isprintable():
            self._syntax(f"invalid non-printable character U+{ord(c):04X}")
        return self._token(EXACT_TOKEN_TYPES.get(c, 55), start)

    def __next__(self):
        if self.done:
            raise StopIteration("EOF")
        # Only an unfinished string needs source from earlier physical lines.
        keep = self.modes[0]["start"][0] if self.modes else self.row
        discard = keep - self.base_row
        if discard > 0:
            del self.lines[:discard]
            del self.original[:discard]
            self.base_row = keep
        while True:
            if self.pending:
                return self.pending.pop(0)
            if self.modes and self.modes[-1]["literal"]:
                token = self._fstring()
                if token is not None:
                    return token
            c = self._peek()
            if not c:
                if self.parens:
                    offset = 0
                    if self.modes:
                        offset = len(
                            "".join(self.lines[self.modes[0]["start"][0] - self.base_row :]).encode(
                                "utf-8"
                            )
                        )
                    self._eof_error(offset)
                row = self.row + 1 if self.extra else self.row
                column = 0 if self.extra else -1
                position = (row, column)
                line = ""
                if not self.extra and self.row == self.last_row:
                    line = self.last_line
                if len(self.indents) > 1:
                    self.indents.pop()
                    return (6, "", position, position, line)
                self.done = True
                if line is None:
                    raise StopIteration
                return (0, "", position, position, line)
            if self.atbol:
                self._indent()
                if self.pending:
                    return self.pending.pop(0)
                c = self._peek()
                if not c:
                    continue
            if c in " \t\f":
                self.col += 1
                continue
            start = self._position()
            allow_name = True
            if c == "#":
                while self.col < len(self.line) and self.line[self.col] not in "\r\n":
                    self.col += 1
                if self.extra:
                    return self._token(64, start)
                c = self._peek()
            if c == "\r":
                self.col += 1
                c = self._peek()
                allow_name = False
            if c == "\n":
                self.col += 1
                self.atbol = True
                kind = 65 if self.blank or self.parens else 4
                if kind == 65 and not self.extra:
                    continue
                end = self._position()
                if kind == 4 and not self.extra:
                    end = (end[0], end[1] - 1)
                implicit = not self.original[-1].endswith("\n")
                text_end = end
                if implicit and self.extra:
                    text_end = (end[0], end[1] - 1)
                text = self._slice(start, text_end)
                if implicit and kind == 65:
                    text = ""
                return self._token(kind, start, end, text)
            self.blank = False
            if c == "\\":
                self._continuation()
                continue
            if c in "\"'":
                return self._string(start, "")
            if c in _DIGITS or c == "." and self.line[self.col + 1 : self.col + 2] in _DIGITS:
                return self._number(start)
            if allow_name and _name_start(c):
                end = self.col + 1
                while end < len(self.line) and _name_char(self.line[end]):
                    end += 1
                word = self.line[self.col : end]
                self.col = end
                if word.lower() in _PREFIXES and self.line[end : end + 1] in ("'", '"'):
                    return self._string(start, word)
                if not self.extra and not word.isidentifier():
                    for index in range(len(word)):
                        valid = (
                            word[index].isidentifier()
                            if index == 0
                            else ("a" + word[index]).isidentifier()
                        )
                        if not valid:
                            character = word[index]
                            message = f"invalid non-printable character U+{ord(character):04X}"
                            if character.isprintable():
                                message = (
                                    f"invalid character '{character}' (U+{ord(character):04X})"
                                )
                            self._syntax(message, start[1] + index + 1)
                kind = 1
                if not self.extra and word in ("async", "await"):
                    kind = 57 if word == "async" else 56
                return self._token(kind, start)
            return self._operator(start)
