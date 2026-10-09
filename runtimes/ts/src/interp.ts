/* The Diaspore interpreter: frames, execution, calls, attributes, modules,
 * capabilities, fuel, and durable-flow journaling. Mirrors src/diaspore/vm.py. */

import * as nodeFs from "node:fs";
import * as nodePath from "node:path";

import {
  type Value,
  type G,
  type YieldMsg,
  type KwArgs,
  NONE,
  ELLIPSIS,
  PyNoneT,
  DiasporeList,
  DiasporeTuple,
  DiasporeDict,
  DiasporeSet,
  DiasporeSlice,
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
import { getIter, iterToArray, DiasporeGenerator } from "./iter.ts";
import { callableName, sliceIndices } from "./ops.ts";
import { builtinMethod, methodAllowed } from "./methods.ts";
import { argCount } from "./helpers.ts";
import { formatValue } from "./format.ts";
import { makeBuiltins } from "./builtins.ts";
import { makeNativeModules } from "./natives.ts";
import { fromHost, toHost } from "./host.ts";

type Node = Record<string, any>;

function has(n: Node, k: string): boolean {
  return n[k] !== undefined && n[k] !== null;
}

/** Execution scope with lexical parents and module globals. */
export class Frame {
  vars: Map<string, Value>;
  parent: Frame | null;
  localset: Set<string> | null; // null => non-strict scope (module/class)
  gdecl: Set<string>;
  ndecl: Set<string>;
  isClass: boolean;
  globals: Map<string, Value>;
  isGen = false;
  line = 0;
  fname: string;
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
}

const EMPTY_SET: Set<string> = new Set();

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
  steps = 0;
  depth = 0;
  stdoutW: (s: string) => void;
  stderrW: (s: string) => void;
  modules: Map<string, Value> = new Map();
  excStack: DiasporeInstance[] = [];
  repring: Set<Value> = new Set();
  builtins: Map<string, Value>;
  types: Map<string, BuiltinType> = new Map();
  natives: Map<string, NativeModule>;
  bindings: Map<string, NativeModule> = new Map();

  constructor(bundle: Node, opts: InterpOpts = {}) {
    if (bundle.diaspore !== 1 || typeof bundle.main !== "string" || bundle.modules == null) {
      throw new Error("unsupported Diaspore bundle");
    }
    this.bundle = bundle;
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
      typeof v === "string" ||
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
        if (typeof key !== "string") {
          throw diasporeErr("TypeError", `durable ${what} results must have str keys`);
        }
        out.setHK("s" + key, key, this.journalValue(val, what));
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
        this.printTraceback(e);
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
    let b = "Traceback (most recent call last):\n";
    const mods = this.bundle.modules as Record<string, Node>;
    for (let i = e.tb.length - 1; i >= 0; i--) {
      const t = e.tb[i];
      const m = mods[t.module] ?? {};
      const file = (m.file as string) ?? t.module;
      b += `  File "${file}", line ${t.line}, in ${t.fname}\n`;
      const src = m.src as string[] | undefined;
      if (src !== undefined && t.line >= 1 && t.line <= src.length) {
        b += `    ${src[t.line - 1].trim()}\n`;
      }
    }
    const v = e.value;
    const name = v instanceof DiasporeInstance ? v.cls.name : typeName(v);
    const msg =
      v instanceof DiasporeInstance
        ? this.drive(excStr(this, v))
        : this.drive(strOf(this, v));
    b += msg !== "" ? `${name}: ${msg}\n` : `${name}\n`;
    this.stderrW(b);
  }

  // ------------------------------------------------------------- modules

  *importModule(name: string): G<Value> {
    const cached = this.modules.get(name);
    if (cached !== undefined) return cached;
    const mods = this.bundle.modules as Record<string, Node>;
    const m = mods[name];
    if (m !== undefined) {
      let parent: Value | null = null;
      const dot = name.lastIndexOf(".");
      if (dot >= 0) {
        // packages: ancestors execute first (each exactly once)
        parent = yield* this.importModule(name.slice(0, dot));
      }
      const fr = new Frame(new Map(), null, null, EMPTY_SET, EMPTY_SET, false, null, "<module>", name);
      fr.vars.set("__name__", name === this.main ? "__main__" : name);
      const mod = new DiasporeModule(name, fr);
      this.modules.set(name, mod);
      try {
        yield* this.execBlock(m.body as unknown[], fr);
      } catch (e) {
        if (e instanceof DiasporeError) e.tb.push({ module: name, fname: "<module>", line: fr.line });
        throw e;
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
      while (f !== null) {
        const v = f.vars.get(id);
        if (v !== undefined) return v;
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
      yield* this.execStmt(s, fr);
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
          let v: Value;
          try {
            v = yield* this.getAttr(mod, spec.n);
          } catch (e) {
            if (e instanceof DiasporeError) {
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
    while (closure !== null && closure.isClass) closure = closure.parent;
    const fn = new DiasporeFunction(node, closure, fr.moduleName);
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
    const bases: (DiasporeClass | BuiltinType)[] = [];
    for (const b of (s.bases ?? []) as Node[]) {
      if (b.op === "starred") {
        for (const bv of yield* iterToArray(this, yield* this.ev(b.v, fr))) {
          bases.push(yield* this.checkBase(bv));
        }
      } else {
        bases.push(yield* this.checkBase(yield* this.ev(b, fr)));
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
          if (typeof k !== "string") throw diasporeErr("TypeError", "keywords must be strings");
          merged.set(k, v);
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
    const cf = new Frame(
      new Map(),
      fr,
      null,
      EMPTY_SET,
      EMPTY_SET,
      true,
      fr.globals,
      s.name,
      fr.moduleName,
    );
    yield* this.execBlock(s.body, cf);
    const cls = yield* this.buildClass(s.name, bases, cf.vars, metaclass, merged);
    this.storeName(fr, s.name, cls);
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
  ): G<DiasporeClass> {
    const make = (): DiasporeClass => {
      const made = new DiasporeClass(name, [...bases], new Map(ns));
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
      metaclass === null ||
      (metaclass instanceof BuiltinType && metaclass.name === "type") ||
      (metaclass instanceof DiasporeClass &&
        metaclass.lookup("__new__") === undefined &&
        metaclass.lookup("__call__") === undefined &&
        metaclass.lookup("__init__") === undefined);
    if (!plainMeta) {
      const nsDict = new DiasporeDict();
      for (const [k, v] of ns) nsDict.setHK("s" + k, k, v);
      const result = yield* this.invoke(metaclass as Value,
      [name, new DiasporeTuple([...bases]), nsDict],
      new Map(kwargs),);
      if (!(result instanceof DiasporeClass)) {
        throw diasporeErr("TypeError", `metaclass returned ${typeName(result)}, not a class`);
      }
      return result;
    }
    const cls = make();
    if (kwargs.size > 0) {
      for (const base of bases) {
        if (!(base instanceof DiasporeClass)) continue;
        const initSub = base.lookup("__init_subclass__");
        if (initSub !== undefined) {
          const fn = initSub instanceof ClassMethodV ? initSub.fn : initSub;
          yield* this.invoke(new BoundMethod(fn, cls), [], new Map(kwargs));
          return cls;
        }
      }
      throw diasporeErr("TypeError", `${name}.__init_subclass__() takes no keyword arguments`);
    }
    return cls;
  }

  *exRaise(s: Node, fr: Frame): G<void> {
    if (!has(s, "exc")) {
      if (this.excStack.length === 0) {
        throw diasporeErr("RuntimeError", "No active exception to re-raise");
      }
      throw new DiasporeError(this.excStack[this.excStack.length - 1]);
    }
    let v = yield* this.ev(s.exc, fr);
    if (has(s, "cause")) yield* this.ev(s.cause, fr);
    if (v instanceof DiasporeClass) {
      if (!v.issub(BASE_EXC)) {
        throw diasporeErr("TypeError", "exceptions must derive from BaseException");
      }
      v = yield* this.instantiate(v, [], new Map());
    }
    if (!(v instanceof DiasporeInstance && v.cls.issub(BASE_EXC))) {
      throw diasporeErr("TypeError", "exceptions must derive from BaseException");
    }
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
      const membersV = exc.d.get("exceptions");
      const members = membersV instanceof DiasporeTuple ? membersV.items : [];
      const matched = members.filter(matches);
      const unmatched = members.filter((e) => !matches(e));
      if (matched.length === 0) return [null, exc];
      const msgV = exc.d.get("message");
      const msg = typeof msgV === "string" ? msgV : "";
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
        const o = yield* this.ev(t.o, fr);
        if (o instanceof DiasporeInstance && o.d.has(t.n)) {
          o.d.delete(t.n);
        } else {
          throw diasporeErr(
            "AttributeError",
            `'${typeName(o)}' object has no attribute '${t.n}'`,
          );
        }
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
          if (!(yield* compare(this, ops[i], left, right))) return false;
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
            for (const [hk, en] of v.map) out.setHK(hk, en.key, en.val);
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
        let out = "";
        for (const p of parts) {
          if (typeof p.s === "string") {
            out += p.s;
            continue;
          }
          let v = yield* this.ev(p.v, fr);
          const conv = p.conv as string | null | undefined;
          if (conv === "r") v = yield* this.repr(v);
          else if (conv === "s") v = yield* this.str(v);
          else if (conv === "a") v = yield* asciiOf(this, v);
          const spec = has(p, "spec") ? ((yield* this.ev(p.spec, fr)) as string) : "";
          out += yield* formatValue(this, v, spec);
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
        return e.v as string;
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
        return mapAsDict(fr.globals);
      }
      if (id === "locals" && !this.shadowed(fr).has("locals")) {
        return fr.localset === null ? mapAsDict(fr.vars) : dictCopy(fr.vars);
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
          if (typeof k !== "string") throw diasporeErr("TypeError", "keywords must be strings");
          kwargs.set(k, v);
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
    if (f instanceof DiasporeFunction) return yield* this.callFunction(f, args, kwargs);
    if (f instanceof BoundMethod) return yield* this.invoke(f.fn, [f.recv, ...args], kwargs);
    if (f instanceof DiasporeClass) return yield* this.instantiate(f, args, kwargs);
    if (f instanceof BuiltinType) return yield* f.ctor(this, args, kwargs);
    if (f instanceof Builtin) return yield* f.fn(this, args, kwargs);
    if (f instanceof DiasporeInstance) {
      if (f.cls.lookup("__call__") !== undefined) {
        return yield* this.callDunderKw(f, "__call__", args, kwargs);
      }
      throw diasporeErr("TypeError", `'${f.cls.name}' object is not callable`);
    }
    if (f instanceof SuperMarkerT) {
      throw diasporeErr("RuntimeError", "super(): no arguments and no enclosing method");
    }
    throw diasporeErr("TypeError", `'${typeName(f)}' object is not callable`);
  }

  // ------------------------------------------------------------- exec/eval

  compileSnippet(source: Value, filename: string, mode: string): Value {
    if (source instanceof CodeObject) return source;
    if (typeof source !== "string") {
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
    fr.defclass = fn.defclass;
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
      if (names.includes(k)) {
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
      for (const [k, v] of extra) d.setHK("s" + k, k, v);
      out.set(p.kwarg, d);
    }
    return out;
  }

  *instantiate(cls: DiasporeClass, args: Value[], kwargs: KwArgs): G<Value> {
    const inst = new DiasporeInstance(cls);
    if (cls.prim !== null) {
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
    const init = cls.lookup("__init__");
    if (init === undefined) {
      if ((args.length > 0 || kwargs.size > 0) && cls.prim === null) {
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
    if (v instanceof DiasporeFunction) return new BoundMethod(v, inst);
    if (v instanceof StaticMethodV) return v.fn;
    if (v instanceof ClassMethodV) return new BoundMethod(v.fn, cls);
    if (v instanceof Builtin) return new BoundMethod(v, inst);
    return v;
  }

  *getAttr(o: Value, n: string): G<Value> {
    if (o instanceof DiasporeInstance) {
      if (n === "__class__") return o.cls;
      const dv = o.d.get(n);
      if (dv !== undefined) return dv;
      if ((n === "exceptions" || n === "message") && o.cls.issub(BASE_EXC_GROUP())) {
        return o.d.get(n) ?? NONE;
      }
      const cv = o.cls.lookup(n);
      if (cv instanceof Property) {
        if (cv.fget === null) {
          throw diasporeErr(
            "AttributeError",
            `property '${n}' of '${o.cls.name}' object has no getter`,
          );
        }
        return yield* this.invoke(this.bindAttr(cv.fget, o, o.cls), [], new Map());
      }
      if (cv !== undefined) return this.bindAttr(cv, o, o.cls);
      if (o.prim !== null) {
        const pm = primMethod(this, o.prim, n);
        if (pm !== null) return pm;
      }
      const ga = o.cls.lookup("__getattr__");
      if (ga !== undefined) {
        return yield* this.invoke(this.bindAttr(ga, o, o.cls), [n], new Map());
      }
      throw diasporeErr("AttributeError", `'${o.cls.name}' object has no attribute '${n}'`);
    }
    if (o instanceof BuiltinType) {
      if (n === "__name__") return o.name;
      const unbound = this.builtinUnbound(o.name, n);
      if (unbound !== null) return unbound;
      throw diasporeErr("AttributeError", `type object '${o.name}' has no attribute '${n}'`);
    }
    if (o instanceof DiasporeClass) {
      const v = o.lookup(n);
      if (v === undefined) {
        if (n === "__name__" || n === "__qualname__") return o.name;
        if (n === "__mro__") return new DiasporeTuple([...o.mro]);
        if (n === "__bases__") {
          return new DiasporeTuple(o.bases.length > 0 ? [...o.bases] : [OBJECT]);
        }
        if (n === "__dict__") {
          const d = new DiasporeDict();
          for (const [k, val] of o.ns) d.setHK("s" + k, k, val);
          return d;
        }
        throw diasporeErr("AttributeError", `type object '${o.name}' has no attribute '${n}'`);
      }
      if (v instanceof ClassMethodV) return new BoundMethod(v.fn, o);
      if (v instanceof StaticMethodV) return v.fn;
      return v;
    }
    if (o instanceof SuperObject) {
      const mro = o.obj instanceof DiasporeInstance ? o.obj.cls.mro : (o.obj as DiasporeClass).mro;
      let start = mro.indexOf(o.cls);
      start = start >= 0 ? start + 1 : 0;
      for (let i = start; i < mro.length; i++) {
        const v = mro[i].ns.get(n);
        if (v !== undefined) return this.bindAttr(v, o.obj, mro[i]);
      }
      throw diasporeErr("AttributeError", `'super' object has no attribute '${n}'`);
    }
    if (o instanceof DiasporeModule) {
      const v = o.frame.vars.get(n);
      if (v !== undefined) return v;
      throw diasporeErr("AttributeError", `module '${o.name}' has no attribute '${n}'`);
    }
    if (o instanceof NativeModule) {
      const v = o.ns.get(n);
      if (v !== undefined) return v;
      throw diasporeErr("AttributeError", `module '${o.name}' has no attribute '${n}'`);
    }
    if (o instanceof DiasporeFile) {
      return this.fileMethod(o, n);
    }
    if (o instanceof Property) {
      if (n === "setter") return new Builtin("setter", propWither(o, "fset"));
      if (n === "getter") return new Builtin("getter", propWither(o, "fget"));
      if (n === "deleter") return new Builtin("deleter", propWither(o, "fdel"));
      if (n === "fget") return o.fget ?? NONE;
      if (n === "fset") return o.fset ?? NONE;
      if (n === "fdel") return o.fdel ?? NONE;
      throw diasporeErr("AttributeError", `'property' object has no attribute '${n}'`);
    }
    if (o instanceof DiasporeFunction) {
      const av = o.attrs.get(n);
      if (av !== undefined) return av;
      if (n === "__name__" || n === "__qualname__") return o.name;
      if (n === "__doc__") return o.attrs.get("__doc__") ?? NONE;
      if (n === "__dict__") {
        const d = new DiasporeDict();
        for (const [k, val] of o.attrs) d.setHK("s" + k, k, val);
        return d;
      }
      throw diasporeErr("AttributeError", `'function' object has no attribute '${n}'`);
    }
    if (o instanceof BoundMethod) {
      if (n === "__self__") return o.recv;
      if (n === "__func__") return o.fn;
      if (n === "__name__") return callableName(o.fn);
      return yield* this.getAttr(o.fn, n);
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
    if (
      (typeof o === "bigint" || typeof o === "number" || typeof o === "boolean") &&
      ["real", "imag", "conjugate", "bit_length", "numerator", "denominator", "is_integer"].includes(n)
    ) {
      return numAttr(o, n);
    }
    if (o instanceof DiasporeGenerator) {
      return this.genMethod(o, n);
    }
    if (o instanceof NativeObject) {
      if (!n.startsWith(".") && !n.startsWith("__")) {
        const pv = o.props.get(n);
        if (pv !== undefined) return pv;
        const mv = o.methods.get(n);
        if (mv !== undefined) return mv;
      }
      throw diasporeErr("AttributeError", `'${o.kind}' object has no attribute '${n}'`);
    }
    return builtinMethod(this, o, n);
  }

  /** `dict.__getitem__`, `list.append`, ... as unbound functions operating on
   * the RAW primitive of a builtin-subclass instance (bypassing overrides). */
  builtinUnbound(tname: string, method: string): Value | null {
    const self = this;
    const raw = (o: Value): Value => (o instanceof DiasporeInstance && o.prim !== null ? o.prim : o);
    const dunders: Record<string, BuiltinFn> = {
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
      return new Builtin(method, function* (I2, args): G<Value> {
        const fn = builtinMethod(self, raw(args[0]), method) as Builtin;
        return yield* fn.fn(I2, args.slice(1), new Map());
      });
    }
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
    if (o instanceof DiasporeFunction) {
      if (n === "__name__") {
        if (typeof v !== "string") throw diasporeErr("TypeError", "__name__ must be a string");
        o.name = v;
      } else {
        o.attrs.set(n, v);
      }
      return;
    }
    if (o instanceof DiasporeInstance) {
      const prop = o.cls.lookup(n);
      if (prop instanceof Property) {
        if (prop.fset === null) {
          throw diasporeErr(
            "AttributeError",
            `property '${n}' of '${o.cls.name}' object has no setter`,
          );
        }
        yield* this.invoke(this.bindAttr(prop.fset, o, o.cls), [v], new Map());
        return;
      }
      o.d.set(n, v);
      return;
    }
    if (o instanceof DiasporeClass) {
      o.ns.set(n, v);
      return;
    }
    if (o instanceof DiasporeModule) {
      o.frame.vars.set(n, v);
      return;
    }
    throw diasporeErr("AttributeError", `'${typeName(o)}' object has no attribute '${n}'`);
  }

  // ------------------------------------------------------------- protocols

  *truth(v: Value): G<boolean> {
    return yield* truth(this, v);
  }

  *diasporeLen(v: Value): G<bigint> {
    return yield* diasporeLen(this, v);
  }

  *str(v: Value): G<string> {
    return yield* strOf(this, v);
  }

  *repr(v: Value): G<string> {
    return yield* reprOf(this, v);
  }

  *excStrOf(v: DiasporeInstance): G<string> {
    return yield* excStr(this, v);
  }

  *binop(kind: string, l: Value, r: Value): G<Value> {
    return yield* binop(this, kind, l, r);
  }

  *compare(kind: string, l: Value, r: Value): G<boolean> {
    return yield* compare(this, kind, l, r);
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

  *formatValue(v: Value, spec: string): G<string> {
    return yield* formatValue(this, v, spec);
  }

  // ------------------------------------------------------------- output

  write(s: string): void {
    this.stdoutW(s);
  }

  ewrite(s: string): void {
    this.stderrW(s);
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
  "dict", "list", "set", "str", "int", "float", "tuple", "bytes", "bool",
]);
const IMMUTABLE_PRIM: Set<string> = new Set(["str", "bytes", "int", "float", "tuple", "bool"]);

/** Empty primitive for a mutable/immutable builtin base. */
function primEmpty(base: string): Value {
  switch (base) {
    case "dict":
      return new DiasporeDict();
    case "list":
      return new DiasporeList();
    case "set":
      return new DiasporeSet();
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
function propWither(p: Property, slot: "fget" | "fset" | "fdel"): BuiltinFn {
  return function* (_I, args): G<Value> {
    const f = args[0];
    return new Property(
      slot === "fget" ? f : p.fget,
      slot === "fset" ? f : p.fset,
      slot === "fdel" ? f : p.fdel,
      p.doc,
    );
  };
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
  for (const [k, v] of m) d.setHK("s" + k, k, v);
  return d;
}
const dictCopy = mapAsDict;

function dictToMap(d: DiasporeDict): Map<string, Value> {
  const m = new Map<string, Value>();
  for (const [k, v] of d.entries()) {
    if (typeof k === "string") m.set(k, v);
  }
  return m;
}

function mapToDict(m: Map<string, Value>, d: DiasporeDict): void {
  for (const [k, v] of m) d.setHK("s" + k, k, v);
}

// Provided by natives.ts (imported lazily to avoid a hard cycle at init).
import { fileMethodTable } from "./natives.ts";
import { primTypeName } from "./methods.ts";
import { compileSnippet } from "./snippet.ts";
