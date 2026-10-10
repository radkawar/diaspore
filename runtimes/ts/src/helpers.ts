/* Small argument-checking helpers shared by builtins, methods, and natives. */

import {
  type Value,
  type G,
  type KwArgs,
  type PyString,
  isPyString,
  DiasporeList,
  DiasporeTuple,
  DiasporeDict,
  DiasporeSet,
  DiasporeRange,
  DiasporeClass,
  DiasporeInstance,
  Property,
  asIndex,
  typeName,
  diasporeErr,
  strLen,
} from "./values.ts";
import type { Interp } from "./interp.ts";

/** Bind a special method on an object's type, bypassing receiver attributes. */
export function* lookupSpecial(I: Interp, value: Value, name: string): G<Value | undefined> {
  const cls = value instanceof DiasporeInstance ? value.cls :
    value instanceof DiasporeClass ? value.metaclass : null;
  if (!(cls instanceof DiasporeClass)) return undefined;
  let method = cls.lookup(name);
  if (method instanceof DiasporeInstance && method.prim instanceof Property) method = method.prim;
  if (method instanceof Property) {
    if (method.fget === null) throw diasporeErr("AttributeError", `property '${name}' has no getter`);
    return yield* I.invoke(I.bindAttr(method.fget, value, cls), [], new Map());
  }
  if (method instanceof DiasporeInstance && method.cls.lookup("__get__") !== undefined) {
    return yield* I.callDunder(method, "__get__", [value, cls]);
  }
  return method === undefined ? undefined : I.bindAttr(method, value, cls);
}

/** Select the singular or plural spelling for an argument count. */
export function plural(n: number, s: string): string {
  return n === 1 ? s : s + "s";
}

/** Enforce positional arity using function-call diagnostics. */
export function argCount(name: string, args: Value[], min: number, max: number): void {
  if (args.length < min || (max >= 0 && args.length > max)) {
    if (min === max) {
      throw diasporeErr(
        "TypeError",
        `${name}() takes exactly ${min} argument${plural(min, "")} (${args.length} given)`,
      );
    }
    throw diasporeErr("TypeError", `${name} expected at most ${max} arguments, got ${args.length}`);
  }
}

/** Enforce positional arity using builtin count diagnostics. */
export function needArgs(name: string, args: Value[], min: number, max: number): void {
  if (args.length < min) {
    throw diasporeErr("TypeError", `${name} expected at least ${min} arguments, got ${args.length}`);
  }
  if (max >= 0 && args.length > max) {
    throw diasporeErr("TypeError", `${name} expected at most ${max} arguments, got ${args.length}`);
  }
}

/** Reject keyword arguments for positional-only builtins. */
export function noKw(name: string, kw: KwArgs): void {
  if (kw.size > 0) {
    throw diasporeErr("TypeError", `${name}() takes no keyword arguments`);
  }
}

/** Require an integer-compatible value. */
export function wantInt(what: string, v: Value): bigint {
  const n = asIndex(v);
  if (n === null) {
    throw diasporeErr("TypeError", `${what} must be an integer, not '${typeName(v)}'`);
  }
  return n;
}

/** Require Python text without discarding explicit surrogate boundaries. */
export function wantStr(what: string, v: Value): PyString {
  if (isPyString(v)) return v;
  throw diasporeErr("TypeError", `${what} must be str, not ${typeName(v)}`);
}

/** Convert an integer-compatible index to a host number. */
export function wantIndexNum(what: string, v: Value): number {
  return Number(wantInt(what, v));
}

/** Charge fuel for the length of a sized container before a bulk host-speed
 * operation consumes it (mirrors diaspore-py's _charge_sized). */
export function chargeSized(I: Interp, v: Value): void {
  let n = -1;
  if (isPyString(v)) n = strLen(v);
  else if (v instanceof Uint8Array) n = v.length;
  else if (v instanceof DiasporeList || v instanceof DiasporeTuple) n = v.items.length;
  else if (v instanceof DiasporeDict || v instanceof DiasporeSet) n = v.size;
  else if (v instanceof DiasporeRange) n = Number(v.len());
  if (n > 0) I.fuelN(n);
}
