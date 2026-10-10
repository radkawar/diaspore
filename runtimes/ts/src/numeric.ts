/* Exact binary64 hexadecimal conversion without decimal intermediates. */

import { type PyString, chars, diasporeErr, strText, unicodeCodecError } from "./values.ts";

/** Parse float.fromhex syntax, rounding once to nearest binary64 with ties to even. */
export function parseFloatHex(value: PyString): number {
  const points = chars(value);
  for (let index = 0; index < points.length; index++) {
    const point = points[index].codePointAt(0)!;
    if (point >= 0xd800 && point <= 0xdfff) {
      let end = index + 1;
      while (end < points.length) {
        const next = points[end].codePointAt(0)!;
        if (next < 0xd800 || next > 0xdfff) break;
        end++;
      }
      throw unicodeCodecError("UnicodeEncodeError", "utf-8", value, index, end, "surrogates not allowed");
    }
  }
  const text = strText(value).replace(/^[\t\n\v\f\r ]+|[\t\n\v\f\r ]+$/g, "");
  const special = /^([+-]?)(inf(?:inity)?|nan)$/i.exec(text);
  if (special !== null) {
    const result = special[2].toLowerCase() === "nan" ? NaN : Infinity;
    return special[1] === "-" ? -result : result;
  }
  const match = /^([+-]?)(?:0[xX])?([0-9a-fA-F]*)(?:\.([0-9a-fA-F]*))?(?:[pP]([+-]?[0-9]+))?$/.exec(text);
  if (match === null || match[2].length + (match[3]?.length ?? 0) === 0) {
    throw diasporeErr("ValueError", "invalid hexadecimal floating-point string");
  }
  const negative = match[1] === "-";
  const fraction = match[3] ?? "";
  const coefficient = BigInt("0x" + match[2] + fraction);
  if (coefficient === 0n) return negative ? -0 : 0;
  // Saturated exponents cannot be cancelled by any host-representable string.
  const explicitExponent = Math.max(-Number.MAX_SAFE_INTEGER, Math.min(Number.MAX_SAFE_INTEGER, Number(match[4] ?? 0)));
  const exponent = explicitExponent - 4 * fraction.length;
  const top = coefficient.toString(2).length - 1 + exponent;
  if (top > 1023) throw diasporeErr("OverflowError", "hexadecimal value too large to represent as a float");
  if (top < -1075) return negative ? -0 : 0;
  const quantum = Math.max(top - 52, -1074);
  const shift = quantum - exponent;
  let rounded: bigint;
  if (shift > 0) {
    const bits = BigInt(shift);
    rounded = coefficient >> bits;
    const remainder = coefficient - (rounded << bits);
    const half = 1n << (bits - 1n);
    if (remainder > half || (remainder === half && (rounded & 1n) !== 0n)) rounded++;
  } else {
    rounded = coefficient << BigInt(-shift);
  }
  const result = Number(rounded) * 2 ** quantum;
  if (!Number.isFinite(result)) throw diasporeErr("OverflowError", "hexadecimal value too large to represent as a float");
  return negative ? -result : result;
}

const floatBits = new DataView(new ArrayBuffer(8));

/** Return Python's exact, fixed-precision float.hex representation. */
export function formatFloatHex(value: number): string {
  if (Number.isNaN(value)) return "nan";
  if (!Number.isFinite(value)) return value < 0 ? "-inf" : "inf";
  const sign = value < 0 || Object.is(value, -0) ? "-" : "";
  if (value === 0) return sign + "0x0.0p+0";
  floatBits.setFloat64(0, value);
  const bits = floatBits.getBigUint64(0);
  const encodedExponent = Number((bits >> 52n) & 0x7ffn);
  const fraction = (bits & 0xfffffffffffffn).toString(16).padStart(13, "0");
  const exponent = encodedExponent === 0 ? -1022 : encodedExponent - 1023;
  return `${sign}0x${encodedExponent === 0 ? "0" : "1"}.${fraction}p${exponent >= 0 ? "+" : ""}${exponent}`;
}
