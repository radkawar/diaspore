"""Cross-check portable marshal data with the CPython format oracle."""

import importlib.util
import io
import marshal
from pathlib import Path

import pytest

PROVIDER_PATH = Path(__file__).resolve().parents[1] / "src/diaspore/stdlib/marshal.py"


@pytest.fixture
def provider():
    spec = importlib.util.spec_from_file_location("diaspore_marshal_test", PROVIDER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", range(5))
@pytest.mark.parametrize("value", [
    None, False, True, StopIteration, Ellipsis, 0, -2147483648, 2147483647,
    2147483648, -(2**123), 2**200, 1.25, -0.0, float("inf"),
    complex(1.25, -2.5), b"\x00\xff", "ascii", "a" * 256, "\u20ac\ud800",
    (), (1, "x"), [1, None], {"a": [1, 2]}, {1, 2}, frozenset({3, 4}),
])
def test_interoperable_data(provider, version, value):
    assert provider.loads(marshal.dumps(value, version)) == value
    assert marshal.loads(provider.dumps(value, version)) == value
    assert provider.loads(provider.dumps(value, version)) == value


@pytest.mark.parametrize("version", [3, 4])
def test_shared_and_cyclic_references(provider, version):
    child = []
    mapping = {"child": child}
    root = (child, mapping, child)
    child.extend([root, mapping])
    for encoder, decoder in [(marshal, provider), (provider, marshal), (provider, provider)]:
        result = decoder.loads(encoder.dumps(root, version))
        assert result[0] is result[2]
        assert result[0][0] is result
        assert result[0][1] is result[1]
        assert result[1]["child"] is result[0]


@pytest.mark.parametrize("version", [0, 1, 2])
def test_old_versions_reject_cycles(provider, version):
    value = []
    value.append(value)
    with pytest.raises(ValueError, match="deeply nested"):
        provider.dumps(value, version)


@pytest.mark.parametrize("payload, error", [
    (b"", EOFError), (b"i\x00", EOFError), (b"?", ValueError),
    (b"0", TypeError), (b"r\x00\x00\x00\x00", ValueError),
    (b"s\xff\xff\xff\xff", ValueError),
    (b"[\xff\xff\xff\x7f", EOFError),
    (b"s\xff\xff\xff\x7f", EOFError),
    (b"l\x01\x00\x00\x00\x00\x00", ValueError),
    (b"l\x01\x00\x00\x00\x00\x80", ValueError),
    (b"l\x00\x00\x00\x80", ValueError),
    (b"\xa9\x01r\x00\x00\x00\x00", ValueError),
])
def test_malformed_records(provider, payload, error):
    with pytest.raises(error):
        provider.loads(payload)
    with pytest.raises(error):
        provider.load(io.BytesIO(payload))


def test_buffers_and_stream_position(provider):
    value = {"payload": b"x" * 70000}
    stream = io.BytesIO()
    assert provider.dump(value, stream) is None
    provider.dump(42, stream, 0)
    stream.seek(0)
    assert provider.load(stream) == value
    assert provider.load(stream) == 42
    with pytest.raises(EOFError):
        provider.load(stream)
    encoded = marshal.dumps(value)
    assert provider.loads(bytearray(encoded)) == value
    assert provider.loads(memoryview(encoded)) == value
    assert provider.loads(encoded + b"trailing") == value
    assert marshal.loads(provider.dumps(bytearray(b"abc"))) == b"abc"
    with pytest.raises(TypeError):
        provider.loads(3)
    with pytest.raises(BufferError):
        provider.loads(memoryview(encoded)[::2])
    with pytest.raises(TypeError):
        provider.load(io.StringIO("N"))


def test_code_and_api_boundary(provider):
    code = compile("pass", "<test>", "exec")
    with pytest.raises(ValueError, match="code objects are unsupported"):
        provider.loads(marshal.dumps(code))
    with pytest.raises(ValueError, match="code objects are unsupported"):
        provider.dumps(code)
    with pytest.raises(ValueError, match="unmarshallable"):
        provider.dumps(object())
    with pytest.raises(TypeError):
        provider.dumps(None, allow_code=False)
    with pytest.raises(TypeError):
        provider.loads(b"N", allow_code=False)
    assert provider.version == 4


@pytest.mark.parametrize("received, error", [
    (None, TypeError), (1.0, TypeError), (0, EOFError), (-1, EOFError), (2, ValueError),
])
def test_invalid_stream_read_counts(provider, received, error):
    class Stream:
        def read(self, size):
            return b""

        def readinto(self, buffer):
            return received

    with pytest.raises(error):
        provider.load(Stream())


@pytest.mark.parametrize("version", [None, "4", 1.0])
def test_version_requires_integer(provider, version):
    with pytest.raises(TypeError):
        provider.dumps(None, version)


@pytest.mark.parametrize("depth", [300, 1500, 1999])
@pytest.mark.parametrize("version", range(5))
def test_cpython_nesting_limit(provider, depth, version):
    value = None
    for _ in range(depth):
        value = [value]
    for encoder, decoder in [(marshal, provider), (provider, marshal), (provider, provider)]:
        result = decoder.loads(encoder.dumps(value, version))
        for _ in range(depth):
            assert type(result) is list
            assert len(result) == 1
            result = result[0]
        assert result is None


def test_cpython_nesting_overflow(provider):
    value = None
    for _ in range(2000):
        value = [value]
    payload = b"[\x01\x00\x00\x00" * 2000 + b"N"
    for implementation in (marshal, provider):
        with pytest.raises(ValueError):
            implementation.dumps(value)
        with pytest.raises(ValueError):
            implementation.loads(payload)


def test_provider_runs_in_sandbox(tmp_path):
    from .conftest import run_diaspore_py

    source = tmp_path / "main.py"
    oracle = marshal.dumps({"a": [1, 2], "b": (3.5, b"ok")}, 4)
    source.write_text(
        "import marshal\n"
        "import importlib._bootstrap_external\n"
        f"assert marshal.loads({oracle!r}) == {{'a': [1, 2], 'b': (3.5, b'ok')}}\n"
        "value = []\n"
        "value.append(value)\n"
        "result = marshal.loads(marshal.dumps(value))\n"
        "assert result[0] is result\n"
        "assert marshal.loads(marshal.dumps((1, 'text', b'bytes'))) == (1, 'text', b'bytes')\n"
        "deep = None\n"
        "for index in range(300):\n"
        "    deep = [deep]\n"
        "deep = marshal.loads(marshal.dumps(deep))\n"
        "for index in range(300):\n"
        "    deep = deep[0]\n"
        "assert deep is None\n"
        "try:\n"
        "    marshal.loads(b'c')\n"
        "except ValueError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('code object accepted')\n"
    )
    result = run_diaspore_py(source)
    assert result.code == 0, result.err_line
    assert result.stdout == ""
