/* Native ABC callbacks preserve their caller's sandbox frame. */

import {
  type G, type Value, BuiltinType, DiasporeClass, DiasporeDict,
  DiasporeFrozenSet, DiasporeList, DiasporeSet, DiasporeTuple,
  NativeModule, NativeObject, NONE, NOT_IMPLEMENTED, diasporeErr, objId,
} from "./values.ts";
import { bi } from "./builtins.ts";
import { argCount, noKw } from "./helpers.ts";
import { iterToArray } from "./iter.ts";
import type { Interp } from "./interp.ts";

type ABCClass = DiasporeClass | BuiltinType;
const collectionFlags = new WeakMap<ABCClass, number>();

/** Return the native sequence/mapping flags inherited or registered on a class. */
export function abcCollectionFlags(cls: Value): number {
  if (!(cls instanceof DiasporeClass || cls instanceof BuiltinType)) return 0;
  const direct = collectionFlags.get(cls);
  if (direct !== undefined) return direct;
  if (cls instanceof DiasporeClass) {
    for (const base of cls.mro) {
      const flags = collectionFlags.get(base);
      if (flags !== undefined) return flags;
    }
    if (cls.prim !== null) return collectionFlags.get(cls.prim) ?? 0;
  }
  return 0;
}

class WeakClassSet {
  readonly references = new Map<number, WeakRef<ABCClass>>();

  has(value: Value): boolean {
    if (!(value instanceof DiasporeClass || value instanceof BuiltinType)) return false;
    return this.references.get(objId(value))?.deref() === value;
  }

  add(value: ABCClass): void {
    const id = objId(value);
    if (!this.references.has(id)) this.references.set(id, new WeakRef(value));
  }

  *values(): IterableIterator<ABCClass> {
    for (const [id, reference] of this.references) {
      const value = reference.deref();
      if (value === undefined) this.references.delete(id);
      else yield value;
    }
  }
}

class ABCData extends NativeObject {
  readonly registry = new WeakClassSet();
  readonly positive = new WeakClassSet();
  readonly negative = new WeakClassSet();
  version: bigint;

  constructor(version: bigint) {
    super("_abc._abc_data", new Map());
    this.version = version;
  }
}

/** Build the interpreter-local CPython _abc accelerator and invalidation token. */
export function makeABCModule(I: Interp): NativeModule {
  let token = 0n;
  const ns = new Map<string, Value>();
  const getattr = I.builtins.get("getattr")!;
  const issubclass = I.builtins.get("issubclass")!;
  I.types.set("_abc._abc_data", new BuiltinType("_abc_data", function* (): G<Value> {
    return new ABCData(token);
  }));

  function* data(cls: Value): G<ABCData> {
    const value = yield* I.getAttr(cls, "_abc_impl");
    if (!(value instanceof ABCData)) throw diasporeErr("TypeError", "_abc_impl is set to a wrong type");
    return value;
  }

  function* abstract(value: Value): G<boolean> {
    return yield* I.truth(yield* I.invoke(getattr, [value, "__isabstractmethod__", false], new Map()));
  }

  ns.set("_abc_init", bi("_abc_init", function* (_I, args, kw): G<Value> {
    noKw("_abc_init", kw);
    argCount("_abc_init", args, 1, 1);
    const cls = args[0];
    const namespace = yield* I.getAttr(cls, "__dict__");
    const items = namespace instanceof DiasporeDict
      ? namespace.entries()
      : (yield* iterToArray(I, yield* I.invoke(yield* I.getAttr(namespace, "items"), [], new Map())));
    const names = new DiasporeSet();
    for (const item of items) {
      const pair = Array.isArray(item) ? item : yield* iterToArray(I, item);
      if (pair.length !== 2) throw diasporeErr("TypeError", "items() returned item which size is not 2");
      if (yield* abstract(pair[1])) names.add(pair[0]);
    }
    const bases = yield* I.getAttr(cls, "__bases__");
    if (!(bases instanceof DiasporeTuple)) throw diasporeErr("TypeError", "__bases__ is not tuple");
    for (const base of bases.items) {
      const inherited = yield* I.invoke(getattr, [base, "__abstractmethods__", new DiasporeTuple()], new Map());
      for (const name of yield* iterToArray(I, inherited)) {
        const value = yield* I.invoke(getattr, [cls, name, NONE], new Map());
        if (yield* abstract(value)) names.add(name);
      }
    }
    yield* I.setAttr(cls, "__abstractmethods__", new DiasporeFrozenSet(names.map));
    yield* I.setAttr(cls, "_abc_impl", new ABCData(token));
    const classNS = cls instanceof DiasporeClass ? cls.ns : cls instanceof BuiltinType ? cls.namespace : null;
    if (classNS?.has("__abc_tpflags__")) {
      const flags = classNS.get("__abc_tpflags__")!;
      if (typeof flags === "bigint") {
        if (flags < -9223372036854775808n || flags > 9223372036854775807n) {
          throw diasporeErr("OverflowError", "Python int too large to convert to C long");
        }
        if ((flags & 96n) === 96n) {
          throw diasporeErr("TypeError", "__abc_tpflags__ cannot be both Py_TPFLAGS_SEQUENCE and Py_TPFLAGS_MAPPING");
        }
        collectionFlags.set(cls as ABCClass, abcCollectionFlags(cls) | Number(flags & 96n));
      }
      classNS.delete("__abc_tpflags__");
    }
    return NONE;
  }));

  ns.set("_abc_register", bi("_abc_register", function* (_I, args, kw): G<Value> {
    noKw("_abc_register", kw);
    argCount("_abc_register", args, 2, 2);
    const [cls, subclass] = args;
    if (!(subclass instanceof DiasporeClass || subclass instanceof BuiltinType)) {
      throw diasporeErr("TypeError", "Can only register classes");
    }
    if (yield* I.truth(yield* I.invoke(issubclass, [subclass, cls], new Map()))) return subclass;
    if (yield* I.truth(yield* I.invoke(issubclass, [cls, subclass], new Map()))) {
      throw diasporeErr("RuntimeError", "Refusing to create an inheritance cycle");
    }
    (yield* data(cls)).registry.add(subclass);
    token++;
    const flags = abcCollectionFlags(cls);
    if (flags !== 0) {
      const pending: ABCClass[] = [subclass];
      while (pending.length) {
        const current = pending.pop()!;
        if (collectionFlags.get(current) === flags) continue;
        collectionFlags.set(current, flags);
        for (const child of I.classes) {
          if (child.declaredBases.includes(current)) pending.push(child);
        }
      }
    }
    return subclass;
  }));

  ns.set("_abc_instancecheck", bi("_abc_instancecheck", function* (_I, args, kw): G<Value> {
    noKw("_abc_instancecheck", kw);
    argCount("_abc_instancecheck", args, 2, 2);
    const [cls, instance] = args;
    const state = yield* data(cls);
    const subclass = yield* I.getAttr(instance, "__class__");
    if (state.positive.has(subclass)) return true;
    const subtype = yield* I.invoke(I.types.get("type")!, [instance], new Map());
    if (subtype === subclass && state.version === token && state.negative.has(subclass)) return false;
    const check = yield* I.getAttr(cls, "__subclasscheck__");
    const result = yield* I.invoke(check, [subclass], new Map());
    if (subtype === subclass || (yield* I.truth(result))) return result;
    return yield* I.invoke(check, [subtype], new Map());
  }));

  ns.set("_abc_subclasscheck", bi("_abc_subclasscheck", function* (_I, args, kw): G<Value> {
    noKw("_abc_subclasscheck", kw);
    argCount("_abc_subclasscheck", args, 2, 2);
    const [cls, subclass] = args;
    if (!(subclass instanceof DiasporeClass || subclass instanceof BuiltinType)) {
      throw diasporeErr("TypeError", "issubclass() arg 1 must be a class");
    }
    const state = yield* data(cls);
    if (state.positive.has(subclass)) return true;
    if (state.version < token) {
      state.negative.references.clear();
      state.version = token;
    } else if (state.negative.has(subclass)) return false;
    const result = yield* I.invoke(yield* I.getAttr(cls, "__subclasshook__"), [subclass], new Map());
    if (result === true) {
      state.positive.add(subclass);
      return true;
    }
    if (result === false) {
      state.negative.add(subclass);
      return false;
    }
    if (result !== NOT_IMPLEMENTED) {
      throw diasporeErr("AssertionError", "__subclasshook__ must return either False, True, or NotImplemented");
    }
    const mro = yield* I.getAttr(subclass, "__mro__");
    if ((yield* iterToArray(I, mro)).includes(cls)) {
      state.positive.add(subclass);
      return true;
    }
    if (state.registry.has(subclass)) return true;
    // Snapshot the registry because subclass hooks can register more classes.
    for (const registered of [...state.registry.values()]) {
      if (yield* I.truth(yield* I.invoke(issubclass, [subclass, registered], new Map()))) {
        state.positive.add(subclass);
        return true;
      }
    }
    const children = yield* I.invoke(yield* I.getAttr(cls, "__subclasses__"), [], new Map());
    if (!(children instanceof DiasporeList)) throw diasporeErr("TypeError", "__subclasses__() must return a list");
    for (const child of children.items) {
      if (yield* I.truth(yield* I.invoke(issubclass, [subclass, child], new Map()))) {
        state.positive.add(subclass);
        return true;
      }
    }
    state.negative.add(subclass);
    return false;
  }));

  ns.set("_get_dump", bi("_get_dump", function* (_I, args, kw): G<Value> {
    noKw("_get_dump", kw);
    argCount("_get_dump", args, 1, 1);
    const state = yield* data(args[0]);
    const ref = yield* I.getAttr(yield* I.importModule("_weakref"), "ref");
    const sets: Value[] = [];
    for (const cache of [state.registry, state.positive, state.negative]) {
      const references = new DiasporeSet();
      for (const value of cache.values()) references.add(yield* I.invoke(ref, [value], new Map()));
      sets.push(references);
    }
    return new DiasporeTuple([...sets, state.version]);
  }));

  for (const name of ["_reset_registry", "_reset_caches"]) {
    ns.set(name, bi(name, function* (_I, args, kw): G<Value> {
      noKw(name, kw);
      argCount(name, args, 1, 1);
      const state = yield* data(args[0]);
      if (name === "_reset_registry") state.registry.references.clear();
      else {
        state.positive.references.clear();
        state.negative.references.clear();
      }
      return NONE;
    }));
  }
  ns.set("get_cache_token", bi("get_cache_token", function* (_I, args, kw): G<Value> {
    noKw("get_cache_token", kw);
    argCount("get_cache_token", args, 0, 0);
    return token;
  }));
  return new NativeModule("_abc", ns);
}
