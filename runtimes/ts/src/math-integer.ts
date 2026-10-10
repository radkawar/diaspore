/* Exact integer mathematics and protocol-preserving sequential products. */

import { type G, type Value, PyNoneT, diasporeErr } from "./values.ts";
import { bi } from "./builtins.ts";
import { indexInteger } from "./buffers.ts";
import { noKw } from "./helpers.ts";
import { getIter } from "./iter.ts";
import type { Interp } from "./interp.ts";

const longMax = (1n << 63n) - 1n;

function absolute(value: bigint): bigint {
  return value < 0n ? -value : value;
}

function gcd(I: Interp, left: bigint, right: bigint): bigint {
  left = absolute(left);
  right = absolute(right);
  if (left === 0n) return right;
  if (left === 1n || right === 1n) return 1n;
  while (right !== 0n) {
    I.fuel();
    const remainder = left % right;
    left = right;
    right = remainder;
  }
  return left;
}

// Balanced products avoid repeatedly multiplying a large result by tiny factors.
function productRange(I: Interp, first: bigint, last: bigint): bigint {
  if (last - first < 32n) {
    let result = 1n;
    for (let value = first; value <= last; value++) {
      I.fuel();
      result *= value;
    }
    return result;
  }
  const middle = (first + last) >> 1n;
  const left = productRange(I, first, middle);
  const right = productRange(I, middle + 1n, last);
  I.fuel();
  return left * right;
}

function* factorial(I: Interp, value: Value): G<bigint> {
  const n = yield* indexInteger(I, value);
  if (n < 0n) throw diasporeErr("ValueError", "factorial() not defined for negative values");
  if (n > longMax) throw diasporeErr("OverflowError", `factorial() argument should not exceed ${longMax}`);
  return productRange(I, 2n, n);
}

function positional(name: string, args: Value[], minimum: number, maximum: number): void {
  if (args.length >= minimum && args.length <= maximum) return;
  const count = args.length < minimum ? minimum : maximum;
  const bound = minimum === maximum ? "" : args.length < minimum ? "at least " : "at most ";
  throw diasporeErr("TypeError", `${name} expected ${bound}${count} argument${count === 1 ? "" : "s"}, got ${args.length}`);
}

/** Construct math's exact integer operations and generic sequential product. */
export function mathIntegerEntries(): [string, Value][] {
  return [
    ["factorial", bi("factorial", function* (I, args, kw): G<Value> {
      noKw("math.factorial", kw);
      if (args.length !== 1) {
        throw diasporeErr("TypeError", `math.factorial() takes exactly one argument (${args.length} given)`);
      }
      return yield* factorial(I, args[0]);
    })],
    ["gcd", bi("gcd", function* (I, args, kw): G<Value> {
      noKw("math.gcd", kw);
      let result = 0n;
      for (const value of args) {
        I.fuel();
        const integer = yield* indexInteger(I, value);
        if (result !== 1n) result = gcd(I, result, integer);
      }
      return result;
    })],
    ["lcm", bi("lcm", function* (I, args, kw): G<Value> {
      noKw("math.lcm", kw);
      let result = 1n;
      for (const value of args) {
        I.fuel();
        const integer = yield* indexInteger(I, value);
        if (result === 0n) continue;
        if (integer === 0n) { result = 0n; continue; }
        result = absolute((result / gcd(I, result, integer)) * integer);
      }
      return result;
    })],
    ["comb", bi("comb", function* (I, args, kw): G<Value> {
      noKw("math.comb", kw);
      positional("comb", args, 2, 2);
      const n = yield* indexInteger(I, args[0]);
      let k = yield* indexInteger(I, args[1]);
      if (n < 0n) throw diasporeErr("ValueError", "n must be a non-negative integer");
      if (k < 0n) throw diasporeErr("ValueError", "k must be a non-negative integer");
      if (k > n) return 0n;
      if (n - k < k) k = n - k;
      if (k > longMax) throw diasporeErr("OverflowError", `min(n - k, k) must not exceed ${longMax}`);
      const first = n - k;
      let result = 1n;
      for (let index = 1n; index <= k; index++) {
        I.fuel();
        result = (result * (first + index)) / index;
      }
      return result;
    })],
    ["perm", bi("perm", function* (I, args, kw): G<Value> {
      noKw("math.perm", kw);
      positional("perm", args, 1, 2);
      if (args.length === 1 || args[1] instanceof PyNoneT) return yield* factorial(I, args[0]);
      const n = yield* indexInteger(I, args[0]);
      const k = yield* indexInteger(I, args[1]);
      if (n < 0n) throw diasporeErr("ValueError", "n must be a non-negative integer");
      if (k < 0n) throw diasporeErr("ValueError", "k must be a non-negative integer");
      if (k > n) return 0n;
      if (k > longMax) throw diasporeErr("OverflowError", `k must not exceed ${longMax}`);
      return productRange(I, n - k + 1n, n);
    })],
    ["prod", bi("prod", function* (I, args, kw): G<Value> {
      if (args.length !== 1) {
        throw diasporeErr("TypeError", `prod() takes exactly 1 positional argument (${args.length} given)`);
      }
      for (const name of kw.keys()) {
        if (name !== "start") throw diasporeErr("TypeError", `'${name}' is an invalid keyword argument for prod()`);
      }
      const iterator = yield* getIter(I, args[0]);
      let result: Value = kw.get("start") ?? 1n;
      for (;;) {
        I.fuel();
        const [item, present] = yield* iterator.next();
        if (!present) return result;
        if (typeof result === "bigint" && typeof item === "bigint") result *= item;
        else if (typeof result === "number" && typeof item === "number") result *= item;
        else result = yield* I.binop("mul", result, item);
      }
    })],
  ];
}
