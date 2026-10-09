/* Iterators and generators. Every iterator's next() is a JS generator so it
 * can call back into diaspore code (map/filter functions, __next__ methods). */

import {
  type Value,
  type G,
  NONE,
  PyNoneT,
  makeExc,
  DiasporeIterBase,
  DiasporeList,
  DiasporeTuple,
  DiasporeDict,
  DiasporeSet,
  DiasporeRange,
  DiasporeInstance,
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

/** Iterate Unicode code points rather than UTF-16 code units. */
export class StrIter extends DiasporeIterBase {
  kindName = "str_iterator";
  rs: string[];
  i = 0;
  constructor(s: string) {
    super();
    this.rs = chars(s).slice();
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

/** Iterate a range without materializing its values. */
export class RangeIter extends DiasporeIterBase {
  kindName = "range_iterator";
  cur: bigint;
  step: bigint;
  left: bigint;
  constructor(r: DiasporeRange) {
    super();
    this.cur = r.start;
    this.step = r.step;
    this.left = r.len();
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
  obj: DiasporeInstance;
  done = false;
  constructor(I: Interp, obj: DiasporeInstance) {
    super();
    this.I = I;
    this.obj = obj;
  }
  *next(): G<[Value, boolean]> {
    if (this.done) return [NONE, false];
    try {
      const v = yield* this.I.callDunder(this.obj, "__next__", []);
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
  started = false;
  done = false;
  constructor(I: Interp, fn: DiasporeFunction, frame: Frame) {
    super();
    this.fn = fn;
    this.it = genBody(I, fn, frame);
  }

  advance(token: GenToken): [Value, boolean] {
    if (this.done) {
      this.retval = NONE;
      return [NONE, false];
    }
    this.started = true;
    let res: IteratorResult<{ yv: Value }, Value>;
    try {
      if (token.kind === "send") {
        res = this.it.next(token.v);
      } else {
        res = this.it.throw(new DiasporeError(token.exc));
      }
    } catch (e) {
      this.done = true;
      throw e;
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
  if (v instanceof DiasporeTuple) return new SeqIter(v.items);
  if (typeof v === "string") return new StrIter(v);
  if (v instanceof Uint8Array) return new BytesIter(v);
  if (v instanceof DiasporeRange) return new RangeIter(v);
  if (v instanceof DiasporeDict) return new SeqIter(v.keys());
  if (v instanceof DiasporeSet) return new SeqIter(v.elems());
  if (v instanceof NativeObject) {
    throw diasporeErr("TypeError", `'${v.kind}' object is not iterable`);
  }
  if (v instanceof DiasporeInstance) {
    if (v.cls.lookup("__iter__") !== undefined) {
      const it = yield* I.callDunder(v, "__iter__", []);
      if (it instanceof DiasporeIterBase) return it;
      if (it instanceof DiasporeInstance) return new InstIter(I, it);
      return yield* getIter(I, it);
    }
    if (v.cls.lookup("__next__") !== undefined) {
      return new InstIter(I, v);
    }
    if (v.prim !== null) return yield* getIter(I, v.prim);
    throw diasporeErr("TypeError", `'${v.cls.name}' object is not iterable`);
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
