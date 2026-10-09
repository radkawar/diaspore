"""Incremental SHA-256, SHA-1 and MD5 hashes implemented with 32-bit words."""

_MASK = 4294967295
_SHA256_K = (
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
)
_MD5_K = (
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
)
_MD5_SHIFT = (7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21)
algorithms_available = {"sha256", "sha1", "md5"}
algorithms_guaranteed = {"sha256", "sha1", "md5"}


def _rol(value, count):
    return ((value << count) | (value >> (32 - count))) & _MASK


def _ror(value, count):
    return ((value >> count) | (value << (32 - count))) & _MASK


def _word_bytes(value, size, little=False):
    shifts = range(size) if little else range(size - 1, -1, -1)
    return bytes([(value >> (8 * shift)) & 255 for shift in shifts])


class _Hash:
    block_size = 64

    def __init__(self, name, data=b""):
        self.name = name
        self._length = 0
        self._buffer = b""
        if name == "sha256":
            self.digest_size = 32
            self._state = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                           0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
        else:
            self.digest_size = 16 if name == "md5" else 20
            self._state = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476]
            if name == "sha1":
                self._state.append(0xc3d2e1f0)
        self.update(data)

    def update(self, data):
        if isinstance(data, str):
            raise TypeError("Strings must be encoded before hashing")
        if not isinstance(data, bytes):
            raise TypeError("object supporting the buffer API required")
        self._length += len(data)
        start = 0
        if self._buffer:
            needed = 64 - len(self._buffer)
            self._buffer += data[:needed]
            if len(self._buffer) < 64:
                return None
            self._compress(self._buffer)
            self._buffer = b""
            start = needed
        while start + 64 <= len(data):
            self._compress(data[start:start + 64])
            start += 64
        self._buffer = data[start:]
        return None

    def copy(self):
        result = _Hash(self.name)
        result._length = self._length
        result._buffer = self._buffer
        result._state = list(self._state)
        return result

    def digest(self):
        result = self.copy()
        padding = b"\x80" + bytes((55 - self._length) % 64)
        result.update(padding + _word_bytes(self._length * 8, 8, self.name == "md5"))
        output = []
        for word in result._state:
            output.extend(_word_bytes(word, 4, self.name == "md5"))
        return bytes(output)

    def hexdigest(self):
        return "".join([f"{value:02x}" for value in self.digest()])

    def _compress(self, block):
        words = []
        for offset in range(0, 64, 4):
            a, b, c, d = block[offset:offset + 4]
            if self.name == "md5":
                words.append(a | (b << 8) | (c << 16) | (d << 24))
            else:
                words.append((a << 24) | (b << 16) | (c << 8) | d)
        if self.name == "sha256":
            self._sha256(words)
        elif self.name == "sha1":
            self._sha1(words)
        else:
            self._md5(words)

    def _sha256(self, words):
        for index in range(16, 64):
            x, y = words[index - 15], words[index - 2]
            s0 = _ror(x, 7) ^ _ror(x, 18) ^ (x >> 3)
            s1 = _ror(y, 17) ^ _ror(y, 19) ^ (y >> 10)
            words.append((words[index - 16] + s0 + words[index - 7] + s1) & _MASK)
        a, b, c, d, e, f, g, h = self._state
        for index in range(64):
            s1 = _ror(e, 6) ^ _ror(e, 11) ^ _ror(e, 25)
            choice = (e & f) ^ ((~e) & g)
            t1 = (h + s1 + choice + _SHA256_K[index] + words[index]) & _MASK
            s0 = _ror(a, 2) ^ _ror(a, 13) ^ _ror(a, 22)
            majority = (a & b) ^ (a & c) ^ (b & c)
            t2 = (s0 + majority) & _MASK
            h, g, f, e, d, c, b, a = g, f, e, (d + t1) & _MASK, c, b, a, (t1 + t2) & _MASK
        self._state = [
            (old + value) & _MASK for old, value in zip(self._state, (a, b, c, d, e, f, g, h))
        ]

    def _sha1(self, words):
        for index in range(16, 80):
            value = words[index - 3] ^ words[index - 8] ^ words[index - 14] ^ words[index - 16]
            words.append(_rol(value, 1))
        a, b, c, d, e = self._state
        for index in range(80):
            if index < 20:
                f, k = (b & c) | ((~b) & d), 0x5a827999
            elif index < 40:
                f, k = b ^ c ^ d, 0x6ed9eba1
            elif index < 60:
                f, k = (b & c) | (b & d) | (c & d), 0x8f1bbcdc
            else:
                f, k = b ^ c ^ d, 0xca62c1d6
            value = (_rol(a, 5) + f + e + k + words[index]) & _MASK
            e, d, c, b, a = d, c, _rol(b, 30), a, value
        self._state = [(old + value) & _MASK for old, value in zip(self._state, (a, b, c, d, e))]

    def _md5(self, words):
        a, b, c, d = self._state
        for index in range(64):
            if index < 16:
                f, g = (b & c) | ((~b) & d), index
            elif index < 32:
                f, g = (d & b) | ((~d) & c), (5 * index + 1) % 16
            elif index < 48:
                f, g = b ^ c ^ d, (3 * index + 5) % 16
            else:
                f, g = c ^ (b | (~d)), (7 * index) % 16
            value = (a + f + _MD5_K[index] + words[g]) & _MASK
            shift = _MD5_SHIFT[(index // 16) * 4 + index % 4]
            a, d, c, b = d, c, b, (b + _rol(value, shift)) & _MASK
        self._state = [(old + value) & _MASK for old, value in zip(self._state, (a, b, c, d))]


def sha256(data=b"", *, usedforsecurity=True):
    return _Hash("sha256", data)


def sha1(data=b"", *, usedforsecurity=True):
    return _Hash("sha1", data)


def md5(data=b"", *, usedforsecurity=True):
    return _Hash("md5", data)


def new(name, data=b"", *, usedforsecurity=True):
    normalized = name.lower()
    if normalized in ("sha-1", "sha-256"):
        normalized = normalized.replace("-", "")
    if normalized not in algorithms_available:
        raise ValueError("unsupported hash type " + name)
    return _Hash(normalized, data)
