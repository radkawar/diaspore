"""Compare portable codec primitives with CPython's C-layer contracts."""

import _codecs as oracle
import io
import subprocess
import sys

import pytest

from diaspore.compiler import compile_bundle
from diaspore.stdlib import _codecs as portable
from diaspore.vm import Machine


def outcome(function, *args):
    try:
        return ("return", function(*args))
    except UnicodeError as exc:
        return (type(exc).__name__, exc.encoding, exc.object, exc.start, exc.end, exc.reason)
    except Exception as exc:
        return (type(exc).__name__, str(exc))


@pytest.mark.parametrize("width", [16, 32])
@pytest.mark.parametrize("suffix", ["", "_le", "_be"])
@pytest.mark.parametrize(
    "errors", ["strict", "ignore", "replace", "backslashreplace", "surrogatepass"]
)
def test_wide_encoding_oracle(width, suffix, errors):
    name = "utf_" + str(width) + suffix + "_encode"
    for text in ["", "a\x00\uffff\U00010000\U0010ffff", "a\ud800\udfffz"]:
        assert outcome(getattr(portable, name), text, errors) == outcome(
            getattr(oracle, name), text, errors
        )


@pytest.mark.parametrize("width", [16, 32])
@pytest.mark.parametrize("suffix", ["", "_le", "_be"])
@pytest.mark.parametrize(
    "errors", ["strict", "ignore", "replace", "backslashreplace", "surrogatepass"]
)
@pytest.mark.parametrize("final", [False, True])
def test_wide_decoding_oracle(width, suffix, errors, final):
    name = "utf_" + str(width) + suffix + "_decode"
    encode = getattr(oracle, "utf_" + str(width) + suffix + "_encode")
    good = encode("a\x00\uffff\U00010000\U0010ffff")[0]
    malformed = [
        b"\x00\xd8",
        b"\x00\xdc",
        b"\x00\xd8A\x00",
        b"\x00\xd8\x00\x00",
        b"\x00\x00\x11\x00",
        b"\xff\xff\xff\xff",
    ]
    for data in [good[:end] for end in range(len(good) + 1)] + malformed:
        assert outcome(getattr(portable, name), data, errors, final) == outcome(
            getattr(oracle, name), data, errors, final
        )


@pytest.mark.parametrize("width", [16, 32])
@pytest.mark.parametrize("byteorder", [-2, -1, 0, 1, 2])
def test_wide_byteorder_oracle(width, byteorder):
    encode = "utf_" + str(width) + "_encode"
    decode = "utf_" + str(width) + "_ex_decode"
    assert outcome(getattr(portable, encode), "A", "strict", byteorder) == outcome(
        getattr(oracle, encode), "A", "strict", byteorder
    )
    for order in ["le", "be"]:
        data = "\ufeffA".encode("utf-" + str(width) + "-" + order)
        for size in range(len(data) + 1):
            assert outcome(
                getattr(portable, decode), data[:size], "strict", byteorder, True
            ) == outcome(getattr(oracle, decode), data[:size], "strict", byteorder, True)


@pytest.mark.parametrize("name", ["utf_16_le_encode", "utf_32_be_encode"])
@pytest.mark.parametrize("replacement", ["X", b"X", b"ABCD", "\ud800"])
def test_wide_encoding_error_callbacks(name, replacement):
    def handler(exc):
        return replacement, -1

    oracle.register_error("codec_primitive_test", handler)
    portable.register_error("codec_primitive_test", handler)
    assert outcome(getattr(portable, name), "a\ud800z", "codec_primitive_test") == outcome(
        getattr(oracle, name), "a\ud800z", "codec_primitive_test"
    )


@pytest.mark.parametrize("name", ["utf_16_le_decode", "utf_32_be_decode", "charmap_decode"])
def test_decode_buffer_protocol(name):
    for value in [bytearray(b"abcd"), memoryview(b"abcd"), memoryview(b"abcd")[::2], 4, [65, 66]]:
        assert outcome(getattr(portable, name), value) == outcome(getattr(oracle, name), value)


@pytest.mark.parametrize("errors", ["strict", "ignore", "replace", "backslashreplace"])
def test_charmap_oracle(errors):
    for table in ["abc", "\x00" + "x" * 255, "\x00\ufffe\u2603", "a\ufffeb", "abc" * 100]:
        actual_map = portable.charmap_build(table)
        expected_map = oracle.charmap_build(table)
        for text in ["abc", "\u2603\ufffe", "\x00x?"]:
            assert outcome(portable.charmap_encode, text, errors, actual_map) == outcome(
                oracle.charmap_encode, text, errors, expected_map
            )
        for data in [b"", b"\x00\x01\x02\xff", b"abc"]:
            assert outcome(portable.charmap_decode, data, errors, table) == outcome(
                oracle.charmap_decode, data, errors, table
            )
    for mapping in [{65: b"xy", 66: None}, {65: 256}, {65: "x"}, {65: -1}]:
        assert outcome(portable.charmap_encode, "ABC", errors, mapping) == outcome(
            oracle.charmap_encode, "ABC", errors, mapping
        )
    for mapping in [{65: "xy", 66: None}, {65: 0x110000}, {65: b"x"}, {65: -1}]:
        assert outcome(portable.charmap_decode, b"ABC", errors, mapping) == outcome(
            oracle.charmap_decode, b"ABC", errors, mapping
        )


def run_oracle_program(tmp_path, source):
    entry = tmp_path / "main.py"
    entry.write_text(source)
    expected = subprocess.run(
        [sys.executable, str(entry)], capture_output=True, text=True, check=True
    )
    bundle = compile_bundle(str(entry), self_contained=True)
    output, errors = io.StringIO(), io.StringIO()
    machine = Machine(bundle, stdout=output, stderr=errors)
    try:
        assert machine.run() == 0, errors.getvalue()
    finally:
        machine.close()
    assert output.getvalue() == expected.stdout


@pytest.mark.parametrize(
    "encoding",
    [
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "utf-32",
        "utf-32-le",
        "utf-32-be",
        "windows-1252",
        "iso8859-15",
    ],
)
def test_codec_provider_roundtrip(tmp_path, encoding):
    run_oracle_program(
        tmp_path,
        "import codecs\ntext = 'abc \\u20ac'\n"
        + "data = codecs.encode(text, "
        + repr(encoding)
        + ")\n"
        + "print(data)\nprint(codecs.decode(data, "
        + repr(encoding)
        + "))\n",
    )


@pytest.mark.parametrize(
    "encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"]
)
def test_json_decodes_unicode_bytes(tmp_path, encoding):
    data = '{"value": "\u2603\U0001f600"}'.encode(encoding)
    provider = "encodings." + encoding.replace("-", "_")
    run_oracle_program(
        tmp_path,
        "import json\nimport " + provider + "\n" + "print(json.loads(" + repr(data) + "))\n",
    )


@pytest.mark.parametrize("width", [16, 32])
@pytest.mark.parametrize("byteorder", [None, 1.5, "little", 1 << 40, -(1 << 40)])
def test_byteorder_validation(width, byteorder):
    for suffix, value in [("_encode", "a"), ("_ex_decode", b"")]:
        name = "utf_" + str(width) + suffix
        assert outcome(getattr(portable, name), value, "strict", byteorder) == outcome(
            getattr(oracle, name), value, "strict", byteorder
        )


@pytest.mark.parametrize("width", [16, 32])
@pytest.mark.parametrize("position", [-1, 99])
def test_wide_decode_error_callbacks(width, position):
    name = "utf_" + str(width) + "_le_decode"
    data = bytes([0] * (width // 8 - 1) + [0xD8]) + b"x"

    def handler(exc):
        return "replacement", position

    oracle.register_error("codec_primitive_decode_test", handler)
    portable.register_error("codec_primitive_decode_test", handler)
    assert outcome(getattr(portable, name), data, "codec_primitive_decode_test", False) == outcome(
        getattr(oracle, name), data, "codec_primitive_decode_test", False
    )


@pytest.mark.parametrize(
    "encoding", ["utf16", "utf-16", "utf16le", "utf-16le", "utf_16_le", "UTF-32BE", "utf-16garbage"]
)
def test_surrogatepass_handler_aliases(encoding):
    error = UnicodeEncodeError(encoding, "\ud800", 0, 1, "surrogates not allowed")
    assert outcome(portable.lookup_error("surrogatepass"), error) == outcome(
        oracle.lookup_error("surrogatepass"), error
    )
