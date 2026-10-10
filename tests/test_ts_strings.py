"""TypeScript Python string boundaries across operations and bundle transports."""

import json
import subprocess
import unicodedata
from pathlib import Path

import pytest

from diaspore.dspb import encode

from .conftest import TS_DIR, Result, assert_matches, build, run_cpython

_SOURCE = r'''
import json
import re
import unicodedata

pair = '\ud800\udfff'
scalar = '\U000103ff'
print(pair == scalar, len(pair), len(scalar))
print([ord(c) for c in pair], [ord(c) for c in scalar])
print(len({pair: 'pair', scalar: 'scalar'}), {pair: 'pair', scalar: 'scalar'}[pair])
print(hash(pair) == hash('\ud800' + '\udfff'))
for value in [pair, scalar, pair + scalar, '\ud800' + '\udfff',
              ''.join(['\ud800', '\udfff']), '\udfff\ud800'[::-1],
              ('\udfff\ud800' * 2)[1:3], f'{pair}:{scalar}', f'\ud800\udfff{scalar}']:
    print([ord(c) for c in value], ascii(value), ascii(value[::-1]))
    for form in ['NFC', 'NFD', 'NFKC', 'NFKD']:
        print(form, [ord(c) for c in unicodedata.normalize(form, value)])
    for ascii_only in [False, True]:
        text = json.dumps(value, ensure_ascii=ascii_only)
        print(ascii(text), [ord(c) for c in json.loads(text)])
print('\ud800' in scalar, '\ud800' in pair)
print(pair.find('\udfff'), scalar.find('\udfff'))
print([ord(c) for c in pair.replace('\udfff', '\udffe')])
print([ord(c) for c in re.match('(.*)', pair).group(1)])
try:
    pair.encode('utf-8')
except UnicodeEncodeError as error:
    print(error.start, error.end, [ord(c) for c in error.object])
print(pair.encode('utf-8', 'surrogatepass').hex())
print(scalar.encode('utf-8').hex())
print([ord(c) for c in pair.encode('utf-8', 'surrogatepass').decode('utf-8', 'surrogatepass')])
class Names:
    pass
names = Names()
for name, value in [(pair, 1), (scalar, 2), ('\0pD800,DFFF', 3)]:
    setattr(names, name, value)
    print(getattr(names, name), names.__dict__[name], name in vars(names))
print(len(vars(names)))
print('ordinary text'.upper(), 'abc'[1:], ''.join(['a', 'b']))

pair = '\ud800\udc00'
scalar = '\U00010000'
text = 'x' + pair + scalar + pair + 'y'
for needle in [pair, scalar, '\ud800', '\udc00', '']:
    print(ascii(needle), text.find(needle), text.rfind(needle), text.count(needle))
    print(text.find(needle, 2, 6), text.count(needle, 1, 5))
    print(ascii(text.replace(needle, pair + '!')))
    if needle:
        print(ascii(text.split(needle)), ascii(text.rsplit(needle, 1)))
print(ascii(pair.join([scalar, pair, ''])), ascii(''.join(['\ud800', '\udc00'])))
for value in [pair, scalar, 'a' + pair + 'B', 'AΣ' + pair, 'AΣ' + scalar,
              '\ud801\udc00', '\U00010400']:
    print(ascii(value.lower()), ascii(value.upper()), ascii(value.casefold()))
    print(ascii(value.title()), ascii(value.capitalize()), ascii(value.swapcase()))
for value in [pair, scalar, 'abc' + pair + 'z', scalar + pair, '\ud800x\udc00']:
    encoded = value.encode('utf-8', 'surrogatepass')
    print(encoded.hex(), ascii(encoded.decode('utf-8', 'surrogatepass')))
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        print(error.start, error.end, ascii(error.object), ascii(error.reason))

pair_pattern = re.compile(pair)
scalar_pattern = re.compile(scalar)
print(pair_pattern is re.compile(pair), scalar_pattern is re.compile(scalar))
print(pair_pattern is scalar_pattern)
for pattern in [pair_pattern, scalar_pattern, re.compile(r'\ud800\udc00')]:
    print(pattern.fullmatch(pair) is not None, pattern.fullmatch(scalar) is not None)
    print(ascii(pattern.findall(text)), ascii(pattern.split(text)))
    print(ascii(pattern.sub(pair, text)))
    print(ascii(pattern.sub(lambda match: scalar + match.group(0) + pair, text)))
match = re.match('(' + pair + ')(' + scalar + ')', pair + scalar)
print(ascii(match.groups()), match.span(1), match.span(2))
print(ascii(match.expand(r'\2' + pair + r'\1' + scalar)))
print(ascii(re.sub('(.)', pair + r'\1', scalar)))
for value in [pair, scalar, pair + '[.]' + scalar]:
    escaped = re.escape(value)
    print(ascii(escaped), re.fullmatch(escaped, value) is not None)
'''


@pytest.mark.parametrize("binary", [False, True], ids=["json", "dspb"])
def test_ts_surrogate_boundaries(tmp_path: Path, ts_vm: list[str], binary: bool) -> None:
    entry = tmp_path / "strings.py"
    entry.write_text(_SOURCE, encoding="utf-8")
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    bundle = build(entry)
    path = tmp_path / ("strings.dspb" if binary else "strings.dsp.json")
    path.write_bytes(encode(bundle) if binary else json.dumps(bundle).encode())
    process = subprocess.run(
        [*ts_vm, "run", str(path)], capture_output=True, text=True, timeout=120,
    )
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript string boundaries")


def test_ts_string_value_transport(ts_vm: list[str]) -> None:
    source = r'''
import assert from 'node:assert/strict';
import { chars, strFromChars, strConcat, strJoin, strSlice, strRepeat, strEq,
         strLen, strKey, strFromKey, hashKey, strNormalize } from './src/values.ts';
import { encodeWTF8, decodeWTF8 } from './src/unicode-transport.ts';
import { dspbEncode, dspbDecode } from './src/dspb.ts';
import { bundleDigest, canonicalEscape } from './src/flow.ts';
import { loadBundle } from './src/index.ts';
const pair = strFromChars(['\ud800', '\udfff']);
const scalar = '\u{103ff}';
assert.equal(strLen(pair), 2);
assert.equal(strLen(scalar), 1);
assert.equal(strEq(pair, scalar), false);
assert.notEqual(hashKey(pair), hashKey(scalar));
assert.equal(strEq(pair, strConcat('\ud800', '\udfff')), true);
assert.equal(strEq(pair, strJoin(['\ud800', '\udfff'])), true);
assert.equal(strEq(pair, strSlice(strRepeat('\udfff\ud800', 2), 1, 3)), true);
for (const form of ['NFC', 'NFD', 'NFKC', 'NFKD']) {
  assert.deepEqual(chars(strNormalize(pair, form)), ['\ud800', '\udfff']);
}
assert.deepEqual([...encodeWTF8(pair)], [0xed, 0xa0, 0x80, 0xed, 0xbf, 0xbf]);
assert.deepEqual([...encodeWTF8(scalar)], [0xf0, 0x90, 0x8f, 0xbf]);
for (const value of [pair, scalar, '', 'abc', '\ud800', '\udfff', '\0pD800,DFFF']) {
  assert.equal(strEq(value, decodeWTF8(encodeWTF8(value))), true);
  assert.equal(strEq(value, strFromKey(strKey(value))), true);
}
assert.equal(canonicalEscape(pair), '"\\ud800\\udfff"');
const bundle = {diaspore: 1, main: 'm', modules: {m: {name: 'm', file: 'm.py', body: [
  {op: 'expr', v: {op: 'const', t: 'str', v: scalar, codepoints: [0xd800, 0xdfff]}},
  {op: 'expr', v: {op: 'const', t: 'str', v: scalar}},
  {op: 'expr', v: {op: 'fstring', parts: [{s: scalar, codepoints: [0xd800, 0xdfff]}]}},
]}}};
const json = loadBundle(JSON.stringify(bundle));
const binary = dspbDecode(dspbEncode(json));
assert.equal(bundleDigest(binary), bundleDigest(json));
assert.deepEqual(binary.modules.m.body, json.modules.m.body);
'''
    process = subprocess.run(
        [*ts_vm[:-1], "--input-type=module", "-e", source],
        cwd=TS_DIR, capture_output=True, text=True, timeout=120,
    )
    assert process.returncode == 0, process.stderr


@pytest.mark.skipif(
    unicodedata.unidata_version != "15.0.0", reason="requires Unicode 15.0.0 oracle"
)
def test_ts_printable_matches_pinned_unicode(tmp_path: Path, ts_vm: list[str]) -> None:
    entry = tmp_path / "printable.py"
    entry.write_text(
        r"""
points = (
    0, 9, 10, 13, 31, 32, 33, 39, 92, 126, 127, 128, 160, 173,
    0x301, 0x378, 0x88F, 0x897, 0xC5C, 0xCDC, 0x1ACF, 0x1680,
    0x200B, 0x2028, 0x2029, 0xD800, 0xDFFF, 0xE000, 0xFDD0, 0xFFFF,
    0x10000, 0x1E4D0, 0x1F600, 0x1FAE8, 0x1FAE9, 0x2EBF0, 0x31350,
    0x323AF, 0x323B0, 0x10FFFF,
)
print("empty", "".isprintable(), repr(""))
for point in points:
    value = chr(point)
    print(point, value.isprintable(), repr(value), repr("a" + value + "z"),
          "%r" % value, "{!r}".format(value), f"{value!r}", ascii(value))
pair = chr(0xD800) + chr(0xDC00)
print("surrogates", len(pair), pair.isprintable(), repr(pair))
""",
        encoding="utf-8",
    )
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    path = tmp_path / "printable.dsp.json"
    path.write_text(json.dumps(build(entry)), encoding="utf-8")
    process = subprocess.run(
        [*ts_vm, "run", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = process.stderr.strip().splitlines()
    result = Result(process.stdout, process.returncode, lines[-1] if lines else "")
    assert_matches(result, expected, "TypeScript Unicode 15.0.0 printable and repr")


@pytest.mark.skipif(
    unicodedata.unidata_version != "15.0.0", reason="requires Unicode 15.0.0 oracle"
)
def test_ts_printable_helper_exhaustive(ts_vm: list[str]) -> None:
    source = r"""
import { isPrintableCodePoint } from './src/unicode15.ts';
const ranges = [];
for (let code = 0; code < 0x110000; code++) {
  if (!isPrintableCodePoint(code)) continue;
  const previous = ranges[ranges.length - 1];
  if (previous && previous[1] === code - 1) previous[1] = code;
  else ranges.push([code, code]);
}
console.log(JSON.stringify(ranges));
"""
    process = subprocess.run(
        [*ts_vm[:-1], "--input-type=module", "-e", source],
        cwd=TS_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0, process.stderr
    expected = []
    for code in range(0x110000):
        if chr(code).isprintable():
            if expected and expected[-1][1] == code - 1:
                expected[-1][1] = code
            else:
                expected.append([code, code])
    assert json.loads(process.stdout) == expected
