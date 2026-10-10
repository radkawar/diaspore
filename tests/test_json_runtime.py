"""Compare the Python JSON accelerator bridge against CPython."""

from pathlib import Path

import pytest

from .conftest import assert_matches, run_cpython, run_diaspore_py


@pytest.mark.parametrize("source", [
    '''import json
import _json
print(json.scanner.make_scanner is _json.make_scanner)
print(json.encoder.c_make_encoder is _json.make_encoder)
for value in [None, True, False, 0, -12, 1.25, float('inf'), float('nan'),
              'a\\n\\t\\"\\\\é😀\\ud800', [1, {'key': [2, 3]}]]:
    for ascii_only in [False, True]:
        text = json.dumps(value, ensure_ascii=ascii_only)
        print(ascii(text), ascii(json.loads(text)))
''',
    '''import json
print(json.loads('{"x": 1, "x": 2, "n": 1.25, "c": NaN}',
                 parse_int=lambda value: 'int:' + value,
                 parse_float=lambda value: 'float:' + value,
                 parse_constant=lambda value: 'constant:' + value,
                 object_pairs_hook=lambda pairs: ('pairs', pairs)))
print(json.loads('{"x": {"y": 2}}', object_hook=lambda value: sorted(value.items())))
print(json.loads('"a\\u0001b"', strict=False))
for source in ['{', '[1,]', '{"x":}', '"\\\\q"', '"abc', '"\\u0001"', 'true x']:
    try:
        json.loads(source)
    except json.JSONDecodeError as error:
        print(type(error).__name__, error.msg, error.pos, error.lineno, error.colno)
        print(error.doc == source, str(error))
''',
    '''import json
class Value:
    pass
value = Value()
print(json.dumps({'b': [value], 'a': 1}, default=lambda obj: {'custom': True},
                 sort_keys=True, separators=(',', ':')))
for options in [{}, {'sort_keys': True}, {'skipkeys': True}, {'allow_nan': False}]:
    for value in [{False: 'f', None: 'n', 2: 'i'}, {(): 'bad'}, {Value(): 'bad'}, float('nan')]:
        try:
            print(json.dumps(value, **options))
        except (ValueError, TypeError) as error:
            print(type(error).__name__, str(error))
loop = []
loop.append(loop)
try:
    json.dumps(loop)
except ValueError as error:
    print(str(error))
''',
    '''import _json
import json
for text, end, strict in [('"x"', 1, True), ('"é😀"', 1, True),
                          ('"\\\\ud800"', 1, True), ('"x', 1, True),
                          ('"\\u0001"', 1, False), ('"\\u0001"', 1, True)]:
    try:
        print(ascii(_json.scanstring(text, end, strict)))
    except json.JSONDecodeError as error:
        print(error.msg, error.pos, error.lineno, error.colno)
for function in [_json.scanstring, _json.encode_basestring,
                 _json.make_scanner, _json.make_encoder]:
    for args, kwargs in [((), {}), ((123,), {}), ((), {'s': 'x'})]:
        try:
            print(function(*args, **kwargs))
        except (TypeError, AttributeError) as error:
            print(type(error).__name__, str(error))
''',
    '''import json
class Integer(int):
    pass
class Text(str):
    pass
class Mapping(dict):
    pass
class Items(list):
    pass
for value in [Integer(3), Text('abc'), {Integer(3): 1}, {Text('x'): 2},
              Items([1, 2]), Mapping(x=1)]:
    print(json.dumps(value))
class Reverse(int):
    def __lt__(self, other):
        return int(self) > int(other)
print(json.dumps({Reverse(1): 'one', Reverse(2): 'two'}, sort_keys=True))
''',
], ids=["primitive-roundtrip", "decoder-hooks-errors", "encoder-options",
        "private-surface", "primitive-subclasses"])
def test_json_accelerator_matches_cpython(tmp_path: Path, source: str) -> None:
    entry = tmp_path / "json_case.py"
    entry.write_text(source, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert_matches(run_diaspore_py(entry), expected, "Python JSON accelerator")
