/* Float/str/bytes formatting with CPython parity: pyFloatStr (repr of float),
 * str/bytes reprs, exact decimal rounding, and the format-spec mini-language. */

import {
  type Value,
  type G,
  type PyString,
  isPyString,
  strFromChars,
  strConcat,
  strRepeat,
  chars,
  typeName,
  DiasporeInstance,
  DiasporeRange,
  diasporeErr,
} from "./values.ts";
import type { Interp } from "./interp.ts";
import { isPrintableCodePoint } from "./unicode15.ts";

// --------------------------------------------------------------------------
// pyFloatStr: CPython repr(float)

/**
 * Renders a float exactly like CPython's repr: shortest round-trip digits,
 * fixed notation unless exp < -4 or exp >= 16, integral floats get ".0",
 * exponent styled e+NN / e-NN with at least two digits.
 */
export function pyFloatStr(f: number): string {
  if (f === Infinity) return "inf";
  if (f === -Infinity) return "-inf";
  if (Number.isNaN(f)) return "nan";
  if (f === 0) return Object.is(f, -0) ? "-0.0" : "0.0";
  // toExponential() without an argument gives the shortest unique digits.
  const s = f.toExponential();
  const m = /^(-?)(\d)(?:\.(\d+))?e([+-]\d+)$/.exec(s);
  if (m === null) return String(f); // unreachable for finite floats
  const neg = m[1] === "-";
  const digits = m[2] + (m[3] ?? "");
  const exp = parseInt(m[4], 10);
  let out: string;
  if (exp < -4 || exp >= 16) {
    out = digits.length > 1 ? digits[0] + "." + digits.slice(1) : digits;
    out += "e" + expString(exp);
  } else if (exp >= digits.length - 1) {
    out = digits + "0".repeat(exp - digits.length + 1) + ".0";
  } else if (exp >= 0) {
    out = digits.slice(0, exp + 1) + "." + digits.slice(exp + 1);
  } else {
    out = "0." + "0".repeat(-exp - 1) + digits;
  }
  return neg ? "-" + out : out;
}

function expString(exp: number): string {
  const sign = exp < 0 ? "-" : "+";
  let es = String(Math.abs(exp));
  if (es.length < 2) es = "0" + es;
  return sign + es;
}

// --------------------------------------------------------------------------
// complex

import { Complex, diasporeErr as _diasporeErr } from "./values.ts";

/** A complex component formatted like CPython: integral values drop `.0`. */
function cfloat(x: number): string {
  if (x === Infinity) return "inf";
  if (x === -Infinity) return "-inf";
  if (Number.isNaN(x)) return "nan";
  let s = pyFloatStr(x);
  if (s.endsWith(".0")) s = s.slice(0, -2);
  return s;
}

/** CPython complex repr: `1j`, `(3+4j)`, `(-0-3j)`, `nanj`, `0j`. */
export function complexRepr(z: Complex): string {
  if (Object.is(z.re, 0)) {
    // pure imaginary (positive-zero real part)
    return cfloat(z.im) + "j";
  }
  const reS = cfloat(z.re);
  const neg = z.im < 0 || Object.is(z.im, -0);
  return "(" + reS + (neg ? "-" : "+") + cfloat(Math.abs(z.im)) + "j)";
}

/** Parse a Python complex literal string, e.g. "3+4j", "1j", "-2", "(1-2j)". */
export function parseComplex(orig: string): Complex {
  const fail = () =>
    _diasporeErr("ValueError", `complex() arg is a malformed string`);
  let s = orig.trim();
  if (s.startsWith("(") && s.endsWith(")")) s = s.slice(1, -1).trim();
  if (s === "") throw fail();
  const floatOf = (t: string): number => {
    if (/^[+-]?(inf|infinity)$/i.test(t)) return t[0] === "-" ? -Infinity : Infinity;
    if (/^[+-]?nan$/i.test(t)) return NaN;
    if (!/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(t)) throw fail();
    return Number(t);
  };
  const lower = s.toLowerCase();
  if (lower.endsWith("j")) {
    const body = s.slice(0, -1);
    // find the split between real and imaginary (a +/- not at position 0
    // and not right after an exponent 'e')
    let split = -1;
    for (let i = 1; i < body.length; i++) {
      const c = body[i];
      if ((c === "+" || c === "-") && body[i - 1].toLowerCase() !== "e") {
        split = i;
      }
    }
    if (split < 0) {
      // pure imaginary
      let imag = body;
      if (imag === "" || imag === "+") imag = "1";
      else if (imag === "-") imag = "-1";
      return new Complex(0, floatOf(imag));
    }
    const reS = body.slice(0, split);
    let imS = body.slice(split);
    if (imS === "+") imS = "1";
    else if (imS === "-") imS = "-1";
    else if (imS[0] === "+") imS = imS.slice(1);
    return new Complex(floatOf(reS), floatOf(imS));
  }
  return new Complex(floatOf(s), 0);
}

// --------------------------------------------------------------------------
// str / bytes reprs

/** Render a Python literal, escaping nonprintable code points independently. */
export function reprString(s: PyString): string {
  let q = "'";
  const points = chars(s);
  if (points.includes("'") && !points.includes('"')) q = '"';
  let b = q;
  for (const r of points) {
    const c = r.codePointAt(0) as number;
    if (r === "\\") b += "\\\\";
    else if (r === q) b += "\\" + q;
    else if (r === "\n") b += "\\n";
    else if (r === "\r") b += "\\r";
    else if (r === "\t") b += "\\t";
    else if (!isPrintableCodePoint(c)) {
      const width = c <= 0xff ? 2 : c <= 0xffff ? 4 : 8;
      b += (width === 2 ? "\\x" : width === 4 ? "\\u" : "\\U") + c.toString(16).padStart(width, "0");
    }
    else b += r;
  }
  return b + q;
}

/** Render a Python bytes literal. */
export function reprBytes(bs: Uint8Array): string {
  let hasSingle = false;
  let hasDouble = false;
  for (const c of bs) {
    if (c === 0x27) hasSingle = true;
    else if (c === 0x22) hasDouble = true;
  }
  const q = hasSingle && !hasDouble ? 0x22 : 0x27;
  let b = "b" + String.fromCharCode(q);
  for (const c of bs) {
    if (c === 0x5c) b += "\\\\";
    else if (c === q) b += "\\" + String.fromCharCode(q);
    else if (c === 0x0a) b += "\\n";
    else if (c === 0x0d) b += "\\r";
    else if (c === 0x09) b += "\\t";
    else if (c < 0x20 || c >= 0x7f) b += "\\x" + c.toString(16).padStart(2, "0");
    else b += String.fromCharCode(c);
  }
  return b + String.fromCharCode(q);
}

/** Render a range using Python's repr convention. */
export function reprRange(r: DiasporeRange): string {
  if (r.step === 1n) return `range(${r.start}, ${r.stop})`;
  return `range(${r.start}, ${r.stop}, ${r.step})`;
}

// --------------------------------------------------------------------------
// exact decimal machinery (correct rounding with ties-to-even, like CPython)

const f64view = new DataView(new ArrayBuffer(8));

/** Decompose a finite nonzero float: |f| = mant * 2^exp2 exactly. */
function decompose(af: number): { mant: bigint; exp2: number } {
  f64view.setFloat64(0, af);
  const bits = f64view.getBigUint64(0);
  const e = Number((bits >> 52n) & 0x7ffn);
  const m = bits & 0xfffffffffffffn;
  if (e === 0) return { mant: m, exp2: -1074 };
  return { mant: m | 0x10000000000000n, exp2: e - 1075 };
}

/** Exact decimal of |f|: returns D (bigint) and scale with |f| = D * 10^-scale. */
function exactDecimal(af: number): { D: bigint; scale: number } {
  if (af === 0) return { D: 0n, scale: 0 };
  const { mant, exp2 } = decompose(af);
  if (exp2 >= 0) return { D: mant << BigInt(exp2), scale: 0 };
  return { D: mant * 5n ** BigInt(-exp2), scale: -exp2 };
}

/** Round D / 10^k half-to-even (k >= 0). */
function divRoundEven(D: bigint, k: number): bigint {
  if (k <= 0) return D * 10n ** BigInt(-k);
  const p = 10n ** BigInt(k);
  const q = D / p;
  const r = D % p;
  const twice = r * 2n;
  if (twice > p) return q + 1n;
  if (twice < p) return q;
  return q % 2n === 0n ? q : q + 1n;
}

/** Fixed-point formatting of |f| with exactly prec fraction digits. */
export function fixedStr(af: number, prec: number): string {
  if (!Number.isFinite(af)) return Number.isNaN(af) ? "nan" : "inf";
  const { D, scale } = exactDecimal(af);
  const R = divRoundEven(D, scale - prec);
  let digits = R.toString();
  if (prec === 0) return digits;
  if (digits.length <= prec) digits = "0".repeat(prec - digits.length + 1) + digits;
  return digits.slice(0, digits.length - prec) + "." + digits.slice(digits.length - prec);
}

/** Round |f| to n significant digits; returns digits (length n, no dot) and
 * the decimal exponent of the leading digit. */
function sigRound(af: number, n: number): { digits: string; exp10: number } {
  const { D, scale } = exactDecimal(af);
  if (D === 0n) return { digits: "0".repeat(n), exp10: 0 };
  const ds = D.toString();
  let exp10 = ds.length - 1 - scale;
  let R = divRoundEven(D, ds.length - n);
  let rs = R.toString();
  if (rs.length > n) {
    // carry: 999 -> 1000
    R = R / 10n;
    exp10 += 1;
    rs = R.toString();
  }
  if (rs.length < n) rs = rs + "0".repeat(n - rs.length);
  return { digits: rs, exp10 };
}

/** Scientific formatting of |f| with prec digits after the point. */
export function expStr(af: number, prec: number): string {
  if (!Number.isFinite(af)) return Number.isNaN(af) ? "nan" : "inf";
  if (af === 0) {
    const mant = prec > 0 ? "0." + "0".repeat(prec) : "0";
    return mant + "e+00";
  }
  const { digits, exp10 } = sigRound(af, prec + 1);
  const mant = prec > 0 ? digits[0] + "." + digits.slice(1) : digits;
  return mant + "e" + expString(exp10);
}

/** %g-style formatting of |f| with prec significant digits (Go/CPython rules:
 * scientific iff exp < -4 or exp >= prec; trailing zeros stripped). */
export function gStr(af: number, prec: number, alternate = false): string {
  if (!Number.isFinite(af)) return Number.isNaN(af) ? "nan" : "inf";
  if (prec < 1) prec = 1;
  if (af === 0) return alternate ? "0." + "0".repeat(prec - 1) : "0";
  const { digits, exp10 } = sigRound(af, prec);
  if (exp10 < -4 || exp10 >= prec) {
    const frac = alternate ? digits.slice(1) : digits.slice(1).replace(/0+$/, "");
    const mant = frac.length > 0 || alternate ? digits[0] + "." + frac : digits[0];
    return mant + "e" + expString(exp10);
  }
  if (exp10 >= digits.length - 1) {
    return digits + "0".repeat(exp10 - digits.length + 1) + (alternate ? "." : "");
  }
  if (exp10 >= 0) {
    const frac = alternate ? digits.slice(exp10 + 1) : digits.slice(exp10 + 1).replace(/0+$/, "");
    return frac.length > 0 || alternate ? digits.slice(0, exp10 + 1) + "." + frac : digits.slice(0, exp10 + 1);
  }
  const frac = "0".repeat(-exp10 - 1) + (alternate ? digits : digits.replace(/0+$/, ""));
  return frac.length > 0 ? "0." + frac : "0";
}

// --------------------------------------------------------------------------
// Format-spec mini-language (spec 10).

interface FmtSpec {
  fill: string;
  align: string; // "" | "<" | ">" | "^" | "="
  sign: string; // "" | "+" | "-" | " "
  alternate: boolean;
  zero: boolean;
  width: number;
  comma: boolean;
  hasPrec: boolean;
  prec: number;
  typ: string; // "" or one of d b o x X f e g % s
}

/** Parse the supported Python format-spec mini-language. */
export function parseFmtSpec(spec: PyString): FmtSpec {
  const fs: FmtSpec = {
    fill: " ",
    align: "",
    sign: "",
    alternate: false,
    zero: false,
    width: 0,
    comma: false,
    hasPrec: false,
    prec: 0,
    typ: "",
  };
  const rs = chars(spec).slice();
  let i = 0;
  const isAlign = (r: string) => r === "<" || r === ">" || r === "^" || r === "=";
  if (rs.length >= 2 && isAlign(rs[1])) {
    fs.fill = rs[0];
    fs.align = rs[1];
    i = 2;
  } else if (rs.length >= 1 && isAlign(rs[0])) {
    fs.align = rs[0];
    i = 1;
  }
  if (i < rs.length && (rs[i] === "+" || rs[i] === "-" || rs[i] === " ")) {
    fs.sign = rs[i];
    i++;
  }
  if (rs[i] === "#") {
    fs.alternate = true;
    i++;
  }
  if (i < rs.length && rs[i] === "0") {
    fs.zero = true;
    if (fs.align === "") {
      fs.align = "=";
      fs.fill = "0";
    }
    i++;
  }
  while (i < rs.length && rs[i] >= "0" && rs[i] <= "9") {
    fs.width = fs.width * 10 + (rs[i].charCodeAt(0) - 48);
    i++;
  }
  if (i < rs.length && rs[i] === ",") {
    fs.comma = true;
    i++;
  }
  if (i < rs.length && rs[i] === ".") {
    i++;
    if (i >= rs.length || rs[i] < "0" || rs[i] > "9") {
      throw diasporeErr("ValueError", "Format specifier missing precision");
    }
    fs.hasPrec = true;
    while (i < rs.length && rs[i] >= "0" && rs[i] <= "9") {
      fs.prec = fs.prec * 10 + (rs[i].charCodeAt(0) - 48);
      i++;
    }
  }
  if (i < rs.length) {
    fs.typ = rs[i];
    i++;
  }
  if (i !== rs.length) {
    throw diasporeErr("ValueError", `Invalid format specifier ${reprString(spec)}`);
  }
  if (!"dboxXfFeEgG%s".includes(fs.typ) && fs.typ !== "") {
    throw diasporeErr("ValueError", `Unknown format code '${fs.typ}'`);
  }
  return fs;
}

/** Honor __format__ and preserve logical code-point boundaries in text padding. */
export function* formatValue(I: Interp, v: Value, spec: PyString): G<PyString> {
  if (v instanceof DiasporeInstance && v.cls.lookup("__format__") !== undefined) {
    const result = yield* I.callDunder(v, "__format__", [spec]);
    if (!isPyString(result)) throw diasporeErr("TypeError", `__format__ must return a str, not ${typeName(result)}`);
    return result;
  }
  if (chars(spec).length === 0) return yield* I.str(v);
  if (v instanceof DiasporeInstance && v.prim !== null) v = v.prim;
  if (!isPyString(v) && typeof v !== "bigint" && typeof v !== "number" && typeof v !== "boolean") {
    throw diasporeErr("TypeError", `unsupported format string passed to ${typeName(v)}.__format__`);
  }
  const fs = parseFmtSpec(spec);
  if (isPyString(v)) return formatStr(v, fs);
  if (typeof v === "boolean") v = v ? 1n : 0n;
  if (typeof v === "bigint") return formatInt(v, fs);
  if (typeof v === "number") return formatFloat(v, fs);
  throw diasporeErr("TypeError", `unsupported format string passed to ${typeName(v)}.__format__`);
}

function formatStr(s: PyString, fs: FmtSpec): PyString {
  if (fs.typ !== "" && fs.typ !== "s") {
    throw diasporeErr("ValueError", `Unknown format code '${fs.typ}' for object of type 'str'`);
  }
  if (fs.sign !== "") {
    throw diasporeErr("ValueError", "Sign not allowed in string format specifier");
  }
  if (fs.alternate) throw diasporeErr("ValueError", "Alternate form (#) not allowed in string format specifier");
  if (fs.align === "=") {
    throw diasporeErr("ValueError", "'=' alignment not allowed in string format specifier");
  }
  let rs = chars(s).slice();
  if (fs.hasPrec && rs.length > fs.prec) rs = rs.slice(0, fs.prec);
  const align = fs.align === "" ? "<" : fs.align;
  return pad(strFromChars(rs), rs.length, align, fs.fill, fs.width);
}

function formatInt(n: bigint, fs: FmtSpec): PyString {
  if ("fFeEgG%".includes(fs.typ) && fs.typ !== "") {
    if (!Number.isFinite(Number(n))) throw diasporeErr("OverflowError", "int too large to convert to float");
    return formatFloat(Number(n), fs);
  }
  if (fs.hasPrec) {
    throw diasporeErr("ValueError", "Precision not allowed in integer format specifier");
  }
  const neg = n < 0n;
  const mag = neg ? -n : n;
  let body = "";
  switch (fs.typ) {
    case "":
    case "d":
      body = mag.toString(10);
      if (fs.comma) body = groupWithZeroFill(body, "", neg, fs);
      break;
    case "b":
      body = mag.toString(2);
      break;
    case "o":
      body = mag.toString(8);
      break;
    case "x":
      body = mag.toString(16);
      break;
    case "X":
      body = mag.toString(16).toUpperCase();
      break;
    default:
      throw diasporeErr("ValueError", `Unknown format code '${fs.typ}' for object of type 'int'`);
  }
  const prefix = fs.alternate && "boxX".includes(fs.typ) && fs.typ !== "" ? "0" + fs.typ : "";
  return padNumber(body, neg, fs, prefix);
}

function formatFloat(f: number, fs: FmtSpec): PyString {
  const neg = !Number.isNaN(f) && (f < 0 || Object.is(f, -0));
  const av = Math.abs(f);
  let prec = fs.hasPrec ? fs.prec : 6;
  let body: string;
  switch (fs.typ.toLowerCase()) {
    case "f":
      body = fixedStr(av, prec);
      break;
    case "e":
      body = expStr(av, prec);
      break;
    case "%":
      body = Number.isFinite(av) ? fixedStr(av * 100, prec) + "%" : fixedStr(av, prec);
      break;
    case "g":
      body = gStr(av, prec, fs.alternate);
      break;
    case "":
      if (fs.hasPrec) {
        body = gStr(av, prec, fs.alternate);
      } else {
        const s = pyFloatStr(av);
        body = s.startsWith("-") ? s.slice(1) : s;
      }
      break;
    default:
      throw diasporeErr("ValueError", `Unknown format code '${fs.typ}' for object of type 'float'`);
  }
  if (fs.alternate && Number.isFinite(av) && !body.includes(".")) {
    const exponent = body.search(/[e%]/);
    body = exponent < 0 ? body + "." : body.slice(0, exponent) + "." + body.slice(exponent);
  }
  if (fs.typ === "F" || fs.typ === "E" || fs.typ === "G") body = body.toUpperCase();
  if (fs.comma) {
    const dot = body.search(/[.eE%]/);
    if (dot >= 0) {
      body = groupWithZeroFill(body.slice(0, dot), body.slice(dot), neg, fs);
    } else {
      body = groupWithZeroFill(body, "", neg, fs);
    }
  }
  return padNumber(body, neg, fs);
}

/** Under zero padding the padding zeros take part in the thousands grouping,
 * CPython-style (format(-42, "08,d") == "-000,042"). */
function groupWithZeroFill(digits: string, rest: string, neg: boolean, fs: FmtSpec): string {
  let grouped = groupDigits(digits);
  if (fs.align === "=" && fs.fill === "0") {
    const signLen = neg || fs.sign === "+" || fs.sign === " " ? 1 : 0;
    const room = fs.width - signLen - chars(rest).length;
    while (grouped.length < room) {
      digits = "0" + digits;
      grouped = groupDigits(digits);
    }
  }
  return grouped + rest;
}

function groupDigits(s: string): string {
  if (s.length <= 3) return s;
  let b = "";
  const lead = s.length % 3;
  if (lead > 0) b = s.slice(0, lead);
  for (let i = lead; i < s.length; i += 3) {
    if (b.length > 0) b += ",";
    b += s.slice(i, i + 3);
  }
  return b;
}

function padNumber(body: string, neg: boolean, fs: FmtSpec, prefix = ""): PyString {
  let sign = "";
  if (neg) sign = "-";
  else if (fs.sign === "+") sign = "+";
  else if (fs.sign === " ") sign = " ";
  let align = fs.align === "" ? ">" : fs.align;
  if (align === "=") {
    let n = fs.width - sign.length - prefix.length - chars(body).length;
    if (n < 0) n = 0;
    return strConcat(sign + prefix, strRepeat(fs.fill, n), body);
  }
  const s = sign + prefix + body;
  return pad(s, chars(s).length, align, fs.fill, fs.width);
}

function pad(s: PyString, runeLen: number, align: string, fill: string, width: number): PyString {
  const n = width - runeLen;
  if (n <= 0) return s;
  switch (align) {
    case "<":
      return strConcat(s, strRepeat(fill, n));
    case ">":
      return strConcat(strRepeat(fill, n), s);
    case "^": {
      const left = Math.floor(n / 2);
      return strConcat(strRepeat(fill, left), s, strRepeat(fill, n - left));
    }
  }
  return s;
}

// --------------------------------------------------------------------------
// misc numeric formatting helpers

/** round(f, nd) with correct decimal rounding (ties to even). */
export function roundFloatDigits(f: number, nd: number): number {
  if (Number.isNaN(f) || !Number.isFinite(f)) return f;
  if (nd >= 0) {
    if (nd > 323) return f;
    const s = fixedStr(Math.abs(f), nd);
    const r = parseFloat(s);
    return f < 0 || Object.is(f, -0) ? -r : r;
  }
  // negative ndigits: round at 10^-nd
  const { D, scale } = exactDecimal(Math.abs(f));
  const R = divRoundEven(D, scale - nd) * 10n ** BigInt(-nd);
  const r = Number(R);
  return f < 0 || Object.is(f, -0) ? -r : r;
}

/** round(f) -> int with ties to even (exact). */
export function roundFloatToInt(f: number): bigint {
  const neg = f < 0;
  const af = Math.abs(f);
  const fl = Math.floor(af);
  const diff = af - fl;
  let r: number;
  if (diff > 0.5) r = fl + 1;
  else if (diff < 0.5) r = fl;
  else r = fl % 2 === 0 ? fl : fl + 1;
  const b = BigInt(r);
  return neg ? -b : b;
}

/** Deterministic-clock helper: round to 6 decimal places. */
export function round6(x: number): number {
  return parseFloat((x < 0 ? "-" : "") + fixedStr(Math.abs(x), 6));
}
