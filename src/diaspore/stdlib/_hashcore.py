"""Incremental MD5, SHA-1 and SHA-2 with optional private native compression.

The portable implementation remains available when the runtime does not expose
_diaspore_hash. Both paths share public metadata and buffer validation; native
engines hold only incremental state and never replace public hashlib modules.
"""

try:
    from _diaspore_hash import new as _native_hash
except ImportError:
    _native_hash = None

_HASH_SIZES = {
    "md5": (64, 16),
    "sha1": (64, 20),
    "sha224": (64, 28),
    "sha256": (64, 32),
    "sha384": (128, 48),
    "sha512": (128, 64),
}

_MASK = 4294967295
_MASK64 = 0xFFFFFFFFFFFFFFFF
_SHA512_K = (
    0x428A2F98D728AE22,
    0x7137449123EF65CD,
    0xB5C0FBCFEC4D3B2F,
    0xE9B5DBA58189DBBC,
    0x3956C25BF348B538,
    0x59F111F1B605D019,
    0x923F82A4AF194F9B,
    0xAB1C5ED5DA6D8118,
    0xD807AA98A3030242,
    0x12835B0145706FBE,
    0x243185BE4EE4B28C,
    0x550C7DC3D5FFB4E2,
    0x72BE5D74F27B896F,
    0x80DEB1FE3B1696B1,
    0x9BDC06A725C71235,
    0xC19BF174CF692694,
    0xE49B69C19EF14AD2,
    0xEFBE4786384F25E3,
    0x0FC19DC68B8CD5B5,
    0x240CA1CC77AC9C65,
    0x2DE92C6F592B0275,
    0x4A7484AA6EA6E483,
    0x5CB0A9DCBD41FBD4,
    0x76F988DA831153B5,
    0x983E5152EE66DFAB,
    0xA831C66D2DB43210,
    0xB00327C898FB213F,
    0xBF597FC7BEEF0EE4,
    0xC6E00BF33DA88FC2,
    0xD5A79147930AA725,
    0x06CA6351E003826F,
    0x142929670A0E6E70,
    0x27B70A8546D22FFC,
    0x2E1B21385C26C926,
    0x4D2C6DFC5AC42AED,
    0x53380D139D95B3DF,
    0x650A73548BAF63DE,
    0x766A0ABB3C77B2A8,
    0x81C2C92E47EDAEE6,
    0x92722C851482353B,
    0xA2BFE8A14CF10364,
    0xA81A664BBC423001,
    0xC24B8B70D0F89791,
    0xC76C51A30654BE30,
    0xD192E819D6EF5218,
    0xD69906245565A910,
    0xF40E35855771202A,
    0x106AA07032BBD1B8,
    0x19A4C116B8D2D0C8,
    0x1E376C085141AB53,
    0x2748774CDF8EEB99,
    0x34B0BCB5E19B48A8,
    0x391C0CB3C5C95A63,
    0x4ED8AA4AE3418ACB,
    0x5B9CCA4F7763E373,
    0x682E6FF3D6B2B8A3,
    0x748F82EE5DEFB2FC,
    0x78A5636F43172F60,
    0x84C87814A1F0AB72,
    0x8CC702081A6439EC,
    0x90BEFFFA23631E28,
    0xA4506CEBDE82BDE9,
    0xBEF9A3F7B2C67915,
    0xC67178F2E372532B,
    0xCA273ECEEA26619C,
    0xD186B8C721C0C207,
    0xEADA7DD6CDE0EB1E,
    0xF57D4F7FEE6ED178,
    0x06F067AA72176FBA,
    0x0A637DC5A2C898A6,
    0x113F9804BEF90DAE,
    0x1B710B35131C471B,
    0x28DB77F523047D84,
    0x32CAAB7B40C72493,
    0x3C9EBE0A15C9BEBC,
    0x431D67C49C100D4C,
    0x4CC5D4BECB3E42B6,
    0x597F299CFC657E2A,
    0x5FCB6FAB3AD6FAEC,
    0x6C44198C4A475817,
)
_SHA256_K = (
    0x428A2F98,
    0x71374491,
    0xB5C0FBCF,
    0xE9B5DBA5,
    0x3956C25B,
    0x59F111F1,
    0x923F82A4,
    0xAB1C5ED5,
    0xD807AA98,
    0x12835B01,
    0x243185BE,
    0x550C7DC3,
    0x72BE5D74,
    0x80DEB1FE,
    0x9BDC06A7,
    0xC19BF174,
    0xE49B69C1,
    0xEFBE4786,
    0x0FC19DC6,
    0x240CA1CC,
    0x2DE92C6F,
    0x4A7484AA,
    0x5CB0A9DC,
    0x76F988DA,
    0x983E5152,
    0xA831C66D,
    0xB00327C8,
    0xBF597FC7,
    0xC6E00BF3,
    0xD5A79147,
    0x06CA6351,
    0x14292967,
    0x27B70A85,
    0x2E1B2138,
    0x4D2C6DFC,
    0x53380D13,
    0x650A7354,
    0x766A0ABB,
    0x81C2C92E,
    0x92722C85,
    0xA2BFE8A1,
    0xA81A664B,
    0xC24B8B70,
    0xC76C51A3,
    0xD192E819,
    0xD6990624,
    0xF40E3585,
    0x106AA070,
    0x19A4C116,
    0x1E376C08,
    0x2748774C,
    0x34B0BCB5,
    0x391C0CB3,
    0x4ED8AA4A,
    0x5B9CCA4F,
    0x682E6FF3,
    0x748F82EE,
    0x78A5636F,
    0x84C87814,
    0x8CC70208,
    0x90BEFFFA,
    0xA4506CEB,
    0xBEF9A3F7,
    0xC67178F2,
)
_MD5_K = (
    0xD76AA478,
    0xE8C7B756,
    0x242070DB,
    0xC1BDCEEE,
    0xF57C0FAF,
    0x4787C62A,
    0xA8304613,
    0xFD469501,
    0x698098D8,
    0x8B44F7AF,
    0xFFFF5BB1,
    0x895CD7BE,
    0x6B901122,
    0xFD987193,
    0xA679438E,
    0x49B40821,
    0xF61E2562,
    0xC040B340,
    0x265E5A51,
    0xE9B6C7AA,
    0xD62F105D,
    0x02441453,
    0xD8A1E681,
    0xE7D3FBC8,
    0x21E1CDE6,
    0xC33707D6,
    0xF4D50D87,
    0x455A14ED,
    0xA9E3E905,
    0xFCEFA3F8,
    0x676F02D9,
    0x8D2A4C8A,
    0xFFFA3942,
    0x8771F681,
    0x6D9D6122,
    0xFDE5380C,
    0xA4BEEA44,
    0x4BDECFA9,
    0xF6BB4B60,
    0xBEBFBC70,
    0x289B7EC6,
    0xEAA127FA,
    0xD4EF3085,
    0x04881D05,
    0xD9D4D039,
    0xE6DB99E5,
    0x1FA27CF8,
    0xC4AC5665,
    0xF4292244,
    0x432AFF97,
    0xAB9423A7,
    0xFC93A039,
    0x655B59C3,
    0x8F0CCC92,
    0xFFEFF47D,
    0x85845DD1,
    0x6FA87E4F,
    0xFE2CE6E0,
    0xA3014314,
    0x4E0811A1,
    0xF7537E82,
    0xBD3AF235,
    0x2AD7D2BB,
    0xEB86D391,
)
_MD5_SHIFT = (7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21)


def _rol(value, count):
    return ((value << count) | (value >> (32 - count))) & _MASK


def _ror(value, count):
    return ((value >> count) | (value << (32 - count))) & _MASK


def _ror64(value, count):
    return ((value >> count) | (value << (64 - count))) & _MASK64


def _buffer_bytes(data):
    if type(data) is bytes:
        return data
    if isinstance(data, str):
        raise TypeError("Strings must be encoded before hashing")
    if not isinstance(data, (bytes, bytearray, memoryview)) and not hasattr(
        type(data), "__buffer__"
    ):
        raise TypeError("object supporting the buffer API required")
    view = memoryview(data)
    if not view.c_contiguous:
        raise BufferError("memoryview: underlying buffer is not C-contiguous")
    return view.tobytes()


def _word_bytes(value, size, little=False):
    shifts = range(size) if little else range(size - 1, -1, -1)
    return bytes([(value >> (8 * shift)) & 255 for shift in shifts])


class _Hash:
    def __init__(self, name, data=b""):
        self._name = name
        self._engine = None
        if _native_hash is not None:
            if name not in _HASH_SIZES:
                raise ValueError("unsupported hash type " + name)
            self._block_size, self._digest_size = _HASH_SIZES[name]
            self._engine = _native_hash(name)
            self.update(data)
            return
        self._length = 0
        self._buffer = b""
        self._block_size = 64
        if name == "sha256":
            self._digest_size = 32
            self._state = [
                0x6A09E667,
                0xBB67AE85,
                0x3C6EF372,
                0xA54FF53A,
                0x510E527F,
                0x9B05688C,
                0x1F83D9AB,
                0x5BE0CD19,
            ]
        elif name == "sha224":
            self._digest_size = 28
            self._state = [
                0xC1059ED8,
                0x367CD507,
                0x3070DD17,
                0xF70E5939,
                0xFFC00B31,
                0x68581511,
                0x64F98FA7,
                0xBEFA4FA4,
            ]
        elif name == "sha384":
            self._block_size = 128
            self._digest_size = 48
            self._state = [
                0xCBBB9D5DC1059ED8,
                0x629A292A367CD507,
                0x9159015A3070DD17,
                0x152FECD8F70E5939,
                0x67332667FFC00B31,
                0x8EB44A8768581511,
                0xDB0C2E0D64F98FA7,
                0x47B5481DBEFA4FA4,
            ]
        elif name == "sha512":
            self._block_size = 128
            self._digest_size = 64
            self._state = [
                0x6A09E667F3BCC908,
                0xBB67AE8584CAA73B,
                0x3C6EF372FE94F82B,
                0xA54FF53A5F1D36F1,
                0x510E527FADE682D1,
                0x9B05688C2B3E6C1F,
                0x1F83D9ABFB41BD6B,
                0x5BE0CD19137E2179,
            ]
        elif name == "md5" or name == "sha1":
            self._digest_size = 16 if name == "md5" else 20
            self._state = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]
            if name == "sha1":
                self._state.append(0xC3D2E1F0)
        else:
            raise ValueError("unsupported hash type " + name)
        self.update(data)

    @property
    def name(self):
        return self._name

    @property
    def digest_size(self):
        return self._digest_size

    @property
    def block_size(self):
        return self._block_size

    def update(self, data, /):
        data = _buffer_bytes(data)
        if self._engine is not None:
            self._engine.update(data)
            return None
        self._length += len(data)
        start = 0
        if self._buffer:
            needed = self._block_size - len(self._buffer)
            self._buffer += data[:needed]
            if len(self._buffer) < self._block_size:
                return None
            self._compress(self._buffer)
            self._buffer = b""
            start = needed
        while start + self._block_size <= len(data):
            self._compress(data[start : start + self._block_size])
            start += self._block_size
        self._buffer = data[start:]
        return None

    def copy(self):
        result = object.__new__(_Hash)
        result._name = self._name
        result._block_size = self._block_size
        result._digest_size = self._digest_size
        if self._engine is not None:
            result._engine = self._engine.copy()
            return result
        result._engine = None
        result._length = self._length
        result._buffer = self._buffer
        result._state = list(self._state)
        return result

    def digest(self):
        if self._engine is not None:
            return self._engine.digest()
        result = self.copy()
        size = self._block_size
        length_size = size // 8
        padding = b"\x80" + bytes((size - length_size - 1 - self._length) % size)
        result.update(padding + _word_bytes(self._length * 8, length_size, self.name == "md5"))
        output = []
        for word in result._state:
            output.extend(_word_bytes(word, size // 16, self.name == "md5"))
        return bytes(output[: self._digest_size])

    def hexdigest(self):
        if self._engine is not None:
            return self._engine.hexdigest()
        return "".join([f"{value:02x}" for value in self.digest()])

    def _compress(self, block):
        if self._block_size == 128:
            words = []
            for offset in range(0, 128, 8):
                value = 0
                for index in range(offset, offset + 8):
                    value = (value << 8) | block[index]
                words.append(value)
            self._sha512(words)
            return
        words = []
        for offset in range(0, 64, 4):
            a, b, c, d = block[offset : offset + 4]
            if self.name == "md5":
                words.append(a | (b << 8) | (c << 16) | (d << 24))
            else:
                words.append((a << 24) | (b << 16) | (c << 8) | d)
        if self.name == "sha256" or self.name == "sha224":
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

    def _sha512(self, words):
        for index in range(16, 80):
            x, y = words[index - 15], words[index - 2]
            s0 = _ror64(x, 1) ^ _ror64(x, 8) ^ (x >> 7)
            s1 = _ror64(y, 19) ^ _ror64(y, 61) ^ (y >> 6)
            words.append((words[index - 16] + s0 + words[index - 7] + s1) & _MASK64)
        a, b, c, d, e, f, g, h = self._state
        for index in range(80):
            s1 = _ror64(e, 14) ^ _ror64(e, 18) ^ _ror64(e, 41)
            choice = (e & f) ^ ((~e) & g)
            t1 = (h + s1 + choice + _SHA512_K[index] + words[index]) & _MASK64
            s0 = _ror64(a, 28) ^ _ror64(a, 34) ^ _ror64(a, 39)
            majority = (a & b) ^ (a & c) ^ (b & c)
            t2 = (s0 + majority) & _MASK64
            h, g, f, e, d, c, b, a = g, f, e, (d + t1) & _MASK64, c, b, a, (t1 + t2) & _MASK64
        self._state = [
            (old + value) & _MASK64 for old, value in zip(self._state, (a, b, c, d, e, f, g, h))
        ]

    def _sha1(self, words):
        for index in range(16, 80):
            value = words[index - 3] ^ words[index - 8] ^ words[index - 14] ^ words[index - 16]
            words.append(_rol(value, 1))
        a, b, c, d, e = self._state
        for index in range(80):
            if index < 20:
                f, k = (b & c) | ((~b) & d), 0x5A827999
            elif index < 40:
                f, k = b ^ c ^ d, 0x6ED9EBA1
            elif index < 60:
                f, k = (b & c) | (b & d) | (c & d), 0x8F1BBCDC
            else:
                f, k = b ^ c ^ d, 0xCA62C1D6
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
