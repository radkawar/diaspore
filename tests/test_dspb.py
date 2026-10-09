"""DSPB binary container: exact round-trip, executability, size."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from diaspore import Machine, bundle_digest, compile_bundle, validate_bundle
from diaspore.dspb import decode, encode
from diaspore.encoding import load_bundle_bytes

from .conftest import corpus_entries


def _id(p: Path) -> str:
    return p.parent.name if p.name == "main.py" else p.stem


@pytest.mark.parametrize("entry", corpus_entries(), ids=_id)
def test_roundtrip_exact(entry: Path) -> None:
    bundle = compile_bundle(str(entry))
    again = decode(encode(bundle))
    assert again == bundle
    assert bundle_digest(again) == bundle_digest(bundle)
    validate_bundle(again)


def test_debug_src_roundtrips(tmp_path: Path) -> None:
    entry = tmp_path / "p.py"
    entry.write_text("def f():\n    return 1 / 0\nf()\n", encoding="utf-8")
    bundle = compile_bundle(str(entry), debug=True)
    assert decode(encode(bundle)) == bundle


def test_sniffing_all_containers(tmp_path: Path) -> None:
    bundle = compile_bundle(str(Path("tests/corpus/smoke.py")))
    raw_json = json.dumps(bundle).encode()
    binary = encode(bundle)
    assert load_bundle_bytes(raw_json) == bundle
    assert load_bundle_bytes(binary) == bundle
    assert load_bundle_bytes(gzip.compress(raw_json)) == bundle
    assert load_bundle_bytes(gzip.compress(binary)) == bundle  # .dspb.gz


def test_runs_identically(tmp_path: Path) -> None:
    import io

    bundle = compile_bundle(str(Path("tests/corpus/32_match.py")))
    out1, out2 = io.StringIO(), io.StringIO()
    assert Machine(bundle, stdout=out1).run() == 0
    assert Machine(decode(encode(bundle)), stdout=out2).run() == 0
    assert out1.getvalue() == out2.getvalue()


class TestHostileDSPB:
    """Reject malformed containers without unbounded allocation or recursion."""

    def _enc_smoke(self) -> bytes:
        return encode(compile_bundle("tests/corpus/smoke.py"))

    def test_truncations_never_crash(self) -> None:
        from diaspore.dspb import DSPBError
        data = self._enc_smoke()
        for cut in list(range(0, 40)) + [len(data) // 2, len(data) - 1]:
            with pytest.raises(DSPBError):
                decode(data[:cut])

    def test_bitflips_fail_cleanly(self) -> None:
        from diaspore.dspb import DSPBError
        data = bytearray(self._enc_smoke())
        for pos in range(6, min(len(data), 220)):
            corrupted = bytearray(data)
            corrupted[pos] ^= 0xFF
            try:
                decode(bytes(corrupted))
            except DSPBError:
                pass  # A mutation may still encode a valid bundle.

    def test_huge_count_rejected_before_allocation(self) -> None:
        from diaspore.dspb import MAGIC, DSPBError, _Writer
        w = _Writer()
        w.u(0)          # empty string table
        payload = w.buf.getvalue()
        w2 = _Writer()
        w2.u(1)         # semver
        w2.u(0)         # invalid main string reference
        evil = _Writer()
        evil.u(1 << 60)  # exceeds the module count limit
        blob = MAGIC + payload + w2.buf.getvalue() + evil.buf.getvalue()
        with pytest.raises(DSPBError):
            decode(blob)

    def test_deep_nesting_bounded(self) -> None:
        # unary(unary(unary(... const))) nested 50k deep, hand-encoded
        from diaspore.dspb import _OP_IDX, MAGIC, DSPBError, _Writer
        body = bytearray()
        for _ in range(50_000):
            body += bytes([_OP_IDX["unary"], 0, 0])  # op, flags, kind=neg
        body += bytes([_OP_IDX["const"], 0, 5])      # none const
        head = _Writer()
        head.u(1)  # strtab: 1 entry
        head.u(1)
        head.buf.write(b"m")
        head.u(1)      # semver
        head.u(0)      # main -> "m"
        head.u(1)      # module count
        head.u(0)      # name
        head.u(0)      # file
        head.byte(0)   # no src
        head.u(1)      # body: 1 stmt... (expr wrapper omitted; decoder sees unary)
        blob = MAGIC + head.buf.getvalue() + bytes(body)
        with pytest.raises(DSPBError):
            decode(blob)

    def test_bad_opcode_and_enums(self) -> None:
        from diaspore.dspb import MAGIC, DSPBError, _Writer
        head = _Writer()
        head.u(1)
        head.u(1)
        head.buf.write(b"m")
        head.u(1)
        head.u(0)
        head.u(1)
        head.u(0)
        head.u(0)
        head.byte(0)
        head.u(1)
        for tail in [bytes([250, 0]),               # bad opcode
                     bytes([0, 0, 99]),             # const with bad kind byte
                     bytes([7, 0, 200, 0, 0, 5])]:  # binop with bad kind byte
            with pytest.raises(DSPBError):
                decode(MAGIC + head.buf.getvalue() + tail)


def test_meaningfully_smaller() -> None:
    sizes = []
    for entry in [Path("examples/http/server.py"), Path("selfhost/dspc.py")]:
        bundle = compile_bundle(str(entry))
        raw = len(json.dumps(bundle, separators=(",", ":")).encode())
        binary = len(encode(bundle))
        sizes.append((entry.name, raw, binary, raw / binary))
        assert binary < raw / 2.5, f"{entry}: only {raw / binary:.2f}x"
    print("\n" + "\n".join(f"{n}: json {r:,} -> dspb {b:,} ({x:.2f}x)"
                           for n, r, b, x in sizes))
