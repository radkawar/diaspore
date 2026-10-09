"""Read and write JSON, gzip-compressed JSON, and DSPB bundles.

Loaders detect containers from their content; writers select a codec by file
extension. Bundle digests use the decoded JSON model, not the container bytes.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_GZIP_MAGIC = b"\x1f\x8b"


@dataclass(frozen=True)
class Codec:
    """Describe a bundle container's detection, decoding, and encoding.

    Attributes:
        name: Container identifier.
        extensions: File suffixes accepted by the writer.
        sniff: Predicate that recognizes container bytes.
        decode: Function that returns a bundle from container bytes.
        encode: Function accepting a bundle and pretty-print flag.
    """
    name: str
    extensions: tuple[str, ...]
    sniff: Callable[[bytes], bool]
    decode: Callable[[bytes], dict]
    encode: Callable[[dict, bool], bytes]


def _json_encode(bundle: dict, pretty: bool) -> bytes:
    if pretty:
        return (json.dumps(bundle, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return (json.dumps(bundle, separators=(",", ":"), ensure_ascii=False)
            + "\n").encode("utf-8")


JSON_CODEC = Codec(
    name="json",
    extensions=(".json",),
    sniff=lambda b: not b.startswith(_GZIP_MAGIC),
    decode=lambda b: json.loads(b.decode("utf-8")),
    encode=_json_encode,
)

GZIP_CODEC = Codec(
    name="json.gz",
    extensions=(".gz",),
    sniff=lambda b: b.startswith(_GZIP_MAGIC),
    # Detect the inner container so gzip-wrapped DSPB is also accepted.
    decode=lambda b: load_bundle_bytes(gzip.decompress(b)),
    # A fixed timestamp makes gzip output reproducible.
    encode=lambda bundle, pretty: gzip.compress(_json_encode(bundle, pretty), mtime=0),
)


def _dspb_encode(bundle: dict, pretty: bool) -> bytes:
    from . import dspb
    return dspb.encode(bundle)


def _dspb_decode(data: bytes) -> dict:
    from . import dspb
    return dspb.decode(data)


DSPB_CODEC = Codec(
    name="dspb",
    extensions=(".dspb",),
    sniff=lambda b: b.startswith(b"DSPB01"),
    decode=_dspb_decode,
    encode=_dspb_encode,
)

# Check specific signatures before the JSON fallback.
CODECS: tuple[Codec, ...] = (DSPB_CODEC, GZIP_CODEC, JSON_CODEC)


def codec_for_path(path: str | Path) -> Codec:
    """Select a writer codec from a file suffix.

    Args:
        path: Destination path.

    Returns:
        The matching codec, or the JSON codec for an unknown suffix.
    """
    suffix = Path(path).suffix
    for codec in CODECS:
        if suffix in codec.extensions:
            return codec
    return JSON_CODEC


def load_bundle_bytes(data: bytes) -> dict:
    """Decode a bundle by detecting its container.

    Args:
        data: Encoded bundle bytes.

    Returns:
        The decoded bundle.

    Raises:
        ValueError: The container is unrecognized or malformed.
    """
    for codec in CODECS:
        if codec.sniff(data):
            return codec.decode(data)
    raise ValueError("unrecognized bundle encoding")


def load_bundle_file(path: str | Path) -> dict:
    """Read and decode a bundle file.

    Args:
        path: Source path.

    Returns:
        The decoded bundle.
    """
    return load_bundle_bytes(Path(path).read_bytes())


def write_bundle_file(bundle: dict, path: str | Path, pretty: bool = False) -> int:
    """Encode and write a bundle using the destination suffix.

    Args:
        bundle: Bundle to encode.
        path: Destination path.
        pretty: Whether to indent JSON output.

    Returns:
        The number of bytes written.
    """
    data = codec_for_path(path).encode(bundle, pretty)
    Path(path).write_bytes(data)
    return len(data)
