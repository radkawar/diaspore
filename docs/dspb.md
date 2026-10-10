# DSPB: the optional binary bundle container

DSPB encodes the same semantic model as a JSON bundle in a compact binary
container. JSON remains the canonical form and default output. Every DSPB
implementation MUST preserve this invariant:

    decode(encode(bundle)) == compact(bundle)      (exact structural equality)

Digests (`bundle_digest`), validation, and all semantics operate on the
decoded model, so containers never affect meaning. Loaders sniff by magic
bytes; `.dspb.gz` (gzip over DSPB) is valid - gzip decoding re-sniffs its
payload. Reference implementation: `src/diaspore/dspb.py` (normative where this
document is ambiguous). Round-trip conformance: `tests/test_dspb.py`.

## Primitives

- varint - unsigned LEB128 (7 bits per byte, high bit = continuation).
- str-ref - varint index into the string table.
- byte - one raw byte.

## Layout

```
magic     6 bytes  "DSPB01" (standalone) or "DSPB02" (linked)
strtab    varint count, then per string: varint byte-length + UTF-8/surrogatepass bytes
semver    varint   (1 - same value as JSON's "diaspore" field)
main      str-ref
stdlib    str-ref  (DSPB02 only; required)
modules   varint count, then per module:
            name str-ref, file str-ref,
            src-flag byte (0|1), if 1: str-list (varint count + str-refs)
            body node-list
node-list varint count + that many nodes
```

`DSPB01` represents a standalone bundle: the decoded model MUST omit
`stdlib`, and the encoding is unchanged from the standalone format.
`DSPB02` represents a linked bundle: the decoded model MUST include the
`stdlib` string referenced immediately after `main`, before the module count.
There is no presence flag. The semantic version remains `diaspore: 1` in both
containers; the magic selects the container layout, not the language version.
Encoders MUST select `DSPB02` exactly when the model contains `stdlib`.
The reference MUST be `sha256:` followed by exactly 64 lowercase hexadecimal
digits (71 ASCII characters total). Null, empty strings, uppercase digits,
other algorithms, and malformed references MUST be rejected.

Decoders MUST consume the entire payload. Extra bytes after the final module,
including an attempted metadata trailer, MUST be rejected for both versions.
Truncated references and invalid string-table indices MUST be rejected.
Readers that only support `DSPB01` reject the `DSPB02` magic rather than
silently ignoring a linked dependency. Existing standalone fixtures remain
valid and retain their byte-for-byte encoding.

The decoded `stdlib` field participates in the canonical bundle digest just
like every other model field; containers do not add separate digest rules.
Before execution, runtimes MUST compare a linked reference with the shipped
stdlib content id and reject a mismatch. An absent reference means standalone,
not permission to use the shipped stdlib as an implicit import fallback.

The Python decoder bounds nesting to 8000 nodes and further limits recursive
descent to the available host call stack, reserving room to report a
`DSPBError`. Decoding does not change the process recursion limit or require
prior VM initialization.

String-table bytes preserve lone surrogate codepoints using UTF-8
`surrogatepass` (WTF-8); other malformed UTF-8 is rejected. Ordinary Unicode
strings retain their standard UTF-8 representation.
For string constants and literal f-string parts with `codepoints` metadata,
encoders construct the string-table entry from those codepoints, not from the
JSON `v` or `s` field. Decoders regenerate the metadata whenever the entry
contains a surrogate codepoint and emit canonical JSON text in `v` or `s`
(combining adjacent high/low pairs). No extra binary field or flag is needed.

## Nodes

Every node: opcode byte (index into the OPS table below), flags byte
(bit 0 = `ln` present -> varint follows; higher bits are the op's optional
fields in the listed order), then fields in fixed order.

Enum tables (indices are normative):

```
OPS        = const name attr index slicelit starred call binop unary boolop
             compare cond list tuple set dict fstring func funcexpr yield
             yieldfrom namedexpr expr assign augassign if while for break
             continue pass return class raise try with import importfrom del
BIN_KINDS  = add sub mul div floordiv mod pow lshift rshift bitand bitor
             bitxor matmul
CMP_KINDS  = eq ne lt le gt ge in notin is isnot
UNARY      = neg pos not invert
CONST      = int float str bytes bool none ellipsis complex
CONV       = (none) r s a
```

Flag-bit optionals per op (bit 1 upward): `slicelit`: lower, upper, step;
`yield`/`return`: v; `raise`: exc, cause; `import`: alias.

Per-op payloads (after flags/ln). `N` = node, `L` = node-list,
`S` = str-ref, `SL` = varint count + str-refs:

| op | payload |
|---|---|
| const | kind byte; int/float/str/bytes -> S; complex -> S re, S im (float-strings); bool -> byte 0/1; none/ellipsis -> nothing |
| name | S id |
| attr | N o, S n |
| index | N o, N k |
| slicelit | optional N lower/upper/step per flags |
| starred / yieldfrom / expr | N v |
| call | N f, L args, varint kw-count x (byte named? [1 -> S name], N v) |
| binop | kind byte, N l, N r |
| augassign | kind byte, N target, N v |
| unary | kind byte, N v |
| boolop | byte (0=and, 1=or), L values |
| compare | N left, varint count x kind byte, L comps |
| cond | N test, N then, N else |
| list / tuple / set | L elts |
| dict | varint count x (byte has-key? [1 -> N k], N v) |
| fstring | varint count x part: byte flag (0 = text -> S; else bit0=value: N v, conv byte, bit1 -> N spec) |
| func / funcexpr | S name, byte flags (bit0 gen, bit1 coro, bit2 asyncgen, bit3 annotation_scope), byte pflags (bit0 vararg, bit1 kwarg, bit2 posonly), [varint posonly if pflags & 4], SL args, L defaults, [S vararg], SL kwonly, varint kwdefault-count x (byte present? [1 -> N]), [S kwarg], SL locals, SL gdecl, SL ndecl, L body |
| yield / return | [N v] per flags |
| namedexpr | N target, N v |
| assign | L targets, N v |
| if / while | N test, L then/body, L orelse |
| for | N target, N iter, L body, L orelse |
| break / continue / pass | nothing |
| class | S name, byte flags (bit0 kwargs, bit1 binding), [S binding], [kwargs: varint count x (byte named? [1->S n], N v)], L bases, L body |
| raise | [N exc], [N cause] per flags |
| try | byte star, L body, varint handler-count x (byte hflags: bit0 t, bit1 name; [N t], [S name], L body), L orelse, L finalbody |
| with | varint item-count x (byte has-name, N ctx, [N name]), L body |
| import | S module, byte bind (0 absent, 1 root, 2 none), [S alias] per flags |
| importfrom | S module, varint count x (S n, S alias) |
| del | L targets |

Decoding materializes the compact JSON form (spec section 2.2): empty lists,
false `gen`/`coro`/`asyncgen`/`annotation_scope`/`star`, zero `params.posonly`, and absent optionals are omitted from the produced
dicts. Key order does not affect structural equality.

The optional class `binding` is the final namespace store key. If absent,
it defaults to `name`; the source `name` remains the class construction and
metadata name, including the context used for private slot-name mangling.
When present, `binding` MUST be a string, not null. Its string reference
precedes any keyword arguments. Class flag bits other than 0 and 1 are
invalid. Classes without `binding` retain their existing binary encoding.

## Indexed standard-library store

Installed libraries use the `DSPS02` container (`stdlib.dsps`). This is not a
program bundle: it holds an index followed by independently encoded module
bundles. Runtimes read the container once, verify and parse only its index at
startup, and decode a module only when requested. The index supplies module
names and `file` paths for import resolution without reading module IR.

All fixed-width integers below are unsigned little-endian. Text lengths count
UTF-8 bytes; index names and paths require valid UTF-8 without surrogates.
Module names consist of nonempty identifier components separated by dots.
Ordering compares Unicode codepoints (equivalently, valid UTF-8 bytes), not
locale-sensitive collation. Files are nonempty and contain no NUL characters.
The index is limited to 64 MiB. Stored module lengths are between 1 byte and
256 MiB; decoded lengths are between 6 bytes and 256 MiB. Readers check these
limits before allocation or decompression.

```
header:
  magic             6 bytes, ASCII "DSPS02"
  index-length      uint32
  index-sha256      32 raw bytes
index:
  content-id        32 raw bytes
  dictionary-length uint16, at most 32768
  dictionary        dictionary-length raw preset bytes
  module-count      uint32
  records           module-count records, sorted by module name
record:
  name-length       uint16
  name              name-length UTF-8 bytes
  file-length       uint16
  file              file-length UTF-8 bytes
  offset            uint32, relative to the payload area after the index
  stored-length     uint32
  decoded-length    uint32
  codec             byte: 0 = raw DSPB, 1 = raw-DEFLATE-compressed DSPB
  payload-sha256    32 raw bytes, hashing the stored payload
payloads:
  module payloads in record order, with no gaps or trailing bytes
```

The content id is exposed as `sha256:` followed by the lowercase hexadecimal
encoding of `content-id`. It remains the SHA-256 of the canonical JSON object
`{"diaspore_stdlib":1,"modules":{...}}`, as defined in spec section 1.
Changing compression, string tables, or index layout does not change that id.
Generation computes it from the complete compiled IR before encoding.

Each payload decodes to a standalone `DSPB01` bundle with exactly one module.
Its `main`, module key, module `name`, and module `file` MUST agree with the
index. Each payload has its own string table, so decoding it requires no
other payload. Compression applies to individual payloads, never to the
whole store. Python, Go, and TypeScript distributions use codec 1; the
dependency-free Lua distribution uses codec 0 with the same content id.

Codec 1 uses an independent raw DEFLATE stream for each module, with no zlib
or gzip wrapper. The index dictionary supplies its initial history window.
An empty dictionary is valid and means ordinary raw DEFLATE. Encoders may
choose any preset bytes without changing the canonical content id; the shipped
encoder trains a bounded dictionary from patterns recurring across module
payloads. Dictionary bytes are covered by the index digest and loaded
once; module strings and IR remain lazy. Readers MUST reject dictionaries
larger than 32768 bytes and compressed streams that are truncated, have
trailing data, or exceed the exact declared decoded length.

The Lua distribution emits raw payloads and an empty dictionary. Its loader
understands the dictionary field but rejects compressed payloads. Installed
containers with another magic are rejected rather than interpreted as JSON
or as an older store layout.

Readers MUST verify the index digest before using its contents, reject
duplicate or unsorted names, unknown codecs, truncated records, overflowing
or noncontiguous payload ranges, and extra index or container bytes. Raw
payloads MUST have equal stored and decoded lengths. Before decoding a
requested module, readers MUST check its stored-payload digest and decoded
length, then validate its bundle and index identity. Decoded module IR is
cached; per-machine globals and execution state remain separate.

These hashes detect corrupted distribution bytes; they are not signatures
and do not authenticate a maliciously replaced store. The index binds its
declared canonical content id to the encoded module digests. Loading does not
recompute the canonical whole-library hash, which would defeat lazy decoding.
Caller-supplied parsed JSON stores still require canonical content-hash
validation. Distribution licenses and provenance are shipped separately,
not repeated in every module payload.
