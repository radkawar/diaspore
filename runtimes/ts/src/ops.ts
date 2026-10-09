/* Operators, comparisons, indexing/slicing, truthiness, and repr/str:
 * CPython-parity semantics mirroring diaspore-py (reference) and diaspore-go. */

import {
  type Value,
  type G,
  PyNoneT,
  PyEllipsisT,
  DiasporeList,
  DiasporeTuple,
  DiasporeDict,
  DiasporeSet,
  DiasporeRange,
  DiasporeSlice,
  DiasporeClass,
  DiasporeInstance,
  DiasporeFunction,
  BoundMethod,
  Builtin,
  BuiltinType,
  DiasporeModule,
  NativeModule,
  NativeObject,
  DiasporeFile,
  DiasporeIterBase,
  Complex,
  typeName,
  asIndex,
  asFloat,
  isNumber,
  bigFloatCmp,
  strCmp,
  strLen,
  chars,
  hasAstral,
  bytesEq,
  bytesCmp,
  bytesConcat,
  bytesIndexOf,
  diasporeErr,
  DiasporeError,
  makeExc,
  excArgs,
  BASE_EXC,
  EXC_CLASSES,
} from "./values.ts";
import { pyFloatStr, reprString, reprBytes, reprRange, complexRepr } from "./format.ts";
import { getIter, iterToArray, DiasporeGenerator } from "./iter.ts";
import type { Interp } from "./interp.ts";

const BIN_SYM: Record<string, string> = {
  add: "+",
  sub: "-",
  mul: "*",
  div: "/",
  floordiv: "//",
  mod: "%",
  pow: "**",
  lshift: "<<",
  rshift: ">>",
  bitand: "&",
  bitor: "|",
  bitxor: "^",
  matmul: "@",
};

const BIN_DUNDER: Record<string, [string, string]> = {
  add: ["__add__", "__radd__"],
  sub: ["__sub__", "__rsub__"],
  mul: ["__mul__", "__rmul__"],
  div: ["__truediv__", "__rtruediv__"],
  matmul: ["__matmul__", "__rmatmul__"],
  floordiv: ["__floordiv__", "__rfloordiv__"],
  mod: ["__mod__", "__rmod__"],
  pow: ["__pow__", "__rpow__"],
  lshift: ["__lshift__", "__rlshift__"],
  rshift: ["__rshift__", "__rrshift__"],
  bitand: ["__and__", "__rand__"],
  bitor: ["__or__", "__ror__"],
  bitxor: ["__xor__", "__rxor__"],
};

function binopTypeError(kind: string, l: Value, r: Value): DiasporeError {
  return diasporeErr(
    "TypeError",
    `unsupported operand type(s) for ${BIN_SYM[kind]}: '${typeName(l)}' and '${typeName(r)}'`,
  );
}

/** Evaluate a binary operator, including user-defined overloads. */
export function* binop(I: Interp, kind: string, l: Value, r: Value, inplace = false): G<Value> {
  if (kind === "mod" && (typeof l === "string" || l instanceof Uint8Array)) {
    throw diasporeErr("TypeError", "diaspore does not support %-formatting; use f-strings");
  }
  if (kind === "mul") {
    // Meter allocation bombs: "x" * n, [0] * n, ...
    let seq = l;
    let n = r;
    if (asIndex(l) !== null) {
      seq = r;
      n = l;
    }
    const count = asIndex(n);
    if (count !== null && count > 0n) {
      let size = -1;
      if (typeof seq === "string") size = seq.length;
      else if (seq instanceof Uint8Array) size = seq.length;
      else if (seq instanceof DiasporeList || seq instanceof DiasporeTuple) size = seq.items.length;
      if (size >= 0) {
        I.fuelN(Number((BigInt(size) * count) / 16n + 1n));
      }
    }
  }
  const lInst = l instanceof DiasporeInstance;
  const rInst = r instanceof DiasporeInstance;
  if (lInst || rInst) {
    const names = BIN_DUNDER[kind];
    if (names !== undefined) {
      if (inplace && lInst) {
        // augmented assignment: __i<op>__ tried before the plain operator
        const iname = "__i" + names[0].slice(2);
        if ((l as DiasporeInstance).cls.lookup(iname) !== undefined) {
          return yield* I.callDunder(l as DiasporeInstance, iname, [r]);
        }
      }
      if (lInst && (l as DiasporeInstance).cls.lookup(names[0]) !== undefined) {
        return yield* I.callDunder(l as DiasporeInstance, names[0], [r]);
      }
      if (names[1] !== "" && rInst && (r as DiasporeInstance).cls.lookup(names[1]) !== undefined) {
        return yield* I.callDunder(r as DiasporeInstance, names[1], [l]);
      }
    }
    throw binopTypeError(kind, l, r);
  }
  if (kind === "matmul") {
    throw binopTypeError(kind, l, r); // dunder-only: no builtin type supports @
  }
  // complex arithmetic (either operand complex, both numeric-or-complex)
  if (l instanceof Complex || r instanceof Complex) {
    const lc = toComplex(l);
    const rc = toComplex(r);
    if (lc !== null && rc !== null) return complexBinop(kind, lc, rc);
    throw binopTypeError(kind, l, r);
  }

  // int op int (bool counts as int)
  const li = asIndex(l);
  if (li !== null) {
    const ri = asIndex(r);
    if (ri !== null) return intBinop(I, kind, li, ri);
  }
  // numeric with a float involved
  if (isNumber(l) && isNumber(r)) {
    return floatBinop(kind, asFloat(l) as number, asFloat(r) as number);
  }

  switch (kind) {
    case "add":
      if (typeof l === "string") {
        if (typeof r === "string") return l + r;
        throw diasporeErr("TypeError", `can only concatenate str (not "${typeName(r)}") to str`);
      }
      if (l instanceof Uint8Array) {
        if (r instanceof Uint8Array) return bytesConcat(l, r);
        throw diasporeErr("TypeError", `can only concatenate bytes (not "${typeName(r)}") to bytes`);
      }
      if (l instanceof DiasporeList) {
        if (r instanceof DiasporeList) {
          if (inplace) {
            l.items.push(...r.items);
            return l;
          }
          return new DiasporeList([...l.items, ...r.items]);
        }
        if (inplace) {
          try {
            const items = yield* iterToArray(I, r);
            l.items.push(...items);
            return l;
          } catch {
            // fall through to the TypeError below
          }
        }
        throw diasporeErr("TypeError", `can only concatenate list (not "${typeName(r)}") to list`);
      }
      if (l instanceof DiasporeTuple) {
        if (r instanceof DiasporeTuple) return new DiasporeTuple([...l.items, ...r.items]);
        throw diasporeErr("TypeError", `can only concatenate tuple (not "${typeName(r)}") to tuple`);
      }
      break;
    case "mul": {
      let res = seqRepeat(l, r, inplace);
      if (res !== null) return res;
      res = seqRepeat(r, l, false);
      if (res !== null) return res;
      break;
    }
    case "bitor":
    case "bitand":
    case "bitxor":
    case "sub":
      if (l instanceof DiasporeSet && r instanceof DiasporeSet) return setOp(kind, l, r);
      break;
  }
  throw binopTypeError(kind, l, r);
}

/** seq * n; returns null when the pair is not a repeat form. */
function seqRepeat(seq: Value, count: Value, inplace: boolean): Value | null {
  const isSeq =
    typeof seq === "string" ||
    seq instanceof Uint8Array ||
    seq instanceof DiasporeList ||
    seq instanceof DiasporeTuple;
  const n0 = asIndex(count);
  if (n0 === null) {
    if (isSeq && typeof count === "number") {
      throw diasporeErr(
        "TypeError",
        `can't multiply sequence by non-int of type '${typeName(count)}'`,
      );
    }
    return null;
  }
  if (!isSeq) return null;
  if (n0 > 9007199254740991n) {
    throw diasporeErr("OverflowError", "cannot fit 'int' into an index-sized integer");
  }
  const n = n0 < 0n ? 0n : n0;
  const nn = Number(n);
  if (typeof seq === "string") return seq.repeat(nn);
  if (seq instanceof Uint8Array) {
    const out = new Uint8Array(seq.length * nn);
    for (let i = 0; i < nn; i++) out.set(seq, i * seq.length);
    return out;
  }
  const items: Value[] = [];
  for (let i = 0; i < nn; i++) items.push(...(seq as DiasporeList | DiasporeTuple).items);
  if (seq instanceof DiasporeTuple) return new DiasporeTuple(items);
  if (inplace) {
    (seq as DiasporeList).items = items;
    return seq;
  }
  return new DiasporeList(items);
}

/** Convert a numeric value to complex, or return null for other values. */
export function toComplex(v: Value): Complex | null {
  if (v instanceof Complex) return v;
  const f = asFloat(v);
  return f === null ? null : new Complex(f, 0);
}

function complexBinop(kind: string, a: Complex, b: Complex): Value {
  switch (kind) {
    case "add":
      return new Complex(a.re + b.re, a.im + b.im);
    case "sub":
      return new Complex(a.re - b.re, a.im - b.im);
    case "mul":
      return new Complex(a.re * b.re - a.im * b.im, a.re * b.im + a.im * b.re);
    case "div": {
      const d = b.re * b.re + b.im * b.im;
      if (d === 0) throw diasporeErr("ZeroDivisionError", "complex division by zero");
      return new Complex((a.re * b.re + a.im * b.im) / d, (a.im * b.re - a.re * b.im) / d);
    }
    case "pow":
      return complexPow(a, b);
  }
  throw diasporeErr(
    "TypeError",
    `unsupported operand type(s) for ${BIN_SYM[kind]}: 'complex' and 'complex'`,
  );
}

function complexPow(a: Complex, b: Complex): Complex {
  if (a.re === 0 && a.im === 0) {
    if (b.re === 0 && b.im === 0) return new Complex(1, 0);
    throw diasporeErr("ZeroDivisionError", "0.0 to a negative or complex power");
  }
  // CPython uses exact repeated multiplication for small integer exponents.
  if (b.im === 0 && Number.isInteger(b.re) && Math.abs(b.re) <= 100) {
    let n = b.re;
    let base = a;
    if (n < 0) {
      base = complexBinop("div", new Complex(1, 0), a) as Complex;
      n = -n;
    }
    let result = new Complex(1, 0);
    for (let i = 0; i < n; i++) {
      result = new Complex(
        result.re * base.re - result.im * base.im,
        result.re * base.im + result.im * base.re,
      );
    }
    return result;
  }
  // exp(b * log(a))
  const logr = Math.log(Math.hypot(a.re, a.im));
  const theta = Math.atan2(a.im, a.re);
  const rePart = b.re * logr - b.im * theta;
  const imPart = b.re * theta + b.im * logr;
  const mag = Math.exp(rePart);
  return new Complex(mag * Math.cos(imPart), mag * Math.sin(imPart));
}

/** Apply a set algebra operator to two sandbox sets. */
export function setOp(kind: string, a: DiasporeSet, b: DiasporeSet): DiasporeSet {
  const out = new DiasporeSet();
  switch (kind) {
    case "bitor":
      for (const [hk, v] of a.map) out.addHK(hk, v);
      for (const [hk, v] of b.map) out.addHK(hk, v);
      break;
    case "bitand":
      for (const [hk, v] of a.map) if (b.map.has(hk)) out.addHK(hk, v);
      break;
    case "sub":
      for (const [hk, v] of a.map) if (!b.map.has(hk)) out.addHK(hk, v);
      break;
    case "bitxor":
      for (const [hk, v] of a.map) if (!b.map.has(hk)) out.addHK(hk, v);
      for (const [hk, v] of b.map) if (!a.map.has(hk)) out.addHK(hk, v);
      break;
  }
  return out;
}

function intBinop(I: Interp, kind: string, a: bigint, b: bigint): Value {
  switch (kind) {
    case "add":
      return a + b;
    case "sub":
      return a - b;
    case "mul": {
      const bits = bitLen(a) + bitLen(b);
      if (bits > 512) I.fuelN(bits / 512 + 1);
      return a * b;
    }
    case "div":
      if (b === 0n) throw diasporeErr("ZeroDivisionError", "division by zero");
      return intTrueDiv(a, b);
    case "floordiv":
      if (b === 0n) throw diasporeErr("ZeroDivisionError", "integer division or modulo by zero");
      return floorDiv(a, b);
    case "mod":
      if (b === 0n) throw diasporeErr("ZeroDivisionError", "integer modulo by zero");
      return floorMod(a, b);
    case "pow":
      if (b >= 0n) {
        if (b > 0xffffffffn) throw diasporeErr("OverflowError", "exponent too large");
        const cost = (bitLen(a) * Number(b)) / 512;
        if (cost > 1) I.fuelN(cost + 1);
        return a ** b;
      }
      if (a === 0n) {
        throw diasporeErr("ZeroDivisionError", "0.0 cannot be raised to a negative power");
      }
      return Math.pow(Number(a), Number(b));
    case "lshift":
      if (b < 0n) throw diasporeErr("ValueError", "negative shift count");
      if (b > 0xffffffffn) throw diasporeErr("OverflowError", "shift count too large");
      I.fuelN((bitLen(a) + Number(b)) / 512 + 1);
      return a << b;
    case "rshift":
      if (b < 0n) throw diasporeErr("ValueError", "negative shift count");
      if (b > 0xffffffffn) return a < 0n ? -1n : 0n;
      return a >> b;
    case "bitand":
      return a & b;
    case "bitor":
      return a | b;
    case "bitxor":
      return a ^ b;
  }
  throw diasporeErr("TypeError", `unknown int op ${kind}`);
}

function bitLen(a: bigint): number {
  if (a < 0n) a = -a;
  if (a < 0x100000000n) return 32;
  return a.toString(16).length * 4;
}

/** Divide integers with rounding toward negative infinity. */
export function floorDiv(a: bigint, b: bigint): bigint {
  let q = a / b;
  if (a % b !== 0n && (a < 0n) !== (b < 0n)) q -= 1n;
  return q;
}

/** Compute an integer remainder with the divisor's sign. */
export function floorMod(a: bigint, b: bigint): bigint {
  let m = a % b;
  if (m !== 0n && (m < 0n) !== (b < 0n)) m += b;
  return m;
}

/** int / int with CPython-grade rounding. */
export function intTrueDiv(a: bigint, b: bigint): number {
  const SAFE = 9007199254740992n;
  if (a > -SAFE && a < SAFE && b > -SAFE && b < SAFE) {
    return Number(a) / Number(b);
  }
  const neg = (a < 0n) !== (b < 0n);
  let ua = a < 0n ? -a : a;
  const ub = b < 0n ? -b : b;
  // Scale so the quotient carries ~64 extra bits, then let Number() round.
  const q = (ua << 64n) / ub;
  const r = Number(q) * 2 ** -64;
  return neg ? -r : r;
}

function floatBinop(kind: string, a: number, b: number): Value {
  switch (kind) {
    case "add":
      return a + b;
    case "sub":
      return a - b;
    case "mul":
      return a * b;
    case "div":
      if (b === 0) throw diasporeErr("ZeroDivisionError", "float division by zero");
      return a / b;
    case "floordiv":
      if (b === 0) throw diasporeErr("ZeroDivisionError", "float floor division by zero");
      return Math.floor(a / b);
    case "mod": {
      if (b === 0) throw diasporeErr("ZeroDivisionError", "float modulo");
      let m = a % b;
      if (m !== 0 && (m < 0) !== (b < 0)) m += b;
      return m;
    }
    case "pow":
      if (a === 0 && b < 0) {
        throw diasporeErr("ZeroDivisionError", "0.0 cannot be raised to a negative power");
      }
      return Math.pow(a, b);
  }
  throw diasporeErr(
    "TypeError",
    `unsupported operand type(s) for ${BIN_SYM[kind]}: 'float' and 'float'`,
  );
}

const UNARY_SYM: Record<string, string> = { neg: "-", pos: "+", invert: "~" };

/** Evaluate a unary operator, including user-defined overloads. */
export function* unary(I: Interp, kind: string, v: Value): G<Value> {
  if (v instanceof DiasporeInstance) {
    if (kind === "neg" && v.cls.lookup("__neg__") !== undefined) {
      return yield* I.callDunder(v, "__neg__", []);
    }
    throw diasporeErr("TypeError", `bad operand type for unary ${UNARY_SYM[kind]}: '${v.cls.name}'`);
  }
  const n = asIndex(v);
  switch (kind) {
    case "neg":
      if (n !== null) return -n;
      if (typeof v === "number") return -v;
      if (v instanceof Complex) return new Complex(-v.re, -v.im);
      break;
    case "pos":
      if (n !== null) return n;
      if (typeof v === "number") return v;
      if (v instanceof Complex) return v;
      break;
    case "invert":
      if (n !== null) return ~n;
      break;
  }
  throw diasporeErr("TypeError", `bad operand type for unary ${UNARY_SYM[kind]}: '${typeName(v)}'`);
}

// --------------------------------------------------------------------------
// comparisons

const CMP_SYM: Record<string, string> = { lt: "<", le: "<=", gt: ">", ge: ">=" };
const CMP_DUNDER: Record<string, string> = {
  lt: "__lt__",
  le: "__le__",
  gt: "__gt__",
  ge: "__ge__",
};

/** Evaluate a comparison using Python value semantics. */
export function* compare(I: Interp, kind: string, l: Value, r: Value): G<boolean> {
  switch (kind) {
    case "is":
      return isIdentical(l, r);
    case "isnot":
      return !isIdentical(l, r);
    case "in":
      return yield* contains(I, r, l);
    case "notin":
      return !(yield* contains(I, r, l));
    case "eq":
      return yield* eq(I, l, r);
    case "ne": {
      if (l instanceof DiasporeInstance && l.cls.lookup("__ne__") !== undefined) {
        return yield* I.truth(yield* I.callDunder(l, "__ne__", [r]));
      }
      return !(yield* eq(I, l, r));
    }
  }
  // ordering
  if (l instanceof DiasporeInstance) {
    if (l.cls.lookup(CMP_DUNDER[kind]) !== undefined) {
      return yield* I.truth(yield* I.callDunder(l, CMP_DUNDER[kind], [r]));
    }
    throw orderTypeError(kind, l, r);
  }
  if (r instanceof DiasporeInstance) throw orderTypeError(kind, l, r);
  const [c, ordered] = yield* cmp3(I, l, r, kind);
  if (!ordered) return false; // NaN / incomparable sets
  switch (kind) {
    case "lt":
      return c < 0;
    case "le":
      return c <= 0;
    case "gt":
      return c > 0;
    case "ge":
      return c >= 0;
  }
  throw diasporeErr("TypeError", `unknown comparison ${kind}`);
}

function orderTypeError(kind: string, l: Value, r: Value): DiasporeError {
  return diasporeErr(
    "TypeError",
    `'${CMP_SYM[kind]}' not supported between instances of '${typeName(l)}' and '${typeName(r)}'`,
  );
}

/** Orders two non-NaN numbers exactly (bigint/bool/float mixes). */
export function numCmp(l: Value, r: Value): number {
  const li = asIndex(l);
  const ri = asIndex(r);
  if (li !== null && ri !== null) return li < ri ? -1 : li > ri ? 1 : 0;
  if (li !== null) return bigFloatCmp(li, r as number);
  if (ri !== null) return -bigFloatCmp(ri, l as number);
  const a = l as number;
  const b = r as number;
  return a < b ? -1 : a > b ? 1 : 0;
}

function* cmp3(I: Interp, l: Value, r: Value, kind: string): G<[number, boolean]> {
  if (isNumber(l) && isNumber(r)) {
    if (typeof l === "number" && Number.isNaN(l)) return [0, false];
    if (typeof r === "number" && Number.isNaN(r)) return [0, false];
    return [numCmp(l, r), true];
  }
  if (typeof l === "string" && typeof r === "string") return [strCmp(l, r), true];
  if (l instanceof Uint8Array && r instanceof Uint8Array) return [bytesCmp(l, r), true];
  if (l instanceof DiasporeList && r instanceof DiasporeList) {
    return yield* cmpSeq(I, l.items, r.items);
  }
  if (l instanceof DiasporeTuple && r instanceof DiasporeTuple) {
    return yield* cmpSeq(I, l.items, r.items);
  }
  if (l instanceof DiasporeSet && r instanceof DiasporeSet) {
    const sub = setIsSubset(l, r);
    const sup = setIsSubset(r, l);
    if (sub && sup) return [0, true];
    if (sub) return [-1, true];
    if (sup) return [1, true];
    return [0, false]; // incomparable sets: every ordering is False
  }
  throw orderTypeError(kind, l, r);
}

function* cmpSeq(I: Interp, a: Value[], b: Value[]): G<[number, boolean]> {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const same = yield* eqElem(I, a[i], b[i]);
    if (!same) {
      const lt = yield* compare(I, "lt", a[i], b[i]);
      return [lt ? -1 : 1, true];
    }
  }
  if (a.length < b.length) return [-1, true];
  if (a.length > b.length) return [1, true];
  return [0, true];
}

/** Test subset membership using canonical sandbox hash keys. */
export function setIsSubset(a: DiasporeSet, b: DiasporeSet): boolean {
  for (const hk of a.map.keys()) {
    if (!b.map.has(hk)) return false;
  }
  return true;
}

/** Compare value identity using the runtime's immutable-value model. */
export function isIdentical(l: Value, r: Value): boolean {
  switch (typeof l) {
    case "bigint":
      return typeof r === "bigint" && l === r;
    case "boolean":
      return typeof r === "boolean" && l === r;
    case "number":
      return typeof r === "number" && Object.is(l, r);
    case "string":
      return typeof r === "string" && l === r;
  }
  if (l instanceof PyNoneT) return r instanceof PyNoneT;
  if (l instanceof Uint8Array) {
    return r instanceof Uint8Array && (l === r || (l.length === 0 && r.length === 0));
  }
  if (l instanceof DiasporeTuple) {
    // CPython interns the empty tuple, so `() is ()` is True.
    return (
      r instanceof DiasporeTuple && (l === r || (l.items.length === 0 && r.items.length === 0))
    );
  }
  return l === r;
}

/** Compare values for equality, including user-defined overloads. */
export function* eq(I: Interp, l: Value, r: Value): G<boolean> {
  if (l instanceof DiasporeInstance) {
    if (l.cls.lookup("__eq__") !== undefined) {
      return yield* I.truth(yield* I.callDunder(l, "__eq__", [r]));
    }
    if (r instanceof DiasporeInstance && r.cls.lookup("__eq__") !== undefined) {
      return yield* I.truth(yield* I.callDunder(r, "__eq__", [l]));
    }
    return isIdentical(l, r);
  }
  if (r instanceof DiasporeInstance) return yield* eq(I, r, l);
  if (l instanceof Complex || r instanceof Complex) {
    const lc = toComplex(l);
    const rc = toComplex(r);
    if (lc === null || rc === null) return false;
    return lc.re === rc.re && lc.im === rc.im;
  }
  if (isNumber(l) && isNumber(r)) {
    if (typeof l === "number" && Number.isNaN(l)) return false;
    if (typeof r === "number" && Number.isNaN(r)) return false;
    return numCmp(l, r) === 0;
  }
  if (typeof l === "string") return typeof r === "string" && l === r;
  if (l instanceof Uint8Array) return r instanceof Uint8Array && bytesEq(l, r);
  if (l instanceof PyNoneT) return r instanceof PyNoneT;
  if (l instanceof DiasporeList) {
    if (r instanceof DiasporeList) return yield* eqSeq(I, l.items, r.items);
    return false;
  }
  if (l instanceof DiasporeTuple) {
    if (r instanceof DiasporeTuple) return yield* eqSeq(I, l.items, r.items);
    return false;
  }
  if (l instanceof DiasporeDict) {
    if (!(r instanceof DiasporeDict) || l.size !== r.size) return false;
    for (const [hk, e] of l.map) {
      const bv = r.map.get(hk);
      if (bv === undefined) return false;
      if (!(yield* eqElem(I, e.val, bv.val))) return false;
    }
    return true;
  }
  if (l instanceof DiasporeSet) {
    if (!(r instanceof DiasporeSet) || l.size !== r.size) return false;
    return setIsSubset(l, r);
  }
  if (l instanceof DiasporeRange) {
    if (!(r instanceof DiasporeRange)) return false;
    const la = l.len();
    if (la !== r.len()) return false;
    if (la === 0n) return true;
    if (l.start !== r.start) return false;
    return la === 1n || l.step === r.step;
  }
  return isIdentical(l, r);
}

/** Element equality inside containers (CPython's RichCompareBool: identity
 * implies equality: [nan] == [nan] while nan == nan stays False). */
export function* eqElem(I: Interp, a: Value, b: Value): G<boolean> {
  if (isIdentical(a, b)) return true;
  if (typeof a === "number" && typeof b === "number" && Number.isNaN(a) && Number.isNaN(b)) {
    return true;
  }
  return yield* eq(I, a, b);
}

function* eqSeq(I: Interp, a: Value[], b: Value[]): G<boolean> {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (!(yield* eqElem(I, a[i], b[i]))) return false;
  }
  return true;
}

/** Test membership in a container or iterable. */
export function* contains(I: Interp, container: Value, item: Value): G<boolean> {
  if (container instanceof DiasporeInstance) {
    if (container.cls.lookup("__contains__") !== undefined) {
      return yield* I.truth(yield* I.callDunder(container, "__contains__", [item]));
    }
    if (container.prim !== null && container.cls.lookup("__iter__") === undefined) {
      return yield* contains(I, container.prim, item);
    }
    return yield* iterContains(I, container, item);
  }
  if (typeof container === "string") {
    if (typeof item !== "string") {
      throw diasporeErr(
        "TypeError",
        `'in <string>' requires string as left operand, not ${typeName(item)}`,
      );
    }
    return container.includes(item);
  }
  if (container instanceof Uint8Array) {
    if (item instanceof Uint8Array) return bytesIndexOf(container, item) >= 0;
    const n = asIndex(item);
    if (n !== null) {
      if (n < 0n || n > 255n) throw diasporeErr("ValueError", "byte must be in range(0, 256)");
      return container.indexOf(Number(n)) >= 0;
    }
    throw diasporeErr("TypeError", `a bytes-like object is required, not '${typeName(item)}'`);
  }
  if (container instanceof DiasporeList || container instanceof DiasporeTuple) {
    for (const x of container.items) {
      if (yield* eqElem(I, x, item)) return true;
    }
    return false;
  }
  if (container instanceof DiasporeDict) return container.has(item);
  if (container instanceof DiasporeSet) return container.has(item);
  if (container instanceof DiasporeRange) {
    let n = asIndex(item);
    if (n === null) {
      if (typeof item === "number") {
        if (!Number.isInteger(item)) return false;
        n = BigInt(item);
      } else {
        return false;
      }
    }
    const r = container;
    if (r.step > 0n) return n >= r.start && n < r.stop && (n - r.start) % r.step === 0n;
    return n <= r.start && n > r.stop && (r.start - n) % -r.step === 0n;
  }
  if (container instanceof DiasporeIterBase) {
    return yield* iterContains(I, container, item);
  }
  throw diasporeErr("TypeError", `argument of type '${typeName(container)}' is not iterable`);
}

function* iterContains(I: Interp, container: Value, item: Value): G<boolean> {
  const it = yield* getIter(I, container);
  for (;;) {
    const [x, ok] = yield* it.next();
    if (!ok) return false;
    if (yield* eqElem(I, x, item)) return true;
  }
}

// --------------------------------------------------------------------------
// truthiness & length

/** Evaluate Python truthiness, including user-defined hooks. */
export function* truth(I: Interp, v: Value): G<boolean> {
  switch (typeof v) {
    case "boolean":
      return v;
    case "bigint":
      return v !== 0n;
    case "number":
      return v !== 0; // NaN is truthy
    case "string":
      return v !== "";
  }
  if (v instanceof PyNoneT) return false;
  if (v instanceof Uint8Array) return v.length > 0;
  if (v instanceof DiasporeList) return v.items.length > 0;
  if (v instanceof DiasporeTuple) return v.items.length > 0;
  if (v instanceof DiasporeDict) return v.size > 0;
  if (v instanceof DiasporeSet) return v.size > 0;
  if (v instanceof DiasporeRange) return v.len() > 0n;
  if (v instanceof DiasporeInstance) {
    if (
      v.cls.lookup("__bool__") === undefined &&
      v.cls.lookup("__len__") === undefined &&
      v.prim !== null
    ) {
      return yield* truth(I, v.prim);
    }
    if (v.cls.lookup("__bool__") !== undefined) {
      const r = yield* I.callDunder(v, "__bool__", []);
      if (typeof r !== "boolean") {
        throw diasporeErr("TypeError", `__bool__ should return bool, returned ${typeName(r)}`);
      }
      return r;
    }
    if (v.cls.lookup("__len__") !== undefined) {
      return (yield* diasporeLen(I, v)) !== 0n;
    }
    return true;
  }
  return true;
}

/** Obtain the length of a sized sandbox value. */
export function* diasporeLen(I: Interp, v: Value): G<bigint> {
  if (typeof v === "string") return BigInt(strLen(v));
  if (v instanceof Uint8Array) return BigInt(v.length);
  if (v instanceof DiasporeList) return BigInt(v.items.length);
  if (v instanceof DiasporeTuple) return BigInt(v.items.length);
  if (v instanceof DiasporeDict) return BigInt(v.size);
  if (v instanceof DiasporeSet) return BigInt(v.size);
  if (v instanceof DiasporeRange) return v.len();
  if (v instanceof DiasporeInstance) {
    if (v.cls.lookup("__len__") === undefined && v.prim !== null) {
      return yield* diasporeLen(I, v.prim);
    }
    const r = yield* I.callDunderOrErr(
      v,
      "__len__",
      [],
      `object of type '${v.cls.name}' has no len()`,
    );
    if (typeof r === "bigint") return r;
    throw diasporeErr("TypeError", "__len__ should return an int");
  }
  throw diasporeErr("TypeError", `object of type '${typeName(v)}' has no len()`);
}

// --------------------------------------------------------------------------
// indexing & slicing

function seqIndexCheck(n: number, idx: bigint, kindMsg: string): number {
  let i = idx;
  if (i < 0n) i += BigInt(n);
  if (i < 0n || i >= BigInt(n)) throw diasporeErr("IndexError", kindMsg);
  return Number(i);
}

/** Read an index or slice from a sandbox value. */
export function* getIndex(I: Interp, o: Value, k: Value): G<Value> {
  if (o instanceof DiasporeInstance) {
    if (o.cls.lookup("__getitem__") === undefined && o.prim !== null) {
      if (
        o.prim instanceof DiasporeDict &&
        !o.prim.has(k) &&
        o.cls.lookup("__missing__") !== undefined
      ) {
        return yield* I.callDunder(o, "__missing__", [k]);
      }
      return yield* getIndex(I, o.prim, k);
    }
    return yield* I.callDunderOrErr(
      o,
      "__getitem__",
      [k],
      `'${o.cls.name}' object is not subscriptable`,
    );
  }
  if (o instanceof DiasporeList) {
    if (k instanceof DiasporeSlice) {
      const [start, stop, step] = sliceIndices(k, o.items.length);
      return new DiasporeList(sliceSeq(o.items, start, stop, step));
    }
    const n = asIndex(k);
    if (n === null) {
      throw diasporeErr("TypeError", `list indices must be integers or slices, not ${typeName(k)}`);
    }
    return o.items[seqIndexCheck(o.items.length, n, "list index out of range")];
  }
  if (o instanceof DiasporeTuple) {
    if (k instanceof DiasporeSlice) {
      const [start, stop, step] = sliceIndices(k, o.items.length);
      return new DiasporeTuple(sliceSeq(o.items, start, stop, step));
    }
    const n = asIndex(k);
    if (n === null) {
      throw diasporeErr("TypeError", `tuple indices must be integers or slices, not ${typeName(k)}`);
    }
    return o.items[seqIndexCheck(o.items.length, n, "tuple index out of range")];
  }
  if (typeof o === "string") {
    const rs = hasAstral(o) ? chars(o) : null;
    const len = rs !== null ? rs.length : o.length;
    if (k instanceof DiasporeSlice) {
      const [start, stop, step] = sliceIndices(k, len);
      let b = "";
      forSlice(start, stop, step, (i) => {
        b += rs !== null ? rs[i] : o[i];
      });
      return b;
    }
    const n = asIndex(k);
    if (n === null) {
      throw diasporeErr("TypeError", `string indices must be integers, not '${typeName(k)}'`);
    }
    const i = seqIndexCheck(len, n, "string index out of range");
    return rs !== null ? rs[i] : o[i];
  }
  if (o instanceof Uint8Array) {
    if (k instanceof DiasporeSlice) {
      const [start, stop, step] = sliceIndices(k, o.length);
      const out: number[] = [];
      forSlice(start, stop, step, (i) => out.push(o[i]));
      return new Uint8Array(out);
    }
    const n = asIndex(k);
    if (n === null) {
      throw diasporeErr("TypeError", `byte indices must be integers or slices, not ${typeName(k)}`);
    }
    return BigInt(o[seqIndexCheck(o.length, n, "index out of range")]);
  }
  if (o instanceof DiasporeDict) {
    if (k instanceof DiasporeSlice) throw diasporeErr("TypeError", "unhashable type: 'slice'");
    const v = o.get(k);
    if (v === undefined) throw new DiasporeError(makeExc("KeyError", k));
    return v;
  }
  if (o instanceof DiasporeRange) {
    if (k instanceof DiasporeSlice) return sliceRange(o, k);
    const n = asIndex(k);
    if (n === null) {
      throw diasporeErr("TypeError", `range indices must be integers or slices, not ${typeName(k)}`);
    }
    const i = seqIndexCheck(Number(o.len()), n, "range object index out of range");
    return o.start + BigInt(i) * o.step;
  }
  throw diasporeErr("TypeError", `'${typeName(o)}' object is not subscriptable`);
}

/** Assign an index or slice on a mutable sandbox value. */
export function* setIndex(I: Interp, o: Value, k: Value, v: Value): G<void> {
  if (o instanceof DiasporeInstance) {
    if (o.cls.lookup("__setitem__") === undefined && o.prim !== null) {
      yield* setIndex(I, o.prim, k, v);
      return;
    }
    yield* I.callDunderOrErr(
      o,
      "__setitem__",
      [k, v],
      `'${o.cls.name}' object does not support item assignment`,
    );
    return;
  }
  if (o instanceof DiasporeList) {
    if (k instanceof DiasporeSlice) {
      yield* setListSlice(I, o, k, v);
      return;
    }
    const n = asIndex(k);
    if (n === null) {
      throw diasporeErr("TypeError", `list indices must be integers or slices, not ${typeName(k)}`);
    }
    o.items[seqIndexCheck(o.items.length, n, "list assignment index out of range")] = v;
    return;
  }
  if (o instanceof DiasporeDict) {
    if (k instanceof DiasporeSlice) throw diasporeErr("TypeError", "unhashable type: 'slice'");
    o.set(k, v);
    return;
  }
  throw diasporeErr("TypeError", `'${typeName(o)}' object does not support item assignment`);
}

function* setListSlice(I: Interp, l: DiasporeList, sl: DiasporeSlice, v: Value): G<void> {
  const items = yield* iterToArray(I, v);
  const [start, stop0, step] = sliceIndices(sl, l.items.length);
  if (step === 1) {
    const stop = stop0 < start ? start : stop0;
    l.items = [...l.items.slice(0, start), ...items, ...l.items.slice(stop)];
    return;
  }
  const idxs: number[] = [];
  forSlice(start, stop0, step, (i) => idxs.push(i));
  if (idxs.length !== items.length) {
    throw diasporeErr(
      "ValueError",
      `attempt to assign sequence of size ${items.length} to extended slice of size ${idxs.length}`,
    );
  }
  for (let j = 0; j < idxs.length; j++) l.items[idxs[j]] = items[j];
}

/** Delete an index or slice from a mutable sandbox value. */
export function* delIndex(I: Interp, o: Value, k: Value): G<void> {
  if (o instanceof DiasporeInstance) {
    if (o.cls.lookup("__delitem__") === undefined && o.prim !== null) {
      yield* delIndex(I, o.prim, k);
      return;
    }
    yield* I.callDunderOrErr(
      o,
      "__delitem__",
      [k],
      `'${o.cls.name}' object doesn't support item deletion`,
    );
    return;
  }
  if (o instanceof DiasporeList) {
    if (k instanceof DiasporeSlice) {
      const [start, stop, step] = sliceIndices(k, o.items.length);
      const kill = new Set<number>();
      forSlice(start, stop, step, (i) => kill.add(i));
      o.items = o.items.filter((_, i) => !kill.has(i));
      return;
    }
    const n = asIndex(k);
    if (n === null) {
      throw diasporeErr("TypeError", `list indices must be integers or slices, not ${typeName(k)}`);
    }
    o.items.splice(seqIndexCheck(o.items.length, n, "list assignment index out of range"), 1);
    return;
  }
  if (o instanceof DiasporeDict) {
    if (o.delete(k) === undefined) throw new DiasporeError(makeExc("KeyError", k));
    return;
  }
  throw diasporeErr("TypeError", `'${typeName(o)}' object doesn't support item deletion`);
}

/** Resolves a slice against a sequence of length n, CPython-style. */
export function sliceIndices(sl: DiasporeSlice, n: number): [number, number, number] {
  const getInt = (v: Value): bigint | null => {
    if (v instanceof PyNoneT) return null;
    const i = asIndex(v);
    if (i === null) {
      throw diasporeErr(
        "TypeError",
        "slice indices must be integers or None or have an __index__ method",
      );
    }
    return i;
  };
  const stepV = getInt(sl.step);
  let step = 1;
  if (stepV !== null) {
    if (stepV === 0n) throw diasporeErr("ValueError", "slice step cannot be zero");
    step = clampBig(stepV);
  }
  const clamp = (v0: bigint): number => {
    let v = v0;
    if (v < 0n) {
      v += BigInt(n);
      if (v < 0n) return step < 0 ? -1 : 0;
    } else if (v >= BigInt(n)) {
      return step < 0 ? n - 1 : n;
    }
    return Number(v);
  };
  const lowV = getInt(sl.lower);
  const upV = getInt(sl.upper);
  let start: number;
  let stop: number;
  if (step > 0) {
    start = 0;
    stop = n;
  } else {
    start = n - 1;
    stop = -1;
  }
  if (lowV !== null) start = clamp(lowV);
  if (upV !== null) stop = clamp(upV);
  return [start, stop, step];
}

function clampBig(v: bigint): number {
  if (v > 9007199254740991n) return 9007199254740991;
  if (v < -9007199254740991n) return -9007199254740991;
  return Number(v);
}

/** Visit the indexes of a resolved slice. */
export function forSlice(start: number, stop: number, step: number, fn: (i: number) => void): void {
  if (step > 0) {
    for (let i = start; i < stop; i += step) fn(i);
  } else {
    for (let i = start; i > stop; i += step) fn(i);
  }
}

/** Copy the values selected by a resolved slice. */
export function sliceSeq(items: Value[], start: number, stop: number, step: number): Value[] {
  const out: Value[] = [];
  forSlice(start, stop, step, (i) => out.push(items[i]));
  return out;
}

function sliceRange(r: DiasporeRange, sl: DiasporeSlice): DiasporeRange {
  const n = Number(r.len());
  const [start, stop, step] = sliceIndices(sl, n);
  let count = 0;
  forSlice(start, stop, step, () => count++);
  const newStart = r.start + BigInt(start) * r.step;
  const newStep = r.step * BigInt(step);
  const newStop = newStart + BigInt(count) * newStep;
  return new DiasporeRange(newStart, newStop, newStep);
}

// --------------------------------------------------------------------------
// repr / str

/** Render repr with recursion protection for containers. */
export function* reprOf(I: Interp, v: Value): G<string> {
  if (v instanceof DiasporeList || v instanceof DiasporeTuple || v instanceof DiasporeDict) {
    if (I.repring.has(v)) {
      if (v instanceof DiasporeList) return "[...]";
      if (v instanceof DiasporeTuple) return "(...)";
      return "{...}";
    }
    I.repring.add(v);
    try {
      if (v instanceof DiasporeList) {
        return "[" + (yield* reprJoin(I, v.items)) + "]";
      }
      if (v instanceof DiasporeTuple) {
        if (v.items.length === 1) return "(" + (yield* reprOf(I, v.items[0])) + ",)";
        return "(" + (yield* reprJoin(I, v.items)) + ")";
      }
      const parts: string[] = [];
      for (const [key, val] of v.entries()) {
        parts.push((yield* reprOf(I, key)) + ": " + (yield* reprOf(I, val)));
      }
      return "{" + parts.join(", ") + "}";
    } finally {
      I.repring.delete(v);
    }
  }
  switch (typeof v) {
    case "bigint":
      return v.toString();
    case "number":
      return pyFloatStr(v);
    case "boolean":
      return v ? "True" : "False";
    case "string":
      return reprString(v);
  }
  if (v instanceof Complex) return complexRepr(v);
  if (v instanceof PyNoneT) return "None";
  if (v instanceof PyEllipsisT) return "Ellipsis";
  if (v instanceof Uint8Array) return reprBytes(v);
  if (v instanceof DiasporeRange) return reprRange(v);
  if (v instanceof DiasporeSet) {
    if (v.size === 0) return "set()";
    return "{" + (yield* reprJoin(I, v.elems())) + "}";
  }
  if (v instanceof DiasporeSlice) {
    const l = yield* reprOf(I, v.lower);
    const u = yield* reprOf(I, v.upper);
    const s = yield* reprOf(I, v.step);
    return `slice(${l}, ${u}, ${s})`;
  }
  if (v instanceof DiasporeInstance) {
    if (v.cls.issub(BASE_EXC) && v.cls.lookup("__repr__") === undefined) {
      return v.cls.name + "(" + (yield* reprJoin(I, excArgs(v))) + ")";
    }
    if (v.cls.lookup("__repr__") !== undefined) {
      const r = yield* I.callDunder(v, "__repr__", []);
      if (typeof r !== "string") throw diasporeErr("TypeError", "__repr__ returned non-string");
      return r;
    }
    if (v.prim !== null) return yield* reprOf(I, v.prim);
    return `<${v.cls.name} object>`;
  }
  if (v instanceof DiasporeClass) return `<class '${v.name}'>`;
  if (v instanceof BuiltinType) return `<class '${v.name}'>`;
  if (v instanceof DiasporeFunction) return `<function ${v.name}>`;
  if (v instanceof BoundMethod) return `<bound method ${callableName(v.fn)}>`;
  if (v instanceof DiasporeModule || v instanceof NativeModule) return `<module '${v.name}'>`;
  if (v instanceof DiasporeFile) return "<file>";
  if (v instanceof NativeObject) return `<${v.kind}>`;
  if (v instanceof Builtin) return `<builtin ${v.name}>`;
  if (v instanceof DiasporeGenerator) return `<generator ${v.fn.name}>`;
  return `<${typeName(v)}>`;
}

function* reprJoin(I: Interp, items: Value[]): G<string> {
  const parts: string[] = [];
  for (const x of items) parts.push(yield* reprOf(I, x));
  return parts.join(", ");
}

/** Obtain a diagnostic name for a callable value. */
export function callableName(f: Value): string {
  if (f instanceof DiasporeFunction) return f.name;
  if (f instanceof Builtin) return f.name;
  if (f instanceof DiasporeClass) return f.name;
  if (f instanceof BuiltinType) return f.name;
  return "function";
}

/** Render str, including user-defined conversion hooks. */
export function* strOf(I: Interp, v: Value): G<string> {
  if (typeof v === "string") return v;
  if (v instanceof DiasporeInstance) {
    if (v.cls.issub(BASE_EXC) && v.cls.lookup("__str__") === undefined) {
      return yield* excStr(I, v);
    }
    for (const name of ["__str__", "__repr__"]) {
      if (v.cls.lookup(name) !== undefined) {
        const r = yield* I.callDunder(v, name, []);
        if (typeof r !== "string") {
          throw diasporeErr("TypeError", `${name} returned non-string (${typeName(r)})`);
        }
        return r;
      }
    }
    if (v.prim !== null) return yield* strOf(I, v.prim);
    return `<${v.cls.name} object>`;
  }
  return yield* reprOf(I, v);
}

/** ascii(v): repr with non-ASCII backslash-escaped (CPython escape rules). */
export function* asciiOf(I: Interp, v: Value): G<string> {
  const r = yield* reprOf(I, v);
  let out = "";
  for (const ch of r) {
    const c = ch.codePointAt(0) as number;
    if (c < 0x80) out += ch;
    else if (c <= 0xff) out += "\\x" + c.toString(16).padStart(2, "0");
    else if (c <= 0xffff) out += "\\u" + c.toString(16).padStart(4, "0");
    else out += "\\U" + c.toString(16).padStart(8, "0");
  }
  return out;
}

/** str(e) for an exception instance without a user __str__. */
export function* excStr(I: Interp, inst: DiasporeInstance): G<string> {
  if (inst.cls.lookup("__str__") !== undefined) return yield* strOf(I, inst);
  const args = excArgs(inst);
  const keyError = EXC_CLASSES.get("KeyError") as DiasporeClass;
  if (inst.cls.issub(keyError) && args.length === 1) {
    return yield* reprOf(I, args[0]);
  }
  if (args.length === 0) return "";
  if (args.length === 1) return yield* strOf(I, args[0]);
  return yield* reprOf(I, new DiasporeTuple(args));
}

// --------------------------------------------------------------------------
// sorting

/** Stable merge sort mirroring diaspore-go's stableSort (comparison order and
 * therefore error messages match). */
export function* stableSort(
  items: Value[],
  less: (a: Value, b: Value) => G<boolean>,
): G<void> {
  const n = items.length;
  if (n < 2) return;
  const buf: Value[] = new Array(n);
  function* rec(lo: number, hi: number): G<void> {
    if (hi - lo < 2) return;
    const mid = (lo + hi) >> 1;
    yield* rec(lo, mid);
    yield* rec(mid, hi);
    let i = lo;
    let j = mid;
    let k = lo;
    while (i < mid && j < hi) {
      if (yield* less(items[j], items[i])) {
        buf[k++] = items[j++];
      } else {
        buf[k++] = items[i++];
      }
    }
    while (i < mid) buf[k++] = items[i++];
    while (j < hi) buf[k++] = items[j++];
    for (let t = lo; t < hi; t++) items[t] = buf[t];
  }
  yield* rec(0, n);
}

/** Sorts items in place, stably, with optional key and reverse (Python
 * semantics: reverse is applied around the stable sort). */
export function* sortValues(
  I: Interp,
  items: Value[],
  key: Value | null,
  reverse: boolean,
): G<void> {
  let useKey = key !== null;
  if (key instanceof PyNoneT) useKey = false;
  const less = function* (a: Value, b: Value): G<boolean> {
    return yield* compare(I, "lt", a, b);
  };
  if (reverse) items.reverse();
  if (useKey) {
    interface KV {
      k: Value;
      v: Value;
    }
    const dec: Value[] = new Array(items.length);
    for (let i = 0; i < items.length; i++) {
      const k = yield* I.invoke(key as Value, [items[i]], new Map());
      dec[i] = { k, v: items[i] } as unknown as Value;
    }
    yield* stableSort(dec, function* (a, b) {
      return yield* less((a as unknown as KV).k, (b as unknown as KV).k);
    });
    for (let i = 0; i < items.length; i++) items[i] = (dec[i] as unknown as KV).v;
  } else {
    yield* stableSort(items, less);
  }
  if (reverse) items.reverse();
}
