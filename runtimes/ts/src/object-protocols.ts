/* Default object state and reduction protocols from CPython 3.12. */

import {
  type G, type Value, BoundMethod, BuiltinType, Complex, DiasporeClass,
  DiasporeDict, DiasporeError, DiasporeFunction, DiasporeInstance, DiasporeList, DiasporeSet,
  DiasporeNamespace, DiasporeTuple, NONE, NOT_IMPLEMENTED, PyNoneT, PyEllipsisT, PyNotImplementedT, diasporeErr,
  asIndex, excIsA, isPyString, strKey, typeName,
} from "./values.ts";
import { indexInteger } from "./buffers.ts";
import { getIter, iterToArray } from "./iter.ts";
import { instanceHasDict, instanceSlotCount } from "./slots.ts";
import type { Interp } from "./interp.ts";

function raw(value: Value): Value {
  return value instanceof DiasporeInstance && value.prim !== null ? value.prim : value;
}

function* optionalAttr(I: Interp, value: Value, name: string): G<Value | undefined> {
  try {
    return yield* I.getAttr(value, name);
  } catch (error) {
    if (error instanceof DiasporeError && excIsA(error.value, "AttributeError")) return undefined;
    throw error;
  }
}

function* special(I: Interp, value: Value, name: string): G<Value | undefined> {
  if (!(value instanceof DiasporeInstance)) return yield* optionalAttr(I, value, name);
  const descriptor = value.cls.lookup(name);
  if (descriptor === undefined) return undefined;
  if (descriptor instanceof DiasporeInstance && descriptor.cls.lookup("__get__") !== undefined) {
    return yield* I.callDunder(descriptor, "__get__", [value, value.cls]);
  }
  return I.bindAttr(descriptor, value, value.cls);
}

function* slotNames(I: Interp, cls: Value): G<DiasporeList | null> {
  const namespace = cls instanceof DiasporeClass ? cls.ns : cls instanceof BuiltinType ? cls.namespace : null;
  let names = namespace?.get("__slotnames__");
  if (names !== undefined) {
    const unwrapped = raw(names);
    if (unwrapped instanceof PyNoneT) return null;
    if (!(unwrapped instanceof DiasporeList)) {
      const className = yield* I.getAttr(cls, "__name__");
      throw diasporeErr("TypeError", `${yield* I.str(className)}.__slotnames__ should be a list or None, not ${typeName(names)}`);
    }
    return unwrapped;
  }
  const copyreg = yield* I.importModule("copyreg");
  names = yield* I.invoke(yield* I.getAttr(copyreg, "_slotnames"), [cls], new Map());
  const unwrapped = raw(names);
  if (unwrapped instanceof PyNoneT) return null;
  if (!(unwrapped instanceof DiasporeList)) throw diasporeErr("TypeError", "copyreg._slotnames didn't return a list or None");
  return unwrapped;
}

/** Return object.__getstate__ without dispatching to a user override. */
export function* objectGetState(I: Interp, value: Value, required = false): G<Value> {
  const cls = value instanceof DiasporeInstance ? value.cls : yield* I.invoke(I.types.get("type")!, [value], new Map());
  let state: Value = NONE;
  const dictionary = value instanceof DiasporeInstance && instanceHasDict(value.cls) ? value.peekDict() : undefined;
  if (value instanceof DiasporeInstance && dictionary !== undefined && dictionary.size !== 0) {
    state = value.getDict();
  } else if (value instanceof DiasporeFunction && value.attrs.size !== 0) {
    state = new DiasporeNamespace(value.attrs);
  }
  const slots = yield* slotNames(I, cls);
  if (required && cls instanceof DiasporeClass && instanceSlotCount(cls) > (slots?.items.length ?? 0)) {
    throw diasporeErr("TypeError", `cannot pickle '${typeName(value)}' object`);
  }
  if (slots !== null && slots.items.length !== 0) {
    const slotState = new DiasporeDict();
    const count = slots.items.length;
    for (let index = 0; index < count; index++) {
      const name = slots.items[index];
      if (!isPyString(name)) throw diasporeErr("TypeError", `attribute name must be string, not '${typeName(name)}'`);
      const item = yield* optionalAttr(I, value, strKey(name));
      if (item !== undefined) slotState.set(name, item);
      if (slots.items.length !== count) throw diasporeErr("RuntimeError", "__slotsname__ changed size during iteration");
    }
    if (slotState.size !== 0) return new DiasporeTuple([state, slotState]);
  }
  return state;
}

/** Implement object.__reduce_ex__; object.__reduce__ disables override dispatch. */
export function* objectReduce(I: Interp, value: Value, protocol: Value, checkOverride = true): G<Value> {
  const number = yield* indexInteger(I, protocol);
  if (number < -2147483648n || number > 2147483647n) throw diasporeErr("OverflowError", "Python int too large to convert to C int");
  const cls = value instanceof DiasporeInstance ? value.cls : yield* I.invoke(I.types.get("type")!, [value], new Map());
  if (checkOverride) {
    const reduce = yield* optionalAttr(I, value, "__reduce__");
    if (reduce !== undefined) {
      const classReduce = yield* I.getAttr(cls, "__reduce__");
      if (classReduce !== I.builtinUnbound("object", "__reduce__")) return yield* I.invoke(reduce, [], new Map());
    }
  }
  if (number < 2n) {
    const copyreg = yield* I.importModule("copyreg");
    return yield* I.invoke(yield* I.getAttr(copyreg, "_reduce_ex"), [value, number], new Map());
  }

  const primitive = raw(value);
  let args: Value | undefined;
  let kwargs: Value | undefined;
  const getnewargsEx = yield* special(I, value, "__getnewargs_ex__");
  if (getnewargsEx !== undefined) {
    const result = yield* I.invoke(getnewargsEx, [], new Map());
    const pair = raw(result);
    if (!(pair instanceof DiasporeTuple)) throw diasporeErr("TypeError", `__getnewargs_ex__ should return a tuple, not '${typeName(result)}'`);
    if (pair.items.length !== 2) throw diasporeErr("ValueError", `__getnewargs_ex__ should return a tuple of length 2, not ${pair.items.length}`);
    [args, kwargs] = pair.items;
    if (!(raw(args) instanceof DiasporeTuple)) throw diasporeErr("TypeError", `first item of the tuple returned by __getnewargs_ex__ must be a tuple, not '${typeName(args)}'`);
    if (!(raw(kwargs) instanceof DiasporeDict)) throw diasporeErr("TypeError", `second item of the tuple returned by __getnewargs_ex__ must be a dict, not '${typeName(kwargs)}'`);
  } else {
    const getnewargs = yield* special(I, value, "__getnewargs__");
    if (getnewargs !== undefined) {
      args = yield* I.invoke(getnewargs, [], new Map());
      if (!(raw(args) instanceof DiasporeTuple)) throw diasporeErr("TypeError", `__getnewargs__ should return a tuple, not '${typeName(args)}'`);
    } else if (typeof primitive === "bigint" || typeof primitive === "number" || typeof primitive === "boolean" ||
        isPyString(primitive) || primitive instanceof Uint8Array || primitive instanceof DiasporeTuple) {
      args = new DiasporeTuple([typeof primitive === "boolean" ? primitive ? 1n : 0n : primitive]);
    } else if (primitive instanceof Complex) {
      args = new DiasporeTuple([primitive.re, primitive.im]);
    }
  }
  const copyreg = yield* I.importModule("copyreg");
  const keywords = kwargs === undefined ? null : raw(kwargs) as DiasporeDict;
  let constructor: Value;
  let newargs: DiasporeTuple;
  if (keywords === null || keywords.size === 0) {
    constructor = yield* I.getAttr(copyreg, "__newobj__");
    newargs = new DiasporeTuple([cls, ...(args === undefined ? [] : (raw(args) as DiasporeTuple).items)]);
  } else {
    constructor = yield* I.getAttr(copyreg, "__newobj_ex__");
    newargs = new DiasporeTuple([cls, args!, kwargs!]);
  }
  const getstate = yield* I.getAttr(value, "__getstate__");
  const defaultState = getstate instanceof BoundMethod && getstate.recv === value && getstate.fn === I.builtinUnbound("object", "__getstate__");
  const required = args === undefined && !(primitive instanceof DiasporeList || primitive instanceof DiasporeDict);
  if (defaultState && required && !(value instanceof DiasporeInstance && value.prim === null) &&
      !(value instanceof PyNoneT || value instanceof PyEllipsisT || value instanceof PyNotImplementedT)) {
    throw diasporeErr("TypeError", `cannot pickle '${typeName(value)}' object`);
  }
  const state = defaultState ? yield* objectGetState(I, value, required) : yield* I.invoke(getstate, [], new Map());
  const listitems = primitive instanceof DiasporeList ? yield* getIter(I, value) : NONE;
  const dictitems = primitive instanceof DiasporeDict
    ? yield* getIter(I, yield* I.invoke(yield* I.getAttr(value, "items"), [], new Map())) : NONE;
  return new DiasporeTuple([constructor, newargs, state, listitems, dictitems]);
}

/** Reduce a set or frozenset using its actual iteration and state protocols. */
export function* setReduce(I: Interp, value: Value): G<Value> {
  if (!(raw(value) instanceof DiasporeSet)) throw diasporeErr("TypeError", "set reduction requires a set or frozenset");
  const iterator = yield* getIter(I, value);
  // PySequence_List obtains its iterator before evaluating the allocation hint.
  // The hint affects observable callbacks even though no allocation needs it here.
  if (value instanceof DiasporeInstance && value.cls.lookup("__len__") !== undefined) {
    let needsHint = false;
    try {
      yield* I.invoke(I.builtins.get("len")!, [value], new Map());
    } catch (error) {
      if (!(error instanceof DiasporeError && excIsA(error.value, "TypeError"))) throw error;
      needsHint = true;
    }
    if (needsHint) {
      const hint = yield* special(I, value, "__length_hint__");
      let size: Value = NOT_IMPLEMENTED;
      if (hint !== undefined) {
        try {
          size = yield* I.invoke(hint, [], new Map());
        } catch (error) {
          if (!(error instanceof DiasporeError && excIsA(error.value, "TypeError"))) throw error;
        }
      }
      if (size !== NOT_IMPLEMENTED) {
        const number = asIndex(raw(size));
        if (number === null) throw diasporeErr("TypeError", `__length_hint__ must be an integer, not ${typeName(size)}`);
        if (number < -9223372036854775808n || number > 9223372036854775807n) {
          throw diasporeErr("OverflowError", "Python int too large to convert to C ssize_t");
        }
        if (number < 0n) throw diasporeErr("ValueError", "__length_hint__() should return >= 0");
      }
    }
  }
  const items = new DiasporeList(yield* iterToArray(I, iterator));
  const getstate = yield* I.getAttr(value, "__getstate__");
  const state = getstate instanceof BoundMethod && getstate.recv === value && getstate.fn === I.builtinUnbound("object", "__getstate__")
    ? yield* objectGetState(I, value) : yield* I.invoke(getstate, [], new Map());
  const cls = value instanceof DiasporeInstance ? value.cls : yield* I.invoke(I.types.get("type")!, [value], new Map());
  return new DiasporeTuple([cls, new DiasporeTuple([items]), state]);
}
