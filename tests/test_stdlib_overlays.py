"""Check maintained overlay reproducibility and deferred codec registration."""

import difflib
import io
from pathlib import Path

from diaspore import Machine
from diaspore.compiler import compile_bundle

ROOT = Path(__file__).resolve().parents[1]


def test_io_overlay_matches_recorded_patch():
    upstream = ROOT / "src/diaspore/cpython/_pyio.py"
    overlay = ROOT / "src/diaspore/stdlib/_io.py"
    expected = "".join(
        difflib.unified_diff(
            upstream.read_text().splitlines(keepends=True),
            overlay.read_text().splitlines(keepends=True),
            fromfile="a/Lib/_pyio.py",
            tofile="b/src/diaspore/stdlib/_io.py",
        )
    )
    assert (ROOT / "tools/overlays/_pyio_to_io.patch").read_text() == expected


def test_codec_registry_bootstraps_common_encodings(tmp_path):
    source = tmp_path / "codec_registry.py"
    source.write_text(
        "import codecs\n"
        "for name in ('utf-8', 'ascii', 'latin-1'):\n"
        "    codec = codecs.lookup(name)\n"
        "    assert codec.encode('abc')[0] == b'abc'\n"
        "    assert codec.decode(b'abc')[0] == 'abc'\n"
        "print('registered')\n"
    )
    bundle = compile_bundle(str(source), self_contained=True)
    assert {"encodings", "encodings.ascii", "encodings.latin_1", "encodings.utf_8"} <= set(
        bundle["modules"]
    )
    stdout = io.StringIO()
    machine = Machine(bundle, stdout=stdout)
    try:
        assert machine.run() == 0
    finally:
        machine.close()
    assert stdout.getvalue() == "registered\n"


def test_unicode_tables_are_compact_and_lazy(tmp_path):
    source = tmp_path / "unicode_tables.py"
    source.write_text(
        "import unicodedata as u\n"
        "assert u._properties is None\n"
        "assert u._decompositions is None\n"
        "assert u._compositions is None\n"
        "assert u._names is None\n"
        "assert u._ranges is None\n"
        "assert u.normalize('NFC', 'ascii') == 'ascii'\n"
        "assert u._properties is None\n"
        "assert u._decompositions is None\n"
        "assert u.name('\\uac00') == 'HANGUL SYLLABLE GA'\n"
        "assert u._names is None\n"
        "assert u._ranges is None\n"
        "assert u.category('A') == 'Lu'\n"
        "properties = u._properties\n"
        "assert properties is not None\n"
        "assert u.category('a') == 'Ll'\n"
        "assert u._properties is properties\n"
        "assert u._decompositions is None\n"
        "assert u._names is None\n"
        "assert u.normalize('NFD', '\\u00e9') == 'e\\u0301'\n"
        "assert u._decompositions is not None\n"
        "assert u._compositions is None\n"
        "assert u.normalize('NFC', 'e\\u0301') == '\\u00e9'\n"
        "assert u._compositions is not None\n"
        "assert u._names is None\n"
        "assert u.name('\\u4e00') == 'CJK UNIFIED IDEOGRAPH-4E00'\n"
        "assert u._ranges is not None\n"
        "assert u._names is None\n"
        "assert u.lookup('KEYCAP DIGIT ONE') == '1\\ufe0f\\u20e3'\n"
        "names = u._names\n"
        "assert names is not None\n"
        "assert u.lookup('LF') == '\\n'\n"
        "assert u.name('A') == 'LATIN CAPITAL LETTER A'\n"
        "assert u._names is names\n"
    )
    bundle = compile_bundle(str(source), self_contained=True)
    for module in ("_unicode15", "_unicode15_names"):
        # Each generated table is one IR constant, not a tree of element literals.
        module_ir = bundle["modules"][module]
        pending = [module_ir]
        constants = 0
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                constants += value.get("op") == "const"
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        assert constants < 20
    machine = Machine(bundle)
    try:
        assert machine.run() == 0
    finally:
        machine.close()
