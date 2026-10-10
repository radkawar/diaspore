"""Compare native and portable incremental hashing against CPython."""

import io
from pathlib import Path

import pytest

from diaspore.compiler import compile_bundle
from diaspore.vm import Machine

from .conftest import run_cpython


@pytest.mark.parametrize("accelerated", [False, True], ids=["portable", "native"])
@pytest.mark.parametrize("algorithm", ["md5", "sha1", "sha224", "sha256", "sha384", "sha512"])
def test_incremental_hash_oracle(tmp_path: Path, algorithm: str, accelerated: bool) -> None:
    entry = tmp_path / "hash_probe.py"
    entry.write_text(
        "import hashlib\n"
        f"name = {algorithm!r}\n"
        "class Buffer(bytes):\n"
        "    pass\n"
        "data = bytes(range(256))\n"
        "h = hashlib.new(name, Buffer(data[:71]), usedforsecurity=False)\n"
        "print(h.name, h.digest_size, h.block_size)\n"
        "print(h.hexdigest(), h.digest().hex())\n"
        "fork = h.copy()\n"
        "print(h.update(memoryview(data)[71:129]))\n"
        "print(h.update(bytearray(data[129:])))\n"
        "print(h.hexdigest(), h.digest().hex())\n"
        "print(h.hexdigest() == hashlib.new(name, data).hexdigest())\n"
        "fork.update(b'fork')\n"
        "print(fork.hexdigest(), h.hexdigest())\n"
        "empty = hashlib.new(name)\n"
        "print(empty.hexdigest(), empty.copy().hexdigest())\n"
        "for value in ['text', 1, [1], None, memoryview(data)[::2]]:\n"
        "    try:\n"
        "        h.update(value)\n"
        "    except (TypeError, BufferError) as error:\n"
        "        print(type(error).__name__, str(error))\n"
        "print(h.hexdigest())\n",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    out, err = io.StringIO(), io.StringIO()
    machine = Machine(compile_bundle(str(entry)), stdout=out, stderr=err)
    if not accelerated:
        del machine.interp.natives["_diaspore_hash"]
    assert machine.run() == expected.code == 0, err.getvalue()
    assert out.getvalue() == expected.stdout
    assert err.getvalue() == ""


def test_native_hash_fuel_is_metered(tmp_path: Path) -> None:
    entry = tmp_path / "hash_fuel.py"
    entry.write_text(
        "import _diaspore_hash\n"
        "from host import data\n"
        "engine = _diaspore_hash.new('sha256')\n"
        "engine.update(data)\n",
        encoding="utf-8",
    )
    err = io.StringIO()
    machine = Machine(
        compile_bundle(str(entry), stdlib=False, extra_modules={"host"}),
        bindings={"host": {"data": b"x" * 65536}},
        max_steps=100,
        stderr=err,
    )
    assert machine.run() == 3
    assert "step budget exceeded (100)" in err.getvalue()
