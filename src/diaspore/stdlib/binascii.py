"""Binary encodings used by base64."""


class Error(ValueError):
    pass


def hexlify(data, sep=None, bytes_per_sep=1):
    if not isinstance(data, bytes):
        raise TypeError("a bytes-like object is required")
    if sep is not None:
        raise NotImplementedError("hexlify separators are not supported")
    return "".join([f"{value:02x}" for value in data]).encode("ascii")


def unhexlify(data):
    if isinstance(data, bytes):
        data = data.decode("ascii")
    if len(data) % 2:
        raise Error("Odd-length string")
    result = []
    digits = "0123456789abcdef"
    for index in range(0, len(data), 2):
        high = digits.find(data[index].lower())
        low = digits.find(data[index + 1].lower())
        if high < 0 or low < 0:
            raise Error("Non-hexadecimal digit found")
        result.append(high * 16 + low)
    return bytes(result)


b2a_hex = hexlify
a2b_hex = unhexlify


def b2a_base64(data, newline=True):
    from base64 import b64encode
    result = b64encode(data)
    return result + b"\n" if newline else result


def a2b_base64(data, strict_mode=False):
    from base64 import b64decode
    return b64decode(data, validate=strict_mode)
