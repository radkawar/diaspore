"""Lua preserves class metadata separately from lexical IR bindings."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from diaspore.compiler import compile_bundle
from diaspore.validator import validate_bundle

from .conftest import LUA_DIR, ROOT, run_cpython


@pytest.fixture
def lua_binary() -> str:
    binary = shutil.which("lua5.4") or shutil.which("lua")
    if binary is None:
        pytest.skip("Lua 5.4 not installed")
    return binary


def run_bundle(lua_binary: str, bundle: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    path = tmp_path / "class.dsp.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return subprocess.run(
        [lua_binary, str(LUA_DIR / "cli.lua"), "run", str(path)],
        capture_output=True,
        text=True,
        input="",
        cwd=ROOT,
        timeout=30,
    )


def test_lua_private_class_binding(lua_binary: str, tmp_path: Path) -> None:
    entry = tmp_path / "private_classes.py"
    entry.write_text(
        """
events = []
class Meta(type):
    @classmethod
    def __prepare__(meta, name, bases):
        events.append(('prepare', name))
        return {}
    def __new__(meta, name, bases, namespace):
        events.append(('new', name))
        return super().__new__(meta, name, bases, namespace)

def decorate(cls):
    events.append(('decorate', cls.__name__))
    return cls

class Outer:
    class __Inner(metaclass=Meta):
        __marker = 17
        def value(self):
            return self.__marker
    @decorate
    class __Decorated:
        pass
    def classes(self):
        return self.__Inner, self.__Decorated

inner, decorated = Outer().classes()
print(inner is Outer._Outer__Inner, decorated is Outer._Outer__Decorated)
print(inner.__name__, decorated.__name__, inner().value())
print('_Outer__Inner' in vars(Outer), '__Inner' in vars(Outer))
print(events)
class __Standalone:
    pass
print(__Standalone.__name__)
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = compile_bundle(str(entry), stdlib=False)
    validate_bundle(bundle)
    result = run_bundle(lua_binary, bundle, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected.stdout


@pytest.mark.parametrize("binding", [None, "_Outer__Inner"])
def test_lua_raw_class_binding(lua_binary: str, tmp_path: Path, binding: str | None) -> None:
    entry = tmp_path / "raw_class.py"
    entry.write_text("class Original:\n    pass\nprint(Original.__name__)\n", encoding="utf-8")
    bundle = compile_bundle(str(entry), stdlib=False)
    body = bundle["modules"][bundle["main"]]["body"]
    class_node = body[0]
    class_node["name"] = "__Inner"
    if binding is not None:
        class_node["binding"] = binding
    body[1]["v"]["args"][0]["o"]["id"] = binding or "__Inner"
    validate_bundle(bundle)
    result = run_bundle(lua_binary, bundle, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "__Inner\n"


@pytest.mark.parametrize("binding", [None, 0, False, [], {}])
def test_lua_rejects_malformed_class_binding(lua_binary: str, tmp_path: Path, binding) -> None:
    bundle = {
        "diaspore": 1,
        "main": "main",
        "modules": {
            "main": {
                "name": "main",
                "file": "main.py",
                "body": [{"op": "class", "name": "Original", "binding": binding, "body": []}],
            }
        },
    }
    result = run_bundle(lua_binary, bundle, tmp_path)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.rstrip().endswith("TypeError: class binding must be a string")
