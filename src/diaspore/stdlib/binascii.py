"""Binary-to-ASCII base64 and hexadecimal encodings and CRC checksums."""


class Error(ValueError):
    """Report invalid encoded data."""


class Incomplete(Exception):
    """Report incomplete encoded data."""


def _bytes(data):
    if isinstance(data, bytes):
        return data
    view = memoryview(data)
    if not view.c_contiguous:
        raise BufferError("memoryview: underlying buffer is not C-contiguous")
    return view.cast("B")


def _ascii(data):
    if isinstance(data, str):
        try:
            return data.encode("ascii")
        except UnicodeEncodeError:
            raise ValueError("string argument should contain only ASCII characters") from None
    return _bytes(data)


def _index(value):
    if isinstance(value, int):
        return value
    if hasattr(value, "__index__"):
        result = value.__index__()
        if isinstance(result, int):
            return result
    raise TypeError("an integer is required")


_NO_SEPARATOR = object()


def hexlify(data, sep=_NO_SEPARATOR, bytes_per_sep=1):
    """Return hexadecimal bytes, optionally separating fixed-size groups.

    Args:
        data: Bytes-like input.
        sep: Optional single-character separator.
        bytes_per_sep: Group size; negative values count from the left.

    Returns:
        The ASCII hexadecimal representation as bytes.
    """
    data = _bytes(data)
    group = _index(bytes_per_sep)
    if group < -2147483648 or group > 2147483647:
        raise OverflowError("Python int too large to convert to C int")
    separator = b""
    if sep is not _NO_SEPARATOR:
        if not isinstance(sep, (str, bytes)):
            raise TypeError("sep must be str or bytes.")
        if len(sep) != 1:
            raise ValueError("sep must be length 1.")
        if isinstance(sep, str):
            if ord(sep) > 255:
                raise ValueError("sep must be ASCII.")
            separator = bytes([ord(sep)])
        else:
            separator = sep
    out = []
    digits = b"0123456789abcdef"
    for index in range(len(data)):
        if separator and group and index:
            distance = len(data) - index if group > 0 else index
            if distance % abs(group) == 0:
                out.append(separator[0])
        value = data[index]
        out.extend([digits[value >> 4], digits[value & 15]])
    return bytes(out)


def unhexlify(data, /):
    """Decode an even-length ASCII hexadecimal string into bytes."""
    data = _ascii(data)
    if len(data) % 2:
        raise Error("Odd-length string")
    result = []
    for index in range(0, len(data), 2):
        value = 0
        for byte in data[index : index + 2]:
            if 48 <= byte <= 57:
                digit = byte - 48
            elif 65 <= byte <= 70:
                digit = byte - 55
            elif 97 <= byte <= 102:
                digit = byte - 87
            else:
                raise Error("Non-hexadecimal digit found")
            value = value * 16 + digit
        result.append(value)
    return bytes(result)


b2a_hex = hexlify
a2b_hex = unhexlify
_BASE64 = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_BASE64_VALUES = [-1] * 256
for _position in range(64):
    _BASE64_VALUES[_BASE64[_position]] = _position


def b2a_base64(data, /, *, newline=True):
    """Encode bytes-like data as base64, optionally appending a newline."""
    data = _bytes(data)
    out = []
    for index in range(0, len(data), 3):
        remaining = len(data) - index
        value = data[index] << 16
        if remaining > 1:
            value |= data[index + 1] << 8
        if remaining > 2:
            value |= data[index + 2]
        out.extend(
            [
                _BASE64[value >> 18],
                _BASE64[(value >> 12) & 63],
                _BASE64[(value >> 6) & 63] if remaining > 1 else 61,
                _BASE64[value & 63] if remaining > 2 else 61,
            ]
        )
    if newline:
        out.append(10)
    return bytes(out)


def a2b_base64(data, /, *, strict_mode=False):
    """Decode base64 data, rejecting noncanonical input in strict mode."""
    data = _ascii(data)
    out = []
    position = 0
    carry = 0
    pads = 0
    padding = False
    if strict_mode and data[:1] == b"=":
        raise Error("Leading padding not allowed")
    for index in range(len(data)):
        byte = data[index]
        if byte == 61:
            padding = True
            if strict_mode and position == 0:
                raise Error("Excess padding not allowed")
            if position >= 2:
                pads += 1
                if position + pads >= 4:
                    if strict_mode and index + 1 != len(data):
                        raise Error("Excess data after padding")
                    return bytes(out)
            continue
        value = _BASE64_VALUES[byte]
        if value < 0:
            if strict_mode:
                raise Error("Only base64 data is allowed")
            continue
        if strict_mode and padding:
            raise Error("Discontinuous padding not allowed")
        pads = 0
        if position == 0:
            carry = value
        elif position == 1:
            out.append((carry << 2) | (value >> 4))
            carry = value & 15
        elif position == 2:
            out.append((carry << 4) | (value >> 2))
            carry = value & 3
        else:
            out.append((carry << 6) | value)
        position = (position + 1) % 4
    if position == 1:
        count = len(out) // 3 * 4 + 1
        raise Error(
            "Invalid base64-encoded string: number of data characters ("
            + str(count)
            + ") cannot be 1 more than a multiple of 4"
        )
    if position:
        raise Error("Incorrect padding")
    return bytes(out)


def _crc_table():
    table = []
    for value in range(256):
        for _ in range(8):
            value = (value >> 1) ^ (3988292384 if value & 1 else 0)
        table.append(value)
    return table


_CRC_TABLE = _crc_table()


def crc32(data, crc=0, /):
    """Return the unsigned CRC-32 of data, continuing the supplied checksum."""
    data = _bytes(data)
    value = (_index(crc) ^ 4294967295) & 4294967295
    for byte in data:
        value = _CRC_TABLE[(value ^ byte) & 255] ^ (value >> 8)
    return value ^ 4294967295


def crc_hqx(data, crc, /):
    """Return the CRC-CCITT of data, continuing the supplied checksum."""
    data = _bytes(data)
    value = _index(crc) & 65535
    for byte in data:
        value ^= byte << 8
        for _ in range(8):
            value = ((value << 1) ^ (4129 if value & 32768 else 0)) & 65535
    return value
