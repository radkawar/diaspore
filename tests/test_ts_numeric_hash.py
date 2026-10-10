"""TypeScript numeric hashes match CPython's binary64 rational hash contract."""

import json
import math
import random
import struct
import subprocess
from pathlib import Path

from .conftest import TS_DIR


def test_ts_numeric_hash(tmp_path: Path, ts_vm: list[str]) -> None:
    values = [
        0.0,
        -0.0,
        1.0,
        -1.0,
        3.25,
        -3.25,
        0.1,
        -0.1,
        float("inf"),
        float("-inf"),
        float.fromhex("0x1p-1074"),
        float.fromhex("-0x1p-1074"),
        float.fromhex("0x1p-1022"),
        float.fromhex("0x1.fffffffffffffp+1023"),
        float(2**61),
        float(2**62),
        float(2**100),
    ]
    rng = random.Random(59381)
    for _ in range(500):
        value = struct.unpack(">d", rng.getrandbits(64).to_bytes(8, "big"))[0]
        if not math.isnan(value):
            values.append(value)
    pairs = [(value, 0.0) for value in values]
    pairs.extend(zip(values, reversed(values)))
    fixture = tmp_path / "numeric_hash.json"
    fixture.write_text(
        json.dumps(
            {
                "values": [struct.pack(">d", value).hex() for value in values],
                "pairs": [
                    [struct.pack(">d", real).hex(), struct.pack(">d", imag).hex()]
                    for real, imag in pairs
                ],
            }
        ),
        encoding="utf-8",
    )
    script = r"""
import { readFileSync } from "node:fs";
import { Complex, hashValue } from "./src/values.ts";
const fixture = JSON.parse(readFileSync(process.argv[1], "utf8"));
const buffer = new DataView(new ArrayBuffer(8));
function decode(hex) {
  buffer.setBigUint64(0, BigInt("0x" + hex));
  return buffer.getFloat64(0);
}
console.log(JSON.stringify({
  values: fixture.values.map(bits => String(hashValue(decode(bits)))),
  pairs: fixture.pairs.map(([real, imag]) =>
    String(hashValue(new Complex(decode(real), decode(imag))))),
}));
"""
    process = subprocess.run(
        [ts_vm[0], "--input-type=module", "-e", script, str(fixture)],
        cwd=TS_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {
        "values": [str(hash(value)) for value in values],
        "pairs": [str(hash(complex(real, imag))) for real, imag in pairs],
    }
