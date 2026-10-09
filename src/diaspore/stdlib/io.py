"""Provide in-memory text and byte streams in the Diaspore subset."""


class StringIO:
    def __init__(self, initial=""):
        self._parts = [initial] if initial else []
        self._closed = False

    def write(self, s):
        if self._closed:
            raise ValueError("I/O operation on closed file")
        self._parts.append(s)
        return len(s)

    def getvalue(self):
        return "".join(self._parts)

    def read(self, size=-1):
        val = self.getvalue()
        self._parts = [val]
        return val

    def close(self):
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class BytesIO:
    def __init__(self, initial=b""):
        self._data = bytes(initial)

    def write(self, b):
        self._data += bytes(b)
        return len(b)

    def getvalue(self):
        return self._data

    def read(self, size=-1):
        return self._data

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
