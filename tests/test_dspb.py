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


_STDLIB_ID = "sha256:" + "0123456789abcdef" * 4
_INVALID_STDLIB_REFERENCES = [
    None, False, 42, [], {}, "", "sha256:", "sha256:" + "0" * 63,
    "sha256:" + "0" * 65, "sha256:" + "A" * 64, "sha256:" + "g" * 64,
    "sha512:" + "0" * 64, "SHA256:" + "0" * 64,
    "sha256:" + "0" * 64 + "\n", "sha256:" + "\u0660" * 64,
]
_INVALID_CLASS_BINDINGS = [None, False, 1, [], {}]


def _minimal_bundle() -> dict:
    return {
        "diaspore": 1,
        "main": "m",
        "modules": {"m": {"name": "m", "file": "m", "body": []}},
    }


def test_legacy_standalone_wire_fixture() -> None:
    fixture = b"DSPB01\x01\x01m\x01\x00\x01\x00\x00\x00\x00"
    bundle = _minimal_bundle()
    assert encode(bundle) == fixture
    assert decode(fixture) == bundle
    assert "stdlib" not in load_bundle_bytes(fixture)


def test_linked_wire_fixture() -> None:
    fixture = (
        b"DSPB02\x02\x01m\x47" + _STDLIB_ID.encode("ascii")
        + b"\x01\x00\x01\x01\x00\x00\x00\x00"
    )
    bundle = _minimal_bundle()
    bundle["stdlib"] = _STDLIB_ID
    assert encode(bundle) == fixture
    assert decode(fixture) == bundle
    assert load_bundle_bytes(fixture) == bundle


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("flags", [0, 1, 2, 3])
def test_class_binding_containers(linked: bool, flags: int) -> None:
    from diaspore.dspb import _OP_IDX, _dec, _enc, _Reader, _Writer

    node = {"op": "class", "name": "__Inner", "body": []}
    if flags & 1:
        node["kwargs"] = [{"n": "metaclass", "v": {"op": "name", "id": "type"}}]
    if flags & 2:
        node["binding"] = "_Outer__Inner"
    writer = _Writer()
    _enc(writer, node)
    raw = writer.buf.getvalue()
    assert raw[:4] == bytes([_OP_IDX["class"], 0, 0, flags])
    if flags & 2:
        assert raw[4] == 1
        assert writer.strlist[1] == "_Outer__Inner"
    if flags == 0:
        assert raw == bytes([_OP_IDX["class"], 0, 0, 0, 0, 0])
    reader = _Reader(raw, 0)
    reader.strings = writer.strlist
    assert _dec(reader) == node
    assert reader.pos == len(raw)
    bundle = _minimal_bundle()
    bundle["modules"]["m"]["body"] = [node]
    if linked:
        bundle["stdlib"] = _STDLIB_ID
    for data in (encode(bundle), json.dumps(bundle).encode("utf-8")):
        restored = load_bundle_bytes(data)
        assert restored == bundle
        validate_bundle(restored)
        assert bundle_digest(restored) == bundle_digest(bundle)


@pytest.mark.parametrize("binding", _INVALID_CLASS_BINDINGS)
def test_invalid_class_binding_rejected(binding) -> None:
    from diaspore import BundleValidationError
    from diaspore.dspb import DSPBError

    bundle = _minimal_bundle()
    bundle["modules"]["m"]["body"] = [
        {"op": "class", "name": "__Inner", "binding": binding, "body": []},
    ]
    with pytest.raises(BundleValidationError, match="binding"):
        validate_bundle(bundle)
    with pytest.raises(DSPBError, match="binding"):
        encode(bundle)


@pytest.mark.parametrize("payload", [b"\x04", b"\x02", b"\x02\x01"])
def test_invalid_class_binding_wire_rejected(payload: bytes) -> None:
    from diaspore.dspb import _OP_IDX, DSPBError, _dec, _Reader

    reader = _Reader(bytes([_OP_IDX["class"], 0, 0]) + payload, 0)
    reader.strings = ["__Inner"]
    with pytest.raises(DSPBError):
        _dec(reader)


def _metadata_bundle(linked: bool) -> dict:
    bundle = _minimal_bundle()
    bundle["modules"]["m"]["body"] = [{
        "op": "func",
        "name": "annotation",
        "annotation_scope": True,
        "params": {},
        "body": [
            {
                "op": "expr",
                "v": {"op": "const", "t": "str", "v": "\U000103ff",
                      "codepoints": [0xD800, 0xDFFF]},
            },
            {
                "op": "expr",
                "v": {"op": "fstring", "parts": [
                    {"s": "\ud800", "codepoints": [0xD800]},
                ]},
            },
        ],
    }]
    if linked:
        bundle["stdlib"] = _STDLIB_ID
    return bundle


@pytest.mark.parametrize("linked", [False, True])
def test_stdlib_metadata_containers(linked: bool) -> None:
    bundle = _metadata_bundle(linked)
    binary = encode(bundle)
    assert binary[:6] == (b"DSPB02" if linked else b"DSPB01")
    raw_json = json.dumps(bundle).encode("utf-8")
    for raw in (binary, raw_json, gzip.compress(binary), gzip.compress(raw_json)):
        restored = load_bundle_bytes(raw)
        assert restored == bundle
        assert bundle_digest(restored) == bundle_digest(bundle)
        validate_bundle(restored)
    other = dict(bundle, stdlib="sha256:" + "f" * 64)
    assert bundle_digest(other) != bundle_digest(bundle)


@pytest.mark.parametrize("reference", _INVALID_STDLIB_REFERENCES)
def test_invalid_stdlib_reference_rejected(reference) -> None:
    from diaspore import BundleValidationError
    from diaspore.dspb import DSPBError

    bundle = dict(_minimal_bundle(), stdlib=reference)
    restored = load_bundle_bytes(json.dumps(bundle).encode("utf-8"))
    with pytest.raises(BundleValidationError, match="stdlib"):
        validate_bundle(restored)
    with pytest.raises(DSPBError, match="stdlib"):
        encode(bundle)


@pytest.mark.parametrize("reference", ["", "sha256:" + "G" * 64, _STDLIB_ID + "\n"])
def test_invalid_linked_wire_reference(reference: str) -> None:
    from diaspore.dspb import DSPBError

    raw = reference.encode("ascii")
    fixture = (
        b"DSPB02\x02\x01m" + bytes([len(raw)]) + raw
        + b"\x01\x00\x01\x01\x00\x00\x00\x00"
    )
    for data in (fixture, gzip.compress(fixture)):
        with pytest.raises(DSPBError, match="stdlib"):
            load_bundle_bytes(data)


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("trailing", [b"\x00", b"STDL\x00", b"DSPB02"])
def test_trailing_payload_rejected(linked: bool, trailing: bytes) -> None:
    from diaspore.dspb import DSPBError

    bundle = _minimal_bundle()
    if linked:
        bundle["stdlib"] = _STDLIB_ID
    with pytest.raises(DSPBError, match="trailing"):
        load_bundle_bytes(encode(bundle) + trailing)


def test_linked_reference_missing_or_invalid_index() -> None:
    from diaspore.dspb import DSPBError

    header = b"DSPB02\x01\x01m\x01\x00"
    for suffix in (b"", b"\x01", b"\x80", b"\x00"):
        with pytest.raises(DSPBError):
            load_bundle_bytes(header + suffix)


class TestTypedDSPB:
    """Cross-check the optional host schema without requiring it in runtimes."""

    pydantic = pytest.importorskip("pydantic")

    @pytest.mark.parametrize("linked", [False, True])
    def test_stdlib_metadata(self, linked: bool) -> None:
        from diaspore.ir import validate_bundle as typed_validate

        bundle = _metadata_bundle(linked)
        binary = encode(bundle)
        raw_json = json.dumps(bundle).encode("utf-8")
        for raw in (binary, raw_json, gzip.compress(binary), gzip.compress(raw_json)):
            restored = load_bundle_bytes(raw)
            model = typed_validate(restored)
            assert model.model_dump(by_alias=True, exclude_unset=True) == bundle
            assert ("stdlib" in model.model_dump()) == linked

    def test_stdlib_schema_optional_nonnullable(self) -> None:
        from diaspore.ir import bundle_schema

        schema = bundle_schema()
        field = schema["properties"]["stdlib"]
        assert "stdlib" not in schema["required"]
        assert field["type"] == "string"
        assert field["minLength"] == field["maxLength"] == 71
        assert field["pattern"] == r"^sha256:[0-9a-f]{64}$"
        assert "default" not in field

    @pytest.mark.parametrize("reference", _INVALID_STDLIB_REFERENCES)
    def test_invalid_stdlib_reference(self, reference) -> None:
        from diaspore.ir import validate_bundle as typed_validate

        bundle = dict(_minimal_bundle(), stdlib=reference)
        restored = load_bundle_bytes(json.dumps(bundle).encode("utf-8"))
        with pytest.raises(self.pydantic.ValidationError, match="stdlib"):
            typed_validate(restored)

    @pytest.mark.parametrize("linked", [False, True])
    @pytest.mark.parametrize("flags", [0, 1, 2, 3])
    def test_class_binding(self, linked: bool, flags: int) -> None:
        from diaspore.ir import validate_bundle as typed_validate

        node = {"op": "class", "name": "__Inner", "body": []}
        if flags & 1:
            node["kwargs"] = [{"n": "metaclass", "v": {"op": "name", "id": "type"}}]
        if flags & 2:
            node["binding"] = "_Outer__Inner"
        bundle = _minimal_bundle()
        bundle["modules"]["m"]["body"] = [node]
        if linked:
            bundle["stdlib"] = _STDLIB_ID
        for data in (encode(bundle), json.dumps(bundle).encode("utf-8")):
            restored = load_bundle_bytes(data)
            assert typed_validate(restored).model_dump(exclude_unset=True) == bundle

    @pytest.mark.parametrize("binding", _INVALID_CLASS_BINDINGS)
    def test_invalid_class_binding(self, binding) -> None:
        from diaspore.ir import validate_bundle as typed_validate

        bundle = _minimal_bundle()
        bundle["modules"]["m"]["body"] = [
            {"op": "class", "name": "__Inner", "binding": binding, "body": []},
        ]
        with pytest.raises(self.pydantic.ValidationError, match="binding"):
            typed_validate(bundle)


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


@pytest.mark.parametrize("count", [0, 1, 2])
def test_positional_only_count_roundtrips(tmp_path: Path, count: int) -> None:
    params = ("a, b", "a, /, b", "a, b, /")[count]
    entry = tmp_path / "params.py"
    entry.write_text(f"def f({params}):\n    return a, b\n", encoding="utf-8")
    bundle = compile_bundle(str(entry))
    decoded = decode(encode(bundle))
    assert decoded == bundle
    assert decoded["modules"]["params"]["body"][0]["params"].get("posonly", 0) == count


def test_positional_only_flag_precedes_args() -> None:
    from diaspore.dspb import _OP_IDX, DSPBError, _dec, _enc, _Reader, _Writer

    node = {
        "op": "func",
        "name": "f",
        "params": {
            "args": ["x"],
            "posonly": 1,
            "vararg": "rest",
            "kwarg": "kwargs",
        },
        "body": [],
    }
    writer = _Writer()
    _enc(writer, node)
    raw = writer.buf.getvalue()
    assert raw[:6] == bytes([_OP_IDX["func"], 0, 0, 0, 7, 1])
    reader = _Reader(raw, 0)
    reader.strings = writer.strlist
    assert _dec(reader) == node
    malformed = bytearray(raw)
    malformed[5] = 2
    reader = _Reader(bytes(malformed), 0)
    reader.strings = writer.strlist
    with pytest.raises(DSPBError, match="positional-only"):
        _dec(reader)


@pytest.mark.parametrize("count", [-1, 2, 1.5, True, "1", None])
def test_invalid_positional_only_count_rejected(count) -> None:
    from diaspore.dspb import DSPBError, _enc, _Writer

    node = {
        "op": "func",
        "name": "f",
        "params": {
            "args": ["x"],
            "posonly": count,
        },
        "body": [],
    }
    with pytest.raises(DSPBError, match="positional-only"):
        _enc(_Writer(), node)


@pytest.mark.parametrize("flags", [0, 1, 3, 7])
def test_function_kind_flags_roundtrip(flags: int) -> None:
    from diaspore.dspb import _OP_IDX, _dec, _enc, _Reader, _Writer

    node = {"op": "func", "name": "f", "params": {}, "body": []}
    for mask, field in ((1, "gen"), (2, "coro"), (4, "asyncgen")):
        if flags & mask:
            node[field] = True
    writer = _Writer()
    _enc(writer, node)
    raw = writer.buf.getvalue()
    assert raw[:5] == bytes([_OP_IDX["func"], 0, 0, flags, 0])
    reader = _Reader(raw, 0)
    reader.strings = writer.strlist
    assert _dec(reader) == node


def test_sniffing_all_containers(tmp_path: Path) -> None:
    bundle = compile_bundle(str(Path("tests/corpus/smoke.py")))
    raw_json = json.dumps(bundle).encode()
    binary = encode(bundle)
    assert load_bundle_bytes(raw_json) == bundle
    assert load_bundle_bytes(binary) == bundle
    assert load_bundle_bytes(gzip.compress(raw_json)) == bundle
    assert load_bundle_bytes(gzip.compress(binary)) == bundle  # .dspb.gz


@pytest.mark.parametrize(
    "value", ["\ud800", "\udfff", "\ud800x\udfff", "\ud800\udc00", "\u00e9\U0001f600"]
)
def test_string_table_preserves_surrogates(tmp_path: Path, value: str) -> None:
    entry = tmp_path / "unicode.py"
    entry.write_text(f"value = {value!r}\n", encoding="utf-8")
    bundle = compile_bundle(str(entry))
    binary = encode(bundle)
    assert value.encode("utf-8", errors="surrogatepass") in binary
    assert decode(binary) == bundle
    assert load_bundle_bytes(gzip.compress(binary)) == bundle


@pytest.mark.parametrize("value", [
    "", "ASCII", "\U000103ff", "\U0001f600", "\ud800", "\udfff",
    "\ud800\udfff", "\ud800x\udfff", "\udfff\ud800", "a\ud800\udfff\U000103ffz",
])
def test_literal_codepoints_containers(tmp_path: Path, value: str) -> None:
    import io

    from diaspore.compiler import dumps

    entry = tmp_path / "literal.py"
    entry.write_text(
        f"value = {ascii(value)}\n"
        f"text = f{ascii(value + '{1}')}\n"
        "print([ord(c) for c in value], [ord(c) for c in text])\n",
        encoding="utf-8",
    )
    bundle = compile_bundle(str(entry))
    has_surrogate = any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    constant = bundle["modules"]["literal"]["body"][0]["v"]
    assert ("codepoints" in constant) == has_surrogate
    if has_surrogate:
        assert constant["codepoints"] == list(map(ord, value))
    else:
        assert constant == {"op": "const", "t": "str", "v": value}
    expected = f"{list(map(ord, value))} {list(map(ord, value + '1'))}\n"
    json_bundle = json.loads(dumps(bundle))
    for restored in (bundle, json_bundle, decode(encode(bundle)), decode(encode(json_bundle))):
        assert restored == bundle
        assert bundle_digest(restored) == bundle_digest(bundle)
        validate_bundle(restored)
        out = io.StringIO()
        assert Machine(restored, stdout=out).run() == 0
        assert out.getvalue() == expected


@pytest.mark.parametrize("pretty", [False, True])
def test_json_containers_preserve_lone_surrogates(tmp_path: Path, pretty: bool) -> None:
    from diaspore.compiler import dumps
    from diaspore.encoding import write_bundle_file

    entry = tmp_path / "unicode.py"
    value = "\ud800x\udfff \u00e9\U0001f600"
    entry.write_text(f"value = {value!r}\n", encoding="utf-8")
    bundle = compile_bundle(str(entry))
    text = dumps(bundle, pretty=pretty)
    assert text.isascii()
    assert json.loads(text) == bundle
    for extension in (".json", ".json.gz"):
        path = tmp_path / ("unicode" + extension)
        write_bundle_file(bundle, path, pretty=pretty)
        raw = path.read_bytes()
        assert load_bundle_bytes(raw) == bundle
        if extension.endswith(".gz"):
            raw = gzip.decompress(raw)
        assert raw.isascii()


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

    @pytest.mark.parametrize(
        "raw",
        [
            b"\x80",
            b"\xc0\x80",
            b"\xe0\x80\x80",
            b"\xed\xa0",
            b"\xf0\x80\x80\x80",
            b"\xf4\x90\x80\x80",
            b"\xf5\x80\x80\x80",
        ],
    )
    def test_invalid_string_table_utf8_rejected(self, raw: bytes) -> None:
        from diaspore.dspb import MAGIC, DSPBError

        blob = MAGIC + b"\x01" + bytes([len(raw)]) + raw
        with pytest.raises(DSPBError, match="invalid UTF-8"):
            decode(blob)

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
        w.u(0)  # empty string table
        payload = w.buf.getvalue()
        w2 = _Writer()
        w2.u(1)  # semver
        w2.u(0)  # invalid main string reference
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
        body += bytes([_OP_IDX["const"], 0, 5])  # none const
        head = _Writer()
        head.u(1)  # strtab: 1 entry
        head.u(1)
        head.buf.write(b"m")
        head.u(1)  # semver
        head.u(0)  # main -> "m"
        head.u(1)  # module count
        head.u(0)  # name
        head.u(0)  # file
        head.byte(0)  # no src
        head.u(1)  # body: 1 stmt... (expr wrapper omitted; decoder sees unary)
        blob = MAGIC + head.buf.getvalue() + bytes(body)
        with pytest.raises(DSPBError):
            decode(blob)

    @pytest.mark.parametrize("linked", [False, True])
    @pytest.mark.parametrize("recursion_limit", [256, 1000, 40000])
    @pytest.mark.parametrize("op", ["unary", "list"])
    def test_depth_budget_tracks_host_stack(
        self, linked: bool, recursion_limit: int, op: str,
    ) -> None:
        import sys

        from diaspore.dspb import _MAX_DEPTH, _OP_IDX, DSPBError, _Reader

        bundle = _minimal_bundle()
        if linked:
            bundle["stdlib"] = _STDLIB_ID
        # Replace the empty module body with one nested expression.
        header = encode(bundle)[:-1] + b"\x01"
        child = bytes([_OP_IDX[op], 0, 0 if op == "unary" else 1])
        leaf = bytes([_OP_IDX["const"], 0, 5])
        previous_limit = sys.getrecursionlimit()
        try:
            sys.setrecursionlimit(recursion_limit)
            reader = _Reader(b"", 0)
            assert 0 < reader.max_depth <= _MAX_DEPTH
            if sys.getrecursionlimit() >= 40000:
                assert reader.max_depth == _MAX_DEPTH
            depth = min(reader.max_depth // 2, 100)
            restored = decode(header + child * depth + leaf)
            node = restored["modules"]["m"]["body"][0]
            for _ in range(depth):
                assert node["op"] == op
                node = node["v"] if op == "unary" else node["elts"][0]
            assert node == {"op": "const", "t": "none"}
            with pytest.raises(DSPBError, match="node nesting too deep"):
                decode(header + child * (_MAX_DEPTH + 1) + leaf)
        finally:
            sys.setrecursionlimit(previous_limit)

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
        for tail in [
            bytes([250, 0]),  # bad opcode
            bytes([0, 0, 99]),  # const with bad kind byte
            bytes([7, 0, 200, 0, 0, 5]),
        ]:  # binop with bad kind byte
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
    print("\n" + "\n".join(f"{n}: json {r:,} -> dspb {b:,} ({x:.2f}x)" for n, r, b, x in sizes))
