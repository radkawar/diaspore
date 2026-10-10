/* The Diaspore interpreter: frames, execution, calls, attributes, modules,
 * capabilities, fuel, and durable-flow journaling. Mirrors src/diaspore/vm.py. */

import * as nodeFs from "node:fs";
import * as nodePath from "node:path";

import {
  type Value,
  type PyString,
  isPyString,
  strText,
  strKey,
  strFromKey,
  strFromChars,
  strConcat,
  type G,
  type YieldMsg,
  type KwArgs,
  NONE,
  ELLIPSIS,
  NOT_IMPLEMENTED,
  PyNoneT,
  DiasporeList,
  DiasporeTuple,
  DiasporeDict,
  DiasporeMappingProxy,
  DiasporeNamespace,
  DiasporeSet,
  DiasporeFrozenSet,
  DiasporeSlice,
  DiasporeRange,
  DiasporeClass,
  DiasporeInstance,
  DiasporeFunction,
  BoundMethod,
  StaticMethodV,
  ClassMethodV,
  SuperObject,
  SuperMarkerT,
  DiasporeModule,
  NativeModule,
  Builtin,
  BuiltinType,
  NativeObject,
  DiasporeFile,
  Property,
  Complex,
  CodeObject,
  DiasporeIterBase,
  OBJECT,
  EXC_CLASSES,
  makeGroup,
  type BuiltinFn,
  storedHashKey,
  typeName,
  diasporeErr,
  makeExc,
  DiasporeError,
  DiasporeTrap,
  BreakSig,
  BREAK,
  ContinueSig,
  CONTINUE,
  ReturnSig,
  SuspendSig,
  excIsA,
  excArgs,
  BASE_EXC,
} from "./values.ts";
import { bufferBytes } from "./buffers.ts";
import { closeInterpreter } from "./lifecycle.ts";
import { displayException } from "./sys-exceptions.ts";
import { functionCode, functionClosure, replaceFunctionCode, attachTraceback } from "./introspection.ts";
import {
  binop,
  unary,
  compare,
  eq,
  eqElem,
  contains,
  truth,
  diasporeLen,
  getIndex,
  setIndex,
  delIndex,
  reprOf,
  strOf,
  excStr,
  asciiOf,
  sortValues,
} from "./ops.ts";
import { getIter, iterToArray, DiasporeGenerator, ListReverseIter, RangeIter } from "./iter.ts";
import { callableName, sliceIndices } from "./ops.ts";
import { builtinMethod, methodAllowed, builtinMethodNames, makeStrTranslation, makeBytesTranslation, parseBytesHex } from "./methods.ts";
import { argCount, noKw } from "./helpers.ts";
import { formatValue } from "./format.ts";
import { parseFloatHex, formatFloatHex } from "./numeric.ts";
import { makeBuiltins, dictUpdateFrom } from "./builtins.ts";
import { loadStdlibStore, validateStdlibStore, type StdlibStore } from "./stdlib.ts";
import { abcCollectionFlags } from "./abc.ts";
import { objectReduce, objectGetState, setReduce } from "./object-protocols.ts";
import { initializeSlots, instanceHasDict, SlotDescriptor } from "./slots.ts";
import { makeNativeModules } from "./natives.ts";
import { BUILTIN_DOCS } from "./builtin-docs.ts";
import { fromHost, toHost } from "./host.ts";

type Node = Record<string, any>;

function has(n: Node, k: string): boolean {
  return n[k] !== undefined && n[k] !== null;
}

const EXCEPTION_MEMBERS = new Set(["args", "__cause__", "__context__", "__traceback__", "__suppress_context__"]);
const OSERROR_MEMBERS = new Set(["errno", "strerror", "filename", "filename2"]);
const SYNTAX_ERROR_MEMBERS = new Set(["msg", "filename", "lineno", "offset", "text", "end_lineno", "end_offset", "print_file_and_line"]);

function exceptionMember(cls: DiasporeClass, name: string): boolean {
  return EXCEPTION_MEMBERS.has(name) ||
    (OSERROR_MEMBERS.has(name) && cls.issub(EXC_CLASSES.get("OSError")!)) ||
    (SYNTAX_ERROR_MEMBERS.has(name) && cls.issub(EXC_CLASSES.get("SyntaxError")!));
}

/** Execution scope with lexical parents and module globals. */
export class Frame {
  vars: Map<string, Value>;
  parent: Frame | null;
  localset: Set<string> | null; // null => non-strict scope (module/class)
  gdecl: Set<string>;
  ndecl: Set<string>;
  isClass: boolean;
  annotationScope = false;
  globals: Map<string, Value>;
  isGen = false;
  line = 0;
  fname: string;
  code: CodeObject | null = null;
  back: Frame | null = null;
  moduleName: string;
  defclass: DiasporeClass | null = null;
  self_: Value | null = null;

  constructor(
    vars: Map<string, Value>,
    parent: Frame | null,
    localset: Set<string> | null,
    gdecl: Set<string>,
    ndecl: Set<string>,
    isClass: boolean,
    globals_: Map<string, Value> | null,
    fname: string,
    moduleName: string,
  ) {
    this.vars = vars;
    this.parent = parent;
    this.localset = localset;
    this.gdecl = gdecl;
    this.ndecl = ndecl;
    this.isClass = isClass;
    this.globals = globals_ ?? vars;
    this.fname = fname;
    this.moduleName = moduleName;
  }
}

/** A recorded effect result used during durable replay. */
export interface JournalEntry {
  m: string;
  f: string;
  r: Value;
}

/** Scoped capability grants indexed by capability and operation. */
export type Policy = Record<string, Record<string, string[]>>;

/** A synchronous host function exposed to sandboxed code. */
export type HostFunction = (...args: unknown[]) => unknown;

/** Host modules available through sandbox imports. */
export type Bindings = Record<string, Record<string, HostFunction>>;

/** Embedding options controlling authority, execution limits, and I/O. */
export interface InterpOpts {
  argv?: string[];
  bindings?: Bindings;
  caps?: Iterable<string>;
  policy?: Policy | null;
  maxSteps?: number;
  maxDepth?: number;
  durable?: boolean;
  deterministic?: boolean;
  journal?: JournalEntry[];
  stdout?: (s: string) => void;
  stderr?: (s: string) => void;
  stdlibStore?: StdlibStore;
}

const EMPTY_SET: Set<string> = new Set();
const OBJECT_METHODS = new Set(["__new__", "__init__", "__repr__", "__str__", "__format__", "__getattribute__", "__reduce__", "__reduce_ex__", "__getstate__"]);

// Evaluated constants, keyed by IR node (kept off the nodes themselves so the
// bundle stays byte-canonical for digesting).
const constCache = new WeakMap<object, Value>();

/** A sandbox interpreter with fuel metering and durable replay. */
export class Interp {
  bundle: Node;
  main: string;
  argv: string[];
  caps: Set<string>;
  policy: Policy;
  durable: boolean;
  deterministic: boolean;
  journal: JournalEntry[];
  replayCursor = 0;
  replaying: boolean;
  maxSteps: number; // 0 = unlimited
  maxDepth: number;
  readonly depthCap: number;
  steps = 0;
  depth = 0;
  stdoutW: (s: string) => void;
  stderrW: (s: string) => void;
  modules: Map<string, Value> = new Map();
  excStack: DiasporeInstance[] = [];
  repring: Set<Value> = new Set();
  closeResources: (() => void)[] = [];
  frames: Frame[] = [];
  classes: Set<DiasporeClass> = new Set();
  builtins: Map<string, Value>;
  types: Map<string, BuiltinType> = new Map();
  natives: Map<string, NativeModule>;
  bindings: Map<string, NativeModule> = new Map();
  readonly stdlibStore: StdlibStore | null;
  private readonly builtinDescriptors = new Map<string, Map<string, Value>>();

  constructor(bundle: Node, opts: InterpOpts = {}) {
    if (bundle.diaspore !== 1 || typeof bundle.main !== "string" || bundle.modules == null) {
      throw new Error("unsupported Diaspore bundle");
    }
    this.bundle = bundle;
    if (bundle.stdlib !== undefined) {
      if (typeof bundle.stdlib !== "string" || !/^sha256:[0-9a-f]{64}$/.test(bundle.stdlib)) {
        throw new Error("invalid Diaspore stdlib reference");
      }
      this.stdlibStore = opts.stdlibStore === undefined ? loadStdlibStore() : validateStdlibStore(opts.stdlibStore);
      if (bundle.stdlib !== this.stdlibStore.id) {
        throw new Error(`Diaspore stdlib mismatch: bundle requires ${bundle.stdlib}, runtime provides ${this.stdlibStore.id}`);
      }
    } else this.stdlibStore = null;
    this.main = bundle.main as string;
    this.argv = opts.argv !== undefined && opts.argv.length > 0 ? [...opts.argv] : [this.main];
    this.caps = new Set(opts.caps ?? []);
    this.policy = opts.policy ?? {};
    this.durable = opts.durable ?? false;
    this.deterministic = opts.deterministic ?? false;
    if (this.durable && this.caps.size > 0) {
      throw new Error("durable mode forbids capabilities; route I/O through host bindings");
    }
    if (this.deterministic && this.caps.size > 0) {
      throw new Error("deterministic mode forbids capabilities");
    }
    this.journal = opts.journal !== undefined ? [...opts.journal] : [];
    this.replaying = this.journal.length > 0;
    this.maxSteps = opts.maxSteps ?? 0;
    this.maxDepth = opts.maxDepth ?? 1000;
    this.depthCap = this.maxDepth;
    this.stdoutW = opts.stdout ?? ((s) => process.stdout.write(s));
    this.stderrW = opts.stderr ?? ((s) => process.stderr.write(s));
    this.builtins = makeBuiltins(this);
    this.natives = makeNativeModules(this);
    for (const [name, functions] of Object.entries(opts.bindings ?? {})) {
      const ns = new Map<string, Value>();
      for (const [fname, fn] of Object.entries(functions)) {
        const builtin = new Builtin(fname, function* (_I, args, kw): G<Value> {
          if (kw.size > 0) throw diasporeErr("TypeError", `${fname}() takes no keyword arguments`);
          const hostArgs = args.map(toHost);
          let result: unknown;
          try {
            result = fn(...hostArgs);
          } catch (error) {
            if (error instanceof DiasporeError || error instanceof DiasporeTrap) throw error;
            throw diasporeErr("RuntimeError", error instanceof Error ? error.message : String(error));
          }
          return fromHost(result);
        });
        ns.set(fname, this.journaled(name, fname, builtin));
      }
      this.bindings.set(name, new NativeModule(name, ns));
    }
  }

  /** Finish imported shutdown hooks and release interpreter-owned resources. */
  close(): void {
    closeInterpreter(this, () => {
      let failure: unknown;
      for (const cleanup of this.closeResources) {
        try { cleanup(); } catch (error) { failure ??= error; }
      }
      this.closeResources.length = 0;
      if (failure !== undefined) throw failure;
    });
  }

  // ------------------------------------------------------------- durability

  /** Consume the next journal entry during replay: [true, result] or
   * [false, NONE] once the journal is exhausted (live mode). */
  replayNext(mod: string, fname: string): [boolean, Value] {
    if (!this.replaying) return [false, NONE];
    const entry = this.journal[this.replayCursor];
    if (entry.m !== mod || entry.f !== fname) {
      throw new DiasporeTrap(
        `diaspore: journal divergence at entry ${this.replayCursor} ` +
          `(recorded ${entry.m}.${entry.f}, replay reached ${mod}.${fname}); ` +
          `durable programs must be deterministic`,
        5,
      );
    }
    this.replayCursor++;
    if (this.replayCursor >= this.journal.length) this.replaying = false;
    return [true, entry.r];
  }

  /** Journal entries must round-trip through JSON identically: only
   * canonical JSON value kinds are allowed. */
  journalValue(v: Value, what: string): Value {
    if (
      v instanceof PyNoneT ||
      isPyString(v) ||
      typeof v === "boolean" ||
      typeof v === "bigint" ||
      typeof v === "number"
    ) {
      return v;
    }
    if (v instanceof DiasporeList) {
      return new DiasporeList(v.items.map((x) => this.journalValue(x, what)));
    }
    if (v instanceof DiasporeDict) {
      const out = new DiasporeDict();
      for (const [key, val] of v.entries()) {
        if (!isPyString(key)) {
          throw diasporeErr("TypeError", `durable ${what} results must have str keys`);
        }
        out.set(key, this.journalValue(val, what));
      }
      return out;
    }
    throw diasporeErr(
      "TypeError",
      `durable ${what} results must be JSON-serializable ` +
        `(got ${typeName(v)}); tuples are not allowed; return lists`,
    );
  }

  *effect(mod: string, fname: string, fn: () => G<Value>): G<Value> {
    const [replayed, r] = this.replayNext(mod, fname);
    if (replayed) return r;
    const v = yield* fn();
    this.journal.push({ m: mod, f: fname, r: this.journalValue(v, `${mod}.${fname}`) });
    return v;
  }

  /** Wraps a Builtin as a journaled effect when the VM is durable. */
  journaled(mod: string, fname: string, b: Builtin): Builtin {
    if (!this.durable) return b;
    const inner = b.fn;
    const self = this;
    return new Builtin(fname, function* (I, args, kw): G<Value> {
      return yield* self.effect(mod, fname, function* () {
        return yield* inner(I, args, kw);
      });
    });
  }

  // ------------------------------------------------------------- fuel

  fuel(): void {
    if (++this.steps > this.maxSteps && this.maxSteps > 0) {
      throw new DiasporeTrap(`diaspore: step budget exceeded (${this.maxSteps})`);
    }
  }

  fuelN(n: number): void {
    this.steps += n;
    if (this.steps > this.maxSteps && this.maxSteps > 0) {
      throw new DiasporeTrap(`diaspore: step budget exceeded (${this.maxSteps})`);
    }
  }

  // ------------------------------------------------------------- entry

  /** Drive a generator that must never yield (no enclosing diaspore generator). */
  drive<T>(g: G<T>): T {
    const res = g.next(undefined);
    if (!res.done) throw new DiasporeTrap("diaspore: yield outside generator frame", 4);
    return res.value;
  }

  /** Invoke a module-level function, converting arguments and results to host values. */
  call(module: string, fn: string, ...args: unknown[]): unknown {
    const mod = this.drive(this.importModule(module));
    const callable = this.drive(this.getAttr(mod, fn));
    return toHost(this.drive(this.invoke(callable, args.map(fromHost), new Map())));
  }

  /** Execute the main module; returns the process exit code. SuspendSig
   * propagates to the caller (durable CLI handles it). */
  run(): number {
    try {
      this.drive(this.importModule(this.main));
      return 0;
    } catch (e) {
      if (e instanceof DiasporeError) {
        if (excIsA(e.value, "SystemExit")) {
          const args = excArgs(e.value as DiasporeInstance);
          const code = args.length > 0 ? args[0] : 0n;
          if (code instanceof PyNoneT) return 0;
          if (typeof code === "boolean") return code ? 1 : 0;
          if (typeof code === "bigint") return Number(BigInt.asIntN(32, code));
          this.stderrW(this.drive(strOf(this, code)) + "\n");
          return 1;
        }
        this.reportUncaught(e);
        return 1;
      }
      if (e instanceof DiasporeTrap) {
        this.stderrW(e.msg + "\n");
        return e.code;
      }
      if (e instanceof RangeError && e.message.includes("call stack")) {
        // JS stack exhaustion before the diaspore depth limit fired
        this.stderrW("RecursionError: maximum recursion depth exceeded\n");
        return 1;
      }
      throw e;
    }
  }

  printTraceback(e: DiasporeError): void {
    const tb = e.value instanceof DiasporeInstance ? e.value.exc.get("__traceback__") ?? NONE : NONE;
    this.drive(displayException(this, e.value, tb));
  }

  private exceptionHeader(namespace: Map<string, Value>, text: string): void {
    const file = namespace.get("stderr");
    if (file !== undefined) {
      try {
        this.drive(this.invoke(this.drive(this.getAttr(file, "write")), [text], new Map()));
        return;
      } catch (error) {
        if (!(error instanceof DiasporeError)) throw error;
      }
    }
    this.stderrW(text);
  }

  private reportUncaught(e: DiasporeError): void {
    const sys = this.modules.get("sys");
    const namespace = sys instanceof NativeModule ? sys.ns : sys instanceof DiasporeModule ? sys.frame.vars : undefined;
    if (namespace === undefined) { this.printTraceback(e); return; }
    const exception = e.value;
    const cls = exception instanceof DiasporeInstance ? exception.cls : NONE;
    const tb = exception instanceof DiasporeInstance ? exception.exc.get("__traceback__") ?? NONE : NONE;
    namespace.set("last_exc", exception);
    namespace.set("last_type", cls);
    namespace.set("last_value", exception);
    namespace.set("last_traceback", tb);
    const hook = namespace.get("excepthook");
    if (hook === undefined) {
      this.exceptionHeader(namespace, "sys.excepthook is missing\n");
      this.printTraceback(e);
      return;
    }
    try {
      this.drive(this.invoke(hook, [cls, exception, tb], new Map()));
    } catch (error) {
      if (!(error instanceof DiasporeError)) throw error;
      this.exceptionHeader(namespace, "Error in sys.excepthook:\n");
      this.printTraceback(error);
      this.exceptionHeader(namespace, "\nOriginal exception was:\n");
      this.printTraceback(e);
    }
  }

  // ------------------------------------------------------------- modules

  /** Resolve program modules before the exact linked store, without executing either. */
  moduleCode(name: string): Node | undefined {
    if (Object.hasOwn(this.bundle.modules, name)) return this.bundle.modules[name];
    const modules = this.stdlibStore?.modules;
    return modules !== undefined && Object.hasOwn(modules, name) ? modules[name] : undefined;
  }

  *importModule(name: string): G<Value> {
    const cached = this.modules.get(name);
    if (cached !== undefined) return cached;
    const m = this.moduleCode(name);
    if (m !== undefined) {
      let parent: Value | null = null;
      const dot = name.lastIndexOf(".");
      if (dot >= 0) {
        // packages: ancestors execute first (each exactly once)
        parent = yield* this.importModule(name.slice(0, dot));
      }
      const fr = new Frame(new Map(), null, null, EMPTY_SET, EMPTY_SET, false, null, "<module>", name);
      fr.vars.set("__name__", name === this.main ? "__main__" : name);
      fr.vars.set("__file__", m.file as string);
      const doc = m.body?.[0];
      fr.vars.set("__doc__", doc?.op === "expr" && doc.v?.op === "const" && doc.v.t === "str" ? this.constVal(doc.v) : NONE);
      fr.vars.set("__package__", dot < 0 ? "" : name.slice(0, dot));
      const mod = new DiasporeModule(name, fr);
      this.modules.set(name, mod);
      if (name === this.main) this.modules.set("__main__", mod);
      fr.back = this.frames.at(-1) ?? null;
      this.frames.push(fr);
      try {
        yield* this.execBlock(m.body as unknown[], fr);
      } catch (e) {
        if (e instanceof DiasporeError) e.tb.push({ module: name, fname: "<module>", line: fr.line });
        this.modules.delete(name);
        if (name === this.main) this.modules.delete("__main__");
        throw e;
      } finally {
        this.frames.pop();
      }
      if (parent instanceof DiasporeModule) {
        // the executed submodule becomes an attribute of its parent package
        parent.frame.vars.set(name.slice(dot + 1), mod);
      }
      return mod;
    }
    const binding = this.bindings.get(name);
    if (binding !== undefined) {
      this.modules.set(name, binding);
      return binding;
    }
    const nat = this.natives.get(name);
    if (nat !== undefined) {
      this.modules.set(name, nat);
      return nat;
    }
    throw diasporeErr("ModuleNotFoundError", `No module named '${name}'`);
  }

  // ------------------------------------------------------------- names

  loadName(fr: Frame, id: string): Value {
    if (id === ".builtins") return this.natives.get("builtins") as NativeModule;
    if (fr.gdecl.has(id)) {
      const v = fr.globals.get(id);
      if (v !== undefined) return v;
    } else if (fr.localset !== null && fr.localset.has(id)) {
      const v = fr.vars.get(id);
      if (v !== undefined) return v;
      throw diasporeErr(
        "UnboundLocalError",
        `cannot access local variable '${id}' where it is not associated with a value`,
      );
    } else {
      let f: Frame | null = fr;
      let classVisible = fr.annotationScope;
      while (f !== null) {
        if (f === fr || !f.isClass || classVisible) {
          const v = f.vars.get(id);
          if (v !== undefined) return v;
        }
        if (f !== fr && !f.annotationScope) classVisible = false;
        f = f.parent;
      }
      const g = fr.globals.get(id);
      if (g !== undefined) return g;
    }
    const b = this.builtins.get(id);
    if (b !== undefined) return b;
    throw diasporeErr("NameError", `name '${id}' is not defined`);
  }

  storeName(fr: Frame, id: string, v: Value): void {
    if (fr.gdecl.has(id)) {
      fr.globals.set(id, v);
    } else if (fr.ndecl.has(id)) {
      let f = fr.parent;
      while (f !== null) {
        if (f.localset !== null && f.localset.has(id)) {
          f.vars.set(id, v);
          return;
        }
        f = f.parent;
      }
      throw diasporeErr("RuntimeError", `nonlocal binding for '${id}' not found`);
    } else {
      fr.vars.set(id, v);
    }
  }

  // ------------------------------------------------------------- execution

  *execBlock(stmts: unknown[] | undefined | null, fr: Frame): G<void> {
    if (stmts == null) return; // Compact bundles omit empty bodies (spec 2.2).
    for (const s0 of stmts) {
      const s = s0 as Node;
      this.fuel();
      if (typeof s.ln === "number") fr.line = s.ln;
      try {
        yield* this.execStmt(s, fr);
      } catch (error) {
        if (error instanceof DiasporeError) {
          if (error.value instanceof DiasporeInstance && !error.value.exc.has("__context__")) {
            this.linkExceptionContext(error.value);
          }
          attachTraceback(this, error, fr);
        }
        throw error;
      }
    }
  }

  *execStmt(s: Node, fr: Frame): G<void> {
    switch (s.op as string) {
      case "expr":
        yield* this.ev(s.v, fr);
        return;
      case "assign": {
        const v = yield* this.ev(s.v, fr);
        for (const t of s.targets as Node[]) yield* this.assign(t, v, fr);
        return;
      }
      case "augassign":
        yield* this.exAugassign(s, fr);
        return;
      case "if":
        if (yield* this.truth(yield* this.ev(s.test, fr))) {
          yield* this.execBlock(s.then, fr);
        } else {
          yield* this.execBlock(s.orelse, fr);
        }
        return;
      case "while": {
        let broke = false;
        while (yield* this.truth(yield* this.ev(s.test, fr))) {
          this.fuel();
          try {
            yield* this.execBlock(s.body, fr);
          } catch (e) {
            if (e instanceof BreakSig) {
              broke = true;
              break;
            }
            if (e instanceof ContinueSig) continue;
            throw e;
          }
        }
        if (!broke) yield* this.execBlock(s.orelse, fr);
        return;
      }
      case "for": {
        const it = yield* getIter(this, yield* this.ev(s.iter, fr));
        let broke = false;
        for (;;) {
          this.fuel();
          const [x, ok] = yield* it.next();
          if (!ok) break;
          yield* this.assign(s.target, x, fr);
          try {
            yield* this.execBlock(s.body, fr);
          } catch (e) {
            if (e instanceof BreakSig) {
              broke = true;
              break;
            }
            if (e instanceof ContinueSig) continue;
            throw e;
          }
        }
        if (!broke) yield* this.execBlock(s.orelse, fr);
        return;
      }
      case "break":
        throw BREAK;
      case "continue":
        throw CONTINUE;
      case "pass":
        return;
      case "return":
        throw new ReturnSig(has(s, "v") ? yield* this.ev(s.v, fr) : NONE);
      case "func":
        this.storeName(fr, s.name, yield* this.makeFunction(s, fr));
        return;
      case "class":
        yield* this.exClass(s, fr);
        return;
      case "raise":
        yield* this.exRaise(s, fr);
        return;
      case "try":
        yield* this.exTry(s, fr);
        return;
      case "with":
        yield* this.withItems(s.items as Node[], 0, s.body, fr);
        return;
      case "import": {
        const mod = yield* this.importModule(s.module);
        const bind = s.bind as string | undefined;
        if (bind === "none") return;
        if (bind === "root") {
          const root = this.modules.get((s.module as string).split(".")[0]) as Value;
          this.storeName(fr, s.alias, root);
        } else {
          this.storeName(fr, s.alias, mod);
        }
        return;
      }
      case "importfrom": {
        const mod = yield* this.importModule(s.module);
        for (const spec of s.names as Node[]) {
          if (spec.n === "*") {
            const ns = mod instanceof DiasporeModule ? mod.frame.vars : (mod as NativeModule).ns;
            const exports = ns.get("__all__");
            if (exports === undefined) {
              for (const name of [...ns.keys()]) {
                if (!name.startsWith("_") && !name.startsWith(".")) this.storeName(fr, name, yield* this.getAttr(mod, name));
              }
            } else {
              for (let index = 0n; ; index++) {
                let name: Value;
                try {
                  name = yield* getIndex(this, exports, index);
                } catch (error) {
                  if (error instanceof DiasporeError && excIsA(error.value, "IndexError")) break;
                  throw error;
                }
                if (!isPyString(name)) throw diasporeErr("TypeError", "Item in __all__ must be str");
                const key = strKey(name);
                this.storeName(fr, key, yield* this.getAttr(mod, key));
              }
            }
            continue;
          }
          let v: Value;
          try {
            v = yield* this.getAttr(mod, spec.n);
          } catch (e) {
            if (e instanceof DiasporeError && excIsA(e.value, "AttributeError")) {
              throw diasporeErr("ImportError", `cannot import name '${spec.n}' from '${s.module}'`);
            }
            throw e;
          }
          this.storeName(fr, spec.alias, v);
        }
        return;
      }
      case "del":
        yield* this.exDel(s, fr);
        return;
    }
    throw new DiasporeTrap(`diaspore: bad statement op ${JSON.stringify(s.op)}`, 4);
  }

  *assign(t: Node, v: Value, fr: Frame): G<void> {
    switch (t.op as string) {
      case "name":
        this.storeName(fr, t.id, v);
        return;
      case "attr":
        yield* this.setAttr(yield* this.ev(t.o, fr), t.n, v);
        return;
      case "index":
        yield* setIndex(this, yield* this.ev(t.o, fr), yield* this.evKey(t.k, fr), v);
        return;
      case "tuple":
        yield* this.unpack((t.elts ?? []) as Node[], v, fr);
        return;
    }
    throw diasporeErr("TypeError", "cannot assign to this target");
  }

  *unpack(elts: Node[], v: Value, fr: Frame): G<void> {
    let star: number | null = null;
    for (let i = 0; i < elts.length; i++) {
      if (elts[i].op === "starred") {
        star = i;
        break;
      }
    }
    let items: Value[];
    try {
      items = yield* iterToArray(this, v);
    } catch (e) {
      if (e instanceof DiasporeError && excIsA(e.value, "TypeError")) {
        throw diasporeErr("TypeError", `cannot unpack non-iterable ${typeName(v)} object`);
      }
      throw e;
    }
    if (star === null) {
      if (items.length < elts.length) {
        throw diasporeErr(
          "ValueError",
          `not enough values to unpack (expected ${elts.length}, got ${items.length})`,
        );
      }
      if (items.length > elts.length) {
        throw diasporeErr("ValueError", `too many values to unpack (expected ${elts.length})`);
      }
      for (let i = 0; i < elts.length; i++) yield* this.assign(elts[i], items[i], fr);
    } else {
      const after = elts.length - star - 1;
      if (items.length < elts.length - 1) {
        throw diasporeErr(
          "ValueError",
          `not enough values to unpack (expected at least ${elts.length - 1}, got ${items.length})`,
        );
      }
      for (let i = 0; i < star; i++) yield* this.assign(elts[i], items[i], fr);
      yield* this.assign(
        elts[star].v,
        new DiasporeList(items.slice(star, items.length - after)),
        fr,
      );
      for (let i = 0; i < after; i++) {
        yield* this.assign(elts[star + 1 + i], items[items.length - after + i], fr);
      }
    }
  }

  *exAugassign(s: Node, fr: Frame): G<void> {
    const t = s.target as Node;
    const kind = s.kind as string;
    const rhs = yield* this.ev(s.v, fr);
    if (t.op === "name") {
      const cur = this.loadName(fr, t.id);
      this.storeName(fr, t.id, yield* binop(this, kind, cur, rhs, true));
    } else if (t.op === "attr") {
      const o = yield* this.ev(t.o, fr);
      const cur = yield* this.getAttr(o, t.n);
      yield* this.setAttr(o, t.n, yield* binop(this, kind, cur, rhs, true));
    } else if (t.op === "index") {
      const o = yield* this.ev(t.o, fr);
      const k = yield* this.evKey(t.k, fr);
      const cur = yield* getIndex(this, o, k);
      yield* setIndex(this, o, k, yield* binop(this, kind, cur, rhs, true));
    } else {
      throw diasporeErr("TypeError", "illegal augmented assignment target");
    }
  }

  *makeFunction(node: Node, fr: Frame): G<DiasporeFunction> {
    let closure: Frame | null = fr;
    if (node.annotation_scope !== true) {
      while (closure !== null && closure.isClass) closure = closure.parent;
    }
    const fn = new DiasporeFunction(node, closure, fr.moduleName);
    const doc = node.body?.[0];
    fn.metadata.set("__doc__", doc?.op === "expr" && doc.v?.op === "const" && doc.v.t === "str" ? this.constVal(doc.v) : NONE);
    fn.metadata.set("__qualname__", fr.fname === "<module>" ? fn.name : fr.fname + (fr.isClass ? "." : ".<locals>.") + fn.name);
    const p = fn.params; // normalized: absent fields are their defaults
    for (const d of p.defaults as Node[]) {
      fn.defaults.push(yield* this.ev(d, fr));
    }
    const kwdefs = p.kwdefaults as (Node | null)[];
    for (let i = 0; i < p.kwonly.length; i++) {
      const d = kwdefs[i];
      if (d !== null && d !== undefined) {
        fn.kwdefaults.set(p.kwonly[i], yield* this.ev(d, fr));
      }
    }
    return fn;
  }

  *exClass(s: Node, fr: Frame): G<void> {
    if (s.binding !== undefined && typeof s.binding !== "string") throw new Error("invalid class binding: expected a string");
    const originalBases: Value[] = [];
    for (const b of (s.bases ?? []) as Node[]) {
      if (b.op === "starred") originalBases.push(...(yield* iterToArray(this, yield* this.ev(b.v, fr))));
      else originalBases.push(yield* this.ev(b, fr));
    }
    const bases: (DiasporeClass | BuiltinType)[] = [];
    let resolvedBases = false;
    for (const base of originalBases) {
      if (base instanceof DiasporeClass || base instanceof BuiltinType) bases.push(yield* this.checkBase(base));
      else {
        const replacement = yield* this.invoke(yield* this.getAttr(base, "__mro_entries__"), [new DiasporeTuple(originalBases)], new Map());
        if (!(replacement instanceof DiasporeTuple)) throw diasporeErr("TypeError", "__mro_entries__ must return a tuple");
        for (const item of replacement.items) bases.push(yield* this.checkBase(item));
        resolvedBases = true;
      }
    }
    const merged: KwArgs = new Map();
    for (const kw of (s.kwargs ?? []) as Node[]) {
      if (!has(kw, "n")) {
        const d = yield* this.ev(kw.v, fr);
        if (!(d instanceof DiasporeDict)) {
          throw diasporeErr("TypeError", "argument after ** must be a mapping");
        }
        for (const [k, v] of d.entries()) {
          if (!isPyString(k)) throw diasporeErr("TypeError", "keywords must be strings");
          merged.set(strKey(k), v);
        }
      } else {
        merged.set(kw.n as string, yield* this.ev(kw.v, fr));
      }
    }
    let metaclass: Value | null = null;
    if (merged.has("metaclass")) {
      metaclass = merged.get("metaclass") as Value;
      merged.delete("metaclass");
    }
    for (const base of bases) {
      if (!(base instanceof DiasporeClass) || base.metaclass === null) continue;
      if (metaclass === null || (metaclass instanceof BuiltinType && metaclass.name === "type")) metaclass = base.metaclass;
      else if (metaclass instanceof DiasporeClass && base.metaclass instanceof DiasporeClass) {
        if (base.metaclass.issub(metaclass)) metaclass = base.metaclass;
        else if (!metaclass.issub(base.metaclass)) throw diasporeErr("TypeError", "metaclass conflict");
      }
    }
    let prepared: Value | null = null;
    if (metaclass !== null) {
      let prepare: Value | null = null;
      try { prepare = yield* this.getAttr(metaclass, "__prepare__"); }
      catch (error) { if (!(error instanceof DiasporeError && excIsA(error.value, "AttributeError"))) throw error; }
      if (prepare !== null) prepared = yield* this.invoke(prepare, [s.name, new DiasporeTuple(bases)], merged);
    }
    const cf = new Frame(
      prepared === null ? new Map() : new PreparedNamespace(this, prepared),
      fr,
      null,
      EMPTY_SET,
      EMPTY_SET,
      true,
      fr.globals,
      s.name,
      fr.moduleName,
    );
    cf.vars.set("__module__", fr.globals.get("__name__") ?? fr.moduleName);
    cf.vars.set("__qualname__", s.name);
    const doc = s.body?.[0];
    if (doc?.op === "expr" && doc.v?.op === "const" && doc.v.t === "str") cf.vars.set("__doc__", this.constVal(doc.v));
    cf.back = this.frames.at(-1) ?? null;
    this.frames.push(cf);
    try { yield* this.execBlock(s.body, cf); }
    finally { this.frames.pop(); }
    if (resolvedBases) cf.vars.set("__orig_bases__", new DiasporeTuple(originalBases));
    const cls = yield* this.buildClass(s.name, bases, cf.vars, metaclass, merged, prepared);
    cf.vars = cls.ns;
    this.storeName(fr, s.binding ?? s.name, cls);
  }

  *checkBase(bv: Value): G<DiasporeClass | BuiltinType> {
    if (bv instanceof DiasporeClass) return bv;
    if (bv instanceof BuiltinType && PRIM_BASE_NAMES.has(bv.name)) return bv;
    throw diasporeErr("TypeError", `cannot subclass ${yield* this.repr(bv)} in diaspore`);
  }

  /**
   * CPython-shaped class creation: an explicit metaclass is called as
   * metaclass(name, bases_tuple, ns_dict, **kwargs) unless it is `type` (or
   * a plain subclass adding no construction hooks, which degrades to normal
   * creation); remaining class kwargs dispatch to the nearest
   * base's __init_subclass__, else TypeError like CPython.
   */
  *buildClass(
    name: string,
    bases: (DiasporeClass | BuiltinType)[],
    ns: Map<string, Value>,
    metaclass: Value | null,
    kwargs: KwArgs,
    prepared: Value | null = null,
    invokeMetaclass = true,
  ): G<DiasporeClass> {
    if (metaclass === null) {
      for (const base of bases) {
        if (!(base instanceof DiasporeClass) || base.metaclass === null) continue;
        if (metaclass === null) metaclass = base.metaclass;
        else if (base.metaclass instanceof DiasporeClass && metaclass instanceof DiasporeClass) {
          if (base.metaclass.issub(metaclass)) metaclass = base.metaclass;
          else if (!metaclass.issub(base.metaclass)) throw diasporeErr("TypeError", "metaclass conflict");
        }
      }
    }
    const make = (): DiasporeClass => {
      const namespace = new Map([...ns].filter(([key]) => !key.startsWith(".")));
      for (const key of ["__init_subclass__", "__class_getitem__"]) {
        const method = namespace.get(key);
        if (method instanceof DiasporeFunction) namespace.set(key, new ClassMethodV(method));
      }
      const constructor = namespace.get("__new__");
      if (constructor instanceof DiasporeFunction) namespace.set("__new__", new StaticMethodV(constructor));
      const made = new DiasporeClass(name, [...bases], namespace);
      made.runtime = this;
      this.classes.add(made);
      made.metaclass = metaclass as DiasporeClass | BuiltinType | null;
      for (const v of made.ns.values()) {
        if (v instanceof DiasporeFunction && v.defclass === null) {
          v.defclass = made;
        } else if (
          (v instanceof StaticMethodV || v instanceof ClassMethodV) &&
          v.fn instanceof DiasporeFunction &&
          v.fn.defclass === null
        ) {
          v.fn.defclass = made;
        }
      }
      return made;
    };
    const plainMeta =
      !invokeMetaclass ||
      metaclass === null ||
      (metaclass instanceof BuiltinType && metaclass.name === "type") ||
      (metaclass instanceof DiasporeClass &&
        metaclass.lookup("__new__") === undefined &&
        metaclass.lookup("__call__") === undefined &&
        metaclass.lookup("__init__") === undefined);
    if (!plainMeta) {
      const nsDict = prepared ?? new DiasporeNamespace(ns);
      const result = yield* this.invoke(metaclass as Value,
      [name, new DiasporeTuple([...bases]), nsDict],
      new Map(kwargs),);
      if (!(result instanceof DiasporeClass)) {
        throw diasporeErr("TypeError", `metaclass returned ${typeName(result)}, not a class`);
      }
      return result;
    }
    const cls = make();
    yield* initializeSlots(this, cls);
    if (!cls.ns.has("__doc__")) cls.ns.set("__doc__", NONE);
    for (const [key, descriptor] of cls.ns) {
      if (descriptor instanceof DiasporeInstance && descriptor.cls.lookup("__set_name__") !== undefined) {
        yield* this.callDunder(descriptor, "__set_name__", [cls, key]);
      }
    }
    for (const base of bases) {
      if (!(base instanceof DiasporeClass)) continue;
      const initSub = base.lookup("__init_subclass__");
      if (initSub !== undefined) {
        const fn = initSub instanceof ClassMethodV ? initSub.fn : initSub;
        yield* this.invoke(new BoundMethod(fn, cls), [], new Map(kwargs));
        return cls;
      }
    }
    if (kwargs.size > 0) throw diasporeErr("TypeError", `${name}.__init_subclass__() takes no keyword arguments`);
    return cls;
  }

  private linkExceptionContext(value: DiasporeInstance): void {
    const active = this.excStack[this.excStack.length - 1];
    if (active === undefined || active === value) return;
    const visited = new Set<DiasporeInstance>();
    let previous = active;
    while (!visited.has(previous)) {
      visited.add(previous);
      const next = previous.exc.get("__context__");
      if (next === value) { previous.exc.set("__context__", NONE); break; }
      if (!(next instanceof DiasporeInstance)) break;
      previous = next;
    }
    value.exc.set("__context__", active);
  }

  *exRaise(s: Node, fr: Frame): G<void> {
    if (!has(s, "exc")) {
      if (this.excStack.length === 0) {
        throw diasporeErr("RuntimeError", "No active exception to re-raise");
      }
      throw new DiasporeError(this.excStack[this.excStack.length - 1]);
    }
    let v = yield* this.ev(s.exc, fr);
    let cause = has(s, "cause") ? yield* this.ev(s.cause, fr) : undefined;
    if (v instanceof DiasporeClass) {
      if (!v.issub(BASE_EXC)) {
        throw diasporeErr("TypeError", "exceptions must derive from BaseException");
      }
      v = yield* this.instantiate(v, [], new Map());
    }
    if (!(v instanceof DiasporeInstance && v.cls.issub(BASE_EXC))) {
      throw diasporeErr("TypeError", "exceptions must derive from BaseException");
    }
    if (cause !== undefined) {
      if (cause instanceof DiasporeClass && cause.issub(BASE_EXC)) cause = yield* this.instantiate(cause, [], new Map());
      if (!(cause instanceof PyNoneT) && !(cause instanceof DiasporeInstance && cause.cls.issub(BASE_EXC))) {
        throw diasporeErr("TypeError", "exception causes must derive from BaseException");
      }
      v.exc.set("__cause__", cause);
      v.exc.set("__suppress_context__", true);
    }
    this.linkExceptionContext(v);
    throw new DiasporeError(v);
  }

  *exTry(s: Node, fr: Frame): G<void> {
    // `finally` runs for diaspore-level control flow (fallthrough, exceptions,
    // return/break/continue) but NOT for traps or durable suspension.
    if (s.star) {
      yield* this.exTryStar(s, fr);
      return;
    }
    try {
      let diasporeE: DiasporeError | null = null;
      try {
        yield* this.execBlock(s.body, fr);
      } catch (e) {
        if (e instanceof DiasporeError) diasporeE = e;
        else throw e;
      }
      if (diasporeE !== null) {
        let matched = false;
        for (const h of (s.handlers ?? []) as Node[]) {
          if (!(yield* this.handlerMatches(h, diasporeE.value, fr))) continue;
          matched = true;
          if (has(h, "name")) fr.vars.set(h.name, diasporeE.value);
          this.excStack.push(diasporeE.value as DiasporeInstance);
          try {
            yield* this.execBlock(h.body, fr);
          } finally {
            this.excStack.pop();
            if (has(h, "name")) fr.vars.delete(h.name);
          }
          break;
        }
        if (!matched) throw diasporeE;
      } else {
        yield* this.execBlock(s.orelse, fr);
      }
    } catch (e) {
      if (e instanceof DiasporeTrap || e instanceof SuspendSig) throw e;
      yield* this.execBlock(s.finalbody, fr);
      throw e;
    }
    yield* this.execBlock(s.finalbody, fr);
  }

  /** PEP 654: each except* clause matches the subgroup of exceptions of its
   * type; unmatched exceptions propagate as a residual group. */
  *exTryStar(s: Node, fr: Frame): G<void> {
    try {
      let pending: Value | null = null;
      try {
        yield* this.execBlock(s.body, fr);
      } catch (e) {
        if (e instanceof DiasporeError) pending = e.value;
        else throw e;
      }
      if (pending !== null) {
        for (const h of (s.handlers ?? []) as Node[]) {
          const [match, rest]: [Value | null, Value | null] = yield* this.splitGroup(pending, h, fr);
          if (match === null) continue;
          if (has(h, "name")) fr.vars.set(h.name, match);
          this.excStack.push(match as DiasporeInstance);
          try {
            yield* this.execBlock(h.body, fr);
          } finally {
            this.excStack.pop();
            if (has(h, "name")) fr.vars.delete(h.name);
          }
          pending = rest;
          if (pending === null) break;
        }
        if (pending !== null) throw new DiasporeError(pending);
      } else {
        yield* this.execBlock(s.orelse, fr);
      }
    } catch (e) {
      if (e instanceof DiasporeTrap || e instanceof SuspendSig) throw e;
      yield* this.execBlock(s.finalbody, fr);
      throw e;
    }
    yield* this.execBlock(s.finalbody, fr);
  }

  /** (matching_subgroup | null, residual | null) for handler h against exc,
   * treating a non-group exception as a singleton group. */
  *splitGroup(exc: Value, h: Node, fr: Frame): G<[Value | null, Value | null]> {
    const t = yield* this.ev(h.t, fr);
    const classes = t instanceof DiasporeTuple ? t.items : [t];
    const matches = (e: Value): boolean =>
      classes.some((c) => c instanceof DiasporeClass && e instanceof DiasporeInstance && e.cls.issub(c));
    const groupCls = EXC_CLASSES.get("BaseExceptionGroup") as DiasporeClass;
    if (exc instanceof DiasporeInstance && exc.cls.issub(groupCls)) {
      const membersV = exc.exc.get("exceptions");
      const members = membersV instanceof DiasporeTuple ? membersV.items : [];
      const matched = members.filter(matches);
      const unmatched = members.filter((e) => !matches(e));
      if (matched.length === 0) return [null, exc];
      const msgV = exc.exc.get("message");
      const msg = isPyString(msgV) ? msgV : "";
      const mg = makeGroup(msg, matched);
      const rest = unmatched.length > 0 ? makeGroup(msg, unmatched) : null;
      return [mg, rest];
    }
    if (exc instanceof DiasporeInstance && matches(exc)) {
      return [makeGroup("", [exc]), null];
    }
    return [null, exc];
  }

  *handlerMatches(h: Node, exc: Value, fr: Frame): G<boolean> {
    if (!has(h, "t")) return true;
    const t = yield* this.ev(h.t, fr);
    const classes = t instanceof DiasporeTuple ? t.items : [t];
    for (const c of classes) {
      if (!(c instanceof DiasporeClass && c.issub(BASE_EXC))) {
        throw diasporeErr(
          "TypeError",
          "catching classes that do not inherit from BaseException is not allowed",
        );
      }
      if ((exc as DiasporeInstance).cls.issub(c)) return true;
    }
    return false;
  }

  *withItems(items: Node[], i: number, body: unknown[], fr: Frame): G<void> {
    if (i === items.length) {
      yield* this.execBlock(body, fr);
      return;
    }
    const item = items[i];
    const ctx = yield* this.ev(item.ctx, fr);
    const self = this;
    let enter: () => G<Value>;
    let exit_: (a: Value, b: Value, c: Value) => G<Value>;
    if (ctx instanceof DiasporeFile) {
      enter = function* () {
        return ctx;
      };
      exit_ = function* () {
        self.closeFile(ctx);
        return false;
      };
    } else if (ctx instanceof NativeObject && ctx.methods.has("__enter__")) {
      const en = ctx.methods.get("__enter__") as Builtin;
      const ex = ctx.methods.get("__exit__") as Builtin;
      enter = function* () {
        return yield* self.invoke(en, [], new Map());
      };
      exit_ = function* (a, b, c) {
        return yield* self.invoke(ex, [a, b, c], new Map());
      };
    } else if (ctx instanceof DiasporeInstance) {
      if (ctx.cls.lookup("__enter__") === undefined || ctx.cls.lookup("__exit__") === undefined) {
        throw diasporeErr(
          "TypeError",
          `'${ctx.cls.name}' object does not support the context manager protocol`,
        );
      }
      enter = function* () {
        return yield* self.callDunder(ctx, "__enter__", []);
      };
      exit_ = function* (a, b, c) {
        return yield* self.callDunder(ctx, "__exit__", [a, b, c]);
      };
    } else {
      throw diasporeErr(
        "TypeError",
        `'${typeName(ctx)}' object does not support the context manager protocol`,
      );
    }
    const entered = yield* enter();
    if (has(item, "name")) yield* this.assign(item.name, entered, fr);
    try {
      yield* this.withItems(items, i + 1, body, fr);
    } catch (e) {
      if (e instanceof DiasporeError) {
        const inst = e.value as DiasporeInstance;
        if (yield* this.truth(yield* exit_(inst.cls, inst, NONE))) return;
        throw e;
      }
      if (e instanceof DiasporeTrap || e instanceof SuspendSig) throw e; // abandonment
      yield* exit_(NONE, NONE, NONE); // return/break/continue unwinding
      throw e;
    }
    yield* exit_(NONE, NONE, NONE);
  }

  closeFile(f: DiasporeFile): void {
    if (!f.closed) {
      nodeFs.closeSync(f.fd);
      f.closed = true;
    }
  }

  *exDel(s: Node, fr: Frame): G<void> {
    for (const t of s.targets as Node[]) {
      const op = t.op as string;
      if (op === "name") {
        const id = t.id as string;
        if (fr.vars.has(id)) {
          fr.vars.delete(id);
        } else if (fr.localset !== null && fr.localset.has(id)) {
          throw diasporeErr(
            "UnboundLocalError",
            `cannot access local variable '${id}' where it is not associated with a value`,
          );
        } else if (fr.globals.has(id)) {
          fr.globals.delete(id);
        } else {
          throw diasporeErr("NameError", `name '${id}' is not defined`);
        }
      } else if (op === "index") {
        const o = yield* this.ev(t.o, fr);
        const k = yield* this.evKey(t.k, fr);
        yield* delIndex(this, o, k);
      } else if (op === "attr") {
        yield* this.delAttr(yield* this.ev(t.o, fr), t.n);
      } else {
        throw diasporeErr("TypeError", "cannot delete this target");
      }
    }
  }

  // ------------------------------------------------------------- expressions

  *ev(e0: unknown, fr: Frame): G<Value> {
    const e = e0 as Node;
    switch (e.op as string) {
      case "const": {
        const cached = constCache.get(e);
        if (cached !== undefined) return cached;
        const v = this.constVal(e);
        constCache.set(e, v);
        return v;
      }
      case "name":
        return this.loadName(fr, e.id);
      case "attr":
        return yield* this.getAttr(yield* this.ev(e.o, fr), e.n);
      case "index":
        return yield* getIndex(this, yield* this.ev(e.o, fr), yield* this.evKey(e.k, fr));
      case "binop":
        return yield* binop(this, e.kind, yield* this.ev(e.l, fr), yield* this.ev(e.r, fr));
      case "unary": {
        const kind = e.kind as string;
        const v = yield* this.ev(e.v, fr);
        if (kind === "not") return !(yield* this.truth(v));
        return yield* unary(this, kind, v);
      }
      case "boolop": {
        const vals = e.values as Node[];
        let v: Value = NONE;
        if (e.kind === "and") {
          for (let i = 0; i < vals.length; i++) {
            v = yield* this.ev(vals[i], fr);
            if (i === vals.length - 1 || !(yield* this.truth(v))) return v;
          }
        } else {
          for (let i = 0; i < vals.length; i++) {
            v = yield* this.ev(vals[i], fr);
            if (i === vals.length - 1 || (yield* this.truth(v))) return v;
          }
        }
        return v;
      }
      case "compare": {
        let left = yield* this.ev(e.left, fr);
        const ops = e.ops as string[];
        const comps = e.comps as Node[];
        for (let i = 0; i < ops.length; i++) {
          const right = yield* this.ev(comps[i], fr);
          const result = yield* compare(this, ops[i], left, right);
          if (i === ops.length - 1 || !(yield* this.truth(result))) return result;
          left = right;
        }
        return true;
      }
      case "cond":
        return (yield* this.truth(yield* this.ev(e.test, fr)))
          ? yield* this.ev(e.then, fr)
          : yield* this.ev(e.else, fr);
      case "call":
        return yield* this.evCall(e, fr);
      case "list":
        return new DiasporeList(yield* this.evalElts((e.elts ?? []) as Node[], fr));
      case "tuple":
        return new DiasporeTuple(yield* this.evalElts((e.elts ?? []) as Node[], fr));
      case "set": {
        const out = new DiasporeSet();
        for (const x of yield* this.evalElts((e.elts ?? []) as Node[], fr)) out.add(x);
        return out;
      }
      case "dict": {
        const out = new DiasporeDict();
        for (const entry of (e.entries ?? []) as Node[]) {
          if (!has(entry, "k")) {
            const v = yield* this.ev(entry.v, fr);
            if (!(v instanceof DiasporeDict)) {
              throw diasporeErr("TypeError", "argument after ** must be a mapping");
            }
            for (const [hk, en] of v.map) out.setHK(storedHashKey(v.map, hk), en.key, en.val);
          } else {
            const k = yield* this.ev(entry.k, fr);
            const v = yield* this.ev(entry.v, fr);
            out.set(k, v);
          }
        }
        return out;
      }
      case "fstring": {
        const parts = e.parts as Node[];
        let out: PyString = "";
        for (const p of parts) {
          if (typeof p.s === "string") {
            out = strConcat(out, p.codepoints === undefined ? p.s : strFromChars(p.codepoints.map((point: number) => String.fromCodePoint(point))));
            continue;
          }
          let v = yield* this.ev(p.v, fr);
          const conv = p.conv as string | null | undefined;
          if (conv === "r") v = yield* this.repr(v);
          else if (conv === "s") v = yield* this.str(v);
          else if (conv === "a") v = yield* asciiOf(this, v);
          const spec = has(p, "spec") ? ((yield* this.ev(p.spec, fr)) as PyString) : "";
          out = strConcat(out, yield* formatValue(this, v, spec));
        }
        return out;
      }
      case "funcexpr":
      case "func":
        return yield* this.makeFunction(e, fr);
      case "yield": {
        if (!fr.isGen) throw new DiasporeTrap("diaspore: yield outside generator frame", 4);
        const v = has(e, "v") ? yield* this.ev(e.v, fr) : NONE;
        const sent = yield { yv: v } as YieldMsg;
        return sent === undefined ? NONE : sent; // value from send(), None from next()
      }
      case "yieldfrom": {
        if (!fr.isGen) throw new DiasporeTrap("diaspore: yield outside generator frame", 4);
        const it = yield* getIter(this, yield* this.ev(e.v, fr));
        const sub = it instanceof DiasporeGenerator ? it : null;
        let y: Value;
        {
          const [v0, ok] = yield* it.next();
          if (!ok) return it.takeRetval();
          y = v0;
        }
        for (;;) {
          let sent: Value;
          try {
            const s = yield { yv: y } as YieldMsg;
            sent = s === undefined ? NONE : s;
          } catch (ex) {
            // forward throw/close into the subgenerator (PEP 380)
            if (ex instanceof DiasporeError && sub !== null) {
              if (excIsA(ex.value, "GeneratorExit")) {
                sub.close();
                throw ex;
              }
              const [ty, ok] = sub.throwValue(ex.value);
              if (!ok) return sub.takeRetval();
              y = ty;
              continue;
            }
            throw ex;
          }
          const [ny, ok] = sub !== null ? sub.sendValue(sent) : yield* it.next();
          if (!ok) return it.takeRetval();
          y = ny;
        }
      }
      case "namedexpr": {
        const v = yield* this.ev(e.v, fr);
        this.storeName(fr, (e.target as Node).id, v);
        return v;
      }
    }
    throw new DiasporeTrap(`diaspore: bad expression op ${JSON.stringify(e.op)}`, 4);
  }

  constVal(e: Node): Value {
    switch (e.t as string) {
      case "none":
        return NONE;
      case "ellipsis":
        return ELLIPSIS;
      case "int":
        return BigInt(e.v as string);
      case "float": {
        const v = e.v as string;
        if (v === "inf") return Infinity;
        if (v === "-inf") return -Infinity;
        if (v === "nan") return NaN;
        return Number(v);
      }
      case "str":
        return e.codepoints === undefined ? e.v as string : strFromChars(e.codepoints.map((point: number) => String.fromCodePoint(point)));
      case "bool":
        return e.v as boolean;
      case "bytes":
        return new Uint8Array(Buffer.from(e.v as string, "base64"));
      case "complex":
        return new Complex(Number(e.re as string), Number(e.im as string));
    }
    throw new DiasporeTrap(`diaspore: bad const type ${JSON.stringify(e.t)}`, 4);
  }

  *evKey(k: Node, fr: Frame): G<Value> {
    if (k.op === "slicelit") {
      return new DiasporeSlice(
        has(k, "lower") ? yield* this.ev(k.lower, fr) : NONE,
        has(k, "upper") ? yield* this.ev(k.upper, fr) : NONE,
        has(k, "step") ? yield* this.ev(k.step, fr) : NONE,
      );
    }
    return yield* this.ev(k, fr);
  }

  *evalElts(elts: Node[], fr: Frame): G<Value[]> {
    const out: Value[] = [];
    for (const x of elts) {
      if (x.op === "starred") {
        out.push(...(yield* iterToArray(this, yield* this.ev(x.v, fr))));
      } else {
        out.push(yield* this.ev(x, fr));
      }
    }
    return out;
  }

  /** Names visible in the frame chain (a user rebinding shadows the
   * frame-aware builtins globals()/locals()/exec/eval). */
  private shadowed(fr: Frame): Set<string> {
    const s = new Set<string>();
    let f: Frame | null = fr;
    while (f !== null) {
      for (const k of f.vars.keys()) s.add(k);
      f = f.parent;
    }
    for (const k of fr.globals.keys()) s.add(k);
    return s;
  }

  *evCall(e: Node, fr: Frame): G<Value> {
    // frame-aware builtins: globals()/locals() (zero-arg), exec/eval (1-arg)
    const fnNode = e.f as Node;
    if (
      fnNode.op === "name" &&
      (e.args === undefined || (e.args as unknown[]).length === 0) &&
      (e.kw === undefined || (e.kw as unknown[]).length === 0)
    ) {
      const id = fnNode.id as string;
      if (id === "globals" && !this.shadowed(fr).has("globals")) {
        return new DiasporeNamespace(fr.globals);
      }
      if (id === "locals" && !this.shadowed(fr).has("locals")) {
        return fr.localset === null ? new DiasporeNamespace(fr.vars) : dictCopy(fr.vars);
      }
    }
    if (
      fnNode.op === "name" &&
      (fnNode.id === "exec" || fnNode.id === "eval") &&
      !this.shadowed(fr).has(fnNode.id as string)
    ) {
      const argNodes = (e.args ?? []) as Node[];
      if (argNodes.length === 1 && argNodes[0].op !== "starred") {
        const code = yield* this.ev(argNodes[0], fr);
        const gns: Value = fr.globals;
        const lns: Value = fr.vars;
        return yield* this.execSnippet(
          code,
          gns,
          lns === gns ? null : lns,
          fnNode.id === "eval" ? "eval" : "exec",
        );
      }
    }
    const f = yield* this.ev(e.f, fr);
    const args: Value[] = [];
    for (const a of (e.args ?? []) as Node[]) {
      if (a.op === "starred") {
        args.push(...(yield* iterToArray(this, yield* this.ev(a.v, fr))));
      } else {
        args.push(yield* this.ev(a, fr));
      }
    }
    const kwargs: KwArgs = new Map();
    for (const kw of (e.kw ?? []) as Node[]) {
      if (!has(kw, "n")) {
        const d = yield* this.ev(kw.v, fr);
        if (!(d instanceof DiasporeDict)) {
          throw diasporeErr("TypeError", "argument after ** must be a mapping");
        }
        for (const [k, v] of d.entries()) {
          if (!isPyString(k)) throw diasporeErr("TypeError", "keywords must be strings");
          kwargs.set(strKey(k), v);
        }
      } else {
        kwargs.set(kw.n, yield* this.ev(kw.v, fr));
      }
    }
    if (f instanceof SuperMarkerT) return this.makeSuper(args, fr);
    return yield* this.invoke(f, args, kwargs);
  }

  // ------------------------------------------------------------- calling

  *invoke(f: Value, args: Value[], kwargs: KwArgs): G<Value> {
    this.fuel();
    if (f instanceof StaticMethodV) return yield* this.invoke(f.fn, args, kwargs);
    if (f instanceof DiasporeFunction) return yield* this.callFunction(f, args, kwargs);
    if (f instanceof BoundMethod) return yield* this.invoke(f.fn, [f.recv, ...args], kwargs);
    if (f instanceof DiasporeClass) {
      if (f.metaclass instanceof DiasporeClass) {
        const call = f.metaclass.lookup("__call__");
        if (call !== undefined) return yield* this.invoke(this.bindAttr(call, f, f.metaclass), args, kwargs);
      }
      return yield* this.instantiate(f, args, kwargs);
    }
    if (f instanceof BuiltinType) return yield* f.ctor(this, args, kwargs);
    if (f instanceof Builtin) return yield* f.fn(this, args, kwargs);
    if (f instanceof DiasporeInstance) {
      if (f.prim instanceof StaticMethodV) return yield* this.invoke(f.prim.fn, args, kwargs);
      if (f.cls.lookup("__call__") !== undefined) {
        return yield* this.callDunderKw(f, "__call__", args, kwargs);
      }
      throw diasporeErr("TypeError", `'${f.cls.name}' object is not callable`);
    }
    if (f instanceof SuperMarkerT) {
      throw diasporeErr("RuntimeError", "super(): no arguments and no enclosing method");
    }
    if (f instanceof NativeObject && f.methods.has("__call__")) return yield* this.invoke(f.methods.get("__call__")!, args, kwargs);
    throw diasporeErr("TypeError", `'${typeName(f)}' object is not callable`);
  }

  // ------------------------------------------------------------- exec/eval

  compileSnippet(source: Value, filename: string, mode: string): Value {
    if (source instanceof CodeObject) return source;
    if (!isPyString(source)) {
      throw diasporeErr("TypeError", "compile() arg 1 must be a string");
    }
    return compileSnippet(source, filename, mode);
  }

  *execSnippet(code: Value, glob: Value | null, loc: Value | null, mode: string): G<Value> {
    const co = code instanceof CodeObject ? code : this.compileSnippet(code, "<string>", mode);
    if (!(co instanceof CodeObject)) {
      throw diasporeErr("TypeError", "exec/eval arg must be str or code");
    }
    // Resolve the globals namespace to a Map the exec frame runs over.
    let globalsMap: Map<string, Value>;
    let writeback: DiasporeDict | null = null;
    if (glob === null) {
      const main = this.modules.get(this.main);
      globalsMap = main instanceof DiasporeModule ? main.frame.vars : new Map();
    } else if (glob instanceof DiasporeDict) {
      globalsMap = dictToMap(glob);
      writeback = glob;
    } else if (glob instanceof Map) {
      globalsMap = glob as Map<string, Value>;
    } else {
      throw diasporeErr("TypeError", "exec() globals must be a dict");
    }
    let ns = globalsMap;
    let locWriteback: DiasporeDict | null = null;
    if (loc !== null && loc !== glob) {
      if (loc instanceof DiasporeDict) {
        ns = dictToMap(loc);
        locWriteback = loc;
      } else if (loc instanceof Map) {
        ns = loc as Map<string, Value>;
      }
    }
    const fr = new Frame(
      ns,
      null,
      null,
      EMPTY_SET,
      EMPTY_SET,
      false,
      globalsMap,
      "<exec>",
      this.main,
    );
    try {
      if (mode === "eval") {
        const node = co.nodes[0] as Node | undefined;
        if (node === undefined || node.op !== "expr") {
          throw diasporeErr("SyntaxError", "eval() argument must be an expression");
        }
        return yield* this.ev(node.v, fr);
      }
      try {
        yield* this.execBlock(co.nodes, fr);
      } catch (e) {
        if (e instanceof ReturnSig) return NONE;
        throw e;
      }
      return NONE;
    } finally {
      if (writeback !== null) mapToDict(ns === globalsMap ? globalsMap : ns, writeback);
      if (locWriteback !== null) mapToDict(ns, locWriteback);
    }
  }

  *callFunction(fn: DiasporeFunction, args: Value[], kwargs: KwArgs): G<Value> {
    const vars = this.bindArgs(fn, args, kwargs);
    const fr = new Frame(
      vars,
      fn.closure,
      fn.localset,
      fn.gdecl,
      fn.ndecl,
      false,
      fn.closure !== null ? fn.closure.globals : null,
      fn.name,
      fn.moduleName,
    );
    fr.annotationScope = fn.node.annotation_scope === true;
    fr.defclass = fn.defclass;
    fr.code = functionCode(this, fn);
    const p = fn.params.args;
    fr.self_ = p.length > 0 ? (vars.get(p[0]) ?? null) : null;
    if (fn.gen) {
      fr.isGen = true;
      return new DiasporeGenerator(this, fn, fr);
    }
    if (this.depth >= this.maxDepth) {
      throw diasporeErr("RecursionError", "maximum recursion depth exceeded");
    }
    this.depth++;
    fr.back = this.frames.at(-1) ?? null;
    this.frames.push(fr);
    try {
      yield* this.execBlock(fn.node.body as unknown[], fr);
      return NONE;
    } catch (e) {
      if (e instanceof ReturnSig) return e.value;
      if (e instanceof DiasporeError) {
        e.tb.push({ module: fr.moduleName, fname: fn.name, line: fr.line });
      }
      throw e;
    } finally {
      this.frames.pop();
      this.depth--;
    }
  }

  bindArgs(fn: DiasporeFunction, args: Value[], kwargs: KwArgs): Map<string, Value> {
    const p = fn.params;
    const names = p.args;
    const out = new Map<string, Value>();
    let pos = args;
    if (args.length > names.length) {
      if (p.vararg !== null && p.vararg !== undefined) {
        out.set(p.vararg, new DiasporeTuple(args.slice(names.length)));
        pos = args.slice(0, names.length);
      } else {
        const n = names.length;
        const haveDefaults = fn.defaults.length > 0;
        const took = haveDefaults ? `from ${n - fn.defaults.length} to ${n}` : String(n);
        const sfx = n !== 1 || haveDefaults ? "s" : "";
        const verb = args.length !== 1 ? "were" : "was";
        throw diasporeErr(
          "TypeError",
          `${fn.name}() takes ${took} positional argument${sfx} but ${args.length} ${verb} given`,
        );
      }
    } else if (p.vararg !== null && p.vararg !== undefined) {
      out.set(p.vararg, new DiasporeTuple([]));
    }
    for (let i = 0; i < pos.length && i < names.length; i++) out.set(names[i], pos[i]);
    const extra = new Map<string, Value>();
    for (const [k, v] of kwargs) {
      const position = names.indexOf(k);
      if (position >= 0 && position < p.posonly && p.kwarg === null) {
        throw diasporeErr("TypeError", `${fn.name}() got some positional-only arguments passed as keyword arguments: '${k}'`);
      }
      if (position >= p.posonly) {
        if (out.has(k)) {
          throw diasporeErr("TypeError", `${fn.name}() got multiple values for argument '${k}'`);
        }
        out.set(k, v);
      } else if (p.kwonly.includes(k)) {
        out.set(k, v);
      } else if (p.kwarg !== null && p.kwarg !== undefined) {
        extra.set(k, v);
      } else {
        throw diasporeErr("TypeError", `${fn.name}() got an unexpected keyword argument '${k}'`);
      }
    }
    const ndefaults = fn.defaults.length;
    const required = ndefaults > 0 ? names.slice(0, names.length - ndefaults) : names;
    const missing = required.filter((n) => !out.has(n));
    if (missing.length > 0) {
      throw diasporeErr(
        "TypeError",
        `${fn.name}() missing ${missing.length} required positional argument` +
          `${missing.length > 1 ? "s" : ""}: ${nameList(missing)}`,
      );
    }
    for (let i = 0; i < ndefaults; i++) {
      const name = names[names.length - ndefaults + i];
      if (!out.has(name)) out.set(name, fn.defaults[i]);
    }
    const missKw: string[] = [];
    for (const name of p.kwonly) {
      if (!out.has(name)) {
        const d = fn.kwdefaults.get(name);
        if (d !== undefined) out.set(name, d);
        else missKw.push(name);
      }
    }
    if (missKw.length > 0) {
      throw diasporeErr(
        "TypeError",
        `${fn.name}() missing ${missKw.length} required keyword-only argument` +
          `${missKw.length > 1 ? "s" : ""}: ${nameList(missKw)}`,
      );
    }
    if (p.kwarg !== null && p.kwarg !== undefined) {
      const d = new DiasporeDict();
      for (const [k, v] of extra) d.set(strFromKey(k), v);
      out.set(p.kwarg, d);
    }
    return out;
  }

  *instantiate(cls: DiasporeClass, args: Value[], kwargs: KwArgs): G<Value> {
    const abstract = cls.ns.get("__abstractmethods__");
    if (abstract !== undefined && (yield* this.truth(abstract))) throw diasporeErr("TypeError", `Can't instantiate abstract class ${cls.name}`);
    const newMethod = cls.lookup("__new__");
    let created: Value;
    if (newMethod !== undefined) {
      created = yield* this.invoke(newMethod instanceof StaticMethodV ? newMethod.fn : newMethod, [cls, ...args], kwargs);
    } else if (cls.prim?.name === "type") {
      created = yield* this.invoke(this.builtinUnbound("type", "__new__")!, [cls, ...args], kwargs);
    } else created = new DiasporeInstance(cls);
    if (created instanceof DiasporeClass) {
      const init = cls.lookup("__init__");
      if (init !== undefined) yield* this.invoke(this.bindAttr(init, created, cls), args, kwargs);
      return created;
    }
    if (!(created instanceof DiasporeInstance) || !created.cls.issub(cls)) return created;
    const inst = created;
    if (newMethod === undefined && cls.prim !== null) {
      const base = cls.prim.name;
      // immutable primitives take their value from the constructor args
      // (like __new__); mutable ones start empty and __init__ fills them.
      if (IMMUTABLE_PRIM.has(base)) {
        inst.prim =
          args.length > 0 || kwargs.size > 0
            ? yield* cls.prim.ctor(this, args, kwargs)
            : primEmpty(base);
      } else {
        inst.prim = primEmpty(base);
      }
    }
    if (inst.prim instanceof Property) inst.d.set("__doc__", inst.prim.doc ?? NONE);
    const init = cls.lookup("__init__");
    if (init === undefined) {
      if ((args.length > 0 || kwargs.size > 0) && cls.prim === null && newMethod === undefined) {
        throw diasporeErr("TypeError", `${cls.name}() takes no arguments`);
      }
      if (
        cls.prim !== null &&
        (cls.prim.name === "dict" || cls.prim.name === "list" || cls.prim.name === "set") &&
        (args.length > 0 || kwargs.size > 0)
      ) {
        // inherited builtin __init__: populate the primitive
        inst.prim = yield* cls.prim.ctor(this, args, kwargs);
      }
      return inst;
    }
    const r = yield* this.invoke(this.bindAttr(init, inst, cls), args, kwargs);
    if (!(r instanceof PyNoneT)) {
      throw diasporeErr("TypeError", `__init__() should return None, not '${typeName(r)}'`);
    }
    return inst;
  }

  *callDunder(obj: DiasporeInstance, name: string, args: Value[]): G<Value> {
    return yield* this.callDunderKw(obj, name, args, new Map());
  }

  *callDunderKw(obj: DiasporeInstance, name: string, args: Value[], kwargs: KwArgs): G<Value> {
    const fn = obj.cls.lookup(name);
    if (fn === undefined) {
      throw diasporeErr("AttributeError", `'${obj.cls.name}' object has no attribute '${name}'`);
    }
    return yield* this.invoke(this.bindAttr(fn, obj, obj.cls), args, kwargs);
  }

  *callDunderOrErr(obj: DiasporeInstance, name: string, args: Value[], errmsg: string): G<Value> {
    if (obj.cls.lookup(name) === undefined) throw diasporeErr("TypeError", errmsg);
    return yield* this.callDunder(obj, name, args);
  }

  makeSuper(args: Value[], fr: Frame): Value {
    if (args.length === 0) {
      if (fr.defclass === null || fr.self_ === null) {
        throw diasporeErr("RuntimeError", "super(): __class__ cell not found");
      }
      return new SuperObject(fr.defclass, fr.self_);
    }
    if (args.length === 2 && args[0] instanceof DiasporeClass) {
      return new SuperObject(args[0], args[1]);
    }
    throw diasporeErr("TypeError", "super() argument error");
  }

  // ------------------------------------------------------------- attributes

  bindAttr(v: Value, inst: Value, cls: DiasporeClass): Value {
    if (v instanceof DiasporeInstance && (v.prim instanceof StaticMethodV || v.prim instanceof ClassMethodV)) v = v.prim;
    if (v instanceof DiasporeFunction) return new BoundMethod(v, inst);
    if (v instanceof StaticMethodV) return v.fn;
    if (v instanceof ClassMethodV) return new BoundMethod(v.fn, cls);
    if (v instanceof Builtin) return new BoundMethod(v, v.kind === "classmethod_descriptor" ? cls : inst);
    return v;
  }

  private lookupClassAttr(cls: DiasporeClass, name: string): Value | undefined {
    for (const base of cls.mro) {
      if (base === OBJECT && cls.prim !== null) {
        const descriptor = this.builtinUnbound(cls.prim.name, name);
        if (descriptor !== null) return descriptor;
      }
      const value = base.ns.get(name);
      if (value !== undefined) return value;
    }
    return undefined;
  }

  *getAttr(o: Value, n: string, base = false): G<Value> {
    if (n === "__class__" && !(o instanceof DiasporeInstance || o instanceof DiasporeClass || o instanceof BuiltinType)) {
      return yield* this.invoke(this.types.get("type")!, [o], new Map());
    }
    if (n === "__doc__" && (o === NONE || o === NOT_IMPLEMENTED || o === ELLIPSIS)) return NONE;
    if (OBJECT_METHODS.has(n) && (
      o === NONE || o === NOT_IMPLEMENTED || o === ELLIPSIS ||
      typeof o === "bigint" || typeof o === "number" || typeof o === "boolean" || isPyString(o) ||
      o instanceof Uint8Array || o instanceof DiasporeList || o instanceof DiasporeTuple ||
      o instanceof DiasporeDict || o instanceof DiasporeSet || o instanceof DiasporeRange ||
      o instanceof DiasporeSlice || o instanceof Complex
    )) {
      const cls = yield* this.invoke(this.types.get("type")!, [o], new Map());
      const descriptor = yield* this.getAttr(cls, n);
      return n === "__new__" || descriptor === NONE ? descriptor : new BoundMethod(descriptor, o);
    }
    if (o instanceof DiasporeInstance) {
      if (!base) {
        const hook = o.cls.lookup("__getattribute__");
        if (hook !== undefined) return yield* this.invoke(this.bindAttr(hook, o, o.cls), [strFromKey(n)], new Map());
      }
      if (n === "__class__" && o.cls.lookup(n) === undefined) return o.cls;
      if (o.cls.issub(BASE_EXC)) {
        if (n === "__suppress_context__") return o.exc.get(n) ?? false;
        if (o.exc.has(n) || exceptionMember(o.cls, n)) return o.exc.get(n) ?? (n === "args" ? new DiasporeTuple([]) : NONE);
        if (n === "with_traceback" && o.cls.lookup(n) === undefined) return new Builtin(n, function* (_I, args, kwargs): G<Value> {
          noKw(`${o.cls.name}.with_traceback`, kwargs);
          if (args.length !== 1) throw diasporeErr("TypeError", `${o.cls.name}.with_traceback() takes exactly one argument (${args.length} given)`);
          const tb = args[0];
          if (!(tb instanceof PyNoneT) && !(tb instanceof NativeObject && tb.kind === "traceback")) throw diasporeErr("TypeError", "__traceback__ must be a traceback or None");
          o.exc.set("__traceback__", tb);
          return o;
        });
        if (n === "add_note" && o.cls.lookup(n) === undefined) return new Builtin(n, function* (_I, args, kwargs): G<Value> {
          noKw(`${o.cls.name}.add_note`, kwargs);
          if (args.length !== 1) throw diasporeErr("TypeError", `${o.cls.name}.add_note() takes exactly one argument (${args.length} given)`);
          const note = args[0];
          if (!isPyString(note instanceof DiasporeInstance ? note.prim : note)) throw diasporeErr("TypeError", `note must be a str, not '${typeName(note)}'`);
          let notes = o.d.get("__notes__");
          if (notes === undefined) { notes = new DiasporeList(); o.d.set("__notes__", notes); }
          const payload = notes instanceof DiasporeInstance ? notes.prim : notes;
          if (!(payload instanceof DiasporeList)) throw diasporeErr("TypeError", "Cannot add note: __notes__ is not a list");
          payload.items.push(note);
          return NONE;
        });
      }
      if ((n === "exceptions" || n === "message") && o.cls.issub(BASE_EXC_GROUP())) {
        return o.exc.get(n) ?? NONE;
      }
      let cv = this.lookupClassAttr(o.cls, n);
      if (cv instanceof SlotDescriptor) return cv.get(o);
      if (cv instanceof DiasporeInstance && cv.prim instanceof Property) cv = cv.prim;
      if (cv instanceof Property) {
        if (cv.fget === null) {
          throw diasporeErr(
            "AttributeError",
            `property '${n}' of '${o.cls.name}' object has no getter`,
          );
        }
        return yield* this.invoke(cv.fget, [o], new Map());
      }
      if (cv instanceof DiasporeInstance) {
        const data = cv.cls.lookup("__set__") !== undefined || cv.cls.lookup("__delete__") !== undefined;
        if (data && cv.cls.lookup("__get__") !== undefined) {
          return yield* this.callDunder(cv, "__get__", [o, o.cls]);
        }
      }
      const dv = instanceHasDict(o.cls) ? o.peekDict()?.get(n) : undefined;
      if (dv !== undefined) return dv;
      if (cv instanceof DiasporeInstance && cv.cls.lookup("__get__") !== undefined) {
        return yield* this.callDunder(cv, "__get__", [o, o.cls]);
      }
      if (cv !== undefined) return this.bindAttr(cv, o, o.cls);
      if (n === "__dict__" && instanceHasDict(o.cls)) return o.getDict();
      if (o.prim !== null) {
        if (o.prim instanceof Property && (n === "getter" || n === "setter" || n === "deleter")) {
          return new Builtin(n, propWither(o.prim, n === "getter" ? "fget" : n === "setter" ? "fset" : "fdel", o.cls));
        }
        if (o.prim instanceof Property && ["__get__", "__set__", "__delete__"].includes(n)) return propertyAccessor(o.prim, o, n);
        if (o.prim instanceof Property || o.prim instanceof StaticMethodV || o.prim instanceof ClassMethodV) return yield* this.getAttr(o.prim, n);
        if (o.prim instanceof NativeObject) return yield* this.getAttr(o.prim, n);
        if (typeof o.prim === "number" && (n === "hex" || n === "fromhex")) {
          return new BoundMethod(this.builtinUnbound("float", n)!, n === "fromhex" ? o.cls : o);
        }
        if (o.prim instanceof DiasporeDict && ["__ior__", "__or__", "__ror__"].includes(n)) return new BoundMethod(this.builtinUnbound("dict", n)!, o);
        if (o.prim instanceof DiasporeList && n === "__reversed__") return new BoundMethod(this.builtinUnbound("list", n)!, o);
        const pm = primMethod(this, o.prim, n);
        if (pm !== null) return pm;
      }
      if (OBJECT_METHODS.has(n)) {
        const descriptor = yield* this.getAttr(o.cls, n);
        return n === "__new__" ? descriptor : new BoundMethod(descriptor, o);
      }
      const ga = o.cls.lookup("__getattr__");
      if (ga !== undefined) {
        return yield* this.invoke(this.bindAttr(ga, o, o.cls), [strFromKey(n)], new Map());
      }
      throw diasporeErr("AttributeError", `'${o.cls.name}' object has no attribute '${n}'`);
    }
    if (o instanceof BuiltinType) {
      if (n === "__doc__" && Object.hasOwn(BUILTIN_DOCS, o.name)) return BUILTIN_DOCS[o.name] ?? NONE;
      if (n === "__flags__") return BigInt(abcCollectionFlags(o));
      if (n === "__name__" || n === "__qualname__") return strFromKey(o.name);
      if (n === "__module__") return this.builtinModule(o);
      if (o.name === "function" && (n === "__code__" || n === "__globals__")) {
        const descriptor = new Builtin(n, function* (I, args): G<Value> { return yield* I.getAttr(args[0], n); });
        descriptor.kind = n === "__code__" ? "getset_descriptor" : "member_descriptor";
        descriptor.objclass = o;
        return descriptor;
      }
      if (n === "__class__") return this.types.get("type")!;
      if (n === "__bases__") return new DiasporeTuple(o.name === "bool" ? [this.types.get("int")!] : [OBJECT]);
      if (n === "__mro__") return new DiasporeTuple(o.name === "bool" ? [o, this.types.get("int")!, OBJECT] : [o, OBJECT]);
      if (n === "__dict__") {
        if (o.namespace === null) {
          const ns = new Map<string, Value>();
          const names = ["__new__", "__repr__", "__str__", "__eq__", "__hash__", ...builtinMethodNames(o.name)];
          if (o.name === "type") names.push("__init__", "__dict__", "__name__", "__bases__", "__mro__", "__prepare__", "__call__");
          if (o.name === "list" || o.name === "range") names.push("__reversed__");
          if (["str", "bytes", "tuple", "list", "dict", "set", "frozenset", "range"].includes(o.name)) names.push("__len__", "__iter__", "__contains__");
          if (["str", "bytes", "tuple", "list", "dict", "range"].includes(o.name)) names.push("__getitem__");
          if (["list", "dict"].includes(o.name)) names.push("__setitem__", "__delitem__");
          if (o.name.endsWith("iterator") || o.name === "generator") names.push("__iter__", "__next__");
          if (o.name === "int" || o.name === "bool") names.push("__index__", "to_bytes", "from_bytes");
          if (o.name === "float") names.push("hex", "fromhex");
          if (o.name === "bytes") names.push("fromhex");
          if (o.name === "dict") names.push("fromkeys");
          if (["list", "dict", "set"].includes(o.name)) names.push("__init__");
          if (o.name === "set" || o.name === "frozenset") names.push("__reduce__");
          for (const name of names) {
            const value = this.builtinUnbound(o.name, name);
            if (value !== null) ns.set(name, value);
          }
          if (["list", "dict", "set"].includes(o.name)) ns.set("__hash__", NONE);
          if (Object.hasOwn(BUILTIN_DOCS, o.name)) ns.set("__doc__", o.name === "type" || o.name === "property" ? this.builtinUnbound(o.name, "__doc__")! : BUILTIN_DOCS[o.name] ?? NONE);
          o.namespace = ns;
        }
        return new DiasporeMappingProxy(o.namespace);
      }
      if (o.name === "dict" && n === "fromkeys") return new Builtin("fromkeys", function* (I, args): G<Value> {
        const result = new DiasporeDict();
        for (const key of yield* iterToArray(I, args[0])) result.set(key, args[1] ?? NONE);
        return result;
      });
      const unbound = this.builtinUnbound(o.name, n);
      if (unbound instanceof StaticMethodV) return unbound.fn;
      if (unbound !== null) return unbound instanceof Builtin && unbound.kind === "classmethod_descriptor" ? new BoundMethod(unbound, o) : unbound;
      throw diasporeErr("AttributeError", `type object '${o.name}' has no attribute '${n}'`);
    }
    if (o instanceof DiasporeClass) {
      if (o.metaclass instanceof DiasporeClass) {
        const descriptor = o.metaclass.lookup(n);
        if (descriptor instanceof Property) {
          if (descriptor.fget === null) throw diasporeErr("AttributeError", `property '${n}' has no getter`);
          return yield* this.invoke(descriptor.fget, [o], new Map());
        }
        if (descriptor instanceof DiasporeInstance && descriptor.cls.lookup("__get__") !== undefined &&
            (descriptor.cls.lookup("__set__") !== undefined || descriptor.cls.lookup("__delete__") !== undefined)) {
          return yield* this.callDunder(descriptor, "__get__", [o, o.metaclass]);
        }
      }
      if (n === "__dict__") return new DiasporeMappingProxy(o.ns);
      if (n === "__flags__") return BigInt((this.classes.has(o) ? 1 << 9 : 0) | abcCollectionFlags(o));
      if (n === "__class__") return o.metaclass ?? this.types.get("type")!;
      if (n === "__name__") return o.name;
      if (n === "__qualname__") return o.ns.get(n) ?? o.name;
      if (n === "__mro__") return new DiasporeTuple(o.prim === null ? [...o.mro] : [...o.mro.slice(0, -1), o.prim, OBJECT]);
      if (n === "__bases__") return new DiasporeTuple(o.declaredBases.length > 0 ? [...o.declaredBases] : o === OBJECT ? [] : [OBJECT]);
      if (o === OBJECT && OBJECT_METHODS.has(n)) {
        return this.builtinUnbound("object", n)!;
      }
      let v = this.lookupClassAttr(o, n);
      if (v instanceof DiasporeInstance && (v.prim instanceof ClassMethodV || v.prim instanceof StaticMethodV)) v = v.prim;
      if (v === undefined) {
        if (n === "__subclasses__") return new Builtin(n, function* (I): G<Value> {
          return new DiasporeList([...I.classes].filter(cls => cls.declaredBases.includes(o) || (o === OBJECT && cls.declaredBases.length === 0)));
        });
        if (n === "__subclasshook__") return new Builtin(n, function* (): G<Value> { return NOT_IMPLEMENTED; });
        if (o.prim !== null) {
          const inherited = this.builtinUnbound(o.prim.name, n);
          if (inherited !== null) return inherited instanceof Builtin && inherited.kind === "classmethod_descriptor" ? new BoundMethod(inherited, o) : inherited;
        }
        if (n === "__init_subclass__") return new BoundMethod(this.builtinUnbound("object", n)!, o);
        if (OBJECT_METHODS.has(n)) return yield* this.getAttr(OBJECT, n);
        if (o.metaclass instanceof DiasporeClass) {
          const attr = o.metaclass.lookup(n);
          if (attr !== undefined) return this.bindAttr(attr, o, o.metaclass);
        }
        throw diasporeErr("AttributeError", `type object '${o.name}' has no attribute '${n}'`);
      }
      if (v instanceof ClassMethodV) return new BoundMethod(v.fn, o);
      if (v instanceof Builtin && v.kind === "classmethod_descriptor") return new BoundMethod(v, o);
      if (v instanceof StaticMethodV) return v.fn;
      if (v instanceof DiasporeInstance && v.cls.lookup("__get__") !== undefined) return yield* this.callDunder(v, "__get__", [NONE, o]);
      return v;
    }
    if (o instanceof SuperObject) {
      const classAccess = o.obj instanceof DiasporeClass && o.obj.issub(o.cls);
      const lookupClass = o.obj instanceof DiasporeInstance ? o.obj.cls
        : classAccess ? o.obj as DiasporeClass
        : o.obj instanceof DiasporeClass && o.obj.metaclass instanceof DiasporeClass ? o.obj.metaclass : null;
      if (lookupClass === null || !lookupClass.issub(o.cls)) {
        throw diasporeErr("TypeError", "super(type, obj): obj must be an instance or subtype of type");
      }
      const mro = lookupClass.mro;
      let start = mro.indexOf(o.cls);
      start = start >= 0 ? start + 1 : 0;
      for (let i = start; i < mro.length; i++) {
        const v = mro[i].ns.get(n);
        if (v !== undefined) return this.bindAttr(v, o.obj, lookupClass);
      }
      if (lookupClass.prim !== null) {
        const inherited = this.builtinUnbound(lookupClass.prim.name, n);
        if (inherited !== null) return n === "__new__" || classAccess ? inherited : new BoundMethod(inherited, o.obj);
      }
      if (start <= mro.indexOf(OBJECT) && n === "__init_subclass__") {
        return new BoundMethod(this.builtinUnbound("object", n)!, o.obj instanceof DiasporeInstance ? o.obj.cls : o.obj);
      }
      if (start <= mro.indexOf(OBJECT) && OBJECT_METHODS.has(n)) {
        const inherited = this.builtinUnbound("object", n)!;
        return n === "__new__" || classAccess ? inherited : new BoundMethod(inherited, o.obj);
      }
      throw diasporeErr("AttributeError", `'super' object has no attribute '${n}'`);
    }
    if (o instanceof DiasporeModule) {
      if (n === "__dict__") return new DiasporeNamespace(o.frame.vars);
      const v = o.frame.vars.get(n);
      if (v !== undefined) return v;
      throw diasporeErr("AttributeError", `module '${o.name}' has no attribute '${n}'`);
    }
    if (o instanceof NativeModule) {
      if (n === "__dict__") return new DiasporeNamespace(o.ns);
      if (n === "__name__") return o.name;
      const v = o.ns.get(n);
      if (v !== undefined) return v;
      throw diasporeErr("AttributeError", `module '${o.name}' has no attribute '${n}'`);
    }
    if (o instanceof DiasporeFile) {
      return this.fileMethod(o, n);
    }
    if (o instanceof StaticMethodV || o instanceof ClassMethodV) {
      if (n === "__func__" || n === "__wrapped__") return o.fn;
      if (n === "__isabstractmethod__") return o.fn instanceof DiasporeFunction ? o.fn.attrs.get(n) ?? false : false;
      return yield* this.getAttr(o.fn, n);
    }
    if (o instanceof Property) {
      if (n === "__isabstractmethod__") {
        for (const fn of [o.fget, o.fset, o.fdel]) {
          if (fn instanceof DiasporeFunction && (yield* this.truth(fn.attrs.get("__isabstractmethod__") ?? false))) return true;
        }
        return false;
      }
      if (n === "setter") return new Builtin("setter", propWither(o, "fset"));
      if (n === "getter") return new Builtin("getter", propWither(o, "fget"));
      if (n === "deleter") return new Builtin("deleter", propWither(o, "fdel"));
      if (n === "fget") return o.fget ?? NONE;
      if (n === "fset") return o.fset ?? NONE;
      if (n === "fdel") return o.fdel ?? NONE;
      if (n === "__doc__") return o.doc ?? NONE;
      if (["__get__", "__set__", "__delete__"].includes(n)) return propertyAccessor(o, o, n);
      throw diasporeErr("AttributeError", `'property' object has no attribute '${n}'`);
    }
    if (o instanceof DiasporeFunction) {
      if (n === "__code__") return functionCode(this, o);
      if (n === "__closure__") return functionClosure(o);
      if (n === "__globals__") {
        let globals = o.metadata.get(n);
        if (globals === undefined) { globals = new DiasporeNamespace(o.closure?.globals ?? new Map()); o.metadata.set(n, globals); }
        return globals;
      }
      if (n === "__module__") return o.metadata.get(n) ?? o.moduleName;
      if (n === "__defaults__") return o.defaults.length ? new DiasporeTuple(o.defaults) : NONE;
      if (n === "__kwdefaults__") return o.kwdefaults.size ? new DiasporeNamespace(o.kwdefaults) : NONE;
      if (n === "__annotations__") {
        let annotations = o.metadata.get(n);
        if (annotations === undefined) { annotations = new DiasporeDict(); o.metadata.set(n, annotations); }
        return annotations;
      }
      if (n === "__get__") return new Builtin("__get__", function* (_I, args): G<Value> {
        argCount("__get__", args, 1, 2);
        return args[0] instanceof PyNoneT ? o : new BoundMethod(o, args[0]);
      });
      if (n === "__call__") return o;
      const metadata = o.metadata.get(n);
      if (metadata !== undefined) return metadata;
      const av = o.attrs.get(n);
      if (av !== undefined) return av;
      if (n === "__name__" || n === "__qualname__") return strFromKey(o.name);
      if (n === "__doc__") return NONE;
      if (n === "__dict__") {
        return new DiasporeNamespace(o.attrs);
      }
      throw diasporeErr("AttributeError", `'function' object has no attribute '${n}'`);
    }
    if (o instanceof BoundMethod) {
      if (n === "__self__") return o.recv;
      if (n === "__func__") return o.fn;
      if (n === "__name__") return callableName(o.fn);
      return yield* this.getAttr(o.fn, n);
    }
    if (n === "__doc__" && !(o instanceof NativeObject) && !(o instanceof Builtin)) {
      const name = typeName(o);
      if (Object.hasOwn(BUILTIN_DOCS, name)) return BUILTIN_DOCS[name] ?? NONE;
    }
    if (o instanceof DiasporeSlice) {
      if (n === "start") return o.lower;
      if (n === "stop") return o.upper;
      if (n === "step") return o.step;
      if (n === "indices") return new Builtin("indices", sliceIndicesMethod(o));
      throw diasporeErr("AttributeError", `'slice' object has no attribute '${n}'`);
    }
    if (o instanceof Complex) {
      if (n === "real") return o.re;
      if (n === "imag") return o.im;
      if (n === "conjugate") {
        return new Builtin("conjugate", function* (): G<Value> {
          return new Complex(o.re, -o.im);
        });
      }
      throw diasporeErr("AttributeError", `'complex' object has no attribute '${n}'`);
    }
    if (o instanceof DiasporeDict && ["__ior__", "__or__", "__ror__"].includes(n)) return new BoundMethod(this.builtinUnbound("dict", n)!, o);
    if (o instanceof DiasporeList && n === "__reversed__") return new BoundMethod(this.builtinUnbound("list", n)!, o);
    if (
      (typeof o === "bigint" || typeof o === "number" || typeof o === "boolean") &&
      ["real", "imag", "conjugate", "bit_length", "numerator", "denominator", "is_integer"].includes(n)
    ) {
      return numAttr(o, n);
    }
    if (o instanceof DiasporeRange && n === "__reversed__") return new BoundMethod(this.builtinUnbound("range", n)!, o);
    if (o instanceof Uint8Array && n === "fromhex") return new BoundMethod(this.builtinUnbound("bytes", n)!, this.types.get("bytes")!);
    if (n === "maketrans" && (isPyString(o) || o instanceof Uint8Array)) return (this.builtinUnbound(isPyString(o) ? "str" : "bytes", n) as StaticMethodV).fn;
    if (typeof o === "number" && (n === "hex" || n === "fromhex")) {
      return new BoundMethod(this.builtinUnbound("float", n)!, n === "fromhex" ? this.types.get("float")! : o);
    }
    if ((typeof o === "bigint" || typeof o === "boolean") && n === "__index__") {
      return new BoundMethod(this.builtinUnbound("int", n)!, o);
    }
    if ((typeof o === "bigint" || typeof o === "boolean") && n === "to_bytes") return new Builtin("to_bytes", function* (I, args, kw): G<Value> {
      argCount("to_bytes", args, 0, 2);
      const length = args[0] ?? kw.get("length") ?? 1n;
      const order = args[1] ?? kw.get("byteorder") ?? "big";
      if (typeof length !== "bigint") throw diasporeErr("TypeError", "length must be an integer");
      if (length < 0n) throw diasporeErr("ValueError", "length argument must be non-negative");
      if (order !== "big" && order !== "little") throw diasporeErr("ValueError", "byteorder must be either 'little' or 'big'");
      const signed = yield* I.truth(kw.get("signed") ?? false);
      let integer = typeof o === "boolean" ? (o ? 1n : 0n) : o;
      const bits = length * 8n;
      const limit = 1n << (signed && bits ? bits - 1n : bits);
      if ((!signed && integer < 0n) || integer >= limit || (signed && integer < -limit)) throw diasporeErr("OverflowError", "int too big to convert");
      if (integer < 0n) integer += 1n << bits;
      const result = new Uint8Array(Number(length));
      for (let j = 0; j < result.length; j++) { result[order === "little" ? j : result.length - j - 1] = Number(integer & 255n); integer >>= 8n; }
      return result;
    });
    if (o instanceof CodeObject) {
      const value = o.attrs.get(n);
      if (value !== undefined) return value;
      if (n === "co_filename") return o.filename;
      throw diasporeErr("AttributeError", `code object has no attribute '${n}'`);
    }
    if (o instanceof DiasporeGenerator) {
      return this.genMethod(o, n);
    }
    if (o instanceof NativeObject) {
      const pv = o.props.get(n);
      if (pv !== undefined) return pv;
      if (o.kind === "GenericAlias" && n !== "__mro_entries__") return yield* this.getAttr(o.props.get("__origin__")!, n);
      if (o.kind === "cell" && n === "cell_contents") throw diasporeErr("ValueError", "Cell is empty");
      const mv = o.methods.get(n);
      if (mv !== undefined) return mv;
      if (n === "__doc__" && Object.hasOwn(BUILTIN_DOCS, o.kind)) return BUILTIN_DOCS[o.kind] ?? NONE;
      throw diasporeErr("AttributeError", `'${o.kind}' object has no attribute '${n}'`);
    }
    if (o instanceof Builtin) {
      if (n === "__name__") return strFromKey(o.name);
      if (n === "__qualname__") return strFromKey(o.self instanceof BuiltinType || o.self instanceof DiasporeClass ? `${o.self.name}.${o.name}` : o.name);
      if (n === "__self__" && o.kind === "builtin_function_or_method") return o.self ?? NONE;
      if (n === "__objclass__" && o.objclass !== undefined) return o.objclass;
      if (n === "__module__") return this.builtinModule(o);
      if (n === "__get__") return new Builtin("__get__", function* (I, args): G<Value> {
        if ((o.kind === "getset_descriptor" || o.kind === "member_descriptor") && !(args[0] instanceof PyNoneT)) return yield* o.fn(I, [args[0]], new Map());
        if (o.kind === "classmethod_descriptor") return new BoundMethod(o, args[1] ?? (yield* I.getAttr(args[0], "__class__")));
        if (args[0] instanceof PyNoneT) return o;
        return new BoundMethod(o, args[0]);
      });
      throw diasporeErr("AttributeError", `builtin function has no attribute '${n}'`);
    }
    if (OBJECT_METHODS.has(n)) {
      const cls = yield* this.invoke(this.types.get("type")!, [o], new Map());
      const descriptor = yield* this.getAttr(cls, n);
      return n === "__new__" ? descriptor : new BoundMethod(descriptor, o);
    }
    return builtinMethod(this, o, n);
  }

  private builtinModule(value: Value): Value {
    for (const [name, module] of this.natives) {
      for (const member of module.ns.values()) if (member === value) return name;
    }
    for (const [name, type] of this.types) {
      if (type === value && name.includes(".")) return name.slice(0, name.lastIndexOf("."));
    }
    return NONE;
  }

  /** `dict.__getitem__`, `list.append`, ... as unbound functions operating on
   * the RAW primitive of a builtin-subclass instance (bypassing overrides). */
  builtinUnbound(tname: string, method: string): Value | null {
    let descriptors = this.builtinDescriptors.get(tname);
    const cached = descriptors?.get(method);
    if (cached !== undefined) return cached;
    const descriptor = this.makeBuiltinUnbound(tname, method);
    if (descriptor !== null) {
      if (method === "__new__" && descriptor instanceof Builtin) descriptor.self = tname === "object" ? OBJECT : this.types.get(tname);
      if (descriptor instanceof Builtin && descriptor.kind.endsWith("_descriptor") && descriptor.objclass === undefined) {
        descriptor.objclass = tname === "object" ? OBJECT : this.types.get(tname);
      }
      if (descriptors === undefined) {
        descriptors = new Map();
        this.builtinDescriptors.set(tname, descriptors);
      }
      descriptors.set(method, descriptor);
    }
    return descriptor;
  }

  private makeBuiltinUnbound(tname: string, method: string): Value | null {
    if (method === "__hash__" && ["list", "dict", "set"].includes(tname)) return NONE;
    if (tname === "GenericAlias" && ["__eq__", "__ne__", "__hash__"].includes(method)) return new Builtin(method, function* (I, args, kwargs): G<Value> {
      noKw(method, kwargs);
      argCount(method, args, method === "__hash__" ? 1 : 2, method === "__hash__" ? 1 : 2);
      const alias = args[0];
      if (!(alias instanceof NativeObject) || alias.kind !== "GenericAlias") throw diasporeErr("TypeError", `descriptor '${method}' requires a 'types.GenericAlias' object`);
      return yield* I.invoke(alias.methods.get(method)!, args.slice(1), new Map());
    });
    if (tname === "bool" && !["__new__", "__repr__", "__and__", "__or__", "__xor__", "__rand__", "__ror__", "__rxor__"].includes(method)) {
      return this.builtinUnbound("int", method);
    }
    if (tname === "bytes" && method === "fromhex") {
      const descriptor = new Builtin(method, function* (I, args, kwargs): G<Value> {
        const cls = args[0];
        const name = cls instanceof DiasporeClass || cls instanceof BuiltinType ? cls.name : "bytes";
        noKw(`${name}.fromhex`, kwargs);
        if (args.length !== 2) throw diasporeErr("TypeError", `${name}.fromhex() takes exactly one argument (${Math.max(0, args.length - 1)} given)`);
        const text = args[1] instanceof DiasporeInstance && args[1].prim !== null ? args[1].prim : args[1];
        if (!isPyString(text)) throw diasporeErr("TypeError", `fromhex() argument must be str, not ${text === NONE ? "None" : typeName(args[1])}`);
        I.fuelN(typeof text === "string" ? text.length : text.points.length);
        const value = parseBytesHex(text);
        return cls === I.types.get("bytes") ? value : yield* I.invoke(cls, [value], new Map());
      });
      descriptor.kind = "classmethod_descriptor";
      return descriptor;
    }
    if (tname === "float" && method === "fromhex") {
      const descriptor = new Builtin(method, function* (I, args, kwargs): G<Value> {
        const name = args[0] instanceof DiasporeClass || args[0] instanceof BuiltinType ? args[0].name : "float";
        noKw(`${name}.fromhex`, kwargs);
        if (args.length !== 2) throw diasporeErr("TypeError", `${name}.fromhex() takes exactly one argument (${Math.max(0, args.length - 1)} given)`);
        const text = args[1] instanceof DiasporeInstance && args[1].prim !== null ? args[1].prim : args[1];
        if (!isPyString(text)) throw diasporeErr("TypeError", `bad argument type for built-in operation`);
        I.fuelN(typeof text === "string" ? text.length : strText(text).length);
        const value = parseFloatHex(text);
        return args[0] === I.types.get("float") ? value : yield* I.invoke(args[0], [value], new Map());
      });
      descriptor.kind = "classmethod_descriptor";
      return descriptor;
    }
    if (tname === "float" && method === "hex") return new Builtin(method, function* (_I, args, kwargs): G<Value> {
      if (args.length === 0) throw diasporeErr("TypeError", "unbound method float.hex() needs an argument");
      const name = args[0] instanceof DiasporeInstance ? args[0].cls.name : "float";
      noKw(`${name}.hex`, kwargs);
      if (args.length !== 1) throw diasporeErr("TypeError", `${name}.hex() takes no arguments (${args.length - 1} given)`);
      const value = args[0] instanceof DiasporeInstance && args[0].prim !== null ? args[0].prim : args[0];
      if (typeof value !== "number") throw diasporeErr("TypeError", `descriptor 'hex' for 'float' objects doesn't apply to a '${typeName(args[0])}' object`);
      return formatFloatHex(value);
    });
    const self = this;
    const raw = (o: Value): Value => (o instanceof DiasporeInstance && o.prim !== null ? o.prim : o);
    if ((tname === "list" || tname === "range") && method === "__reversed__") return new Builtin(method, function* (_I, args, kwargs): G<Value> {
      noKw("__reversed__", kwargs);
      argCount("__reversed__", args, 1, 1);
      const value = raw(args[0]);
      if (tname === "list" && value instanceof DiasporeList) return new ListReverseIter(value);
      if (tname === "range" && value instanceof DiasporeRange) return new RangeIter(value, true);
      throw diasporeErr("TypeError", `descriptor '__reversed__' requires a ${tname}`);
    });
    if (tname === "list" && (method === "__eq__" || method === "__ne__")) return new Builtin(method, function* (I, args, kwargs): G<Value> {
      noKw(method, kwargs);
      argCount(method, args, 1, 2);
      const receiver = raw(args[0]);
      if (!(receiver instanceof DiasporeList)) {
        const name = receiver instanceof NativeObject && receiver.kind === "GenericAlias" ? "types.GenericAlias" : typeName(receiver);
        throw diasporeErr("TypeError", `descriptor '${method}' requires a 'list' object but received a '${name}'`);
      }
      argCount(method, args, 2, 2);
      const other = raw(args[1]);
      return other instanceof DiasporeList ? yield* I.compare(method === "__eq__" ? "eq" : "ne", receiver, other) : NOT_IMPLEMENTED;
    });
    if (["int", "float", "complex"].includes(tname) && method === "__abs__") return new Builtin(method, function* (I, args, kwargs): G<Value> {
      noKw("__abs__", kwargs);
      argCount("__abs__", args, 1, 1);
      return yield* I.invoke(I.builtins.get("abs")!, [raw(args[0])], new Map());
    });
    if (tname === "dict" && ["__ior__", "__or__", "__ror__"].includes(method)) return new Builtin(method, function* (I, args, kwargs): G<Value> {
      noKw(method, kwargs);
      argCount(method, args, 2, 2);
      const receiver = raw(args[0]);
      if (!(receiver instanceof DiasporeDict)) throw diasporeErr("TypeError", `descriptor '${method}' requires a dict`);
      if (method === "__ior__") {
        yield* dictUpdateFrom(I, receiver, args[1]);
        return args[0];
      }
      const other = raw(args[1]);
      if (!(other instanceof DiasporeDict)) return NOT_IMPLEMENTED;
      return yield* binop(I, "bitor", method === "__ror__" ? other : receiver, method === "__ror__" ? receiver : other);
    });
    if ((tname === "set" || tname === "frozenset") && method === "__reduce__") return new Builtin(method, function* (I, args, kwargs): G<Value> {
      argCount(`${tname}.__reduce__`, args, 1, 1);
      if (kwargs.size !== 0) throw diasporeErr("TypeError", "__reduce__() takes no keyword arguments");
      return yield* setReduce(I, args[0]);
    });
    if (tname === "str" && method === "maketrans") return new StaticMethodV(new Builtin("maketrans", function* (_I, args, kwargs): G<Value> {
      return makeStrTranslation(args, kwargs);
    }));
    if ((tname === "bytes" || tname === "bytearray") && method === "maketrans") return new StaticMethodV(new Builtin("maketrans", function* (_I, args, kwargs): G<Value> {
      return makeBytesTranslation(args, kwargs);
    }));
    if (tname === "object" && method === "__init_subclass__") {
      const descriptor = new Builtin(method, function* (_I, args, kwargs): G<Value> {
        const cls = args[0];
        const name = cls instanceof DiasporeClass || cls instanceof BuiltinType ? cls.name : "object";
        if (args.length !== 1 || kwargs.size !== 0) throw diasporeErr("TypeError", `${name}.__init_subclass__() takes no keyword arguments`);
        return NONE;
      });
      descriptor.kind = "classmethod_descriptor";
      return descriptor;
    }
    if (tname === "type" && method === "__call__") return new Builtin("__call__", function* (I, args, kwargs): G<Value> {
      if (!(args[0] instanceof DiasporeClass)) throw diasporeErr("TypeError", "type.__call__ requires a type");
      return yield* I.instantiate(args[0], args.slice(1), kwargs);
    });
    if ((tname === "type" && ["__dict__", "__name__", "__bases__", "__mro__", "__doc__"].includes(method)) || (tname === "property" && method === "__doc__")) {
      const descriptor = new Builtin(method, function* (I, args): G<Value> { return yield* I.getAttr(args[0], method); });
      descriptor.kind = tname === "property" ? "member_descriptor" : "getset_descriptor";
      return descriptor;
    }
    if (tname === "dict" && method === "fromkeys") {
      const descriptor = new Builtin(method, function* (I, args): G<Value> {
        const result = new DiasporeDict();
        for (const key of yield* iterToArray(I, args[1])) result.set(key, args[2] ?? NONE);
        return result;
      });
      descriptor.kind = "classmethod_descriptor";
      return descriptor;
    }
    if (method === "__new__") return new Builtin("__new__", function* (I, args, kw): G<Value> {
      const cls = args[0];
      if (!(cls instanceof DiasporeClass || cls instanceof BuiltinType)) throw diasporeErr("TypeError", "__new__ requires a type");
      if (tname === "object") {
        if (!(cls instanceof DiasporeClass)) throw diasporeErr("TypeError", "object.__new__ requires a type");
        return new DiasporeInstance(cls);
      }
      if (tname === "type") {
        argCount("type.__new__", args, 4, 4);
        const [name, bases, ns] = args.slice(1);
        if (!isPyString(name) || !(bases instanceof DiasporeTuple) || !(ns instanceof DiasporeDict)) throw diasporeErr("TypeError", "type.__new__ argument types: (type, str, tuple, dict)");
        const checked: (DiasporeClass | BuiltinType)[] = [];
        for (const base of bases.items) checked.push(yield* I.checkBase(base));
        return yield* I.buildClass(strKey(name), checked, dictToMap(ns), cls, kw, null, false);
      }
      const primitive = ["dict", "list", "set"].includes(tname)
        ? primEmpty(tname) : yield* I.types.get(tname)!.ctor(I, args.slice(1), kw);
      if (cls instanceof BuiltinType) return primitive;
      const result = new DiasporeInstance(cls);
      result.prim = primitive;
      return result;
    });
    if (tname === "object" && OBJECT_METHODS.has(method)) {
      const descriptor = new Builtin(method, function* (I, args, kwargs): G<Value> {
        if (method === "__reduce__" || method === "__reduce_ex__" || method === "__getstate__") noKw(method, kwargs);
        if (method === "__reduce__") {
          argCount("object.__reduce__", args, 1, 1);
          return yield* objectReduce(I, args[0], 0n, false);
        }
        if (method === "__reduce_ex__") {
          argCount("object.__reduce_ex__", args, 2, 2);
          return yield* objectReduce(I, args[0], args[1]);
        }
        if (method === "__getstate__") {
          argCount("object.__getstate__", args, 1, 1);
          return yield* objectGetState(I, args[0]);
        }
        if (method === "__getattribute__") {
          if (!isPyString(args[1])) throw diasporeErr("TypeError", "attribute name must be string");
          return yield* I.getAttr(args[0], strKey(args[1]), true);
        }
        if (method === "__init__") return NONE;
        if (method === "__str__") return yield* I.repr(args[0]);
        if (method === "__format__") {
          argCount("object.__format__", args, 2, 2);
          if (!isPyString(args[1])) throw diasporeErr("TypeError", "format_spec must be a string");
          if (strText(args[1]) !== "") throw diasporeErr("TypeError", `unsupported format string passed to ${typeName(args[0])}.__format__`);
          return yield* I.str(args[0]);
        }
        return args[0] instanceof DiasporeInstance ? `<${args[0].cls.name} object>` : yield* I.repr(args[0]);
      });
      descriptor.kind = "wrapper_descriptor";
      return descriptor;
    }
    if (method === "__str__" && ["int", "bool", "float", "complex"].includes(tname)) return this.builtinUnbound("object", method);
    if (method === "__format__") {
      if (!["str", "int", "bool", "float", "complex"].includes(tname)) return this.builtinUnbound("object", method);
      return new Builtin(method, function* (I, args): G<Value> {
        argCount("__format__", args, 2, 2);
        if (!isPyString(args[1])) throw diasporeErr("TypeError", "format_spec must be a string");
        return yield* formatValue(I, raw(args[0]), args[1]);
      });
    }
    if (tname === "type" && method === "__init__") return new Builtin("__init__", function* (): G<Value> { return NONE; });
    if (tname === "type" && method === "__prepare__") return new Builtin("__prepare__", function* (): G<Value> { return new DiasporeDict(); });
    if (tname === "dict" && method === "__init__") return new Builtin("__init__", function* (I, args, kwargs): G<Value> {
      argCount("dict.__init__", args, 1, 2);
      const receiver = raw(args[0]);
      if (!(receiver instanceof DiasporeDict)) throw diasporeErr("TypeError", "descriptor '__init__' requires a dict");
      return yield* I.invoke(builtinMethod(I, receiver, "update"), args.slice(1), kwargs);
    });
    if ((tname === "list" || tname === "set") && method === "__init__") return new Builtin("__init__", function* (I, args, kwargs): G<Value> {
      argCount(`${tname}.__init__`, args, 1, 2);
      if (kwargs.size !== 0) throw diasporeErr("TypeError", `${tname}() takes no keyword arguments`);
      const receiver = raw(args[0]);
      if (tname === "list" ? !(receiver instanceof DiasporeList) : !(receiver instanceof DiasporeSet) || receiver instanceof DiasporeFrozenSet) {
        throw diasporeErr("TypeError", `descriptor '__init__' requires a ${tname}`);
      }
      if (receiver instanceof DiasporeList) receiver.items.length = 0;
      else (receiver as DiasporeSet).clear();
      if (args.length === 1) return NONE;
      const iterator = yield* getIter(I, args[1]);
      while (true) {
        I.fuel();
        const [value, present] = yield* iterator.next();
        if (!present) break;
        if (receiver instanceof DiasporeList) receiver.items.push(value);
        else (receiver as DiasporeSet).add(value);
      }
      return NONE;
    });
    if (method === "__init__" && ["classmethod", "staticmethod", "property"].includes(tname)) return new Builtin("__init__", function* (I, args, kwargs): G<Value> {
      if (!(args[0] instanceof DiasporeInstance)) throw diasporeErr("TypeError", "descriptor initialization requires an instance");
      args[0].prim = yield* I.types.get(tname)!.ctor(I, args.slice(1), kwargs);
      if (args[0].prim instanceof Property) args[0].d.set("__doc__", args[0].prim.doc ?? NONE);
      return NONE;
    });
    if (tname === "int" && method === "__index__") return new Builtin("__index__", function* (_I, args): G<Value> {
      const value = raw(args[0]);
      if (typeof value === "bigint") return value;
      if (typeof value === "boolean") return value ? 1n : 0n;
      throw diasporeErr("TypeError", "descriptor '__index__' requires an int");
    });
    if (tname === "int" && method === "from_bytes") return new Builtin("from_bytes", function* (I, args, kw): G<Value> {
      argCount("from_bytes", args, 1, 2);
      const order = args[1] ?? kw.get("byteorder") ?? "big";
      if (order !== "little" && order !== "big") throw diasporeErr("ValueError", "byteorder must be either 'little' or 'big'");
      const source = bufferBytes(args[0]) ?? (yield* I.invoke(I.types.get("bytes")!, [args[0]], new Map())) as Uint8Array;
      let result = 0n;
      for (let j = 0; j < source.length; j++) result = (result << 8n) | BigInt(source[order === "big" ? j : source.length - j - 1]);
      if ((yield* I.truth(kw.get("signed") ?? false)) && source.length && source[order === "big" ? 0 : source.length - 1] >= 128) result -= 1n << BigInt(source.length * 8);
      return result;
    });
    const dunders: Record<string, BuiltinFn> = {
      __repr__: function* (I, args): G<Value> { return yield* I.repr(raw(args[0])); },
      __str__: function* (I, args): G<Value> { return yield* I.str(raw(args[0])); },
      __eq__: function* (I, args): G<Value> { return yield* eq(I, raw(args[0]), raw(args[1])); },
      __hash__: function* (I, args): G<Value> { return yield* I.invoke(I.builtins.get("hash")!, [raw(args[0])], new Map()); },
      __delitem__: function* (I, args): G<Value> { yield* delIndex(I, raw(args[0]), args[1]); return NONE; },
      __next__: function* (I, args): G<Value> { return yield* I.invoke(I.builtins.get("next")!, [raw(args[0])], new Map()); },
      __getitem__: function* (_I, args): G<Value> {
        return yield* getIndex(self, raw(args[0]), args[1]);
      },
      __setitem__: function* (_I, args): G<Value> {
        yield* setIndex(self, raw(args[0]), args[1], args[2]);
        return NONE;
      },
      __len__: function* (_I, args): G<Value> {
        return yield* diasporeLen(self, raw(args[0]));
      },
      __contains__: function* (_I, args): G<Value> {
        return yield* contains(self, raw(args[0]), args[1]);
      },
      __iter__: function* (_I, args): G<Value> {
        return yield* getIter(self, raw(args[0]));
      },
    };
    if (method in dunders) return new Builtin(method, dunders[method]);
    if (methodAllowed(tname, method)) {
      const descriptor = new Builtin(method, function* (I2, args, kwargs): G<Value> {
        if (args.length === 0) throw diasporeErr("TypeError", `unbound method ${tname}.${method}() needs an argument`);
        const receiver = raw(args[0]);
        const receiverType = typeName(receiver);
        if (receiverType !== tname && !(tname === "int" && receiverType === "bool")) {
          throw diasporeErr("TypeError", `descriptor '${method}' for '${tname}' objects doesn't apply to a '${typeName(args[0])}' object`);
        }
        const fn = builtinMethod(self, receiver, method) as Builtin;
        return yield* fn.fn(I2, args.slice(1), kwargs);
      });
      descriptor.kind = "method_descriptor";
      return descriptor;
    }
    if (tname !== "object" && OBJECT_METHODS.has(method)) return this.builtinUnbound("object", method);
    return null;
  }

  fileMethod(o: DiasporeFile, n: string): Value {
    const m = fileMethodTable(this, o, n);
    if (m === null) {
      throw diasporeErr("AttributeError", `'file' object has no attribute '${n}'`);
    }
    return m;
  }

  /** Exhausted-generator StopIteration carrying the return value in args[0]
   * (first exhaustion only, like CPython). */
  private genStop(g: DiasporeGenerator): DiasporeError {
    const rv = g.takeRetval();
    return new DiasporeError(rv instanceof PyNoneT ? makeExc("StopIteration") : makeExc("StopIteration", rv));
  }

  genMethod(g: DiasporeGenerator, n: string): Value {
    const self = this;
    if (n === "send") {
      return new Builtin("send", function* (_I, args): G<Value> {
        argCount("send", args, 1, 1);
        const [v, ok] = g.sendValue(args[0]);
        if (ok) return v;
        throw self.genStop(g);
      });
    }
    if (n === "throw") {
      return new Builtin("throw", function* (I2, args): G<Value> {
        argCount("throw", args, 1, 1);
        let exc = args[0];
        if (exc instanceof DiasporeClass && exc.issub(BASE_EXC)) {
          exc = yield* I2.instantiate(exc, [], new Map());
        }
        if (!(exc instanceof DiasporeInstance && (exc as DiasporeInstance).cls.issub(BASE_EXC))) {
          throw diasporeErr("TypeError", "exceptions must derive from BaseException");
        }
        const [v, ok] = g.throwValue(exc);
        if (ok) return v;
        throw self.genStop(g);
      });
    }
    if (n === "close") {
      return new Builtin("close", function* (): G<Value> {
        g.close();
        return NONE;
      });
    }
    throw diasporeErr("AttributeError", `'generator' object has no attribute '${n}'`);
  }

  *setAttr(o: Value, n: string, v: Value): G<void> {
    if (o instanceof Property && n === "__doc__") { o.doc = v; return; }
    if (o instanceof NativeObject && o.kind === "cell" && n === "cell_contents") {
      o.props.set(n, v);
      return;
    }
    if (o instanceof DiasporeFunction) {
      if (n === "__defaults__") {
        if (!(v instanceof DiasporeTuple || v instanceof PyNoneT)) throw diasporeErr("TypeError", "__defaults__ must be set to a tuple object");
        o.defaults = v instanceof DiasporeTuple ? v.items : [];
        return;
      }
      if (n === "__kwdefaults__") {
        if (!(v instanceof DiasporeDict || v instanceof PyNoneT)) throw diasporeErr("TypeError", "__kwdefaults__ must be set to a dict object");
        o.kwdefaults = v instanceof DiasporeDict ? dictToMap(v) : new Map();
        return;
      }
      if (n === "__name__") {
        if (!isPyString(v)) throw diasporeErr("TypeError", "__name__ must be a string");
        o.name = strKey(v);
      } else if (n === "__code__") {
        if (!(v instanceof CodeObject)) throw diasporeErr("TypeError", "__code__ must be set to a code object");
        replaceFunctionCode(this, o, v);
      } else if (["__module__", "__doc__", "__qualname__", "__annotations__"].includes(n)) {
        if (n === "__qualname__" && !isPyString(v)) throw diasporeErr("TypeError", "__qualname__ must be a string");
        if (n === "__annotations__" && !(v instanceof DiasporeDict)) throw diasporeErr("TypeError", "__annotations__ must be a dict");
        o.metadata.set(n, v);
      } else {
        o.attrs.set(n, v);
      }
      return;
    }
    if (o instanceof DiasporeInstance) {
      const hook = o.cls.lookup("__setattr__");
      if (hook !== undefined && hook !== OBJECT.ns.get("__setattr__")) {
        yield* this.invoke(this.bindAttr(hook, o, o.cls), [strFromKey(n), v], new Map());
      } else {
        yield* this.setAttrBase(o, n, v);
      }
      return;
    }
    if (o instanceof DiasporeClass) {
      let descriptor = o.metaclass instanceof DiasporeClass ? o.metaclass.lookup(n) : undefined;
      if (descriptor instanceof DiasporeInstance && descriptor.prim instanceof Property) descriptor = descriptor.prim;
      if (descriptor instanceof Property) {
        if (descriptor.fset === null) throw diasporeErr("AttributeError", `property '${n}' of '${o.metaclass?.name}' object has no setter`);
        yield* this.invoke(descriptor.fset, [o, v], new Map());
        return;
      }
      if (descriptor instanceof DiasporeInstance && descriptor.cls.lookup("__set__") !== undefined) {
        yield* this.callDunder(descriptor, "__set__", [o, v]);
        return;
      }
      if (n === "__name__") {
        if (!isPyString(v)) throw diasporeErr("TypeError", `can only assign string to ${o.name}.__name__, not '${typeName(v)}'`);
        o.name = strKey(v);
        return;
      }
      o.runtime ??= this;
      o.ns.set(n, v);
      return;
    }
    if (o instanceof DiasporeModule) {
      o.frame.vars.set(n, v);
      return;
    }
    if (o instanceof NativeModule) {
      o.ns.set(n, v);
      return;
    }
    throw diasporeErr("AttributeError", `'${typeName(o)}' object has no attribute '${n}'`);
  }

  *setAttrBase(o: DiasporeInstance, n: string, v: Value): G<void> {
    if (o.cls.issub(BASE_EXC)) {
      if ((n === "__cause__" || n === "__context__") && !(v instanceof PyNoneT) && !(v instanceof DiasporeInstance && v.cls.issub(BASE_EXC))) {
        throw diasporeErr("TypeError", n === "__cause__" ? "exception cause must be None or derive from BaseException" : "exception context must be None or derive from BaseException");
      }
      if (n === "__traceback__" && !(v instanceof PyNoneT) && !(v instanceof NativeObject && v.kind === "traceback")) throw diasporeErr("TypeError", "__traceback__ must be a traceback or None");
      if (n === "__suppress_context__" && typeof v !== "boolean") throw diasporeErr("TypeError", "attribute value type must be bool");
      if (n === "__cause__") o.exc.set("__suppress_context__", true);
      if (n === "args") {
        o.exc.set(n, new DiasporeTuple(yield* iterToArray(this, v)));
        return;
      }
      if (o.exc.has(n) || exceptionMember(o.cls, n)) {
        o.exc.set(n, v);
        return;
      }
    }
    let prop = o.cls.lookup(n);
    if (prop instanceof SlotDescriptor) { prop.set(o, v); return; }
    if (prop instanceof DiasporeInstance && prop.prim instanceof Property) prop = prop.prim;
    if (prop instanceof Property) {
      if (prop.fset === null) {
        throw diasporeErr("AttributeError", `property '${n}' of '${o.cls.name}' object has no setter`);
      }
      yield* this.invoke(prop.fset, [o, v], new Map());
      return;
    }
    if (prop instanceof DiasporeInstance) {
      if (prop.cls.lookup("__set__") !== undefined) {
        yield* this.callDunder(prop, "__set__", [o, v]);
        return;
      }
      if (prop.cls.lookup("__delete__") !== undefined) {
        throw diasporeErr("AttributeError", "__set__");
      }
    }
    if (!instanceHasDict(o.cls)) throw diasporeErr("AttributeError", `'${o.cls.name}' object has no attribute '${n}'`);
    if (n === "__dict__" && prop === undefined) { o.setDict(v); return; }
    o.d.set(n, v);
  }

  *delAttr(o: Value, n: string): G<void> {
    if (o instanceof Property && n === "__doc__") { o.doc = null; return; }
    if (o instanceof NativeObject && o.kind === "cell" && n === "cell_contents") {
      o.props.delete(n);
      return;
    }
    if (o instanceof DiasporeInstance) {
      const hook = o.cls.lookup("__delattr__");
      if (hook !== undefined && hook !== OBJECT.ns.get("__delattr__")) {
        yield* this.invoke(this.bindAttr(hook, o, o.cls), [strFromKey(n)], new Map());
      } else {
        yield* this.delAttrBase(o, n);
      }
      return;
    }
    if (o instanceof DiasporeClass) {
      let descriptor = o.metaclass instanceof DiasporeClass ? o.metaclass.lookup(n) : undefined;
      if (descriptor instanceof DiasporeInstance && descriptor.prim instanceof Property) descriptor = descriptor.prim;
      if (descriptor instanceof Property) {
        if (descriptor.fdel === null) throw diasporeErr("AttributeError", `property '${n}' of '${o.metaclass?.name}' object has no deleter`);
        yield* this.invoke(descriptor.fdel, [o], new Map());
        return;
      }
      if (descriptor instanceof DiasporeInstance && descriptor.cls.lookup("__delete__") !== undefined) {
        yield* this.callDunder(descriptor, "__delete__", [o]);
        return;
      }
      if (o.ns.delete(n)) return;
      throw diasporeErr("AttributeError", `type object '${o.name}' has no attribute '${n}'`);
    }
    if (o instanceof DiasporeFunction && o.attrs.delete(n)) return;
    if (o instanceof DiasporeModule && o.frame.vars.delete(n)) return;
    if (o instanceof NativeModule && o.ns.delete(n)) return;
    throw diasporeErr("AttributeError", `'${typeName(o)}' object has no attribute '${n}'`);
  }

  *delAttrBase(o: DiasporeInstance, n: string): G<void> {
    if (o.cls.issub(BASE_EXC) && EXCEPTION_MEMBERS.has(n)) {
      throw diasporeErr("TypeError", n === "__suppress_context__" ? "can't delete numeric/char attribute" : `${n} may not be deleted`);
    }
    if (o.cls.issub(BASE_EXC) && (o.exc.has(n) || exceptionMember(o.cls, n))) {
      o.exc.delete(n);
      return;
    }
    let prop = o.cls.lookup(n);
    if (prop instanceof SlotDescriptor) { prop.delete(o); return; }
    if (prop instanceof DiasporeInstance && prop.prim instanceof Property) prop = prop.prim;
    if (prop instanceof Property) {
      if (prop.fdel === null) {
        throw diasporeErr("AttributeError", `property '${n}' of '${o.cls.name}' object has no deleter`);
      }
      yield* this.invoke(prop.fdel, [o], new Map());
      return;
    }
    if (prop instanceof DiasporeInstance) {
      if (prop.cls.lookup("__delete__") !== undefined) {
        yield* this.callDunder(prop, "__delete__", [o]);
        return;
      }
      if (prop.cls.lookup("__set__") !== undefined) {
        throw diasporeErr("AttributeError", "__delete__");
      }
    }
    if (!instanceHasDict(o.cls) || !o.peekDict()?.delete(n)) {
      throw diasporeErr("AttributeError", `'${o.cls.name}' object has no attribute '${n}'`);
    }
  }

  // ------------------------------------------------------------- protocols

  *truth(v: Value): G<boolean> {
    return yield* truth(this, v);
  }

  *diasporeLen(v: Value): G<bigint> {
    return yield* diasporeLen(this, v);
  }

  *str(v: Value): G<PyString> {
    return yield* strOf(this, v);
  }

  *repr(v: Value): G<PyString> {
    return yield* reprOf(this, v);
  }

  *excStrOf(v: DiasporeInstance): G<PyString> {
    return yield* excStr(this, v);
  }

  *binop(kind: string, l: Value, r: Value): G<Value> {
    return yield* binop(this, kind, l, r);
  }

  *compare(kind: string, l: Value, r: Value): G<boolean> {
    return yield* this.truth(yield* compare(this, kind, l, r));
  }

  *eq(l: Value, r: Value): G<boolean> {
    return yield* eq(this, l, r);
  }

  *eqElem(a: Value, b: Value): G<boolean> {
    return yield* eqElem(this, a, b);
  }

  *contains(container: Value, item: Value): G<boolean> {
    return yield* contains(this, container, item);
  }

  *sortValues(items: Value[], key: Value | null, reverse: boolean): G<void> {
    yield* sortValues(this, items, key, reverse);
  }

  *formatValue(v: Value, spec: PyString): G<PyString> {
    return yield* formatValue(this, v, spec);
  }

  // ------------------------------------------------------------- output

  write(s: PyString): void {
    this.stdoutW(strText(s));
  }

  ewrite(s: PyString): void {
    this.stderrW(strText(s));
  }

  // ------------------------------------------------------------- capabilities

  requireCap(cap: string): void {
    if (!this.caps.has(cap)) {
      throw diasporeErr(
        "PermissionError",
        `diaspore: capability '${cap}' not granted (run with --allow-${cap})`,
      );
    }
  }

  requireNet(kind: string, host: string, port: number): void {
    this.requireCap("net");
    const pol = this.policy.net;
    if (pol === undefined) return;
    for (const p of pol[kind] ?? []) {
      const idx = p.lastIndexOf(":");
      if (idx <= 0) continue;
      const ph = p.slice(0, idx);
      const pp = p.slice(idx + 1);
      const hostOk = ph === "*" || ph.toLowerCase() === host.toLowerCase();
      const portOk = pp === "*" || pp === String(port);
      if (hostOk && portOk) return;
    }
    throw diasporeErr("PermissionError", `diaspore: net.${kind} ${host}:${port} not permitted by policy`);
  }

  requireFS(kind: string, path: string): string {
    this.requireCap("fs");
    const real = realpathBestEffort(path);
    const pol = this.policy.fs;
    if (pol === undefined) return real;
    for (const root of pol[kind] ?? []) {
      const rroot = realpathBestEffort(root);
      const sep = nodePath.sep;
      if (real === rroot || real.startsWith(rroot.replace(/[/\\]+$/, "") + sep)) {
        return real;
      }
    }
    throw diasporeErr("PermissionError", `diaspore: fs.${kind} ${path} not permitted by policy`);
  }
}

function nameList(names: string[]): string {
  const quoted = names.map((n) => `'${n}'`);
  if (quoted.length === 1) return quoted[0];
  if (quoted.length === 2) return `${quoted[0]} and ${quoted[1]}`;
  return quoted.slice(0, -1).join(", ") + `, and ${quoted[quoted.length - 1]}`;
}

/** os.path.realpath semantics: resolve as far as the path exists. */
function realpathBestEffort(p: string): string {
  try {
    return nodeFs.realpathSync(p);
  } catch {
    const dir = nodePath.dirname(p);
    const base = nodePath.basename(p);
    if (dir === p || base === "") return nodePath.resolve(p);
    return nodePath.join(realpathBestEffort(dir), base);
  }
}

function BASE_EXC_GROUP(): DiasporeClass {
  return EXC_CLASSES.get("BaseExceptionGroup") as DiasporeClass;
}

/** Builtin types that may be subclassed by user classes. */
export const PRIM_BASE_NAMES: Set<string> = new Set([
  "dict", "list", "set", "frozenset", "str", "int", "float", "tuple", "bytes", "bool", "type", "classmethod", "staticmethod", "property", "GenericAlias",
]);
const IMMUTABLE_PRIM: Set<string> = new Set(["str", "bytes", "int", "float", "tuple", "bool", "frozenset", "classmethod", "staticmethod", "property", "GenericAlias"]);

/** Empty primitive for a mutable/immutable builtin base. */
function primEmpty(base: string): Value {
  switch (base) {
    case "dict":
      return new DiasporeDict();
    case "list":
      return new DiasporeList();
    case "set":
      return new DiasporeSet();
    case "frozenset":
      return new DiasporeFrozenSet();
    case "tuple":
      return new DiasporeTuple();
    case "str":
      return "";
    case "bytes":
      return new Uint8Array(0);
    case "int":
      return 0n;
    case "bool":
      return false;
    case "float":
      return 0;
  }
  return NONE;
}

/** property.setter/getter/deleter: returns a new property with `slot` replaced. */
function propWither(p: Property, slot: "fget" | "fset" | "fdel", cls?: DiasporeClass): BuiltinFn {
  return function* (I, args): G<Value> {
    argCount(slot, args, 1, 1);
    const fget = slot === "fget" ? args[0] : p.fget;
    const fset = slot === "fset" ? args[0] : p.fset;
    const fdel = slot === "fdel" ? args[0] : p.fdel;
    const doc = p.docFromGetter ? (fget instanceof DiasporeFunction ? fget.metadata.get("__doc__") ?? NONE : NONE) : p.doc;
    if (cls !== undefined) return yield* I.instantiate(cls, [fget ?? NONE, fset ?? NONE, fdel ?? NONE, p.docFromGetter ? NONE : doc ?? NONE], new Map());
    return new Property(fget, fset, fdel, doc, p.docFromGetter);
  };
}

function propertyAccessor(property: Property, receiver: Value, name: string): Builtin {
  return new Builtin(name, function* (I, args): G<Value> {
    argCount(name, args, name === "__set__" ? 2 : 1, name === "__delete__" ? 1 : 2);
    if (name === "__get__" && args[0] instanceof PyNoneT) return receiver;
    const fn = name === "__get__" ? property.fget : name === "__set__" ? property.fset : property.fdel;
    if (fn === null) throw diasporeErr("AttributeError", "property has no " + (name === "__get__" ? "getter" : name === "__set__" ? "setter" : "deleter"));
    const result = yield* I.invoke(fn, name === "__set__" ? args : [args[0]], new Map());
    return name === "__get__" ? result : NONE;
  });
}

/** Resolve slice.indices(len) to a (start, stop, step) tuple. */
function sliceIndicesMethod(sl: DiasporeSlice): BuiltinFn {
  return function* (_I, args): G<Value> {
    argCount("indices", args, 1, 1);
    const nv = args[0];
    if (typeof nv !== "bigint" && typeof nv !== "boolean") {
      throw diasporeErr("TypeError", "slice indices must be integers");
    }
    const [start, stop, step] = sliceIndices(sl, Number(nv));
    return new DiasporeTuple([BigInt(start), BigInt(stop), BigInt(step)]);
  };
}

/** int/float/bool attribute surface (real/imag/conjugate/bit_length/...). */
function numAttr(o: bigint | number | boolean, n: string): Value {
  const isInt = typeof o === "bigint" || typeof o === "boolean";
  const asNum: Value = typeof o === "boolean" ? (o ? 1n : 0n) : o;
  if (n === "real") return asNum;
  if (n === "imag") return isInt ? 0n : 0;
  if (n === "conjugate") {
    return new Builtin("conjugate", function* (): G<Value> {
      return asNum;
    });
  }
  if (n === "bit_length" && isInt) {
    return new Builtin("bit_length", function* (): G<Value> {
      let x = asNum as bigint;
      if (x < 0n) x = -x;
      return BigInt(x === 0n ? 0 : x.toString(2).length);
    });
  }
  if (n === "is_integer" && !isInt) {
    return new Builtin("is_integer", function* (): G<Value> {
      return Number.isFinite(o as number) && Number.isInteger(o as number);
    });
  }
  if (n === "numerator" && isInt) return asNum;
  if (n === "denominator" && isInt) return 1n;
  throw diasporeErr("AttributeError", `'${typeName(o)}' object has no attribute '${n}'`);
}

/** Primitive-method access for a builtin-subclass instance's underlying value. */
function primMethod(I: Interp, prim: Value, n: string): Value | null {
  const tname = primTypeName(prim);
  if (tname !== null && methodAllowed(tname, n)) {
    return builtinMethod(I, prim, n);
  }
  return null;
}

/** Snapshot a frame-vars Map as a DiasporeDict (for globals()/locals() values). */
function mapAsDict(m: Map<string, Value>): DiasporeDict {
  const d = new DiasporeDict();
  for (const [k, v] of m) if (!k.startsWith(".")) d.set(strFromKey(k), v);
  return d;
}
const dictCopy = mapAsDict;

function dictToMap(d: DiasporeDict): Map<string, Value> {
  const m = new Map<string, Value>();
  for (const [k, v] of d.entries()) {
    if (isPyString(k)) m.set(strKey(k), v);
  }
  return m;
}

function mapToDict(m: Map<string, Value>, d: DiasporeDict): void {
  for (const [k, v] of m) d.set(strFromKey(k), v);
}

// Provided by natives.ts (imported lazily to avoid a hard cycle at init).
import { fileMethodTable } from "./natives.ts";
import { primTypeName } from "./methods.ts";
import { compileSnippet } from "./snippet.ts";

/** Class-body storage preserving a metaclass's prepared mapping protocol. */
class PreparedNamespace extends Map<string, Value> {
  private runtime: Interp;
  private mapping: Value;
  constructor(runtime: Interp, mapping: Value) {
    super();
    this.runtime = runtime;
    this.mapping = mapping;
  }
  override get(key: string): Value | undefined {
    try { return this.runtime.drive(getIndex(this.runtime, this.mapping, key)); }
    catch (error) {
      if (error instanceof DiasporeError && excIsA(error.value, "KeyError")) return undefined;
      throw error;
    }
  }
  override has(key: string): boolean { return this.get(key) !== undefined; }
  override set(key: string, value: Value): this {
    this.runtime.drive(setIndex(this.runtime, this.mapping, key, value));
    return this;
  }
  override delete(key: string): boolean {
    if (!this.has(key)) return false;
    this.runtime.drive(delIndex(this.runtime, this.mapping, key));
    return true;
  }
  override *entries(): MapIterator<[string, Value]> {
    for (const key of this.runtime.drive(iterToArray(this.runtime, this.mapping))) {
      if (!isPyString(key)) throw diasporeErr("TypeError", "class namespace keys must be strings");
      const name = strKey(key);
      yield [name, this.get(name)!];
    }
  }
  override *keys(): MapIterator<string> {
    for (const [key] of this.entries()) yield key;
  }
  override *values(): MapIterator<Value> {
    for (const [, value] of this.entries()) yield value;
  }
  override [Symbol.iterator](): MapIterator<[string, Value]> { return this.entries(); }
  override get size(): number {
    return this.runtime.drive(iterToArray(this.runtime, this.mapping)).length;
  }
}
