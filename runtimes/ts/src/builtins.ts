/* Builtin functions and type constructors (spec 9). */

import * as nodeFs from "node:fs";

import {
  type Value,
  type G,
  type KwArgs,
  NONE,
  ELLIPSIS,
  PyNoneT,
  DiasporeList,
  DiasporeTuple,
  DiasporeDict,
  DiasporeSet,
  DiasporeRange,
  DiasporeSlice,
  DiasporeClass,
  DiasporeInstance,
  DiasporeFunction,
  DiasporeModule,
  NativeModule,
  BoundMethod,
  StaticMethodV,
  ClassMethodV,
  SUPER_MARKER,
  Builtin,
  BuiltinType,
  DiasporeIterBase,
  Property,
  Complex,
  CodeObject,
  typeName,
  asIndex,
  asFloat,
  isIntKind,
  intFromFloat,
  hashKey,
  objId,
  chars,
  diasporeErr,
  DiasporeError,
  makeExc,
  excIsA,
  OBJECT,
  EXC_CLASSES,
} from "./values.ts";
import {
  reprString,
  roundFloatDigits,
  roundFloatToInt,
} from "./format.ts";
import {
  getIter,
  iterToArray,
  DiasporeGenerator,
  InstIter,
  SeqIter,
  MapIter,
  FilterIter,
  ZipIter,
  EnumIter,
} from "./iter.ts";
import { argCount, needArgs, chargeSized, wantStr } from "./helpers.ts";
import { parseComplex, complexRepr } from "./format.ts";
import { asciiOf } from "./ops.ts";
import type { Interp } from "./interp.ts";
import { biOpen } from "./natives.ts";

/** Wrap an implementation as a sandbox builtin. */
export function bi(name: string, fn: (I: Interp, args: Value[], kw: KwArgs) => G<Value>): Builtin {
  return new Builtin(name, fn);
}

const MISSING = Symbol("missing");

// --------------------------------------------------------------------------

/** Construct the builtin namespace for an interpreter. */
export function makeBuiltins(I: Interp): Map<string, Value> {
  const types = new Map<string, BuiltinType>([
    ["int", new BuiltinType("int", ctorInt)],
    ["float", new BuiltinType("float", ctorFloat)],
    ["bool", new BuiltinType("bool", ctorBool)],
    ["str", new BuiltinType("str", ctorStr)],
    ["bytes", new BuiltinType("bytes", ctorBytes)],
    ["list", new BuiltinType("list", ctorList)],
    ["tuple", new BuiltinType("tuple", ctorTuple)],
    ["dict", new BuiltinType("dict", ctorDict)],
    ["set", new BuiltinType("set", ctorSet)],
    ["range", new BuiltinType("range", ctorRange)],
    ["complex", new BuiltinType("complex", ctorComplex)],
    ["type", new BuiltinType("type", ctorType)],
    [
      "NoneType",
      new BuiltinType("NoneType", function* (): G<Value> {
        return NONE;
      }),
    ],
  ]);
  I.types = types;

  const b = new Map<string, Value>();
  const add = (name: string, fn: (I: Interp, args: Value[], kw: KwArgs) => G<Value>) =>
    b.set(name, bi(name, fn));

  add("print", biPrint);
  add("len", function* (I, args): G<Value> {
    argCount("len", args, 1, 1);
    return yield* I.diasporeLen(args[0]);
  });
  add("enumerate", biEnumerate);
  add("zip", biZip);
  add("map", biMap);
  add("filter", biFilter);
  add("reversed", biReversed);
  add("sorted", biSorted);
  add("sum", biSum);
  add("min", (I, args, kw) => minMax(I, "min", args, kw, true));
  add("max", (I, args, kw) => minMax(I, "max", args, kw, false));
  add("any", biAny);
  add("all", biAll);
  add("abs", biAbs);
  add("round", biRound);
  add("divmod", biDivmod);
  add("pow", biPow);
  add("ord", biOrd);
  add("chr", biChr);
  add("hex", baseConv("hex", "0x", 16));
  add("bin", baseConv("bin", "0b", 2));
  add("oct", baseConv("oct", "0o", 8));
  add("repr", function* (I, args): G<Value> {
    argCount("repr", args, 1, 1);
    return yield* I.repr(args[0]);
  });
  add("ascii", function* (I, args): G<Value> {
    argCount("ascii", args, 1, 1);
    return yield* asciiOf(I, args[0]);
  });
  add("isinstance", biIsinstance);
  add("issubclass", biIssubclass);
  add("callable", biCallable);
  add("hash", biHash);
  add("id", biID);
  add("iter", function* (I, args): G<Value> {
    argCount("iter", args, 1, 1);
    return yield* getIter(I, args[0]);
  });
  add("next", biNext);
  add("getattr", biGetattr);
  add("setattr", function* (I, args): G<Value> {
    argCount("setattr", args, 3, 3);
    const name = attrName(args[1]);
    yield* I.setAttr(args[0], name, args[2]);
    return NONE;
  });
  add("hasattr", function* (I, args): G<Value> {
    argCount("hasattr", args, 2, 2);
    const name = attrName(args[1]);
    try {
      yield* I.getAttr(args[0], name);
      return true;
    } catch (e) {
      if (e instanceof DiasporeError && excIsA(e.value, "AttributeError")) return false;
      throw e;
    }
  });
  add("input", biInput);
  add("open", biOpen);
  add("exit", biExit);
  add("staticmethod", function* (_I, args): G<Value> {
    argCount("staticmethod", args, 1, 1);
    return new StaticMethodV(args[0]);
  });
  add("classmethod", function* (_I, args): G<Value> {
    argCount("classmethod", args, 1, 1);
    return new ClassMethodV(args[0]);
  });
  add("format", function* (I, args): G<Value> {
    argCount("format", args, 1, 2);
    let spec = "";
    if (args.length === 2) {
      if (typeof args[1] !== "string") {
        throw diasporeErr("TypeError", `format() argument 2 must be str, not ${typeName(args[1])}`);
      }
      spec = args[1];
    }
    return yield* I.formatValue(args[0], spec);
  });
  b.set("super", SUPER_MARKER);
  b.set("type", types.get("type") as BuiltinType);
  b.set("object", OBJECT);
  b.set("Ellipsis", ELLIPSIS);
  // Unspellable helper behind the compiler's `await x` lowering: resolves an
  // awaitable to the coroutine/generator that yield-from drives.
  b.set(
    ".await",
    bi(".await", function* (I2, args): G<Value> {
      argCount(".await", args, 1, 1);
      const x = args[0];
      if (x instanceof DiasporeGenerator) return x;
      if (x instanceof DiasporeInstance && x.cls.lookup("__await__") !== undefined) {
        const r = yield* I2.callDunder(x, "__await__", []);
        if (r instanceof DiasporeGenerator) return r;
        throw diasporeErr("TypeError", "__await__() must return a coroutine or generator");
      }
      throw diasporeErr("TypeError", `object ${typeName(x)} can't be used in 'await' expression`);
    }),
  );
  add("property", function* (_I, args, kw): G<Value> {
    argCount("property", args, 0, 4);
    const get = (i: number, name: string): Value | null => {
      const kv = kw.get(name);
      if (kv !== undefined) return kv instanceof PyNoneT ? null : kv;
      if (i < args.length) return args[i] instanceof PyNoneT ? null : args[i];
      return null;
    };
    return new Property(get(0, "fget"), get(1, "fset"), get(2, "fdel"), get(3, "doc"));
  });
  add("slice", function* (_I, args): G<Value> {
    argCount("slice", args, 1, 3);
    if (args.length === 1) return new DiasporeSlice(NONE, args[0], NONE);
    return new DiasporeSlice(args[0], args[1], args.length === 3 ? args[2] : NONE);
  });
  add("globals", function* (): G<Value> {
    throw diasporeErr("RuntimeError", "globals() unsupported here"); // frame-special-cased
  });
  add("locals", function* (): G<Value> {
    throw diasporeErr("RuntimeError", "locals() unsupported here"); // frame-special-cased
  });
  add("vars", biVars);
  add("dir", biDir);
  add("compile", function* (I, args): G<Value> {
    needArgs("compile", args, 1, 6);
    const filename = args.length > 1 && typeof args[1] === "string" ? args[1] : "<string>";
    const mode = args.length > 2 && typeof args[2] === "string" ? args[2] : "exec";
    return I.compileSnippet(args[0], filename, mode);
  });
  add("exec", function* (I, args): G<Value> {
    needArgs("exec", args, 1, 3);
    return yield* I.execSnippet(args[0], arg(args, 1), arg(args, 2), "exec");
  });
  add("eval", function* (I, args): G<Value> {
    needArgs("eval", args, 1, 3);
    return yield* I.execSnippet(args[0], arg(args, 1), arg(args, 2), "eval");
  });
  for (const [name, t] of types) {
    if (name !== "NoneType" && name !== "type") b.set(name, t);
  }
  for (const [name, cls] of EXC_CLASSES) b.set(name, cls);
  return b;
}

function arg(args: Value[], i: number): Value | null {
  if (i >= args.length) return null;
  return args[i] instanceof PyNoneT ? null : args[i];
}

function* biVars(_I: Interp, args: Value[]): G<Value> {
  if (args.length === 0) throw diasporeErr("TypeError", "vars() with no argument unsupported here");
  const o = args[0];
  const d = new DiasporeDict();
  if (o instanceof DiasporeInstance) {
    for (const [k, v] of o.d) d.setHK("s" + k, k, v);
    return d;
  }
  if (o instanceof DiasporeModule) {
    for (const [k, v] of o.frame.vars) d.setHK("s" + k, k, v);
    return d;
  }
  if (o instanceof DiasporeClass) {
    for (const [k, v] of o.ns) d.setHK("s" + k, k, v);
    return d;
  }
  throw diasporeErr("TypeError", "vars() argument must have __dict__");
}

function* biDir(_I: Interp, args: Value[]): G<Value> {
  if (args.length === 0) return new DiasporeList();
  const o = args[0];
  const names = new Set<string>();
  if (o instanceof DiasporeInstance) {
    for (const k of o.d.keys()) names.add(k);
    for (const c of o.cls.mro) for (const k of c.ns.keys()) names.add(k);
  } else if (o instanceof DiasporeModule) {
    for (const k of o.frame.vars.keys()) names.add(k);
  } else if (o instanceof DiasporeClass) {
    for (const c of o.mro) for (const k of c.ns.keys()) names.add(k);
  } else if (o instanceof NativeModule) {
    for (const k of o.ns.keys()) names.add(k);
  }
  const sorted = [...names].sort();
  return new DiasporeList(sorted.map((s) => s as Value));
}

// --------------------------------------------------------------------------
// output & input

function* biPrint(I: Interp, args: Value[], kw: KwArgs): G<Value> {
  let sep = " ";
  let end = "\n";
  for (const [k, v] of kw) {
    if (k === "sep") {
      if (!(v instanceof PyNoneT)) {
        if (typeof v !== "string") {
          throw diasporeErr("TypeError", `sep must be None or a string, not ${typeName(v)}`);
        }
        sep = v;
      }
    } else if (k === "end") {
      if (!(v instanceof PyNoneT)) {
        if (typeof v !== "string") {
          throw diasporeErr("TypeError", `end must be None or a string, not ${typeName(v)}`);
        }
        end = v;
      }
    } else {
      throw diasporeErr("TypeError", `'${k}' is an invalid keyword argument for print()`);
    }
  }
  if (I.replaying) {
    // replay re-executes past segments: still evaluate (str may raise),
    // but the output already happened in the original run
    for (const a of args) yield* I.str(a);
    return NONE;
  }
  // CPython streams arguments: if str() of a later arg raises, earlier args
  // and separators are already on stdout.
  for (let i = 0; i < args.length; i++) {
    if (i > 0) I.write(sep);
    I.write(yield* I.str(args[i]));
  }
  I.write(end);
  return NONE;
}

// Buffered synchronous stdin reader (used by input()).
let stdinBuf = Buffer.alloc(0);
let stdinEOF = false;

/** Read one buffered stdin line, or null at EOF. */
export function readLineSync(): string | null {
  for (;;) {
    const nl = stdinBuf.indexOf(0x0a);
    if (nl >= 0) {
      const line = stdinBuf.subarray(0, nl).toString("utf8");
      stdinBuf = stdinBuf.subarray(nl + 1);
      return line;
    }
    if (stdinEOF) {
      if (stdinBuf.length > 0) {
        const line = stdinBuf.toString("utf8");
        stdinBuf = Buffer.alloc(0);
        return line;
      }
      return null;
    }
    const chunk = Buffer.alloc(65536);
    let n = 0;
    try {
      n = nodeFs.readSync(0, chunk, 0, chunk.length, null);
    } catch (e) {
      const err = e as NodeJS.ErrnoException;
      if (err.code === "EAGAIN") continue;
      if (err.code === "EOF") n = 0;
      else throw e;
    }
    if (n === 0) {
      stdinEOF = true;
    } else {
      stdinBuf = Buffer.concat([stdinBuf, chunk.subarray(0, n)]);
    }
  }
}

function* biInput(I: Interp, args: Value[]): G<Value> {
  argCount("input", args, 0, 1);
  if (I.durable) {
    const [replayed, r] = I.replayNext("builtins", "input");
    if (replayed) return r;
  }
  if (args.length === 1 && !I.replaying) {
    I.write(yield* I.str(args[0]));
  }
  const line = readLineSync();
  if (line === null) {
    throw diasporeErr("RuntimeError", "EOF when reading a line");
  }
  if (I.durable) I.journal.push({ m: "builtins", f: "input", r: line });
  return line;
}

/** Raise SystemExit with the requested exit code. */
export function* biExit(_I: Interp, args: Value[]): G<Value> {
  argCount("exit", args, 0, 1);
  const code: Value = args.length === 1 ? args[0] : 0n;
  throw new DiasporeError(makeExc("SystemExit", code));
}

// --------------------------------------------------------------------------
// iteration builtins

function* biEnumerate(I: Interp, args: Value[], kw: KwArgs): G<Value> {
  argCount("enumerate", args, 1, 2);
  let start = 0n;
  const intArg = (v: Value): bigint => {
    const n = asIndex(v);
    if (n === null) {
      throw diasporeErr("TypeError", `'${typeName(v)}' object cannot be interpreted as an integer`);
    }
    return n;
  };
  if (args.length === 2) start = intArg(args[1]);
  const kv = kw.get("start");
  if (kv !== undefined) start = intArg(kv);
  return new EnumIter(yield* getIter(I, args[0]), start);
}

function* biZip(I: Interp, args: Value[]): G<Value> {
  const its: DiasporeIterBase[] = [];
  for (const a of args) its.push(yield* getIter(I, a));
  return new ZipIter(its);
}

function* biMap(I: Interp, args: Value[]): G<Value> {
  if (args.length < 2) {
    throw diasporeErr("TypeError", "map() must have at least two arguments.");
  }
  const its: DiasporeIterBase[] = [];
  for (const a of args.slice(1)) its.push(yield* getIter(I, a));
  return new MapIter(I, args[0], its);
}

function* biFilter(I: Interp, args: Value[]): G<Value> {
  argCount("filter", args, 2, 2);
  const it = yield* getIter(I, args[1]);
  const fn = args[0] instanceof PyNoneT ? null : args[0];
  return new FilterIter(I, fn, it);
}

function* biReversed(I: Interp, args: Value[]): G<Value> {
  argCount("reversed", args, 1, 1);
  const v = args[0];
  let items: Value[];
  if (v instanceof DiasporeList) items = [...v.items];
  else if (v instanceof DiasporeTuple) items = [...v.items];
  else if (typeof v === "string") items = chars(v).slice();
  else if (v instanceof Uint8Array) items = [...v].map((c) => BigInt(c));
  else if (v instanceof DiasporeRange) {
    items = [];
    const n = v.len();
    for (let i = 0n; i < n; i++) items.push(v.start + i * v.step);
  } else {
    throw diasporeErr("TypeError", `'${typeName(v)}' object is not reversible`);
  }
  items.reverse();
  return new SeqIter(items, "reversed");
}

function* biSorted(I: Interp, args: Value[], kw: KwArgs): G<Value> {
  argCount("sorted", args, 1, 1);
  chargeSized(I, args[0]);
  const items = yield* iterToArray(I, args[0]);
  I.fuelN(items.length);
  const [key, reverse] = yield* keyReverseKw("sorted", I, kw);
  yield* I.sortValues(items, key, reverse);
  return new DiasporeList(items);
}

/** Parse the key and reverse options shared by sorting builtins. */
export function* keyReverseKw(
  name: string,
  I: Interp,
  kw: KwArgs,
): G<[Value | null, boolean]> {
  let key: Value | null = null;
  let reverse = false;
  for (const [k, v] of kw) {
    if (k === "key") {
      key = v;
    } else if (k === "reverse") {
      reverse = yield* I.truth(v);
    } else {
      throw diasporeErr("TypeError", `'${k}' is an invalid keyword argument for ${name}()`);
    }
  }
  if (key instanceof PyNoneT) key = null;
  return [key, reverse];
}

function* biSum(I: Interp, args: Value[], kw: KwArgs): G<Value> {
  argCount("sum", args, 1, 2);
  let total: Value = 0n;
  if (args.length === 2) total = args[1];
  const kv = kw.get("start");
  if (kv !== undefined) total = kv;
  const it = yield* getIter(I, args[0]);
  for (;;) {
    I.fuel();
    const [x, ok] = yield* it.next();
    if (!ok) return total;
    total = yield* I.binop("add", total, x);
  }
}

function* minMax(I: Interp, name: string, args: Value[], kw: KwArgs, isMin: boolean): G<Value> {
  if (args.length === 0) {
    throw diasporeErr("TypeError", `${name} expected at least 1 argument, got 0`);
  }
  let key: Value | null = null;
  let deflt: Value | typeof MISSING = MISSING;
  for (const [k, v] of kw) {
    if (k === "key") {
      if (!(v instanceof PyNoneT)) key = v;
    } else if (k === "default") {
      deflt = v;
    } else {
      throw diasporeErr("TypeError", `'${k}' is an invalid keyword argument for ${name}()`);
    }
  }
  let items: Value[];
  if (args.length === 1) {
    chargeSized(I, args[0]);
    items = yield* iterToArray(I, args[0]);
    I.fuelN(items.length);
    if (items.length === 0) {
      if (deflt !== MISSING) return deflt;
      throw diasporeErr("ValueError", `${name}() iterable argument is empty`);
    }
  } else {
    items = args;
  }
  let best = items[0];
  let bestK = key !== null ? yield* I.invoke(key, [best], new Map()) : best;
  for (let i = 1; i < items.length; i++) {
    const x = items[i];
    const xk = key !== null ? yield* I.invoke(key, [x], new Map()) : x;
    const pick = isMin
      ? yield* I.compare("lt", xk, bestK)
      : yield* I.compare("lt", bestK, xk);
    if (pick) {
      best = x;
      bestK = xk;
    }
  }
  return best;
}

function* biAny(I: Interp, args: Value[]): G<Value> {
  argCount("any", args, 1, 1);
  const it = yield* getIter(I, args[0]);
  for (;;) {
    I.fuel();
    const [x, ok] = yield* it.next();
    if (!ok) return false;
    if (yield* I.truth(x)) return true;
  }
}

function* biAll(I: Interp, args: Value[]): G<Value> {
  argCount("all", args, 1, 1);
  const it = yield* getIter(I, args[0]);
  for (;;) {
    I.fuel();
    const [x, ok] = yield* it.next();
    if (!ok) return true;
    if (!(yield* I.truth(x))) return false;
  }
}

// --------------------------------------------------------------------------
// numeric builtins

function* biAbs(I: Interp, args: Value[]): G<Value> {
  argCount("abs", args, 1, 1);
  const n = asIndex(args[0]);
  if (n !== null) return n < 0n ? -n : n;
  if (typeof args[0] === "number") return Math.abs(args[0]);
  if (args[0] instanceof Complex) return Math.hypot(args[0].re, args[0].im);
  if (args[0] instanceof DiasporeInstance && args[0].cls.lookup("__abs__") !== undefined) {
    return yield* I.callDunder(args[0], "__abs__", []);
  }
  throw diasporeErr("TypeError", `bad operand type for abs(): '${typeName(args[0])}'`);
}

function* biRound(_I: Interp, args: Value[]): G<Value> {
  argCount("round", args, 1, 2);
  let hasND = false;
  let nd = 0n;
  if (args.length === 2 && !(args[1] instanceof PyNoneT)) {
    const n = asIndex(args[1]);
    if (n === null) {
      throw diasporeErr(
        "TypeError",
        `'${typeName(args[1])}' object cannot be interpreted as an integer`,
      );
    }
    nd = n;
    hasND = true;
  }
  const iv = asIndex(args[0]);
  if (iv !== null) {
    if (!hasND || nd >= 0n) return iv;
    // negative ndigits on an int: round at 10^-nd, half to even
    const p = 10n ** -nd;
    const neg = iv < 0n;
    const mag = neg ? -iv : iv;
    const q = mag / p;
    const r = mag % p;
    let out = q;
    const twice = r * 2n;
    if (twice > p || (twice === p && q % 2n === 1n)) out = q + 1n;
    out *= p;
    return neg ? -out : out;
  }
  if (typeof args[0] !== "number") {
    throw diasporeErr("TypeError", `type ${typeName(args[0])} doesn't define __round__ method`);
  }
  const f = args[0];
  if (!hasND) {
    if (Number.isNaN(f)) throw diasporeErr("ValueError", "cannot convert float NaN to integer");
    if (!Number.isFinite(f)) {
      throw diasporeErr("OverflowError", "cannot convert float infinity to integer");
    }
    return roundFloatToInt(f);
  }
  if (Number.isNaN(f) || !Number.isFinite(f)) return f;
  return roundFloatDigits(f, Number(nd));
}

function* biDivmod(I: Interp, args: Value[]): G<Value> {
  argCount("divmod", args, 2, 2);
  const q = yield* I.binop("floordiv", args[0], args[1]);
  const r = yield* I.binop("mod", args[0], args[1]);
  return new DiasporeTuple([q, r]);
}

function* biPow(I: Interp, args: Value[]): G<Value> {
  argCount("pow", args, 2, 3);
  if (args.length === 3) {
    const a = asIndex(args[0]);
    const bb = asIndex(args[1]);
    const m = asIndex(args[2]);
    if (a === null || bb === null || m === null) {
      throw diasporeErr(
        "TypeError",
        "pow() 3rd argument not allowed unless all arguments are integers",
      );
    }
    if (m === 0n) throw diasporeErr("ValueError", "pow() 3rd argument cannot be 0");
    if (bb < 0n) throw diasporeErr("ValueError", "base is not invertible for the given modulus");
    return modPow(a, bb, m);
  }
  return yield* I.binop("pow", args[0], args[1]);
}

function modPow(base: bigint, exp: bigint, mod: bigint): bigint {
  const am = mod < 0n ? -mod : mod;
  let result = 1n;
  let b = ((base % am) + am) % am;
  let e = exp;
  while (e > 0n) {
    if (e & 1n) result = (result * b) % am;
    b = (b * b) % am;
    e >>= 1n;
  }
  // CPython: result takes the sign of the modulus
  if (mod < 0n && result !== 0n) result -= am;
  return result;
}

function* biOrd(_I: Interp, args: Value[]): G<Value> {
  argCount("ord", args, 1, 1);
  const v = args[0];
  if (typeof v === "string") {
    const rs = chars(v);
    if (rs.length !== 1) {
      throw diasporeErr(
        "TypeError",
        `ord() expected a character, but string of length ${rs.length} found`,
      );
    }
    return BigInt(rs[0].codePointAt(0) as number);
  }
  if (v instanceof Uint8Array) {
    if (v.length !== 1) {
      throw diasporeErr(
        "TypeError",
        `ord() expected a character, but string of length ${v.length} found`,
      );
    }
    return BigInt(v[0]);
  }
  throw diasporeErr("TypeError", `ord() expected string of length 1, but ${typeName(v)} found`);
}

function* biChr(_I: Interp, args: Value[]): G<Value> {
  argCount("chr", args, 1, 1);
  const n = asIndex(args[0]);
  if (n === null) {
    throw diasporeErr("TypeError", `'${typeName(args[0])}' object cannot be interpreted as an integer`);
  }
  if (n < 0n || n >= 0x110000n) {
    throw diasporeErr("ValueError", "chr() arg not in range(0x110000)");
  }
  return String.fromCodePoint(Number(n));
}

function baseConv(name: string, prefix: string, base: number) {
  return function* (_I: Interp, args: Value[]): G<Value> {
    argCount(name, args, 1, 1);
    const n = asIndex(args[0]);
    if (n === null) {
      throw diasporeErr(
        "TypeError",
        `'${typeName(args[0])}' object cannot be interpreted as an integer`,
      );
    }
    const sign = n < 0n ? "-" : "";
    const mag = n < 0n ? -n : n;
    return sign + prefix + mag.toString(base);
  };
}

// --------------------------------------------------------------------------
// reflection builtins

function valueMatchesType(v: Value, bt: BuiltinType): boolean {
  switch (bt.name) {
    case "int":
      return isIntKind(v);
    case "bool":
      return typeof v === "boolean";
    case "float":
      return typeof v === "number";
    case "str":
      return typeof v === "string";
    case "bytes":
      return v instanceof Uint8Array;
    case "list":
      return v instanceof DiasporeList;
    case "tuple":
      return v instanceof DiasporeTuple;
    case "dict":
      return v instanceof DiasporeDict;
    case "set":
      return v instanceof DiasporeSet;
    case "range":
      return v instanceof DiasporeRange;
    case "complex":
      return v instanceof Complex;
    case "NoneType":
      return v instanceof PyNoneT;
    case "type":
      return v instanceof DiasporeClass || v instanceof BuiltinType;
  }
  return false;
}

function* biIsinstance(_I: Interp, args: Value[]): G<Value> {
  argCount("isinstance", args, 2, 2);
  const classes = args[1] instanceof DiasporeTuple ? args[1].items : [args[1]];
  const v = args[0];
  for (const c of classes) {
    if (c === OBJECT) return true;
    if (c instanceof DiasporeClass) {
      if (v instanceof DiasporeInstance && v.cls.issub(c)) return true;
    } else if (c instanceof BuiltinType) {
      if (c.name === "type") {
        if (v instanceof DiasporeClass || v instanceof BuiltinType) return true;
      } else if (v instanceof DiasporeInstance && v.prim !== null) {
        // builtin-subclass instance: matches its primitive's builtin type
        if (v.cls.prim !== null) {
          if (v.cls.prim.name === c.name || (c.name === "int" && v.cls.prim.name === "bool")) {
            return true;
          }
        }
        if (valueMatchesType(v.prim, c)) return true;
      } else if (!(v instanceof DiasporeInstance) && valueMatchesType(v, c)) {
        return true;
      }
    } else {
      throw diasporeErr("TypeError", "isinstance() arg 2 must be a type or tuple of types");
    }
  }
  return false;
}

function* biIssubclass(_I: Interp, args: Value[]): G<Value> {
  argCount("issubclass", args, 2, 2);
  const a = args[0];
  const classes = args[1] instanceof DiasporeTuple ? args[1].items : [args[1]];
  if (!(a instanceof DiasporeClass || a instanceof BuiltinType)) {
    throw diasporeErr("TypeError", "issubclass() arg 1 must be a class");
  }
  for (const c of classes) {
    if (c === OBJECT) return true;
  }
  for (const c of classes) {
    if (a instanceof DiasporeClass && c instanceof DiasporeClass && a.issub(c)) return true;
    if (a instanceof BuiltinType && c instanceof BuiltinType) {
      if (a.name === c.name || (a.name === "bool" && c.name === "int")) return true;
    }
  }
  return false;
}

function* biCallable(_I: Interp, args: Value[]): G<Value> {
  argCount("callable", args, 1, 1);
  const v = args[0];
  if (
    v instanceof DiasporeFunction ||
    v instanceof BoundMethod ||
    v instanceof DiasporeClass ||
    v instanceof BuiltinType ||
    v instanceof Builtin
  ) {
    return true;
  }
  if (v instanceof DiasporeInstance) return v.cls.lookup("__call__") !== undefined;
  return false;
}

function fnvHash(s: string): bigint {
  let h = 0xcbf29ce484222325n;
  for (let i = 0; i < s.length; i++) {
    h ^= BigInt(s.charCodeAt(i));
    h = (h * 0x100000001b3n) & 0xffffffffffffffffn;
  }
  return h & 0x7fffffffffffffffn;
}

function* biHash(_I: Interp, args: Value[]): G<Value> {
  argCount("hash", args, 1, 1);
  const v = args[0];
  if (v instanceof DiasporeInstance) return BigInt(objId(v)) % 2n ** 61n;
  const n = asIndex(v);
  if (n !== null) return n;
  // Numerically-integral values (1.0, 1+0j) hash like the equal int.
  const hk = hashKey(v);
  if (hk.length > 1 && hk[0] === "i") {
    try {
      return BigInt(hk.slice(1));
    } catch {
      /* fall through */
    }
  }
  return fnvHash(hk);
}

function* biID(_I: Interp, args: Value[]): G<Value> {
  argCount("id", args, 1, 1);
  const v = args[0];
  if (typeof v === "object" && v !== null) return BigInt(objId(v as object));
  return fnvHash(hashKey(v));
}

function* biNext(I: Interp, args: Value[]): G<Value> {
  argCount("next", args, 1, 2);
  let it: DiasporeIterBase | null = null;
  const v = args[0];
  if (v instanceof DiasporeIterBase) it = v;
  else if (v instanceof DiasporeInstance && v.cls.lookup("__next__") !== undefined) {
    it = new InstIter(I, v);
  }
  if (it === null) {
    throw diasporeErr("TypeError", `'${typeName(v)}' object is not an iterator`);
  }
  const [x, ok] = yield* it.next();
  if (ok) return x;
  if (args.length === 2) return args[1];
  const rv = it.takeRetval();
  throw new DiasporeError(
    rv instanceof PyNoneT ? makeExc("StopIteration") : makeExc("StopIteration", rv),
  );
}

function attrName(v: Value): string {
  if (typeof v === "string") return v;
  throw diasporeErr("TypeError", `attribute name must be string, not '${typeName(v)}'`);
}

function* biGetattr(I: Interp, args: Value[]): G<Value> {
  argCount("getattr", args, 2, 3);
  const name = attrName(args[1]);
  try {
    return yield* I.getAttr(args[0], name);
  } catch (e) {
    if (args.length === 3 && e instanceof DiasporeError && excIsA(e.value, "AttributeError")) {
      return args[2];
    }
    throw e;
  }
}

// --------------------------------------------------------------------------
// type constructors

function* ctorInt(I: Interp, args: Value[]): G<Value> {
  argCount("int", args, 0, 2);
  if (args.length === 0) return 0n;
  const v = args[0];
  if (args.length === 2) {
    const base = asIndex(args[1]);
    if (base === null) {
      throw diasporeErr(
        "TypeError",
        `'${typeName(args[1])}' object cannot be interpreted as an integer`,
      );
    }
    if (typeof v !== "string") {
      throw diasporeErr("TypeError", "int() can't convert non-string with explicit base");
    }
    return parsePyInt(v, Number(base));
  }
  if (v instanceof DiasporeInstance) {
    for (const d of ["__int__", "__index__", "__trunc__"]) {
      if (v.cls.lookup(d) !== undefined) return yield* I.callDunder(v, d, []);
    }
    if (v.prim !== null && (typeof v.prim === "bigint" || typeof v.prim === "number" || typeof v.prim === "string")) {
      return yield* ctorInt(I, [v.prim]);
    }
    throw diasporeErr(
      "TypeError",
      `int() argument must be a string or a real number, not '${v.cls.name}'`,
    );
  }
  const n = asIndex(v);
  if (n !== null) return n;
  if (typeof v === "number") {
    if (Number.isNaN(v)) throw diasporeErr("ValueError", "cannot convert float NaN to integer");
    if (!Number.isFinite(v)) {
      throw diasporeErr("OverflowError", "cannot convert float infinity to integer");
    }
    return intFromFloat(v);
  }
  if (typeof v === "string") return parsePyInt(v, 10);
  throw diasporeErr(
    "TypeError",
    `int() argument must be a string or a real number, not '${typeName(v)}'`,
  );
}

function parsePyInt(orig: string, base: number): bigint {
  const fail = () =>
    diasporeErr("ValueError", `invalid literal for int() with base ${base}: ${reprString(orig)}`);
  let s = orig.trim();
  let sign = "";
  if (s.startsWith("+") || s.startsWith("-")) {
    sign = s[0];
    s = s.slice(1);
  }
  let lower = s.toLowerCase();
  if (base === 16 && lower.startsWith("0x")) lower = lower.slice(2);
  else if (base === 8 && lower.startsWith("0o")) lower = lower.slice(2);
  else if (base === 2 && lower.startsWith("0b")) lower = lower.slice(2);
  const cleaned = stripUnderscores(lower);
  if (cleaned === null || cleaned === "") throw fail();
  const digits = "0123456789abcdefghijklmnopqrstuvwxyz".slice(0, base);
  let out = 0n;
  const bb = BigInt(base);
  for (const ch of cleaned) {
    const d = digits.indexOf(ch);
    if (d < 0) throw fail();
    out = out * bb + BigInt(d);
  }
  return sign === "-" ? -out : out;
}

function stripUnderscores(s: string): string | null {
  if (!s.includes("_")) return s;
  let b = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "_") {
      if (i === 0 || i === s.length - 1 || s[i - 1] === "_") return null;
      continue;
    }
    b += s[i];
  }
  return b;
}

function* ctorFloat(I: Interp, args: Value[]): G<Value> {
  argCount("float", args, 0, 1);
  if (args.length === 0) return 0;
  const v = args[0];
  if (typeof v === "number") return v;
  const n = asIndex(v);
  if (n !== null) return Number(n);
  if (typeof v === "string") return parsePyFloat(v);
  if (v instanceof DiasporeInstance) {
    if (v.cls.lookup("__float__") !== undefined) return yield* I.callDunder(v, "__float__", []);
    if (v.prim !== null && (typeof v.prim === "bigint" || typeof v.prim === "number")) {
      return typeof v.prim === "bigint" ? Number(v.prim) : v.prim;
    }
    throw diasporeErr(
      "TypeError",
      `float() argument must be a string or a real number, not '${v.cls.name}'`,
    );
  }
  throw diasporeErr(
    "TypeError",
    `float() argument must be a string or a real number, not '${typeName(v)}'`,
  );
}

function* ctorComplex(_I: Interp, args: Value[]): G<Value> {
  argCount("complex", args, 0, 2);
  const toNum = (v: Value): number | null => {
    const n = asFloat(v);
    return n;
  };
  if (args.length === 0) return new Complex(0, 0);
  const a = args[0];
  if (args.length === 1) {
    if (typeof a === "string") return parseComplex(a);
    if (a instanceof Complex) return a;
    const n = toNum(a);
    if (n === null) {
      throw diasporeErr(
        "TypeError",
        `complex() argument must be a string or a number, not '${typeName(a)}'`,
      );
    }
    return new Complex(n, 0);
  }
  const re = a instanceof Complex ? a : toNum(a);
  const im = args[1] instanceof Complex ? args[1] : toNum(args[1]);
  if (re === null || im === null) {
    throw diasporeErr("TypeError", "complex() argument must be a number");
  }
  // complex(re, im): re and im may themselves be complex (CPython combines them)
  const reC = re instanceof Complex ? re : new Complex(re, 0);
  const imC = im instanceof Complex ? im : new Complex(im, 0);
  return new Complex(reC.re - imC.im, reC.im + imC.re);
}

function parsePyFloat(orig: string): number {
  const fail = () =>
    diasporeErr("ValueError", `could not convert string to float: ${reprString(orig)}`);
  const s = orig.trim();
  if (s === "") throw fail();
  const m = /^([+-]?)(inf|infinity|nan)$/i.exec(s);
  if (m !== null) {
    const mag = m[2].toLowerCase() === "nan" ? NaN : Infinity;
    return m[1] === "-" ? -mag : mag;
  }
  if (/[xXpP]/.test(s)) throw fail();
  const cleaned = stripUnderscores(s.toLowerCase());
  if (cleaned === null) throw fail();
  if (!/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/.test(cleaned)) throw fail();
  const f = Number(cleaned);
  if (Number.isNaN(f)) throw fail();
  return f;
}

function* ctorBool(I: Interp, args: Value[]): G<Value> {
  argCount("bool", args, 0, 1);
  if (args.length === 0) return false;
  return yield* I.truth(args[0]);
}

function* ctorStr(I: Interp, args: Value[], kw: KwArgs): G<Value> {
  if (args.length > 1 || kw.size > 0) {
    throw diasporeErr("TypeError", "diaspore str() takes one argument");
  }
  if (args.length === 0) return "";
  return yield* I.str(args[0]);
}

function* ctorBytes(I: Interp, args: Value[]): G<Value> {
  argCount("bytes", args, 0, 2);
  if (args.length === 0) return new Uint8Array(0);
  const v = args[0];
  if (typeof v === "string") {
    if (args.length < 2) {
      throw diasporeErr("TypeError", "string argument without an encoding");
    }
    const enc = wantStr("encoding", args[1]);
    return encodeStrTo(v, enc);
  }
  if (v instanceof Uint8Array) return new Uint8Array(v);
  const n = asIndex(v);
  if (n !== null) {
    if (n < 0n) throw diasporeErr("ValueError", "negative count");
    return new Uint8Array(Number(n));
  }
  const items = yield* iterToArray(I, v);
  const out = new Uint8Array(items.length);
  for (let i = 0; i < items.length; i++) {
    const x = asIndex(items[i]);
    if (x === null) {
      throw diasporeErr(
        "TypeError",
        `'${typeName(items[i])}' object cannot be interpreted as an integer`,
      );
    }
    if (x < 0n || x > 255n) throw diasporeErr("ValueError", "bytes must be in range(0, 256)");
    out[i] = Number(x);
  }
  return out;
}

/** Encode text using a supported Python encoding name. */
export function encodeStrTo(s: string, enc: string): Uint8Array {
  switch (enc.toLowerCase()) {
    case "utf-8":
    case "utf8":
      return new TextEncoder().encode(s);
    case "latin-1":
    case "latin1":
    case "iso-8859-1": {
      const out: number[] = [];
      for (const r of s) {
        const c = r.codePointAt(0) as number;
        if (c > 0xff) {
          throw diasporeErr(
            "ValueError",
            `'latin-1' codec can't encode character ${reprString(r)}`,
          );
        }
        out.push(c);
      }
      return new Uint8Array(out);
    }
  }
  throw diasporeErr("ValueError", `unknown encoding: ${enc}`);
}

function* ctorList(I: Interp, args: Value[]): G<Value> {
  if (args.length === 1) chargeSized(I, args[0]);
  argCount("list", args, 0, 1);
  if (args.length === 0) return new DiasporeList();
  return new DiasporeList(yield* iterToArray(I, args[0]));
}

function* ctorTuple(I: Interp, args: Value[]): G<Value> {
  if (args.length === 1) chargeSized(I, args[0]);
  argCount("tuple", args, 0, 1);
  if (args.length === 0) return new DiasporeTuple();
  return new DiasporeTuple(yield* iterToArray(I, args[0]));
}

function* ctorSet(I: Interp, args: Value[]): G<Value> {
  if (args.length === 1) chargeSized(I, args[0]);
  argCount("set", args, 0, 1);
  const out = new DiasporeSet();
  if (args.length === 1) {
    for (const x of yield* iterToArray(I, args[0])) out.add(x);
  }
  return out;
}

/** Merge a mapping or iterable of pairs into a sandbox dictionary. */
export function* dictUpdateFrom(I: Interp, d: DiasporeDict, src: Value): G<void> {
  if (src instanceof DiasporeInstance && src.prim !== null) src = src.prim;
  if (src instanceof DiasporeDict) {
    for (const [hk, e] of src.map) d.setHK(hk, e.key, e.val);
    return;
  }
  const pairs = yield* iterToArray(I, src);
  for (let i = 0; i < pairs.length; i++) {
    const kv = yield* iterToArray(I, pairs[i]);
    if (kv.length !== 2) {
      throw diasporeErr(
        "ValueError",
        `dictionary update sequence element #${i} has length ${kv.length}; 2 is required`,
      );
    }
    d.set(kv[0], kv[1]);
  }
}

function* ctorDict(I: Interp, args: Value[], kw: KwArgs): G<Value> {
  if (args.length > 1) {
    throw diasporeErr("TypeError", `dict expected at most 1 argument, got ${args.length}`);
  }
  const out = new DiasporeDict();
  if (args.length === 1) yield* dictUpdateFrom(I, out, args[0]);
  for (const [k, v] of kw) out.setHK("s" + k, k, v);
  return out;
}

function* ctorRange(_I: Interp, args: Value[]): G<Value> {
  argCount("range", args, 1, 3);
  const vals: bigint[] = [];
  for (const a of args) {
    const n = asIndex(a);
    if (n === null) {
      throw diasporeErr("TypeError", `'${typeName(a)}' object cannot be interpreted as an integer`);
    }
    vals.push(n);
  }
  if (vals.length === 1) return new DiasporeRange(0n, vals[0], 1n);
  if (vals.length === 2) return new DiasporeRange(vals[0], vals[1], 1n);
  if (vals[2] === 0n) throw diasporeErr("ValueError", "range() arg 3 must not be zero");
  return new DiasporeRange(vals[0], vals[1], vals[2]);
}

function* ctorType(I: Interp, args: Value[]): G<Value> {
  argCount("type", args, 1, 3);
  const v = args[0];
  if (args.length > 1) {
    // type(name, bases, ns): dynamic class creation
    if (args.length !== 3) {
      throw diasporeErr("TypeError", "type() takes 1 or 3 arguments");
    }
    const bases = args[1];
    const ns = args[2];
    if (typeof v !== "string" || !(bases instanceof DiasporeTuple) || !(ns instanceof DiasporeDict)) {
      throw diasporeErr("TypeError", "type() argument types: (str, tuple, dict)");
    }
    const checked: DiasporeClass[] = [];
    for (const b of bases.items) {
      if (!(b instanceof DiasporeClass)) {
        throw diasporeErr("TypeError", `cannot subclass ${yield* I.repr(b)} in diaspore`);
      }
      checked.push(b);
    }
    const nsMap = new Map<string, Value>();
    for (const [k, val] of ns.entries()) {
      if (typeof k === "string") nsMap.set(k, val);
    }
    return yield* I.buildClass(v, checked, nsMap, null, new Map());
  }
  const t = I.types;
  if (v instanceof DiasporeInstance) return v.cls;
  if (v instanceof DiasporeClass || v instanceof BuiltinType) return t.get("type") as BuiltinType;
  switch (typeof v) {
    case "boolean":
      return t.get("bool") as BuiltinType;
    case "bigint":
      return t.get("int") as BuiltinType;
    case "number":
      return t.get("float") as BuiltinType;
    case "string":
      return t.get("str") as BuiltinType;
  }
  if (v instanceof Uint8Array) return t.get("bytes") as BuiltinType;
  if (v instanceof DiasporeList) return t.get("list") as BuiltinType;
  if (v instanceof DiasporeTuple) return t.get("tuple") as BuiltinType;
  if (v instanceof DiasporeDict) return t.get("dict") as BuiltinType;
  if (v instanceof DiasporeSet) return t.get("set") as BuiltinType;
  if (v instanceof DiasporeRange) return t.get("range") as BuiltinType;
  if (v instanceof PyNoneT) return t.get("NoneType") as BuiltinType;
  if (v instanceof Complex) return t.get("complex") as BuiltinType;
  const name = typeName(v);
  return new BuiltinType(name, function* (): G<Value> {
    throw diasporeErr("TypeError", `cannot construct '${name}' values`);
  });
}
