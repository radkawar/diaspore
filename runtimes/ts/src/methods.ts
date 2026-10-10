/* Methods on builtin types (spec 9.1), implemented natively with CPython
 * code-point semantics, including explicit adjacent surrogate code points. */

import {
  type Value,
  type PyString,
  isPyString,
  strText,
  strLen,
  strFromChars,
  strJoin,
  strConcat,
  strRepeat,
  type G,
  type KwArgs,
  NONE,
  OBJECT,
  PyNoneT,
  DiasporeList,
  DiasporeTuple,
  DiasporeDict,
  DiasporeDictView,
  isSetView,
  DiasporeMappingProxy,
  DiasporeSet,
  DiasporeFrozenSet,
  DiasporeInstance,
  Builtin,
  BoundMethod,
  typeName,
  asIndex,
  chars,
  diasporeErr,
  DiasporeError,
  excIsA,
  makeExc,
  bytesIndexOf,
  hashValue,
  hashKey,
  storedHashKey,
} from "./values.ts";
import { reprString } from "./format.ts";
import { braceFormat } from "./string-format.ts";
import { acquireBuffer, indexInteger, type BufferExport } from "./buffers.ts";
import { getIter, iterToArray } from "./iter.ts";
import { needArgs, noKw, wantInt, wantStr } from "./helpers.ts";
import { eqElem, getIndex, setOp, setIsSubset } from "./ops.ts";
import { keyReverseKw } from "./builtins.ts";
import { textCodec } from "./codecs.ts";
import { REGEX_DIGIT, REGEX_XID_START, REGEX_XID_CONTINUE } from "./re_unicode.ts";
import { isPrintableCodePoint } from "./unicode15.ts";
import type { Interp } from "./interp.ts";

type MFn = (I: Interp, args: Value[], kw: KwArgs) => G<Value>;

/** Resolve a bound method on a primitive sandbox value. */
export function builtinMethod(I: Interp, o: Value, n: string): Value {
  let fn: MFn | null = null;
  if (isPyString(o)) fn = strMethod(o, n);
  else if (o instanceof DiasporeList) fn = listMethod(o, n);
  else if (o instanceof DiasporeDict) fn = dictMethod(o, n);
  else if (isSetView(o) && n === "isdisjoint") {
    fn = function* (I2, args, kw) {
      noKw(n, kw);
      needArgs(n, args, 1, 1);
      const it = yield* getIter(I2, args[0]);
      for (;;) {
        I2.fuel();
        const [item, ok] = yield* it.next();
        if (!ok) return true;
        if (yield* I2.contains(o, item)) return false;
      }
    };
  }
  else if (o instanceof DiasporeSet) fn = setMethod(o, n);
  else if (o instanceof DiasporeTuple) fn = tupleMethod(o, n);
  else if (o instanceof Uint8Array) fn = bytesMethod(o, n);
  if (fn === null) {
    const name = typeName(o);
    const sequence = name === "str" || name === "bytes" || name === "tuple" || name === "list" || name === "range";
    const mapping = o instanceof DiasporeDict;
    const container = sequence || mapping || o instanceof DiasporeSet || isSetView(o);
    if (n === "__hash__" && (o instanceof DiasporeList ||
        mapping && !(o instanceof DiasporeMappingProxy) ||
        o instanceof DiasporeSet && !(o instanceof DiasporeFrozenSet) || isSetView(o))) return NONE;
    if (n === "__ne__") return new BoundMethod(OBJECT.ns.get("__ne__")!, o);
    if (n === "__eq__" || n === "__repr__" || n === "__str__" || n === "__hash__" ||
        (container && (n === "__len__" || n === "__iter__" || n === "__contains__")) ||
        ((sequence || mapping) && n === "__getitem__") ||
        ((o instanceof DiasporeList || (mapping && !(o instanceof DiasporeMappingProxy))) &&
          (n === "__setitem__" || n === "__delitem__"))) {
      const descriptor = I.builtinUnbound(name, n);
      if (descriptor !== null) return new BoundMethod(descriptor, o);
    }
  }
  if (fn === null) {
    throw diasporeErr("AttributeError", `'${typeName(o)}' object has no attribute '${n}'`);
  }
  return new Builtin(n, fn);
}

// Method-name allowlists per builtin type (for builtin-subclass routing).
const STR_METHODS = new Set([
  "upper", "lower", "title", "capitalize", "casefold", "strip", "lstrip",
  "rstrip", "split", "rsplit", "splitlines", "join", "replace", "find",
  "rfind", "index", "count", "startswith", "endswith", "isdigit", "isalpha",
  "isalnum", "isspace", "zfill", "ljust", "rjust", "encode", "partition",
  "rpartition", "removeprefix", "removesuffix", "center", "islower",
  "isupper", "swapcase", "format", "format_map", "isidentifier", "isascii",
  "maketrans", "translate", "isprintable", "isdecimal", "expandtabs",
]);
const LIST_METHODS = new Set([
  "append", "extend", "insert", "pop", "remove", "clear", "index", "count",
  "reverse", "sort", "copy",
]);
const DICT_METHODS = new Set([
  "get", "keys", "values", "items", "pop", "update", "setdefault", "clear", "copy",
]);
const SET_METHODS = new Set([
  "add", "remove", "discard", "pop", "clear", "copy", "union", "intersection",
  "difference", "symmetric_difference", "issubset", "issuperset",
  "isdisjoint", "update", "difference_update", "intersection_update", "symmetric_difference_update",
]);
const FROZENSET_METHODS: Record<string, true> = {
  copy: true, union: true, intersection: true, difference: true,
  symmetric_difference: true, issubset: true, issuperset: true, isdisjoint: true,
  __hash__: true,
};
const TUPLE_METHODS = new Set(["count", "index"]);
const BYTES_METHODS = new Set([
  "decode", "find", "split", "strip", "lstrip", "rstrip", "replace",
  "startswith", "endswith", "upper", "lower", "join", "hex", "maketrans", "translate",
  "ljust", "rjust", "center", "isascii",
]);

const ALLOWLISTS: Record<string, Set<string>> = {
  str: STR_METHODS,
  list: LIST_METHODS,
  dict: DICT_METHODS,
  set: SET_METHODS,
  tuple: TUPLE_METHODS,
  bytes: BYTES_METHODS,
};

/** Whether `method` is a pinned method of builtin type `tname`. */
export function methodAllowed(tname: string, method: string): boolean {
  if (tname === "frozenset") return FROZENSET_METHODS[method] === true;
  const set = ALLOWLISTS[tname];
  return set !== undefined && set.has(method);
}

/** Enumerate supported native methods for the live builtin type namespace. */
export function builtinMethodNames(name: string): string[] {
  return name === "frozenset" ? Object.keys(FROZENSET_METHODS) : [...(ALLOWLISTS[name] ?? [])];
}

/** Builtin-type name for a raw primitive value (for prim method routing). */
export function primTypeName(v: Value): string | null {
  if (isPyString(v)) return "str";
  if (v instanceof DiasporeList) return "list";
  if (v instanceof DiasporeDict) return "dict";
  if (v instanceof DiasporeFrozenSet) return "frozenset";
  if (v instanceof DiasporeSet) return "set";
  if (v instanceof DiasporeTuple) return "tuple";
  if (v instanceof Uint8Array) return "bytes";
  return null;
}

// --------------------------------------------------------------------------
// unicode predicates

const RE_LOWER = /\p{Ll}/u;
const RE_UPPER = /\p{Lu}/u;
const RE_TITLE = /\p{Lt}/u;
const RE_CASED = /\p{Cased}/u;
const RE_CASE_IGNORABLE = /\p{Case_Ignorable}/u;
const RE_LETTER = /\p{L}/u;
const RE_ND = /\p{Nd}/u;
const RE_NUM = /\p{N}/u;
const RE_SPACE = /[\t\n\v\f\r \x1c-\x1f\x85\u2028\u2029]|\p{Zs}/u;

/** Return the Unicode 15 decimal value of one code point, or null for non-digits. */
export function decimalValue(point: string): number | null {
  const code = point.codePointAt(0);
  if (code === undefined || point.length !== (code > 0xffff ? 2 : 1)) return null;
  let low = 0, high = REGEX_DIGIT.length / 2;
  while (low < high) {
    const middle = (low + high) >>> 1;
    const start = REGEX_DIGIT[middle * 2];
    if (code < start) high = middle;
    else if (code > REGEX_DIGIT[middle * 2 + 1]) low = middle + 1;
    else return (code - start) % 10;
  }
  return null;
}

function isCased(r: string): boolean {
  return RE_CASED.test(r);
}

function isSpaceCh(r: string): boolean {
  return RE_SPACE.test(r);
}

// --------------------------------------------------------------------------
// str helpers

function pyCase(s: PyString, upper: boolean): PyString {
  const convert = (run: string) => upper ? run.toUpperCase() : run.toLowerCase();
  if (typeof s === "string") return convert(s);
  const parts: string[] = [];
  let run = "";
  for (const point of s) {
    const code = point.codePointAt(0)!;
    if (code >= 0xd800 && code <= 0xdfff) {
      if (run !== "") parts.push(convert(run));
      parts.push(point);
      run = "";
    } else run += point;
  }
  if (run !== "") parts.push(convert(run));
  return strJoin(parts);
}

// Final sigma depends on the original text, including characters transformed
// separately by title, capitalize and swapcase.
function lowerAt(points: string[], index: number): string {
  const point = points[index];
  if (point !== "\u03a3") return point.toLowerCase();
  let before = index - 1;
  while (before >= 0 && RE_CASE_IGNORABLE.test(points[before])) before--;
  if (before < 0 || !isCased(points[before])) return "\u03c3";
  let after = index + 1;
  while (after < points.length && RE_CASE_IGNORABLE.test(points[after])) after++;
  return after === points.length || !isCased(points[after]) ? "\u03c2" : "\u03c3";
}

function pyTitle(s: PyString): PyString {
  const points = chars(s), parts: string[] = [];
  let prevCased = false;
  for (let index = 0; index < points.length; index++) {
    const r = points[index], cased = isCased(r);
    parts.push(cased ? prevCased ? lowerAt(points, index) : r.toUpperCase() : r);
    prevCased = cased;
  }
  return strJoin(parts);
}

function pyCasefold(s: PyString): PyString {
  const parts: string[] = [];
  for (const r of s) parts.push(r === "ß" || r === "ẞ" ? "ss" : r.toLowerCase());
  return strJoin(parts);
}

/**
 * Build a translation table from a dictionary or two equal-length strings.
 * String keys and paired arguments use Python code points, not UTF-16 units.
 */
export function makeStrTranslation(args: Value[], kw: KwArgs): DiasporeDict {
  noKw("maketrans", kw);
  needArgs("maketrans", args, 1, 3);
  const first = args[0] instanceof DiasporeInstance && args[0].prim !== null ? args[0].prim : args[0];
  if (args.length === 1) {
    if (!(first instanceof DiasporeDict) || first instanceof DiasporeMappingProxy) {
      throw diasporeErr("TypeError", "if you give only one argument to maketrans it must be a dict");
    }
    const result = new DiasporeDict();
    for (const { key, val } of first.map.values()) {
      const raw = key instanceof DiasporeInstance && key.prim !== null ? key.prim : key;
      if (isPyString(raw)) {
        const points = chars(raw);
        if (points.length !== 1) throw diasporeErr("ValueError", "string keys in translate table must be of length 1");
        result.set(BigInt(points[0].codePointAt(0)!), val);
      } else {
        if (asIndex(raw) === null) throw diasporeErr("TypeError", "keys in translate table must be strings or integers");
        result.set(key, val);
      }
    }
    return result;
  }
  const second = args[1] instanceof DiasporeInstance && args[1].prim !== null ? args[1].prim : args[1];
  if (!isPyString(second)) throw diasporeErr("TypeError", `maketrans() argument 2 must be str, not ${typeName(args[1])}`);
  let third: PyString | undefined;
  if (args.length === 3) {
    const raw = args[2] instanceof DiasporeInstance && args[2].prim !== null ? args[2].prim : args[2];
    if (!isPyString(raw)) throw diasporeErr("TypeError", `maketrans() argument 3 must be str, not ${typeName(args[2])}`);
    third = raw;
  }
  if (!isPyString(first)) throw diasporeErr("TypeError", "first maketrans argument must be a string if there is a second argument");
  const from = chars(first), to = chars(second);
  if (from.length !== to.length) throw diasporeErr("ValueError", "the first two maketrans arguments must have equal length");
  const result = new DiasporeDict();
  for (let index = 0; index < from.length; index++) {
    result.set(BigInt(from[index].codePointAt(0)!), BigInt(to[index].codePointAt(0)!));
  }
  if (third !== undefined) for (const point of third) result.set(BigInt(point.codePointAt(0)!), NONE);
  return result;
}

function splitWhitespace(s: PyString, maxsplit: number, fromRight: boolean): PyString[] {
  const rs = chars(s), parts: PyString[] = [];
  let start = 0, end = rs.length;
  if (!fromRight) {
    while (start < end && isSpaceCh(rs[start])) start++;
    while (start < end) {
      if (maxsplit >= 0 && parts.length === maxsplit) break;
      let at = start;
      while (at < end && !isSpaceCh(rs[at])) at++;
      parts.push(strFromChars(rs.slice(start, at)));
      start = at;
      while (start < end && isSpaceCh(rs[start])) start++;
    }
    if (start < end) parts.push(strFromChars(rs.slice(start, end)));
  } else {
    while (end > start && isSpaceCh(rs[end - 1])) end--;
    while (end > start) {
      if (maxsplit >= 0 && parts.length === maxsplit) break;
      let at = end;
      while (at > start && !isSpaceCh(rs[at - 1])) at--;
      parts.push(strFromChars(rs.slice(at, end)));
      end = at;
      while (end > start && isSpaceCh(rs[end - 1])) end--;
    }
    if (end > start) parts.push(strFromChars(rs.slice(start, end)));
    parts.reverse();
  }
  return parts;
}

function splitSeparator(s: PyString, separator: PyString, maxsplit: number, fromRight: boolean): PyString[] {
  const rs = chars(s), sep = chars(separator), parts: PyString[] = [];
  let start = 0, end = rs.length;
  while (maxsplit < 0 || parts.length < maxsplit) {
    const at = findRunes(rs, sep, start, end, fromRight);
    if (at < 0) break;
    if (fromRight) {
      parts.push(strFromChars(rs.slice(at + sep.length, end)));
      end = at;
    } else {
      parts.push(strFromChars(rs.slice(start, at)));
      start = at + sep.length;
    }
  }
  parts.push(strFromChars(rs.slice(start, end)));
  if (fromRight) parts.reverse();
  return parts;
}

function splitLines(s: PyString, keepends: boolean): PyString[] {
  const out: PyString[] = [], rs = chars(s);
  let start = 0;
  for (let i = 0; i < rs.length; i++) {
    const r = rs[i], c = r.codePointAt(0)!;
    if (r === "\n" || r === "\v" || r === "\f" || r === "\r" ||
        c === 0x1c || c === 0x1d || c === 0x1e || c === 0x85 || c === 0x2028 || c === 0x2029) {
      const end = i;
      if (r === "\r" && rs[i + 1] === "\n") i++;
      out.push(strFromChars(rs.slice(start, keepends ? i + 1 : end)));
      start = i + 1;
    }
  }
  if (start < rs.length) out.push(strFromChars(rs.slice(start)));
  return out;
}

/** Resolves optional start/end arguments against a code-point array. */
function strRegion(rs: string[], bounds: Value[]): [number, number] {
  let start = 0;
  let end = rs.length;
  const adjust = (v: Value, def: number, clampEnd = false): number => {
    if (v instanceof PyNoneT) return def;
    const n = asIndex(v);
    if (n === null) {
      throw diasporeErr(
        "TypeError",
        "slice indices must be integers or None or have an __index__ method",
      );
    }
    let i = Number(n);
    if (i < 0) {
      i += rs.length;
      if (i < 0) i = 0;
    } else if (clampEnd && i > rs.length) {
      i = rs.length;
    }
    return i;
  };
  if (bounds.length >= 1) start = adjust(bounds[0], 0);
  if (bounds.length >= 2) end = adjust(bounds[1], rs.length, true);
  return [start, end];
}

function runesMatch(rs: string[], sub: string[], at: number): boolean {
  if (at < 0 || at + sub.length > rs.length) return false;
  for (let j = 0; j < sub.length; j++) {
    if (rs[at + j] !== sub[j]) return false;
  }
  return true;
}

function findRunes(rs: string[], sub: string[], start: number, end: number, last: boolean): number {
  if (start > end) return -1;
  if (sub.length === 0) return last ? end : start;
  if (last) {
    for (let i = end - sub.length; i >= start; i--) {
      if (runesMatch(rs, sub, i)) return i;
    }
    return -1;
  }
  for (let i = start; i + sub.length <= end; i++) {
    if (runesMatch(rs, sub, i)) return i;
  }
  return -1;
}

function countRunes(rs: string[], sub: string[]): number {
  if (sub.length === 0) return rs.length + 1;
  let count = 0;
  for (let i = 0; i + sub.length <= rs.length; ) {
    if (runesMatch(rs, sub, i)) {
      count++;
      i += sub.length;
    } else {
      i++;
    }
  }
  return count;
}

function pyReplace(s: PyString, old: PyString, rep: PyString, count: number): PyString {
  if (count === 0) return s;
  const rs = chars(s), sub = chars(old), out: PyString[] = [];
  let start = 0, done = 0;
  if (sub.length === 0) {
    for (let i = 0; i < rs.length; i++) {
      if (count < 0 || done < count) { out.push(rep); done++; }
      out.push(rs[i]);
    }
    if (count < 0 || done < count) out.push(rep);
  } else {
    while (count < 0 || done < count) {
      const at = findRunes(rs, sub, start, rs.length, false);
      if (at < 0) break;
      out.push(strFromChars(rs.slice(start, at)), rep);
      start = at + sub.length;
      done++;
    }
    out.push(strFromChars(rs.slice(start)));
  }
  return strJoin(out);
}

// --------------------------------------------------------------------------
// str methods

function strMethod(s: PyString, n: string): MFn | null {
  switch (n) {
    case "expandtabs":
      return function* (I, args, kw) {
        if (args.length + kw.size > 1) throw diasporeErr("TypeError", `expandtabs() takes at most 1 argument (${args.length + kw.size} given)`);
        for (const key of kw.keys()) {
          if (key !== "tabsize") throw diasporeErr("TypeError", `'${key}' is an invalid keyword argument for expandtabs()`);
        }
        const width = yield* indexInteger(I, args[0] ?? kw.get("tabsize") ?? 8n);
        if (width < -2147483648n || width > 2147483647n) throw diasporeErr("OverflowError", "Python int too large to convert to C int");
        const tabsize = Number(width), parts: PyString[] = [];
        let column = 0;
        for (const point of s) {
          I.fuel();
          if (point === "\t") {
            if (tabsize > 0) {
              const spaces = tabsize - column % tabsize;
              I.fuelN(Math.floor(spaces / 16) + 1);
              parts.push(" ".repeat(spaces));
              column += spaces;
            }
          } else {
            parts.push(point);
            column = point === "\r" || point === "\n" ? 0 : column + 1;
          }
        }
        return strJoin(parts);
      };
    case "maketrans":
      return function* (_I, args, kw) { return makeStrTranslation(args, kw); };
    case "translate":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const parts: PyString[] = [];
        for (const point of s) {
          I.fuel();
          let replacement: Value;
          try {
            replacement = yield* getIndex(I, args[0], BigInt(point.codePointAt(0)!));
          } catch (error) {
            if (!(error instanceof DiasporeError) || !excIsA(error.value, "LookupError")) throw error;
            parts.push(point);
            continue;
          }
          const raw = replacement instanceof DiasporeInstance && replacement.prim !== null ? replacement.prim : replacement;
          if (raw instanceof PyNoneT) continue;
          if (isPyString(raw)) { parts.push(raw); continue; }
          const ordinal = asIndex(raw);
          if (ordinal === null) throw diasporeErr("TypeError", "character mapping must return integer, None or str");
          if (ordinal < 0n || ordinal > 0x10ffffn) throw diasporeErr("ValueError", "character mapping must be in range(0x110000)");
          parts.push(String.fromCodePoint(Number(ordinal)));
        }
        return strJoin(parts);
      };
    case "isprintable":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 0, 0);
        for (const point of s) {
          I.fuel();
          if (!isPrintableCodePoint(point.codePointAt(0)!)) return false;
        }
        return true;
      };
    case "isascii":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 0, 0);
        for (const char of s) {
          I.fuel();
          if (char.codePointAt(0)! > 127) return false;
        }
        return true;
      };
    case "isidentifier":
      return function* (_I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 0, 0);
        if (strLen(s) === 0) return false;
        let initial = true;
        for (const char of s) {
          const point = char.codePointAt(0)!;
          const ranges = initial ? REGEX_XID_START : REGEX_XID_CONTINUE;
          initial = false;
          if (point === 95) continue;
          let low = 0, high = ranges.length / 2 - 1, found = false;
          while (low <= high) {
            const middle = (low + high) >>> 1;
            if (point < ranges[middle * 2]) high = middle - 1;
            else if (point > ranges[middle * 2 + 1]) low = middle + 1;
            else { found = true; break; }
          }
          if (!found) return false;
        }
        return true;
      };
    case "format":
      return function* (I, args, kw) { return yield* braceFormat(I, s, args, kw); };
    case "format_map":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        return yield* braceFormat(I, s, [], new Map(), args[0]);
      };
    case "upper":
      return function* (I, args, kw) {
        noKw(n, kw);
        return pyCase(s, true);
      };
    case "lower":
      return function* (I, args, kw) {
        noKw(n, kw);
        return pyCase(s, false);
      };
    case "title":
      return function* (I, args, kw) {
        noKw(n, kw);
        return pyTitle(s);
      };
    case "capitalize":
      return function* (I, args, kw) {
        noKw(n, kw);
        const rs = chars(s);
        if (rs.length === 0) return s;
        const parts = [rs[0].toUpperCase()];
        for (let index = 1; index < rs.length; index++) parts.push(lowerAt(rs, index));
        return strJoin(parts);
      };
    case "casefold":
      return function* (I, args, kw) {
        noKw(n, kw);
        return pyCasefold(s);
      };
    case "strip":
    case "lstrip":
    case "rstrip":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 0, 1);
        let inSet = isSpaceCh;
        if (args.length === 1 && !(args[0] instanceof PyNoneT)) {
          const cs = wantStr(`${n} arg`, args[0]);
          const set = new Set(chars(cs));
          inSet = (r: string) => set.has(r);
        }
        let rs = chars(s).slice();
        if (n !== "rstrip") {
          let i = 0;
          while (i < rs.length && inSet(rs[i])) i++;
          rs = rs.slice(i);
        }
        if (n !== "lstrip") {
          let j = rs.length;
          while (j > 0 && inSet(rs[j - 1])) j--;
          rs = rs.slice(0, j);
        }
        return strFromChars(rs);
      };
    case "split":
    case "rsplit":
      return function* (I, args, kw) {
        if (args.length + kw.size > 2) throw diasporeErr("TypeError", `${n}() takes at most 2 arguments (${args.length + kw.size} given)`);
        let separator = args[0] ?? NONE, maximum = args[1] ?? -1n;
        for (const [key, value] of kw) {
          if (key !== "sep" && key !== "maxsplit") throw diasporeErr("TypeError", `'${key}' is an invalid keyword argument for ${n}()`);
          const position = key === "sep" ? 0 : 1;
          if (args.length > position) throw diasporeErr("TypeError", `argument for ${n}() given by name ('${key}') and position (${position + 1})`);
          if (key === "sep") separator = value;
          else maximum = value;
        }
        const count = yield* indexInteger(I, maximum);
        if (count < -(1n << 63n) || count >= 1n << 63n) throw diasporeErr("OverflowError", "Python int too large to convert to C ssize_t");
        const maxsplit = Number(count);
        let sep: PyString | null = null;
        if (!(separator instanceof PyNoneT)) {
          const raw = separator instanceof DiasporeInstance && separator.prim !== null ? separator.prim : separator;
          sep = wantStr("sep", raw);
          if (sep === "") throw diasporeErr("ValueError", "empty separator");
        }
        const parts = sep === null
          ? splitWhitespace(s, maxsplit, n === "rsplit")
          : splitSeparator(s, sep, maxsplit, n === "rsplit");
        return new DiasporeList(parts);
      };
    case "splitlines":
      return function* (I, args, kw) {
        if (args.length + kw.size > 1) throw diasporeErr("TypeError", `splitlines() takes at most 1 argument (${args.length + kw.size} given)`);
        for (const key of kw.keys()) {
          if (key !== "keepends") throw diasporeErr("TypeError", `'${key}' is an invalid keyword argument for splitlines()`);
        }
        const keepends = yield* I.truth(args[0] ?? kw.get("keepends") ?? false);
        return new DiasporeList(splitLines(s, keepends));
      };
    case "join":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const items = yield* iterToArray(I, args[0]);
        const parts: PyString[] = [];
        for (let i = 0; i < items.length; i++) {
          const x = items[i];
          if (!isPyString(x)) {
            throw diasporeErr(
              "TypeError",
              `sequence item ${i}: expected str instance, ${typeName(x)} found`,
            );
          }
          parts.push(x);
        }
        return strJoin(parts, s);
      };
    case "replace":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 2, 3);
        const old = wantStr("replace old", args[0]);
        const rep = wantStr("replace new", args[1]);
        let count = -1;
        if (args.length === 3) count = Number(wantInt("count", args[2]));
        return pyReplace(s, old, rep, count);
      };
    case "find":
    case "rfind":
    case "index":
    case "count":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 3);
        const sub = wantStr("sub", args[0]);
        const rs = chars(s).slice();
        const [start, end] = strRegion(rs, args.slice(1));
        if (n === "count") return start > end ? 0n : BigInt(countRunes(rs.slice(start, end), chars(sub)));
        const idx = findRunes(rs, chars(sub), start, end, n === "rfind");
        if (n === "index" && idx < 0) throw diasporeErr("ValueError", "substring not found");
        return BigInt(idx);
      };
    case "startswith":
    case "endswith":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 3);
        const rs = chars(s).slice();
        const [start, end] = strRegion(rs, args.slice(1));
        const pats = args[0] instanceof DiasporeTuple ? args[0].items : [args[0]];
        for (const p of pats) {
          if (!isPyString(p)) {
            throw diasporeErr(
              "TypeError",
              `${n} first arg must be str or a tuple of str, not ${typeName(p)}`,
            );
          }
          const sub = chars(p);
          if (sub.length > end - start) continue;
          if (runesMatch(rs, sub, n === "startswith" ? start : end - sub.length)) return true;
        }
        return false;
      };
    case "isdecimal":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 0, 0);
        if (strLen(s) === 0) return false;
        for (const char of s) {
          I.fuel();
          if (decimalValue(char) === null) return false;
        }
        return true;
      };
    case "isdigit":
    case "isalpha":
    case "isalnum":
    case "isspace":
      return function* (I, args, kw) {
        noKw(n, kw);
        if (s === "") return false;
        let pred: (r: string) => boolean;
        switch (n) {
          case "isdigit":
            pred = (r) => RE_ND.test(r);
            break;
          case "isalpha":
            pred = (r) => RE_LETTER.test(r);
            break;
          case "isalnum":
            pred = (r) => RE_LETTER.test(r) || RE_ND.test(r) || RE_NUM.test(r);
            break;
          default:
            pred = isSpaceCh;
        }
        for (const r of s) {
          if (!pred(r)) return false;
        }
        return true;
      };
    case "zfill":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const w = Number(wantInt("width", args[0]));
        const rs = chars(s);
        if (rs.length >= w) return s;
        let sign = "";
        let body = s;
        if (rs.length > 0 && (rs[0] === "+" || rs[0] === "-")) {
          sign = rs[0];
          body = strFromChars(rs.slice(1));
        }
        return strConcat(sign, "0".repeat(w - rs.length), body);
      };
    case "ljust":
    case "rjust":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 2);
        const w = Number(wantInt("width", args[0]));
        let fill: PyString = " ";
        if (args.length === 2) {
          const f = wantStr("fillchar", args[1]);
          if (chars(f).length !== 1) {
            throw diasporeErr("TypeError", "The fill character must be exactly one character long");
          }
          fill = f;
        }
        const pad = w - chars(s).length;
        if (pad <= 0) return s;
        return n === "ljust" ? strConcat(s, strRepeat(fill, pad)) : strConcat(strRepeat(fill, pad), s);
      };
    case "encode":
      return function* (I, args, kw) {
        needArgs(n, args, 0, 2);
        return yield* textCodec(I, "encode", s,
          wantStr("encoding", args[0] ?? kw.get("encoding") ?? "utf-8"),
          wantStr("errors", args[1] ?? kw.get("errors") ?? "strict"));
      };
    case "partition":
    case "rpartition":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const sep = wantStr("sep", args[0]);
        if (sep === "") throw diasporeErr("ValueError", "empty separator");
        const rs = chars(s), sub = chars(sep);
        const i = findRunes(rs, sub, 0, rs.length, n === "rpartition");
        if (i < 0) return new DiasporeTuple(n === "partition" ? [s, "", ""] : ["", "", s]);
        return new DiasporeTuple([strFromChars(rs.slice(0, i)), sep, strFromChars(rs.slice(i + sub.length))]);
      };
    case "removeprefix":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const p = wantStr("prefix", args[0]);
        const rs = chars(s), sub = chars(p);
        return runesMatch(rs, sub, 0) ? strFromChars(rs.slice(sub.length)) : s;
      };
    case "removesuffix":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const p = wantStr("suffix", args[0]);
        const rs = chars(s), sub = chars(p);
        return sub.length && runesMatch(rs, sub, rs.length - sub.length) ? strFromChars(rs.slice(0, rs.length - sub.length)) : s;
      };
    case "center":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 2);
        const w = Number(wantInt("width", args[0]));
        let fill: PyString = " ";
        if (args.length === 2) {
          const f = wantStr("fillchar", args[1]);
          if (chars(f).length !== 1) {
            throw diasporeErr("TypeError", "The fill character must be exactly one character long");
          }
          fill = f;
        }
        const marg = w - chars(s).length;
        if (marg <= 0) return s;
        const left = (marg >> 1) + (marg & w & 1);
        return strConcat(strRepeat(fill, left), s, strRepeat(fill, marg - left));
      };
    case "islower":
    case "isupper":
      return function* (I, args, kw) {
        noKw(n, kw);
        let hasCased = false;
        for (const r of s) {
          if (isCased(r)) {
            hasCased = true;
            if (n === "islower" && !RE_LOWER.test(r)) return false;
            if (n === "isupper" && !RE_UPPER.test(r)) return false;
          }
        }
        return hasCased;
      };
    case "swapcase":
      return function* (I, args, kw) {
        noKw(n, kw);
        const points = chars(s), parts: string[] = [];
        for (let index = 0; index < points.length; index++) {
          const r = points[index];
          if (RE_UPPER.test(r) || RE_TITLE.test(r)) parts.push(lowerAt(points, index));
          else if (RE_LOWER.test(r)) parts.push(r.toUpperCase());
          else parts.push(r);
        }
        return strJoin(parts);
      };
  }
  return null;
}

// --------------------------------------------------------------------------
// list methods

function listMethod(l: DiasporeList, n: string): MFn | null {
  switch (n) {
    case "append":
      return function* (I, args) {
        needArgs(n, args, 1, 1);
        l.items.push(args[0]);
        return NONE;
      };
    case "extend":
      return function* (I, args) {
        needArgs(n, args, 1, 1);
        const items = yield* iterToArray(I, args[0]);
        l.items.push(...items);
        return NONE;
      };
    case "insert":
      return function* (I, args) {
        needArgs(n, args, 2, 2);
        let i = Number(wantInt("index", args[0]));
        if (i < 0) {
          i += l.items.length;
          if (i < 0) i = 0;
        } else if (i > l.items.length) {
          i = l.items.length;
        }
        l.items.splice(i, 0, args[1]);
        return NONE;
      };
    case "pop":
      return function* (I, args) {
        needArgs(n, args, 0, 1);
        if (l.items.length === 0) throw diasporeErr("IndexError", "pop from empty list");
        let i = l.items.length - 1;
        if (args.length === 1) {
          i = Number(wantInt("index", args[0]));
          if (i < 0) i += l.items.length;
          if (i < 0 || i >= l.items.length) {
            throw diasporeErr("IndexError", "pop index out of range");
          }
        }
        return l.items.splice(i, 1)[0];
      };
    case "remove":
      return function* (I, args) {
        needArgs(n, args, 1, 1);
        for (let i = 0; i < l.items.length; i++) {
          if (yield* eqElem(I, l.items[i], args[0])) {
            l.items.splice(i, 1);
            return NONE;
          }
        }
        throw diasporeErr("ValueError", "list.remove(x): x not in list");
      };
    case "clear":
      return function* () {
        l.items.length = 0;
        return NONE;
      };
    case "index":
      return function* (I, args) {
        needArgs(n, args, 1, 3);
        const i = yield* seqIndexOf(I, l.items, args);
        if (i < 0) {
          throw diasporeErr("ValueError", `${yield* I.repr(args[0])} is not in list`);
        }
        return BigInt(i);
      };
    case "count":
      return function* (I, args) {
        needArgs(n, args, 1, 1);
        let count = 0n;
        for (const x of l.items) {
          if (yield* eqElem(I, x, args[0])) count++;
        }
        return count;
      };
    case "reverse":
      return function* () {
        l.items.reverse();
        return NONE;
      };
    case "sort":
      return function* (I, args, kw) {
        if (args.length > 0) {
          throw diasporeErr("TypeError", "sort() takes no positional arguments");
        }
        const [key, reverse] = yield* keyReverseKw("sort", I, kw);
        yield* I.sortValues(l.items, key, reverse);
        return NONE;
      };
    case "copy":
      return function* () {
        return new DiasporeList([...l.items]);
      };
  }
  return null;
}

function* seqIndexOf(I: Interp, items: Value[], args: Value[]): G<number> {
  let start = 0;
  let end = items.length;
  if (args.length >= 2) {
    start = Number(wantInt("start", args[1]));
    if (start < 0) {
      start += items.length;
      if (start < 0) start = 0;
    }
  }
  if (args.length >= 3) {
    end = Number(wantInt("stop", args[2]));
    if (end < 0) end += items.length;
    if (end > items.length) end = items.length;
  }
  for (let i = start; i < end && i < items.length; i++) {
    if (yield* eqElem(I, items[i], args[0])) return i;
  }
  return -1;
}

// --------------------------------------------------------------------------
// dict methods

function dictMethod(d: DiasporeDict, n: string): MFn | null {
  if (d instanceof DiasporeMappingProxy &&
      n !== "get" && n !== "keys" && n !== "values" && n !== "items" && n !== "copy") {
    return null;
  }
  switch (n) {
    case "get":
      return function* (I, args) {
        needArgs(n, args, 1, 2);
        const v = d.get(args[0]);
        if (v !== undefined) return v;
        return args.length === 2 ? args[1] : NONE;
      };
    case "keys":
    case "values":
    case "items":
      return function* (_I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 0, 0);
        return new DiasporeDictView(d, n);
      };
    case "pop":
      return function* (I, args) {
        needArgs(n, args, 1, 2);
        const v = d.delete(args[0]);
        if (v !== undefined) return v;
        if (args.length === 2) return args[1];
        throw new DiasporeError(makeExc("KeyError", args[0]));
      };
    case "update":
      return function* (I, args, kw) {
        needArgs(n, args, 0, 1);
        if (args.length === 1) {
          yield* dictUpdateFrom(I, d, args[0]);
        }
        for (const [k, v] of kw) d.setHK("s" + k, k, v);
        return NONE;
      };
    case "setdefault":
      return function* (I, args) {
        needArgs(n, args, 1, 2);
        const v = d.get(args[0]);
        if (v !== undefined) return v;
        const dv: Value = args.length === 2 ? args[1] : NONE;
        d.set(args[0], dv);
        return dv;
      };
    case "clear":
      return function* () {
        d.clear();
        return NONE;
      };
    case "copy":
      return function* () {
        return d.copy();
      };
  }
  return null;
}

import { dictUpdateFrom } from "./builtins.ts";

// --------------------------------------------------------------------------
// set methods

function setMethod(s: DiasporeSet, n: string): MFn | null {
  if (s instanceof DiasporeFrozenSet && FROZENSET_METHODS[n] !== true) return null;
  if (s instanceof DiasporeFrozenSet && n === "__hash__") {
    return function* (_I, args, kw) {
      noKw(n, kw);
      needArgs(n, args, 0, 0);
      return hashValue(s);
    };
  }
  switch (n) {
    case "pop":
      return function* (_I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 0, 0);
        return s.pop();
      };
    case "add":
      return function* (I, args) {
        needArgs(n, args, 1, 1);
        s.add(args[0]);
        return NONE;
      };
    case "remove":
      return function* (I, args) {
        needArgs(n, args, 1, 1);
        if (!s.discard(args[0])) throw new DiasporeError(makeExc("KeyError", args[0]));
        return NONE;
      };
    case "discard":
      return function* (I, args) {
        needArgs(n, args, 1, 1);
        s.discard(args[0]);
        return NONE;
      };
    case "update":
    case "difference_update":
      return function* (I, args, kw) {
        noKw(n, kw);
        for (const arg of args) {
          const raw = arg instanceof DiasporeInstance && arg.prim !== null ? arg.prim : arg;
          if (raw === s) {
            if (n === "difference_update") s.clear();
            continue;
          }
          if (raw instanceof DiasporeSet) {
            for (const [slot, item] of raw.map) {
              I.fuel();
              const hash = storedHashKey(raw.map, slot);
              if (n === "update") s.addHK(hash, item);
              else s.discardHK(hash, item);
            }
          } else if (n === "update" && raw === arg && raw instanceof DiasporeDict && !(raw instanceof DiasporeMappingProxy)) {
            for (const [slot, entry] of raw.map) {
              I.fuel();
              s.addHK(storedHashKey(raw.map, slot), entry.key);
            }
          } else {
            // These methods commit each item before requesting the next one.
            const it = yield* getIter(I, arg);
            for (;;) {
              I.fuel();
              const [item, ok] = yield* it.next();
              if (!ok) break;
              const hash = hashKey(item);
              if (n === "update") s.addHK(hash, item);
              else s.discardHK(hash, item);
            }
          }
        }
        return NONE;
      };
    case "intersection_update":
      return function* (I, args, kw) {
        noKw(n, kw);
        let current = s;
        for (const arg of args) {
          const raw = arg instanceof DiasporeInstance && arg.prim !== null ? arg.prim : arg;
          if (raw === current) continue;
          const result = new DiasporeSet();
          if (raw instanceof DiasporeSet) {
            const small = raw.size > current.size ? current : raw;
            const large = small === current ? raw : current;
            for (const [slot, item] of small.map) {
              I.fuel();
              const hash = storedHashKey(small.map, slot);
              if (large.hasHK(hash, item)) result.addHK(hash, item);
            }
          } else {
            const it = yield* getIter(I, arg);
            for (;;) {
              I.fuel();
              const [item, ok] = yield* it.next();
              if (!ok) break;
              const hash = hashKey(item);
              if (current.hasHK(hash, item)) {
                result.addHK(hash, item);
                if (result.size >= current.size) break;
              }
            }
          }
          current = result;
        }
        // Intersection commits only after every input completed successfully.
        if (current !== s) s.map = current.map;
        return NONE;
      };
    case "symmetric_difference_update":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const raw = args[0] instanceof DiasporeInstance && args[0].prim !== null ? args[0].prim : args[0];
        if (raw === s) { s.clear(); return NONE; }
        if (raw === args[0] && raw instanceof DiasporeDict && !(raw instanceof DiasporeMappingProxy)) {
          for (const [slot, entry] of raw.map) {
            I.fuel();
            const hash = storedHashKey(raw.map, slot);
            if (!s.discardHK(hash, entry.key)) s.addHK(hash, entry.key);
          }
          return NONE;
        }
        let other: DiasporeSet;
        if (raw instanceof DiasporeSet) other = raw;
        else {
          // Collapse duplicates before mutating the receiver.
          other = new DiasporeSet();
          const it = yield* getIter(I, args[0]);
          for (;;) {
            I.fuel();
            const [item, ok] = yield* it.next();
            if (!ok) break;
            other.add(item);
          }
        }
        for (const [slot, item] of other.map) {
          I.fuel();
          const hash = storedHashKey(other.map, slot);
          if (!s.discardHK(hash, item)) s.addHK(hash, item);
        }
        return NONE;
      };
    case "clear":
      return function* () {
        s.clear();
        return NONE;
      };
    case "copy":
      return function* (_I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 0, 0);
        return s.copy();
      };
    case "union":
    case "intersection":
    case "difference":
    case "symmetric_difference":
      return function* (I, args, kw) {
        noKw(n, kw);
        if (n === "symmetric_difference") needArgs(n, args, 1, 1);
        let out = s.copy();
        const op =
          n === "union"
            ? "bitor"
            : n === "intersection"
              ? "bitand"
              : n === "difference"
                ? "sub"
                : "bitxor";
        for (const a of args) {
          const other = yield* toSet(I, a);
          out = setOp(op, out, other);
        }
        return out;
      };
    case "issubset":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        return setIsSubset(s, yield* toSet(I, args[0]));
      };
    case "issuperset":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        return setIsSubset(yield* toSet(I, args[0]), s);
      };
    case "isdisjoint":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const it = yield* getIter(I, args[0]);
        for (;;) {
          I.fuel();
          const [item, ok] = yield* it.next();
          if (!ok) return true;
          if (s.has(item)) return false;
        }
      };
  }
  return null;
}

function* toSet(I: Interp, v: Value): G<DiasporeSet> {
  if (v instanceof DiasporeSet) return v;
  const out = new DiasporeSet();
  for (const x of yield* iterToArray(I, v)) out.add(x);
  return out;
}

// --------------------------------------------------------------------------
// tuple methods

function tupleMethod(t: DiasporeTuple, n: string): MFn | null {
  switch (n) {
    case "count":
      return function* (I, args) {
        needArgs(n, args, 1, 1);
        let count = 0n;
        for (const x of t.items) {
          if (yield* eqElem(I, x, args[0])) count++;
        }
        return count;
      };
    case "index":
      return function* (I, args) {
        needArgs(n, args, 1, 3);
        const i = yield* seqIndexOf(I, t.items, args);
        if (i < 0) throw diasporeErr("ValueError", "tuple.index(x): x not in tuple");
        return BigInt(i);
      };
  }
  return null;
}

// --------------------------------------------------------------------------
// bytes methods

/**
 * Parse bytes.fromhex text with Python code-point error offsets.
 * Non-ASCII input is rejected before parsing ASCII digit pairs.
 */
export function parseBytesHex(value: PyString): Uint8Array {
  let length = 0;
  for (const point of value) {
    if (point.codePointAt(0)! >= 128) {
      throw diasporeErr("ValueError", `non-hexadecimal number found in fromhex() arg at position ${length}`);
    }
    length++;
  }
  const text = strText(value), result = new Uint8Array(Math.floor(length / 2));
  let offset = 0;
  for (let index = 0; index < length;) {
    const code = text.charCodeAt(index);
    if (code === 32 || code >= 9 && code <= 13) { index++; continue; }
    let byte = 0;
    for (let digit = 0; digit < 2; digit++, index++) {
      const point = text.charCodeAt(index), lower = point | 32;
      const nibble = point >= 48 && point <= 57 ? point - 48 : lower >= 97 && lower <= 102 ? lower - 87 : -1;
      if (nibble < 0) throw diasporeErr("ValueError", `non-hexadecimal number found in fromhex() arg at position ${index}`);
      byte = byte << 4 | nibble;
    }
    result[offset++] = byte;
  }
  return offset === result.length ? result : result.subarray(0, offset);
}

/** Build a 256-byte translation table from two equal-length buffer exports. */
export function makeBytesTranslation(args: Value[], kw: KwArgs): Uint8Array {
  noKw("maketrans", kw);
  needArgs("maketrans", args, 2, 2);
  const from = acquireBuffer(args[0]);
  if (from === null) throw diasporeErr("TypeError", `a bytes-like object is required, not '${typeName(args[0])}'`);
  try {
    const to = acquireBuffer(args[1]);
    if (to === null) throw diasporeErr("TypeError", `a bytes-like object is required, not '${typeName(args[1])}'`);
    try {
      if (from.data.length !== to.data.length) throw diasporeErr("ValueError", "maketrans arguments must have same length");
      const table = new Uint8Array(256);
      for (let byte = 0; byte < table.length; byte++) table[byte] = byte;
      for (let index = 0; index < from.data.length; index++) table[from.data[index]] = to.data[index];
      return table;
    } finally { to.release(); }
  } finally { from.release(); }
}

function bytesMethod(b: Uint8Array, n: string): MFn | null {
  switch (n) {
    case "isascii":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 0, 0);
        for (const byte of b) {
          I.fuel();
          if (byte >= 128) return false;
        }
        return true;
      };
    case "ljust":
    case "rjust":
    case "center":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 2);
        const width = yield* indexInteger(I, args[0]);
        if (width < -(1n << 63n) || width >= 1n << 63n) throw diasporeErr("OverflowError", "Python int too large to convert to C ssize_t");
        let fill = 32;
        if (args.length === 2) {
          const value = args[1], raw = value instanceof DiasporeInstance && value.prim !== null ? value.prim : value;
          const bytearray = value instanceof DiasporeInstance && value.cls.mro.some(cls => cls === I.builtins.get("bytearray"));
          if (!(raw instanceof Uint8Array) && !bytearray) throw diasporeErr("TypeError", `${n}() argument 2 must be a byte string of length 1, not ${typeName(value)}`);
          const lease = acquireBuffer(raw)!;
          try {
            if (lease.data.length !== 1) throw diasporeErr("TypeError", `${n}() argument 2 must be a byte string of length 1, not ${typeName(value)}`);
            fill = lease.data[0];
          } finally { lease.release(); }
        }
        if (width <= BigInt(b.length)) return b;
        const size = Number(width), margin = size - b.length;
        const left = n === "rjust" ? margin : n === "center" ? Math.floor(margin / 2) + (margin % 2 && size % 2 ? 1 : 0) : 0;
        I.fuelN(Math.floor(size / 16) + 1);
        let result: Uint8Array;
        try { result = new Uint8Array(size); }
        catch (error) {
          if (error instanceof RangeError) throw diasporeErr("MemoryError", "");
          throw error;
        }
        result.fill(fill);
        result.set(b, left);
        return result;
      };
    case "maketrans":
      return function* (_I, args, kw) { return makeBytesTranslation(args, kw); };
    case "translate":
      return function* (I, args, kw) {
        needArgs(n, args, 1, 2);
        for (const key of kw.keys()) {
          if (key !== "delete") throw diasporeErr("TypeError", `'${key}' is an invalid keyword argument for translate()`);
          if (args.length === 2) throw diasporeErr("TypeError", "argument for translate() given by name ('delete') and position (2)");
        }
        const table = args[0] instanceof PyNoneT ? null : acquireBuffer(args[0]);
        if (table === null && !(args[0] instanceof PyNoneT)) throw diasporeErr("TypeError", `a bytes-like object is required, not '${typeName(args[0])}'`);
        try {
          if (table !== null && table.data.length !== 256) throw diasporeErr("ValueError", "translation table must be 256 characters long");
          const deleteArg = args[1] ?? kw.get("delete");
          const deletion = deleteArg === undefined ? null : acquireBuffer(deleteArg);
          if (deletion === null && deleteArg !== undefined) throw diasporeErr("TypeError", `a bytes-like object is required, not '${typeName(deleteArg)}'`);
          try {
            const removed = deletion === null || deletion.data.length === 0 ? null : new Uint8Array(256);
            if (removed !== null && deletion !== null) for (const byte of deletion.data) removed[byte] = 1;
            let size = b.length;
            if (removed !== null) for (const byte of b) size -= removed[byte];
            I.fuelN(Math.floor(b.length / 16) + 1);
            const result = new Uint8Array(size);
            let offset = 0;
            for (const byte of b) {
              if (removed !== null && removed[byte]) continue;
              result[offset++] = table === null ? byte : table.data[byte];
            }
            return result;
          } finally { deletion?.release(); }
        } finally { table?.release(); }
      };
    case "join":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const leases: BufferExport[] = [];
        try {
          let size = 0;
          for (const value of yield* iterToArray(I, args[0])) {
            const lease = acquireBuffer(value);
            if (lease === null) throw diasporeErr("TypeError", "sequence item is not a bytes-like object");
            leases.push(lease);
            size += lease.data.length;
          }
          size += Math.max(0, leases.length - 1) * b.length;
          I.fuelN(Math.floor(size / 16) + 1);
          const result = new Uint8Array(size);
          let offset = 0;
          for (let index = 0; index < leases.length; index++) {
            if (index) { result.set(b, offset); offset += b.length; }
            result.set(leases[index].data, offset); offset += leases[index].data.length;
          }
          return result;
        } finally { for (const lease of leases) lease.release(); }
      };
    case "hex":
      return function* (_I, args, kw) {
        needArgs(n, args, 0, 2);
        const separator = args[0] ?? kw.get("sep");
        const group = asIndex(args[1] ?? kw.get("bytes_per_sep") ?? 1n);
        if (group === null) throw diasporeErr("TypeError", "bytes_per_sep must be an integer");
        let sep = "";
        if (separator !== undefined) {
          if (isPyString(separator)) sep = strText(separator);
          else if (separator instanceof Uint8Array) sep = String.fromCharCode(...separator);
          else throw diasporeErr("TypeError", "sep must be str or bytes");
          if (sep.length !== 1 || sep.charCodeAt(0) > 127) throw diasporeErr("ValueError", "sep must be length 1 and ASCII");
        }
        let result = "";
        const count = Number(group < 0n ? -group : group);
        for (let index = 0; index < b.length; index++) {
          if (index && count && (group < 0n ? index % count === 0 : (b.length - index) % count === 0)) result += sep;
          result += b[index].toString(16).padStart(2, "0");
        }
        return result;
      };
    case "decode":
      return function* (I, args, kw) {
        needArgs(n, args, 0, 2);
        return yield* textCodec(I, "decode", b,
          wantStr("encoding", args[0] ?? kw.get("encoding") ?? "utf-8"),
          wantStr("errors", args[1] ?? kw.get("errors") ?? "strict"));
      };
    case "find":
      return function* (I, args) {
        needArgs(n, args, 1, 1);
        if (!(args[0] instanceof Uint8Array)) {
          throw diasporeErr(
            "TypeError",
            `argument should be a bytes-like object, not '${typeName(args[0])}'`,
          );
        }
        return BigInt(bytesIndexOf(b, args[0]));
      };
    case "split":
      return function* (I, args) {
        needArgs(n, args, 0, 2);
        let parts: Uint8Array[];
        if (args.length === 0 || args[0] instanceof PyNoneT) {
          parts = bytesFields(b);
        } else {
          const sep = args[0];
          if (!(sep instanceof Uint8Array)) {
            throw diasporeErr(
              "TypeError",
              `a bytes-like object is required, not '${typeName(sep)}'`,
            );
          }
          if (sep.length === 0) throw diasporeErr("ValueError", "empty separator");
          parts = [];
          let idx = 0;
          for (;;) {
            const at = bytesIndexOf(b, sep, idx);
            if (at < 0) break;
            parts.push(b.subarray(idx, at));
            idx = at + sep.length;
          }
          parts.push(b.subarray(idx));
        }
        return new DiasporeList(parts.map((p) => new Uint8Array(p) as Value));
      };
    case "strip":
    case "lstrip":
    case "rstrip":
      return function* (I, args) {
        needArgs(n, args, 0, 1);
        const set = new Set<number>();
        if (args.length === 0 || args[0] instanceof PyNoneT) {
          for (const c of [0x20, 0x09, 0x0a, 0x0d, 0x0b, 0x0c]) set.add(c);
        } else {
          const cut = args[0];
          if (!(cut instanceof Uint8Array)) {
            throw diasporeErr(
              "TypeError",
              `a bytes-like object is required, not '${typeName(cut)}'`,
            );
          }
          for (const c of cut) set.add(c);
        }
        let lo = 0;
        let hi = b.length;
        if (n !== "rstrip") while (lo < hi && set.has(b[lo])) lo++;
        if (n !== "lstrip") while (hi > lo && set.has(b[hi - 1])) hi--;
        return new Uint8Array(b.subarray(lo, hi));
      };
    case "replace":
      return function* (I, args) {
        needArgs(n, args, 2, 3);
        const old = args[0];
        const rep = args[1];
        if (!(old instanceof Uint8Array)) {
          throw diasporeErr("TypeError", `a bytes-like object is required, not '${typeName(old)}'`);
        }
        if (!(rep instanceof Uint8Array)) {
          throw diasporeErr("TypeError", `a bytes-like object is required, not '${typeName(rep)}'`);
        }
        let count = -1;
        if (args.length === 3) count = Number(wantInt("count", args[2]));
        const out: number[] = [];
        let idx = 0;
        let done = 0;
        if (old.length > 0) {
          for (;;) {
            if (count >= 0 && done >= count) break;
            const at = bytesIndexOf(b, old, idx);
            if (at < 0) break;
            for (let i = idx; i < at; i++) out.push(b[i]);
            for (const c of rep) out.push(c);
            idx = at + old.length;
            done++;
          }
        }
        for (let i = idx; i < b.length; i++) out.push(b[i]);
        return new Uint8Array(out);
      };
    case "startswith":
    case "endswith":
      return function* (I, args) {
        needArgs(n, args, 1, 1);
        const pats = args[0] instanceof DiasporeTuple ? args[0].items : [args[0]];
        for (const p of pats) {
          if (!(p instanceof Uint8Array)) {
            throw diasporeErr(
              "TypeError",
              `${n} first arg must be bytes or a tuple of bytes, not ${typeName(p)}`,
            );
          }
          if (p.length > b.length) continue;
          const off = n === "startswith" ? 0 : b.length - p.length;
          let all = true;
          for (let i = 0; i < p.length; i++) {
            if (b[off + i] !== p[i]) {
              all = false;
              break;
            }
          }
          if (all) return true;
        }
        return false;
      };
    case "upper":
    case "lower":
      return function* () {
        const out = new Uint8Array(b.length);
        for (let i = 0; i < b.length; i++) {
          let c = b[i];
          if (n === "upper" && c >= 0x61 && c <= 0x7a) c -= 32;
          else if (n === "lower" && c >= 0x41 && c <= 0x5a) c += 32;
          out[i] = c;
        }
        return out;
      };
  }
  return null;
}

function bytesFields(b: Uint8Array): Uint8Array[] {
  const ws = new Set([0x20, 0x09, 0x0a, 0x0d, 0x0b, 0x0c]);
  const out: Uint8Array[] = [];
  let i = 0;
  while (i < b.length) {
    while (i < b.length && ws.has(b[i])) i++;
    if (i >= b.length) break;
    let j = i;
    while (j < b.length && !ws.has(b[j])) j++;
    out.push(b.subarray(i, j));
    i = j;
  }
  return out;
}

