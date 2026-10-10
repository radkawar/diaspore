/* Real-number conversion shared by native mathematical functions. */
import { type Value, type G, DiasporeInstance, asIndex, diasporeErr, typeName } from "./values.ts";
import type { Interp } from "./interp.ts";
import { lookupSpecial } from "./helpers.ts";

/** Convert through Python's real-number protocol without accepting numeric text. */
export function* mathFloat(I: Interp, value: Value): G<number> {
  if (typeof value === "number") return value;
  if (value instanceof DiasporeInstance && typeof value.prim === "number") return value.prim;
  let integer = asIndex(value);
  if (integer === null) {
    const floating = yield* lookupSpecial(I, value, "__float__");
    if (floating !== undefined) {
      const result = yield* I.invoke(floating, [], new Map());
      const number = result instanceof DiasporeInstance ? result.prim : result;
      if (typeof number !== "number") {
        throw diasporeErr("TypeError", `${typeName(value)}.__float__ returned non-float (type ${typeName(result)})`);
      }
      return number;
    }
    if (value instanceof DiasporeInstance && value.prim !== null) integer = asIndex(value.prim);
    if (integer === null) {
      const index = yield* lookupSpecial(I, value, "__index__");
      if (index === undefined) throw diasporeErr("TypeError", `must be real number, not ${typeName(value)}`);
      const result = yield* I.invoke(index, [], new Map());
      integer = asIndex(result instanceof DiasporeInstance ? result.prim as Value : result);
      if (integer === null) throw diasporeErr("TypeError", `__index__ returned non-int (type ${typeName(result)})`);
    }
  }
  const number = Number(integer);
  if (!Number.isFinite(number)) throw diasporeErr("OverflowError", "int too large to convert to float");
  return number;
}
