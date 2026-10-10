"""Portable CPython 3.12 Unicode 15.0.0 character data and normalization.

All code points, including unassigned characters, noncharacters, surrogates,
and supplementary planes, use the pinned CPython database. Name lookup includes
formal aliases, named sequences, and algorithmic Hangul and ideograph names.
NFC, NFD, NFKC, and NFKD use recursive decomposition, canonical ordering, and
composition exclusions. Generated ASCII constants are decoded and cached by
table group on first lookup, with no codec, JSON, or compression dependencies.
The historical ucd_3_2_0 database is not supported; its methods raise
NotImplementedError rather than substituting current data.
"""

import _unicode15 as _data

unidata_version = "15.0.0"
_missing = object()
_properties = None
_decompositions = None
_compositions = None
_names = None
_ranges = None
_L = (
    "G",
    "GG",
    "N",
    "D",
    "DD",
    "R",
    "M",
    "B",
    "BB",
    "S",
    "SS",
    "",
    "J",
    "JJ",
    "C",
    "K",
    "T",
    "P",
    "H",
)
_V = (
    "A",
    "AE",
    "YA",
    "YAE",
    "EO",
    "E",
    "YEO",
    "YE",
    "O",
    "WA",
    "WAE",
    "OE",
    "YO",
    "U",
    "WEO",
    "WE",
    "WI",
    "YU",
    "EU",
    "YI",
    "I",
)
_T = (
    "",
    "G",
    "GG",
    "GS",
    "N",
    "NJ",
    "NH",
    "D",
    "L",
    "LG",
    "LM",
    "LB",
    "LS",
    "LT",
    "LP",
    "LH",
    "M",
    "B",
    "BS",
    "S",
    "SS",
    "NG",
    "J",
    "C",
    "K",
    "T",
    "P",
    "H",
)


def _integers(text, delta=False):
    values = []
    previous = 0
    for field in text.split():
        value = int(field, 16)
        if delta:
            value += previous
        values.append(value)
        previous = value
    return values


def _load_properties():
    global _properties
    if _properties is None:
        records = []
        for line in _data.PROPERTY_RECORDS.split("\n"):
            fields = line.split("\t")
            records.append(
                (
                    fields[0],
                    int(fields[1]),
                    fields[2],
                    fields[3],
                    int(fields[4]),
                    int(fields[5]) if fields[5] else None,
                    int(fields[6]) if fields[6] else None,
                    float(fields[7]) if fields[7] else None,
                )
            )
        _properties = (
            _integers(_data.PROPERTY_STARTS, True),
            _integers(_data.PROPERTY_INDICES),
            records,
        )
    return _properties


def _load_decompositions():
    global _decompositions
    if _decompositions is None:
        values = []
        for raw in _data.DECOMPOSITION_VALUES.split("\n"):
            fields = raw.split()
            compatibility = fields[0].startswith("<")
            values.append((raw, compatibility, [int(part, 16) for part in fields[compatibility:]]))
        _decompositions = (_integers(_data.DECOMPOSITION_KEYS, True), values)
    return _decompositions


def _load_compositions():
    global _compositions
    if _compositions is None:
        _compositions = (
            _integers(_data.COMPOSITION_KEYS, True),
            _integers(_data.COMPOSITION_VALUES),
        )
    return _compositions


def _load_ranges():
    global _ranges
    if _ranges is None:
        import _unicode15_names as data

        ranges = []
        for line in data.ALGORITHMIC_RANGES.split("\n"):
            fields = line.split("\t")
            ranges.append((int(fields[0], 16), int(fields[1], 16), fields[2]))
        _ranges = ranges
    return _ranges


def _load_names():
    global _names
    if _names is None:
        import _unicode15_names as data

        names = []
        previous = ""
        for line in data.NAMES.split("\n"):
            common, suffix = line.split(":", 1)
            previous = previous[: int(common, 16)] + suffix
            names.append(previous)
        values = []
        for line in data.VALUES.split("\n"):
            values.append("".join(chr(code) for code in _integers(line)))
        _names = (
            names,
            values,
            _integers(data.PRIMARY_CODEPOINTS, True),
            _integers(data.PRIMARY_INDICES),
        )
    return _names


def _find(keys, value):
    low = 0
    high = len(keys)
    while low < high:
        middle = (low + high) // 2
        if keys[middle] < value:
            low = middle + 1
        else:
            high = middle
    if low < len(keys) and keys[low] == value:
        return low
    return -1


def _codepoint(char, function, position=""):
    if not isinstance(char, str) or len(char) != 1:
        raise TypeError(
            function
            + "() argument"
            + position
            + " must be a unicode character, not "
            + type(char).__name__
        )
    return ord(char)


def _record(code):
    starts, indices, records = _load_properties()
    low = 0
    high = len(starts)
    while low < high:
        middle = (low + high) // 2
        if starts[middle] <= code:
            low = middle + 1
        else:
            high = middle
    return records[indices[low - 1]]


def category(chr, /):
    """Return the general category of one Unicode character."""
    return _record(_codepoint(chr, "category"))[0]


def combining(chr, /):
    """Return the canonical combining class of one Unicode character."""
    return _record(_codepoint(chr, "combining"))[1]


def bidirectional(chr, /):
    """Return the bidirectional class, or an empty string if undefined."""
    return _record(_codepoint(chr, "bidirectional"))[2]


def east_asian_width(chr, /):
    """Return the East Asian width property of one Unicode character."""
    return _record(_codepoint(chr, "east_asian_width"))[3]


def mirrored(chr, /):
    """Return one for a mirrored character and zero otherwise."""
    return _record(_codepoint(chr, "mirrored"))[4]


def decimal(chr, default=_missing, /):
    """Return the decimal value, the supplied default, or raise ValueError."""
    value = _record(_codepoint(chr, "decimal", " 1"))[5]
    if value is not None:
        return value
    if default is not _missing:
        return default
    raise ValueError("not a decimal")


def digit(chr, default=_missing, /):
    """Return the digit value, the supplied default, or raise ValueError."""
    value = _record(_codepoint(chr, "digit", " 1"))[6]
    if value is not None:
        return value
    if default is not _missing:
        return default
    raise ValueError("not a digit")


def numeric(chr, default=_missing, /):
    """Return the numeric value as a float, a default, or raise ValueError."""
    value = _record(_codepoint(chr, "numeric", " 1"))[7]
    if value is not None:
        return value
    if default is not _missing:
        return default
    raise ValueError("not a numeric character")


def name(chr, default=_missing, /):
    """Return the primary name, a supplied default, or raise ValueError."""
    code = _codepoint(chr, "name", " 1")
    if 0xAC00 <= code < 0xD7A4:
        syllable = code - 0xAC00
        return (
            "HANGUL SYLLABLE "
            + _L[syllable // 588]
            + _V[(syllable % 588) // 28]
            + _T[syllable % 28]
        )
    for start, end, prefix in _load_ranges():
        if start <= code <= end:
            return prefix + format(code, "04X")
    names, _, codepoints, indices = _load_names()
    index = _find(codepoints, code)
    if index >= 0:
        return names[indices[index]]
    if default is not _missing:
        return default
    raise ValueError("no such name")


def _syllable_part(text, position, choices):
    best = -1
    length = -1
    for index in range(len(choices)):
        part = choices[index]
        if len(part) > length and text.startswith(part, position):
            best = index
            length = len(part)
    return best, position + max(length, 0)


def _lookup(text):
    # CPython's algorithmic Hangul and unified-ideograph paths are case-sensitive.
    if text.startswith("HANGUL SYLLABLE "):
        leading, position = _syllable_part(text, 16, _L)
        vowel, position = _syllable_part(text, position, _V)
        trailing, position = _syllable_part(text, position, _T)
        if leading >= 0 and vowel >= 0 and trailing >= 0 and position == len(text):
            return chr(0xAC00 + (leading * 21 + vowel) * 28 + trailing)
        return None
    upper = text.upper()
    for start, end, prefix in _load_ranges():
        candidate = upper
        if prefix == "CJK UNIFIED IDEOGRAPH-":
            candidate = text
        if candidate.startswith(prefix):
            suffix = candidate[len(prefix) :]
            if len(suffix) < 4 or len(suffix) > 5:
                continue
            valid = True
            for char in suffix:
                if char not in "0123456789ABCDEF":
                    valid = False
                    break
            if valid:
                code = int(suffix, 16)
                if start <= code <= end:
                    if prefix == "CJK UNIFIED IDEOGRAPH-" or suffix == format(code, "04X"):
                        return chr(code)
    names, values, _, _ = _load_names()
    index = _find(names, upper)
    if index >= 0:
        return values[index]
    return None


def lookup(name, /):
    """Return the character or named sequence for a Unicode name.

    Raises:
        KeyError: The name is not in Unicode 15.0.0.
        TypeError: The argument is not a string or read-only byte string.
    """
    if isinstance(name, str):
        encoded = name.encode("utf-8")
        text = name
    elif isinstance(name, bytes):
        encoded = name
        text = name.decode("utf-8", "replace")
    else:
        raise TypeError(
            "lookup() argument must be str or read-only bytes-like object, not "
            + type(name).__name__
        )
    if len(encoded) > 256:
        raise KeyError("name too long")
    # Unicode case expansion must not turn a non-ASCII spelling into a name.
    ascii_only = True
    for byte in encoded:
        if byte >= 128:
            ascii_only = False
            break
    if ascii_only:
        result = _lookup(text)
        if result is not None:
            return result
    raise KeyError("undefined character name '" + text.split("\x00", 1)[0] + "'")


def decomposition(chr, /):
    """Return the tagged hexadecimal decomposition mapping, or an empty string."""
    code = _codepoint(chr, "decomposition")
    keys, values = _load_decompositions()
    index = _find(keys, code)
    if index < 0:
        return ""
    return values[index][0]


def _decompose(code, compatibility, output):
    if 0xAC00 <= code < 0xD7A4:
        syllable = code - 0xAC00
        output.append(0x1100 + syllable // 588)
        output.append(0x1161 + (syllable % 588) // 28)
        trailing = syllable % 28
        if trailing:
            output.append(0x11A7 + trailing)
        return
    keys, values = _load_decompositions()
    index = _find(keys, code)
    if index >= 0:
        mapping = values[index]
        if compatibility or not mapping[1]:
            for part in mapping[2]:
                _decompose(part, compatibility, output)
            return
    output.append(code)


def _compose_pair(first, second):
    if 0x1100 <= first < 0x1113 and 0x1161 <= second < 0x1176:
        return 0xAC00 + ((first - 0x1100) * 21 + second - 0x1161) * 28
    if 0xAC00 <= first < 0xD7A4 and (first - 0xAC00) % 28 == 0 and 0x11A8 <= second < 0x11C3:
        return first + second - 0x11A7
    keys, values = _load_compositions()
    index = _find(keys, first * 0x110000 + second)
    if index >= 0:
        return values[index]
    return -1


def _normalize(form, text, function):
    if not isinstance(form, str):
        raise TypeError(function + "() argument 1 must be str, not " + type(form).__name__)
    if not isinstance(text, str):
        raise TypeError(function + "() argument 2 must be str, not " + type(text).__name__)
    # CPython returns early for empty input even if the form string is invalid.
    if not text:
        return text
    if form not in ("NFC", "NFD", "NFKC", "NFKD"):
        raise ValueError("invalid normalization form")
    ascii_only = True
    for char in text:
        if ord(char) >= 128:
            ascii_only = False
            break
    if ascii_only:
        return text
    codes = []
    compatibility = form == "NFKC" or form == "NFKD"
    for char in text:
        _decompose(ord(char), compatibility, codes)
    classes = []
    for index in range(len(codes)):
        combining_class = _record(codes[index])[1]
        classes.append(combining_class)
        if combining_class:
            position = index
            while position > 0 and classes[position - 1] > combining_class:
                codes[position - 1], codes[position] = codes[position], codes[position - 1]
                classes[position - 1], classes[position] = classes[position], classes[position - 1]
                position -= 1
    if form == "NFC" or form == "NFKC":
        # Compact in place. A mark blocks composition only if it survives.
        starter = -1
        last_class = 0
        written = 0
        for index in range(len(codes)):
            code = codes[index]
            combining_class = classes[index]
            composite = -1
            if starter >= 0 and (last_class == 0 or last_class < combining_class):
                composite = _compose_pair(codes[starter], code)
            if composite >= 0:
                codes[starter] = composite
            else:
                if combining_class == 0:
                    starter = written
                codes[written] = code
                written += 1
                last_class = combining_class
        del codes[written:]
    return "".join(chr(code) for code in codes)


def normalize(form, unistr, /):
    """Return Unicode normalization in NFC, NFD, NFKC, or NFKD form."""
    return _normalize(form, unistr, "normalize")


def is_normalized(form, unistr, /):
    """Return whether the string is already in the requested normal form."""
    return _normalize(form, unistr, "is_normalized") == unistr


class UCD:
    """Identify the unavailable historical database without substituting data."""

    unidata_version = "3.2.0"

    def __new__(cls, *args, **kwargs):
        """Reject construction of historical Unicode database instances."""
        raise TypeError("cannot create 'unicodedata.UCD' instances")

    def _unsupported(self, *args, **kwargs):
        raise NotImplementedError("the Unicode 3.2.0 database is not supported")

    name = _unsupported
    lookup = _unsupported
    category = _unsupported
    combining = _unsupported
    bidirectional = _unsupported
    east_asian_width = _unsupported
    mirrored = _unsupported
    decimal = _unsupported
    digit = _unsupported
    numeric = _unsupported
    decomposition = _unsupported
    normalize = _unsupported
    is_normalized = _unsupported


ucd_3_2_0 = object.__new__(UCD)
