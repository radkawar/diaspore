"""Encode DSPS02 stores and read their index without decoding module payloads.

The index authenticates payload locations and hashes. A module is checked,
decoded, and validated on first access; callers receive isolated copies of its
cached IR. The canonical content id is independent of container compression.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping

MAGIC = b"DSPS02"
_HEADER = struct.Struct("<6sI32s")
_RECORD = struct.Struct("<IIIB32s")
_MAX_INDEX = 64 * 1024 * 1024
_MAX_MODULE = 256 * 1024 * 1024
_MAX_DICTIONARY = 32768


class StoreError(ValueError):
    """An invalid compact standard-library store."""


def _text(value, *, name=False):
    if not isinstance(value, str) or not value or "\0" in value:
        raise StoreError("invalid standard-library index text")
    if name and not all(part.isidentifier() for part in value.split(".")):
        raise StoreError("invalid standard-library module name: " + value)
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        raise StoreError("invalid UTF-8 in standard-library index") from None
    if len(raw) > 65535:
        raise StoreError("standard-library index text is too long")
    return raw


def _dictionary(payloads):
    """Train the measured deterministic cross-module preset dictionary."""
    from collections import Counter

    counts = Counter()
    for payload in payloads.values():
        sample = payload if len(payload) <= 131072 else payload[:65536] + payload[-65536:]
        patterns = set()
        for width, stride in ((8, 4), (16, 8), (32, 16)):
            patterns.update(sample[pos:pos + width]
                            for pos in range(0, len(sample) - width + 1, stride))
        counts.update(patterns)
    ranked = sorted(((pattern, count) for pattern, count in counts.items() if count > 1),
                    key=lambda item: (item[1] * (len(item[0]) - 3), item[1], item[0]),
                    reverse=True)
    selected = bytearray()
    chunks = []
    for pattern, _ in ranked:
        if pattern in selected:
            continue
        if len(selected) + len(pattern) > _MAX_DICTIONARY:
            continue
        selected.extend(pattern)
        chunks.append(pattern)
        if len(selected) == _MAX_DICTIONARY:
            break
    # Deflate prefers dictionary suffixes; put the most frequent patterns last.
    return b"".join(reversed(chunks))


def encode_stdlib_store(store: dict, *, compress: bool = True) -> bytes:
    """Encode a canonical parsed store as independently compressed DSPB modules.

    Args:
        store: A source-compiled store with its canonical content identifier.
        compress: Use preset-dictionary raw deflate; false writes raw DSPB payloads.

    Returns:
        A complete DSPS02 container, without distribution metadata.
    """
    import zlib

    from .dspb import encode
    from .stdlib_store import _validate_store

    _validate_store(store)
    modules = store["modules"]
    decoded_payloads = {
        name: encode({"diaspore": 1, "main": name, "modules": {name: modules[name]}})
        for name in sorted(modules)
    }
    preset = _dictionary(decoded_payloads) if compress else b""
    index = bytearray(bytes.fromhex(store["id"][7:]))
    index.extend(struct.pack("<H", len(preset)))
    index.extend(preset)
    index.extend(struct.pack("<I", len(modules)))
    payloads = []
    offset = 0
    for name in sorted(modules):
        module = modules[name]
        for text in (_text(name, name=True), _text(module["file"])):
            index.extend(struct.pack("<H", len(text)))
            index.extend(text)
        decoded = decoded_payloads[name]
        if compress:
            compressor = (zlib.compressobj(level=9, wbits=-15, zdict=preset) if preset
                          else zlib.compressobj(level=9, wbits=-15))
            payload = compressor.compress(decoded) + compressor.flush()
        else:
            payload = decoded
        if len(decoded) > _MAX_MODULE or len(payload) > _MAX_MODULE:
            raise StoreError("standard-library module payload is too large")
        if offset + len(payload) > 0xFFFFFFFF:
            raise StoreError("standard-library store payloads are too large")
        index.extend(_RECORD.pack(offset, len(payload), len(decoded), int(compress),
                                  hashlib.sha256(payload).digest()))
        payloads.append(payload)
        offset += len(payload)
    if len(index) > _MAX_INDEX:
        raise StoreError("standard-library index is too large")
    return b"".join([_HEADER.pack(MAGIC, len(index), hashlib.sha256(index).digest()),
                     index, *payloads])


class LazyModules(Mapping):
    """Expose index membership and isolated, lazily decoded module IR."""

    def __init__(self, records, payload_start, source, preset):
        """Retain validated index records and the distribution bytes."""
        import threading

        self._records = records
        self._payload_start = payload_start
        self._source = source
        self._preset = preset
        self._cache = {}
        self._lock = threading.Lock()

    def __len__(self):
        """Return the indexed module count without touching payloads."""
        return len(self._records)

    def __iter__(self):
        """Iterate module names in their canonical sorted order."""
        return iter(self._records)

    def __contains__(self, name):
        """Check membership using only the index."""
        return name in self._records

    def module_file(self, name):
        """Return an indexed source filename without decoding its module."""
        return self._records[name][0]

    def __getitem__(self, name):
        """Return a private copy of one validated module, decoding it once."""
        import copy

        record = self._records[name]
        with self._lock:
            if name not in self._cache:
                self._cache[name] = self._decode(name, record)
            module = self._cache[name]
        return copy.deepcopy(module)

    def _decode(self, name, record):
        from .dspb import MAGIC as DSPB_MAGIC
        from .dspb import decode
        from .validator import validate_bundle

        filename, offset, stored, decoded, codec, digest = record
        start = self._payload_start + offset
        payload = self._source[start:start + stored]
        if len(payload) != stored or hashlib.sha256(payload).digest() != digest:
            raise StoreError("standard-library payload digest mismatch: " + name)
        if codec == 1:
            import zlib

            try:
                inflater = (zlib.decompressobj(wbits=-15, zdict=self._preset) if self._preset
                            else zlib.decompressobj(wbits=-15))
                payload = inflater.decompress(payload, decoded + 1)
                if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
                    raise StoreError("invalid standard-library deflate payload: " + name)
            except zlib.error as error:
                raise StoreError("invalid standard-library deflate payload: " + name) from error
        if len(payload) != decoded:
            raise StoreError("standard-library decoded length mismatch: " + name)
        if not payload.startswith(DSPB_MAGIC):
            raise StoreError("standard-library payload must be a standalone DSPB01 bundle")
        bundle = decode(payload)
        modules = bundle["modules"]
        if (bundle.get("diaspore") != 1 or "stdlib" in bundle or bundle["main"] != name
                or len(modules) != 1 or name not in modules
                or modules[name].get("name") != name
                or modules[name].get("file") != filename):
            raise StoreError("standard-library payload identity mismatch: " + name)
        validate_bundle(bundle)
        return modules[name]


def _header(header, total):
    if len(header) != _HEADER.size:
        raise StoreError("truncated standard-library header")
    magic, length, digest = _HEADER.unpack(header)
    if magic != MAGIC:
        raise StoreError("not a DSPS02 standard-library store")
    if length < 38 or length > _MAX_INDEX or length > total - _HEADER.size:
        raise StoreError("invalid standard-library index length")
    return length, digest


def _parse_index(index, digest, total, source):
    if hashlib.sha256(index).digest() != digest:
        raise StoreError("standard-library index digest mismatch")
    identifier = "sha256:" + index[:32].hex()
    dictionary_length = struct.unpack_from("<H", index, 32)[0]
    if dictionary_length > _MAX_DICTIONARY or 38 + dictionary_length > len(index):
        raise StoreError("invalid standard-library dictionary length")
    preset = index[34:34 + dictionary_length]
    pos = 34 + dictionary_length
    count = struct.unpack_from("<I", index, pos)[0]
    pos += 4
    if count > (len(index) - pos) // (_RECORD.size + 6):
        raise StoreError("invalid standard-library module count")
    records = {}
    end = 0
    previous = ""
    for _ in range(count):
        texts = []
        for is_name in (True, False):
            if pos + 2 > len(index):
                raise StoreError("truncated standard-library index text")
            size = struct.unpack_from("<H", index, pos)[0]
            pos += 2
            if pos + size > len(index):
                raise StoreError("truncated standard-library index text")
            try:
                text = index[pos:pos + size].decode("utf-8")
            except UnicodeError:
                raise StoreError("invalid UTF-8 in standard-library index") from None
            _text(text, name=is_name)
            texts.append(text)
            pos += size
        name, filename = texts
        if name <= previous:
            raise StoreError("standard-library names must be sorted and unique")
        previous = name
        if pos + _RECORD.size > len(index):
            raise StoreError("truncated standard-library index record")
        offset, stored, decoded, codec, payload_digest = _RECORD.unpack_from(index, pos)
        pos += _RECORD.size
        if codec not in (0, 1):
            raise StoreError("unsupported standard-library payload codec")
        if (offset != end or not 6 <= decoded <= _MAX_MODULE
                or not 1 <= stored <= _MAX_MODULE or codec == 0 and stored != decoded):
            raise StoreError("invalid standard-library payload span")
        end += stored
        records[name] = filename, offset, stored, decoded, codec, payload_digest
    payload_start = _HEADER.size + len(index)
    if pos != len(index) or payload_start + end != total:
        raise StoreError("standard-library index or payloads contain trailing or missing data")
    return {"diaspore_stdlib": 1, "id": identifier,
            "modules": LazyModules(records, payload_start, source, preset)}


def decode_stdlib_store(data: bytes) -> dict:
    """Parse a compact store's index, retaining its bytes for lazy module access.

    Args:
        data: A complete DSPS02 container.

    Returns:
        The store identifier and a lazy module mapping.
    """
    if not isinstance(data, bytes):
        data = bytes(data)
    length, digest = _header(data[:_HEADER.size], len(data))
    return _parse_index(data[_HEADER.size:_HEADER.size + length], digest, len(data), data)


def load_stdlib_store(path) -> dict:
    """Read a distribution once and parse only its compact index.

    Args:
        path: The installed DSPS02 file.

    Returns:
        The store identifier and a lazy module mapping backed by immutable bytes.
    """
    with open(path, "rb") as source:
        data = source.read()
    return decode_stdlib_store(data)
