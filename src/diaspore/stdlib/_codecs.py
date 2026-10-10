"""Portable codec registry, Unicode transformation formats, and character maps."""

__all__ = [
    "register",
    "unregister",
    "lookup",
    "encode",
    "decode",
    "register_error",
    "lookup_error",
    "utf_8_encode",
    "utf_8_decode",
    "ascii_encode",
    "ascii_decode",
    "latin_1_encode",
    "latin_1_decode",
    "utf_16_encode",
    "utf_16_le_encode",
    "utf_16_be_encode",
    "utf_16_decode",
    "utf_16_le_decode",
    "utf_16_be_decode",
    "utf_16_ex_decode",
    "utf_32_encode",
    "utf_32_le_encode",
    "utf_32_be_encode",
    "utf_32_decode",
    "utf_32_le_decode",
    "utf_32_be_decode",
    "utf_32_ex_decode",
    "charmap_build",
    "charmap_encode",
    "charmap_decode",
]

_search_path = []
_cache = {}
_initialized = False


def register(search_function):
    if not callable(search_function):
        raise TypeError("argument must be callable")
    _search_path.append(search_function)


def unregister(search_function):
    if search_function in _search_path:
        _search_path.remove(search_function)
        _cache.clear()


def lookup(encoding):
    global _initialized
    if not isinstance(encoding, str):
        raise TypeError("lookup() argument must be str, not " + type(encoding).__name__)
    if not _initialized:
        # Import only after codecs has defined CodecInfo and its base classes.
        import encodings as encodings

        # Explicit aliases retain these imports as static bundle dependencies.
        from encodings import ascii as ascii
        from encodings import latin_1 as latin_1
        from encodings import utf_8 as utf_8

        _initialized = True
    key = encoding.lower().replace(" ", "_").replace("-", "_")
    if key in _cache:
        return _cache[key]
    for search in _search_path:
        result = search(key)
        if result is not None:
            if not isinstance(result, tuple) or len(result) != 4:
                raise TypeError("codec search functions must return 4-tuples")
            _cache[key] = result
            return result
    raise LookupError("unknown encoding: " + encoding)


def encode(obj, encoding="utf-8", errors="strict"):
    return lookup(encoding)[0](obj, errors)[0]


def decode(obj, encoding="utf-8", errors="strict"):
    return lookup(encoding)[1](obj, errors)[0]


def register_error(name, handler):
    if not isinstance(name, str):
        raise TypeError("argument 1 must be str, not " + type(name).__name__)
    if not callable(handler):
        raise TypeError("handler must be callable")
    _error_handlers[name] = handler


def lookup_error(name):
    if not isinstance(name, str):
        raise TypeError("lookup_error() argument must be str, not " + type(name).__name__)
    if name not in _error_handlers:
        raise LookupError("unknown error handler name '" + name + "'")
    return _error_handlers[name]


def _check_exception(exc):
    if not isinstance(exc, (UnicodeEncodeError, UnicodeDecodeError, UnicodeTranslateError)):
        raise TypeError("don't know how to handle " + type(exc).__name__ + " in error callback")


def _strict(exc):
    if not isinstance(exc, BaseException):
        raise TypeError("codec must pass exception instance")
    raise exc


def _ignore(exc):
    _check_exception(exc)
    return "", exc.end


def _replace(exc):
    _check_exception(exc)
    if isinstance(exc, UnicodeEncodeError):
        return "?" * (exc.end - exc.start), exc.end
    if isinstance(exc, UnicodeTranslateError):
        return "\ufffd" * (exc.end - exc.start), exc.end
    return "\ufffd", exc.end


def _xmlcharrefreplace(exc):
    if not isinstance(exc, UnicodeEncodeError):
        raise TypeError("don't know how to handle " + type(exc).__name__ + " in error callback")
    return "".join("&#" + str(ord(c)) + ";" for c in exc.object[exc.start : exc.end]), exc.end


def _escape(code):
    if code <= 255:
        return "\\x" + format(code, "02x")
    if code <= 65535:
        return "\\u" + format(code, "04x")
    return "\\U" + format(code, "08x")


def _backslashreplace(exc):
    _check_exception(exc)
    chars = exc.object[exc.start : exc.end]
    if isinstance(exc, UnicodeDecodeError):
        return "".join(_escape(c) for c in chars), exc.end
    return "".join(_escape(ord(c)) for c in chars), exc.end


def _namereplace(exc):
    if not isinstance(exc, UnicodeEncodeError):
        raise TypeError("don't know how to handle " + type(exc).__name__ + " in error callback")
    # Static namereplace calls include name data; ordinary codecs do not need it.
    try:
        unicodedata = __import__("unicodedata")
    except ImportError:
        raise LookupError(
            "Unicode name replacement requires 'import unicodedata' in the program"
        ) from None

    result = []
    for c in exc.object[exc.start : exc.end]:
        name = unicodedata.name(c, None)
        result.append("\\N{" + name + "}" if name is not None else _escape(ord(c)))
    return "".join(result), exc.end


def _surrogateescape(exc):
    if isinstance(exc, UnicodeEncodeError):
        result = []
        for c in exc.object[exc.start : exc.end]:
            code = ord(c)
            if not 0xDC80 <= code <= 0xDCFF:
                raise exc
            result.append(code - 0xDC00)
        return bytes(result), exc.end
    if isinstance(exc, UnicodeDecodeError):
        result = []
        end = exc.start
        for code in exc.object[exc.start : exc.end]:
            if code < 128 or len(result) == 4:
                break
            result.append(chr(0xDC00 + code))
            end += 1
        if result:
            return "".join(result), end
    raise exc


def _surrogatepass(exc):
    encoding = exc.encoding.lower().replace("_", "-")
    wide = encoding in (
        "utf16",
        "utf-16",
        "utf16le",
        "utf16be",
        "utf-16le",
        "utf-16be",
        "utf16-le",
        "utf16-be",
        "utf-16-le",
        "utf-16-be",
        "utf32",
        "utf-32",
        "utf32le",
        "utf32be",
        "utf-32le",
        "utf-32be",
        "utf32-le",
        "utf32-be",
        "utf-32-le",
        "utf-32-be",
    )
    if wide:
        width = 2 if "16" in encoding else 4
        order = 1 if encoding.endswith("be") else -1
        if encoding in ("utf16", "utf-16", "utf32", "utf-32"):
            order = _native_order()
        if isinstance(exc, UnicodeEncodeError):
            result = bytearray()
            for char in exc.object[exc.start : exc.end]:
                code = ord(char)
                if not 0xD800 <= code <= 0xDFFF:
                    raise exc
                _append_unit(result, code, width, order)
            return bytes(result), exc.end
        if isinstance(exc, UnicodeDecodeError) and exc.start + width <= len(exc.object):
            code = _read_unit(exc.object, exc.start, width, order)
            if 0xD800 <= code <= 0xDFFF:
                return chr(code), exc.start + width
        raise exc
    if exc.encoding.lower().replace("_", "-") not in ("utf-8", "utf8"):
        raise exc
    if isinstance(exc, UnicodeEncodeError):
        result = []
        for c in exc.object[exc.start : exc.end]:
            code = ord(c)
            if not 0xD800 <= code <= 0xDFFF:
                raise exc
            result.extend((0xE0 | (code >> 12), 0x80 | ((code >> 6) & 63), 0x80 | (code & 63)))
        return bytes(result), exc.end
    if isinstance(exc, UnicodeDecodeError):
        data = exc.object
        pos = exc.start
        if (
            pos + 3 <= len(data)
            and data[pos] == 0xED
            and 0xA0 <= data[pos + 1] <= 0xBF
            and 0x80 <= data[pos + 2] <= 0xBF
        ):
            code = ((data[pos] & 15) << 12) | ((data[pos + 1] & 63) << 6) | (data[pos + 2] & 63)
            return chr(code), pos + 3
    raise exc


_error_handlers = {
    "strict": _strict,
    "ignore": _ignore,
    "replace": _replace,
    "xmlcharrefreplace": _xmlcharrefreplace,
    "backslashreplace": _backslashreplace,
    "namereplace": _namereplace,
    "surrogateescape": _surrogateescape,
    "surrogatepass": _surrogatepass,
}


def _error(errors, exc, encoding):
    result = lookup_error(errors)(exc)
    if not isinstance(result, tuple) or len(result) != 2:
        raise TypeError(
            "encoding error handler must return (str/bytes, int) tuple"
            if encoding
            else "decoding error handler must return (str, int) tuple"
        )
    replacement, pos = result
    if not isinstance(replacement, (str, bytes) if encoding else str) or not isinstance(pos, int):
        raise TypeError(
            "encoding error handler must return (str/bytes, int) tuple"
            if encoding
            else "decoding error handler must return (str, int) tuple"
        )
    if pos < 0:
        pos += len(exc.object)
    if pos < 0 or pos > len(exc.object):
        raise IndexError("position " + str(pos) + " from error handler out of bounds")
    return replacement, pos


def _arguments(input, errors, encoding):
    if encoding:
        if not isinstance(input, str):
            raise TypeError("argument 1 must be str, not " + type(input).__name__)
    elif not isinstance(input, bytes):
        # bytes(int) and bytes(iterable) are not buffer-protocol conversions.
        try:
            view = memoryview(input)
        except TypeError:
            raise TypeError(
                "a bytes-like object is required, not '" + type(input).__name__ + "'"
            ) from None
        if not view.c_contiguous:
            raise BufferError("memoryview: underlying buffer is not C-contiguous")
        input = view.tobytes()
    if errors is not None and not isinstance(errors, str):
        raise TypeError("argument 2 must be str or None, not " + type(errors).__name__)
    if errors is not None and "\x00" in errors:
        raise ValueError("embedded null character")
    return input, "strict" if errors is None else errors


def _encode(input, errors, encoding, limit):
    input, errors = _arguments(input, errors, True)
    output = bytearray()
    pos = 0
    while pos < len(input):
        code = ord(input[pos])
        invalid = code >= limit if limit else 0xD800 <= code <= 0xDFFF
        if invalid:
            end = pos + 1
            while end < len(input):
                following = ord(input[end])
                if not (following >= limit if limit else 0xD800 <= following <= 0xDFFF):
                    break
                end += 1
            reason = (
                "ordinal not in range(" + str(limit) + ")" if limit else "surrogates not allowed"
            )
            exc = UnicodeEncodeError(encoding, input, pos, end, reason)
            replacement, newpos = _error(errors, exc, True)
            if isinstance(replacement, str):
                try:
                    replacement = _encode(replacement, "strict", encoding, limit)[0]
                except UnicodeEncodeError:
                    raise exc  # noqa: B904 - Preserve the codec's implicit error context.
            output.extend(replacement)
            input = exc.object
            pos = newpos
            continue
        if limit or code < 128:
            output.append(code)
        elif code < 2048:
            output.extend((0xC0 | (code >> 6), 0x80 | (code & 63)))
        elif code < 65536:
            output.extend((0xE0 | (code >> 12), 0x80 | ((code >> 6) & 63), 0x80 | (code & 63)))
        else:
            output.extend(
                (
                    0xF0 | (code >> 18),
                    0x80 | ((code >> 12) & 63),
                    0x80 | ((code >> 6) & 63),
                    0x80 | (code & 63),
                )
            )
        pos += 1
    return bytes(output), len(input)


# Builtin codec functions do not bind when stored on a codec class.
@staticmethod
def utf_8_encode(input, errors="strict"):
    return _encode(input, errors, "utf-8", 0)


@staticmethod
def ascii_encode(input, errors="strict"):
    return _encode(input, errors, "ascii", 128)


@staticmethod
def latin_1_encode(input, errors="strict"):
    return _encode(input, errors, "latin-1", 256)


@staticmethod
def latin_1_decode(input, errors="strict"):
    input, errors = _arguments(input, errors, False)
    return "".join(chr(code) for code in input), len(input)


@staticmethod
def ascii_decode(input, errors="strict"):
    input, errors = _arguments(input, errors, False)
    output = []
    pos = 0
    while pos < len(input):
        if input[pos] < 128:
            output.append(chr(input[pos]))
            pos += 1
        else:
            exc = UnicodeDecodeError("ascii", input, pos, pos + 1, "ordinal not in range(128)")
            replacement, pos = _error(errors, exc, False)
            output.append(replacement)
            input = exc.object
    return "".join(output), len(input)


@staticmethod
def utf_8_decode(input, errors="strict", final=False):
    input, errors = _arguments(input, errors, False)
    output = []
    pos = 0
    while pos < len(input):
        first = input[pos]
        if first < 128:
            output.append(chr(first))
            pos += 1
            continue
        if 0xC2 <= first <= 0xDF:
            size = 2
        elif 0xE0 <= first <= 0xEF:
            size = 3
        elif 0xF0 <= first <= 0xF4:
            size = 4
        else:
            size = 0
        end = pos + 1
        reason = "invalid start byte"
        if size:
            code = first & (0x7F >> size)
            reason = None
            for offset in range(1, size):
                if pos + offset >= len(input):
                    reason = "unexpected end of data"
                    break
                byte = input[pos + offset]
                lower = 0x80
                upper = 0xBF
                if offset == 1:
                    if first == 0xE0:
                        lower = 0xA0
                    elif first == 0xED:
                        upper = 0x9F
                    elif first == 0xF0:
                        lower = 0x90
                    elif first == 0xF4:
                        upper = 0x8F
                if not lower <= byte <= upper:
                    reason = "invalid continuation byte"
                    break
                code = (code << 6) | (byte & 63)
                end += 1
            if reason is None:
                output.append(chr(code))
                pos += size
                continue
            if reason == "unexpected end of data" and not final:
                break
        exc = UnicodeDecodeError("utf-8", input, pos, end, reason)
        replacement, pos = _error(errors, exc, False)
        output.append(replacement)
        input = exc.object
    return "".join(output), pos


def _native_order():
    import sys

    return -1 if sys.byteorder == "little" else 1


def _append_unit(output, code, width, order):
    for index in range(width):
        shift = index if order < 0 else width - index - 1
        output.append((code >> (shift * 8)) & 255)


def _read_unit(input, pos, width, order):
    code = 0
    for index in range(width):
        shift = index if order < 0 else width - index - 1
        code |= input[pos + index] << (shift * 8)
    return code


def _wide_encode(input, errors, width, order):
    input, errors = _arguments(input, errors, True)
    if not -(1 << 31) <= order < (1 << 31):
        raise OverflowError("Python int too large to convert to C int")
    encoding = "utf-" + str(width * 8)
    output = bytearray()
    if order == 0:
        order = _native_order()
        _append_unit(output, 0xFEFF, width, order)
    else:
        encoding += "-le" if order < 0 else "-be"
    pos = 0
    while pos < len(input):
        code = ord(input[pos])
        if 0xD800 <= code <= 0xDFFF:
            exc = UnicodeEncodeError(encoding, input, pos, pos + 1, "surrogates not allowed")
            replacement, pos = _error(errors, exc, True)
            if isinstance(replacement, str):
                try:
                    replacement = _wide_encode(replacement, "strict", width, order)[0]
                except UnicodeEncodeError:
                    raise exc  # noqa: B904
            if len(replacement) % width:
                raise exc
            output.extend(replacement)
            input = exc.object
            continue
        if width == 2 and code >= 0x10000:
            code -= 0x10000
            _append_unit(output, 0xD800 | (code >> 10), width, order)
            code = 0xDC00 | (code & 1023)
        _append_unit(output, code, width, order)
        pos += 1
    return bytes(output), len(input)


def _wide_decode(input, errors, width, byteorder, final):
    from _operator import index

    input, errors = _arguments(input, errors, False)
    byteorder = index(byteorder)
    if not -(1 << 31) <= byteorder < (1 << 31):
        raise OverflowError("Python int too large to convert to C int")
    order = byteorder
    pos = 0
    if order == 0:
        order = _native_order()
        if len(input) >= width:
            bom = _read_unit(input, 0, width, -1)
            if bom == 0xFEFF:
                order = -1
                byteorder = -1
                pos = width
            elif bom == (0xFFFE if width == 2 else 0xFFFE0000):
                order = 1
                byteorder = 1
                pos = width
    encoding = "utf-" + str(width * 8) + ("-le" if order < 0 else "-be")
    output = []
    while pos < len(input):
        end = pos + width
        reason = None
        if end > len(input):
            if not final:
                break
            end = len(input)
            reason = "truncated data"
        else:
            code = _read_unit(input, pos, width, order)
            if width == 2 and 0xD800 <= code <= 0xDBFF:
                if end + width > len(input):
                    if not final:
                        break
                    end = len(input)
                    reason = "unexpected end of data"
                else:
                    low = _read_unit(input, end, width, order)
                    if 0xDC00 <= low <= 0xDFFF:
                        code = 0x10000 + ((code - 0xD800) << 10) + low - 0xDC00
                        end += width
                    else:
                        reason = "illegal UTF-16 surrogate"
            elif 0xD800 <= code <= 0xDFFF:
                reason = (
                    "illegal encoding"
                    if width == 2
                    else "code point in surrogate code point range(0xd800, 0xe000)"
                )
            elif code > 0x10FFFF:
                reason = "code point not in range(0x110000)"
        if reason is None:
            output.append(chr(code))
            pos = end
        else:
            exc = UnicodeDecodeError(encoding, input, pos, end, reason)
            replacement, pos = _error(errors, exc, False)
            output.append(replacement)
            input = exc.object
    return "".join(output), pos, byteorder


@staticmethod
def utf_16_encode(input, errors="strict", byteorder=0):
    from _operator import index

    return _wide_encode(input, errors, 2, index(byteorder))


@staticmethod
def utf_16_le_encode(input, errors="strict"):
    return _wide_encode(input, errors, 2, -1)


@staticmethod
def utf_16_be_encode(input, errors="strict"):
    return _wide_encode(input, errors, 2, 1)


@staticmethod
def utf_32_encode(input, errors="strict", byteorder=0):
    from _operator import index

    return _wide_encode(input, errors, 4, index(byteorder))


@staticmethod
def utf_32_le_encode(input, errors="strict"):
    return _wide_encode(input, errors, 4, -1)


@staticmethod
def utf_32_be_encode(input, errors="strict"):
    return _wide_encode(input, errors, 4, 1)


@staticmethod
def utf_16_ex_decode(input, errors="strict", byteorder=0, final=False):
    return _wide_decode(input, errors, 2, byteorder, final)


@staticmethod
def utf_32_ex_decode(input, errors="strict", byteorder=0, final=False):
    return _wide_decode(input, errors, 4, byteorder, final)


@staticmethod
def utf_16_decode(input, errors="strict", final=False):
    return _wide_decode(input, errors, 2, 0, final)[:2]


@staticmethod
def utf_16_le_decode(input, errors="strict", final=False):
    return _wide_decode(input, errors, 2, -1, final)[:2]


@staticmethod
def utf_16_be_decode(input, errors="strict", final=False):
    return _wide_decode(input, errors, 2, 1, final)[:2]


@staticmethod
def utf_32_decode(input, errors="strict", final=False):
    return _wide_decode(input, errors, 4, 0, final)[:2]


@staticmethod
def utf_32_le_decode(input, errors="strict", final=False):
    return _wide_decode(input, errors, 4, -1, final)[:2]


@staticmethod
def utf_32_be_decode(input, errors="strict", final=False):
    return _wide_decode(input, errors, 4, 1, final)[:2]


@staticmethod
def charmap_build(table):
    if not isinstance(table, str) or not table:
        raise TypeError("bad argument type for built-in operation")
    result = {}
    for position, char in enumerate(table[:256]):
        result[ord(char)] = position
    # Compact CPython maps reserve the undefined marker; general maps do not.
    compact = table[0] == "\x00" and "\x00" not in table[1:256]
    if compact:
        for char in table[:256]:
            if ord(char) > 0xFFFF:
                compact = False
                break
    if compact and 0xFFFE in result:
        del result[0xFFFE]
    return result


def _charmap_value(mapping, code, encoding):
    try:
        value = mapping[code]
    except LookupError:
        return None
    if value is None:
        return None
    if isinstance(value, int):
        limit = 256 if encoding else 0x110000
        if not 0 <= value < limit:
            raise TypeError(
                "character mapping must be in range(256)"
                if encoding
                else "character mapping must be in range(0x110000)"
            )
        return bytes([value]) if encoding else (None if value == 0xFFFE else chr(value))
    if isinstance(value, bytes if encoding else str):
        return None if not encoding and value == "\ufffe" else value
    raise TypeError(
        "character mapping must return integer, bytes or None, not " + type(value).__name__
        if encoding
        else "character mapping must return integer, None or str"
    )


@staticmethod
def charmap_encode(input, errors="strict", mapping=None):
    if mapping is None:
        return latin_1_encode(input, errors)
    input, errors = _arguments(input, errors, True)
    output = bytearray()
    pos = 0
    while pos < len(input):
        value = _charmap_value(mapping, ord(input[pos]), True)
        if value is not None:
            output.extend(value)
            pos += 1
            continue
        end = pos + 1
        while end < len(input) and _charmap_value(mapping, ord(input[end]), True) is None:
            end += 1
        exc = UnicodeEncodeError("charmap", input, pos, end, "character maps to <undefined>")
        replacement, pos = _error(errors, exc, True)
        if isinstance(replacement, str):
            try:
                replacement = charmap_encode(replacement, "strict", mapping)[0]
            except UnicodeEncodeError:
                raise exc  # noqa: B904
        output.extend(replacement)
        input = exc.object
    return bytes(output), len(input)


@staticmethod
def charmap_decode(input, errors="strict", mapping=None):
    if mapping is None:
        return latin_1_decode(input, errors)
    input, errors = _arguments(input, errors, False)
    output = []
    pos = 0
    while pos < len(input):
        value = _charmap_value(mapping, input[pos], False)
        if value is not None:
            output.append(value)
            pos += 1
        else:
            exc = UnicodeDecodeError(
                "charmap", input, pos, pos + 1, "character maps to <undefined>"
            )
            replacement, pos = _error(errors, exc, False)
            output.append(replacement)
            input = exc.object
    return "".join(output), len(input)
