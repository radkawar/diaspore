"""Lua string representations escape nonprintable Unicode like CPython."""

from pathlib import Path

from .conftest import assert_matches, run_cpython, run_diaspore_lua


def test_lua_unicode_repr(tmp_path: Path, lua_vm: list[str]) -> None:
    points = list(range(256)) + [
        0x378, 0x61C, 0x1680, 0x180E, 0x2000, 0x200B, 0x2028, 0x2029,
        0x2060, 0x3000, 0xD800, 0xDBFF, 0xDC00, 0xDFFF, 0xE000, 0xFEFF,
        0xFFFD, 0xFFFF, 0x1F642, 0x10FFFF,
    ]
    entry = tmp_path / "unicode_repr.py"
    entry.write_text(
        "points = " + repr(points) + "\n"
        "for code in points:\n"
        "    text = chr(code)\n"
        "    print(code, repr(text), ascii(text), repr([text]), repr({'key': text}))\n"
        "    print('%r' % text, '{!r}'.format(text))\n"
        "print(repr('left' + chr(0xD800) + chr(0xDCFF) + 'right'))\n",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua Unicode repr")
