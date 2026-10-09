/* Native modules (spec 11), capability-checked files, and unsupported-module errors. */

import * as nodeFs from "node:fs";

import {
  type Value,
  type G,
  type KwArgs,
  NONE,
  PyNoneT,
  DiasporeList,
  DiasporeFile,
  NativeModule,
  NativeObject,
  BuiltinType,
  Builtin,
  typeName,
  asIndex,
  asFloat,
  intFromFloat,
  strLen,
  strCmp,
  diasporeErr,
  DiasporeError,
  makeExc,
  SuspendSig,
} from "./values.ts";
import { reprString, round6 } from "./format.ts";
import { jsonDumps, jsonLoads } from "./json.ts";
import { iterToArray } from "./iter.ts";
import { argCount, needArgs, wantStr, noKw } from "./helpers.ts";
import { bi, readStdinSync } from "./builtins.ts";
import { decodeBytes } from "./methods.ts";
import { makeReModule } from "./re.ts";
import type { Interp } from "./interp.ts";

/** Construct the native module namespace for an interpreter. */
export function makeNativeModules(I: Interp): Map<string, NativeModule> {
  return new Map([
    ["time", makeTimeModule(I)],
    ["math", makeMathModule()],
    ["json", makeJSONModule()],
    ["random", makeRandomModule(I)],
    ["sys", makeSysModule(I)],
    ["_os", makeOSModule()],
    ["net", makeStubModule("net", ["listen", "connect", "accept", "recv", "send", "close", "peer"])],
    ["re", makeReModule(I)],
    ["tasks", makeStubModule("tasks", ["spawn", "channel", "lock"])],
    ["flow", makeFlowModule()],
  ]);
}

/** Expose unsupported module functions as catchable RuntimeError failures. */
function makeStubModule(name: string, fns: string[]): NativeModule {
  const ns = new Map<string, Value>();
  for (const f of fns) {
    ns.set(
      f,
      bi(f, function* (): G<Value> {
        throw diasporeErr("RuntimeError", `diaspore-ts does not support ${name}`);
      }),
    );
  }
  return new NativeModule(name, ns);
}

// --------------------------------------------------------------------------
// time

function sleepBlocking(secs: number): void {
  const sab = new Int32Array(new SharedArrayBuffer(4));
  Atomics.wait(sab, 0, 0, secs * 1000);
}

const monoStart = performance.now();

function makeTimeModule(I: Interp): NativeModule {
  let timeFn: Builtin;
  let monoFn: Builtin;
  let sleepFn: Builtin;
  if (I.deterministic) {
    let clock = 0;
    const tick = function* (): G<Value> {
      clock = round6(clock + 0.001);
      return clock;
    };
    timeFn = bi("time", tick);
    monoFn = bi("monotonic", tick);
    sleepFn = bi("sleep", function* (_I, args): G<Value> {
      argCount("sleep", args, 1, 1);
      const secs = asFloat(args[0]);
      if (secs === null) throw diasporeErr("TypeError", "an integer or float is required");
      clock = round6(clock + secs);
      return NONE;
    });
  } else {
    timeFn = bi("time", function* (): G<Value> {
      return Date.now() / 1000;
    });
    monoFn = bi("monotonic", function* (): G<Value> {
      return (performance.now() - monoStart) / 1000;
    });
    sleepFn = bi("sleep", function* (_I, args): G<Value> {
      argCount("sleep", args, 1, 1);
      const secs = asFloat(args[0]);
      if (secs === null) throw diasporeErr("TypeError", "an integer or float is required");
      if (secs < 0) throw diasporeErr("ValueError", "sleep length must be non-negative");
      sleepBlocking(secs);
      return NONE;
    });
  }
  return new NativeModule(
    "time",
    new Map<string, Value>([
      ["time", I.journaled("time", "time", timeFn)],
      ["monotonic", I.journaled("time", "monotonic", monoFn)],
      ["sleep", I.journaled("time", "sleep", sleepFn)],
    ]),
  );
}

// --------------------------------------------------------------------------
// math

function mathFn(name: string, arity: number, f: (xs: number[]) => number): Builtin {
  return bi(name, function* (_I, args): G<Value> {
    argCount(name, args, arity, arity);
    const xs: number[] = [];
    for (const a of args) {
      const x = asFloat(a);
      if (x === null) {
        throw diasporeErr("TypeError", `must be real number, not ${typeName(a)}`);
      }
      xs.push(x);
    }
    return f(xs);
  });
}

function domainErr(): DiasporeError {
  return diasporeErr("ValueError", "math domain error");
}

function makeMathModule(): NativeModule {
  const intRound = (name: string, f: (x: number) => number): Builtin =>
    bi(name, function* (_I, args): G<Value> {
      argCount(name, args, 1, 1);
      const n = asIndex(args[0]);
      if (n !== null) return n;
      if (typeof args[0] !== "number") {
        throw diasporeErr("TypeError", `must be real number, not ${typeName(args[0])}`);
      }
      const x = args[0];
      if (Number.isNaN(x)) throw diasporeErr("ValueError", "cannot convert float NaN to integer");
      if (!Number.isFinite(x)) {
        throw diasporeErr("OverflowError", "cannot convert float infinity to integer");
      }
      return intFromFloat(f(x));
    });
  return new NativeModule(
    "math",
    new Map<string, Value>([
      ["pi", Math.PI],
      ["e", Math.E],
      ["inf", Infinity],
      ["nan", NaN],
      ["floor", intRound("floor", Math.floor)],
      ["ceil", intRound("ceil", Math.ceil)],
      [
        "sqrt",
        mathFn("sqrt", 1, (xs) => {
          if (xs[0] < 0) throw domainErr();
          return Math.sqrt(xs[0]);
        }),
      ],
      ["fabs", mathFn("fabs", 1, (xs) => Math.abs(xs[0]))],
      [
        "pow",
        mathFn("pow", 2, (xs) => {
          const [x, y] = xs;
          if (x === 0 && y < 0) throw domainErr();
          if (x < 0 && y !== Math.trunc(y) && Number.isFinite(y)) throw domainErr();
          return Math.pow(x, y);
        }),
      ],
      [
        "log",
        mathFn("log", 1, (xs) => {
          if (xs[0] <= 0) throw domainErr();
          return Math.log(xs[0]);
        }),
      ],
      [
        "log2",
        mathFn("log2", 1, (xs) => {
          if (xs[0] <= 0) throw domainErr();
          return Math.log2(xs[0]);
        }),
      ],
      [
        "log10",
        mathFn("log10", 1, (xs) => {
          if (xs[0] <= 0) throw domainErr();
          return Math.log10(xs[0]);
        }),
      ],
      ["exp", mathFn("exp", 1, (xs) => Math.exp(xs[0]))],
      ["sin", mathFn("sin", 1, (xs) => Math.sin(xs[0]))],
      ["cos", mathFn("cos", 1, (xs) => Math.cos(xs[0]))],
      ["tan", mathFn("tan", 1, (xs) => Math.tan(xs[0]))],
      ["hypot", mathFn("hypot", 2, (xs) => Math.hypot(xs[0], xs[1]))],
      [
        "fmod",
        mathFn("fmod", 2, (xs) => {
          if (xs[1] === 0) throw domainErr();
          return xs[0] % xs[1];
        }),
      ],
      [
        "isnan",
        bi("isnan", function* (_I, args): G<Value> {
          argCount("isnan", args, 1, 1);
          const x = asFloat(args[0]);
          if (x === null) {
            throw diasporeErr("TypeError", `must be real number, not ${typeName(args[0])}`);
          }
          return Number.isNaN(x);
        }),
      ],
      [
        "isinf",
        bi("isinf", function* (_I, args): G<Value> {
          argCount("isinf", args, 1, 1);
          const x = asFloat(args[0]);
          if (x === null) {
            throw diasporeErr("TypeError", `must be real number, not ${typeName(args[0])}`);
          }
          return x === Infinity || x === -Infinity;
        }),
      ],
    ]),
  );
}

// --------------------------------------------------------------------------
// json

function makeJSONModule(): NativeModule {
  return new NativeModule(
    "json",
    new Map<string, Value>([
      [
        "dumps",
        bi("dumps", function* (_I, args, kw): G<Value> {
          argCount("dumps", args, 1, 2);
          let indent: Value = NONE;
          if (args.length === 2) indent = args[1];
          const kv = kw.get("indent");
          if (kv !== undefined) indent = kv;
          return jsonDumps(args[0], indent);
        }),
      ],
      [
        "loads",
        bi("loads", function* (_I, args): G<Value> {
          argCount("loads", args, 1, 1);
          return jsonLoads(wantStr("loads arg", args[0]));
        }),
      ],
    ]),
  );
}

// --------------------------------------------------------------------------
// random (algorithm is VM-specific; excluded from conformance)

class Rng {
  s0: number;
  s1: number;
  s2: number;
  s3: number;
  constructor(seed: number) {
    // splitmix32-seeded xoshiro128**
    let x = seed >>> 0;
    const next = () => {
      x = (x + 0x9e3779b9) >>> 0;
      let z = x;
      z = Math.imul(z ^ (z >>> 16), 0x21f0aaad);
      z = Math.imul(z ^ (z >>> 15), 0x735a2d97);
      return (z ^ (z >>> 15)) >>> 0;
    };
    this.s0 = next();
    this.s1 = next();
    this.s2 = next();
    this.s3 = next();
  }
  nextU32(): number {
    const rotl = (v: number, k: number) => ((v << k) | (v >>> (32 - k))) >>> 0;
    const result = (Math.imul(rotl(Math.imul(this.s1, 5) >>> 0, 7), 9) >>> 0) >>> 0;
    const t = (this.s1 << 9) >>> 0;
    this.s2 ^= this.s0;
    this.s3 ^= this.s1;
    this.s1 ^= this.s2;
    this.s0 ^= this.s3;
    this.s2 ^= t;
    this.s3 = rotl(this.s3, 11);
    return result;
  }
  random(): number {
    const hi = this.nextU32() >>> 5; // 27 bits
    const lo = this.nextU32() >>> 6; // 26 bits
    return (hi * 67108864 + lo) / 9007199254740992;
  }
  below(n: number): number {
    return Math.floor(this.random() * n);
  }
}

function makeRandomMethods(I: Interp, seed: Value, scope: string): Map<string, Builtin> {
  const initial = seed instanceof PyNoneT ? null : asIndex(seed);
  if (!(seed instanceof PyNoneT) && initial === null) {
    throw diasporeErr("TypeError", "seed must be an integer or None in diaspore");
  }
  let rng = new Rng(initial === null ? (I.deterministic ? 0 : Date.now() >>> 0) : Number(BigInt.asUintN(32, initial)));
  const seedFn = bi("seed", function* (_I, args): G<Value> {
    argCount("seed", args, 0, 1);
    const value = args[0] ?? NONE;
    const n = value instanceof PyNoneT ? null : asIndex(value);
    if (!(value instanceof PyNoneT) && n === null) {
      throw diasporeErr("TypeError", "seed must be an integer or None in diaspore");
    }
    rng = new Rng(n === null ? (I.deterministic ? 0 : Date.now() >>> 0) : Number(BigInt.asUintN(32, n)));
    return NONE;
  });
  const randomFn = bi("random", function* (_I, args): G<Value> {
    argCount("random", args, 0, 0);
    return rng.random();
  });
  const randintFn = bi("randint", function* (_I, args): G<Value> {
    argCount("randint", args, 2, 2);
    const a = asIndex(args[0]);
    const b = asIndex(args[1]);
    if (a === null || b === null) throw diasporeErr("TypeError", "randint requires integers");
    if (b < a) throw diasporeErr("ValueError", "empty range for randint()");
    return a + BigInt(rng.below(Number(b - a + 1n)));
  });
  const choiceFn = bi("choice", function* (I2, args): G<Value> {
    argCount("choice", args, 1, 1);
    const items = yield* iterToArray(I2, args[0]);
    if (items.length === 0) {
      throw diasporeErr("IndexError", "Cannot choose from an empty sequence");
    }
    return items[rng.below(items.length)];
  });
  // shuffle journals the resulting order so replay reproduces the mutation
  const shuffleFn = bi("shuffle", function* (I2, args): G<Value> {
    argCount("shuffle", args, 1, 1);
    const l = args[0];
    if (!(l instanceof DiasporeList)) throw diasporeErr("TypeError", "shuffle requires a list");
    const doShuffle = function* (): G<Value> {
      for (let i = l.items.length - 1; i > 0; i--) {
        const j = rng.below(i + 1);
        [l.items[i], l.items[j]] = [l.items[j], l.items[i]];
      }
      return new DiasporeList([...l.items]);
    };
    if (!I2.durable) {
      yield* doShuffle();
      return NONE;
    }
    const order = yield* I2.effect(scope, "shuffle", doShuffle);
    if (!(order instanceof DiasporeList)) {
      throw diasporeErr("TypeError", "shuffle journal entry is not a list");
    }
    l.items = [...order.items];
    return NONE;
  });
  return new Map([
    ["seed", I.journaled(scope, "seed", seedFn)],
    ["random", I.journaled(scope, "random", randomFn)],
    ["randint", I.journaled(scope, "randint", randintFn)],
    ["choice", I.journaled(scope, "choice", choiceFn)],
    ["shuffle", shuffleFn],
  ]);
}

function makeRandomModule(I: Interp): NativeModule {
  let instance = 0;
  const randomType = new BuiltinType("Random", function* (I2, args, kw): G<Value> {
    noKw("Random", kw);
    argCount("Random", args, 0, 1);
    return new NativeObject("Random", makeRandomMethods(I2, args[0] ?? NONE, "random.Random." + instance++));
  });
  I.types.set("Random", randomType);
  const ns = new Map<string, Value>(makeRandomMethods(I, NONE, "random"));
  ns.set("Random", randomType);
  return new NativeModule("random", ns);
}

function makeSysStream(kind: "stdin" | "stdout" | "stderr"): NativeObject {
  const methods = new Map<string, Builtin>();
  if (kind === "stdin") {
    for (const name of ["read", "readline"]) {
      methods.set(name, bi(name, function* (I, args, kw): G<Value> {
        noKw(name, kw);
        argCount(name, args, 0, 1);
        const size = args.length === 0 || args[0] instanceof PyNoneT ? -1n : asIndex(args[0]);
        if (size === null) throw diasporeErr("TypeError", `'${typeName(args[0])}' object cannot be interpreted as an integer`);
        if (size < -(1n << 63n) || size >= 1n << 63n) {
          throw diasporeErr("OverflowError", "Python int too large to convert to C ssize_t");
        }
        if (!I.durable) return readStdinSync(Number(size), name === "readline");
        return yield* I.effect("sys.stdin", name, function* (): G<Value> {
          return readStdinSync(Number(size), name === "readline");
        });
      }));
    }
  } else {
    methods.set("write", bi("write", function* (I, args, kw): G<Value> {
      noKw("write", kw);
      argCount("write", args, 1, 1);
      const text = wantStr("write() argument", args[0]);
      if (!I.replaying) {
        if (kind === "stdout") I.write(text);
        else I.ewrite(text);
      }
      return BigInt(strLen(text));
    }));
  }
  methods.set("flush", bi("flush", function* (_I, args, kw): G<Value> {
    noKw("flush", kw);
    argCount("flush", args, 0, 0);
    // Writes go straight to the configured sink; the interpreter has no output buffer.
    return NONE;
  }));
  return new NativeObject("TextIOWrapper", methods);
}

// --------------------------------------------------------------------------
// sys

function makeSysModule(I: Interp): NativeModule {
  return new NativeModule(
    "sys",
    new Map<string, Value>([
      ["argv", new DiasporeList(I.argv.map((a) => a as Value))],
      ["platform", "diaspore-ts"],
      ["stdin", makeSysStream("stdin")],
      ["stdout", makeSysStream("stdout")],
      ["stderr", makeSysStream("stderr")],
      ["getrecursionlimit", bi("getrecursionlimit", function* (I2, args): G<Value> {
        argCount("getrecursionlimit", args, 0, 0);
        return BigInt(I2.maxDepth);
      })],
      ["setrecursionlimit", bi("setrecursionlimit", function* (I2, args): G<Value> {
        argCount("setrecursionlimit", args, 1, 1);
        const limit = asIndex(args[0]);
        if (limit === null) throw diasporeErr("TypeError", `'${typeName(args[0])}' object cannot be interpreted as an integer`);
        if (limit < -(1n << 31n) || limit >= 1n << 31n) {
          throw diasporeErr("OverflowError", "Python int too large to convert to C int");
        }
        if (limit < 1n) throw diasporeErr("ValueError", "recursion limit must be greater or equal than 1");
        const effective = Math.min(Number(limit), I2.depthCap);
        if (effective <= I2.depth) {
          throw diasporeErr("RecursionError", `cannot set the recursion limit to ${limit} at the recursion depth ${I2.depth}: the limit is too low`);
        }
        I2.maxDepth = effective;
        return NONE;
      })],
      [
        "exit",
        bi("exit", function* (_I, args): G<Value> {
          argCount("exit", args, 0, 1);
          const code: Value = args.length === 1 ? args[0] : 0n;
          throw new DiasporeError(makeExc("SystemExit", code));
        }),
      ],
      [
        "stderr_write",
        bi("stderr_write", function* (I2, args): G<Value> {
          argCount("stderr_write", args, 1, 1);
          const s = wantStr("stderr_write arg", args[0]);
          if (!I2.replaying) I2.ewrite(s);
          return NONE;
        }),
      ],
    ]),
  );
}

// --------------------------------------------------------------------------
// flow

function makeFlowModule(): NativeModule {
  return new NativeModule(
    "flow",
    new Map<string, Value>([
      [
        "suspend",
        bi("suspend", function* (I, args): G<Value> {
          argCount("suspend", args, 0, 1);
          if (!I.durable) {
            throw diasporeErr(
              "RuntimeError",
              "flow.suspend() requires a durable run (diaspore run --durable)",
            );
          }
          const [replayed, r] = I.replayNext("flow", "suspend");
          if (replayed) return r;
          const payload: Value = args.length === 1 ? args[0] : NONE;
          throw new SuspendSig(I.journalValue(payload, "flow.suspend"));
        }),
      ],
    ]),
  );
}

// --------------------------------------------------------------------------
// open() and file methods (capability "fs")

function makeOSModule(): NativeModule {
  const ns = new Map<string, Value>();
  ns.set("getcwd", bi("getcwd", function* (I, args): G<Value> {
    argCount("getcwd", args, 0, 0);
    I.requireCap("fs");
    try {
      return process.cwd();
    } catch (error) {
      throw filesystemError(error, ".");
    }
  }));
  ns.set("realpath", bi("realpath", function* (I, args): G<Value> {
    argCount("realpath", args, 1, 1);
    return I.requireFS("read", wantStr("path", args[0]));
  }));
  ns.set("kind", bi("kind", function* (I, args): G<Value> {
    argCount("kind", args, 1, 1);
    const path = wantStr("path", args[0]);
    const real = I.requireFS("read", path);
    try {
      const stat = nodeFs.statSync(real);
      if (stat.isFile()) return "file";
      if (stat.isDirectory()) return "dir";
      return "other";
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code === "ENOENT" || code === "ENOTDIR") return NONE;
      throw filesystemError(error, path);
    }
  }));
  ns.set("listdir", bi("listdir", function* (I, args): G<Value> {
    argCount("listdir", args, 1, 1);
    const path = wantStr("path", args[0]);
    const real = I.requireFS("read", path);
    try {
      return new DiasporeList(nodeFs.readdirSync(real).sort(strCmp));
    } catch (error) {
      throw filesystemError(error, path);
    }
  }));
  return new NativeModule("_os", ns);
}

function filesystemError(error: unknown, path: string): DiasporeError {
  const err = error as NodeJS.ErrnoException;
  switch (err.code) {
    case "ENOENT":
      return diasporeErr("FileNotFoundError", `[Errno 2] No such file or directory: ${reprString(path)}`);
    case "ENOTDIR":
      return diasporeErr("NotADirectoryError", `[Errno 20] Not a directory: ${reprString(path)}`);
    case "EACCES":
    case "EPERM":
      return diasporeErr("PermissionError", `[Errno 13] Permission denied: ${reprString(path)}`);
    default:
      return diasporeErr("OSError", err.message);
  }
}

/** Open a file after enforcing the interpreter's filesystem policy. */
export function* biOpen(I: Interp, args: Value[], kw: KwArgs): G<Value> {
  argCount("open", args, 1, 2);
  const path = wantStr("path", args[0]);
  let mode = "r";
  if (args.length === 2) mode = wantStr("mode", args[1]);
  const kv = kw.get("mode");
  if (kv !== undefined) mode = wantStr("mode", kv);
  if (!["r", "w", "a", "rb", "wb"].includes(mode)) {
    throw diasporeErr("ValueError", `invalid mode: '${mode}'`);
  }
  const encoding = kw.get("encoding");
  if (encoding !== undefined && !(encoding instanceof PyNoneT)) {
    const name = wantStr("encoding", encoding).toLowerCase().replace(/_/g, "-");
    if (mode.includes("b")) {
      throw diasporeErr("ValueError", "binary mode doesn't take an encoding argument");
    }
    if (name !== "utf-8" && name !== "utf8") {
      throw diasporeErr("NotImplementedError", "only UTF-8 text is supported");
    }
  }
  const kind = mode === "r" || mode === "rb" ? "read" : "write";
  const real = I.requireFS(kind, path);
  let flags: string;
  switch (mode) {
    case "r":
    case "rb":
      flags = "r";
      break;
    case "a":
      flags = "a";
      break;
    default:
      flags = "w";
  }
  let fd: number;
  try {
    fd = nodeFs.openSync(real, flags, 0o644);
  } catch (e) {
    const err = e as NodeJS.ErrnoException;
    if (err.code === "ENOENT") {
      throw diasporeErr("OSError", `[Errno 2] No such file or directory: ${reprString(path)}`);
    }
    if (err.code === "EISDIR") {
      throw diasporeErr("OSError", `[Errno 21] Is a directory: ${reprString(path)}`);
    }
    throw diasporeErr("OSError", err.message);
  }
  return new DiasporeFile(fd, mode === "rb" || mode === "wb", kind === "write");
}

function readAllFrom(f: DiasporeFile): Uint8Array {
  const chunks: Buffer[] = [];
  if (f.buf.length > 0) {
    chunks.push(Buffer.from(f.buf));
    f.buf = new Uint8Array(0);
  }
  const tmp = Buffer.alloc(65536);
  for (;;) {
    const n = nodeFs.readSync(f.fd, tmp, 0, tmp.length, null);
    if (n === 0) break;
    chunks.push(Buffer.from(tmp.subarray(0, n)));
  }
  return new Uint8Array(Buffer.concat(chunks));
}

function readNFrom(f: DiasporeFile, n: number): Uint8Array {
  const out = Buffer.alloc(n);
  let got = 0;
  if (f.buf.length > 0) {
    const take = Math.min(n, f.buf.length);
    out.set(f.buf.subarray(0, take), 0);
    f.buf = f.buf.subarray(take);
    got = take;
  }
  while (got < n) {
    const r = nodeFs.readSync(f.fd, out, got, n - got, null);
    if (r === 0) break;
    got += r;
  }
  return new Uint8Array(out.subarray(0, got));
}

function readLineFrom(f: DiasporeFile): Uint8Array {
  const chunks: number[] = [];
  for (;;) {
    if (f.buf.length === 0) {
      const tmp = Buffer.alloc(8192);
      const n = nodeFs.readSync(f.fd, tmp, 0, tmp.length, null);
      if (n === 0) break;
      f.buf = new Uint8Array(tmp.subarray(0, n));
    }
    const nl = f.buf.indexOf(0x0a);
    if (nl >= 0) {
      for (let i = 0; i <= nl; i++) chunks.push(f.buf[i]);
      f.buf = f.buf.subarray(nl + 1);
      return new Uint8Array(chunks);
    }
    for (let i = 0; i < f.buf.length; i++) chunks.push(f.buf[i]);
    f.buf = new Uint8Array(0);
  }
  return new Uint8Array(chunks);
}

/** Resolve a sandbox file method by name. */
export function fileMethodTable(I: Interp, f: DiasporeFile, n: string): Value | null {
  switch (n) {
    case "read":
      return bi("read", function* (_I, args): G<Value> {
        needArgs("read", args, 0, 1);
        if (f.closed) throw diasporeErr("ValueError", "I/O operation on closed file.");
        let count = -1n;
        if (args.length === 1 && !(args[0] instanceof PyNoneT)) {
          const v = asIndex(args[0]);
          if (v === null) {
            throw diasporeErr(
              "TypeError",
              `argument should be integer or None, not '${typeName(args[0])}'`,
            );
          }
          count = v;
        }
        const data = count < 0n ? readAllFrom(f) : readNFrom(f, Number(count));
        if (f.binary) return data;
        return decodeBytes(data, "utf-8");
      });
    case "readline":
      return bi("readline", function* (): G<Value> {
        if (f.closed) throw diasporeErr("ValueError", "I/O operation on closed file.");
        const line = readLineFrom(f);
        if (f.binary) return line;
        return decodeBytes(line, "utf-8");
      });
    case "write":
      return bi("write", function* (_I, args): G<Value> {
        needArgs("write", args, 1, 1);
        if (f.closed) throw diasporeErr("ValueError", "I/O operation on closed file.");
        if (f.binary) {
          if (!(args[0] instanceof Uint8Array)) {
            throw diasporeErr(
              "TypeError",
              `a bytes-like object is required, not '${typeName(args[0])}'`,
            );
          }
          nodeFs.writeSync(f.fd, args[0]);
          return BigInt(args[0].length);
        }
        if (typeof args[0] !== "string") {
          throw diasporeErr("TypeError", `write() argument must be str, not ${typeName(args[0])}`);
        }
        nodeFs.writeSync(f.fd, Buffer.from(args[0], "utf8"));
        return BigInt(strLen(args[0]));
      });
    case "close":
      return bi("close", function* (I2): G<Value> {
        I2.closeFile(f);
        return NONE;
      });
  }
  return null;
}
