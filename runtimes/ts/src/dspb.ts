/* DSPB: diaspore's optional binary bundle container (docs/dspb.md).
 *
 * An encoding of the same semantic model: JSON remains canonical
 * and default. The invariant: decode(encode(b)) == compact(b) (spec 2.2
 * compact form, absent defaults omitted), so digests and semantics are
 * computed on the decoded model and are container-independent.
 * Port of src/diaspore/dspb.py (the normative reference).
 */

import { encodeWTF8, decodeWTF8 } from "./unicode-transport.ts";
import { type PyString, hashKey, isPyString, strFromChars, strText } from "./values.ts";

/** Magic bytes identifying a standalone DSPB container. */
export const DSPB_MAGIC = Buffer.from("DSPB01", "ascii");
/** Magic bytes identifying a DSPB container with a linked standard library. */
export const DSPB_LINKED_MAGIC = Buffer.from("DSPB02", "ascii");

const OPS = [
  "const", "name", "attr", "index", "slicelit", "starred", "call", "binop",
  "unary", "boolop", "compare", "cond", "list", "tuple", "set", "dict",
  "fstring", "func", "funcexpr", "yield", "yieldfrom", "namedexpr",
  "expr", "assign", "augassign", "if", "while", "for", "break", "continue",
  "pass", "return", "class", "raise", "try", "with", "import", "importfrom",
  "del",
];
const BIN_KINDS = [
  "add", "sub", "mul", "div", "floordiv", "mod", "pow", "lshift",
  "rshift", "bitand", "bitor", "bitxor", "matmul",
];
const CMP_KINDS = ["eq", "ne", "lt", "le", "gt", "ge", "in", "notin", "is", "isnot"];
const UNARY_KINDS = ["neg", "pos", "not", "invert"];
const CONST_KINDS = ["int", "float", "str", "bytes", "bool", "none", "ellipsis", "complex"];
const CONV_KINDS: (string | null)[] = [null, "r", "s", "a"];

const OP_IDX = new Map(OPS.map((op, i) => [op, i]));
const BIN_IDX = new Map(BIN_KINDS.map((k, i) => [k, i]));
const CMP_IDX = new Map(CMP_KINDS.map((k, i) => [k, i]));
const UN_IDX = new Map(UNARY_KINDS.map((k, i) => [k, i]));
const CONST_IDX = new Map(CONST_KINDS.map((k, i) => [k, i]));
const CONV_IDX = new Map(CONV_KINDS.map((k, i) => [k, i]));

// op -> ordered optional-field names contributing flag bits (after ln)
const OPT: Record<string, string[]> = {
  slicelit: ["lower", "upper", "step"],
  yield: ["v"],
  return: ["v"],
  raise: ["exc", "cause"],
  import: ["alias"],
};

/** A malformed or unsupported DSPB container. */
export class DSPBError extends Error {}

type Node = Record<string, unknown>;

function isPresent(node: Node, key: string): boolean {
  return node[key] !== undefined && node[key] !== null;
}

// --------------------------------------------------------------------- writer

class Writer {
  bytes: number[] = [];
  strings = new Map<string, number>();
  strlist: PyString[] = [];

  u(n: number): void {
    if (n < 0) throw new DSPBError("negative varint");
    for (;;) {
      const b = n % 128;
      n = Math.floor(n / 128);
      if (n > 0) this.bytes.push(b | 0x80);
      else {
        this.bytes.push(b);
        return;
      }
    }
  }

  byte(b: number): void {
    this.bytes.push(b & 0xff);
  }

  s(text: PyString): void {
    const key = hashKey(text);
    let idx = this.strings.get(key);
    if (idx === undefined) {
      idx = this.strlist.length;
      this.strings.set(key, idx);
      this.strlist.push(text);
    }
    this.u(idx);
  }
}

const MAX_DEPTH = 8000; // hostile-input bound on node nesting

class Reader {
  data: Buffer;
  pos: number;
  strings: PyString[] = [];
  depth = 0;

  constructor(data: Buffer, pos: number) {
    this.data = data;
    this.pos = pos;
  }

  u(): number {
    let shift = 0;
    let n = 0;
    for (;;) {
      if (this.pos >= this.data.length) throw new DSPBError("truncated varint");
      const b = this.data[this.pos++];
      n += (b & 0x7f) * 2 ** shift;
      if ((b & 0x80) === 0) return n;
      shift += 7;
      if (shift > 63) throw new DSPBError("varint too long");
    }
  }

  /** A length prefix from untrusted input: every counted element costs at
   * least one byte, so counts cannot exceed the remaining bytes;
   * reject it before allocating anything proportional to it. */
  count(): number {
    const n = this.u();
    if (n > this.data.length - this.pos) throw new DSPBError("count exceeds remaining data");
    return n;
  }

  byte(): number {
    if (this.pos >= this.data.length) throw new DSPBError("truncated");
    return this.data[this.pos++];
  }

  text(): PyString {
    const idx = this.u();
    if (idx >= this.strings.length) throw new DSPBError("bad string ref");
    return this.strings[idx];
  }

  s(): string {
    return strText(this.text());
  }
}

function enumAt<T>(table: T[], idx: number, what: string): T {
  if (idx >= table.length) throw new DSPBError(`bad ${what} byte`);
  return table[idx];
}

function literalText(node: Node, field: string): PyString {
  const points = node.codepoints as number[] | undefined;
  return points === undefined ? node[field] as PyString : strFromChars(points.map(point => String.fromCodePoint(point)));
}

function decodedLiteral(value: PyString, field: string): Node {
  const node: Node = { [field]: strText(value) };
  let hasSurrogate = false;
  for (const char of value) {
    const point = char.codePointAt(0)!;
    if (point >= 0xd800 && point <= 0xdfff) {
      hasSurrogate = true;
      break;
    }
  }
  if (hasSurrogate) node.codepoints = Array.from(value, char => char.codePointAt(0)!);
  return node;
}

// ------------------------------------------------------------------ encoding

function encFlags(node: Node, opts: string[]): number {
  let f = isPresent(node, "ln") ? 1 : 0;
  for (let i = 0; i < opts.length; i++) {
    if (isPresent(node, opts[i])) f |= 1 << (i + 1);
  }
  return f;
}

function encNodes(w: Writer, nodes: Node[] | undefined): void {
  const list = nodes ?? [];
  w.u(list.length);
  for (const n of list) enc(w, n);
}

function encStrs(w: Writer, items: string[] | undefined): void {
  const list = items ?? [];
  w.u(list.length);
  for (const s of list) w.s(s);
}

function reqIdx<K>(m: Map<K, number>, k: K, what: string): number {
  const i = m.get(k);
  if (i === undefined) throw new DSPBError(`unknown ${what} ${JSON.stringify(k)}`);
  return i;
}

function enc(w: Writer, node: Node): void {
  const op = node.op as string;
  w.byte(reqIdx(OP_IDX, op, "op"));
  w.byte(encFlags(node, OPT[op] ?? []));
  if (isPresent(node, "ln")) w.u(node.ln as number);
  switch (op) {
    case "const": {
      const kind = node.t as string;
      w.byte(reqIdx(CONST_IDX, kind, "const kind"));
      if (kind === "int" || kind === "float" || kind === "str" || kind === "bytes") {
        w.s(kind === "str" ? literalText(node, "v") : node.v as string);
      } else if (kind === "bool") {
        w.byte(node.v === true ? 1 : 0);
      } else if (kind === "complex") {
        w.s(node.re as string);
        w.s(node.im as string);
      }
      break;
    }
    case "name":
      w.s(node.id as string);
      break;
    case "attr":
      enc(w, node.o as Node);
      w.s(node.n as string);
      break;
    case "index":
      enc(w, node.o as Node);
      enc(w, node.k as Node);
      break;
    case "slicelit":
      for (const key of ["lower", "upper", "step"]) {
        if (isPresent(node, key)) enc(w, node[key] as Node);
      }
      break;
    case "starred":
    case "yieldfrom":
    case "expr":
      enc(w, node.v as Node);
      break;
    case "call": {
      enc(w, node.f as Node);
      encNodes(w, node.args as Node[] | undefined);
      const kws = (node.kw as Node[] | undefined) ?? [];
      w.u(kws.length);
      for (const kw of kws) {
        if (kw.n === null || kw.n === undefined) {
          w.byte(0);
        } else {
          w.byte(1);
          w.s(kw.n as string);
        }
        enc(w, kw.v as Node);
      }
      break;
    }
    case "binop":
    case "augassign":
      w.byte(reqIdx(BIN_IDX, node.kind, "binop kind"));
      if (op === "binop") {
        enc(w, node.l as Node);
        enc(w, node.r as Node);
      } else {
        enc(w, node.target as Node);
        enc(w, node.v as Node);
      }
      break;
    case "unary":
      w.byte(reqIdx(UN_IDX, node.kind, "unary kind"));
      enc(w, node.v as Node);
      break;
    case "boolop":
      w.byte(node.kind === "and" ? 0 : 1);
      encNodes(w, node.values as Node[]);
      break;
    case "compare": {
      enc(w, node.left as Node);
      const ops = node.ops as string[];
      w.u(ops.length);
      for (const k of ops) w.byte(reqIdx(CMP_IDX, k, "cmp kind"));
      encNodes(w, node.comps as Node[]);
      break;
    }
    case "cond":
      enc(w, node.test as Node);
      enc(w, node.then as Node);
      enc(w, node.else as Node);
      break;
    case "list":
    case "tuple":
    case "set":
      encNodes(w, node.elts as Node[] | undefined);
      break;
    case "dict": {
      const entries = (node.entries as Node[] | undefined) ?? [];
      w.u(entries.length);
      for (const e of entries) {
        if (e.k === null || e.k === undefined) {
          w.byte(0);
        } else {
          w.byte(1);
          enc(w, e.k as Node);
        }
        enc(w, e.v as Node);
      }
      break;
    }
    case "fstring": {
      const parts = node.parts as Node[];
      w.u(parts.length);
      for (const p of parts) {
        if (isPyString(p.s)) {
          w.byte(0);
          w.s(literalText(p, "s"));
        } else {
          let flag = 1;
          if (isPresent(p, "spec")) flag |= 2;
          w.byte(flag);
          enc(w, p.v as Node);
          const conv = p.conv === undefined ? null : (p.conv as string | null);
          w.byte(reqIdx(CONV_IDX, conv, "conv"));
          if (isPresent(p, "spec")) enc(w, p.spec as Node);
        }
      }
      break;
    }
    case "func":
    case "funcexpr": {
      w.s(node.name as string);
      w.byte((node.gen === true ? 1 : 0) | (node.coro === true ? 2 : 0) | (node.asyncgen === true ? 4 : 0) | (node.annotation_scope === true ? 8 : 0));
      const p = (node.params as Node | undefined) ?? {};
      const pf = (isPresent(p, "vararg") ? 1 : 0) | (isPresent(p, "kwarg") ? 2 : 0) | (p.posonly ? 4 : 0);
      w.byte(pf);
      if (p.posonly) w.u(p.posonly as number);
      encStrs(w, p.args as string[] | undefined);
      encNodes(w, p.defaults as Node[] | undefined);
      if (isPresent(p, "vararg")) w.s(p.vararg as string);
      encStrs(w, p.kwonly as string[] | undefined);
      const kwd = (p.kwdefaults as (Node | null)[] | undefined) ?? [];
      w.u(kwd.length);
      for (const d of kwd) {
        if (d === null || d === undefined) {
          w.byte(0);
        } else {
          w.byte(1);
          enc(w, d);
        }
      }
      if (isPresent(p, "kwarg")) w.s(p.kwarg as string);
      encStrs(w, node.locals as string[] | undefined);
      encStrs(w, node.gdecl as string[] | undefined);
      encStrs(w, node.ndecl as string[] | undefined);
      encNodes(w, node.body as Node[]);
      break;
    }
    case "yield":
    case "return":
      if (isPresent(node, "v")) enc(w, node.v as Node);
      break;
    case "namedexpr":
      enc(w, node.target as Node);
      enc(w, node.v as Node);
      break;
    case "assign":
      encNodes(w, node.targets as Node[]);
      enc(w, node.v as Node);
      break;
    case "if":
    case "while":
      enc(w, node.test as Node);
      encNodes(w, (op === "if" ? node.then : node.body) as Node[]);
      encNodes(w, node.orelse as Node[] | undefined);
      break;
    case "for":
      enc(w, node.target as Node);
      enc(w, node.iter as Node);
      encNodes(w, node.body as Node[]);
      encNodes(w, node.orelse as Node[] | undefined);
      break;
    case "break":
    case "continue":
    case "pass":
      break;
    case "class": {
      w.s(node.name as string);
      const kwargs = (node.kwargs as Node[] | undefined) ?? [];
      const hasBinding = Object.hasOwn(node, "binding");
      if (hasBinding && typeof node.binding !== "string") throw new DSPBError("invalid class binding");
      w.byte((kwargs.length > 0 ? 1 : 0) | (hasBinding ? 2 : 0));
      if (hasBinding) w.s(node.binding as string);
      if (kwargs.length > 0) {
        w.u(kwargs.length);
        for (const kw of kwargs) {
          if (kw.n === null || kw.n === undefined) {
            w.byte(0);
          } else {
            w.byte(1);
            w.s(kw.n as string);
          }
          enc(w, kw.v as Node);
        }
      }
      encNodes(w, node.bases as Node[] | undefined);
      encNodes(w, node.body as Node[]);
      break;
    }
    case "raise":
      if (isPresent(node, "exc")) enc(w, node.exc as Node);
      if (isPresent(node, "cause")) enc(w, node.cause as Node);
      break;
    case "try": {
      w.byte(node.star === true ? 1 : 0);
      encNodes(w, node.body as Node[]);
      const handlers = (node.handlers as Node[] | undefined) ?? [];
      w.u(handlers.length);
      for (const h of handlers) {
        const hf = (isPresent(h, "t") ? 1 : 0) | (isPresent(h, "name") ? 2 : 0);
        w.byte(hf);
        if (isPresent(h, "t")) enc(w, h.t as Node);
        if (isPresent(h, "name")) w.s(h.name as string);
        encNodes(w, h.body as Node[]);
      }
      encNodes(w, node.orelse as Node[] | undefined);
      encNodes(w, node.finalbody as Node[] | undefined);
      break;
    }
    case "with": {
      const items = node.items as Node[];
      w.u(items.length);
      for (const it of items) {
        w.byte(isPresent(it, "name") ? 1 : 0);
        enc(w, it.ctx as Node);
        if (isPresent(it, "name")) enc(w, it.name as Node);
      }
      encNodes(w, node.body as Node[]);
      break;
    }
    case "import": {
      w.s(node.module as string);
      const bind = node.bind as string | undefined;
      w.byte(bind === undefined || bind === null ? 0 : bind === "root" ? 1 : 2);
      if (isPresent(node, "alias")) w.s(node.alias as string);
      break;
    }
    case "importfrom": {
      w.s(node.module as string);
      const names = node.names as Node[];
      w.u(names.length);
      for (const nm of names) {
        w.s(nm.n as string);
        w.s(nm.alias as string);
      }
      break;
    }
    case "del":
      encNodes(w, node.targets as Node[]);
      break;
    default:
      throw new DSPBError(`unknown op ${JSON.stringify(op)}`);
  }
}

// ------------------------------------------------------------------ decoding

function decNodes(r: Reader): Node[] {
  const count = r.count();
  const out: Node[] = [];
  for (let i = 0; i < count; i++) out.push(dec(r));
  return out;
}

function decStrs(r: Reader): string[] {
  const count = r.count();
  const out: string[] = [];
  for (let i = 0; i < count; i++) out.push(r.s());
  return out;
}

function dec(r: Reader): Node {
  r.depth++;
  if (r.depth > MAX_DEPTH) throw new DSPBError("node nesting too deep");
  try {
    return decInner(r);
  } finally {
    r.depth--;
  }
}

function decInner(r: Reader): Node {
  const opb = r.byte();
  if (opb >= OPS.length) throw new DSPBError("bad opcode");
  const op = OPS[opb];
  const flags = r.byte();
  const ln = (flags & 1) !== 0 ? r.u() : null;
  const opts = OPT[op] ?? [];
  const present: Record<string, boolean> = {};
  for (let i = 0; i < opts.length; i++) {
    present[opts[i]] = (flags & (1 << (i + 1))) !== 0;
  }
  const n: Node = { op };

  switch (op) {
    case "const": {
      const kind = enumAt(CONST_KINDS, r.byte(), "const-kind");
      n.t = kind;
      if (kind === "int" || kind === "float" || kind === "str" || kind === "bytes") {
        if (kind === "str") Object.assign(n, decodedLiteral(r.text(), "v"));
        else n.v = r.s();
      } else if (kind === "bool") {
        n.v = r.byte() === 1;
      } else if (kind === "complex") {
        n.re = r.s();
        n.im = r.s();
      }
      break;
    }
    case "name":
      n.id = r.s();
      break;
    case "attr":
      n.o = dec(r);
      n.n = r.s();
      break;
    case "index":
      n.o = dec(r);
      n.k = dec(r);
      break;
    case "slicelit":
      for (const key of ["lower", "upper", "step"]) {
        if (present[key]) n[key] = dec(r);
      }
      break;
    case "starred":
    case "yieldfrom":
    case "expr":
      n.v = dec(r);
      break;
    case "call": {
      n.f = dec(r);
      const args = decNodes(r);
      if (args.length > 0) n.args = args;
      const kwCount = r.count();
      const kws: Node[] = [];
      for (let i = 0; i < kwCount; i++) {
        const named = r.byte() === 1;
        const kname = named ? r.s() : null;
        kws.push({ n: kname, v: dec(r) });
      }
      if (kws.length > 0) n.kw = kws;
      break;
    }
    case "binop":
    case "augassign":
      n.kind = enumAt(BIN_KINDS, r.byte(), "binop-kind");
      if (op === "binop") {
        n.l = dec(r);
        n.r = dec(r);
      } else {
        n.target = dec(r);
        n.v = dec(r);
      }
      break;
    case "unary":
      n.kind = enumAt(UNARY_KINDS, r.byte(), "unary-kind");
      n.v = dec(r);
      break;
    case "boolop":
      n.kind = r.byte() === 0 ? "and" : "or";
      n.values = decNodes(r);
      break;
    case "compare": {
      n.left = dec(r);
      const count = r.count();
      const ops: string[] = [];
      for (let i = 0; i < count; i++) ops.push(enumAt(CMP_KINDS, r.byte(), "compare-kind"));
      n.ops = ops;
      n.comps = decNodes(r);
      break;
    }
    case "cond":
      n.test = dec(r);
      n.then = dec(r);
      n.else = dec(r);
      break;
    case "list":
    case "tuple":
    case "set": {
      const elts = decNodes(r);
      if (elts.length > 0) n.elts = elts;
      break;
    }
    case "dict": {
      const count = r.count();
      const entries: Node[] = [];
      for (let i = 0; i < count; i++) {
        const hasK = r.byte() === 1;
        const k = hasK ? dec(r) : null;
        entries.push({ k, v: dec(r) });
      }
      if (entries.length > 0) n.entries = entries;
      break;
    }
    case "fstring": {
      const count = r.count();
      const parts: Node[] = [];
      for (let i = 0; i < count; i++) {
        const flag = r.byte();
        if (flag === 0) {
          parts.push(decodedLiteral(r.text(), "s"));
        } else {
          const part: Node = { v: dec(r) };
          const conv = enumAt(CONV_KINDS, r.byte(), "conv");
          if (conv !== null) part.conv = conv;
          if ((flag & 2) !== 0) part.spec = dec(r);
          parts.push(part);
        }
      }
      n.parts = parts;
      break;
    }
    case "func":
    case "funcexpr": {
      n.name = r.s();
      const gbyte = r.byte();
      if ((gbyte & 1) !== 0) n.gen = true;
      if ((gbyte & 2) !== 0) n.coro = true;
      if ((gbyte & 4) !== 0) n.asyncgen = true;
      if ((gbyte & 8) !== 0) n.annotation_scope = true;
      const pf = r.byte();
      const p: Node = {};
      if ((pf & 4) !== 0) p.posonly = r.u();
      const args = decStrs(r);
      if (args.length > 0) p.args = args;
      const defaults = decNodes(r);
      if (defaults.length > 0) p.defaults = defaults;
      if ((pf & 1) !== 0) p.vararg = r.s();
      const kwonly = decStrs(r);
      if (kwonly.length > 0) p.kwonly = kwonly;
      const kwdCount = r.count();
      const kwd: (Node | null)[] = [];
      for (let i = 0; i < kwdCount; i++) {
        kwd.push(r.byte() === 1 ? dec(r) : null);
      }
      if (kwd.length > 0) p.kwdefaults = kwd;
      if ((pf & 2) !== 0) p.kwarg = r.s();
      n.params = p;
      const locals = decStrs(r);
      if (locals.length > 0) n.locals = locals;
      const gdecl = decStrs(r);
      if (gdecl.length > 0) n.gdecl = gdecl;
      const ndecl = decStrs(r);
      if (ndecl.length > 0) n.ndecl = ndecl;
      n.body = decNodes(r);
      break;
    }
    case "yield":
    case "return":
      if (present.v) n.v = dec(r);
      break;
    case "namedexpr":
      n.target = dec(r);
      n.v = dec(r);
      break;
    case "assign":
      n.targets = decNodes(r);
      n.v = dec(r);
      break;
    case "if":
    case "while": {
      n.test = dec(r);
      n[op === "if" ? "then" : "body"] = decNodes(r);
      const orelse = decNodes(r);
      if (orelse.length > 0) n.orelse = orelse;
      break;
    }
    case "for": {
      n.target = dec(r);
      n.iter = dec(r);
      n.body = decNodes(r);
      const orelse = decNodes(r);
      if (orelse.length > 0) n.orelse = orelse;
      break;
    }
    case "break":
    case "continue":
    case "pass":
      break;
    case "class": {
      n.name = r.s();
      const flags = r.byte();
      if ((flags & ~3) !== 0) throw new DSPBError("unknown class flags");
      if ((flags & 2) !== 0) n.binding = r.s();
      if ((flags & 1) !== 0) {
        const count = r.count();
        const kwargs: Node[] = [];
        for (let i = 0; i < count; i++) {
          const name = r.byte() !== 0 ? r.s() : null;
          kwargs.push({ n: name, v: dec(r) });
        }
        n.kwargs = kwargs;
      }
      const bases = decNodes(r);
      if (bases.length > 0) n.bases = bases;
      n.body = decNodes(r);
      break;
    }
    case "raise":
      if (present.exc) n.exc = dec(r);
      if (present.cause) n.cause = dec(r);
      break;
    case "try": {
      if (r.byte() === 1) n.star = true;
      n.body = decNodes(r);
      const hCount = r.count();
      const handlers: Node[] = [];
      for (let i = 0; i < hCount; i++) {
        const hf = r.byte();
        const h: Node = {};
        if ((hf & 1) !== 0) h.t = dec(r);
        if ((hf & 2) !== 0) h.name = r.s();
        h.body = decNodes(r);
        handlers.push(h);
      }
      if (handlers.length > 0) n.handlers = handlers;
      const orelse = decNodes(r);
      if (orelse.length > 0) n.orelse = orelse;
      const finalbody = decNodes(r);
      if (finalbody.length > 0) n.finalbody = finalbody;
      break;
    }
    case "with": {
      const count = r.count();
      const items: Node[] = [];
      for (let i = 0; i < count; i++) {
        const hasName = r.byte() === 1;
        const item: Node = { ctx: dec(r) };
        if (hasName) item.name = dec(r);
        items.push(item);
      }
      n.items = items;
      n.body = decNodes(r);
      break;
    }
    case "import": {
      n.module = r.s();
      const bind = r.byte();
      if (bind === 1) n.bind = "root";
      else if (bind === 2) n.bind = "none";
      if (present.alias) n.alias = r.s();
      break;
    }
    case "importfrom": {
      n.module = r.s();
      const count = r.count();
      const names: Node[] = [];
      for (let i = 0; i < count; i++) {
        names.push({ n: r.s(), alias: r.s() });
      }
      n.names = names;
      break;
    }
    case "del":
      n.targets = decNodes(r);
      break;
  }

  if (ln !== null) n.ln = ln;
  return n;
}

// --------------------------------------------------------------------- bundle

/** Encode a bundle as a DSPB container. */
export function dspbEncode(bundle: Node): Buffer {
  if (bundle.diaspore !== 1) throw new DSPBError("unsupported Diaspore bundle version");
  const linked = Object.hasOwn(bundle, "stdlib");
  if (linked && (typeof bundle.stdlib !== "string" || !/^sha256:[0-9a-f]{64}$/.test(bundle.stdlib))) {
    throw new DSPBError("invalid stdlib content id");
  }
  const w = new Writer();
  w.u(1); // semantic version, same as JSON's "diaspore"
  w.s(bundle.main as string);
  if (linked) w.s(bundle.stdlib as string);
  const mods = bundle.modules as Record<string, Node>;
  const names = Object.keys(mods);
  w.u(names.length);
  for (const name of names) {
    const mod = mods[name];
    w.s(name);
    w.s(mod.file as string);
    const src = mod.src as string[] | undefined;
    if (src === undefined || src === null) {
      w.byte(0);
    } else {
      w.byte(1);
      encStrs(w, src);
    }
    encNodes(w, mod.body as Node[]);
  }
  const body = Buffer.from(w.bytes);
  const head = new Writer();
  head.u(w.strlist.length);
  const headParts: Buffer[] = [];
  for (const s of w.strlist) {
    const raw = encodeWTF8(s);
    head.u(raw.length);
    for (const b of raw) head.bytes.push(b);
  }
  headParts.push(Buffer.from(head.bytes));
  return Buffer.concat([linked ? DSPB_LINKED_MAGIC : DSPB_MAGIC, ...headParts, body]);
}

/** Test whether bytes begin with the DSPB magic. */
export function isDSPB(data: Buffer): boolean {
  return (
    data.length >= DSPB_MAGIC.length &&
    (data.subarray(0, DSPB_MAGIC.length).equals(DSPB_MAGIC) ||
      data.subarray(0, DSPB_LINKED_MAGIC.length).equals(DSPB_LINKED_MAGIC))
  );
}

/** Decode untrusted DSPB bytes, rejecting errors through DSPBError. */
export function dspbDecode(data: Buffer): Node {
  try {
    return dspbDecodeInner(data);
  } catch (e) {
    if (e instanceof DSPBError) throw e;
    throw new DSPBError("decode failed: " + (e as Error).message);
  }
}

function dspbDecodeInner(data: Buffer): Node {
  if (!isDSPB(data)) throw new DSPBError("not an DSPB bundle");
  const r = new Reader(data, DSPB_MAGIC.length);
  const strCount = r.count();
  for (let i = 0; i < strCount; i++) {
    const length = r.u();
    if (r.pos + length > r.data.length) throw new DSPBError("truncated string table");
    try {
      r.strings.push(decodeWTF8(r.data.subarray(r.pos, r.pos + length)));
    } catch {
      throw new DSPBError("invalid UTF-8 in string table");
    }
    r.pos += length;
  }
  if (r.u() !== 1) throw new DSPBError("unsupported semantic version");
  const main = r.s();
  const linked = data.subarray(0, DSPB_LINKED_MAGIC.length).equals(DSPB_LINKED_MAGIC);
  const stdlib = linked ? r.s() : undefined;
  if (stdlib !== undefined && !/^sha256:[0-9a-f]{64}$/.test(stdlib)) {
    throw new DSPBError("invalid stdlib content id");
  }
  const modules: Record<string, Node> = {};
  const modCount = r.count();
  for (let i = 0; i < modCount; i++) {
    const name = r.s();
    const mod: Node = { name, file: r.s() };
    if (r.byte() === 1) mod.src = decStrs(r);
    mod.body = decNodes(r);
    modules[name] = mod;
  }
  if (r.pos !== data.length) throw new DSPBError("trailing bytes after DSPB bundle");
  return linked ? { diaspore: 1, main, stdlib, modules } : { diaspore: 1, main, modules };
}
