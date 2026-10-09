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
magic     6 bytes  "DSPB01"
strtab    varint count, then per string: varint byte-length + UTF-8 bytes
semver    varint   (1 - same value as JSON's "diaspore" field)
main      str-ref
modules   varint count, then per module:
            name str-ref, file str-ref,
            src-flag byte (0|1), if 1: str-list (varint count + str-refs)
            body node-list
node-list varint count + that many nodes
```

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
| func / funcexpr | S name, byte flags (bit0 gen, bit1 coro), byte pflags (bit0 vararg, bit1 kwarg), SL args, L defaults, [S vararg], SL kwonly, varint kwdefault-count x (byte present? [1 -> N]), [S kwarg], SL locals, SL gdecl, SL ndecl, L body |
| yield / return | [N v] per flags |
| namedexpr | N target, N v |
| assign | L targets, N v |
| if / while | N test, L then/body, L orelse |
| for | N target, N iter, L body, L orelse |
| break / continue / pass | nothing |
| class | S name, byte kwflag, [kwargs: varint count x (byte named? [1->S n], N v)], L bases, L body |
| raise | [N exc], [N cause] per flags |
| try | byte star, L body, varint handler-count x (byte hflags: bit0 t, bit1 name; [N t], [S name], L body), L orelse, L finalbody |
| with | varint item-count x (byte has-name, N ctx, [N name]), L body |
| import | S module, byte bind (0 absent, 1 root, 2 none), [S alias] per flags |
| importfrom | S module, varint count x (S n, S alias) |
| del | L targets |

Decoding materializes the compact JSON form (spec section 2.2): empty lists,
false `gen`/`coro`/`star`, and absent optionals are omitted from the produced
dicts. Key order does not affect structural equality.
