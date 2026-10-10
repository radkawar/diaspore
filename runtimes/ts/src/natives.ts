/* Native modules (spec 11), capability-checked files, and unsupported-module errors. */

import * as nodeFs from "node:fs";
import { randomBytes } from "node:crypto";
import { isatty } from "node:tty";
import { acquireBuffer } from "./buffers.ts";
import { markInterpreterSuspended } from "./lifecycle.ts";
import { frameObject } from "./introspection.ts";

import {
  type Value,
  isPyString,
  strText,
  type G,
  type KwArgs,
  NONE,
  PyNoneT,
  DiasporeList,
  DiasporeTuple,
  DiasporeDict,
  DiasporeNamespace,
  DiasporeClass,
  DiasporeInstance,
  DiasporeFile,
  NativeModule,
  NativeObject,
  Builtin,
  Property,
  typeName,
  asIndex,
  asFloat,
  strLen,
  strCmp,
  diasporeErr,
  DiasporeError,
  EXC_CLASSES,
  makeExc,
  hashValue,
  StaticMethodV,
  SuspendSig,
} from "./values.ts";
import { reprString, round6 } from "./format.ts";
import { argCount, needArgs, wantStr, noKw } from "./helpers.ts";
import { bi, readStdinSync } from "./builtins.ts";
import { decodeBytes, encodeText } from "./codecs.ts";
import { makeReModule } from "./re.ts";
import { makeJSONModule } from "./json.ts";
import { makeMathModule } from "./math.ts";
import { makeHashModule } from "./hash.ts";
import { makeABCModule } from "./abc.ts";
import { indexInteger } from "./buffers.ts";
import { loadStdlibStore, makeStdlibModule } from "./stdlib.ts";
import { makeExceptionHook } from "./sys-exceptions.ts";
import { binop } from "./ops.ts";
import { iterToArray } from "./iter.ts";
import { initializeSlots } from "./slots.ts";
import type { Interp } from "./interp.ts";

function hostPath(value: Value): string {
  const path = wantStr("path", value);
  if (typeof path !== "string") encodeText(path, "utf-8", "surrogateescape");
  return strText(path);
}

/** Construct the native module namespace for an interpreter. */
export function makeNativeModules(I: Interp): Map<string, NativeModule> {
  return new Map([
    ["builtins", new NativeModule("builtins", I.builtins)],
    ["time", makeTimeModule(I)],
    ["math", makeMathModule()],
    ["sys", makeSysModule(I)],
    ["_os", makeOSModule(I)],
    ["_json", makeJSONModule(I)],
    ["_abc", makeABCModule(I)],
    ["_diaspore_stdlib", makeStdlibModule(() => I.stdlibStore ?? loadStdlibStore())],
    ["_diaspore_hash", makeHashModule()],
    ["_operator", new NativeModule("_operator", new Map<string, Value>([
      ["__doc__", "Operator primitives for the portable standard library."],
      ["index", bi("index", function* (I2, args, kw): G<Value> {
        noKw("index", kw);
        argCount("index", args, 1, 1);
        return yield* indexInteger(I2, args[0]);
      })],
      ["add", bi("add", function* (I2, args, kw): G<Value> {
        noKw("add", kw);
        argCount("add", args, 2, 2);
        return yield* binop(I2, "add", args[0], args[1]);
      })],
    ]))],
    ["net", makeStubModule("net", ["listen", "connect", "accept", "recv", "send", "close", "peer"])],
    ["re", makeReModule(I)],
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
  const perfFn = bi("perf_counter", function* (runtime, args, kwargs): G<Value> {
    noKw("time.perf_counter", kwargs);
    if (args.length) throw diasporeErr("TypeError", `time.perf_counter() takes no arguments (${args.length} given)`);
    return yield* monoFn.fn(runtime, args, kwargs);
  });
  return new NativeModule(
    "time",
    new Map<string, Value>([
      ["time", I.journaled("time", "time", timeFn)],
      ["monotonic", I.journaled("time", "monotonic", monoFn)],
      ["perf_counter", I.journaled("time", "perf_counter", perfFn)],
      ["sleep", I.journaled("time", "sleep", sleepFn)],
    ]),
  );
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
  const namespaceClass = new DiasporeClass("SimpleNamespace", [], new Map());
  namespaceClass.ns.set("__init__", bi("__init__", function* (_I, args, kw): G<Value> {
    argCount("SimpleNamespace", args, 1, 1);
    for (const [key, value] of kw) (args[0] as DiasporeInstance).d.set(key, value);
    return NONE;
  }));
  const implementation = new DiasporeInstance(namespaceClass);
  const version = new DiasporeInstance(new DiasporeClass("version_info", [I.types.get("tuple")!], new Map()));
  const versionItems: Value[] = [3n, 12n, 12n, "final", 0n];
  version.prim = new DiasporeTuple(versionItems);
  for (const [index, key] of ["major", "minor", "micro", "releaselevel", "serial"].entries()) version.d.set(key, versionItems[index]);
  function structSequence(name: string, fields: string[], values: Value[]): DiasporeInstance {
    const cls = new DiasporeClass(name, [I.types.get("tuple")!], new Map<string, Value>([
      ["__module__", "sys"], ["__slots__", new DiasporeTuple()],
      ["n_fields", BigInt(fields.length)], ["n_sequence_fields", BigInt(fields.length)], ["n_unnamed_fields", 0n],
    ]));
    cls.runtime = I;
    I.drive(initializeSlots(I, cls));
    const readonly = bi("readonly", function* (): G<Value> {
      throw diasporeErr("AttributeError", "readonly attribute");
    });
    for (const [index, field] of fields.entries()) {
      cls.ns.set(field, new Property(bi(field, function* (_I, args): G<Value> {
        return ((args[0] as DiasporeInstance).prim as DiasporeTuple).items[index];
      }), readonly, readonly));
    }
    cls.ns.set("__new__", new StaticMethodV(bi("__new__", function* (I2, args, kw): G<Value> {
      for (const key of kw.keys()) {
        if (key !== "sequence" && key !== "dict") throw diasporeErr("TypeError", `'${key}' is an invalid keyword argument for structseq()`);
      }
      if (args.length > 3) throw diasporeErr("TypeError", `structseq() takes at most 2 arguments (${args.length - 1} given)`);
      for (const [index, key] of ["sequence", "dict"].entries()) {
        if (args.length > index + 1 && kw.has(key)) {
          throw diasporeErr("TypeError", `argument for structseq() given by name ('${key}') and position (${index + 1})`);
        }
      }
      const sequence = args[1] ?? kw.get("sequence");
      if (sequence === undefined) throw diasporeErr("TypeError", "structseq() missing required argument 'sequence' (pos 1)");
      const dictionary = args[2] ?? kw.get("dict");
      if (dictionary !== undefined && !(dictionary instanceof DiasporeDict) &&
          !(dictionary instanceof DiasporeInstance && dictionary.prim instanceof DiasporeDict)) {
        throw diasporeErr("TypeError", `structseq() argument 2 must be dict, not ${typeName(dictionary)}`);
      }
      const items = yield* iterToArray(I2, sequence);
      if (items.length !== fields.length) {
        throw diasporeErr("TypeError", `sys.${name}() takes a ${fields.length}-sequence (${items.length}-sequence given)`);
      }
      const instance = new DiasporeInstance(cls);
      instance.prim = new DiasporeTuple(items);
      return instance;
    })));
    cls.ns.set("__repr__", bi("__repr__", function* (I2, args): G<Value> {
      const items = ((args[0] as DiasporeInstance).prim as DiasporeTuple).items;
      const parts: string[] = [];
      for (const [index, field] of fields.entries()) parts.push(field + "=" + (yield* I2.repr(items[index])));
      return `sys.${name}(` + parts.join(", ") + ")";
    }));
    cls.ns.set("__reduce__", bi("__reduce__", function* (_I, args, kw): G<Value> {
      noKw("__reduce__", kw);
      argCount("__reduce__", args, 1, 1);
      return new DiasporeTuple([cls, new DiasporeTuple([
        (args[0] as DiasporeInstance).prim!, new DiasporeDict(),
      ])]);
    }));
    cls.ns.set("__init_subclass__", bi("__init_subclass__", function* (): G<Value> {
      throw diasporeErr("TypeError", `type 'sys.${name}' is not an acceptable base type`);
    }));
    const instance = new DiasporeInstance(cls);
    instance.prim = new DiasporeTuple(values);
    return instance;
  }
  const floatInfo = structSequence("float_info",
    ["max", "max_exp", "max_10_exp", "min", "min_exp", "min_10_exp", "dig", "mant_dig", "epsilon", "radix", "rounds"],
    [Number.MAX_VALUE, 1024n, 308n, 2 ** -1022, -1021n, -307n, 15n, 53n, Number.EPSILON, 2n, 1n]);
  // V8 BigInt uses uintptr_t digits; conversion limits are not enabled by this runtime.
  const digitBits = ["arm", "ia32", "mips", "mipsel"].includes(process.arch) ? 32n : 64n;
  const intInfo = structSequence("int_info",
    ["bits_per_digit", "sizeof_digit", "default_max_str_digits", "str_digits_check_threshold"],
    [digitBits, digitBits / 8n, 0n, 0n]);
  const hashInfo = structSequence("hash_info",
    ["width", "modulus", "inf", "nan", "imag", "algorithm", "hash_bits", "seed_bits", "cutoff"],
    [64n, (1n << 61n) - 1n, hashValue(Infinity), hashValue(NaN), 1000003n, "fnv1a64-63", 63n, 0n, 0n]);
  implementation.d = new Map<string, Value>([
    ["name", "diaspore"], ["version", version], ["hexversion", 0x030c0cf0n], ["cache_tag", NONE],
  ]);
  const flags = new DiasporeInstance(new DiasporeClass("flags", [], new Map()));
  for (const name of ["debug", "inspect", "interactive", "optimize", "dont_write_bytecode",
    "no_user_site", "no_site", "ignore_environment", "verbose", "bytes_warning", "quiet",
    "hash_randomization", "isolated", "dev_mode", "utf8_mode", "warn_default_encoding",
    "safe_path", "int_max_str_digits"]) flags.d.set(name, 0n);
  const unraisable = bi("unraisablehook", function* (I2, args): G<Value> {
    argCount("unraisablehook", args, 1, 1);
    const record = args[0];
    const error = yield* I2.getAttr(record, "exc_value");
    const message = yield* I2.getAttr(record, "err_msg");
    const object = yield* I2.getAttr(record, "object");
    if (!I2.replaying) {
      I2.ewrite((message instanceof PyNoneT ? "Exception ignored in" : yield* I2.str(message)) + ": " + (yield* I2.repr(object)) + "\n");
      I2.ewrite(typeName(error) + ": " + (yield* I2.str(error)) + "\n");
    }
    return NONE;
  });
  const excepthook = makeExceptionHook();
  const auditHooks: Value[] = [];
  function* audit(I2: Interp, event: string, args: DiasporeTuple): G<void> {
    // Iterate the live list: hooks added by a hook also receive this event.
    for (const hook of auditHooks) {
      let canTrace: Value = false;
      try {
        canTrace = yield* I2.getAttr(hook, "__cantrace__");
      } catch (error) {
        if (!(error instanceof DiasporeError && error.value instanceof DiasporeInstance &&
            error.value.cls.issub(EXC_CLASSES.get("AttributeError")!))) throw error;
      }
      // CPython performs this lookup and truth conversion even without tracing.
      yield* I2.truth(canTrace);
      yield* I2.invoke(hook, [event, args], new Map());
    }
  }
  return new NativeModule(
    "sys",
    new Map<string, Value>([
      ["audit", bi("audit", function* (I2, args, kw): G<Value> {
        noKw("audit", kw);
        if (args.length === 0) {
          throw diasporeErr("TypeError", "audit() missing 1 required positional argument: 'event'");
        }
        if (auditHooks.length === 0) return NONE;
        const event = args[0] instanceof DiasporeInstance && args[0].prim !== null ? args[0].prim : args[0];
        if (!isPyString(event)) {
          throw diasporeErr("TypeError", `expected str for argument 'event', not ${typeName(args[0])}`);
        }
        // The C API validates UTF-8, then treats the event as a NUL-terminated string.
        encodeText(event);
        yield* audit(I2, strText(event).split("\0", 1)[0], new DiasporeTuple(args.slice(1)));
        return NONE;
      })],
      ["addaudithook", bi("addaudithook", function* (I2, args, kw): G<Value> {
        const count = args.length + kw.size;
        if (count > 1) {
          const kind = args.length === 0 ? "keyword argument" : "argument";
          throw diasporeErr("TypeError", `addaudithook() takes at most 1 ${kind} (${count} given)`);
        }
        const hook = args[0] ?? kw.get("hook");
        if (hook === undefined) {
          throw diasporeErr("TypeError", "addaudithook() missing required argument 'hook' (pos 1)");
        }
        try {
          yield* audit(I2, "sys.addaudithook", new DiasporeTuple());
        } catch (error) {
          if (error instanceof DiasporeError && error.value instanceof DiasporeInstance &&
              error.value.cls.issub(EXC_CLASSES.get("Exception")!)) return NONE;
          throw error;
        }
        // Registration deliberately does not check callability.
        auditHooks.push(hook);
        return NONE;
      })],
      ["unraisablehook", unraisable],
      ["__unraisablehook__", unraisable],
      ["excepthook", excepthook],
      ["__excepthook__", excepthook],
      ["argv", new DiasporeList(I.argv.map((a) => a as Value))],
      ["platform", "diaspore-ts"],
      ["byteorder", new Uint8Array(new Uint16Array([1]).buffer)[0] === 1 ? "little" : "big"],
      ["maxsize", (1n << 63n) - 1n],
      ["maxunicode", 0x10ffffn],
      ["version_info", version],
      ["float_info", floatInfo],
      ["int_info", intInfo],
      ["hash_info", hashInfo],
      ["version", "3.12.12 (Diaspore)"],
      ["hexversion", 0x030c0cf0n],
      ["implementation", implementation],
      ["flags", flags],
      ["warnoptions", new DiasporeList()],
      ["_xoptions", new DiasporeDict()],
      ["dont_write_bytecode", true],
      ["getdefaultencoding", bi("getdefaultencoding", function* (_I, args): G<Value> { argCount("getdefaultencoding", args, 0, 0); return "utf-8"; })],
      ["getfilesystemencoding", bi("getfilesystemencoding", function* (_I, args): G<Value> { argCount("getfilesystemencoding", args, 0, 0); return "utf-8"; })],
      ["getfilesystemencodeerrors", bi("getfilesystemencodeerrors", function* (_I, args): G<Value> { argCount("getfilesystemencodeerrors", args, 0, 0); return "surrogateescape"; })],
      ["_getframe", bi("_getframe", function* (I2, args): G<Value> {
        argCount("_getframe", args, 0, 1);
        const depth = asIndex(args[0] ?? 0n);
        if (depth === null) throw diasporeErr("TypeError", "depth must be an integer");
        const frame = I2.frames[I2.frames.length - 1 - Number(depth < 0n ? 0n : depth)];
        if (frame === undefined) throw diasporeErr("ValueError", "call stack is not deep enough");
        return frameObject(I2, frame);
      })],
      ["modules", new DiasporeNamespace(I.modules)],
      ["builtin_module_names", new DiasporeTuple(["_abc", "_diaspore_stdlib", "_json", "_operator", "_os", "builtins", "flow", "math", "net", "posix", "re", "sys", "time"])],
      ["intern", bi("intern", function* (_I, args): G<Value> {
        argCount("intern", args, 1, 1);
        return wantStr("intern argument", args[0]);
      })],
      ["exception", bi("exception", function* (I2, args): G<Value> {
        argCount("exception", args, 0, 0);
        return I2.excStack.at(-1) ?? NONE;
      })],
      ["exc_info", bi("exc_info", function* (I2, args): G<Value> {
        argCount("exc_info", args, 0, 0);
        const error = I2.excStack.at(-1);
        return new DiasporeTuple(error === undefined ? [NONE, NONE, NONE] :
          [error.cls, error, error.exc.get("__traceback__") ?? NONE]);
      })],
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
          const payload = I.journalValue(args.length === 1 ? args[0] : NONE, "flow.suspend");
          markInterpreterSuspended(I);
          throw new SuspendSig(payload);
        }),
      ],
    ]),
  );
}

// --------------------------------------------------------------------------
// open() and file methods (capability "fs")

function makeOSModule(owner: Interp): NativeModule {
  const ns = new Map<string, Value>();
  const descriptors = new Map<bigint, { fd: number; position: number; read: boolean; write: boolean; inheritable: boolean }>();
  owner.closeResources.push(() => {
    let failure: unknown;
    for (const { fd } of descriptors.values()) {
      try { nodeFs.closeSync(fd); } catch (error) { failure ??= error; }
    }
    descriptors.clear();
    if (failure !== undefined) throw filesystemError(failure, "<descriptor>");
  });
  let nextDescriptor = 3n;
  ns.set("urandom", bi("urandom", function* (I, args): G<Value> {
    argCount("urandom", args, 1, 1);
    const count = asIndex(args[0]);
    if (count === null) throw diasporeErr("TypeError", "an integer is required");
    if (count < 0n) throw diasporeErr("ValueError", "negative argument not allowed");
    if (I.deterministic) throw diasporeErr("PermissionError", "entropy is unavailable in deterministic mode");
    I.fuelN(Number(count / 16n + 1n));
    if (!I.durable) return new Uint8Array(randomBytes(Number(count)));
    const encoded = yield* I.effect("_os", "urandom", function* (): G<Value> { return randomBytes(Number(count)).toString("hex"); });
    if (typeof encoded !== "string") throw diasporeErr("TypeError", "urandom journal entry must be a hexadecimal string");
    return new Uint8Array(Buffer.from(encoded, "hex"));
  }));
  ns.set("open", bi("open", function* (I, args): G<Value> {
    argCount("open", args, 2, 3);
    const path = hostPath(args[0]), flags = asIndex(args[1]), mode = asIndex(args[2] ?? 0o777n);
    if (flags === null || mode === null) throw diasporeErr("TypeError", "an integer is required");
    const access = Number(flags & 3n);
    if (access === 3) throw diasporeErr("OSError", "[Errno 22] Invalid argument");
    const read = access !== 1, write = access !== 0;
    let real = I.requireFS(write || (flags & (64n | 512n)) !== 0n ? "write" : "read", path);
    if (read) real = I.requireFS("read", path);
    const mappings: Record<string, number> = { "64": nodeFs.constants.O_CREAT, "128": nodeFs.constants.O_EXCL,
      "512": nodeFs.constants.O_TRUNC, "1024": nodeFs.constants.O_APPEND, "2048": nodeFs.constants.O_NONBLOCK,
      "65536": nodeFs.constants.O_DIRECTORY, "131072": nodeFs.constants.O_NOFOLLOW };
    let nativeFlags = access === 0 ? nodeFs.constants.O_RDONLY : access === 1 ? nodeFs.constants.O_WRONLY : nodeFs.constants.O_RDWR;
    for (const [bit, flag] of Object.entries(mappings)) if ((flags & BigInt(bit)) !== 0n) nativeFlags |= flag;
    try {
      const fd = nodeFs.openSync((flags & 131072n) !== 0n ? path : real, nativeFlags, Number(mode));
      const descriptor = nextDescriptor++;
      descriptors.set(descriptor, { fd, position: 0, read, write, inheritable: false });
      return descriptor;
    } catch (error) { throw filesystemError(error, path); }
  }));
  for (const operation of ["read", "write", "lseek", "ftruncate", "close", "fstat", "isatty", "get_inheritable", "set_inheritable"]) {
    ns.set(operation, bi(operation, function* (I, args): G<Value> {
      const count = operation === "lseek" ? 3 : ["read", "write", "ftruncate", "set_inheritable"].includes(operation) ? 2 : 1;
      argCount(operation, args, count, count);
      I.requireCap("fs");
      const id = asIndex(args[0]);
      if (id === null) throw diasporeErr("TypeError", "an integer is required");
      const descriptor = descriptors.get(id);
      if (descriptor === undefined) throw diasporeErr("OSError", "[Errno 9] Bad file descriptor");
      const { fd } = descriptor;
      try {
        switch (operation) {
          case "close": nodeFs.closeSync(fd); descriptors.delete(id); return NONE;
          case "fstat": return statMapping(nodeFs.fstatSync(fd, { bigint: true }));
          case "isatty": return isatty(fd);
          case "get_inheritable": return descriptor.inheritable;
          case "set_inheritable": descriptor.inheritable = yield* I.truth(args[1]); return NONE;
          case "read": {
            if (!descriptor.read) throw diasporeErr("OSError", "[Errno 9] Bad file descriptor");
            const count = asIndex(args[1]);
            if (count === null) throw diasporeErr("TypeError", "an integer is required");
            if (count < 0n) throw diasporeErr("OSError", "[Errno 22] Invalid argument");
            I.fuelN(Number(count / 16n + 1n));
            const data = new Uint8Array(Number(count));
            const read = nodeFs.readSync(fd, data, 0, data.length, descriptor.position);
            descriptor.position += read;
            return data.subarray(0, read);
          }
          case "write": {
            if (!descriptor.write) throw diasporeErr("OSError", "[Errno 9] Bad file descriptor");
            const lease = acquireBuffer(args[1]);
            if (lease === null) throw diasporeErr("TypeError", "a bytes-like object is required");
            try {
              const written = nodeFs.writeSync(fd, lease.data, 0, lease.data.length, descriptor.position);
              descriptor.position += written;
              return BigInt(written);
            } finally { lease.release(); }
          }
          case "lseek": {
            const offset = asIndex(args[1]), whence = asIndex(args[2]);
            if (offset === null || whence === null) throw diasporeErr("TypeError", "an integer is required");
            if (whence < 0n || whence > 2n) throw diasporeErr("OSError", "[Errno 22] Invalid argument");
            const position = Number(offset) + (whence === 0n ? 0 : whence === 1n ? descriptor.position : nodeFs.fstatSync(fd).size);
            if (!Number.isSafeInteger(position) || position < 0) throw diasporeErr("OSError", "[Errno 22] Invalid argument");
            descriptor.position = position;
            return BigInt(position);
          }
          case "ftruncate": {
            if (!descriptor.write) throw diasporeErr("OSError", "[Errno 9] Bad file descriptor");
            const length = asIndex(args[1]);
            if (length === null) throw diasporeErr("TypeError", "an integer is required");
            if (length < 0n) throw diasporeErr("OSError", "[Errno 22] Invalid argument");
            nodeFs.ftruncateSync(fd, Number(length)); return NONE;
          }
        }
      } catch (error) {
        if (error instanceof DiasporeError) throw error;
        throw filesystemError(error, String(id));
      }
      throw diasporeErr("RuntimeError", "unknown descriptor operation");
    }));
  }
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
    return I.requireFS("read", hostPath(args[0]));
  }));
  ns.set("mkdir", bi("mkdir", function* (I, args): G<Value> {
    argCount("mkdir", args, 1, 2);
    const path = hostPath(args[0]), mode = asIndex(args[1] ?? 0o777n);
    if (mode === null) throw diasporeErr("TypeError", "mode must be an integer");
    const real = I.requireFS("write", path);
    try { nodeFs.mkdirSync(real, { mode: Number(mode) }); }
    catch (error) { throw filesystemError(error, path); }
    return NONE;
  }));
  ns.set("kind", bi("kind", function* (I, args): G<Value> {
    argCount("kind", args, 1, 1);
    const path = hostPath(args[0]);
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
  for (const operation of ["stat", "lstat", "readlink"]) {
    ns.set(operation, bi(operation, function* (I, args): G<Value> {
      argCount(operation, args, 1, 1);
      const path = hostPath(args[0]);
      I.requireFS("read", path);
      try {
        if (operation === "readlink") return nodeFs.readlinkSync(path);
        const stat = operation === "stat" ? nodeFs.statSync(path, { bigint: true }) :
          nodeFs.lstatSync(path, { bigint: true });
        return statMapping(stat);
      } catch (error) {
        throw filesystemError(error, path);
      }
    }));
  }
  ns.set("listdir", bi("listdir", function* (I, args): G<Value> {
    argCount("listdir", args, 1, 1);
    const path = hostPath(args[0]);
    const real = I.requireFS("read", path);
    try {
      return new DiasporeList(nodeFs.readdirSync(real).sort(strCmp));
    } catch (error) {
      throw filesystemError(error, path);
    }
  }));
  return new NativeModule("_os", ns);
}

function statMapping(stat: nodeFs.BigIntStats): DiasporeDict {
  const result = new DiasporeDict();
  for (const key of ["mode", "ino", "dev", "nlink", "uid", "gid", "size", "blksize", "blocks", "rdev"] as const) result.set("st_" + key, stat[key]);
  for (const key of ["atime", "mtime", "ctime"] as const) {
    const ns = stat[key + "Ns" as "atimeNs" | "mtimeNs" | "ctimeNs"];
    result.set("st_" + key, Number(ns) / 1e9);
    result.set("st_" + key + "_ns", ns);
  }
  return result;
}

function filesystemError(error: unknown, path: string): DiasporeError {
  const err = error as NodeJS.ErrnoException;
  const codes: Record<string, [string, bigint, string]> = {
    ENOENT: ["FileNotFoundError", 2n, "No such file or directory"],
    EEXIST: ["FileExistsError", 17n, "File exists"],
    ENOTDIR: ["NotADirectoryError", 20n, "Not a directory"],
    EISDIR: ["IsADirectoryError", 21n, "Is a directory"],
    EACCES: ["PermissionError", 13n, "Permission denied"],
    EPERM: ["PermissionError", 1n, "Operation not permitted"],
    EBADF: ["OSError", 9n, "Bad file descriptor"],
    EINVAL: ["OSError", 22n, "Invalid argument"],
  };
  const [name, number, message] = codes[err.code ?? ""] ?? ["OSError", BigInt(Math.abs(err.errno ?? 0)), err.message];
  const result = makeExc(name, number, message, path);
  return new DiasporeError(result);
}

/** Open a file after enforcing the interpreter's filesystem policy. */
export function* biOpen(I: Interp, args: Value[], kw: KwArgs): G<Value> {
  argCount("open", args, 1, 2);
  const path = hostPath(args[0]);
  let mode = "r";
  if (args.length === 2) mode = strText(wantStr("mode", args[1]));
  const kv = kw.get("mode");
  if (kv !== undefined) mode = strText(wantStr("mode", kv));
  if (!["r", "w", "a", "rb", "wb"].includes(mode)) {
    throw diasporeErr("ValueError", `invalid mode: '${mode}'`);
  }
  const encoding = kw.get("encoding");
  if (encoding !== undefined && !(encoding instanceof PyNoneT)) {
    const name = strText(wantStr("encoding", encoding)).toLowerCase().replace(/_/g, "-");
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
  const file = new DiasporeFile(fd, mode === "rb" || mode === "wb", kind === "write");
  I.closeResources.push(() => I.closeFile(file));
  return file;
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
        if (!isPyString(args[0])) {
          throw diasporeErr("TypeError", `write() argument must be str, not ${typeName(args[0])}`);
        }
        nodeFs.writeSync(f.fd, encodeText(args[0]));
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
