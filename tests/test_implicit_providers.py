"""Keep common bundles small while retaining statically requested providers."""

import io

import pytest

from diaspore.compiler import compile_bundle
from diaspore.vm import Machine

from .conftest import ROOT
from .test_selfhost import selfhost_compile


@pytest.mark.parametrize("source", [
    'print("hello")\n',
    'import codecs\nprint(codecs.lookup("utf-8").name)\n',
    'import json\nprint(json.dumps({"a": [1, 2.5, "x"]}, sort_keys=True))\n',
])
def test_common_sources_do_not_include_unicode_tables(tmp_path, source):
    entry = tmp_path / "main.py"
    entry.write_text(source)
    bundle = compile_bundle(str(entry), self_contained=True)
    assert not {"unicodedata", "_unicode15", "_unicode15_names"} & bundle["modules"].keys()
    assert not {"_pyparse", "_pyunparse", "_tokenize", "ast"} & bundle["modules"].keys()
    output = io.StringIO()
    errors = io.StringIO()
    machine = Machine(bundle, stdout=output, stderr=errors)
    try:
        assert machine.run() == 0, errors.getvalue()
    finally:
        machine.close()


@pytest.mark.parametrize("expression", [
    '"\u2603".encode("ascii", "namereplace")',
    'str.encode("\u2603", "ascii", "namereplace")',
    'codecs.encode("\u2603", "ascii", "namereplace")',
    'codecs.namereplace_errors(error)[0].encode("ascii")',
    'codecs.lookup_error("namereplace")(error)[0].encode("ascii")',
])
def test_name_replacement_includes_its_provider(tmp_path, expression):
    entry = tmp_path / "main.py"
    entry.write_text(
        'import codecs\n'
        'error = UnicodeEncodeError("ascii", "\u2603", 0, 1, "ordinal not in range(128)")\n'
        + "print(" + expression + ")\n"
    )
    bundle = compile_bundle(str(entry), self_contained=True)
    assert {"unicodedata", "_unicode15", "_unicode15_names"} <= bundle["modules"].keys()
    output = io.StringIO()
    errors = io.StringIO()
    machine = Machine(bundle, stdout=output, stderr=errors)
    try:
        assert machine.run() == 0, errors.getvalue()
    finally:
        machine.close()
    assert output.getvalue() == "b'\\\\N{SNOWMAN}'\n"


@pytest.mark.parametrize(("expression", "provider", "expected"), [
    ('codecs.encode("abc", "utf-8-sig")', "utf_8_sig", "b'\\xef\\xbb\\xbfabc'"),
    ('"abc".encode("utf-8-sig")', "utf_8_sig", "b'\\xef\\xbb\\xbfabc'"),
    ('str.encode("abc", "utf-8-sig")', "utf_8_sig", "b'\\xef\\xbb\\xbfabc'"),
    ('b"abc".decode("utf-8-sig")', "utf_8_sig", "abc"),
    ('bytes.decode(b"abc", "utf-8-sig")', "utf_8_sig", "abc"),
    ('codecs.decode(b"abc", "utf-8-sig")', "utf_8_sig", "abc"),
    ('codecs.encode("abc", "csisolatin1")', "latin_1", "b'abc'"),
    ('codecs.getencoder("utf-8-sig")("abc")[0]', "utf_8_sig", "b'\\xef\\xbb\\xbfabc'"),
    ('codecs.getdecoder("utf-8-sig")(b"abc")[0]', "utf_8_sig", "abc"),
    ('codecs.getincrementalencoder("utf-8-sig")().encode("abc")',
     "utf_8_sig", "b'\\xef\\xbb\\xbfabc'"),
    ('codecs.getincrementaldecoder("utf-8-sig")().decode(b"abc")', "utf_8_sig", "abc"),
])
def test_static_codec_provider_executes(tmp_path, expression, provider, expected):
    entry = tmp_path / "main.py"
    entry.write_text("import codecs\nprint(" + expression + ")\n")
    bundle = compile_bundle(str(entry), self_contained=True)
    assert "encodings." + provider in bundle["modules"]
    assert not {"unicodedata", "_unicode15", "_unicode15_names"} & bundle["modules"].keys()
    output, errors = io.StringIO(), io.StringIO()
    machine = Machine(bundle, stdout=output, stderr=errors)
    try:
        assert machine.run() == 0, errors.getvalue()
    finally:
        machine.close()
    assert output.getvalue() == expected + "\n"


@pytest.mark.parametrize(("encoding", "provider"), [
    ("utf-8-sig", "utf_8_sig"),
    ("UTF 16 LE", "utf_16_le"),
    ("windows-1252", "cp1252"),
    ("iso-8859-15", "iso8859_15"),
    ("shiftjis", "shift_jis"),
    ("windows.1252", "cp1252"),
    ("base64", "base64_codec"),
    ("rot13", "rot_13"),
])
def test_static_alias_provider_dependencies(tmp_path, encoding, provider):
    entry = tmp_path / "main.py"
    entry.write_text("import codecs\ncodec = codecs.lookup(" + repr(encoding) + ")\n")
    native = compile_bundle(str(entry), self_contained=True)
    assert "encodings." + provider in native["modules"]
    dspc = compile_bundle(str(ROOT / "selfhost" / "dspc.py"))
    hosted = selfhost_compile(dspc, entry, self_contained=True)
    assert hosted["modules"].keys() == native["modules"].keys()


def test_dynamic_encoding_does_not_embed_every_codec(tmp_path):
    entry = tmp_path / "main.py"
    entry.write_text('import codecs\nname = "utf-8-sig"\ncodec = codecs.lookup(name)\n')
    bundle = compile_bundle(str(entry), self_contained=True)
    assert "encodings.utf_8_sig" not in bundle["modules"]


@pytest.mark.parametrize("function", [
    "encode", "decode", "lookup", "getencoder", "getdecoder",
    "getincrementalencoder", "getincrementaldecoder", "getreader", "getwriter",
    "iterencode", "iterdecode",
])
def test_codec_api_inference_uses_upstream_aliases(function):
    from encodings.aliases import aliases

    from diaspore.stdlib._pyparse import _NATIVE_ENCODING_NAMES, implicit_call_modules

    for encoding in aliases:
        encoding = encoding.lower()
        provider = aliases.get(encoding, encoding)
        args = [{"op": "const", "t": "str", "v": encoding}]
        if function in ("encode", "decode", "iterencode", "iterdecode"):
            args.insert(0, {"op": "name", "id": "data"})
        dependencies = implicit_call_modules({
            "op": "call",
            "f": {"op": "attr", "o": {"op": "name", "id": "codecs"}, "n": function},
            "args": args,
        })
        if encoding in _NATIVE_ENCODING_NAMES:
            assert not dependencies
        else:
            assert dependencies == ("codecs", "encodings." + provider)


@pytest.mark.parametrize(("expression", "size"), [
    ("value.__reduce__()", 2),
    ("object.__reduce__(value)", 2),
    ("value.__reduce_ex__(4)", 5),
    ("object.__reduce_ex__(value, 4)", 5),
])
def test_reduction_includes_copyreg_provider(tmp_path, expression, size):
    entry = tmp_path / "main.py"
    entry.write_text(
        "class Item:\n    pass\nvalue = Item()\nprint(len(" + expression + "))\n"
    )
    native = compile_bundle(str(entry), self_contained=True)
    assert "stdlib" not in native
    assert "copyreg" in native["modules"]
    dspc = compile_bundle(str(ROOT / "selfhost" / "dspc.py"))
    hosted = selfhost_compile(dspc, entry, self_contained=True)
    assert "stdlib" not in hosted
    assert hosted["modules"].keys() == native["modules"].keys()
    output, errors = io.StringIO(), io.StringIO()
    machine = Machine(native, stdout=output, stderr=errors)
    try:
        assert machine.run() == 0, errors.getvalue()
    finally:
        machine.close()
    assert output.getvalue() == str(size) + "\n"


def test_hello_does_not_include_copyreg(tmp_path):
    entry = tmp_path / "main.py"
    entry.write_text('print("hello")\n')
    bundle = compile_bundle(str(entry), self_contained=True)
    assert bundle["modules"].keys() == {"main"}
