"""The portable Lua compiler preserves link mode and supports in-memory compilation."""

import json
import subprocess
from pathlib import Path

import pytest

from .conftest import run_cpython


@pytest.mark.parametrize("self_contained", [False, True])
def test_lua_portable_and_in_memory_compiler(
    tmp_path: Path, lua_vm: list[str], self_contained: bool
) -> None:
    entry = tmp_path / "compiler_probe.py"
    entry.write_text(
        "import json\n"
        "namespace = {}\n"
        "exec(compile('def successor(value):\\n    return value + 1\\n', "
        "'<memory>', 'exec'), namespace)\n"
        "print(namespace['successor'](41), eval(compile('6 * 7', '<expression>', 'eval')))\n"
        "print(json.dumps({'native_json': True, 'value': namespace['successor'](41)}, "
        "sort_keys=True))\n",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    target = tmp_path / "program.json"
    command = [*lua_vm, "build", str(entry), "-o", str(target)]
    if self_contained:
        command.append("--self-contained")
    built = subprocess.run(command, capture_output=True, text=True, timeout=180)
    assert built.returncode == 0, built.stderr
    bundle = json.loads(target.read_text(encoding="utf-8"))
    assert ("stdlib" not in bundle) is self_contained
    assert ("json" in bundle["modules"]) is self_contained
    result = subprocess.run(
        [*lua_vm, "run", str(target)], capture_output=True, text=True, timeout=180
    )
    assert result.returncode == expected.code, result.stderr
    assert result.stdout == expected.stdout
