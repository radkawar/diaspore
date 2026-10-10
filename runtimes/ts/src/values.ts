/* Diaspore VM (TypeScript host): core value model, exception hierarchy, hashing.
 *
 * Diaspore values map to these concrete JS types:
 *   int      -> bigint        float -> number      bool -> boolean
 *   str      -> string | PyStr (code-point semantics) bytes -> Uint8Array
 *   NoneType -> NONE (PyNoneT singleton)           ...   -> ELLIPSIS
 *   list/tuple/dict/set/range/slice and the callable kinds -> classes below.
 */

import type { Interp, Frame } from "./interp.ts";
import { selectOSErrorClass, initializeOSError } from "./os-error-values.ts";

/** Python text whose explicit surrogate boundaries cannot fit in a JS string. */
export class PyStr {
  readonly points: readonly string[];

  constructor(points: Iterable<string>) {
    this.points = Object.freeze(Array.from(points));
  }

  *[Symbol.iterator](): IterableIterator<string> {
    yield* this.points;
  }
}

/** Ordinary strings stay unboxed; PyStr preserves ambiguous UTF-16 boundaries. */
export type PyString = string | PyStr;

/** Test whether a value is Python text. */
export function isPyString(value: unknown): value is PyString {
  return typeof value === "string" || value instanceof PyStr;
}

/** Convert Python text to host UTF-16, losing explicit surrogate boundaries. */
export function strText(value: PyString): string {
  return typeof value === "string" ? value : value.points.join("");
}

/** Encode Python text as a reversible host-map key without surrogate collisions. */
export function strKey(value: PyString): string {
  if (typeof value === "string") return value.startsWith("\0") ? "\0" + value : value;
  return "\0p" + value.points.map(point => point.codePointAt(0)!.toString(16)).join(",");
}

/** Recover a Python name from a key produced by strKey. */
export function strFromKey(key: string): PyString {
  if (key.startsWith("\0\0")) return key.slice(1);
  if (!key.startsWith("\0p")) return key;
  return strFromChars(key.slice(2).split(",").map(point => String.fromCodePoint(parseInt(point, 16))));
}

/** Construct text from individual Python code points without merging surrogates. */
export function strFromChars(points: Iterable<string>): PyString {
  const items = Array.isArray(points) ? points : Array.from(points);
  let previousHigh = false;
  for (const point of items) {
    const code = point.charCodeAt(0);
    if (previousHigh && point.length === 1 && code >= 0xdc00 && code <= 0xdfff) {
      return new PyStr(items);
    }
    previousHigh = point.length === 1 && code >= 0xd800 && code <= 0xdbff;
  }
  return items.join("");
}

/** Concatenate Python strings without fusing adjacent surrogate code points. */
export function strConcat(...parts: PyString[]): PyString {
  return strJoin(parts);
}

/** Join Python text while preserving each input's code-point boundaries. */
export function strJoin(parts: Iterable<PyString>, separator: PyString = ""): PyString {
  let result = "";
  let points: string[] | null = null;
  let first = true;
  // Inspect the new chunk, not the growing rope: indexing the rope flattens it.
  let last = -1;
  const append = (part: PyString): void => {
    if (points !== null) {
      for (const point of part) points.push(point);
    } else if (part instanceof PyStr) {
      points = Array.from(result);
      for (const point of part) points.push(point);
    } else {
      const next = part.charCodeAt(0);
      if (last >= 0xd800 && last <= 0xdbff && next >= 0xdc00 && next <= 0xdfff) {
        points = Array.from(result);
        for (const point of part) points.push(point);
      } else {
        result += part;
        if (part.length !== 0) last = part.charCodeAt(part.length - 1);
      }
    }
  };
  for (const part of parts) {
    if (!first) append(separator);
    append(part);
    first = false;
  }
  return points === null ? result : strFromChars(points);
}

/** Slice Python text using code-point offsets. */
export function strSlice(value: PyString, start?: number, end?: number): PyString {
  if (!hasAstral(value)) return (value as string).slice(start, end);
  return strFromChars(chars(value).slice(start, end));
}

/** Repeat Python text without fusing adjacent surrogate code points. */
export function strRepeat(value: PyString, count: number): PyString {
  if (count <= 0) return "";
  if (count === 1) return value;
  if (typeof value === "string") {
    const first = value.charCodeAt(0), last = value.charCodeAt(value.length - 1);
    if (!(last >= 0xd800 && last <= 0xdbff && first >= 0xdc00 && first <= 0xdfff)) return value.repeat(count);
  }
  return strJoin((function* () {
    for (let index = 0; index < count; index++) yield value;
  })());
}

/** Compare Python strings without equating a scalar and an explicit surrogate pair. */
export function strEq(left: PyString, right: PyString): boolean {
  if (left === right) return true;
  if (typeof left === "string" && typeof right === "string") return false;
  return strCmp(left, right) === 0;
}

/** Normalize scalar runs without joining explicit surrogate code points. */
export function strNormalize(value: PyString, form: string): PyString {
  if (typeof value === "string") return value.normalize(form);
  const parts: PyString[] = [];
  let run = "";
  for (const point of value) {
    const code = point.codePointAt(0)!;
    if (code >= 0xd800 && code <= 0xdfff) {
      if (run !== "") parts.push(run.normalize(form));
      parts.push(point);
      run = "";
    } else {
      run += point;
    }
  }
  if (run !== "") parts.push(run.normalize(form));
  return strJoin(parts);
}

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

/** Runtime representation of the NotImplemented singleton. */
export class PyNotImplementedT {}
/** The shared result for unsupported binary operations. */
export const NOT_IMPLEMENTED = new PyNotImplementedT();

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

interface KeyIndex {
  buckets: Map<string, string[]>;
  hashes: Map<string, string>;
}

// Primitive-only containers keep their direct encoded-key lookup.
const keyIndexes = new WeakMap<object, KeyIndex>();
let collisionKey = 0;

function keyRuntime(v: Value): Interp | null {
  if (v instanceof DiasporeInstance) return v.cls.runtime;
  if (v instanceof NativeObject) return v.runtime;
  if (v instanceof DiasporeTuple || v instanceof DiasporeFrozenSet) {
    const items = v instanceof DiasporeTuple ? v.items : v.map.values();
    for (const item of items) {
      const runtime = keyRuntime(item);
      if (runtime !== null) return runtime;
    }
  }
  if (v instanceof DiasporeSlice) {
    return keyRuntime(v.lower) ?? keyRuntime(v.upper) ?? keyRuntime(v.step);
  }
  return null;
}

function needsKeyEquality(v: Value): boolean {
  if (v instanceof DiasporeInstance || v instanceof DiasporeFrozenSet) return true;
  if (v instanceof NativeObject) return v.runtime !== null;
  if (v instanceof DiasporeTuple) return v.items.some(needsKeyEquality);
  if (v instanceof DiasporeSlice) {
    return needsKeyEquality(v.lower) || needsKeyEquality(v.upper) || needsKeyEquality(v.step);
  }
  return false;
}

function keysEqual(a: Value, b: Value): boolean {
  if (a === b) return true;
  const runtime = keyRuntime(a) ?? keyRuntime(b);
  if (runtime !== null) return runtime.drive(runtime.eqElem(a, b));
  if (a instanceof DiasporeTuple && b instanceof DiasporeTuple) {
    return a.items.length === b.items.length && a.items.every((item, i) => keysEqual(item, b.items[i]));
  }
  if (a instanceof DiasporeFrozenSet || b instanceof DiasporeFrozenSet) {
    if (!(a instanceof DiasporeFrozenSet && b instanceof DiasporeFrozenSet) || a.size !== b.size) return false;
    for (const [slot, item] of a.map) {
      if (!b.hasHK(storedHashKey(a.map, slot), item)) return false;
    }
    return true;
  }
  return hashKey(a) === hashKey(b);
}

function resolveHashKey(
  map: ReadonlyMap<string, unknown>, hk: string, key: Value, dict: boolean, insert = false,
): string | undefined {
  if (map instanceof NamespaceMap) keyIndexes.delete(map);
  let index = keyIndexes.get(map);
  if (index === undefined && !hk.startsWith("u")) {
    return insert || map.has(hk) ? hk : undefined;
  }
  if (index === undefined) {
    index = { buckets: new Map(), hashes: new Map() };
    for (const [slot, entry] of map) {
      const k = dict ? (entry as DictEntry).key : entry as Value;
      const hash = hashValue(k).toString();
      const bucket = index.buckets.get(hash);
      if (bucket === undefined) index.buckets.set(hash, [slot]);
      else bucket.push(slot);
      index.hashes.set(slot, hash);
    }
    keyIndexes.set(map, index);
  }
  const hash = hk.startsWith("u") ? hk.slice(1) : hashValue(key).toString();
  const bucket = index.buckets.get(hash);
  if (bucket !== undefined) {
    for (const slot of bucket) {
      const entry = map.get(slot);
      if (entry === undefined) continue;
      const stored = dict ? (entry as DictEntry).key : entry as Value;
      if (keysEqual(stored, key)) return slot;
    }
  }
  if (!insert) return undefined;
  const slot = map.has(hk) ? "\0" + ++collisionKey : hk;
  if (bucket === undefined) index.buckets.set(hash, [slot]);
  else bucket.push(slot);
  index.hashes.set(slot, hash);
  return slot;
}

function removeHashSlot(map: ReadonlyMap<string, unknown>, slot: string): void {
  const index = keyIndexes.get(map);
  if (index === undefined) return;
  const hash = index.hashes.get(slot);
  if (hash === undefined) return;
  index.hashes.delete(slot);
  const bucket = index.buckets.get(hash) as string[];
  bucket.splice(bucket.indexOf(slot), 1);
  if (bucket.length === 0) index.buckets.delete(hash);
  if (index.hashes.size === 0) keyIndexes.delete(map);
}

function copyKeyIndex(source: object, target: object): void {
  const index = keyIndexes.get(source);
  if (index !== undefined) {
    keyIndexes.set(target, {
      buckets: new Map([...index.buckets].map(([hash, slots]) => [hash, [...slots]])),
      hashes: new Map(index.hashes),
    });
  }
}

/** Reuse a stored key's hash without invoking user code again. */
export function storedHashKey(map: object, slot: string): string {
  const hash = keyIndexes.get(map)?.hashes.get(slot);
  return hash === undefined ? slot : "u" + hash;
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
    const slot = resolveHashKey(this.map, hk, key, true, true) as string;
    const e = this.map.get(slot);
    if (e !== undefined) {
      e.val = val;
    } else {
      this.map.set(slot, { key, val });
    }
  }
  get(k: Value): Value | undefined {
    return this.getHashed(hashKey(k), k);
  }
  getHashed(hk: string, key: Value): Value | undefined {
    const slot = resolveHashKey(this.map, hk, key, true);
    return slot === undefined ? undefined : this.map.get(slot)?.val;
  }
  set(k: Value, v: Value): void {
    this.setHK(hashKey(k), k, v);
  }
  delete(k: Value): Value | undefined {
    const slot = resolveHashKey(this.map, hashKey(k), k, true);
    if (slot === undefined) return undefined;
    const e = this.map.get(slot) as DictEntry;
    this.map.delete(slot);
    removeHashSlot(this.map, slot);
    return e.val;
  }
  has(k: Value): boolean {
    return resolveHashKey(this.map, hashKey(k), k, true) !== undefined;
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
    copyKeyIndex(this.map, d.map);
    return d;
  }
  clear(): void {
    this.map.clear();
    keyIndexes.delete(this.map);
  }
}

/** A live string-keyed namespace backed by a Python dictionary. */
export class DictionaryMap extends Map<string, Value> {
  readonly dictionary: DiasporeDict;
  constructor(dictionary: DiasporeDict) {
    super();
    this.dictionary = dictionary;
  }
  override get(key: string): Value | undefined { return this.dictionary.get(strFromKey(key)); }
  override has(key: string): boolean { return this.dictionary.has(strFromKey(key)); }
  override set(key: string, value: Value): this {
    this.dictionary.set(strFromKey(key), value);
    return this;
  }
  override delete(key: string): boolean { return this.dictionary.delete(strFromKey(key)) !== undefined; }
  override clear(): void { this.dictionary.clear(); }
  override get size(): number { return this.dictionary.size; }
  override *entries(): MapIterator<[string, Value]> {
    for (const { key, val } of this.dictionary.map.values()) if (isPyString(key)) yield [strKey(key), val];
  }
  override *keys(): MapIterator<string> { for (const [key] of this.entries()) yield key; }
  override *values(): MapIterator<Value> { for (const [, value] of this.entries()) yield value; }
  override [Symbol.iterator](): MapIterator<[string, Value]> { return this.entries(); }
}

/** A live projection of a dictionary's keys, values, or items. */
export class DiasporeDictView {
  dict: DiasporeDict;
  kind: "keys" | "values" | "items";
  constructor(dict: DiasporeDict, kind: "keys" | "values" | "items") {
    this.dict = dict;
    this.kind = kind;
  }
  get size(): number {
    return this.dict.size;
  }
}

/** Identify dictionary views with set-like comparison and operator semantics. */
export function isSetView(v: Value): v is DiasporeDictView {
  return v instanceof DiasporeDictView && v.kind !== "values";
}

class NamespaceMap extends Map<string, DictEntry> {
  private ns: Map<string, Value>;
  constructor(ns: Map<string, Value>) {
    super();
    this.ns = ns;
  }
  override get size(): number {
    let count = 0;
    for (const key of this.ns.keys()) if (!key.startsWith(".")) count++;
    return count;
  }
  private name(hk: string): string | undefined {
    if (hk.startsWith("S")) return "\0p" + hk.slice(1);
    if (!hk.startsWith("s") || hk.startsWith("s.")) return undefined;
    return strKey(hk.slice(1));
  }
  override get(hk: string): DictEntry | undefined {
    const name = this.name(hk);
    if (name === undefined) return undefined;
    const val = this.ns.get(name);
    return val === undefined ? undefined : { key: strFromKey(name), val };
  }
  override has(hk: string): boolean {
    const name = this.name(hk);
    return name !== undefined && this.ns.has(name);
  }
  override set(_hk: string, entry: DictEntry): this {
    if (!isPyString(entry.key)) throw diasporeErr("TypeError", "namespace keys must be strings");
    this.ns.set(strKey(entry.key), entry.val);
    return this;
  }
  override delete(hk: string): boolean {
    const name = this.name(hk);
    return name !== undefined && this.ns.delete(name);
  }
  override clear(): void {
    this.ns.clear();
  }
  override *entries(): MapIterator<[string, DictEntry]> {
    for (const [name, val] of this.ns) {
      if (name.startsWith(".")) continue;
      const key = strFromKey(name);
      yield [hashKey(key), { key, val }];
    }
  }
  override *keys(): MapIterator<string> {
    for (const name of this.ns.keys()) if (!name.startsWith(".")) yield hashKey(strFromKey(name));
  }
  override *values(): MapIterator<DictEntry> {
    for (const [name, val] of this.ns) if (!name.startsWith(".")) yield { key: strFromKey(name), val };
  }
  override [Symbol.iterator](): MapIterator<[string, DictEntry]> {
    return this.entries();
  }
}

/** A mutable dictionary backed by a live interpreter namespace. */
export class DiasporeNamespace extends DiasporeDict {
  constructor(ns: Map<string, Value>) {
    super();
    this.map = new NamespaceMap(ns);
  }
  override setHK(hk: string, key: Value, val: Value): void {
    if (!isPyString(key)) throw diasporeErr("TypeError", "namespace keys must be strings");
    this.map.set(hk, { key, val });
  }
}

/** A read-only, live view of a class namespace. */
export class DiasporeMappingProxy extends DiasporeDict {
  constructor(ns: Map<string, Value>) {
    super();
    this.map = new NamespaceMap(ns);
  }
  override setHK(_hk: string, _key: Value, _val: Value): void {
    throw diasporeErr("TypeError", "'mappingproxy' object does not support item assignment");
  }
  override delete(_key: Value): Value | undefined {
    throw diasporeErr("TypeError", "'mappingproxy' object does not support item deletion");
  }
  override clear(): void {
    throw diasporeErr("AttributeError", "'mappingproxy' object has no attribute 'clear'");
  }
}

/** Insertion-ordered set. */
export class DiasporeSet {
  map: Map<string, Value>;
  constructor(map: Map<string, Value> = new Map()) {
    this.map = map;
  }
  get size(): number {
    return this.map.size;
  }
  add(v: Value): void {
    this.addHK(hashKey(v), v);
  }
  addHK(hk: string, v: Value): void {
    const slot = resolveHashKey(this.map, hk, v, false, true) as string;
    if (!this.map.has(slot)) this.map.set(slot, v);
  }
  has(v: Value): boolean {
    if (v instanceof DiasporeSet && !(v instanceof DiasporeFrozenSet)) v = new DiasporeFrozenSet(v.copy().map);
    return this.hasHK(hashKey(v), v);
  }
  hasHK(hk: string, v: Value): boolean {
    return resolveHashKey(this.map, hk, v, false) !== undefined;
  }
  discard(v: Value): boolean {
    if (v instanceof DiasporeSet && !(v instanceof DiasporeFrozenSet)) v = new DiasporeFrozenSet(v.copy().map);
    return this.discardHK(hashKey(v), v);
  }
  /** Discard a key using its stored hash, retaining collision bookkeeping. */
  discardHK(hk: string, v: Value): boolean {
    const slot = resolveHashKey(this.map, hk, v, false);
    if (slot === undefined) return false;
    this.map.delete(slot);
    removeHashSlot(this.map, slot);
    return true;
  }
  /** Remove a stored entry without invoking its hash or equality methods again. */
  pop(): Value {
    const entry = this.map.entries().next();
    if (entry.done) throw diasporeErr("KeyError", "pop from an empty set");
    const [slot, value] = entry.value;
    this.map.delete(slot);
    removeHashSlot(this.map, slot);
    return value;
  }
  elems(): Value[] {
    return [...this.map.values()];
  }
  copy(): DiasporeSet {
    const s = new DiasporeSet();
    for (const [hk, v] of this.map) s.map.set(hk, v);
    copyKeyIndex(this.map, s.map);
    return s;
  }
  clear(): void {
    this.map.clear();
    keyIndexes.delete(this.map);
  }
}

/** Immutable set owning its storage; element hashes are retained on construction. */
export class DiasporeFrozenSet extends DiasporeSet {
  cachedHash: bigint | null = null;
  override copy(): DiasporeFrozenSet {
    return this;
  }
  override add(_v: Value): void {
    throw diasporeErr("AttributeError", "'frozenset' object has no attribute 'add'");
  }
  override addHK(_hk: string, _v: Value): void {
    throw diasporeErr("AttributeError", "'frozenset' object has no attribute 'add'");
  }
  override discard(_v: Value): boolean {
    throw diasporeErr("AttributeError", "'frozenset' object has no attribute 'discard'");
  }
  override pop(): Value {
    throw diasporeErr("AttributeError", "'frozenset' object has no attribute 'pop'");
  }
  override clear(): void {
    throw diasporeErr("AttributeError", "'frozenset' object has no attribute 'clear'");
  }
}

let objectBase: DiasporeClass | undefined;

/** Sandbox class namespace and method resolution order. */
export class DiasporeClass {
  name: string;
  bases: DiasporeClass[];
  declaredBases: (DiasporeClass | BuiltinType)[];
  ns: Map<string, Value>;
  mro: DiasporeClass[];
  runtime: Interp | null = null;
  metaclass: DiasporeClass | BuiltinType | null = null;
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
    this.declaredBases = bases;
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
    if (ns.has("__eq__") && !ns.has("__hash__")) ns.set("__hash__", NONE);
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
  private dictionary: Map<string, Value> | undefined;
  private dictionaryView: Value | undefined;
  private exceptionSlots: Map<string, Value> | undefined;
  /** Underlying primitive for a builtin-type subclass instance (else null). */
  prim: Value | null = null;
  constructor(cls: DiasporeClass) {
    this.cls = cls;
  }
  /** Builtin exception fields, separate from the Python instance dictionary. */
  get exc(): Map<string, Value> {
    return this.exceptionSlots ??= new Map();
  }
  get d(): Map<string, Value> {
    return this.dictionary ??= new Map();
  }
  set d(value: Map<string, Value>) {
    this.dictionary = value;
    this.dictionaryView = value instanceof DictionaryMap ? value.dictionary : undefined;
  }
  /** Inspect existing attributes without allocating a dictionary for slotted instances. */
  peekDict(): Map<string, Value> | undefined {
    return this.dictionary;
  }
  /** Return the actual Python instance dictionary, allocating it only on demand. */
  getDict(): Value {
    if (this.dictionaryView !== undefined) return this.dictionaryView;
    const dictionary = new DiasporeDict();
    if (this.dictionary !== undefined) {
      for (const [name, value] of this.dictionary) dictionary.set(strFromKey(name), value);
    }
    this.dictionary = new DictionaryMap(dictionary);
    this.dictionaryView = dictionary;
    return dictionary;
  }
  /** Replace the dictionary while preserving its identity and live attribute access. */
  setDict(value: Value): void {
    const dictionary = value instanceof DiasporeInstance ? value.prim : value;
    if (!(dictionary instanceof DiasporeDict)) {
      throw diasporeErr("TypeError", `__dict__ must be set to a dictionary, not a '${typeName(value)}'`);
    }
    if (this.dictionaryView === value) return;
    this.dictionary = new DictionaryMap(dictionary);
    this.dictionaryView = value;
  }
  /** Detach the instance dictionary without mutating references to its old contents. */
  deleteDict(): void {
    this.dictionary = undefined;
    this.dictionaryView = undefined;
  }
}

/** Normalized positional and keyword parameter declarations. */
export interface FuncParams {
  args: string[];
  posonly: number;
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
  coro: boolean;
  asyncgen: boolean;
  closure: Frame | null;
  moduleName: string;
  defclass: DiasporeClass | null = null;
  defaults: Value[] = [];
  kwdefaults: Map<string, Value> = new Map();
  /** User-assigned function attributes (f.x = ...). */
  attrs: Map<string, Value> = new Map();
  metadata: Map<string, Value> = new Map();
  constructor(node: Record<string, unknown>, closure: Frame | null, moduleName: string) {
    this.node = node;
    this.name = node.name as string;
    // Compact bundles omit default-valued parameter fields (spec 2.2).
    const p = (node.params ?? {}) as Record<string, unknown>;
    this.params = {
      args: (p.args as string[]) ?? [],
      posonly: (p.posonly as number) ?? 0,
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
    this.coro = node.coro === true;
    this.asyncgen = node.asyncgen === true;
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
  kind = "builtin_function_or_method";
  self?: Value;
  objclass?: Value;
  constructor(name: string, fn: BuiltinFn) {
    this.name = name;
    this.fn = fn;
  }
}

/** A builtin type name paired with its constructor. */
export class BuiltinType {
  name: string;
  ctor: BuiltinFn;
  namespace: Map<string, Value> | null = null;
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
  /** Interpreter for opt-in native equality and hashing protocols. */
  runtime: Interp | null = null;
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
  docFromGetter: boolean;
  constructor(
    fget: Value | null = null,
    fset: Value | null = null,
    fdel: Value | null = null,
    doc: Value | null = null,
    docFromGetter = false,
  ) {
    this.fget = fget;
    this.fset = fset;
    this.fdel = fdel;
    this.doc = doc;
    this.docFromGetter = docFromGetter;
  }
}

/** A compiled code snippet from compile()/exec()/eval(). */
export class CodeObject {
  nodes: unknown[];
  mode: string;
  filename: string;
  attrs: Map<string, Value> = new Map();
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
  | PyStr
  | Uint8Array
  | PyNoneT
  | PyEllipsisT
  | PyNotImplementedT
  | DiasporeList
  | DiasporeTuple
  | DiasporeRange
  | DiasporeSlice
  | DiasporeDict
  | DiasporeDictView
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
  ["EOFError", "Exception"],
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
  ["UnicodeTranslateError", "UnicodeError"],
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
  ["Warning", "Exception"],
  ["UserWarning", "Warning"],
  ["DeprecationWarning", "Warning"],
  ["PendingDeprecationWarning", "Warning"],
  ["SyntaxWarning", "Warning"],
  ["RuntimeWarning", "Warning"],
  ["FutureWarning", "Warning"],
  ["ImportWarning", "Warning"],
  ["UnicodeWarning", "Warning"],
  ["BytesWarning", "Warning"],
  ["ResourceWarning", "Warning"],
  ["EncodingWarning", "Warning"],
];

/** The universal base class: every class's MRO ends with it. */
export const OBJECT: DiasporeClass = new DiasporeClass("object", [], new Map<string, Value>([
  ["__module__", "builtins"],
  ["__doc__", "The base class of the class hierarchy.\n\nWhen called, it accepts no arguments and returns a new featureless\ninstance that has no instance attributes and cannot be given any.\n"],
]));
objectBase = OBJECT;

OBJECT.ns.set("__setattr__", new Builtin("__setattr__", function* (I, args): G<Value> {
  if (args.length !== 3) throw diasporeErr("TypeError", "object.__setattr__() takes 3 arguments");
  if (!(args[0] instanceof DiasporeInstance)) {
    throw diasporeErr("TypeError", "object.__setattr__() requires an instance");
  }
  if (!isPyString(args[1])) throw diasporeErr("TypeError", "attribute name must be string");
  yield* I.setAttrBase(args[0], strKey(args[1]), args[2]);
  return NONE;
}));
OBJECT.ns.set("__delattr__", new Builtin("__delattr__", function* (I, args): G<Value> {
  if (args.length !== 2) throw diasporeErr("TypeError", "object.__delattr__() takes 2 arguments");
  if (!(args[0] instanceof DiasporeInstance)) {
    throw diasporeErr("TypeError", "object.__delattr__() requires an instance");
  }
  if (!isPyString(args[1])) throw diasporeErr("TypeError", "attribute name must be string");
  yield* I.delAttrBase(args[0], strKey(args[1]));
  return NONE;
}));
OBJECT.ns.set("__hash__", new Builtin("__hash__", function* (_I, args): G<Value> {
  if (args.length !== 1 || typeof args[0] !== "object") {
    throw diasporeErr("TypeError", "object.__hash__() requires an object");
  }
  return BigInt(objId(args[0]));
}));
for (const name of ["__eq__", "__ne__"]) {
  const descriptor = new Builtin(name, function* (I, args, kw): G<Value> {
    if (kw.size !== 0) throw diasporeErr("TypeError", `object.${name}() takes no keyword arguments`);
    if (args.length !== 2) throw diasporeErr("TypeError", `object.${name}() takes exactly 2 arguments (${args.length} given)`);
    const [self, other] = args;
    if (name === "__eq__") return self === other ? true : NOT_IMPLEMENTED;
    const equal = self instanceof DiasporeInstance
      ? yield* I.callDunder(self, "__eq__", [other])
      : yield* I.invoke(yield* I.getAttr(self, "__eq__"), [other], new Map());
    return equal === NOT_IMPLEMENTED ? NOT_IMPLEMENTED : !(yield* I.truth(equal));
  });
  descriptor.kind = "wrapper_descriptor";
  OBJECT.ns.set(name, descriptor);
}

/** Builtin exception classes indexed by Python name. */
export const EXC_CLASSES: Map<string, DiasporeClass> = (() => {
  const m = new Map<string, DiasporeClass>();
  for (const [name, parent] of EXC_TREE) {
    const bases = parent !== null ? [m.get(parent) as DiasporeClass] : [];
    m.set(name, new DiasporeClass(name, bases, new Map([["__module__", "builtins"]])));
  }
  // ExceptionGroup inherits from both BaseExceptionGroup and Exception (C3)
  m.set(
    "ExceptionGroup",
    new DiasporeClass(
      "ExceptionGroup",
      [m.get("BaseExceptionGroup") as DiasporeClass, m.get("Exception") as DiasporeClass],
      new Map([["__module__", "builtins"]]),
    ),
  );
  (m.get("BaseException") as DiasporeClass).ns.set(
    "__init__",
    new Builtin("__init__", function* (_I, args): G<Value> {
      const self = args[0] as DiasporeInstance;
      self.exc.set("args", new DiasporeTuple(args.slice(1)));
      return NONE;
    }),
  );
  (m.get("BaseException") as DiasporeClass).ns.set("__reduce__", new Builtin("__reduce__", function* (_I, args, kw): G<Value> {
    if (kw.size !== 0) throw diasporeErr("TypeError", "__reduce__() takes no keyword arguments");
    if (args.length !== 1) throw diasporeErr("TypeError", `expected 0 arguments, got ${args.length - 1}`);
    const self = args[0];
    if (!(self instanceof DiasporeInstance && self.cls.issub(m.get("BaseException")!))) {
      throw diasporeErr("TypeError", "descriptor '__reduce__' requires a 'BaseException' object");
    }
    const result: Value[] = [self.cls, self.exc.get("args") ?? new DiasporeTuple([])];
    if (self.peekDict() !== undefined) result.push(self.getDict());
    return new DiasporeTuple(result);
  }));
  (m.get("BaseException") as DiasporeClass).ns.set("__setstate__", new Builtin("__setstate__", function* (I, args, kw): G<Value> {
    if (kw.size !== 0) throw diasporeErr("TypeError", "__setstate__() takes no keyword arguments");
    if (args.length !== 2) throw diasporeErr("TypeError", `__setstate__() takes exactly one argument (${args.length - 1} given)`);
    const self = args[0];
    if (!(self instanceof DiasporeInstance && self.cls.issub(m.get("BaseException")!))) {
      throw diasporeErr("TypeError", "descriptor '__setstate__' requires a 'BaseException' object");
    }
    const state = args[1] instanceof DiasporeInstance ? args[1].prim : args[1];
    if (state instanceof PyNoneT) return NONE;
    if (!(state instanceof DiasporeDict)) throw diasporeErr("TypeError", "state is not a dictionary");
    for (const [key, value] of state.entries()) {
      const name = key instanceof DiasporeInstance ? key.prim : key;
      if (!isPyString(name)) throw diasporeErr("TypeError", `attribute name must be string, not '${typeName(key)}'`);
      yield* I.setAttr(self, strKey(name), value);
    }
    return NONE;
  }));
  (m.get("BaseExceptionGroup") as DiasporeClass).ns.set("__new__", new StaticMethodV(new Builtin("__new__", groupNew)));
  (m.get("BaseExceptionGroup") as DiasporeClass).ns.set("__str__", new Builtin("__str__", function* (I, args, kw): G<Value> {
    if (kw.size !== 0) throw diasporeErr("TypeError", "__str__() takes no keyword arguments");
    if (args.length !== 1) throw diasporeErr("TypeError", `expected 0 arguments, got ${args.length - 1}`);
    const self = args[0];
    if (!(self instanceof DiasporeInstance && self.cls.issub(m.get("BaseExceptionGroup")!))) {
      throw diasporeErr("TypeError", "descriptor '__str__' requires a 'BaseExceptionGroup' object");
    }
    const message = yield* I.str(self.exc.get("message")!);
    const exceptions = self.exc.get("exceptions") as DiasporeTuple;
    const count = exceptions.items.length;
    return strConcat(message, ` (${count} sub-exception${count === 1 ? "" : "s"})`);
  }));
  (m.get("SyntaxError") as DiasporeClass).ns.set("__init__", new Builtin("__init__", function* (I, args, kw): G<Value> {
    const self = args[0] as DiasporeInstance;
    if (kw.size !== 0) throw diasporeErr("TypeError", `${self.cls.name}() takes no keyword arguments`);
    const positional = args.slice(1);
    self.exc.set("args", new DiasporeTuple(positional));
    if (positional.length > 0) self.exc.set("msg", positional[0]);
    if (positional.length === 2) {
      const details = yield* I.types.get("tuple")!.ctor(I, [positional[1]], new Map());
      setSyntaxErrorDetails(self, (details as DiasporeTuple).items);
    }
    return NONE;
  }));
  return m;
})();

function setSyntaxErrorDetails(self: DiasporeInstance, details: Value[]): void {
  self.exc.delete("end_lineno");
  self.exc.delete("end_offset");
  if (details.length < 4) {
    throw diasporeErr("TypeError", `function takes at least 4 arguments (${details.length} given)`);
  }
  if (details.length > 6) {
    throw diasporeErr("TypeError", `function takes at most 6 arguments (${details.length} given)`);
  }
  const names = ["filename", "lineno", "offset", "text", "end_lineno", "end_offset"];
  for (let i = 0; i < details.length; i++) self.exc.set(names[i], details[i]);
  if (details.length === 5) {
    throw diasporeErr("TypeError", "end_offset must be provided when end_lineno is provided");
  }
}

/** Allocate validated exception groups, narrowing only the exact builtin base. */
function* groupNew(I: Interp, args: Value[]): G<Value> {
  const base = EXC_CLASSES.get("BaseExceptionGroup")!;
  const exceptionGroup = EXC_CLASSES.get("ExceptionGroup")!;
  const exception = EXC_CLASSES.get("Exception")!;
  const requested = args[0];
  if (!(requested instanceof DiasporeClass && requested.issub(base))) {
    throw diasporeErr("TypeError", "BaseExceptionGroup.__new__ requires a BaseExceptionGroup subtype");
  }
  if (args.length !== 3) {
    throw diasporeErr("TypeError", `BaseExceptionGroup.__new__() takes exactly 2 arguments (${args.length - 1} given)`);
  }
  const [, message, exceptions] = args;
  const text = message instanceof DiasporeInstance ? message.prim : message;
  if (!isPyString(text)) {
    throw diasporeErr("TypeError", "argument 1 must be str, not " + typeName(message));
  }
  const sequence = exceptions instanceof DiasporeInstance ? exceptions.prim ?? exceptions : exceptions;
  if (!(sequence instanceof DiasporeList || sequence instanceof DiasporeTuple ||
        sequence instanceof DiasporeRange || sequence instanceof Uint8Array || isPyString(sequence) ||
        (sequence instanceof DiasporeInstance && sequence.cls.lookup("__getitem__") !== undefined))) {
    throw diasporeErr("TypeError", "second argument (exceptions) must be a sequence");
  }
  const items = (yield* I.types.get("tuple")!.ctor(I, [exceptions], new Map())) as DiasporeTuple;
  if (items.items.length === 0) {
    throw diasporeErr("ValueError", "second argument (exceptions) must be a non-empty sequence");
  }
  let nestedBase = false;
  for (let i = 0; i < items.items.length; i++) {
    const item = items.items[i];
    if (!(item instanceof DiasporeInstance && item.cls.issub(BASE_EXC))) {
      throw diasporeErr("ValueError", `Item ${i} of second argument (exceptions) is not an exception`);
    }
    if (!item.cls.issub(exception)) nestedBase = true;
  }
  if (nestedBase && requested.issub(exception)) {
    throw diasporeErr("TypeError", requested === exceptionGroup
      ? "Cannot nest BaseExceptions in an ExceptionGroup"
      : `Cannot nest BaseExceptions in '${requested.name}'`);
  }
  const cls = requested === base && !nestedBase ? exceptionGroup : requested;
  const self = new DiasporeInstance(cls);
  self.exc.set("args", new DiasporeTuple(args.slice(1)));
  self.exc.set("message", message);
  self.exc.set("exceptions", items);
  return self;
}

/** Build an (Base)ExceptionGroup instance directly (used by except* split). */
export function makeGroup(message: PyString, excs: Value[], base = false): DiasporeInstance {
  const exceptionCls = EXC_CLASSES.get("Exception") as DiasporeClass;
  const anyBare = excs.some((e) => !(e instanceof DiasporeInstance && e.cls.issub(exceptionCls)));
  const name = base && anyBare ? "BaseExceptionGroup" : "ExceptionGroup";
  const inst = new DiasporeInstance(EXC_CLASSES.get(name) as DiasporeClass);
  inst.exc.set("args", new DiasporeTuple([message, new DiasporeList([...excs])]));
  inst.exc.set("message", message);
  inst.exc.set("exceptions", new DiasporeTuple([...excs]));
  return inst;
}

/** Root of the sandbox exception hierarchy. */
export const BASE_EXC = EXC_CLASSES.get("BaseException") as DiasporeClass;

/** Construct a builtin exception instance with positional arguments. */
export function makeExc(name: string, ...args: Value[]): DiasporeInstance {
  let cls = EXC_CLASSES.get(name) ?? (EXC_CLASSES.get("RuntimeError") as DiasporeClass);
  const osError = cls.issub(EXC_CLASSES.get("OSError")!);
  if (osError) cls = selectOSErrorClass(cls, args);
  const inst = new DiasporeInstance(cls);
  if (osError) initializeOSError(inst, args);
  else inst.exc.set("args", new DiasporeTuple(args));
  if (cls.issub(EXC_CLASSES.get("SyntaxError")!)) {
    if (args.length > 0) inst.exc.set("msg", args[0]);
    if (args.length === 2 && (args[1] instanceof DiasporeTuple || args[1] instanceof DiasporeList)) {
      setSyntaxErrorDetails(inst, args[1].items);
    }
  }
  return inst;
}

/** Construct a catchable sandbox error with a message. */
export function diasporeErr(name: string, msg: PyString): DiasporeError {
  return new DiasporeError(makeExc(name, msg));
}

/** Construct a codec exception with Python's structured error attributes. */
export function unicodeCodecError(
  kind: "UnicodeEncodeError" | "UnicodeDecodeError", encoding: string,
  object: PyString | Uint8Array, start: number, end: number, reason: string,
): DiasporeError {
  const value = makeExc(kind, encoding, object, BigInt(start), BigInt(end), reason);
  value.exc.set("encoding", encoding);
  value.exc.set("object", object);
  value.exc.set("start", BigInt(start));
  value.exc.set("end", BigInt(end));
  value.exc.set("reason", reason);
  return new DiasporeError(value);
}

/** Read the positional arguments stored on an exception instance. */
export function excArgs(inst: DiasporeInstance): Value[] {
  const a = inst.exc.get("args");
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
  if (v instanceof PyStr) return "str";
  if (v instanceof Uint8Array) return "bytes";
  if (v instanceof PyNoneT) return "NoneType";
  if (v instanceof PyEllipsisT) return "ellipsis";
  if (v instanceof PyNotImplementedT) return "NotImplementedType";
  if (v instanceof DiasporeList) return "list";
  if (v instanceof DiasporeTuple) return "tuple";
  if (v instanceof DiasporeMappingProxy) return "mappingproxy";
  if (v instanceof DiasporeDict) return "dict";
  if (v instanceof DiasporeDictView) return "dict_" + v.kind;
  if (v instanceof DiasporeFrozenSet) return "frozenset";
  if (v instanceof DiasporeSet) return "set";
  if (v instanceof DiasporeRange) return "range";
  if (v instanceof DiasporeSlice) return "slice";
  if (v instanceof DiasporeInstance) return v.cls.name;
  if (v instanceof DiasporeClass || v instanceof BuiltinType) return "type";
  if (v instanceof DiasporeFunction) return "function";
  if (v instanceof BoundMethod) return v.fn instanceof Builtin && v.fn.kind === "wrapper_descriptor" ? "method-wrapper" : "method";
  if (v instanceof Builtin) return v.kind;
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
  if (v instanceof PyStr) return "S" + v.points.map(point => point.codePointAt(0)!.toString(16)).join(",");
  if (needsKeyEquality(v)) {
    return "u" + hashValue(v);
  }
  if (v instanceof Uint8Array) return "b" + bytesToBinStr(v);
  if (v instanceof PyNoneT) return "N";
  if (v instanceof PyEllipsisT) return "E";
  if (v instanceof PyNotImplementedT) return "NI";
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
  if (v instanceof DiasporeDictView && v.kind === "values") return "p" + objId(v);
  if (
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

/** Deterministic hashing for nonnumeric builtin values. */
export function fnvHash(s: string): bigint {
  let h = 0xcbf29ce484222325n;
  for (let i = 0; i < s.length; i++) {
    h ^= BigInt(s.charCodeAt(i));
    h = (h * 0x100000001b3n) & 0xffffffffffffffffn;
  }
  return h & 0x7fffffffffffffffn;
}

/** Python hash protocol, including user methods and compound keys. */
export function hashValue(v: Value): bigint {
  if (typeof v === "number" && !Number.isNaN(v)) {
    if (!Number.isFinite(v)) return v > 0 ? 314159n : -314159n;
    // Binary64 is an integer significand times a power of two. Modulo
    // 2**61 - 1, multiplying by that power rotates the significand's bits.
    f64buf.setFloat64(0, v);
    const bits = f64buf.getBigUint64(0);
    const exponent = Number((bits >> 52n) & 0x7ffn);
    const significand = (bits & 0xfffffffffffffn) | (exponent === 0 ? 0n : 0x10000000000000n);
    const shift = BigInt(((exponent === 0 ? -1074 : exponent - 1075) % 61 + 61) % 61);
    let hash = ((significand << shift) & 0x1fffffffffffffffn) | (significand >> (61n - shift));
    if (v < 0) hash = -hash;
    return hash === -1n ? -2n : hash;
  }
  if (v instanceof Complex) {
    const hash = BigInt.asIntN(64, hashValue(v.re) + 1000003n * hashValue(v.im));
    return hash === -1n ? -2n : hash;
  }
  if (v instanceof DiasporeInstance) {
    const hook = v.cls.lookup("__hash__");
    if (hook instanceof PyNoneT) {
      throw diasporeErr("TypeError", `unhashable type: '${v.cls.name}'`);
    }
    if (v.prim !== null && !v.cls.mro.some(c => c !== OBJECT && c.ns.has("__hash__"))) {
      if (v.prim instanceof DiasporeList || v.prim instanceof DiasporeDict ||
          (v.prim instanceof DiasporeSet && !(v.prim instanceof DiasporeFrozenSet))) {
        throw diasporeErr("TypeError", `unhashable type: '${v.cls.name}'`);
      }
      return hashValue(v.prim);
    }
    if (hook === undefined || hook === OBJECT.ns.get("__hash__")) return BigInt(objId(v));
    const runtime = v.cls.runtime as Interp;
    const result = runtime.drive(runtime.callDunder(v, "__hash__", []));
    let n = asIndex(result);
    if (n === null) throw diasporeErr("TypeError", "__hash__ method should return an integer");
    if (n < -(1n << 63n) || n >= 1n << 63n) n %= (1n << 61n) - 1n;
    return n === -1n ? -2n : n;
  }
  if (v instanceof NativeObject && v.runtime !== null) {
    const hook = v.methods.get("__hash__");
    if (hook === undefined) return fnvHash("p" + objId(v));
    const result = v.runtime.drive(v.runtime.invoke(hook, [], new Map()));
    let n = asIndex(result);
    if (n === null) throw diasporeErr("TypeError", "__hash__ method should return an integer");
    if (n < -(1n << 63n) || n >= 1n << 63n) n %= (1n << 61n) - 1n;
    return n === -1n ? -2n : n;
  }
  if (v instanceof DiasporeFrozenSet) {
    if (v.cachedHash !== null) return v.cachedHash;
    let hash = 0n;
    for (const [slot, item] of v.map) {
      const token = storedHashKey(v.map, slot);
      const h = BigInt.asUintN(64, token.startsWith("u") ? BigInt(token.slice(1)) : hashValue(item));
      hash ^= BigInt.asUintN(64, ((h ^ 89869747n) ^ (h << 16n)) * 3644798167n);
    }
    hash = BigInt.asUintN(64, hash ^ (BigInt(v.size) + 1n) * 1927868237n);
    hash ^= (hash >> 11n) ^ (hash >> 25n);
    hash = BigInt.asIntN(64, hash * 69069n + 907133923n);
    v.cachedHash = hash === -1n ? 590923713n : hash;
    return v.cachedHash;
  }
  if (v instanceof DiasporeTuple || v instanceof DiasporeSlice) {
    const items = v instanceof DiasporeTuple ? v.items : [v.lower, v.upper, v.step];
    let acc = 2870177450012600261n;
    for (const item of items) {
      acc = BigInt.asUintN(64, acc + BigInt.asUintN(64, hashValue(item)) * 14029467366897019727n);
      acc = BigInt.asUintN(64, (acc << 31n) | (acc >> 33n));
      acc = BigInt.asUintN(64, acc * 11400714785074694791n);
    }
    acc = BigInt.asIntN(64, acc + (BigInt(items.length) ^ (2870177450012600261n ^ 3527539n)));
    return acc === -1n ? 1546275796n : acc;
  }
  const hk = hashKey(v);
  if (hk.startsWith("i")) {
    const n = BigInt(hk.slice(1)) % ((1n << 61n) - 1n);
    return n === -1n ? -2n : n;
  }
  return fnvHash(hk);
}

// --------------------------------------------------------------------------
// string code-point helpers

let charsKey: PyString = "";
let charsVal: string[] | null = null;

/** Code points of s as an array (cached for the most recent string). */
export function chars(s: PyString): string[] {
  if (s === charsKey && charsVal !== null) return charsVal;
  const a = Array.from(s);
  charsKey = s;
  charsVal = a;
  return a;
}

const ASTRAL_RE = /[\uD800-\uDFFF]/;

/** Test whether a string contains code points outside the BMP. */
export function hasAstral(s: PyString): boolean {
  return s instanceof PyStr || ASTRAL_RE.test(s);
}

/** Count Unicode code points in a string. */
export function strLen(s: PyString): number {
  return s instanceof PyStr ? s.points.length : hasAstral(s) ? chars(s).length : s.length;
}

/** Python-style string comparison: by code points. */
export function strCmp(a: PyString, b: PyString): number {
  if (typeof a === "string" && typeof b === "string" && !hasAstral(a) && !hasAstral(b)) {
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
