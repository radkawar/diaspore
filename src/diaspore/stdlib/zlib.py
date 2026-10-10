"""DEFLATE streams with stored-block compression and full decompression.

Stored, fixed Huffman, and dynamic Huffman blocks are decoded incrementally.
Compression emits stored blocks at every valid level; compressed bytes and
sizes differ from CPython. Preset dictionaries are explicitly unsupported.
Temporary input views are released before returning, including failure paths;
only decompression copies input that must survive subsequent streaming calls.
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
Z_PARTIAL_FLUSH = 1
Z_FULL_FLUSH = 3
Z_FINISH = 4
Z_BLOCK = 5
Z_TREES = 6


class error(Exception):
    """Report invalid compressed streams or compression parameters."""


class _NeedInput(Exception):
    """Suspend decoding until the caller supplies another input chunk."""


class _OutputFull(Exception):
    """Suspend decoding before producing output beyond the caller's limit."""


def _require_bytes(data):
    if isinstance(data, bytes):
        return data
    view = memoryview(data)
    try:
        if not view.c_contiguous:
            raise BufferError("memoryview: underlying buffer is not C-contiguous")
        return view.cast("B")
    finally:
        view.release()


def _release_bytes(data):
    if isinstance(data, memoryview):
        data.release()


def _copy_input(data):
    data = _require_bytes(data)
    try:
        return data if isinstance(data, bytes) else bytes(data)
    finally:
        _release_bytes(data)


def _extend_input(out, data, start, end):
    chunk = data[start:end]
    try:
        out.extend(chunk)
    finally:
        _release_bytes(chunk)


def _require_int(value):
    if isinstance(value, int):
        return value
    if hasattr(value, "__index__"):
        value = value.__index__()
        if isinstance(value, int):
            return value
    raise TypeError("an integer is required")


def _level(level):
    level = _require_int(level)
    if level < -1 or level > 9:
        raise error("Bad compression level")
    return level


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
    data = _require_bytes(data)
    try:
        value = _require_int(value)
        return _crc(data, value)
    finally:
        _release_bytes(data)


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
    data = _require_bytes(data)
    try:
        value = _require_int(value)
        return _adler(data, value)
    finally:
        _release_bytes(data)


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
        _extend_input(out, data, pos, pos + size)
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
    data = _require_bytes(data)
    try:
        level = _level(level)
        wbits = _require_int(wbits)
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
    finally:
        _release_bytes(data)


class _Bits:
    def __init__(self, data, pos):
        self.data = data
        self.pos = pos
        self.value = 0
        self.count = 0

    def read(self, count):
        while self.count < count:
            if self.pos >= len(self.data):
                raise _NeedInput()
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
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    13,
    15,
    17,
    19,
    23,
    27,
    31,
    35,
    43,
    51,
    59,
    67,
    83,
    99,
    115,
    131,
    163,
    195,
    227,
    258,
]
_LENGTH_EXTRA = [
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    1,
    1,
    1,
    1,
    2,
    2,
    2,
    2,
    3,
    3,
    3,
    3,
    4,
    4,
    4,
    4,
    5,
    5,
    5,
    5,
    0,
]
_DISTANCE_BASE = [
    1,
    2,
    3,
    4,
    5,
    7,
    9,
    13,
    17,
    25,
    33,
    49,
    65,
    97,
    129,
    193,
    257,
    385,
    513,
    769,
    1025,
    1537,
    2049,
    3073,
    4097,
    6145,
    8193,
    12289,
    16385,
    24577,
]
_DISTANCE_EXTRA = [
    0,
    0,
    0,
    0,
    1,
    1,
    2,
    2,
    3,
    3,
    4,
    4,
    5,
    5,
    6,
    6,
    7,
    7,
    8,
    8,
    9,
    9,
    10,
    10,
    11,
    11,
    12,
    12,
    13,
    13,
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


def _window(wbits, compressing=False):
    wbits = _require_int(wbits)
    if -15 <= wbits <= (-9 if compressing else -8):
        return "raw", -wbits
    if 8 <= wbits <= 15 or (not compressing and wbits == 0):
        return "zlib", max(9, wbits) if compressing else wbits
    if 25 <= wbits <= 31 or (not compressing and wbits in (16, 24)):
        return "gzip", wbits - 16
    if not compressing and (wbits == 32 or 40 <= wbits <= 47):
        return "auto", wbits - 32
    raise error("Invalid initialization option")


def _gzip_header(data, pos):
    start = pos
    if len(data) - pos < 10:
        raise _NeedInput()
    if data[pos : pos + 2] != b"\x1f\x8b":
        raise error("incorrect header check")
    if data[pos + 2] != 8:
        raise error("unknown compression method")
    flags = data[pos + 3]
    if flags & 224:
        raise error("unknown header flags set")
    pos += 10
    if flags & 4:
        if len(data) - pos < 2:
            raise _NeedInput()
        size = data[pos] | (data[pos + 1] << 8)
        pos += size + 2
        if pos > len(data):
            raise _NeedInput()
    for flag in (8, 16):
        if flags & flag:
            while True:
                if pos == len(data):
                    raise _NeedInput()
                byte = data[pos]
                pos += 1
                if byte == 0:
                    break
    if flags & 2:
        if len(data) - pos < 2:
            raise _NeedInput()
        if (_crc(data[start:pos]) & 65535) != data[pos] | (data[pos + 1] << 8):
            raise error("header crc mismatch")
        pos += 2
    return pos


class _Decompress:
    def __init__(self, wbits):
        self._wrapper, self._window = _window(wbits)
        self._bits = _Bits(b"", 0)
        self._state = "block" if self._wrapper == "raw" else "header"
        self._final = False
        self._remaining = 0
        self._distance = 0
        self._literal_tree = None
        self._distance_tree = None
        self._history = []
        self._history_pos = 0
        self._total = 0
        self._crc_value = 4294967295
        self._adler_low = 1
        self._adler_high = 0
        self.eof = False
        self.unused_data = b""
        self.unconsumed_tail = b""
        self._flushed = False

    def _emit(self, value, out):
        out.append(value)
        if len(self._history) < 32768:
            self._history.append(value)
        else:
            self._history[self._history_pos] = value
        self._history_pos = (self._history_pos + 1) & 32767
        self._total += 1
        if self._wrapper == "gzip":
            self._crc_value = _CRC_TABLE[(self._crc_value ^ value) & 255] ^ (self._crc_value >> 8)
        elif self._wrapper == "zlib":
            self._adler_low = (self._adler_low + value) % 65521
            self._adler_high = (self._adler_high + self._adler_low) % 65521

    def _end_block(self):
        if self._final:
            self._bits.align()
            self._state = "done" if self._wrapper == "raw" else "trailer"
        else:
            self._state = "block"

    def _step(self, out, full):
        bits = self._bits
        if self._state == "header":
            if len(bits.data) - bits.pos < 2:
                raise _NeedInput()
            wrapper = self._wrapper
            if wrapper == "auto":
                wrapper = "gzip" if bits.data[bits.pos : bits.pos + 2] == b"\x1f\x8b" else "zlib"
            if wrapper == "gzip":
                bits.pos = _gzip_header(bits.data, bits.pos)
            else:
                cmf = bits.read(8)
                flags = bits.read(8)
                if cmf & 15 != 8 or cmf >> 4 > 7 or ((cmf << 8) | flags) % 31:
                    raise error("incorrect header check")
                if self._window and (cmf >> 4) + 8 > self._window:
                    raise error("invalid window size")
                if flags & 32:
                    raise error("preset dictionaries are not supported")
            self._wrapper = wrapper
            self._state = "block"
        elif self._state == "block":
            final = bits.read(1)
            kind = bits.read(2)
            if kind == 0:
                bits.align()
                size = bits.read(16)
                inverse = bits.read(16)
                if size ^ inverse != 65535:
                    raise error("invalid stored block lengths")
                self._remaining = size
                self._state = "stored"
            elif kind == 1:
                self._literal_tree = _FIXED_LITERAL
                self._distance_tree = _FIXED_DISTANCE
                self._state = "codes"
            elif kind == 2:
                literal_tree, distance_tree = _dynamic(bits)
                self._literal_tree = literal_tree
                self._distance_tree = distance_tree
                self._state = "codes"
            else:
                raise error("invalid block type")
            self._final = bool(final)
        elif self._state == "stored":
            if self._remaining:
                if full:
                    raise _OutputFull()
                self._emit(bits.read(8), out)
                self._remaining -= 1
            else:
                self._end_block()
        elif self._state == "codes":
            symbol = _symbol(bits, self._literal_tree)
            if symbol < 256:
                if full:
                    raise _OutputFull()
                self._emit(symbol, out)
            elif symbol == 256:
                self._end_block()
            else:
                if symbol > 285:
                    raise error("invalid literal/length code")
                index = symbol - 257
                length = _LENGTH_BASE[index] + bits.read(_LENGTH_EXTRA[index])
                distance_symbol = _symbol(bits, self._distance_tree)
                if distance_symbol >= 30:
                    raise error("invalid distance code")
                distance = _DISTANCE_BASE[distance_symbol] + bits.read(
                    _DISTANCE_EXTRA[distance_symbol]
                )
                if distance > len(self._history):
                    raise error("invalid distance too far back")
                self._remaining = length
                self._distance = distance
                self._state = "copy"
        elif self._state == "copy":
            if full:
                raise _OutputFull()
            self._emit(self._history[(self._history_pos - self._distance) & 32767], out)
            self._remaining -= 1
            if not self._remaining:
                self._state = "codes"
        elif self._state == "trailer":
            if self._wrapper == "gzip":
                if len(bits.data) - bits.pos < 8:
                    raise _NeedInput()
                if _get32(bits.data, bits.pos) != self._crc_value ^ 4294967295:
                    raise error("incorrect data check")
                if _get32(bits.data, bits.pos + 4) != self._total & 4294967295:
                    raise error("incorrect length check")
                bits.pos += 8
            else:
                expected = (
                    (bits.read(8) << 24) | (bits.read(8) << 16) | (bits.read(8) << 8) | bits.read(8)
                )
                if expected != (self._adler_high << 16) | self._adler_low:
                    raise error("incorrect data check")
            self._state = "done"
        else:
            self.eof = True

    def decompress(self, data, max_length=0):
        data = _copy_input(data)
        max_length = _require_int(max_length)
        if max_length < 0:
            raise ValueError("max_length must be non-negative")
        if self._flushed:
            raise error("inconsistent stream state")
        if self.eof:
            self.unused_data += data
            return b""
        bits = self._bits
        bits.data = bits.data[bits.pos :] + data
        bits.pos = 0
        self.unconsumed_tail = b""
        out = []
        needs_input = False
        while not self.eof:
            pos, value, count = bits.pos, bits.value, bits.count
            try:
                self._step(out, max_length != 0 and len(out) == max_length)
            except _NeedInput:
                bits.pos, bits.value, bits.count = pos, value, count
                needs_input = True
                break
            except _OutputFull:
                bits.pos, bits.value, bits.count = pos, value, count
                break
        if self._state == "done":
            self.eof = True
        if self.eof:
            self.unused_data = bits.data[bits.pos :]
            bits.data = b""
            bits.pos = 0
        elif not needs_input:
            self.unconsumed_tail = bits.data[bits.pos :]
            bits.data = b""
            bits.pos = 0
        self._needs_input = needs_input
        return bytes(out)

    def flush(self, length=DEF_BUF_SIZE):
        length = _require_int(length)
        if length <= 0:
            raise ValueError("length must be greater than zero")
        if self._flushed:
            return b""
        result = self.decompress(self.unconsumed_tail)
        if self.eof:
            self._flushed = True
        return result


def decompressobj(wbits=MAX_WBITS, zdict=b""):
    """Create an incremental raw, zlib, or gzip stream decoder."""
    if zdict:
        raise NotImplementedError("preset dictionaries are not supported")
    return _Decompress(wbits)


class _ZlibDecompressor:
    """Provide the buffered decompression interface consumed by gzip readers."""

    def __init__(self, wbits=MAX_WBITS, zdict=b""):
        self._decoder = decompressobj(wbits, zdict)
        self.needs_input = True
        self.eof = False
        self.unused_data = b""
        self._pending = b""

    def decompress(self, data, max_length=-1):
        data = _copy_input(data)
        max_length = _require_int(max_length)
        if self.eof:
            raise EOFError("End of stream already reached")
        self._pending += data
        if max_length == 0:
            self.needs_input = not self._pending
            return b""
        result = self._decoder.decompress(self._pending, max(0, max_length))
        self._pending = self._decoder.unconsumed_tail
        self.eof = self._decoder.eof
        self.unused_data = self._decoder.unused_data
        self.needs_input = not self.eof and self._decoder._needs_input
        return result


class _Compress:
    def __init__(self, level, wbits):
        self._wrapper, self._window = _window(wbits, True)
        self._level = level
        self._started = False
        self._finished = False
        self._checksum = 0 if self._wrapper == "gzip" else 1
        self._size = 0

    def _header(self, out):
        if self._started:
            return
        self._started = True
        if self._wrapper == "gzip":
            out.extend(
                [
                    31,
                    139,
                    8,
                    0,
                    0,
                    0,
                    0,
                    0,
                    2 if self._level == 9 else (4 if self._level == 1 else 0),
                    3,
                ]
            )
        elif self._wrapper == "zlib":
            cmf = ((self._window - 8) << 4) | 8
            out.extend([cmf, (-cmf * 256) % 31])

    def compress(self, data):
        data = _require_bytes(data)
        try:
            if self._finished:
                raise error("inconsistent stream state")
            out = []
            self._header(out)
            if self._wrapper == "gzip":
                self._checksum = _crc(data, self._checksum)
            elif self._wrapper == "zlib":
                self._checksum = _adler(data, self._checksum)
            self._size = (self._size + len(data)) & 4294967295
            pos = 0
            while pos < len(data):
                size = min(65535, len(data) - pos)
                out.extend([0, size & 255, size >> 8, (size ^ 65535) & 255, (size ^ 65535) >> 8])
                _extend_input(out, data, pos, pos + size)
                pos += size
            return bytes(out)
        finally:
            _release_bytes(data)

    def flush(self, mode=Z_FINISH):
        mode = _require_int(mode)
        if self._finished:
            raise error("inconsistent stream state")
        if mode not in (Z_NO_FLUSH, Z_PARTIAL_FLUSH, Z_SYNC_FLUSH, Z_FULL_FLUSH, Z_FINISH, Z_BLOCK):
            raise error("inconsistent stream state")
        if mode == Z_NO_FLUSH:
            return b""
        out = []
        self._header(out)
        # Every emitted block is byte-aligned and has no history references.
        if mode != Z_BLOCK:
            out.extend([1 if mode == Z_FINISH else 0, 0, 0, 255, 255])
        if mode == Z_FINISH:
            self._finished = True
            if self._wrapper == "gzip":
                _put32(out, self._checksum)
                _put32(out, self._size)
            elif self._wrapper == "zlib":
                value = self._checksum
                out.extend(
                    [(value >> 24) & 255, (value >> 16) & 255, (value >> 8) & 255, value & 255]
                )
        return bytes(out)


def compressobj(
    level=-1,
    method=DEFLATED,
    wbits=MAX_WBITS,
    memLevel=DEF_MEM_LEVEL,
    strategy=Z_DEFAULT_STRATEGY,
    zdict=b"",
):
    """Create a stored-block streaming compressor."""
    level = _level(level)
    method = _require_int(method)
    memLevel = _require_int(memLevel)
    strategy = _require_int(strategy)
    if method != DEFLATED or not 1 <= memLevel <= 9 or not 0 <= strategy <= 4:
        raise ValueError("Invalid initialization option")
    if zdict:
        raise NotImplementedError("preset dictionaries are not supported")
    return _Compress(level, wbits)


def decompress(data, wbits=MAX_WBITS, bufsize=DEF_BUF_SIZE):
    """Decode one complete stream, verifying its wrapper and checksum."""
    bufsize = _require_int(bufsize)
    if bufsize < 0:
        raise ValueError("bufsize must be non-negative")
    decoder = decompressobj(wbits)
    result = decoder.decompress(data)
    if not decoder.eof:
        raise error("incomplete or truncated stream")
    return result
