"""Pack and unpack C records using the runtime's LP64 native ABI.

Standard layouts are platform independent. Native layouts use sys.byteorder,
64-bit long/pointer/size_t values, and their natural alignment.
IEEE binary16/32/64 use round-to-nearest, ties-to-even. NaN payloads are
canonicalized; their sign is preserved.
"""

import math
import sys

__all__ = [
    "calcsize",
    "pack",
    "pack_into",
    "unpack",
    "unpack_from",
    "iter_unpack",
    "Struct",
    "error",
]


class error(Exception):
    """Report an invalid format, value, or buffer size."""

    __module__ = "struct"


_SIZES = {
    "x": 1,
    "c": 1,
    "b": 1,
    "B": 1,
    "?": 1,
    "h": 2,
    "H": 2,
    "i": 4,
    "I": 4,
    "l": 4,
    "L": 4,
    "q": 8,
    "Q": 8,
    "e": 2,
    "f": 4,
    "d": 8,
    "s": 1,
    "p": 1,
}
_CACHE = {}


def _index(value):
    if isinstance(value, int):
        return value
    if hasattr(value, "__index__"):
        result = value.__index__()
        if isinstance(result, int):
            return result
        raise TypeError("__index__ returned non-int")
    raise TypeError("object cannot be interpreted as an integer")


def _buffer(value):
    if isinstance(value, bytes):
        return value
    view = memoryview(value)
    if not view.c_contiguous:
        raise BufferError("memoryview: underlying buffer is not C-contiguous")
    return view.cast("B")


def _float_bits(value, code, native=False):
    if (
        not isinstance(value, (int, float))
        and not hasattr(value, "__float__")
        and not hasattr(value, "__index__")
    ):
        raise error("required argument is not a float")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        raise error("required argument is not a float") from None
    mantissa, exponent_bits = (10, 5) if code == "e" else ((23, 8) if code == "f" else (52, 11))
    sign = 1 if math.copysign(1.0, value) < 0 else 0
    value = abs(value)
    maximum = (1 << exponent_bits) - 1
    bias = maximum >> 1
    if math.isnan(value):
        exponent = maximum
        fraction = 1 << (mantissa - 1)
    elif math.isinf(value):
        exponent = maximum
        fraction = 0
    elif value == 0:
        exponent = 0
        fraction = 0
    else:
        fraction_value, exponent = math.frexp(value)
        exponent += bias - 1
        if exponent <= 0:
            fraction = round(math.ldexp(value, mantissa + bias - 1))
            exponent = 0
            if fraction == 1 << mantissa:
                exponent = 1
                fraction = 0
        else:
            fraction = round(math.ldexp(fraction_value, mantissa + 1)) - (1 << mantissa)
            if fraction == 1 << mantissa:
                fraction = 0
                exponent += 1
            if exponent >= maximum:
                if native and code == "f":
                    exponent = maximum
                    fraction = 0
                else:
                    raise OverflowError("float too large to pack with " + code + " format")
    return (sign << (mantissa + exponent_bits)) | (exponent << mantissa) | fraction


def _bits_float(value, code):
    mantissa, exponent_bits = (10, 5) if code == "e" else ((23, 8) if code == "f" else (52, 11))
    maximum = (1 << exponent_bits) - 1
    sign = value >> (mantissa + exponent_bits)
    exponent = (value >> mantissa) & maximum
    fraction = value & ((1 << mantissa) - 1)
    if exponent == maximum:
        result = float("nan") if fraction else float("inf")
    elif exponent == 0:
        result = math.ldexp(float(fraction), 1 - (maximum >> 1) - mantissa)
    else:
        result = math.ldexp(float((1 << mantissa) | fraction), exponent - (maximum >> 1) - mantissa)
    return -result if sign else result


class Struct:
    """Compile a binary record format for repeated packing and unpacking."""

    def __init__(self, format):
        if isinstance(format, bytes):
            try:
                format = format.decode("ascii")
            except UnicodeDecodeError:
                raise error("bad char in struct format") from None
        if not isinstance(format, str):
            raise TypeError("Struct() argument 1 must be a str or bytes object")
        format.encode("ascii")
        if "\x00" in format:
            raise error("embedded null character")
        self._format = format
        mode = "@"
        pos = 0
        if format and format[0] in "@=<>!":
            mode = format[0]
            pos = 1
        self._native = mode == "@"
        self._little = mode == "<" or (mode in "@=" and sys.byteorder == "little")
        self._fields = []
        self._size = 0
        self._count = 0
        while pos < len(format):
            code = format[pos]
            if code in " \t\n\r\v\f":
                pos += 1
                continue
            count = 0
            counted = False
            while pos < len(format) and "0" <= format[pos] <= "9":
                counted = True
                count = count * 10 + ord(format[pos]) - 48
                if count > sys.maxsize:
                    raise error("total struct size too long")
                pos += 1
            if pos == len(format):
                raise error("repeat count given without format specifier")
            if not counted:
                count = 1
            code = format[pos]
            pos += 1
            size = _SIZES.get(code, 0)
            if mode == "@" and code in "lLnNP":
                size = 8
            if not size:
                raise error("bad char in struct format")
            alignment = size if mode == "@" else 1
            offset = self.size + (-self.size % alignment)
            self._size = offset + size * count
            if self.size > sys.maxsize:
                raise error("total struct size too long")
            self._fields.append((code, count, size, offset))
            self._count += 1 if code in "sp" else (0 if code == "x" else count)

    @property
    def format(self):
        return self._format

    @property
    def size(self):
        return self._size

    def pack(self, *values):
        if len(values) != self._count:
            raise error(
                "pack expected "
                + str(self._count)
                + " items for packing (got "
                + str(len(values))
                + ")"
            )
        out = [0] * self.size
        self._pack(out, 0, values)
        return bytes(out)

    def _pack(self, out, start, values):
        arg = 0
        for code, count, size, offset in self._fields:
            offset += start
            if code == "x":
                continue
            if code in "sp":
                value = values[arg]
                arg += 1
                if not isinstance(value, (bytes, bytearray)):
                    raise error("argument for '" + code + "' must be a bytes object")
                length = min(len(value), max(0, count - (1 if code == "p" else 0)))
                data_offset = offset
                if code == "p" and count:
                    out[offset] = min(length, 255)
                    data_offset += 1
                for index in range(length):
                    out[data_offset + index] = value[index]
                continue
            for index in range(count):
                value = values[arg]
                arg += 1
                if code == "c":
                    if not isinstance(value, bytes) or len(value) != 1:
                        raise error("char format requires a bytes object of length 1")
                    value = value[0]
                elif code == "?":
                    value = 1 if value else 0
                elif code in "efd":
                    try:
                        value = _float_bits(value, code, self._native)
                    except OverflowError:
                        if isinstance(value, int):
                            raise error("int too large to convert") from None
                        raise
                else:
                    if not isinstance(value, int) and not hasattr(value, "__index__"):
                        raise error("required argument is not an integer")
                    value = _index(value)
                    signed = code in "bhilqn"
                    low = -(1 << (size * 8 - 1)) if signed else 0
                    high = (1 << (size * 8 - (1 if signed else 0))) - 1
                    if code == "P":
                        low = -(1 << (size * 8 - 1))
                    if value < low or value > high:
                        if code == "P":
                            if isinstance(values[arg - 1], int):
                                raise error("int too large to convert")
                            raise OverflowError(
                                "Python int too large to convert to C unsigned long"
                            )
                        raise error(
                            "'"
                            + code
                            + "' format requires "
                            + str(low)
                            + " <= number <= "
                            + str(high)
                        )
                for byte in range(size):
                    shift = byte if self._little else size - byte - 1
                    out[offset + index * size + byte] = (value >> (shift * 8)) & 255

    def unpack(self, buffer, /):
        buffer = _buffer(buffer)
        if len(buffer) != self.size:
            raise error("unpack requires a buffer of " + str(self.size) + " bytes")
        return self._unpack(buffer, 0)

    def _unpack(self, buffer, start):
        out = []
        for code, count, size, offset in self._fields:
            offset += start
            if code == "x":
                continue
            if code == "s":
                out.append(bytes(buffer[offset : offset + count]))
                continue
            if code == "p":
                if not count:
                    raise SystemError("Negative size passed to PyBytes_FromStringAndSize")
                length = min(buffer[offset], count - 1)
                out.append(bytes(buffer[offset + 1 : offset + 1 + length]))
                continue
            for index in range(count):
                value = 0
                for byte in range(size):
                    shift = byte if self._little else size - byte - 1
                    value |= buffer[offset + index * size + byte] << (shift * 8)
                if code == "c":
                    value = bytes([value])
                elif code == "?":
                    value = bool(value)
                elif code in "efd":
                    value = _bits_float(value, code)
                elif code in "bhilqn" and value & (1 << (size * 8 - 1)):
                    value -= 1 << (size * 8)
                out.append(value)
        return tuple(out)

    def _offset(self, buffer, offset, operation):
        offset = _index(offset)
        if offset < -sys.maxsize - 1 or offset > sys.maxsize:
            if operation == "pack_into":
                raise IndexError("cannot fit 'int' into an index-sized integer")
            raise OverflowError("Python int too large to convert to C ssize_t")
        if offset < 0:
            if offset + self.size > 0:
                action = (
                    "no space to pack "
                    if operation == "pack_into"
                    else "not enough data to unpack "
                )
                raise error(action + str(self.size) + " bytes at offset " + str(offset))
            if offset + len(buffer) < 0:
                raise error(
                    "offset "
                    + str(offset)
                    + " out of range for "
                    + str(len(buffer))
                    + "-byte buffer"
                )
            offset += len(buffer)
        if offset + self.size > len(buffer):
            action = "packing" if operation == "pack_into" else "unpacking"
            raise error(
                operation
                + " requires a buffer of at least "
                + str(offset + self.size)
                + " bytes for "
                + action
                + " "
                + str(self.size)
                + " bytes at offset "
                + str(offset)
                + " (actual buffer size is "
                + str(len(buffer))
                + ")"
            )
        return offset

    def pack_into(self, buffer, offset, /, *values):
        if len(values) != self._count:
            raise error(
                "pack_into expected "
                + str(self._count)
                + " items for packing (got "
                + str(len(values))
                + ")"
            )
        buffer = memoryview(_buffer(buffer))
        if buffer.readonly:
            raise TypeError("argument must be read-write bytes-like object")
        offset = self._offset(buffer, offset, "pack_into")
        for index in range(offset, offset + self.size):
            buffer[index] = 0
        self._pack(buffer, offset, values)

    def unpack_from(self, buffer, offset=0):
        buffer = _buffer(buffer)
        return self._unpack(buffer, self._offset(buffer, offset, "unpack_from"))

    def iter_unpack(self, buffer, /):
        buffer = _buffer(buffer)
        if self.size == 0:
            raise error("cannot iteratively unpack with a struct of length 0")
        if len(buffer) % self.size:
            raise error(
                "iterative unpacking requires a buffer of a multiple of "
                + str(self.size)
                + " bytes"
            )
        return _UnpackIterator(self, buffer)


class _UnpackIterator:
    def __init__(self, struct, buffer):
        self._struct = struct
        self._buffer = buffer
        self._pos = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._pos == len(self._buffer):
            raise StopIteration
        result = self._struct._unpack(self._buffer, self._pos)
        self._pos += self._struct.size
        return result

    def __length_hint__(self):
        return (len(self._buffer) - self._pos) // self._struct.size


def _compiled(format):
    result = _CACHE.get(format)
    if result is None:
        result = Struct(format)
        if len(_CACHE) >= 100:
            _CACHE.clear()
        _CACHE[format] = result
    return result


def _clearcache():
    _CACHE.clear()


def calcsize(format, /):
    return _compiled(format).size


def pack(format, /, *values):
    return _compiled(format).pack(*values)


def unpack(format, buffer, /):
    return _compiled(format).unpack(buffer)


def pack_into(format, buffer, offset, /, *values):
    return _compiled(format).pack_into(buffer, offset, *values)


def unpack_from(format, /, buffer, offset=0):
    return _compiled(format).unpack_from(buffer, offset)


def iter_unpack(format, buffer, /):
    return _compiled(format).iter_unpack(buffer)
