"""Native TypeScript regex errors retain CPython's class and diagnostics."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from diaspore import compile_bundle

from .conftest import TS_DIR, Result, assert_matches, run_cpython

_CONSTRUCTOR_SOURCE = r"""import re


def show(error):
    print(type(error) is re.error, isinstance(error, Exception),
          isinstance(error, ValueError))
    print(repr(error.msg), repr(error.pattern), repr(error.pos),
          repr(error.lineno), repr(error.colno))
    print(repr(error.args), repr(str(error)))


print(re.error.__module__, issubclass(re.error, Exception),
      issubclass(re.error, ValueError))
for args in [('example',), ('example', None, None),
             ('example', 'plain', None), ('example', None, 2),
             ('example', 'plain', 0), ('example', 'a\nb', 2),
             ('example', b'a\nb', 2), ('example', 'a\nb\nc', 4),
             ('example', '\U0001f600\n(', 2),
             ('example', '\ud800\n(', 2), ('example', 'a\nb', 20),
             ('example', 'a\nb', -1)]:
    show(re.error(*args))
show(re.error(msg='keyword', pattern='a\nb', pos=2))
for args, kwargs in [((), {}), (('one', 'two', 0, 4), {}),
                     (('one',), {'msg': 'two'}),
                     (('one',), {'unknown': 2})]:
    try:
        re.error(*args, **kwargs)
    except TypeError:
        print('constructor argument error')
error = re.error('raised', 'a\nb', 2)
try:
    raise error
except re.error as caught:
    print(caught is error)
    show(caught)
"""

_PATTERN_SOURCE = r"""import re


for pattern in ['(unclosed', '\n(unclosed', '(?i:abc', '(?=abc',
                '[abc', '*a', 'a**', r'\q', r'\x1', ')',
                '(?P<x>a)(?P<x>b)', '(?P<>x)', '(?P<x>x)(?P=y)',
                r'(a)\2', 'a{3,2}', '(?<=a+)',
                b'\n(unclosed', '\U0001f600\n(unclosed',
                '\ud800\n(unclosed']:
    try:
        re.compile(pattern)
    except re.error as error:
        print(type(error) is re.error, isinstance(error, ValueError))
        print(repr(error.msg), repr(error.pattern), repr(error.pos),
              repr(error.lineno), repr(error.colno))
        print(repr(error.args), repr(str(error)))
    else:
        raise AssertionError('malformed expression compiled')
for pattern, flags in [('x', re.ASCII | re.UNICODE), (b'x', re.UNICODE)]:
    try:
        re.compile(pattern, flags)
    except ValueError as error:
        print('flag error', isinstance(error, re.error))
"""

_REPLACEMENT_SOURCE = r"""import re


for pattern, replacement, subject in [
    ('(x)', '\\', 'x'), ('(x)', r'\q', 'x'),
    ('(x)', r'\2', 'x'), ('(x)', r'\g<2>', 'x'),
    ('(x)', r'\g<>', 'x'), ('(x)', r'\g<unknown>', 'x'),
    ('(x)', r'\2', 'no match'), (b'(x)', b'\\', b'x'),
    (b'(x)', br'\q', b'x'), (b'(x)', br'\g<2>', b'x'),
]:
    try:
        re.sub(pattern, replacement, subject)
    except re.error as error:
        print(type(error) is re.error)
        print(repr(error.msg), repr(error.pattern), repr(error.pos),
              repr(error.lineno), repr(error.colno))
        print(repr(error.args), repr(str(error)))
    except IndexError as error:
        print('unknown name', repr(error.args))
    else:
        raise AssertionError('malformed replacement accepted')
match = re.match('(x)', 'x')
try:
    match.expand(r'\2')
except re.error as error:
    print('expand', repr(error.msg), repr(error.pattern), error.pos)
"""

_BOUNDARY_CASES = [
    (r"(a)\1", "xaa"),
    (r"(?=look)", "look"),
    (r"(?!neg)", "neg x"),
    (r"(?P<name>x)", "ax"),
    (r"\Astart", "startx"),
    (r"(unclosed", "x"),
]


@pytest.mark.parametrize(
    "source",
    [
        _CONSTRUCTOR_SOURCE,
        _PATTERN_SOURCE,
        _REPLACEMENT_SOURCE,
        *[
            (
                "import re\n"
                "try:\n"
                f"    match = re.search({pattern!r}, {text!r})\n"
                "    print(None if match is None else "
                "(match.group(), match.span(), match.groups(), match.groupdict()))\n"
                "except re.error as error:\n"
                "    print('error', repr(error.msg), repr(error.pos))\n"
            )
            for pattern, text in _BOUNDARY_CASES
        ],
    ],
    ids=[
        "constructor",
        "pattern-diagnostics",
        "replacement-diagnostics",
        "backreference",
        "lookahead",
        "negative-lookahead",
        "named-group",
        "absolute-anchor",
        "unclosed-group",
    ],
)
def test_ts_regex_errors(tmp_path: Path, source: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    entry = tmp_path / "regex_errors.py"
    entry.write_text(source, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = compile_bundle(str(entry), stdlib=False)
    assert "stdlib" not in bundle
    assert list(bundle["modules"]) == ["regex_errors"]
    bundle_path = tmp_path / "regex_errors.dsp.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    process = subprocess.run(
        [
            node,
            "--experimental-strip-types",
            "--no-warnings",
            str(TS_DIR / "src/cli.ts"),
            "run",
            str(bundle_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = process.stderr.strip().splitlines()
    actual = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(actual, expected, "TypeScript native regex errors")
