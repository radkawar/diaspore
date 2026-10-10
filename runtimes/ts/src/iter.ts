/* Iterators and generators. Every iterator's next() is a JS generator so it
 * can call back into diaspore code (map/filter functions, __next__ methods). */

import {
  type Value,
  type G,
  type PyString,
  isPyString,
  NONE,
  PyNoneT,
  makeExc,
  DiasporeIterBase,
  DiasporeList,
  DiasporeTuple,
  DiasporeDict,
  DiasporeDictView,
  DiasporeSet,
  DiasporeRange,
  DiasporeInstance,
  DiasporeClass,
  DiasporeFunction,
  NativeObject,
  chars,
  excIsA,
  excArgs,
  DiasporeError,
  ReturnSig,
  typeName,
  diasporeErr,
} from "./values.ts";
import type { Interp, Frame } from "./interp.ts";
import { lookupSpecial } from "./helpers.ts";
import { diasporeLen } from "./ops.ts";

/** Iterate a list while observing its current length. */
export class ListIter extends DiasporeIterBase {
  kindName = "list_iterator";
  l: DiasporeList;
  i = 0;
  constructor(l: DiasporeList) {
    super();
    this.l = l;
  }
  *next(): G<[Value, boolean]> {
    if (this.i >= this.l.items.length) return [NONE, false];
    return [this.l.items[this.i++], true];
  }
}

/** Iterate a list backward, observing item changes without copying its storage. */
export class ListReverseIter extends DiasporeIterBase {
  kindName = "list_reverseiterator";
  l: DiasporeList;
  i: number;
  constructor(l: DiasporeList) {
    super();
    this.l = l;
    this.i = l.items.length - 1;
  }
  *next(): G<[Value, boolean]> {
    if (this.i < 0 || this.i >= this.l.items.length) {
      this.i = -1;
      return [NONE, false];
    }
    return [this.l.items[this.i--], true];
  }
}

/** Iterate a fixed sequence of sandbox values. */
export class SeqIter extends DiasporeIterBase {
  kindName: string;
  items: Value[];
  i = 0;
  constructor(items: Value[], kind = "iterator") {
    super();
    this.items = items;
    this.kindName = kind;
  }
  *next(): G<[Value, boolean]> {
    if (this.i >= this.items.length) return [NONE, false];
    return [this.items[this.i++], true];
  }
}

/** Iterate a dictionary view while observing value updates and rejecting size changes. */
export class DictViewIter extends DiasporeIterBase {
  kindName: string;
  view: DiasporeDictView;
  size: number;
  entries: Iterator<{ key: Value; val: Value }> | null;
  slots: string[] | null;
  offset = 0;
  done = false;
  invalid = false;
  remaining: number;
  constructor(view: DiasporeDictView, reverse = false) {
    super();
    this.view = view;
    this.size = view.size;
    this.remaining = view.size;
    this.kindName = "dict_" + (reverse ? "reverse" : "") +
      (view.kind === "keys" ? "key" : view.kind === "values" ? "value" : "item") + "iterator";
    this.entries = reverse ? null : view.dict.map.values();
    this.slots = reverse ? [...view.dict.map.keys()].reverse() : null;
  }
  *next(): G<[Value, boolean]> {
    if (this.done) return [NONE, false];
    if (this.invalid || this.view.size !== this.size) {
      this.invalid = true;
      throw diasporeErr("RuntimeError", "dictionary changed size during iteration");
    }
    let entry: { key: Value; val: Value } | undefined;
    if (this.slots !== null) {
      while (entry === undefined && this.offset < this.slots.length) {
        entry = this.view.dict.map.get(this.slots[this.offset++]);
      }
    } else {
      const next = (this.entries as Iterator<{ key: Value; val: Value }>).next();
      if (!next.done) entry = next.value;
    }
    if (entry === undefined) {
      this.done = true;
      return [NONE, false];
    }
    if (this.remaining === 0) {
      this.done = true;
      throw diasporeErr("RuntimeError", "dictionary keys changed during iteration");
    }
    this.remaining--;
    const value = this.view.kind === "keys" ? entry.key : this.view.kind === "values" ?
      entry.val : new DiasporeTuple([entry.key, entry.val]);
    return [value, true];
  }
}

/** Iterate live set entries, rejecting size changes until exhaustion. */
export class SetIter extends DiasporeIterBase {
  kindName = "set_iterator";
  set: DiasporeSet;
  size: number;
  entries: Iterator<Value>;
  done = false;
  invalid = false;
  constructor(set: DiasporeSet) {
    super();
    this.set = set;
    this.size = set.size;
    this.entries = set.map.values();
  }
  *next(): G<[Value, boolean]> {
    if (this.done) return [NONE, false];
    if (this.invalid || this.set.size !== this.size) {
      this.invalid = true;
      throw diasporeErr("RuntimeError", "Set changed size during iteration");
    }
    const entry = this.entries.next();
    if (entry.done) {
      this.done = true;
      return [NONE, false];
    }
    return [entry.value, true];
  }
}

/** Iterate Python code points, preserving explicit surrogate boundaries. */
export class StrIter extends DiasporeIterBase {
  kindName = "str_iterator";
  rs: string[];
  i = 0;
  constructor(s: PyString) {
    super();
    this.rs = chars(s);
    if (typeof s === "string" && !/[^\x00-\x7f]/.test(s)) this.kindName = "str_ascii_iterator";
  }
  *next(): G<[Value, boolean]> {
    if (this.i >= this.rs.length) return [NONE, false];
    return [this.rs[this.i++], true];
  }
}

/** Iterate bytes as sandbox integers. */
export class BytesIter extends DiasporeIterBase {
  kindName = "bytes_iterator";
  b: Uint8Array;
  i = 0;
  constructor(b: Uint8Array) {
    super();
    this.b = b;
  }
  *next(): G<[Value, boolean]> {
    if (this.i >= this.b.length) return [NONE, false];
    return [BigInt(this.b[this.i++]), true];
  }
}

/** Iterate forward or backward over a range without materializing its values. */
export class RangeIter extends DiasporeIterBase {
  kindName = "range_iterator";
  cur: bigint;
  step: bigint;
  left: bigint;
  constructor(r: DiasporeRange, reverse = false) {
    super();
    this.left = r.len();
    this.cur = reverse ? r.start + (this.left - 1n) * r.step : r.start;
    this.step = reverse ? -r.step : r.step;
    const min = -(1n << 63n), max = (1n << 63n) - 1n;
    const fits = r.start >= min && r.start <= max && r.stop >= min && r.stop <= max &&
      r.step >= min && r.step <= max && this.left <= max;
    const endpointFits = reverse
      ? r.step !== min && r.start - r.step >= min && r.start - r.step <= max
      : this.left === 0n || (r.step > 0n ? r.stop <= max - (r.step - 1n) : r.stop >= min + (-1n - r.step));
    if (!fits || !endpointFits) this.kindName = "longrange_iterator";
  }
  *next(): G<[Value, boolean]> {
    if (this.left <= 0n) return [NONE, false];
    const v = this.cur;
    this.cur += this.step;
    this.left -= 1n;
    return [v, true];
  }
}

/** Adapts a diaspore object implementing __next__. */
export class InstIter extends DiasporeIterBase {
  kindName = "iterator";
  I: Interp;
  obj: DiasporeInstance | DiasporeClass;
  done = false;
  constructor(I: Interp, obj: DiasporeInstance | DiasporeClass) {
    super();
    this.I = I;
    this.obj = obj;
  }
  *next(): G<[Value, boolean]> {
    if (this.done) return [NONE, false];
    try {
      const method = yield* lookupSpecial(this.I, this.obj, "__next__");
      if (method === undefined) throw diasporeErr("TypeError", `'${typeName(this.obj)}' object is not an iterator`);
      const v = yield* this.I.invoke(method, [], new Map());
      return [v, true];
    } catch (e) {
      if (e instanceof DiasporeError && excIsA(e.value, "StopIteration")) {
        this.done = true;
        const args = excArgs(e.value as DiasporeInstance);
        if (args.length > 0) this.retval = args[0];
        return [NONE, false];
      }
      throw e;
    }
  }
}

/** Iterate the integer-indexed sequence protocol until IndexError. */
class ItemIter extends DiasporeIterBase {
  kindName = "iterator";
  I: Interp;
  obj: DiasporeInstance | DiasporeClass;
  index = 0n;
  done = false;
  reverse: boolean;
  constructor(I: Interp, obj: DiasporeInstance | DiasporeClass, index = 0n, reverse = false) {
    super();
    this.I = I;
    this.obj = obj;
    this.index = index;
    this.reverse = reverse;
    if (reverse) this.kindName = "reversed";
  }
  *next(): G<[Value, boolean]> {
    if (this.done || (this.reverse && this.index < 0n)) return [NONE, false];
    const method = yield* lookupSpecial(this.I, this.obj, "__getitem__");
    if (method === undefined) throw diasporeErr("TypeError", `'${typeName(this.obj)}' object is not subscriptable`);
    try {
      const value = yield* this.I.invoke(method, [this.index], new Map());
      this.index += this.reverse ? -1n : 1n;
      return [value, true];
    } catch (error) {
      if (error instanceof DiasporeError &&
          (excIsA(error.value, "IndexError") || excIsA(error.value, "StopIteration"))) {
        this.done = true;
        return [NONE, false];
      }
      throw error;
    }
  }
}

/** Reverse a list or user sequence through live integer-indexed access. */
export function* reverseSequenceIterator(I: Interp, value: Value): G<DiasporeIterBase> {
  if (value instanceof DiasporeList) return new ListReverseIter(value);
  const cls = value instanceof DiasporeInstance ? value.cls :
    value instanceof DiasporeClass ? value.metaclass : null;
  if (!(cls instanceof DiasporeClass) || cls.lookup("__getitem__") === undefined) {
    throw diasporeErr("TypeError", `'${typeName(value)}' object is not reversible`);
  }
  const length = yield* diasporeLen(I, value);
  if (length < 0n) throw diasporeErr("ValueError", "__len__() should return >= 0");
  if (length > 9223372036854775807n) {
    throw diasporeErr("OverflowError", "cannot fit 'int' into an index-sized integer");
  }
  return new ItemIter(I, value as DiasporeInstance | DiasporeClass, length - 1n, true);
}

/** Apply a sandbox callable to values from each input iterator. */
export class MapIter extends DiasporeIterBase {
  kindName = "map";
  I: Interp;
  fn: Value;
  its: DiasporeIterBase[];
  done = false;
  constructor(I: Interp, fn: Value, its: DiasporeIterBase[]) {
    super();
    this.I = I;
    this.fn = fn;
    this.its = its;
  }
  *next(): G<[Value, boolean]> {
    if (this.done) return [NONE, false];
    const args: Value[] = [];
    for (const sub of this.its) {
      const [x, ok] = yield* sub.next();
      if (!ok) {
        this.done = true;
        return [NONE, false];
      }
      args.push(x);
    }
    return [yield* this.I.invoke(this.fn, args, new Map()), true];
  }
}

/** Yield values accepted by a predicate or their own truth value. */
export class FilterIter extends DiasporeIterBase {
  kindName = "filter";
  I: Interp;
  fn: Value | null;
  it: DiasporeIterBase;
  done = false;
  constructor(I: Interp, fn: Value | null, it: DiasporeIterBase) {
    super();
    this.I = I;
    this.fn = fn;
    this.it = it;
  }
  *next(): G<[Value, boolean]> {
    if (this.done) return [NONE, false];
    for (;;) {
      const [x, ok] = yield* this.it.next();
      if (!ok) {
        this.done = true;
        return [NONE, false];
      }
      let keep: Value = x;
      if (this.fn !== null) keep = yield* this.I.invoke(this.fn, [x], new Map());
      if (yield* this.I.truth(keep)) return [x, true];
    }
  }
}

/** Combine input iterators until the first one is exhausted. */
export class ZipIter extends DiasporeIterBase {
  kindName = "zip";
  its: DiasporeIterBase[];
  done = false;
  constructor(its: DiasporeIterBase[]) {
    super();
    this.its = its;
  }
  *next(): G<[Value, boolean]> {
    if (this.done || this.its.length === 0) {
      this.done = true;
      return [NONE, false];
    }
    const row: Value[] = [];
    for (const sub of this.its) {
      const [x, ok] = yield* sub.next();
      if (!ok) {
        this.done = true;
        return [NONE, false];
      }
      row.push(x);
    }
    return [new DiasporeTuple(row), true];
  }
}

/** Pair iterator values with an increasing integer index. */
export class EnumIter extends DiasporeIterBase {
  kindName = "enumerate";
  it: DiasporeIterBase;
  n: bigint;
  constructor(it: DiasporeIterBase, n: bigint) {
    super();
    this.it = it;
    this.n = n;
  }
  *next(): G<[Value, boolean]> {
    const [x, ok] = yield* this.it.next();
    if (!ok) return [NONE, false];
    const v = new DiasporeTuple([this.n, x]);
    this.n += 1n;
    return [v, true];
  }
}

// --------------------------------------------------------------------------
// generators

type GenToken =
  | { kind: "send"; v: Value }
  | { kind: "throw"; exc: Value };

/**
 * A running diaspore generator: holds a paused JS generator executing the body
 * (the interpreter's yield-through chain delivers diaspore `yield`s here).
 * send() resumes the pending yield with a value; throw()/close() inject an
 * exception at the yield point (JS generator .throw maps directly onto
 * CPython's injection semantics, including PEP 380 delegation, since the
 * yield-from loop's own yield is the innermost suspended point). A generator
 * abandoned before exhaustion never runs pending finally blocks (spec 7).
 */
export class DiasporeGenerator extends DiasporeIterBase {
  kindName = "generator";
  fn: DiasporeFunction;
  it: G<Value>;
  frame: Frame;
  runtime: Interp;
  started = false;
  done = false;
  constructor(I: Interp, fn: DiasporeFunction, frame: Frame) {
    super();
    this.fn = fn;
    this.kindName = fn.asyncgen ? "async_generator" : fn.coro ? "coroutine" : "generator";
    this.it = genBody(I, fn, frame);
    this.frame = frame;
    this.runtime = I;
  }

  advance(token: GenToken): [Value, boolean] {
    if (this.done) {
      this.retval = NONE;
      return [NONE, false];
    }
    this.started = true;
    let res: IteratorResult<{ yv: Value }, Value>;
    this.frame.back = this.runtime.frames.at(-1) ?? null;
    this.runtime.frames.push(this.frame);
    try {
      if (token.kind === "send") {
        res = this.it.next(token.v);
      } else {
        res = this.it.throw(new DiasporeError(token.exc));
      }
    } catch (e) {
      this.done = true;
      throw e;
    } finally {
      this.runtime.frames.pop();
    }
    if (res.done) {
      this.done = true;
      this.retval = res.value;
      return [NONE, false];
    }
    return [res.value.yv, true];
  }

  *next(): G<[Value, boolean]> {
    return this.advance({ kind: "send", v: NONE });
  }

  sendValue(v: Value): [Value, boolean] {
    if (!this.started && !(v instanceof PyNoneT)) {
      throw diasporeErr("TypeError", "can't send non-None value to a just-started generator");
    }
    return this.advance({ kind: "send", v });
  }

  throwValue(exc: Value): [Value, boolean] {
    return this.advance({ kind: "throw", exc });
  }

  close(): void {
    if (this.done || !this.started) {
      this.done = true;
      return;
    }
    const ge = makeExc("GeneratorExit");
    let stillYielding: boolean;
    try {
      [, stillYielding] = this.advance({ kind: "throw", exc: ge });
    } catch (e) {
      if (e instanceof DiasporeError && (e.value === ge || excIsA(e.value, "GeneratorExit"))) {
        return;
      }
      throw e;
    }
    if (stillYielding) {
      throw diasporeErr("RuntimeError", "generator ignored GeneratorExit");
    }
  }
}

function* genBody(I: Interp, fn: DiasporeFunction, frame: Frame): G<Value> {
  try {
    yield* I.execBlock(fn.node.body as unknown[], frame);
    return NONE;
  } catch (e) {
    if (e instanceof ReturnSig) return e.value;
    throw e;
  }
}

// --------------------------------------------------------------------------
// protocol entry points

/** Obtain an iterator, including user-defined iteration methods. */
export function* getIter(I: Interp, v: Value): G<DiasporeIterBase> {
  if (v instanceof DiasporeIterBase) return v;
  if (v instanceof DiasporeList) return new ListIter(v);
  if (v instanceof DiasporeTuple) return new SeqIter(v.items, "tuple_iterator");
  if (isPyString(v)) return new StrIter(v);
  if (v instanceof Uint8Array) return new BytesIter(v);
  if (v instanceof DiasporeRange) return new RangeIter(v);
  if (v instanceof DiasporeDictView) return new DictViewIter(v);
  if (v instanceof DiasporeDict) return new DictViewIter(new DiasporeDictView(v, "keys"));
  if (v instanceof DiasporeSet) return new SetIter(v);
  if (v instanceof NativeObject) {
    throw diasporeErr("TypeError", `'${v.kind}' object is not iterable`);
  }
  if (v instanceof DiasporeInstance || v instanceof DiasporeClass) {
    const method = yield* lookupSpecial(I, v, "__iter__");
    if (method !== undefined) {
      if (method instanceof PyNoneT) throw diasporeErr("TypeError", `'${typeName(v)}' object is not iterable`);
      const it = yield* I.invoke(method, [], new Map());
      if (it instanceof DiasporeIterBase) return it;
      const cls = it instanceof DiasporeInstance ? it.cls :
        it instanceof DiasporeClass ? it.metaclass : null;
      if (cls instanceof DiasporeClass && cls.lookup("__next__") !== undefined) {
        return new InstIter(I, it as DiasporeInstance | DiasporeClass);
      }
      throw diasporeErr("TypeError", `iter() returned non-iterator of type '${typeName(it)}'`);
    }
    if (v instanceof DiasporeInstance && v.prim !== null) {
      const primitive = v.prim;
      if (isPyString(primitive) || primitive instanceof Uint8Array ||
          primitive instanceof DiasporeList || primitive instanceof DiasporeTuple ||
          primitive instanceof DiasporeDict || primitive instanceof DiasporeSet ||
          primitive instanceof DiasporeRange) return yield* getIter(I, primitive);
    }
    const cls = v instanceof DiasporeInstance ? v.cls : v.metaclass;
    if (cls instanceof DiasporeClass && cls.lookup("__getitem__") !== undefined) {
      return new ItemIter(I, v);
    }
  }
  throw diasporeErr("TypeError", `'${typeName(v)}' object is not iterable`);
}

/** Materialize an iterable as an array of sandbox values. */
export function* iterToArray(I: Interp, v: Value): G<Value[]> {
  if (v instanceof DiasporeList) return [...v.items];
  if (v instanceof DiasporeTuple) return [...v.items];
  const it = yield* getIter(I, v);
  const out: Value[] = [];
  for (;;) {
    const [x, ok] = yield* it.next();
    if (!ok) return out;
    out.push(x);
  }
}
