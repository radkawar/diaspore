"""BLAKE2b and BLAKE2s with keyed, personalized and tree hashing."""

from _operator import index as _index

from _hashcore import _buffer_bytes, _word_bytes

_SIGMA = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15),
    (14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3),
    (11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4),
    (7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8),
    (9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13),
    (2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9),
    (12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11),
    (13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10),
    (6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5),
    (10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0),
)
_IV32 = (
    0x6A09E667,
    0xBB67AE85,
    0x3C6EF372,
    0xA54FF53A,
    0x510E527F,
    0x9B05688C,
    0x1F83D9AB,
    0x5BE0CD19,
)
_IV64 = (
    0x6A09E667F3BCC908,
    0xBB67AE8584CAA73B,
    0x3C6EF372FE94F82B,
    0xA54FF53A5F1D36F1,
    0x510E527FADE682D1,
    0x9B05688C2B3E6C1F,
    0x1F83D9ABFB41BD6B,
    0x5BE0CD19137E2179,
)


def _bounded(value, low, high, name):
    value = _index(value)
    if value < -2147483648 or value > 2147483647:
        raise OverflowError("Python int too large to convert to C int")
    if value < low or value > high:
        if name == "digest_size":
            raise ValueError("digest_size must be between 1 and " + str(high) + " bytes")
        if name == "inner_size":
            raise ValueError("inner_size must be between 0 and is " + str(high))
        raise ValueError(name + " must be between " + str(low) + " and " + str(high))
    return value


def _unsigned(value, long_long=False):
    if not isinstance(value, int):
        raise TypeError("an integer is required")
    value = int.__index__(value)
    if value < 0:
        raise ValueError("value must be positive")
    if value > 0xFFFFFFFFFFFFFFFF:
        if long_long:
            raise OverflowError("int too big to convert")
        raise OverflowError("Python int too large to convert to C unsigned long")
    return value


class _Blake2:
    def _initialize(
        self,
        data,
        digest_size,
        key,
        salt,
        person,
        fanout,
        depth,
        leaf_size,
        node_offset,
        node_depth,
        inner_size,
        last_node,
    ):
        digest_size = _bounded(digest_size, 1, self.MAX_DIGEST_SIZE, "digest_size")
        key = _buffer_bytes(key)
        salt = _buffer_bytes(salt)
        person = _buffer_bytes(person)
        if len(key) > self.MAX_KEY_SIZE:
            raise ValueError("maximum key length is " + str(self.MAX_KEY_SIZE) + " bytes")
        if len(salt) > self.SALT_SIZE:
            raise ValueError("maximum salt length is " + str(self.SALT_SIZE) + " bytes")
        if len(person) > self.PERSON_SIZE:
            raise ValueError("maximum person length is " + str(self.PERSON_SIZE) + " bytes")
        fanout = _bounded(fanout, 0, 255, "fanout")
        depth = _bounded(depth, 1, 255, "depth")
        leaf_size = _unsigned(leaf_size)
        node_offset = _unsigned(node_offset, True)
        if leaf_size > 0xFFFFFFFF:
            raise OverflowError("leaf_size is too large")
        if self._bits == 32 and node_offset > 0xFFFFFFFFFFFF:
            raise OverflowError("node_offset is too large")
        node_depth = _bounded(node_depth, 0, 255, "node_depth")
        inner_size = _bounded(inner_size, 0, self.MAX_DIGEST_SIZE, "inner_size")
        self._digest_size = digest_size
        self._last_node = bool(last_node)
        self._count = 0
        self._mask = (1 << self._bits) - 1
        offset_size = 8 if self._bits == 64 else 6
        parameters = bytes([digest_size, len(key), fanout, depth])
        parameters += _word_bytes(leaf_size, 4, True)
        parameters += _word_bytes(node_offset, offset_size, True)
        parameters += bytes([node_depth, inner_size])
        if self._bits == 64:
            parameters += bytes(14)
        parameters += salt + bytes(self.SALT_SIZE - len(salt))
        parameters += person + bytes(self.PERSON_SIZE - len(person))
        self._state = list(self._iv)
        word_size = self._bits // 8
        for i in range(8):
            value = 0
            for j in range(word_size):
                value |= parameters[i * word_size + j] << (8 * j)
            self._state[i] ^= value
        self._buffer = key + bytes(self.block_size - len(key)) if key else b""
        self.update(data)

    @property
    def name(self):
        return "blake2b" if self._bits == 64 else "blake2s"

    @property
    def digest_size(self):
        return self._digest_size

    @property
    def block_size(self):
        return self._bits * 2

    def update(self, data, /):
        """Consume a bytes-like object, retaining the final block."""
        data = _buffer_bytes(data)
        if not data:
            return None
        size = self.block_size
        start = 0
        if self._buffer:
            needed = size - len(self._buffer)
            self._buffer += data[:needed]
            if len(data) <= needed:
                return None
            self._count += size
            self._compress(self._buffer, False)
            self._buffer = b""
            start = needed
        while start + size < len(data):
            self._count += size
            self._compress(data[start : start + size], False)
            start += size
        self._buffer = data[start:]
        return None

    def copy(self):
        """Return an independent copy of the incremental state."""
        result = object.__new__(type(self))
        result._digest_size = self._digest_size
        result._last_node = self._last_node
        result._count = self._count
        result._mask = self._mask
        result._state = list(self._state)
        result._buffer = self._buffer
        return result

    def digest(self):
        """Return the digest without changing the incremental state."""
        result = self.copy()
        result._count += len(result._buffer)
        result._compress(result._buffer + bytes(result.block_size - len(result._buffer)), True)
        output = []
        for word in result._state:
            output.extend(_word_bytes(word, self._bits // 8, True))
        return bytes(output[: self._digest_size])

    def hexdigest(self):
        """Return the hexadecimal digest."""
        return "".join([f"{value:02x}" for value in self.digest()])

    def _mix(self, v, a, b, c, d, x, y):
        r0, r1, r2, r3 = self._rotations
        mask = self._mask
        bits = self._bits
        v[a] = (v[a] + v[b] + x) & mask
        value = v[d] ^ v[a]
        v[d] = ((value >> r0) | (value << (bits - r0))) & mask
        v[c] = (v[c] + v[d]) & mask
        value = v[b] ^ v[c]
        v[b] = ((value >> r1) | (value << (bits - r1))) & mask
        v[a] = (v[a] + v[b] + y) & mask
        value = v[d] ^ v[a]
        v[d] = ((value >> r2) | (value << (bits - r2))) & mask
        v[c] = (v[c] + v[d]) & mask
        value = v[b] ^ v[c]
        v[b] = ((value >> r3) | (value << (bits - r3))) & mask

    def _compress(self, block, final):
        word_size = self._bits // 8
        words = []
        for offset in range(0, self.block_size, word_size):
            value = 0
            for j in range(word_size):
                value |= block[offset + j] << (8 * j)
            words.append(value)
        v = self._state + list(self._iv)
        v[12] ^= self._count & self._mask
        v[13] ^= (self._count >> self._bits) & self._mask
        if final:
            v[14] ^= self._mask
            if self._last_node:
                v[15] ^= self._mask
        for i in range(self._rounds):
            s = _SIGMA[i % 10]
            self._mix(v, 0, 4, 8, 12, words[s[0]], words[s[1]])
            self._mix(v, 1, 5, 9, 13, words[s[2]], words[s[3]])
            self._mix(v, 2, 6, 10, 14, words[s[4]], words[s[5]])
            self._mix(v, 3, 7, 11, 15, words[s[6]], words[s[7]])
            self._mix(v, 0, 5, 10, 15, words[s[8]], words[s[9]])
            self._mix(v, 1, 6, 11, 12, words[s[10]], words[s[11]])
            self._mix(v, 2, 7, 8, 13, words[s[12]], words[s[13]])
            self._mix(v, 3, 4, 9, 14, words[s[14]], words[s[15]])
        for i in range(8):
            self._state[i] ^= v[i] ^ v[i + 8]


class blake2b(_Blake2):
    """BLAKE2b hash with up to 64 output bytes."""

    MAX_DIGEST_SIZE = 64
    MAX_KEY_SIZE = 64
    SALT_SIZE = 16
    PERSON_SIZE = 16
    _bits = 64
    _iv = _IV64
    _rotations = (32, 24, 16, 63)
    _rounds = 12

    def __init__(
        self,
        data=b"",
        /,
        *,
        digest_size=64,
        key=b"",
        salt=b"",
        person=b"",
        fanout=1,
        depth=1,
        leaf_size=0,
        node_offset=0,
        node_depth=0,
        inner_size=0,
        last_node=False,
        usedforsecurity=True,
    ):
        bool(usedforsecurity)
        self._initialize(
            data,
            digest_size,
            key,
            salt,
            person,
            fanout,
            depth,
            leaf_size,
            node_offset,
            node_depth,
            inner_size,
            last_node,
        )


class blake2s(_Blake2):
    """BLAKE2s hash with up to 32 output bytes."""

    MAX_DIGEST_SIZE = 32
    MAX_KEY_SIZE = 32
    SALT_SIZE = 8
    PERSON_SIZE = 8
    _bits = 32
    _iv = _IV32
    _rotations = (16, 12, 8, 7)
    _rounds = 10

    def __init__(
        self,
        data=b"",
        /,
        *,
        digest_size=32,
        key=b"",
        salt=b"",
        person=b"",
        fanout=1,
        depth=1,
        leaf_size=0,
        node_offset=0,
        node_depth=0,
        inner_size=0,
        last_node=False,
        usedforsecurity=True,
    ):
        bool(usedforsecurity)
        self._initialize(
            data,
            digest_size,
            key,
            salt,
            person,
            fanout,
            depth,
            leaf_size,
            node_offset,
            node_depth,
            inner_size,
            last_node,
        )


BLAKE2B_SALT_SIZE = 16
BLAKE2B_PERSON_SIZE = 16
BLAKE2B_MAX_KEY_SIZE = 64
BLAKE2B_MAX_DIGEST_SIZE = 64
BLAKE2S_SALT_SIZE = 8
BLAKE2S_PERSON_SIZE = 8
BLAKE2S_MAX_KEY_SIZE = 32
BLAKE2S_MAX_DIGEST_SIZE = 32
