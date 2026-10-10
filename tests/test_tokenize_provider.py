"""Compare the maintained tokenizer with the pinned CPython C backend."""

import _tokenize
import importlib.util
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_PATH = ROOT / "src/diaspore/stdlib/_tokenize.py"
UPSTREAM_PATH = ROOT / "src/diaspore/cpython/tokenize.py"


@pytest.fixture
def provider():
    spec = importlib.util.spec_from_file_location("diaspore_tokenize_test", PROVIDER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def outcome(module, source, extra, encoding=None):
    reader = io.StringIO(source).readline if encoding is None else io.BytesIO(source).readline
    kwargs = {"extra_tokens": extra}
    if encoding is not None:
        kwargs["encoding"] = encoding
    iterator = module.TokenizerIter(reader, **kwargs)
    tokens = []
    try:
        tokens.extend(iterator)
    except Exception as error:
        return tokens, (
            type(error).__name__,
            error.args,
            getattr(error, "lineno", None),
            getattr(error, "offset", None),
            getattr(error, "text", None),
            getattr(error, "end_lineno", None),
            getattr(error, "end_offset", None),
        )
    return tokens, None


SOURCES = [
    "",
    "x",
    "x\n",
    "# comment",
    "# comment\n",
    "  ",
    "\n\n",
    "  # comment\n\nx = 1 # trailing\n",
    "async def f():\n await g()\n",
    "if x:\n\ty\n\tz\nw\n",
    "if x:\n    if y:\n        z\nw\n",
    "if x:\n \f  y\n\n# comment\nz\n",
    "x = (\n  2, # c\n  3\n)\n",
    "x\\\ny\n",
    "x = \\\n    y\n",
    "  \\\n  x\n",
    "é = α + a\u0301\n",
    "𐐀 = '😀'\n",
    "😀",
    "\ufeffx",
    "a\u00a0b",
    "x\r\ny\r",
    "x\rZ\n",
    "`$?",
    "! != := -> ... ** // << >>=\n",
    "0 00 0_0 123_456 0x_1 0o_7 0b_1 1. .2 3.e2 1e-3 2j 3.4J\n",
    "09",
    "0123",
    "1e",
    "12foo",
    "1_",
    "0x",
    "0x__1",
    "0b2",
    "0o8",
    "0b12",
    "0o78",
    "1e+",
    "1e_1",
    "1._0",
    "0x1z",
    "r'a' b'a' u'a' R'a' br'a' rb'a' ur'a'\n",
    "'a\\\nb'\n",
    "'''a\nb''' + x\n",
    '"""é\n𐐀""" + y\n',
    "'",
    "b'bad",
    "'''bad\n",
    "x\x00y\n",
    "x\x0by",
    "[)",
    ")",
    "a=(\n",
    "\\\n",
    "x=\\x\n",
    "if x:\n  y\n z\n",
    "if x:\n\ty\n        z\n",
    'f""',
    'F"abc"',
    'rf"a\\b"',
    'fr"{x}"',
    'f"hi {x!r:>{w}} end"\n',
    'f"{{a}}"',
    'f"é{{é}}x"',
    'f"{x=}"',
    'f"{x:}"',
    'f"{x:{w}}"',
    'f"{x:{{}}}"',
    'f"{x:{y:{z}}}"',
    'f"{x:=10}"',
    'f"{(x:=10)}"',
    'f"{ {1:2} }"',
    'f"{f"{x}"}"',
    'f"{ "same quote" }"',
    'f"{\n x # comment\n}"\n',
    'f"""a\nb{x}\nc"""\n',
    'f"\\N{LATIN CAPITAL LETTER A}x"',
    'rf"\\N{A}"',
    'f"a\\\nb"',
    'f"}"',
    'f"{x"',
    'f"a\nb"',
    'f"""bad\n',
    'f"{(]}"',
    "0b1_2",
    "0o1_8",
    "\rname",
    "\r#comment",
    "[α\r[]f",
    "x\rα",
    ":[\r",
    'f"{x:{y:{z:{q}}}}"',
    'f"{#comment"',
    'f"{\n\\x"',
    "é;0123",
    "x\n\nif y:\n z\n",
    "α\n'''b\nc'''\n",
    'x\nf"""a\nb{x}\nc"""\n',
    "(" * 201,
    'f"{' * 150,
    "".join(" " * level + "if x:\n" for level in range(101)),
]


@pytest.mark.parametrize("extra", [False, True])
@pytest.mark.parametrize("source", SOURCES)
def test_tokenizer_matches_cpython(provider, source, extra):
    assert outcome(provider, source, extra) == outcome(_tokenize, source, extra)


@pytest.mark.parametrize("extra", [False, True])
@pytest.mark.parametrize(
    "filename",
    [
        "stdlib/_tokenize.py",
        "stdlib/_pyparse.py",
        "cpython/tokenize.py",
        "cpython/linecache.py",
        "cpython/warnings.py",
        "cpython/inspect.py",
        "cpython/collections/__init__.py",
    ],
)
def test_complete_source_files(provider, filename, extra):
    source = (ROOT / "src/diaspore" / filename).read_text()
    assert outcome(provider, source, extra) == outcome(_tokenize, source, extra)


@pytest.mark.parametrize("extra", [False, True])
@pytest.mark.parametrize("action", ["always", "error"])
@pytest.mark.parametrize("source", ["1and 2", "0x1or 2", 'f"\\{x}"', 'f"\\}"'])
def test_lexical_warnings(provider, source, action, extra):
    import warnings

    def collect(module):
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter(action)
            result = outcome(module, source, extra)
        return result, [(str(w.message), w.category, w.filename, w.lineno) for w in captured]

    assert collect(provider) == collect(_tokenize)


@pytest.mark.parametrize("extra", [False, True])
@pytest.mark.parametrize(
    "chunks",
    [
        ["x", "y", ""],
        ["x\ny\n", ""],
        ["'''x", "y'''", ""],
        ['f"""x', "y{x}", 'z"""', ""],
        ["# c", "x\r\n", ""],
    ],
)
def test_callback_chunks(provider, chunks, extra):
    assert list(provider.TokenizerIter(iter(chunks).__next__, extra_tokens=extra)) == list(
        _tokenize.TokenizerIter(iter(chunks).__next__, extra_tokens=extra)
    )


@pytest.mark.parametrize("extra", [False, True])
@pytest.mark.parametrize(
    "source,encoding",
    [
        (b"x = '\xe9'\r\n", "latin-1"),
        ("α = '😀'\n".encode(), "utf-8"),
        (b"x\xff\n", "utf-8"),
        (b"# coding: latin-1\n'\xe9'\n", "latin-1"),
        (b"\xef\xbb\xbfx\n", "utf-8-sig"),
    ],
)
def test_encoded_callback_matches_cpython(provider, source, encoding, extra):
    assert outcome(provider, source, extra, encoding) == outcome(_tokenize, source, extra, encoding)


def test_callback_is_lazy_and_stopiteration_is_eof(provider):
    calls = []
    lines = iter(["x + y\n", "z\n"])

    def readline():
        calls.append(None)
        return next(lines)

    iterator = provider.TokenizerIter(readline, extra_tokens=True)
    assert calls == []
    assert iter(iterator) is iterator
    assert next(iterator)[:2] == (1, "x")
    assert len(calls) == 1
    assert next(iterator)[:2] == (55, "+")
    assert len(calls) == 1
    assert list(iterator)[-1] == (0, "", (3, 0), (3, 0), "")
    assert len(calls) == 3
    with pytest.raises(StopIteration, match="EOF"):
        next(iterator)
    assert len(calls) == 3


@pytest.mark.parametrize(
    "result,kwargs,message",
    [
        (b"x", {}, "non-string"),
        (None, {}, "non-string"),
        ("x", {"encoding": "utf-8"}, "non-bytes"),
        (bytearray(b"x"), {"encoding": "utf-8"}, "non-bytes"),
    ],
)
def test_callback_types(provider, result, kwargs, message):
    iterator = provider.TokenizerIter(lambda: result, extra_tokens=True, **kwargs)
    with pytest.raises(TypeError, match=message):
        next(iterator)


def test_callback_exception_propagates(provider):
    error = RuntimeError("read failure")

    def readline():
        raise error

    with pytest.raises(RuntimeError) as caught:
        next(provider.TokenizerIter(readline, extra_tokens=True))
    assert caught.value is error


@pytest.mark.parametrize("encoding", [None, 42, b"utf-8"])
def test_encoding_argument_rejects_non_strings(provider, encoding):
    with pytest.raises(TypeError):
        provider.TokenizerIter(lambda: b"", encoding=encoding, extra_tokens=True)


def test_encoding_lookup_and_surrogate_errors(provider):
    with pytest.raises(ValueError, match="embedded null"):
        provider.TokenizerIter(lambda: b"", encoding="utf-8\x00", extra_tokens=True)
    for source, encoding in [(b"x", "no-such-codec"), (b"\xff", "ascii")]:
        assert outcome(provider, source, True, encoding) == outcome(
            _tokenize, source, True, encoding
        )
    assert outcome(provider, "'\ud800'", True) == outcome(_tokenize, "'\ud800'", True)


def test_unchanged_public_tokenize_uses_provider(provider):
    spec = importlib.util.spec_from_file_location("diaspore_public_tokenize_test", UPSTREAM_PATH)
    upstream = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upstream)
    import tokenize

    upstream._tokenize = provider

    def collect(module, source, binary):
        tokens = []
        try:
            if binary:
                tokens.extend(module.tokenize(io.BytesIO(source.encode()).readline))
            else:
                tokens.extend(module.generate_tokens(io.StringIO(source).readline))
        except Exception as error:
            return tokens, (type(error).__name__, error.args)
        return tokens, None

    for source in SOURCES:
        assert collect(upstream, source, False) == collect(tokenize, source, False)
        assert collect(upstream, source, True) == collect(tokenize, source, True)


def test_public_tokenize_runs_in_sandbox(tmp_path):
    from .conftest import run_diaspore_py

    entry = tmp_path / "main.py"
    entry.write_text(
        "import io\nimport tokenize\n"
        "source = 'x = f\"hi {name!r}\" # comment\\n'\n"
        "tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))\n"
        "assert [t.string for t in tokens] == "
        "['x', '=', 'f\"', 'hi ', '{', 'name', '!', 'r', '}', '\"', '# comment', '\\n', '']\n"
        "assert tokens[0].start == (1, 0)\n"
        "assert tokens[-1].type == tokenize.ENDMARKER\n"
        "encoded = list(tokenize.tokenize(io.BytesIO(source.encode()).readline))\n"
        "assert encoded[0].type == tokenize.ENCODING\n"
        "assert encoded[0].string == 'utf-8'\n"
        "assert encoded[1:] == tokens\n"
        "print('tokenize ok')\n"
    )
    result = run_diaspore_py(entry)
    assert result.code == 0, result.err_line
    assert result.stdout == "tokenize ok\n"
