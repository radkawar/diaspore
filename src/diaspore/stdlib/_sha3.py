"""Incremental SHA-3 and SHAKE using the Keccak-f[1600] permutation."""

from _hashcore import _buffer_bytes

_MASK = 0xFFFFFFFFFFFFFFFF
_ROUNDS = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)
_ROTATIONS = (
    0,
    1,
    62,
    28,
    27,
    36,
    44,
    6,
    55,
    20,
    3,
    10,
    43,
    25,
    39,
    41,
    45,
    15,
    21,
    8,
    18,
    2,
    61,
    56,
    14,
)


def _rotate(value, count):
    return ((value << count) | (value >> (64 - count))) & _MASK


def _permute(state):
    # Lanes are indexed x + 5*y; scratch space is reused across all 24 rounds.
    columns = [0] * 5
    lanes = [0] * 25
    for constant in _ROUNDS:
        for x in range(5):
            columns[x] = state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
        for x in range(5):
            delta = columns[(x + 4) % 5] ^ _rotate(columns[(x + 1) % 5], 1)
            for y in range(5):
                source = x + 5 * y
                target = y + 5 * ((2 * x + 3 * y) % 5)
                lanes[target] = _rotate(state[source] ^ delta, _ROTATIONS[source])
        for y in range(0, 25, 5):
            for x in range(5):
                state[y + x] = lanes[y + x] ^ ((~lanes[y + (x + 1) % 5]) & lanes[y + (x + 2) % 5])
        state[0] ^= constant


def _absorb(state, data, offset, size):
    for lane in range(size // 8):
        word = 0
        start = offset + lane * 8
        for index in range(8):
            word |= data[start + index] << (8 * index)
        state[lane] ^= word
    _permute(state)


def _arguments(name, method, args, kwargs, count):
    label = name + "." + method + "()"
    if kwargs:
        raise TypeError(label + " takes no keyword arguments")
    if len(args) != count:
        expected = "no arguments" if count == 0 else "exactly one argument"
        raise TypeError(label + " takes " + expected + " (" + str(len(args)) + " given)")


def _shake_length(length):
    # The CPython unsigned-long converter rejects __index__-only objects.
    if not isinstance(length, int):
        raise TypeError("an integer is required")
    length = int.__index__(length)
    if length < 0:
        raise ValueError("value must be positive")
    if length > _MASK:
        raise OverflowError("Python int too large to convert to C unsigned long")
    if length >= 1 << 29:
        raise ValueError("length is too large")
    return length


class _Sponge:
    def __new__(cls, *args, **kwargs):
        # All six native constructors share the sha3_224 argument parser.
        total = len(args) + len(kwargs)
        if total > 2:
            kind = "keyword " if not args else ""
            raise TypeError(
                "sha3_224() takes at most 2 " + kind + "arguments (" + str(total) + " given)"
            )
        if len(args) > 1:
            raise TypeError(
                "sha3_224() takes at most 1 positional argument (" + str(len(args)) + " given)"
            )
        for key in kwargs:
            if key != "usedforsecurity":
                raise TypeError("'" + key + "' is an invalid keyword argument for sha3_224()")
        if "usedforsecurity" in kwargs:
            bool(kwargs["usedforsecurity"])
        data = _buffer_bytes(args[0]) if args else b""
        result = object.__new__(cls)
        result._state = [0] * 25
        result._buffer = b""
        result._update(data)
        return result

    @property
    def name(self):
        return self._parameters[0]

    @property
    def block_size(self):
        return self._parameters[1]

    @property
    def digest_size(self):
        return self._parameters[2]

    @property
    def _rate_bits(self):
        return self.block_size * 8

    @property
    def _capacity_bits(self):
        return 1600 - self._rate_bits

    @property
    def _suffix(self):
        return b"\x1f" if self.digest_size == 0 else b"\x06"

    def update(self, *args, **kwargs):
        """Absorb a contiguous bytes-like object without finalizing the state."""
        _arguments(self.name, "update", args, kwargs, 1)
        self._update(_buffer_bytes(args[0]))

    def _update(self, data):
        size = self.block_size
        start = 0
        if self._buffer:
            needed = size - len(self._buffer)
            self._buffer += data[:needed]
            if len(self._buffer) < size:
                return
            _absorb(self._state, self._buffer, 0, size)
            self._buffer = b""
            start = needed
        while start + size <= len(data):
            _absorb(self._state, data, start, size)
            start += size
        self._buffer = data[start:]

    def copy(self, *args, **kwargs):
        """Return an independent copy of the incremental hash state."""
        _arguments(self.name, "copy", args, kwargs, 0)
        result = object.__new__(type(self))
        result._state = list(self._state)
        result._buffer = self._buffer
        return result

    def _digest(self, length):
        if length == 0:
            return b""
        state = list(self._state)
        size = self.block_size
        # Domain separation and the final padding bit may share one byte.
        padding = list(self._buffer) + [0] * (size - len(self._buffer))
        padding[len(self._buffer)] = self._suffix[0]
        padding[-1] |= 128
        _absorb(state, padding, 0, size)
        output = bytearray(length)
        offset = 0
        while offset < length:
            count = min(size, length - offset)
            for index in range(count):
                output[offset + index] = (state[index // 8] >> (8 * (index % 8))) & 255
            offset += count
            if offset < length:
                _permute(state)
        return bytes(output)

    def digest(self, *args, **kwargs):
        """Return the digest without consuming or changing this hash state."""
        _arguments(self.name, "digest", args, kwargs, 0)
        return self._digest(self.digest_size)

    def hexdigest(self, *args, **kwargs):
        """Return the digest as lowercase hexadecimal digits."""
        _arguments(self.name, "hexdigest", args, kwargs, 0)
        return self._digest(self.digest_size).hex()


class sha3_224(_Sponge):
    """SHA3-224 with a 144-byte absorption rate."""

    _parameters = ("sha3_224", 144, 28)


class sha3_256(_Sponge):
    """SHA3-256 with a 136-byte absorption rate."""

    _parameters = ("sha3_256", 136, 32)


class sha3_384(_Sponge):
    """SHA3-384 with a 104-byte absorption rate."""

    _parameters = ("sha3_384", 104, 48)


class sha3_512(_Sponge):
    """SHA3-512 with a 72-byte absorption rate."""

    _parameters = ("sha3_512", 72, 64)


class _Shake(_Sponge):
    def digest(self, *args, **kwargs):
        """Return length bytes of extensible output, starting at its beginning."""
        _arguments(self.name, "digest", args, kwargs, 1)
        return self._digest(_shake_length(args[0]))

    def hexdigest(self, *args, **kwargs):
        """Return length output bytes encoded as lowercase hexadecimal digits."""
        _arguments(self.name, "hexdigest", args, kwargs, 1)
        return self._digest(_shake_length(args[0])).hex()


class shake_128(_Shake):
    """SHAKE128 with a 168-byte absorption and squeezing rate."""

    _parameters = ("shake_128", 168, 0)


class shake_256(_Shake):
    """SHAKE256 with a 136-byte absorption and squeezing rate."""

    _parameters = ("shake_256", 136, 0)
