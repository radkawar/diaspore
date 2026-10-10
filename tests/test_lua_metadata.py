"""Lua reports unavailable host metadata without synthesizing stat fields."""

from pathlib import Path

from .conftest import assert_matches, run_cpython, run_diaspore_lua


def test_lua_fstat_enosys_and_bad_descriptor(tmp_path: Path, lua_vm: list[str]) -> None:
    data = tmp_path / "data.bin"
    data.write_bytes(b"data")
    entry = tmp_path / "descriptor_metadata.py"
    entry.write_text(
        "# diaspore-caps: fs\n"
        "import _os\n"
        f"descriptor = _os.open({str(data)!r}, 0)\n"
        "try:\n"
        "    _os.fstat(descriptor)\n"
        "except OSError as error:\n"
        "    assert error.errno == 38\n"
        "    assert error.strerror == 'diaspore-lua cannot read file metadata'\n"
        "    assert error.args == (38, 'diaspore-lua cannot read file metadata')\n"
        "else:\n"
        "    raise AssertionError('metadata must not be synthesized')\n"
        "_os.close(descriptor)\n"
        "try:\n"
        "    _os.fstat(descriptor)\n"
        "except OSError as error:\n"
        "    assert error.errno == 9\n"
        "else:\n"
        "    raise AssertionError('closed descriptor accepted')\n",
        encoding="utf-8",
    )
    result = run_diaspore_lua(entry, lua_vm, tmp_path)
    assert result.code == 0, result.err_line
    assert result.stdout == ""


def test_lua_isatty_descriptor_protocol(tmp_path: Path, lua_vm: list[str]) -> None:
    data = tmp_path / "terminal_query.bin"
    data.write_bytes(b"data")
    entry = tmp_path / "terminal_query.py"
    entry.write_text(
        "# diaspore-caps: fs\n"
        "import os\n"
        f"descriptor = os.open({str(data)!r}, os.O_RDONLY)\n"
        "print(os.isatty(descriptor), os.read(descriptor, 4))\n"
        "os.close(descriptor)\n"
        "print(os.isatty(descriptor), os.isatty(-1), os.isatty(999999))\n"
        "class Index:\n"
        "    def __index__(self):\n"
        "        return -1\n"
        "print(os.isatty(Index()))\n"
        "for value in ['bad', 1.5, 2 ** 100]:\n"
        "    try:\n"
        "        os.isatty(value)\n"
        "    except (TypeError, OverflowError) as error:\n"
        "        print(type(error).__name__, str(error))\n",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_lua(entry, lua_vm, tmp_path), expected, "Lua terminal queries")
