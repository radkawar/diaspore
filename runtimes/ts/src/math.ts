/* Native real mathematics with Python conversion and IEEE-754 boundaries. */
import {
  type Value, type G, type KwArgs, NativeModule, Builtin, DiasporeTuple, DiasporeInstance,
  PyNoneT, asIndex, diasporeErr, typeName,
} from "./values.ts";
import type { Interp } from "./interp.ts";
import { bi } from "./builtins.ts";
import { argCount, noKw, lookupSpecial } from "./helpers.ts";
import { indexInteger } from "./buffers.ts";
import { mathFloat } from "./math-common.ts";
import { mathIntegerEntries } from "./math-integer.ts";
import { mathSpecialEntries } from "./math-special.ts";

const bits = new DataView(new ArrayBuffer(8));
const signBit = 1n << 63n;
const magnitudeMask = signBit - 1n;

function unary(name: string, operation: (x: number) => number, overflow = true): Builtin {
  return bi(name, function* (I, args, kw): G<Value> {
    noKw(name, kw);
    argCount(name, args, 1, 1);
    const x = yield* mathFloat(I, args[0]);
    const result = operation(x);
    if (Number.isNaN(result) && !Number.isNaN(x)) throw diasporeErr("ValueError", "math domain error");
    if (overflow && !Number.isFinite(result) && !Number.isNaN(result) && Number.isFinite(x)) {
      throw diasporeErr("OverflowError", "math range error");
    }
    return result;
  });
}

function binary(name: string, operation: (x: number, y: number) => number): Builtin {
  return bi(name, function* (I, args, kw): G<Value> {
    noKw(name, kw);
    argCount(name, args, 2, 2);
    const x = yield* mathFloat(I, args[0]);
    const y = yield* mathFloat(I, args[1]);
    const result = operation(x, y);
    if (Number.isNaN(result) && !Number.isNaN(x) && !Number.isNaN(y)) {
      throw diasporeErr("ValueError", "math domain error");
    }
    if (!Number.isFinite(result) && !Number.isNaN(result) && Number.isFinite(x) && Number.isFinite(y)) {
      throw diasporeErr("OverflowError", "math range error");
    }
    return result;
  });
}

function integerRound(name: "floor" | "ceil" | "trunc"): Builtin {
  return bi(name, function* (I, args, kw): G<Value> {
    noKw(name, kw);
    argCount(name, args, 1, 1);
    const value = args[0];
    if (typeof value !== "number") {
      const method = yield* lookupSpecial(I, value, `__${name}__`);
      if (method !== undefined) return yield* I.invoke(method, [], new Map());
      const primitive = value instanceof DiasporeInstance ? value.prim : value;
      if (primitive !== null) {
        const integer = asIndex(primitive);
        if (integer !== null) return integer;
      }
      if (name === "trunc" && typeof primitive !== "number") {
        throw diasporeErr("TypeError", `type ${typeName(value)} doesn't define __trunc__ method`);
      }
    }
    const x = yield* mathFloat(I, value);
    if (Number.isNaN(x)) throw diasporeErr("ValueError", "cannot convert float NaN to integer");
    if (!Number.isFinite(x)) throw diasporeErr("OverflowError", "cannot convert float infinity to integer");
    return BigInt(name === "floor" ? Math.floor(x) : name === "ceil" ? Math.ceil(x) : Math.trunc(x));
  });
}

function* logarithm(I: Interp, value: Value, base: "log" | "log2" | "log10"): G<number> {
  const primitive = value instanceof DiasporeInstance ? value.prim : value;
  const integer = primitive === null ? null : asIndex(primitive);
  const operation = base === "log" ? Math.log : base === "log2" ? Math.log2 : Math.log10;
  if (integer !== null) {
    if (integer <= 0n) throw diasporeErr("ValueError", "math domain error");
    const converted = Number(integer);
    if (Number.isFinite(converted)) return operation(converted);
    const shift = integer.toString(2).length - 53;
    let leading = integer >> BigInt(shift);
    const remainder = integer - (leading << BigInt(shift));
    const half = 1n << BigInt(shift - 1);
    if (remainder > half || (remainder === half && (leading & 1n) !== 0n)) leading++;
    return operation(Number(leading)) + shift * (base === "log2" ? 1 : base === "log10" ? Math.LOG10E * Math.LN2 : Math.LN2);
  }
  const x = yield* mathFloat(I, value);
  if (x <= 0) throw diasporeErr("ValueError", "math domain error");
  return operation(x);
}

function nextafter(x: number, y: number, steps: bigint): number {
  if (steps === 0n || Number.isNaN(x)) return x;
  if (Number.isNaN(y)) return y;
  bits.setFloat64(0, x);
  let ux = bits.getBigUint64(0);
  bits.setFloat64(0, y);
  const uy = bits.getBigUint64(0);
  if (ux === uy) return x;
  const ax = ux & magnitudeMask, ay = uy & magnitudeMask;
  if (((ux ^ uy) & signBit) !== 0n) {
    if (ax + ay <= steps) return y;
    ux = ax < steps ? (uy & signBit) | (steps - ax) : ux - steps;
  } else if (ax > ay) {
    if (ax - ay < steps) return y;
    ux -= steps;
  } else {
    if (ay - ax < steps) return y;
    ux += steps;
  }
  bits.setBigUint64(0, ux);
  return bits.getFloat64(0);
}

function remainder(x: number, y: number): number {
  if (Number.isNaN(x)) return x;
  if (Number.isNaN(y)) return y;
  if (!Number.isFinite(x) || y === 0) throw diasporeErr("ValueError", "math domain error");
  if (!Number.isFinite(y)) return x;
  const ax = Math.abs(x), ay = Math.abs(y);
  const m = ax % ay, complement = ay - m;
  const result = m < complement ? m : m > complement ? -complement : m - 2 * ((0.5 * (ax - m)) % ay);
  return x < 0 || Object.is(x, -0) ? -result : result;
}

function* isclose(I: Interp, args: Value[], kw: KwArgs): G<Value> {
  if (args.length > 2) throw diasporeErr("TypeError", `isclose() takes exactly 2 positional arguments (${args.length} given)`);
  for (const name of kw.keys()) {
    if (name !== "a" && name !== "b" && name !== "rel_tol" && name !== "abs_tol") {
      throw diasporeErr("TypeError", `'${name}' is an invalid keyword argument for isclose()`);
    }
  }
  const values: Value[] = [];
  for (const [index, name] of ["a", "b"].entries()) {
    if (index < args.length && kw.has(name)) {
      throw diasporeErr("TypeError", `argument for isclose() given by name ('${name}') and position (${index + 1})`);
    }
    const value = index < args.length ? args[index] : kw.get(name);
    if (value === undefined) throw diasporeErr("TypeError", `isclose() missing required argument '${name}' (pos ${index + 1})`);
    values.push(value);
  }
  const a = yield* mathFloat(I, values[0]), b = yield* mathFloat(I, values[1]);
  const relative = yield* mathFloat(I, kw.get("rel_tol") ?? 1e-9);
  const absolute = yield* mathFloat(I, kw.get("abs_tol") ?? 0);
  if (relative < 0 || absolute < 0) throw diasporeErr("ValueError", "tolerances must be non-negative");
  if (a === b) return true;
  if (Math.abs(a) === Infinity || Math.abs(b) === Infinity) return false;
  const difference = Math.abs(b - a);
  return difference <= Math.abs(relative * b) || difference <= Math.abs(relative * a) || difference <= absolute;
}

/** Construct the native math namespace with real numeric and integer protocols. */
export function makeMathModule(): NativeModule {
  const entries: [string, Value][] = [
    ["__doc__", "This module provides access to the mathematical functions\ndefined by the C standard."],
    ["pi", Math.PI], ["e", Math.E], ["tau", 2 * Math.PI], ["inf", Infinity], ["nan", NaN],
    ["sqrt", unary("sqrt", Math.sqrt)], ["fabs", unary("fabs", Math.abs)],
    ["sin", unary("sin", Math.sin)], ["cos", unary("cos", Math.cos)], ["tan", unary("tan", Math.tan)],
    ["acos", unary("acos", Math.acos)], ["asin", unary("asin", Math.asin)], ["atan", unary("atan", Math.atan)],
    ["atan2", binary("atan2", Math.atan2)],
    ["exp", unary("exp", Math.exp)], ["expm1", unary("expm1", Math.expm1)],
    ["log1p", unary("log1p", x => { if (x <= -1) throw diasporeErr("ValueError", "math domain error"); return Math.log1p(x); })],
    ["degrees", unary("degrees", x => x * (180 / Math.PI), false)],
    ["radians", unary("radians", x => x * (Math.PI / 180))],
    ["copysign", binary("copysign", (x, y) => {
      bits.setFloat64(0, y);
      const sign = bits.getBigUint64(0) & signBit;
      bits.setFloat64(0, x);
      bits.setBigUint64(0, (bits.getBigUint64(0) & magnitudeMask) | sign);
      return bits.getFloat64(0);
    })],
    ["remainder", binary("remainder", remainder)],
    ["fmod", binary("fmod", (x, y) => { if (Number.isFinite(x) && Math.abs(y) === Infinity) return x; return x % y; })],
    ["pow", binary("pow", (x, y) => {
      if (y === 0 || x === 1 || (x === -1 && Math.abs(y) === Infinity)) return 1;
      if (x === 0 && y < 0 && Number.isFinite(y)) throw diasporeErr("ValueError", "math domain error");
      return Math.pow(x, y);
    })],
    ["floor", integerRound("floor")], ["ceil", integerRound("ceil")], ["trunc", integerRound("trunc")],
    ["log", bi("log", function* (I, args, kw): G<Value> {
      noKw("log", kw); argCount("log", args, 1, 2);
      const x = yield* logarithm(I, args[0], "log");
      if (args.length === 1) return x;
      const base = yield* logarithm(I, args[1], "log");
      if (base === 0) throw diasporeErr("ZeroDivisionError", "float division by zero");
      return x / base;
    })],
    ["log2", bi("log2", function* (I, args, kw): G<Value> {
      noKw("log2", kw); argCount("log2", args, 1, 1); return yield* logarithm(I, args[0], "log2");
    })],
    ["log10", bi("log10", function* (I, args, kw): G<Value> {
      noKw("log10", kw); argCount("log10", args, 1, 1); return yield* logarithm(I, args[0], "log10");
    })],
    ["frexp", bi("frexp", function* (I, args, kw): G<Value> {
      noKw("frexp", kw); argCount("frexp", args, 1, 1);
      let x = yield* mathFloat(I, args[0]);
      if (x === 0 || !Number.isFinite(x)) return new DiasporeTuple([x, 0n]);
      let correction = 0;
      if (Math.abs(x) < 2 ** -1022) { x *= 2 ** 54; correction = -54; }
      bits.setFloat64(0, x);
      const encoded = bits.getBigUint64(0);
      const exponent = Number((encoded >> 52n) & 0x7ffn) - 1022 + correction;
      bits.setBigUint64(0, (encoded & 0x800fffffffffffffn) | (1022n << 52n));
      return new DiasporeTuple([bits.getFloat64(0), BigInt(exponent)]);
    })],
    ["ldexp", bi("ldexp", function* (I, args, kw): G<Value> {
      noKw("ldexp", kw); argCount("ldexp", args, 2, 2);
      let x = yield* mathFloat(I, args[0]);
      const exponentValue = args[1] instanceof DiasporeInstance ? args[1].prim : args[1];
      const exponent = exponentValue === null ? null : asIndex(exponentValue);
      if (exponent === null) throw diasporeErr("TypeError", "Expected an int as second argument to ldexp.");
      if (x === 0 || !Number.isFinite(x)) return x;
      if (exponent > 4096n) throw diasporeErr("OverflowError", "math range error");
      if (exponent < -4096n) return x < 0 ? -0 : 0;
      let power = Number(exponent);
      while (power > 1023) { x *= 2 ** 1023; power -= 1023; }
      // Keep intermediate products normal, leaving only the final subnormal rounding.
      while (power < -1022) { x *= 2 ** -969; power += 969; }
      const result = x * 2 ** power;
      if (!Number.isFinite(result)) throw diasporeErr("OverflowError", "math range error");
      return result;
    })],
    ["ulp", unary("ulp", x => {
      x = Math.abs(x);
      if (!Number.isFinite(x)) return x;
      const higher = nextafter(x, Infinity, 1n);
      return higher === Infinity ? x - nextafter(x, -Infinity, 1n) : higher - x;
    })],
    ["nextafter", bi("nextafter", function* (I, args, kw): G<Value> {
      argCount("nextafter", args, 2, 2);
      for (const name of kw.keys()) if (name !== "steps") {
        throw diasporeErr("TypeError", `'${name}' is an invalid keyword argument for nextafter()`);
      }
      const x = yield* mathFloat(I, args[0]), y = yield* mathFloat(I, args[1]);
      const count = kw.get("steps");
      const steps = count === undefined || count instanceof PyNoneT ? 1n : yield* indexInteger(I, count);
      if (steps < 0n) throw diasporeErr("ValueError", "steps must be a non-negative integer");
      return nextafter(x, y, steps);
    })],
    ["isclose", bi("isclose", isclose)],
  ];
  for (const name of ["isfinite", "isnan", "isinf"]) {
    entries.push([name, bi(name, function* (I, args, kw): G<Value> {
      noKw(name, kw); argCount(name, args, 1, 1);
      const x = yield* mathFloat(I, args[0]);
      return name === "isfinite" ? Number.isFinite(x) : name === "isnan" ? Number.isNaN(x) : Math.abs(x) === Infinity;
    })]);
  }
  entries.push(...mathIntegerEntries(), ...mathSpecialEntries());
  return new NativeModule("math", new Map(entries));
}
