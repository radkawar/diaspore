/* Diaspore VM (TypeScript host): core value model, exception hierarchy, hashing.
 *
 * Diaspore values map to these concrete JS types:
 *   int      -> bigint        float -> number      bool -> boolean
 *   str      -> string (code-point semantics)      bytes -> Uint8Array
 *   NoneType -> NONE (PyNoneT singleton)           ...   -> ELLIPSIS
 *   list/tuple/dict/set/range/slice and the callable kinds -> classes below.
 */

import type { Interp, Frame } from "./interp.ts";

/** A yielded sandbox value carried through the interpreter's generator chain. */
export interface YieldMsg {
  yv: Value;
}
/** Execution generator that forwards sandbox yields to its driver. */
export type G<T> = Generator<YieldMsg, T, Value | undefined>;

/** Keyword arguments in call-site order. */
export type KwArgs = Map<string, Value>;
/** Implementation signature for a sandbox builtin. */
export type BuiltinFn = (I: Interp, args: Value[], kw: KwArgs) => G<Value>;

/** Runtime representation of None. */
export class PyNoneT {}
/** The shared None value. */
export const NONE = new PyNoneT();

/** Runtime representation of Ellipsis. */
export class PyEllipsisT {}
/** The shared Ellipsis value. */
export const ELLIPSIS = new PyEllipsisT();

/** Mutable, ordered sandbox sequence. */
export class DiasporeList {
  items: Value[];
  constructor(items: Value[] = []) {
    this.items = items;
  }
}

/** Fixed-length sandbox sequence. */
export class DiasporeTuple {
  items: Value[];
  constructor(items: Value[] = []) {
    this.items = items;
  }
}

/** Integer progression represented by start, stop, and step. */
export class DiasporeRange {
  start: bigint;
  stop: bigint;
  step: bigint;
  constructor(start: bigint, stop: bigint, step: bigint) {
    this.start = start;
    this.stop = stop;
    this.step = step;
  }
  len(): bigint {
    if (this.step > 0n) {
      if (this.stop <= this.start) return 0n;
      return (this.stop - this.start + this.step - 1n) / this.step;
    }
    if (this.stop >= this.start) return 0n;
    return (this.start - this.stop + -this.step - 1n) / -this.step;
  }
}

/** Unevaluated slice bounds and step. */
export class DiasporeSlice {
  lower: Value;
  upper: Value;
  step: Value;
  constructor(lower: Value, upper: Value, step: Value) {
    this.lower = lower;
    this.upper = upper;
    this.step = step;
  }
}

interface DictEntry {
  key: Value;
  val: Value;
}

/** Insertion-ordered dict with Python key unification (1, 1.0, True alike). */
export class DiasporeDict {
  map: Map<string, DictEntry> = new Map();
  get size(): number {
    return this.map.size;
  }
  getHK(hk: string): Value | undefined {
    return this.map.get(hk)?.val;
  }
  setHK(hk: string, key: Value, val: Value): void {
    const e = this.map.get(hk);
    if (e !== undefined) {
      e.val = val;
    } else {
      this.map.set(hk, { key, val });
    }
  }
  get(k: Value): Value | undefined {
    return this.map.get(hashKey(k))?.val;
  }
  set(k: Value, v: Value): void {
    this.setHK(hashKey(k), k, v);
  }
  delete(k: Value): Value | undefined {
    const hk = hashKey(k);
    const e = this.map.get(hk);
    if (e === undefined) return undefined;
    this.map.delete(hk);
    return e.val;
  }
  has(k: Value): boolean {
    return this.map.has(hashKey(k));
  }
  keys(): Value[] {
    return [...this.map.values()].map((e) => e.key);
  }
  values(): Value[] {
    return [...this.map.values()].map((e) => e.val);
  }
  entries(): [Value, Value][] {
    return [...this.map.values()].map((e) => [e.key, e.val]);
  }
  copy(): DiasporeDict {
    const d = new DiasporeDict();
    for (const [hk, e] of this.map) d.map.set(hk, { key: e.key, val: e.val });
    return d;
  }
  clear(): void {
    this.map.clear();
  }
}

/** Insertion-ordered set. */
export class DiasporeSet {
  map: Map<string, Value> = new Map();
  get size(): number {
    return this.map.size;
  }
  add(v: Value): void {
    const hk = hashKey(v);
    if (!this.map.has(hk)) this.map.set(hk, v);
  }
  addHK(hk: string, v: Value): void {
    if (!this.map.has(hk)) this.map.set(hk, v);
  }
  has(v: Value): boolean {
    return this.map.has(hashKey(v));
  }
  discard(v: Value): boolean {
    return this.map.delete(hashKey(v));
  }
  elems(): Value[] {
    return [...this.map.values()];
  }
  copy(): DiasporeSet {
    const s = new DiasporeSet();
    for (const [hk, v] of this.map) s.map.set(hk, v);
    return s;
  }
  clear(): void {
    this.map.clear();
  }
}

let objectBase: DiasporeClass | undefined;

/** Sandbox class namespace and method resolution order. */
export class DiasporeClass {
  name: string;
  bases: DiasporeClass[];
  ns: Map<string, Value>;
  mro: DiasporeClass[];
  /** The BuiltinType this class (transitively) subclasses, else null.
   * A builtin base (dict/list/...) is recorded here and dropped from `bases`;
   * instances carry an underlying primitive that unoverridden ops act on. */
  prim: BuiltinType | null = null;
  constructor(
    name: string,
    bases: (DiasporeClass | BuiltinType)[],
    ns: Map<string, Value>,
    prim: BuiltinType | null = null,
  ) {
    this.name = name;
    this.bases = bases.filter((b): b is DiasporeClass => b instanceof DiasporeClass);
    this.prim = prim;
    if (this.prim === null) {
      for (const b of this.bases) {
        if (b.prim !== null) {
          this.prim = b.prim;
          break;
        }
      }
    }
    for (const b of bases) {
      if (b instanceof BuiltinType) this.prim = b;
    }
    this.ns = ns;
    this.mro = c3mro(this);
    if (objectBase !== undefined && !this.mro.includes(objectBase)) this.mro.push(objectBase);
  }
  lookup(n: string): Value | undefined {
    for (const c of this.mro) {
      const v = c.ns.get(n);
      if (v !== undefined) return v;
    }
    return undefined;
  }
  issub(other: DiasporeClass): boolean {
    return this.mro.includes(other);
  }
}

function c3mro(cls: DiasporeClass): DiasporeClass[] {
  const seqs: DiasporeClass[][] = [[cls]];
  for (const b of cls.bases) seqs.push([...b.mro]);
  seqs.push([...cls.bases]);
  const out: DiasporeClass[] = [];
  let live = seqs.filter((s) => s.length > 0);
  while (live.length > 0) {
    let head: DiasporeClass | null = null;
    for (const seq of live) {
      const cand = seq[0];
      if (!live.some((s) => s.indexOf(cand) > 0)) {
        head = cand;
        break;
      }
    }
    if (head === null) {
      throw new DiasporeError(makeExc("TypeError", "cannot create a consistent MRO"));
    }
    out.push(head);
    for (const seq of live) {
      if (seq[0] === head) seq.shift();
    }
    live = live.filter((s) => s.length > 0);
  }
  return out;
}

/** Sandbox instance with attributes and an optional primitive payload. */
export class DiasporeInstance {
  cls: DiasporeClass;
  d: Map<string, Value> = new Map();
  /** Underlying primitive for a builtin-type subclass instance (else null). */
  prim: Value | null = null;
  constructor(cls: DiasporeClass) {
    this.cls = cls;
  }
}

/** Normalized positional and keyword parameter declarations. */
export interface FuncParams {
  args: string[];
  defaults: unknown[];
  vararg: string | null;
  kwonly: string[];
  kwdefaults: (unknown | null)[];
  kwarg: string | null;
}

/** Sandbox function with its lexical closure and defining module. */
export class DiasporeFunction {
  node: Record<string, unknown>;
  name: string;
  params: FuncParams;
  localset: Set<string>;
  gdecl: Set<string>;
  ndecl: Set<string>;
  gen: boolean;
  closure: Frame | null;
  moduleName: string;
  defclass: DiasporeClass | null = null;
  defaults: Value[] = [];
  kwdefaults: Map<string, Value> = new Map();
  /** User-assigned function attributes (f.x = ...). */
  attrs: Map<string, Value> = new Map();
  constructor(node: Record<string, unknown>, closure: Frame | null, moduleName: string) {
    this.node = node;
    this.name = node.name as string;
    // Compact bundles omit default-valued parameter fields (spec 2.2).
    const p = (node.params ?? {}) as Record<string, unknown>;
    this.params = {
      args: (p.args as string[]) ?? [],
      defaults: (p.defaults as unknown[]) ?? [],
      vararg: (p.vararg as string | null) ?? null,
      kwonly: (p.kwonly as string[]) ?? [],
      kwdefaults: (p.kwdefaults as (unknown | null)[]) ?? [],
      kwarg: (p.kwarg as string | null) ?? null,
    };
    this.localset = new Set((node.locals as string[]) ?? []);
    this.gdecl = new Set((node.gdecl as string[]) ?? []);
    this.ndecl = new Set((node.ndecl as string[]) ?? []);
    this.gen = (node.gen as boolean) ?? false;
    this.closure = closure;
    this.moduleName = moduleName;
  }
}

/** A callable paired with the receiver supplied as its first argument. */
export class BoundMethod {
  fn: Value;
  recv: Value;
  constructor(fn: Value, recv: Value) {
    this.fn = fn;
    this.recv = recv;
  }
}

/** Descriptor that suppresses receiver binding. */
export class StaticMethodV {
  fn: Value;
  constructor(fn: Value) {
    this.fn = fn;
  }
}

/** Descriptor that binds the owning class as the receiver. */
export class ClassMethodV {
  fn: Value;
  constructor(fn: Value) {
    this.fn = fn;
  }
}

/** A super proxy starting lookup after a specific class. */
export class SuperObject {
  cls: DiasporeClass;
  obj: Value;
  constructor(cls: DiasporeClass, obj: Value) {
    this.cls = cls;
    this.obj = obj;
  }
}

/** A sandbox module backed by an execution frame. */
export class DiasporeModule {
  name: string;
  frame: Frame;
  constructor(name: string, frame: Frame) {
    this.name = name;
    this.frame = frame;
  }
}

/** An importable namespace of native values. */
export class NativeModule {
  name: string;
  ns: Map<string, Value>;
  constructor(name: string, ns: Map<string, Value>) {
    this.name = name;
    this.ns = ns;
  }
}

/** A named native callable. */
export class Builtin {
  name: string;
  fn: BuiltinFn;
  constructor(name: string, fn: BuiltinFn) {
    this.name = name;
    this.fn = fn;
  }
}

/** A builtin type name paired with its constructor. */
export class BuiltinType {
  name: string;
  ctor: BuiltinFn;
  constructor(name: string, ctor: BuiltinFn) {
    this.name = name;
    this.ctor = ctor;
  }
}

/** A native object exposing explicit methods and properties. */
export class NativeObject {
  kind: string;
  methods: Map<string, Builtin>;
  props: Map<string, Value>;
  constructor(kind: string, methods: Map<string, Builtin>, props?: Map<string, Value>) {
    this.kind = kind;
    this.methods = methods;
    this.props = props ?? new Map();
  }
}

/** Marker for frame-aware zero-argument super calls. */
export class SuperMarkerT {}
/** Shared marker for the super builtin. */
export const SUPER_MARKER = new SuperMarkerT();

/** property() descriptor. */
export class Property {
  fget: Value | null;
  fset: Value | null;
  fdel: Value | null;
  doc: Value | null;
  constructor(
    fget: Value | null = null,
    fset: Value | null = null,
    fdel: Value | null = null,
    doc: Value | null = null,
  ) {
    this.fget = fget;
    this.fset = fset;
    this.fdel = fdel;
    this.doc = doc;
  }
}

/** A compiled code snippet from compile()/exec()/eval(). */
export class CodeObject {
  nodes: unknown[];
  mode: string;
  filename: string;
  constructor(nodes: unknown[], mode: string, filename: string) {
    this.nodes = nodes;
    this.mode = mode;
    this.filename = filename;
  }
}

/** Complex number value (immutable). */
export class Complex {
  re: number;
  im: number;
  constructor(re: number, im: number) {
    this.re = re;
    this.im = im;
  }
}

/** Base for all iterator values (list_iterator, generator, map, filter, ...). */
export abstract class DiasporeIterBase {
  abstract kindName: string;
  abstract next(): G<[Value, boolean]>;
  /** StopIteration value for yield-from (generators / user __next__). */
  retval: Value = NONE;
  /** Read the StopIteration value once; later reads see None (CPython: only
   * the first exhaustion's StopIteration carries the return value). */
  takeRetval(): Value {
    const v = this.retval;
    this.retval = NONE;
    return v;
  }
}

/** A capability-authorized file descriptor and its access mode. */
export class DiasporeFile {
  fd: number;
  binary: boolean;
  writable: boolean;
  closed = false;
  buf: Uint8Array = new Uint8Array(0);
  constructor(fd: number, binary: boolean, writable: boolean) {
    this.fd = fd;
    this.binary = binary;
    this.writable = writable;
  }
}

/** All values representable inside the sandbox. */
export type Value =
  | bigint
  | number
  | boolean
  | string
  | Uint8Array
  | PyNoneT
  | PyEllipsisT
  | DiasporeList
  | DiasporeTuple
  | DiasporeRange
  | DiasporeSlice
  | DiasporeDict
  | DiasporeSet
  | DiasporeClass
  | DiasporeInstance
  | DiasporeFunction
  | BoundMethod
  | StaticMethodV
  | ClassMethodV
  | SuperObject
  | DiasporeModule
  | NativeModule
  | Builtin
  | BuiltinType
  | NativeObject
  | SuperMarkerT
  | DiasporeIterBase
  | DiasporeFile
  | Property
  | CodeObject
  | Complex;

// --------------------------------------------------------------------------
// host-level signals (never visible to sandboxed code)

/** A sandbox traceback location. */
export interface TbEntry {
  module: string;
  fname: string;
  line: number;
}

/** A catchable sandbox exception and its traceback. */
export class DiasporeError extends Error {
  value: Value;
  tb: TbEntry[];
  constructor(value: Value, tb?: TbEntry[]) {
    super("diaspore exception");
    this.value = value;
    this.tb = tb ?? [];
  }
}

/** An uncatchable execution failure with a process exit code. */
export class DiasporeTrap {
  msg: string;
  code: number;
  constructor(msg: string, code = 3) {
    this.msg = msg;
    this.code = code;
  }
}

/** Internal control signal for break. */
export class BreakSig {}
/** Shared break signal. */
export const BREAK = new BreakSig();
/** Internal control signal for continue. */
export class ContinueSig {}
/** Shared continue signal. */
export const CONTINUE = new ContinueSig();

/** Internal control signal carrying a function result. */
export class ReturnSig {
  value: Value;
  constructor(value: Value) {
    this.value = value;
  }
}

/** flow.suspend() in a durable run: abandon execution mid-flight. */
export class SuspendSig {
  payload: Value;
  constructor(payload: Value) {
    this.payload = payload;
  }
}

// --------------------------------------------------------------------------
// builtin exception hierarchy

const EXC_TREE: [string, string | null][] = [
  ["BaseException", null],
  ["SystemExit", "BaseException"],
  ["KeyboardInterrupt", "BaseException"],
  ["GeneratorExit", "BaseException"],
  ["BaseExceptionGroup", "BaseException"],
  ["Exception", "BaseException"],
  ["ArithmeticError", "Exception"],
  ["ZeroDivisionError", "ArithmeticError"],
  ["OverflowError", "ArithmeticError"],
  ["AssertionError", "Exception"],
  ["AttributeError", "Exception"],
  ["ImportError", "Exception"],
  ["ModuleNotFoundError", "ImportError"],
  ["LookupError", "Exception"],
  ["IndexError", "LookupError"],
  ["KeyError", "LookupError"],
  ["NameError", "Exception"],
  ["UnboundLocalError", "NameError"],
  ["OSError", "Exception"],
  ["PermissionError", "OSError"],
  ["RuntimeError", "Exception"],
  ["NotImplementedError", "RuntimeError"],
  ["RecursionError", "RuntimeError"],
  ["StopIteration", "Exception"],
  ["StopAsyncIteration", "Exception"],
  ["TypeError", "Exception"],
  ["ValueError", "Exception"],
  ["SyntaxError", "Exception"],
  ["IndentationError", "SyntaxError"],
  ["TabError", "IndentationError"],
  ["SystemError", "Exception"],
  ["ReferenceError", "Exception"],
  ["BufferError", "Exception"],
  ["MemoryError", "Exception"],
  ["FloatingPointError", "ArithmeticError"],
  ["UnicodeError", "ValueError"],
  ["UnicodeDecodeError", "UnicodeError"],
  ["UnicodeEncodeError", "UnicodeError"],
  ["BlockingIOError", "OSError"],
  ["ChildProcessError", "OSError"],
  ["ConnectionError", "OSError"],
  ["BrokenPipeError", "ConnectionError"],
  ["ConnectionResetError", "ConnectionError"],
  ["ConnectionAbortedError", "ConnectionError"],
  ["ConnectionRefusedError", "ConnectionError"],
  ["FileExistsError", "OSError"],
  ["FileNotFoundError", "OSError"],
  ["InterruptedError", "OSError"],
  ["IsADirectoryError", "OSError"],
  ["NotADirectoryError", "OSError"],
  ["ProcessLookupError", "OSError"],
  ["TimeoutError", "OSError"],
];

/** The universal base class: every class's MRO ends with it. */
export const OBJECT: DiasporeClass = new DiasporeClass("object", [], new Map());
objectBase = OBJECT;

/** Builtin exception classes indexed by Python name. */
export const EXC_CLASSES: Map<string, DiasporeClass> = (() => {
  const m = new Map<string, DiasporeClass>();
  for (const [name, parent] of EXC_TREE) {
    const bases = parent !== null ? [m.get(parent) as DiasporeClass] : [];
    m.set(name, new DiasporeClass(name, bases, new Map()));
  }
  // ExceptionGroup inherits from both BaseExceptionGroup and Exception (C3)
  m.set(
    "ExceptionGroup",
    new DiasporeClass(
      "ExceptionGroup",
      [m.get("BaseExceptionGroup") as DiasporeClass, m.get("Exception") as DiasporeClass],
      new Map(),
    ),
  );
  (m.get("BaseException") as DiasporeClass).ns.set(
    "__init__",
    new Builtin("__init__", function* (_I, args): G<Value> {
      const self = args[0] as DiasporeInstance;
      self.d.set("args", new DiasporeTuple(args.slice(1)));
      return NONE;
    }),
  );
  (m.get("BaseExceptionGroup") as DiasporeClass).ns.set("__init__", new Builtin("__init__", groupInit));
  return m;
})();

/** BaseExceptionGroup(message, exceptions) init (PEP 654). */
function* groupInit(_I: Interp, args: Value[]): G<Value> {
  const self = args[0] as DiasporeInstance;
  const rest = args.slice(1);
  if (rest.length !== 2) {
    throw diasporeErr("TypeError", "BaseExceptionGroup() takes exactly 2 arguments");
  }
  const [message, excs] = rest;
  if (typeof message !== "string") {
    throw diasporeErr("TypeError", "argument 1 must be str, not " + typeName(message));
  }
  let items: Value[];
  if (excs instanceof DiasporeList || excs instanceof DiasporeTuple) {
    items = [...excs.items];
  } else {
    throw diasporeErr("TypeError", "second argument (exceptions) must be a sequence");
  }
  if (items.length === 0) {
    throw diasporeErr("ValueError", "second argument (exceptions) must be a non-empty sequence");
  }
  for (const e of items) {
    if (!(e instanceof DiasporeInstance && e.cls.issub(m_baseExc()))) {
      throw diasporeErr("ValueError", "second argument (exceptions) must be a sequence of exceptions");
    }
  }
  self.d.set("args", new DiasporeTuple([message, new DiasporeList(items)]));
  self.d.set("message", message);
  self.d.set("exceptions", new DiasporeTuple(items));
  return NONE;
}

function m_baseExc(): DiasporeClass {
  return EXC_CLASSES.get("BaseException") as DiasporeClass;
}

/** Build an (Base)ExceptionGroup instance directly (used by except* split). */
export function makeGroup(message: string, excs: Value[], base = false): DiasporeInstance {
  const exceptionCls = EXC_CLASSES.get("Exception") as DiasporeClass;
  const anyBare = excs.some((e) => !(e instanceof DiasporeInstance && e.cls.issub(exceptionCls)));
  const name = base && anyBare ? "BaseExceptionGroup" : "ExceptionGroup";
  const inst = new DiasporeInstance(EXC_CLASSES.get(name) as DiasporeClass);
  inst.d.set("args", new DiasporeTuple([message, new DiasporeList([...excs])]));
  inst.d.set("message", message);
  inst.d.set("exceptions", new DiasporeTuple([...excs]));
  return inst;
}

/** Root of the sandbox exception hierarchy. */
export const BASE_EXC = EXC_CLASSES.get("BaseException") as DiasporeClass;

/** Construct a builtin exception instance with positional arguments. */
export function makeExc(name: string, ...args: Value[]): DiasporeInstance {
  const cls = EXC_CLASSES.get(name) ?? (EXC_CLASSES.get("RuntimeError") as DiasporeClass);
  const inst = new DiasporeInstance(cls);
  inst.d.set("args", new DiasporeTuple(args));
  return inst;
}

/** Construct a catchable sandbox error with a message. */
export function diasporeErr(name: string, msg: string): DiasporeError {
  return new DiasporeError(makeExc(name, msg));
}

/** Read the positional arguments stored on an exception instance. */
export function excArgs(inst: DiasporeInstance): Value[] {
  const a = inst.d.get("args");
  return a instanceof DiasporeTuple ? a.items : [];
}

/** Test whether a value belongs to a named exception class. */
export function excIsA(v: Value, name: string): boolean {
  const cls = EXC_CLASSES.get(name);
  return v instanceof DiasporeInstance && cls !== undefined && v.cls.issub(cls);
}

// --------------------------------------------------------------------------
// naming

/** Return the Python-facing type name of a sandbox value. */
export function typeName(v: Value): string {
  switch (typeof v) {
    case "bigint":
      return "int";
    case "number":
      return "float";
    case "boolean":
      return "bool";
    case "string":
      return "str";
  }
  if (v instanceof Uint8Array) return "bytes";
  if (v instanceof PyNoneT) return "NoneType";
  if (v instanceof PyEllipsisT) return "ellipsis";
  if (v instanceof DiasporeList) return "list";
  if (v instanceof DiasporeTuple) return "tuple";
  if (v instanceof DiasporeDict) return "dict";
  if (v instanceof DiasporeSet) return "set";
  if (v instanceof DiasporeRange) return "range";
  if (v instanceof DiasporeSlice) return "slice";
  if (v instanceof DiasporeInstance) return v.cls.name;
  if (v instanceof DiasporeClass || v instanceof BuiltinType) return "type";
  if (v instanceof DiasporeFunction) return "function";
  if (v instanceof BoundMethod) return "method";
  if (v instanceof Builtin) return "builtin_function_or_method";
  if (v instanceof DiasporeModule || v instanceof NativeModule) return "module";
  if (v instanceof DiasporeFile) return "file";
  if (v instanceof NativeObject) return v.kind;
  if (v instanceof StaticMethodV) return "staticmethod";
  if (v instanceof ClassMethodV) return "classmethod";
  if (v instanceof SuperObject || v instanceof SuperMarkerT) return "super";
  if (v instanceof Property) return "property";
  if (v instanceof CodeObject) return "code";
  if (v instanceof Complex) return "complex";
  if (v instanceof DiasporeIterBase) return v.kindName;
  return "object";
}

// --------------------------------------------------------------------------
// numeric helpers

/** int-kind (int or bool) to bigint; null if not int-kind. */
export function asIndex(v: Value): bigint | null {
  if (typeof v === "bigint") return v;
  if (typeof v === "boolean") return v ? 1n : 0n;
  return null;
}

/** Test whether a value is an integer or boolean. */
export function isIntKind(v: Value): boolean {
  return typeof v === "bigint" || typeof v === "boolean";
}

/** Convert a real numeric value to a host number, or return null. */
export function asFloat(v: Value): number | null {
  if (typeof v === "number") return v;
  if (typeof v === "bigint") return Number(v);
  if (typeof v === "boolean") return v ? 1 : 0;
  return null;
}

/** Test whether a value is a real numeric type. */
export function isNumber(v: Value): boolean {
  return typeof v === "bigint" || typeof v === "number" || typeof v === "boolean";
}

/** Truncate a finite float toward zero as an exact diaspore int. */
export function intFromFloat(f: number): bigint {
  return BigInt(Math.trunc(f));
}

/** Compare bigint and float exactly; the float must not be NaN. */
export function bigFloatCmp(z: bigint, f: number): number {
  if (f === Infinity) return -1;
  if (f === -Infinity) return 1;
  const fl = Math.floor(f);
  const fb = BigInt(fl);
  if (z < fb) return -1;
  if (z > fb) return 1;
  return f > fl ? -1 : 0;
}

// --------------------------------------------------------------------------
// identity & hashing

let objIdCounter = 1;
const objIds = new WeakMap<object, number>();

/** Assign a stable identity to a host object for sandbox hashing. */
export function objId(v: object): number {
  let id = objIds.get(v);
  if (id === undefined) {
    id = objIdCounter++;
    objIds.set(v, id);
  }
  return id;
}

/** Encode bytes as a one-code-unit-per-byte string. */
export function bytesToBinStr(b: Uint8Array): string {
  let out = "";
  for (let i = 0; i < b.length; i++) out += String.fromCharCode(b[i]);
  return out;
}

const f64buf = new DataView(new ArrayBuffer(8));

/**
 * Unique comparable encoding of a hashable value with Python numeric key
 * unification. Throws a diaspore TypeError for unhashable values.
 */
export function hashKey(v: Value): string {
  switch (typeof v) {
    case "bigint":
      return "i" + v.toString();
    case "boolean":
      return v ? "i1" : "i0";
    case "number":
      if (Number.isNaN(v)) return "fnan";
      if (Number.isFinite(v) && Number.isInteger(v)) return "i" + BigInt(v).toString();
      f64buf.setFloat64(0, v);
      return "f" + f64buf.getBigUint64(0).toString(16);
    case "string":
      return "s" + v;
  }
  if (v instanceof Uint8Array) return "b" + bytesToBinStr(v);
  if (v instanceof PyNoneT) return "N";
  if (v instanceof PyEllipsisT) return "E";
  if (v instanceof Complex) {
    // im==0 hashes like the equal real number (hash(1+0j) == hash(1)).
    if (v.im === 0) return hashKey(v.re);
    f64buf.setFloat64(0, v.re);
    const rk = f64buf.getBigUint64(0).toString(16);
    f64buf.setFloat64(0, v.im);
    return "c" + rk + "," + f64buf.getBigUint64(0).toString(16);
  }
  if (v instanceof DiasporeSlice) {
    return "L" + hashKey(v.lower) + "|" + hashKey(v.upper) + "|" + hashKey(v.step);
  }
  if (v instanceof DiasporeTuple) {
    let out = "t";
    for (const x of v.items) {
      const k = hashKey(x);
      out += k.length + ":" + k;
    }
    return out;
  }
  if (v instanceof DiasporeRange) return `r${v.start},${v.stop},${v.step}`;
  if (
    v instanceof DiasporeInstance ||
    v instanceof DiasporeClass ||
    v instanceof BuiltinType ||
    v instanceof DiasporeFunction ||
    v instanceof BoundMethod ||
    v instanceof Builtin ||
    v instanceof DiasporeModule ||
    v instanceof NativeModule ||
    v instanceof DiasporeIterBase ||
    v instanceof StaticMethodV ||
    v instanceof ClassMethodV ||
    v instanceof NativeObject
  ) {
    return "p" + objId(v);
  }
  throw diasporeErr("TypeError", `unhashable type: '${typeName(v)}'`);
}

// --------------------------------------------------------------------------
// string code-point helpers

let charsKey = "";
let charsVal: string[] | null = null;

/** Code points of s as an array (cached for the most recent string). */
export function chars(s: string): string[] {
  if (s === charsKey && charsVal !== null) return charsVal;
  const a = Array.from(s);
  charsKey = s;
  charsVal = a;
  return a;
}

const ASTRAL_RE = /[\uD800-\uDFFF]/;

/** Test whether a string contains code points outside the BMP. */
export function hasAstral(s: string): boolean {
  return ASTRAL_RE.test(s);
}

/** Count Unicode code points in a string. */
export function strLen(s: string): number {
  return hasAstral(s) ? chars(s).length : s.length;
}

/** Python-style string comparison: by code points. */
export function strCmp(a: string, b: string): number {
  if (!hasAstral(a) && !hasAstral(b)) {
    return a < b ? -1 : a > b ? 1 : 0;
  }
  const ca = chars(a);
  const cb = Array.from(b);
  const n = Math.min(ca.length, cb.length);
  for (let i = 0; i < n; i++) {
    const x = ca[i].codePointAt(0) as number;
    const y = cb[i].codePointAt(0) as number;
    if (x !== y) return x < y ? -1 : 1;
  }
  return ca.length - cb.length;
}

/** Compare byte sequences for equality. */
export function bytesEq(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** Compare byte sequences lexicographically. */
export function bytesCmp(a: Uint8Array, b: Uint8Array): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  }
  return a.length - b.length;
}

/** Concatenate two byte sequences into a new buffer. */
export function bytesConcat(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

/** Find a byte subsequence at or after a starting index. */
export function bytesIndexOf(hay: Uint8Array, needle: Uint8Array, from = 0): number {
  if (needle.length === 0) return from <= hay.length ? from : -1;
  outer: for (let i = from; i + needle.length <= hay.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (hay[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}
