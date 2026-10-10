"""Portable C locale primitives, independent of the host process locale.

Only C and its POSIX alias are supported. Empty locale names consult the
sandbox environment, not the host environment; unsupported settings fail.
The C locale uses ASCII encoding, empty grouping and ordinal collation.
"""

import posix as _posix

CHAR_MAX = 127
LC_CTYPE = 0
LC_NUMERIC = 1
LC_TIME = 2
LC_COLLATE = 3
LC_MONETARY = 4
LC_MESSAGES = 5
LC_ALL = 6


class Error(Exception):
    """An invalid category or unsupported locale was requested."""


_categories = ("LC_CTYPE", "LC_NUMERIC", "LC_TIME", "LC_COLLATE", "LC_MONETARY", "LC_MESSAGES")


def _category(value):
    value = _posix._integer(value)
    if value < -2147483648 or value > 2147483647:
        raise OverflowError("Python int too large to convert to C int")
    return value


def _environment_locale(category):
    for key in ("LC_ALL", _categories[category], "LANG"):
        value = _posix._getenv(key)
        if value:
            return value
    return "C"


def setlocale(category, locale=None, /):
    """Query the C locale or select C/POSIX without changing host state."""
    category = _category(category)
    if locale is not None:
        if not isinstance(locale, str):
            raise TypeError("locale must be a string or None")
        if "\x00" in locale:
            raise ValueError("embedded null character")
    if category < LC_CTYPE or category > LC_ALL:
        raise Error("locale query failed" if locale is None else "unsupported locale setting")
    if locale is None:
        return "C"
    if locale == "":
        categories = range(6) if category == LC_ALL else (category,)
        for item in categories:
            if _environment_locale(item) not in ("C", "POSIX"):
                raise Error("unsupported locale setting")
    elif locale not in ("C", "POSIX"):
        raise Error("unsupported locale setting")
    return "C"


def localeconv():
    """Return a fresh dictionary containing the C locale's conventions."""
    return {
        "decimal_point": ".",
        "thousands_sep": "",
        "grouping": [],
        "int_curr_symbol": "",
        "currency_symbol": "",
        "mon_decimal_point": "",
        "mon_thousands_sep": "",
        "mon_grouping": [],
        "positive_sign": "",
        "negative_sign": "",
        "int_frac_digits": CHAR_MAX,
        "frac_digits": CHAR_MAX,
        "p_cs_precedes": CHAR_MAX,
        "p_sep_by_space": CHAR_MAX,
        "n_cs_precedes": CHAR_MAX,
        "n_sep_by_space": CHAR_MAX,
        "p_sign_posn": CHAR_MAX,
        "n_sign_posn": CHAR_MAX,
    }


def _text(value):
    if not isinstance(value, str):
        raise TypeError("str argument required")
    if "\x00" in value:
        raise ValueError("embedded null character")
    return value


def strcoll(a, b, /):
    """Compare strings in C locale ordinal order."""
    a = _text(a)
    b = _text(b)
    return (a > b) - (a < b)


def strxfrm(string, /):
    """Return the ordinal sort key for a C locale string."""
    return _text(string)


def getencoding():
    """Return the C locale encoding, unaffected by UTF-8 mode."""
    return "ANSI_X3.4-1968"
