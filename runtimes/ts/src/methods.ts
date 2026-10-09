/* Methods on builtin types (spec 9.1), implemented natively with CPython
 * semantics (code-point string indexing, Python split/strip/title rules). */

import {
  type Value,
  type G,
  type KwArgs,
  NONE,
  PyNoneT,
  DiasporeList,
  DiasporeTuple,
  DiasporeDict,
  DiasporeDictView,
  isSetView,
  DiasporeMappingProxy,
  DiasporeSet,
  DiasporeFrozenSet,
  Builtin,
  typeName,
  asIndex,
  chars,
  diasporeErr,
  DiasporeError,
  makeExc,
  unicodeCodecError,
  bytesIndexOf,
  hashValue,
} from "./values.ts";
import { reprString } from "./format.ts";
import { getIter, iterToArray } from "./iter.ts";
import { needArgs, noKw, wantInt, wantStr } from "./helpers.ts";
import { eqElem, setOp, setIsSubset } from "./ops.ts";
import { keyReverseKw, encodeStrTo } from "./builtins.ts";
import type { Interp } from "./interp.ts";

type MFn = (I: Interp, args: Value[], kw: KwArgs) => G<Value>;

/** Resolve a bound method on a primitive sandbox value. */
export function builtinMethod(I: Interp, o: Value, n: string): Value {
  let fn: MFn | null = null;
  if (typeof o === "string") fn = strMethod(o, n);
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
  "isupper", "swapcase",
]);
const LIST_METHODS = new Set([
  "append", "extend", "insert", "pop", "remove", "clear", "index", "count",
  "reverse", "sort", "copy",
]);
const DICT_METHODS = new Set([
  "get", "keys", "values", "items", "pop", "update", "setdefault", "clear", "copy",
]);
const SET_METHODS = new Set([
  "add", "remove", "discard", "clear", "copy", "union", "intersection",
  "difference", "symmetric_difference", "issubset", "issuperset",
  "isdisjoint",
]);
const FROZENSET_METHODS: Record<string, true> = {
  copy: true, union: true, intersection: true, difference: true,
  symmetric_difference: true, issubset: true, issuperset: true, isdisjoint: true,
  __hash__: true,
};
const TUPLE_METHODS = new Set(["count", "index"]);
const BYTES_METHODS = new Set([
  "decode", "find", "split", "strip", "lstrip", "rstrip", "replace",
  "startswith", "endswith", "upper", "lower",
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

/** Builtin-type name for a raw primitive value (for prim method routing). */
export function primTypeName(v: Value): string | null {
  if (typeof v === "string") return "str";
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
const RE_LETTER = /\p{L}/u;
const RE_ND = /\p{Nd}/u;
const RE_NUM = /\p{N}/u;
const RE_SPACE = /[\t\n\v\f\r \x1c-\x1f\x85\u2028\u2029]|\p{Zs}/u;

function isCased(r: string): boolean {
  return RE_LOWER.test(r) || RE_UPPER.test(r) || RE_TITLE.test(r);
}

function isSpaceCh(r: string): boolean {
  return RE_SPACE.test(r);
}

// --------------------------------------------------------------------------
// str helpers

function pyTitle(s: string): string {
  let b = "";
  let prevCased = false;
  for (const r of s) {
    const cased = isCased(r);
    if (cased) {
      b += prevCased ? r.toLowerCase() : r.toUpperCase();
    } else {
      b += r;
    }
    prevCased = cased;
  }
  return b;
}

function pyCasefold(s: string): string {
  let b = "";
  for (const r of s) {
    if (r === "ß" || r === "ẞ") b += "ss";
    else b += r.toLowerCase();
  }
  return b;
}

function splitWhitespace(s: string, maxsplit: number, fromRight: boolean): string[] {
  const fields = s.split(/\s+/u).filter((x) => x !== "");
  if (maxsplit < 0 || fields.length - 1 <= maxsplit) return fields;
  if (!fromRight) {
    const parts: string[] = [];
    let rest = s.replace(/^\s+/u, "");
    while (parts.length < maxsplit && rest !== "") {
      const m = /\s/u.exec(rest);
      if (m === null) break;
      parts.push(rest.slice(0, m.index));
      rest = rest.slice(m.index).replace(/^\s+/u, "");
    }
    if (rest !== "") parts.push(rest);
    return parts;
  }
  let rest = s.replace(/\s+$/u, "");
  const tail: string[] = [];
  while (tail.length < maxsplit && rest !== "") {
    let idx = -1;
    for (let i = rest.length - 1; i >= 0; i--) {
      if (/\s/u.test(rest[i])) {
        idx = i;
        break;
      }
    }
    if (idx < 0) break;
    tail.unshift(rest.slice(idx + 1));
    rest = rest.slice(0, idx).replace(/\s+$/u, "");
  }
  if (rest !== "") tail.unshift(rest);
  return tail;
}

function splitLines(s: string): string[] {
  const out: string[] = [];
  let cur = "";
  const rs = chars(s);
  for (let i = 0; i < rs.length; i++) {
    const r = rs[i];
    const c = r.codePointAt(0) as number;
    if (
      r === "\n" ||
      r === "\v" ||
      r === "\f" ||
      c === 0x1c ||
      c === 0x1d ||
      c === 0x1e ||
      c === 0x85 ||
      c === 0x2028 ||
      c === 0x2029
    ) {
      out.push(cur);
      cur = "";
    } else if (r === "\r") {
      if (i + 1 < rs.length && rs[i + 1] === "\n") i++;
      out.push(cur);
      cur = "";
    } else {
      cur += r;
    }
  }
  if (cur.length > 0) out.push(cur);
  return out;
}

function strList(parts: string[]): DiasporeList {
  return new DiasporeList(parts.map((p) => p as Value));
}

/** Resolves optional start/end arguments against a code-point array. */
function strRegion(rs: string[], bounds: Value[]): [number, number] {
  let start = 0;
  let end = rs.length;
  const adjust = (v: Value, def: number): number => {
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
    } else if (i > rs.length) {
      i = rs.length;
    }
    return i;
  };
  if (bounds.length >= 1) start = adjust(bounds[0], 0);
  if (bounds.length >= 2) end = adjust(bounds[1], rs.length);
  if (end < start) end = start;
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

function pyReplace(s: string, old: string, rep: string, count: number): string {
  if (count === 0) return s;
  if (old === "") {
    let out = "";
    let done = 0;
    const rs = chars(s);
    for (let i = 0; i < rs.length; i++) {
      if (count < 0 || done < count) {
        out += rep;
        done++;
      }
      out += rs[i];
    }
    if (count < 0 || done < count) out += rep;
    return out;
  }
  let out = "";
  let idx = 0;
  let done = 0;
  for (;;) {
    if (count >= 0 && done >= count) break;
    const at = s.indexOf(old, idx);
    if (at < 0) break;
    out += s.slice(idx, at) + rep;
    idx = at + old.length;
    done++;
  }
  return out + s.slice(idx);
}

// --------------------------------------------------------------------------
// str methods

function strMethod(s: string, n: string): MFn | null {
  switch (n) {
    case "upper":
      return function* (I, args, kw) {
        noKw(n, kw);
        return s.toUpperCase();
      };
    case "lower":
      return function* (I, args, kw) {
        noKw(n, kw);
        return s.toLowerCase();
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
        return rs[0].toUpperCase() + rs.slice(1).join("").toLowerCase();
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
        return rs.join("");
      };
    case "split":
    case "rsplit":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 0, 2);
        let maxsplit = -1;
        if (args.length >= 2) maxsplit = Number(wantInt("maxsplit", args[1]));
        let sep: string | null = null;
        if (args.length >= 1 && !(args[0] instanceof PyNoneT)) {
          sep = wantStr("sep", args[0]);
          if (sep === "") throw diasporeErr("ValueError", "empty separator");
        }
        let parts: string[];
        if (sep === null) {
          parts = splitWhitespace(s, maxsplit, n === "rsplit");
        } else if (n === "split") {
          if (maxsplit < 0) {
            parts = s.split(sep);
          } else {
            parts = [];
            let rest = s;
            while (parts.length < maxsplit) {
              const at = rest.indexOf(sep);
              if (at < 0) break;
              parts.push(rest.slice(0, at));
              rest = rest.slice(at + sep.length);
            }
            parts.push(rest);
          }
        } else {
          parts = s.split(sep);
          if (maxsplit >= 0 && parts.length - 1 > maxsplit) {
            const keep = parts.length - maxsplit;
            const head = parts.slice(0, keep).join(sep);
            parts = [head, ...parts.slice(keep)];
          }
        }
        return strList(parts);
      };
    case "splitlines":
      return function* (I, args, kw) {
        noKw(n, kw);
        return strList(splitLines(s));
      };
    case "join":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const items = yield* iterToArray(I, args[0]);
        const parts: string[] = [];
        for (let i = 0; i < items.length; i++) {
          const x = items[i];
          if (typeof x !== "string") {
            throw diasporeErr(
              "TypeError",
              `sequence item ${i}: expected str instance, ${typeName(x)} found`,
            );
          }
          parts.push(x);
        }
        return parts.join(s);
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
        if (n === "count") return BigInt(countRunes(rs.slice(start, end), Array.from(sub)));
        const idx = findRunes(rs, Array.from(sub), start, end, n === "rfind");
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
        const region = rs.slice(start, end).join("");
        const pats = args[0] instanceof DiasporeTuple ? args[0].items : [args[0]];
        for (const p of pats) {
          if (typeof p !== "string") {
            throw diasporeErr(
              "TypeError",
              `${n} first arg must be str or a tuple of str, not ${typeName(p)}`,
            );
          }
          if (n === "startswith" && region.startsWith(p)) return true;
          if (n === "endswith" && region.endsWith(p)) return true;
        }
        return false;
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
          body = rs.slice(1).join("");
        }
        return sign + "0".repeat(w - rs.length) + body;
      };
    case "ljust":
    case "rjust":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 2);
        const w = Number(wantInt("width", args[0]));
        let fill = " ";
        if (args.length === 2) {
          const f = wantStr("fillchar", args[1]);
          if (chars(f).length !== 1) {
            throw diasporeErr("TypeError", "The fill character must be exactly one character long");
          }
          fill = f;
        }
        const pad = w - chars(s).length;
        if (pad <= 0) return s;
        return n === "ljust" ? s + fill.repeat(pad) : fill.repeat(pad) + s;
      };
    case "encode":
      return function* (I, args, kw) {
        needArgs(n, args, 0, 1);
        let enc = "utf-8";
        if (args.length === 1) enc = wantStr("encoding", args[0]);
        const kv = kw.get("encoding");
        if (kv !== undefined) enc = wantStr("encoding", kv);
        return encodeStrTo(s, enc);
      };
    case "partition":
    case "rpartition":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const sep = wantStr("sep", args[0]);
        if (sep === "") throw diasporeErr("ValueError", "empty separator");
        let i: number;
        if (n === "partition") {
          i = s.indexOf(sep);
          if (i < 0) return new DiasporeTuple([s, "", ""]);
        } else {
          i = s.lastIndexOf(sep);
          if (i < 0) return new DiasporeTuple(["", "", s]);
        }
        return new DiasporeTuple([s.slice(0, i), sep, s.slice(i + sep.length)]);
      };
    case "removeprefix":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const p = wantStr("prefix", args[0]);
        return p !== "" && s.startsWith(p) ? s.slice(p.length) : s;
      };
    case "removesuffix":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 1);
        const p = wantStr("suffix", args[0]);
        return p !== "" && s.endsWith(p) ? s.slice(0, s.length - p.length) : s;
      };
    case "center":
      return function* (I, args, kw) {
        noKw(n, kw);
        needArgs(n, args, 1, 2);
        const w = Number(wantInt("width", args[0]));
        let fill = " ";
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
        return fill.repeat(left) + s + fill.repeat(marg - left);
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
        let b = "";
        for (const r of s) {
          if (RE_UPPER.test(r) || RE_TITLE.test(r)) b += r.toLowerCase();
          else if (RE_LOWER.test(r)) b += r.toUpperCase();
          else b += r;
        }
        return b;
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

function bytesMethod(b: Uint8Array, n: string): MFn | null {
  switch (n) {
    case "decode":
      return function* (I, args, kw) {
        needArgs(n, args, 0, 1);
        let enc = "utf-8";
        if (args.length === 1) enc = wantStr("encoding", args[0]);
        const kv = kw.get("encoding");
        if (kv !== undefined) enc = wantStr("encoding", kv);
        return decodeBytes(b, enc);
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

export function decodeBytes(b: Uint8Array, enc: string): string {
  switch (enc.toLowerCase().replace(/_/g, "-")) {
    case "utf-8":
    case "utf8":
      try {
        return new TextDecoder("utf-8", { fatal: true }).decode(b);
      } catch {
        throw diasporeErr("ValueError", "'utf-8' codec can't decode bytes: invalid data");
      }
    case "ascii":
    case "us-ascii":
    case "646": {
      for (let i = 0; i < b.length; i++) {
        if (b[i] > 0x7f) {
          throw unicodeCodecError("UnicodeDecodeError", "ascii", b, i, i + 1, "ordinal not in range(128)");
        }
      }
      return new TextDecoder("utf-8").decode(b);
    }
    case "latin-1":
    case "latin1":
    case "iso-8859-1": {
      let out = "";
      for (const c of b) out += String.fromCharCode(c);
      return out;
    }
  }
  throw diasporeErr("ValueError", `unknown encoding: ${enc}`);
}
