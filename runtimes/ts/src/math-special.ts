/* CPython 3.12 Lanczos gamma, accurate summation, and vector norm algorithms.
 * Adapted from Modules/mathmodule.c, Python Software Foundation license:
 * src/diaspore/cpython/LICENSE.
 */

import { type Value, type G, DiasporeTuple, DiasporeInstance, diasporeErr } from "./values.ts";
import { bi } from "./builtins.ts";
import { argCount, noKw } from "./helpers.ts";
import { getIter, iterToArray } from "./iter.ts";
import { mathFloat } from "./math-common.ts";
import type { Interp } from "./interp.ts";

const LANCZOS_G = 6.024680040776729583740234375;
const LANCZOS_OFFSET = 5.524680040776729583740234375;
const LANCZOS_NUMERATOR = [
  23531376880.410759688572007674451636754734846804940,
  42919803642.649098768957899047001988850926355848959,
  35711959237.355668049440185451547166705960488635843,
  17921034426.037209699919755754458931112671403265390,
  6039542586.3520280050642916443072979210699388420708,
  1439720407.3117216736632230727949123939715485786772,
  248874557.86205415651146038641322942321632125127801,
  31426415.585400194380614231628318205362874684987640,
  2876370.6289353724412254090516208496135991145378768,
  186056.26539522349504029498971604569928220784236328,
  8071.6720023658162106380029022722506138218516325024,
  210.82427775157934587250973392071336271166969580291,
  2.5066282746310002701649081771338373386264310793408,
];
const LANCZOS_DENOMINATOR = [
  0, 39916800, 120543840, 150917976, 105258076, 45995730,
  13339535, 2637558, 357423, 32670, 1925, 66, 1,
];
const INTEGER_GAMMA = [
  1, 1, 2, 6, 24, 120, 720, 5040, 40320, 362880,
  3628800, 39916800, 479001600, 6227020800, 87178291200,
  1307674368000, 20922789888000, 355687428096000,
  6402373705728000, 121645100408832000, 2432902008176640000,
  51090942171709440000, 1124000727777607680000,
];

function sinPi(value: number): number {
  const reduced = Math.abs(value) % 2;
  let result: number;
  switch (Math.round(2 * reduced)) {
    case 0: result = Math.sin(Math.PI * reduced); break;
    case 1: result = Math.cos(Math.PI * (reduced - 0.5)); break;
    case 2: result = Math.sin(Math.PI * (1 - reduced)); break;
    case 3: result = -Math.cos(Math.PI * (reduced - 1.5)); break;
    default: result = Math.sin(Math.PI * (reduced - 2));
  }
  return value < 0 || Object.is(value, -0) ? -result : result;
}

function lanczosSum(value: number): number {
  let numerator = 0, denominator = 0;
  if (value < 5) {
    for (let index = LANCZOS_NUMERATOR.length - 1; index >= 0; index--) {
      numerator = numerator * value + LANCZOS_NUMERATOR[index];
      denominator = denominator * value + LANCZOS_DENOMINATOR[index];
    }
  } else {
    for (let index = 0; index < LANCZOS_NUMERATOR.length; index++) {
      numerator = numerator / value + LANCZOS_NUMERATOR[index];
      denominator = denominator / value + LANCZOS_DENOMINATOR[index];
    }
  }
  return numerator / denominator;
}

function gamma(value: number): number {
  if (Number.isNaN(value) || value === Infinity) return value;
  if (value === -Infinity || value === 0 || (value < 0 && Number.isInteger(value))) {
    throw diasporeErr("ValueError", "math domain error");
  }
  if (Number.isInteger(value) && value <= INTEGER_GAMMA.length) return INTEGER_GAMMA[value - 1];
  const absolute = Math.abs(value);
  if (absolute < 1e-20) {
    const result = 1 / value;
    if (!Number.isFinite(result)) throw diasporeErr("OverflowError", "math range error");
    return result;
  }
  if (absolute > 200) {
    if (value < 0) return 0 / sinPi(value);
    throw diasporeErr("OverflowError", "math range error");
  }
  const shifted = absolute + LANCZOS_OFFSET;
  const error = absolute > LANCZOS_OFFSET
    ? (shifted - absolute) - LANCZOS_OFFSET
    : (shifted - LANCZOS_OFFSET) - absolute;
  const correction = error * LANCZOS_G / shifted;
  let result: number;
  if (value < 0) {
    result = -Math.PI / sinPi(absolute) / absolute * Math.exp(shifted) / lanczosSum(absolute);
    result -= correction * result;
    if (absolute < 140) result /= Math.pow(shifted, absolute - 0.5);
    else {
      const power = Math.pow(shifted, absolute / 2 - 0.25);
      result /= power;
      result /= power;
    }
  } else {
    result = lanczosSum(absolute) / Math.exp(shifted);
    result += correction * result;
    if (absolute < 140) result *= Math.pow(shifted, absolute - 0.5);
    else {
      const power = Math.pow(shifted, absolute / 2 - 0.25);
      result *= power;
      result *= power;
    }
  }
  if (!Number.isFinite(result)) throw diasporeErr("OverflowError", "math range error");
  return result;
}

function logGamma(value: number): number {
  if (Number.isNaN(value)) return value;
  if (!Number.isFinite(value)) return Infinity;
  if (Number.isInteger(value) && value <= 2) {
    if (value <= 0) throw diasporeErr("ValueError", "math domain error");
    return 0;
  }
  const absolute = Math.abs(value);
  if (absolute < 1e-20) return -Math.log(absolute);
  let result = Math.log(lanczosSum(absolute)) - LANCZOS_G;
  result += (absolute - 0.5) * (Math.log(absolute + LANCZOS_G - 0.5) - 1);
  if (value < 0) {
    result = 1.144729885849400174143427351353058711647
      - Math.log(Math.abs(sinPi(absolute))) - Math.log(absolute) - result;
  }
  if (!Number.isFinite(result)) throw diasporeErr("OverflowError", "math range error");
  return result;
}

function* fsum(I: Interp, source: Value): G<number> {
  const iterator = yield* getIter(I, source);
  const partials: number[] = [];
  let specialSum = 0, infinitySum = 0;
  for (;;) {
    I.fuel();
    const [item, present] = yield* iterator.next();
    if (!present) break;
    let value = yield* mathFloat(I, item);
    const original = value;
    let retained = 0;
    for (let index = 0; index < partials.length; index++) {
      let other = partials[index];
      if (Math.abs(value) < Math.abs(other)) {
        const saved = value;
        value = other;
        other = saved;
      }
      const high = value + other;
      const low = other - (high - value);
      if (low !== 0) partials[retained++] = low;
      value = high;
    }
    partials.length = retained;
    if (value !== 0) {
      if (!Number.isFinite(value)) {
        if (Number.isFinite(original)) throw diasporeErr("OverflowError", "intermediate overflow in fsum");
        if (!Number.isNaN(original)) infinitySum += original;
        specialSum += original;
        partials.length = 0;
      } else partials.push(value);
    }
  }
  if (specialSum !== 0) {
    if (Number.isNaN(infinitySum)) throw diasporeErr("ValueError", "-inf + inf in fsum");
    return specialSum;
  }
  let high = 0, low = 0;
  let remaining = partials.length;
  if (remaining > 0) {
    high = partials[--remaining];
    while (remaining > 0) {
      const value = high, other = partials[--remaining];
      high = value + other;
      low = other - (high - value);
      if (low !== 0) break;
    }
    if (remaining > 0 && ((low < 0 && partials[remaining - 1] < 0) ||
        (low > 0 && partials[remaining - 1] > 0))) {
      const correction = low * 2, rounded = high + correction;
      if (rounded - high === correction) high = rounded;
    }
  }
  return high;
}

const exponentBits = new DataView(new ArrayBuffer(8));
const MIN_NORMAL = 2 ** -1022;

// Dekker's product residual replaces FMA without allocating temporary pairs.
function squareError(value: number, square: number): number {
  const split = value * 134217729;
  const high = split - (split - value), low = value - high;
  return ((high * high - square) + 2 * high * low) + low * low;
}

function vectorNorm(coordinates: number[], maximum: number, foundNaN: boolean): number {
  if (maximum === Infinity) return Infinity;
  if (foundNaN) return NaN;
  if (maximum === 0 || coordinates.length <= 1) return maximum;
  if (maximum < MIN_NORMAL) {
    for (let index = 0; index < coordinates.length; index++) coordinates[index] /= MIN_NORMAL;
    return MIN_NORMAL * vectorNorm(coordinates, maximum / MIN_NORMAL, false);
  }
  exponentBits.setFloat64(0, maximum);
  const exponent = ((exponentBits.getUint32(0) >>> 20) & 0x7ff) - 1022;
  const scale = 2 ** -exponent;
  let sum = 1, productErrors = 0, sumErrors = 0;
  for (const coordinate of coordinates) {
    const value = coordinate * scale;
    const square = value * value;
    const high = sum + square;
    productErrors += squareError(value, square);
    sumErrors += (sum - high) + square;
    sum = high;
  }
  let root = Math.sqrt(sum - 1 + (productErrors + sumErrors));
  const square = root * root, high = sum - square;
  productErrors -= squareError(root, square);
  sumErrors += (sum - high) - square;
  sum = high;
  root += (sum - 1 + (productErrors + sumErrors)) / (2 * root);
  return root / scale;
}

/** Native special functions, accurately summed reductions, and Euclidean distances. */
export function mathSpecialEntries(): [string, Value][] {
  return [
    ...(["gamma", "lgamma"] as const).map((name): [string, Value] => [name, bi(name, function* (I, args, kw) {
      noKw(name, kw);
      argCount(name, args, 1, 1);
      const value = yield* mathFloat(I, args[0]);
      return name === "gamma" ? gamma(value) : logGamma(value);
    })]),
    ["fsum", bi("fsum", function* (I, args, kw) {
      noKw("fsum", kw);
      argCount("fsum", args, 1, 1);
      return yield* fsum(I, args[0]);
    })],
    ["hypot", bi("hypot", function* (I, args, kw) {
      noKw("hypot", kw);
      const coordinates = new Array<number>(args.length);
      let maximum = 0, foundNaN = false;
      for (let index = 0; index < args.length; index++) {
        I.fuel();
        const value = Math.abs(yield* mathFloat(I, args[index]));
        coordinates[index] = value;
        if (Number.isNaN(value)) foundNaN = true;
        if (value > maximum) maximum = value;
      }
      return vectorNorm(coordinates, maximum, foundNaN);
    })],
    ["dist", bi("dist", function* (I, args, kw) {
      noKw("dist", kw);
      argCount("dist", args, 2, 2);
      const leftTuple = args[0] instanceof DiasporeInstance ? args[0].prim : args[0];
      const left = leftTuple instanceof DiasporeTuple ? leftTuple.items : yield* iterToArray(I, args[0]);
      const rightTuple = args[1] instanceof DiasporeInstance ? args[1].prim : args[1];
      const right = rightTuple instanceof DiasporeTuple ? rightTuple.items : yield* iterToArray(I, args[1]);
      if (left.length !== right.length) throw diasporeErr("ValueError", "both points must have the same number of dimensions");
      const coordinates = new Array<number>(left.length);
      let maximum = 0, foundNaN = false;
      for (let index = 0; index < left.length; index++) {
        I.fuel();
        const a = yield* mathFloat(I, left[index]);
        const b = yield* mathFloat(I, right[index]);
        const value = Math.abs(a - b);
        coordinates[index] = value;
        if (Number.isNaN(value)) foundNaN = true;
        if (value > maximum) maximum = value;
      }
      return vectorNorm(coordinates, maximum, foundNaN);
    })],
  ];
}
