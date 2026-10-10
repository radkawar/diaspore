"""CPython native type documentation exposed through TypeScript metadata."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from diaspore import compile_bundle

from .conftest import TS_DIR, Result, assert_matches, run_cpython


@pytest.mark.parametrize(
    "source",
    [
        """for cls in [object, int, float, bool, complex, str, bytes, bytearray,
            memoryview, list, tuple, dict, set, frozenset, range, slice, type,
            staticmethod, classmethod, property, type(None), type(NotImplemented),
            type(Ellipsis)]:
    print(cls.__name__, ascii(cls.__doc__))
    print(cls.__dict__['__doc__'] == cls.__doc__)
""",
        """for value in [{}.keys(), {}.values(), {}.items(), iter([]), reversed([]),
              iter(()), iter(''), iter('é'), iter('😀'), iter(b''),
              iter(range(0)), iter(range(2**100)), iter(set()),
              iter({}), iter({}.values()), iter({}.items()),
              reversed({}), reversed({}.values()), reversed({}.items()),
              enumerate([]), map(str, []), filter(None, []), zip([])]:
    cls = type(value)
    print(cls.__name__, ascii(cls.__doc__))
    print(cls.__dict__['__doc__'] == cls.__doc__)
def function():
    'function documentation'
    pass
class Method:
    def method(self):
        'method documentation'
        pass
for value in [function, Method().method, list[int], int | str]:
    cls = type(value)
    print(cls.__name__, ascii(cls.__doc__))
""",
        """class Documented(list):
    'user type documentation'
class Inherited(Documented):
    pass
print(Documented.__doc__, Documented.__dict__['__doc__'])
print(Inherited.__doc__, Inherited.__dict__['__doc__'])
class Attribute:
    @property
    def value(self):
        'property documentation'
        return 1
print(Attribute.value.__doc__)
print(type(Attribute.value).__doc__ == property.__doc__)
""",
    ],
    ids=["builtin-type-docs", "native-type-docs", "user-docs-preserved"],
)
def test_ts_builtin_documentation(tmp_path: Path, source: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    entry = tmp_path / "builtin_docs.py"
    entry.write_text(source, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    path = tmp_path / "builtin_docs.dsp.json"
    path.write_text(json.dumps(compile_bundle(str(entry), stdlib=False)), encoding="utf-8")
    process = subprocess.run(
        [
            node,
            "--experimental-strip-types",
            "--no-warnings",
            str(TS_DIR / "src/cli.ts"),
            "run",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = process.stderr.strip().splitlines()
    actual = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(actual, expected, "TypeScript builtin documentation")
