"""Parsing primitives used by the standard library's string.Formatter.

Parsing is lazy after argument validation, including field traversal errors.
Brace escapes are separate literal records, as in CPython's markup iterator.
"""


def _index(text):
    value = 0
    if not text:
        return text
    for char in text:
        if not char.isdecimal():
            return text
        digit = int(char)
        if value > (9223372036854775807 - digit) // 10:
            raise ValueError("Too many decimal digits in format string")
        value = value * 10 + digit
    return value


def _field_parts(text, position):
    size = len(text)
    while position < size:
        kind = text[position]
        position += 1
        start = position
        if kind == ".":
            while position < size and text[position] not in ".[":
                position += 1
            name = text[start:position]
            attribute = True
        elif kind == "[":
            while position < size and text[position] != "]":
                position += 1
            if position == size:
                raise ValueError("Missing ']' in format string")
            name = text[start:position]
            position += 1
            attribute = False
        else:
            raise ValueError("Only '.' or '[' may follow ']' in format field specifier")
        if not name:
            raise ValueError("Empty attribute in format string")
        yield attribute, name if attribute else _index(name)


def formatter_field_name_split(field_name, /):
    """Return the first lookup key and an iterator of attribute/item lookups."""
    if not isinstance(field_name, str):
        raise TypeError("expected str, got " + type(field_name).__name__)
    position = 0
    while position < len(field_name) and field_name[position] not in ".[":
        position += 1
    return _index(field_name[:position]), _field_parts(field_name, position)


def _parse(text):
    position = 0
    size = len(text)
    while position < size:
        start = position
        while position < size and text[position] not in "{}":
            position += 1
        if position == size:
            yield text[start:position], None, None, None
            return
        brace = text[position]
        if position + 1 < size and text[position + 1] == brace:
            yield text[start : position + 1], None, None, None
            position += 2
            continue
        if brace == "}":
            raise ValueError("Single '}' encountered in format string")
        if position + 1 == size:
            raise ValueError("Single '{' encountered in format string")
        literal = text[start:position]
        position += 1
        start = position
        while position < size:
            char = text[position]
            if char == "{":
                raise ValueError("unexpected '{' in field name")
            if char in "}!:":
                break
            position += 1
            if char == "[":
                while position < size and text[position] != "]":
                    position += 1
        if position == size:
            raise ValueError("expected '}' before end of string")
        field = text[start:position]
        delimiter = text[position]
        position += 1
        conversion = None
        spec = ""
        if delimiter == "!":
            if position == size:
                raise ValueError("end of string while looking for conversion specifier")
            conversion = text[position]
            # The C API represents a NUL conversion as no conversion.
            if conversion == "\x00":
                conversion = None
            position += 1
            if position == size:
                raise ValueError("unmatched '{' in format spec")
            delimiter = text[position]
            position += 1
            if delimiter not in ":}":
                raise ValueError("expected ':' after conversion specifier")
        if delimiter == ":":
            start = position
            depth = 1
            while position < size:
                char = text[position]
                position += 1
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        break
            if depth:
                raise ValueError("unmatched '{' in format spec")
            spec = text[start : position - 1]
        yield literal, field, spec, conversion


def formatter_parser(format_string, /):
    """Return an iterator of literal, field, format-spec and conversion records."""
    if not isinstance(format_string, str):
        raise TypeError("expected str, got " + type(format_string).__name__)
    return _parse(format_string)
