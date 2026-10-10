"""CPython 3.12 marshal data records, versions 0 through 4.

Code objects are not portable Diaspore IR and raise ValueError. This module
never loads CPython bytecode. The nesting limit matches CPython's Linux
limit of 2000 levels. As in CPython, do not decode untrusted data.
"""

import _struct

version = 4
_NULL = object()


class _Pending:
    def __init__(self):
        self.targets = []

    def finish(self, value):
        for container, key in self.targets:
            container[key] = value


class _Reader:
    def __init__(self, data, file):
        self.data = data
        self.file = file
        self.pos = 0
        self.refs = []

    def take(self, size):
        if self.file is None:
            end = self.pos + size
            if end > len(self.data):
                raise EOFError("marshal data too short")
            result = self.data[self.pos:end]
            self.pos = end
            return result
        chunks = []
        while size:
            count = min(size, 65536)
            buffer = bytearray(count)
            received = self.file.readinto(buffer)
            if not isinstance(received, int):
                try:
                    received = received.__index__()
                except AttributeError:
                    raise TypeError("readinto() returned non-integer") from None
                if not isinstance(received, int):
                    raise TypeError("__index__ returned non-int")
            if received < count:
                raise EOFError("EOF read where not expected")
            if received > count:
                raise ValueError("read() returned too much data")
            chunks.append(bytes(buffer))
            size -= count
        return b"".join(chunks)

    def number(self):
        return _struct.unpack("<i", self.take(4))[0]

    def size(self):
        size = self.number()
        if size < 0:
            raise ValueError("bad marshal data (size out of range)")
        return size

    def text_float(self):
        return float(self.take(self.take(1)[0]).decode("ascii"))

    def item(self):
        stack = [self.record(False)]
        value = None
        while stack:
            try:
                allow_null = stack[-1].send(value)
            except StopIteration as finished:
                stack.pop()
                value = finished.value
            else:
                if len(stack) >= 2000:
                    raise ValueError("recursion limit exceeded")
                stack.append(self.record(allow_null))
                value = None
        return value

    def record(self, null):
        code = self.take(1)[0]
        tag = chr(code & 127)
        flagged = bool(code & 128)
        if tag == "0":
            if null:
                return _NULL
            raise TypeError("NULL object in marshal data for object")
        if tag == "N":
            return None
        if tag == "F":
            return False
        if tag == "T":
            return True
        if tag == "S":
            return StopIteration
        if tag == ".":
            return Ellipsis
        if tag == "c":
            raise ValueError("CPython code objects are unsupported by Diaspore marshal")
        if tag == "r":
            index = self.number()
            if index < 0 or index >= len(self.refs):
                raise ValueError("bad marshal data (invalid reference)")
            return self.refs[index]
        index = len(self.refs)
        pending = _Pending()
        if flagged:
            self.refs.append(pending)
        if tag == "i":
            value = self.number()
        elif tag == "I":
            value = int.from_bytes(self.take(8), "little", signed=True)
        elif tag == "l":
            count = self.number()
            if count == -2147483648:
                raise ValueError("bad marshal data (long size out of range)")
            value = 0
            for position in range(abs(count)):
                digit = _struct.unpack("<H", self.take(2))[0]
                if digit >= 32768:
                    raise ValueError("bad marshal data (digit out of range in long)")
                if digit == 0 and position == abs(count) - 1:
                    raise ValueError("bad marshal data (unnormalized long data)")
                value |= digit << (15 * position)
            if count < 0:
                value = -value
        elif tag == "f":
            value = self.text_float()
        elif tag == "g":
            value = _struct.unpack("<d", self.take(8))[0]
        elif tag == "x":
            value = complex(self.text_float(), self.text_float())
        elif tag == "y":
            real, imag = _struct.unpack("<dd", self.take(16))
            value = complex(real, imag)
        elif tag in ("s", "u", "t", "a", "A", "z", "Z"):
            size = self.take(1)[0] if tag in ("z", "Z") else self.size()
            value = self.take(size)
            if tag != "s":
                encoding = "ascii" if tag in ("a", "A", "z", "Z") else "utf-8"
                value = value.decode(encoding, "surrogatepass")
        elif tag in ("[", "(", ")", "<", ">"):
            size = self.take(1)[0] if tag == ")" else self.size()
            value = [] if tag in ("[", "(", ")") else set()
            if flagged and tag in ("[", "<"):
                self.refs[index] = value
            for position in range(size):
                child = yield False
                if tag in ("[", "(", ")"):
                    value.append(child)
                    if isinstance(child, _Pending):
                        if tag != "[":
                            raise ValueError("bad marshal data (invalid reference)")
                        child.targets.append((value, position))
                else:
                    if isinstance(child, _Pending):
                        raise ValueError("bad marshal data (invalid reference)")
                    value.add(child)
            if tag in ("(", ")"):
                value = tuple(value)
            elif tag == ">":
                value = frozenset(value)
        elif tag == "{":
            value = {}
            if flagged:
                self.refs[index] = value
            while True:
                key = yield True
                if key is _NULL:
                    break
                if isinstance(key, _Pending):
                    raise ValueError("bad marshal data (invalid reference)")
                child = yield True
                if child is _NULL:
                    break
                value[key] = child
                if isinstance(child, _Pending):
                    child.targets.append((value, key))
        else:
            raise ValueError("bad marshal data (unknown type code)")
        if flagged:
            self.refs[index] = value
            pending.finish(value)
        return value


class _Writer:
    def __init__(self, format_version):
        self.version = format_version
        self.refs = {}
        self.objects = []
        self.parts = []

    def number(self, value):
        self.parts.append(_struct.pack("<i", value))

    def text_float(self, value):
        data = format(value, ".17g").encode("ascii")
        self.parts.append(bytes([len(data)]))
        self.parts.append(data)

    def item(self, value):
        stack = [self.record(value)]
        while stack:
            try:
                child = next(stack[-1])
            except StopIteration:
                stack.pop()
            else:
                if len(stack) >= 2000:
                    raise ValueError("object too deeply nested to marshal")
                stack.append(self.record(child))

    def record(self, value):
        for singleton, tag in ((None, b"N"), (False, b"F"), (True, b"T"),
                               (StopIteration, b"S"), (Ellipsis, b".")):
            if value is singleton:
                self.parts.append(tag)
                return
        flag = 0
        if self.version >= 3:
            identity = id(value)
            if identity in self.refs:
                self.parts.append(b"r")
                self.number(self.refs[identity])
                return
            self.refs[identity] = len(self.objects)
            self.objects.append(value)
            flag = 128
        kind = type(value)
        if kind is int:
            tag = "i" if -2147483648 <= value <= 2147483647 else "l"
        elif kind is float:
            tag = "g" if self.version >= 2 else "f"
        elif kind is complex:
            tag = "y" if self.version >= 2 else "x"
        elif kind is str:
            value = value.encode("utf-8", "surrogatepass")
            tag = "u"
            if self.version >= 4 and all(byte < 128 for byte in value):
                tag = "z" if len(value) < 256 else "a"
        elif kind is tuple:
            tag = ")" if self.version >= 4 and len(value) < 256 else "("
        elif kind is list:
            tag = "["
        elif kind is dict:
            tag = "{"
        elif kind is set:
            tag = "<"
        elif kind is frozenset:
            tag = ">"
        else:
            try:
                value = bytes(memoryview(value))
            except TypeError:
                raise ValueError(
                    "unmarshallable object (CPython code objects are unsupported)"
                ) from None
            tag = "s"
        self.parts.append(bytes([ord(tag) | flag]))
        if tag == "i":
            self.number(value)
        elif tag == "l":
            digits = []
            magnitude = abs(value)
            while magnitude:
                digits.append(magnitude & 32767)
                magnitude >>= 15
            self.number(-len(digits) if value < 0 else len(digits))
            for digit in digits:
                self.parts.append(_struct.pack("<H", digit))
        elif tag == "g":
            self.parts.append(_struct.pack("<d", value))
        elif tag == "f":
            self.text_float(value)
        elif tag == "y":
            self.parts.append(_struct.pack("<dd", value.real, value.imag))
        elif tag == "x":
            self.text_float(value.real)
            self.text_float(value.imag)
        elif tag in ("s", "u", "a", "z"):
            if tag == "z":
                self.parts.append(bytes([len(value)]))
            else:
                self.number(len(value))
            self.parts.append(value)
        elif tag == "{":
            for key, child in value.items():
                yield key
                yield child
            self.parts.append(b"0")
        else:
            if tag == ")":
                self.parts.append(bytes([len(value)]))
            else:
                self.number(len(value))
            for child in value:
                yield child


def dumps(value, version=version, /):
    """Serialize a supported data value to CPython marshal bytes."""
    if not isinstance(version, int):
        try:
            version = version.__index__()
        except AttributeError:
            raise TypeError("version must be an integer") from None
        if not isinstance(version, int):
            raise TypeError("__index__ returned non-int")
    if version < -2147483648 or version > 2147483647:
        raise OverflowError("Python int too large to convert to C int")
    writer = _Writer(version)
    writer.item(value)
    return b"".join(writer.parts)


def dump(value, file, version=version, /):
    """Write a supported data value to a binary file."""
    file.write(dumps(value, version))


def loads(data, /):
    """Decode one data value from a buffer, ignoring trailing bytes."""
    view = memoryview(data)
    if not view.c_contiguous:
        raise BufferError("memoryview: underlying buffer is not C-contiguous")
    return _Reader(bytes(view), None).item()


def load(file, /):
    """Read one data value from a binary file, leaving trailing data unread."""
    if not isinstance(file.read(0), bytes):
        raise TypeError("file.read() returned not bytes")
    return _Reader(None, file).item()
