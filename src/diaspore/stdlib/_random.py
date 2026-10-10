"""CPython-compatible MT19937 state and integer-seeded generation.

Default seeds use the sandbox's journaled wall and monotonic clocks, not
operating-system entropy. This generator is not suitable for cryptography.
"""

from _operator import index as _index
from time import monotonic as _monotonic
from time import time as _time

_MASK = 0xFFFFFFFF


class Random:
    """Mersenne Twister core used by the public random module."""

    def __init__(self, seed=None):
        Random.seed(self, seed)

    def seed(self, n=None, /):
        """Initialize all 624 state words from an integer or hashed seed."""
        if n is None:
            wall = int(_time() * 1000000000)
            mono = int(_monotonic() * 1000000000)
            # Clock inputs are replayable sandbox effects; there is no process ID.
            key = [wall & _MASK, (wall >> 32) & _MASK, 0, mono & _MASK, (mono >> 32) & _MASK]
        else:
            if isinstance(n, int):
                n = int.__abs__(n)
            else:
                n = hash(n) & 0xFFFFFFFFFFFFFFFF
            key = []
            while n:
                key.append(n & _MASK)
                n >>= 32
            if not key:
                key.append(0)
        state = [19650218]
        for i in range(1, 624):
            previous = state[i - 1]
            state.append((1812433253 * (previous ^ (previous >> 30)) + i) & _MASK)
        i = 1
        j = 0
        for _unused in range(max(624, len(key))):
            previous = state[i - 1]
            state[i] = ((state[i] ^ ((previous ^ (previous >> 30)) * 1664525)) + key[j] + j) & _MASK
            i += 1
            j += 1
            if i >= 624:
                state[0] = state[623]
                i = 1
            if j >= len(key):
                j = 0
        for _unused in range(623):
            previous = state[i - 1]
            state[i] = ((state[i] ^ ((previous ^ (previous >> 30)) * 1566083941)) - i) & _MASK
            i += 1
            if i >= 624:
                state[0] = state[623]
                i = 1
        state[0] = 0x80000000
        self._state = state
        self._index = 624

    def _uint32(self):
        state = self._state
        if self._index >= 624:
            for i in range(624):
                y = (state[i] & 0x80000000) | (state[(i + 1) % 624] & 0x7FFFFFFF)
                state[i] = state[(i + 397) % 624] ^ (y >> 1)
                if y & 1:
                    state[i] ^= 0x9908B0DF
            self._index = 0
        y = state[self._index]
        self._index += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        return y

    def random(self):
        """Return a double in [0, 1) with 53 bits of precision."""
        a = self._uint32() >> 5
        b = self._uint32() >> 6
        return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0)

    def getrandbits(self, k, /):
        """Return k random bits with CPython's little-endian word consumption."""
        k = _index(k)
        if k < -2147483648 or k > 2147483647:
            raise OverflowError("Python int too large to convert to C int")
        if k < 0:
            raise ValueError("number of bits must be non-negative")
        if k == 0:
            return 0
        if k <= 32:
            return self._uint32() >> (32 - k)
        value = 0
        shift = 0
        while k > 0:
            word = self._uint32()
            if k < 32:
                word >>= 32 - k
            value |= word << shift
            shift += 32
            k -= 32
        return value

    def getstate(self):
        """Return the 624 words followed by the current index."""
        return tuple(self._state) + (self._index,)

    def setstate(self, state, /):
        """Restore a state after validating it without mutating on failure."""
        if not isinstance(state, tuple):
            raise TypeError("state vector must be a tuple")
        if len(state) != 625:
            raise ValueError("state vector is the wrong size")
        words = []
        for i in range(624):
            if not isinstance(state[i], int):
                raise TypeError("an integer is required")
            word = int.__index__(state[i])
            if word < 0:
                raise OverflowError("can't convert negative value to unsigned int")
            if word > 0xFFFFFFFFFFFFFFFF:
                raise OverflowError("Python int too large to convert to C unsigned long")
            words.append(word & _MASK)
        position = _index(state[624])
        if position < -9223372036854775808 or position > 9223372036854775807:
            raise OverflowError("Python int too large to convert to C long")
        if position < 0 or position > 624:
            raise ValueError("invalid state")
        self._state = words
        self._index = position
