"""Standard, URL-safe and line-wrapped base64, plus base16 encoding."""

import binascii

_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _bytes(value):
    if isinstance(value, str):
        for char in value:
            if ord(char) > 127:
                raise ValueError("string argument should contain only ASCII characters")
        return value.encode("ascii")
    if not isinstance(value, bytes):
        raise TypeError("argument should be a bytes-like object or ASCII string")
    return value


def b64encode(s, altchars=None):
    if not isinstance(s, bytes):
        raise TypeError("a bytes-like object is required")
    alphabet = _ALPHABET
    if altchars is not None:
        if not isinstance(altchars, bytes):
            raise TypeError("a bytes-like object is required")
        assert len(altchars) == 2, repr(altchars)
        alphabet = alphabet[:62] + altchars
    result = []
    for index in range(0, len(s), 3):
        count = min(3, len(s) - index)
        value = s[index] << 16
        if count > 1:
            value |= s[index + 1] << 8
        if count > 2:
            value |= s[index + 2]
        result.append(alphabet[(value >> 18) & 63])
        result.append(alphabet[(value >> 12) & 63])
        result.append(alphabet[(value >> 6) & 63] if count > 1 else 61)
        result.append(alphabet[value & 63] if count > 2 else 61)
    return bytes(result)


def b64decode(s, altchars=None, validate=False):
    s = _bytes(s)
    if altchars is not None:
        altchars = _bytes(altchars)
        assert len(altchars) == 2, repr(altchars)
        # Translation is simultaneous even when the replacement characters overlap.
        s = bytes([43 if c == altchars[0] else 47 if c == altchars[1] else c for c in s])
    result = []
    value = 0
    count = 0
    padding = 0
    for index, char in enumerate(s):
        if char == 61:
            if validate and count == 0:
                raise binascii.Error("Leading padding not allowed")
            padding += 1
            if count >= 2 and count + padding >= 4:
                if validate and index + 1 != len(s):
                    raise binascii.Error("Excess data after padding")
                return bytes(result)
            continue
        digit = _ALPHABET.find(bytes([char]))
        if digit < 0:
            if validate:
                raise binascii.Error("Only base64 data is allowed")
            continue
        if validate and padding:
            raise binascii.Error("Discontinuous padding not allowed")
        padding = 0
        value = (value << 6) | digit
        count += 1
        if count == 2:
            result.append((value >> 4) & 255)
        elif count == 3:
            result.append((value >> 2) & 255)
        elif count == 4:
            result.append(value & 255)
            value = 0
            count = 0
    if count == 1:
        length = (len(result) // 3) * 4 + 1
        raise binascii.Error(
            f"Invalid base64-encoded string: number of data characters ({length}) "
            "cannot be 1 more than a multiple of 4"
        )
    if count:
        raise binascii.Error("Incorrect padding")
    return bytes(result)


standard_b64encode = b64encode
standard_b64decode = b64decode


def urlsafe_b64encode(s):
    return b64encode(s, b"-_")


def urlsafe_b64decode(s):
    return b64decode(s, b"-_")


def encodebytes(s):
    if not isinstance(s, bytes):
        raise TypeError("expected bytes-like object")
    result = b""
    for index in range(0, len(s), 57):
        result += b64encode(s[index:index + 57]) + b"\n"
    return result


def decodebytes(s):
    if not isinstance(s, bytes):
        raise TypeError("expected bytes-like object")
    return b64decode(s)


def b16encode(s):
    return binascii.hexlify(s).upper()


def b16decode(s, casefold=False):
    s = _bytes(s)
    if not casefold and any(97 <= char <= 102 for char in s):
        raise binascii.Error("Non-base16 digit found")
    return binascii.unhexlify(s)
