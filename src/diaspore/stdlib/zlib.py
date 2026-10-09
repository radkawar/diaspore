"""Provide one-shot DEFLATE compression and decompression for bytes.

Stored, fixed Huffman, and dynamic Huffman blocks are decoded. Compression
emits stored blocks at every valid level; compressed sizes differ from CPython.
Raw DEFLATE, zlib, and gzip wrappers are supported through wbits. Checksums are
verified. Preset dictionaries and streaming objects are explicitly unsupported.
"""


MAX_WBITS = 15
DEFLATED = 8
DEF_BUF_SIZE = 16384
DEF_MEM_LEVEL = 8
Z_DEFAULT_COMPRESSION = -1
Z_NO_COMPRESSION = 0
Z_BEST_SPEED = 1
Z_BEST_COMPRESSION = 9
Z_DEFAULT_STRATEGY = 0
Z_FILTERED = 1
Z_HUFFMAN_ONLY = 2
Z_RLE = 3
Z_FIXED = 4
Z_NO_FLUSH = 0
Z_SYNC_FLUSH = 2
Z_FULL_FLUSH = 3
Z_FINISH = 4


class error(Exception):
    """Report invalid compressed streams or compression parameters."""


def _require_bytes(data):
    if not isinstance(data, bytes):
        raise TypeError("a bytes object is required")


def _require_int(value):
    if not isinstance(value, int):
        raise TypeError("an integer is required")


def _level(level):
    _require_int(level)
    if level < -1 or level > 9:
        raise error("Bad compression level")


def _crc_table():
    table = []
    for value in range(256):
        for _ in range(8):
            if value & 1:
                value = (value >> 1) ^ 3988292384
            else:
                value >>= 1
        table.append(value)
    return table


_CRC_TABLE = _crc_table()


def _crc(data, value=0):
    value = (value ^ 4294967295) & 4294967295
    for byte in data:
        value = _CRC_TABLE[(value ^ byte) & 255] ^ (value >> 8)
    return value ^ 4294967295


def crc32(data, value=0):
    """Return the unsigned CRC-32, optionally continuing a previous checksum."""
    _require_bytes(data)
    _require_int(value)
    return _crc(data, value)


def _adler(data, value=1):
    low = (value & 65535) % 65521
    high = ((value >> 16) & 65535) % 65521
    pos = 0
    while pos < len(data):
        end = min(pos + 5552, len(data))
        while pos < end:
            low += data[pos]
            high += low
            pos += 1
        low %= 65521
        high %= 65521
    return (high << 16) | low


def adler32(data, value=1):
    """Return Adler-32, optionally continuing a previous checksum."""
    _require_bytes(data)
    _require_int(value)
    return _adler(data, value)


def _put32(out, value):
    for shift in (0, 8, 16, 24):
        out.append((value >> shift) & 255)


def _get32(data, pos):
    return data[pos] | (data[pos + 1] << 8) | (data[pos + 2] << 16) | (data[pos + 3] << 24)


def _stored(data, out):
    pos = 0
    while True:
        size = min(65535, len(data) - pos)
        final = pos + size == len(data)
        out.append(1 if final else 0)
        out.extend([size & 255, size >> 8, (size ^ 65535) & 255, (size ^ 65535) >> 8])
        out.extend(data[pos:pos + size])
        pos += size
        if final:
            return


def _gzip_compress(data, level, mtime):
    out = [31, 139, 8, 0]
    _put32(out, mtime)
    out.extend([2 if level == 9 else (4 if level == 1 else 0), 255])
    _stored(data, out)
    _put32(out, _crc(data))
    _put32(out, len(data) & 4294967295)
    return bytes(out)


def compress(data, level=-1, wbits=MAX_WBITS):
    """Compress bytes into stored blocks with the wrapper selected by wbits."""
    _require_bytes(data)
    _level(level)
    _require_int(wbits)
    if 25 <= wbits <= 31:
        return _gzip_compress(data, level, 0)
    out = []
    if 8 <= wbits <= 15:
        window = max(9, wbits)
        cmf = ((window - 8) << 4) | 8
        out.extend([cmf, (-cmf * 256) % 31])
    elif not -15 <= wbits <= -9:
        raise error("Bad compression window size")
    _stored(data, out)
    if wbits > 0:
        value = _adler(data)
        out.extend([(value >> 24) & 255, (value >> 16) & 255, (value >> 8) & 255, value & 255])
    return bytes(out)


class _Bits:
    def __init__(self, data, pos):
        self.data = data
        self.pos = pos
        self.value = 0
        self.count = 0

    def read(self, count):
        while self.count < count:
            if self.pos >= len(self.data):
                raise error("incomplete or truncated stream")
            self.value |= self.data[self.pos] << self.count
            self.pos += 1
            self.count += 8
        result = self.value & ((1 << count) - 1)
        self.value >>= count
        self.count -= count
        return result

    def align(self):
        self.value = 0
        self.count = 0


def _huffman(lengths, codes=False):
    counts = [0] * 16
    for length in lengths:
        counts[length] += 1
    maximum = 15
    while maximum > 0 and counts[maximum] == 0:
        maximum -= 1
    if maximum == 0:
        if codes:
            raise error("invalid code lengths set")
        return {}, 0
    left = 1
    for length in range(1, 16):
        left = (left << 1) - counts[length]
        if left < 0:
            raise error("oversubscribed Huffman tree")
    if left and (codes or maximum != 1):
        raise error("incomplete Huffman tree")
    next_code = [0] * 16
    code = 0
    for length in range(1, 16):
        code = (code + (counts[length - 1] if length > 1 else 0)) << 1
        next_code[length] = code
    table = {}
    for symbol in range(len(lengths)):
        length = lengths[symbol]
        if length:
            table[(1 << length) | next_code[length]] = symbol
            next_code[length] += 1
    return table, maximum


def _symbol(bits, tree):
    table, maximum = tree
    code = 1
    for _ in range(maximum):
        code = (code << 1) | bits.read(1)
        symbol = table.get(code, -1)
        if symbol >= 0:
            return symbol
    raise error("invalid Huffman code")


_FIXED_LITERAL = _huffman([8] * 144 + [9] * 112 + [7] * 24 + [8] * 8)
_FIXED_DISTANCE = _huffman([5] * 32)
_CODE_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]
_LENGTH_BASE = [
    3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35,
    43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258,
]
_LENGTH_EXTRA = [
    0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3,
    3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0,
]
_DISTANCE_BASE = [
    1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193,
    257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577,
]
_DISTANCE_EXTRA = [
    0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
    7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13,
]


def _dynamic(bits):
    literal_count = bits.read(5) + 257
    distance_count = bits.read(5) + 1
    code_count = bits.read(4) + 4
    if literal_count > 286 or distance_count > 30:
        raise error("too many length or distance symbols")
    code_lengths = [0] * 19
    for index in range(code_count):
        code_lengths[_CODE_ORDER[index]] = bits.read(3)
    code_tree = _huffman(code_lengths, True)
    lengths = []
    total = literal_count + distance_count
    while len(lengths) < total:
        symbol = _symbol(bits, code_tree)
        if symbol < 16:
            lengths.append(symbol)
        else:
            if symbol == 16:
                if not lengths:
                    raise error("invalid bit length repeat")
                count = bits.read(2) + 3
                value = lengths[-1]
            elif symbol == 17:
                count = bits.read(3) + 3
                value = 0
            else:
                count = bits.read(7) + 11
                value = 0
            if len(lengths) + count > total:
                raise error("invalid bit length repeat")
            lengths.extend([value] * count)
    if lengths[256] == 0:
        raise error("missing end-of-block code")
    return _huffman(lengths[:literal_count]), _huffman(lengths[literal_count:])


def _inflate(data, pos):
    bits = _Bits(data, pos)
    out = []
    final = 0
    while not final:
        final = bits.read(1)
        kind = bits.read(2)
        if kind == 0:
            bits.align()
            size = bits.read(16)
            inverse = bits.read(16)
            if size ^ inverse != 65535:
                raise error("invalid stored block lengths")
            end = bits.pos + size
            if end > len(data):
                raise error("incomplete or truncated stream")
            out.extend(data[bits.pos:end])
            bits.pos = end
            continue
        if kind == 1:
            literal_tree, distance_tree = _FIXED_LITERAL, _FIXED_DISTANCE
        elif kind == 2:
            literal_tree, distance_tree = _dynamic(bits)
        else:
            raise error("invalid block type")
        while True:
            symbol = _symbol(bits, literal_tree)
            if symbol < 256:
                out.append(symbol)
            elif symbol == 256:
                break
            else:
                if symbol > 285:
                    raise error("invalid literal/length code")
                index = symbol - 257
                length = _LENGTH_BASE[index] + bits.read(_LENGTH_EXTRA[index])
                distance_symbol = _symbol(bits, distance_tree)
                if distance_symbol >= 30:
                    raise error("invalid distance code")
                distance = _DISTANCE_BASE[distance_symbol] + bits.read(
                    _DISTANCE_EXTRA[distance_symbol]
                )
                if distance > len(out):
                    raise error("invalid distance too far back")
                for _ in range(length):
                    out.append(out[-distance])
    return out, bits.pos


def decompress(data, wbits=MAX_WBITS, bufsize=DEF_BUF_SIZE):
    """Decode a complete stream, ignoring trailing data as CPython does.

    Args:
        data: Compressed bytes.
        wbits: 8..15 or 0 for zlib, -8..-15 for raw DEFLATE, 24..31
            or 16 for gzip, or 40..47 or 32 for automatic zlib/gzip detection.
        bufsize: Nonnegative initial-size hint; output grows as needed.
    """
    _require_bytes(data)
    _require_int(wbits)
    _require_int(bufsize)
    if bufsize < 0:
        raise ValueError("bufsize must be non-negative")
    if -15 <= wbits <= -8:
        out, _ = _inflate(data, 0)
        return bytes(out)
    auto = wbits == 32 or 40 <= wbits <= 47
    if wbits == 16 or 24 <= wbits <= 31 or (auto and data[:2] == b"\x1f\x8b"):
        import gzip
        try:
            out, _ = gzip._member(data, 0)
        except (gzip.BadGzipFile, EOFError) as exc:
            raise error(str(exc)) from None
        return bytes(out)
    if auto:
        wbits -= 32
    if wbits != 0 and not 8 <= wbits <= 15:
        raise error("inconsistent stream state")
    if len(data) < 2:
        raise error("incomplete or truncated stream")
    cmf, flags = data[0], data[1]
    if cmf & 15 != 8 or cmf >> 4 > 7 or ((cmf << 8) | flags) % 31:
        raise error("incorrect header check")
    if wbits and (cmf >> 4) + 8 > wbits:
        raise error("invalid window size")
    if flags & 32:
        raise error("preset dictionaries are not supported")
    out, pos = _inflate(data, 2)
    if pos + 4 > len(data):
        raise error("incomplete or truncated stream")
    expected = (data[pos] << 24) | (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3]
    if _adler(out) != expected:
        raise error("incorrect data check")
    return bytes(out)


def compressobj(*args, **kwargs):
    """Reject unsupported streaming compression and preset dictionaries."""
    raise NotImplementedError("zlib streaming compression is not supported")


def decompressobj(*args, **kwargs):
    """Reject unsupported streaming decompression and preset dictionaries."""
    raise NotImplementedError("zlib streaming decompression is not supported")
