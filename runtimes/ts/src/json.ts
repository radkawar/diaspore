/* Private CPython JSON accelerator and host snapshot JSON helpers (spec 11.3). */

import {
  type Value,
  NONE,
  PyNoneT,
  DiasporeList,
  DiasporeTuple,
  DiasporeDict,
  asIndex,
  typeName,
  diasporeErr,
} from "./values.ts";
import {
  type G, type KwArgs, type PyString, BuiltinType, DiasporeInstance, DiasporeError,
  NativeModule, NativeObject, chars, isPyString, strFromChars, strJoin, strLen,
  makeExc,
} from "./values.ts";
import { bi } from "./builtins.ts";
import { argCount, noKw } from "./helpers.ts";
import { iterToArray } from "./iter.ts";
import { sortValues } from "./ops.ts";
import type { Interp } from "./interp.ts";
import { pyFloatStr } from "./format.ts";

/** Serialize a sandbox value using Python's JSON formatting rules. */
export function jsonDumps(v: Value, indentV: Value): string {
  let hasIndent = false;
  let indent = 0;
  if (!(indentV instanceof PyNoneT)) {
    const n = asIndex(indentV);
    if (n === null) throw diasporeErr("TypeError", "indent must be an int or None");
    hasIndent = true;
    if (n > 0n) indent = Number(n);
  }
  const out: string[] = [];
  jsonWrite(out, v, indent, hasIndent, 0);
  return out.join("");
}

function jsonWrite(b: string[], v: Value, indent: number, hasIndent: boolean, depth: number): void {
  const pad = (d: number) => {
    if (hasIndent) b.push("\n" + " ".repeat(indent * d));
  };
  const itemSep = hasIndent ? "," : ", ";
  if (v instanceof PyNoneT) {
    b.push("null");
    return;
  }
  if (typeof v === "boolean") {
    b.push(v ? "true" : "false");
    return;
  }
  if (typeof v === "bigint") {
    b.push(v.toString());
    return;
  }
  if (typeof v === "number") {
    if (v === Infinity) b.push("Infinity");
    else if (v === -Infinity) b.push("-Infinity");
    else if (Number.isNaN(v)) b.push("NaN");
    else b.push(pyFloatStr(v));
    return;
  }
  if (isPyString(v)) {
    b.push(jsonEscapeString(v));
    return;
  }
  if (v instanceof DiasporeList || v instanceof DiasporeTuple) {
    const items = v.items;
    if (items.length === 0) {
      b.push("[]");
      return;
    }
    b.push("[");
    for (let i = 0; i < items.length; i++) {
      if (i > 0) b.push(itemSep);
      pad(depth + 1);
      jsonWrite(b, items[i], indent, hasIndent, depth + 1);
    }
    pad(depth);
    b.push("]");
    return;
  }
  if (v instanceof DiasporeDict) {
    if (v.size === 0) {
      b.push("{}");
      return;
    }
    b.push("{");
    let i = 0;
    for (const [key, val] of v.entries()) {
      if (i > 0) b.push(itemSep);
      pad(depth + 1);
      if (!isPyString(key)) {
        throw diasporeErr("TypeError", "json keys must be str");
      }
      b.push(jsonEscapeString(key));
      b.push(": ");
      jsonWrite(b, val, indent, hasIndent, depth + 1);
      i++;
    }
    pad(depth);
    b.push("}");
    return;
  }
  throw diasporeErr("TypeError", `Object of type ${typeName(v)} is not JSON serializable`);
}

/** CPython json's default ensure_ascii=True escaping. */
export function jsonEscapeString(s: PyString): string {
  let b = '"';
  for (const ch of chars(s)) {
    const r = ch.codePointAt(0) as number;
    switch (ch) {
      case '"':
        b += '\\"';
        break;
      case "\\":
        b += "\\\\";
        break;
      case "\n":
        b += "\\n";
        break;
      case "\r":
        b += "\\r";
        break;
      case "\t":
        b += "\\t";
        break;
      case "\b":
        b += "\\b";
        break;
      case "\f":
        b += "\\f";
        break;
      default:
        if (r < 0x20) {
          b += "\\u" + r.toString(16).padStart(4, "0");
        } else if (r < 0x7f) {
          b += ch;
        } else if (r > 0xffff) {
          const v = r - 0x10000;
          b +=
            "\\u" +
            (0xd800 + (v >> 10)).toString(16).padStart(4, "0") +
            "\\u" +
            (0xdc00 + (v & 0x3ff)).toString(16).padStart(4, "0");
        } else {
          b += "\\u" + r.toString(16).padStart(4, "0");
        }
    }
  }
  return b + '"';
}

// --------------------------------------------------------------------------
// loads

class JsonParser {
  s: string[];
  i = 0;
  constructor(s: PyString) {
    this.s = chars(s);
  }

  fail(msg: string): never {
    throw diasporeErr("ValueError", `invalid JSON: ${msg}`);
  }

  skipWS(): void {
    while (this.i < this.s.length) {
      const c = this.s[this.i];
      if (c === " " || c === "\t" || c === "\n" || c === "\r") this.i++;
      else return;
    }
  }

  value(): Value {
    if (this.i >= this.s.length) this.fail("Expecting value");
    const c = this.s[this.i];
    if (c === "{") return this.object();
    if (c === "[") return this.array();
    if (c === '"') return this.string();
    if (c === "t" && this.lit("true")) return true;
    if (c === "f" && this.lit("false")) return false;
    if (c === "n" && this.lit("null")) return NONE;
    if (c === "N" && this.lit("NaN")) return NaN;
    if (c === "I" && this.lit("Infinity")) return Infinity;
    if (c === "-" || (c >= "0" && c <= "9")) return this.number();
    this.fail("Expecting value");
  }

  lit(word: string): boolean {
    for (let j = 0; j < word.length; j++) if (this.s[this.i + j] !== word[j]) return false;
    this.i += word.length;
    return true;
  }

  number(): Value {
    const start = this.i;
    if (this.s[this.i] === "-") {
      this.i++;
      if (this.lit("Infinity")) return -Infinity;
    }
    let isFloat = false;
    while (this.i < this.s.length) {
      const c = this.s[this.i];
      if (c >= "0" && c <= "9") {
        this.i++;
      } else if (c === "." || c === "e" || c === "E" || c === "+" || c === "-") {
        if (c === "." || c === "e" || c === "E") isFloat = true;
        this.i++;
      } else {
        break;
      }
    }
    const text = this.s.slice(start, this.i).join("");
    if (!isFloat) {
      try {
        return BigInt(text);
      } catch {
        this.fail("Expecting value");
      }
    }
    const f = Number(text);
    if (Number.isNaN(f) && text !== "NaN") this.fail("Expecting value");
    return f;
  }

  string(): PyString {
    this.i++; // opening quote
    const b: string[] = [];
    for (;;) {
      if (this.i >= this.s.length) this.fail("Unterminated string starting at");
      const c = this.s[this.i];
      if (c === '"') {
        this.i++;
        return strFromChars(b);
      }
      if (c === "\\") {
        this.i++;
        if (this.i >= this.s.length) this.fail("Unterminated string starting at");
        const e = this.s[this.i];
        switch (e) {
          case '"':
            b.push('"');
            break;
          case "\\":
            b.push("\\");
            break;
          case "/":
            b.push("/");
            break;
          case "b":
            b.push("\b");
            break;
          case "f":
            b.push("\f");
            break;
          case "n":
            b.push("\n");
            break;
          case "r":
            b.push("\r");
            break;
          case "t":
            b.push("\t");
            break;
          case "u": {
            let r = this.uEscape();
            if (
              r >= 0xd800 &&
              r <= 0xdbff &&
              this.i + 6 <= this.s.length &&
              this.s[this.i + 1] === "\\" &&
              this.s[this.i + 2] === "u"
            ) {
              const save = this.i;
              this.i += 2;
              const r2 = this.uEscape();
              if (r2 >= 0xdc00 && r2 <= 0xdfff) {
                r = 0x10000 + ((r - 0xd800) << 10) + (r2 - 0xdc00);
              } else {
                this.i = save;
              }
            }
            b.push(String.fromCodePoint(r));
            break;
          }
          default:
            this.fail("Invalid \\escape");
        }
        this.i++;
        continue;
      }
      b.push(c);
      this.i++;
    }
  }

  /** Parses the 4 hex digits after \u; on entry this.i is at 'u'. */
  uEscape(): number {
    if (this.i + 4 >= this.s.length) this.fail("Invalid \\uXXXX escape");
    const hex = this.s.slice(this.i + 1, this.i + 5).join("");
    if (!/^[0-9a-fA-F]{4}$/.test(hex)) this.fail("Invalid \\uXXXX escape");
    this.i += 4;
    return parseInt(hex, 16);
  }

  array(): Value {
    this.i++; // '['
    const out = new DiasporeList();
    this.skipWS();
    if (this.i < this.s.length && this.s[this.i] === "]") {
      this.i++;
      return out;
    }
    for (;;) {
      this.skipWS();
      out.items.push(this.value());
      this.skipWS();
      if (this.i >= this.s.length) this.fail("Expecting ',' delimiter");
      const c = this.s[this.i];
      if (c === ",") {
        this.i++;
      } else if (c === "]") {
        this.i++;
        return out;
      } else {
        this.fail("Expecting ',' delimiter");
      }
    }
  }

  object(): Value {
    this.i++; // '{'
    const out = new DiasporeDict();
    this.skipWS();
    if (this.i < this.s.length && this.s[this.i] === "}") {
      this.i++;
      return out;
    }
    for (;;) {
      this.skipWS();
      if (this.i >= this.s.length || this.s[this.i] !== '"') {
        this.fail("Expecting property name enclosed in double quotes");
      }
      const k = this.string();
      this.skipWS();
      if (this.i >= this.s.length || this.s[this.i] !== ":") {
        this.fail("Expecting ':' delimiter");
      }
      this.i++;
      this.skipWS();
      out.set(k, this.value());
      this.skipWS();
      if (this.i >= this.s.length) this.fail("Expecting ',' delimiter");
      const c = this.s[this.i];
      if (c === ",") {
        this.i++;
      } else if (c === "}") {
        this.i++;
        return out;
      } else {
        this.fail("Expecting ',' delimiter");
      }
    }
  }
}

/** Parse JSON into sandbox values with exact integer handling. */
export function jsonLoads(s: PyString): Value {
  const p = new JsonParser(s);
  p.skipWS();
  const v = p.value();
  p.skipWS();
  if (p.i !== p.s.length) p.fail("Extra data");
  return v;
}

function primitive(v: Value): Value {
  return v instanceof DiasporeInstance && v.prim !== null ? v.prim : v;
}

function jsonString(v: Value): PyString {
  const raw = primitive(v);
  if (isPyString(raw)) return raw;
  throw diasporeErr("TypeError", `first argument must be a string, not ${typeName(v)}`);
}

function encodeString(s: PyString, ascii: boolean): PyString {
  if (ascii) return jsonEscapeString(s);
  const out: string[] = ['"'];
  for (const ch of chars(s)) {
    switch (ch) {
      case '"': out.push('\\"'); break;
      case "\\": out.push("\\\\"); break;
      case "\b": out.push("\\b"); break;
      case "\f": out.push("\\f"); break;
      case "\n": out.push("\\n"); break;
      case "\r": out.push("\\r"); break;
      case "\t": out.push("\\t"); break;
      default:
        if (ch.charCodeAt(0) < 0x20) out.push("\\u" + ch.charCodeAt(0).toString(16).padStart(4, "0"));
        else out.push(ch);
    }
  }
  out.push('"');
  return strJoin(out);
}

function bindJSONArgs(name: string, args: Value[], kw: KwArgs, names: string[]): Value[] {
  if (args.length > names.length) {
    throw diasporeErr("TypeError", `${name}() takes at most ${names.length} arguments (${args.length + kw.size} given)`);
  }
  const out = args.slice();
  for (const [key, value] of kw) {
    const index = names.indexOf(key);
    if (index < 0) throw diasporeErr("TypeError", `'${key}' is an invalid keyword argument for ${name}()`);
    if (index < args.length) throw diasporeErr("TypeError", `argument for ${name}() given by name ('${key}') and position (${index + 1})`);
    out[index] = value;
  }
  for (let i = 0; i < names.length; i++) {
    if (out[i] === undefined) throw diasporeErr("TypeError", `${name}() missing required argument '${names[i]}' (pos ${i + 1})`);
  }
  return out;
}

function* jsonIndex(I: Interp, value: Value): G<bigint> {
  let n = asIndex(primitive(value));
  if (n === null && value instanceof DiasporeInstance && value.cls.lookup("__index__") !== undefined) {
    const result = yield* I.invoke(yield* I.getAttr(value, "__index__"), [], new Map());
    n = asIndex(primitive(result));
    if (n === null) throw diasporeErr("TypeError", `__index__ returned non-int (type ${typeName(result)})`);
  }
  if (n === null) throw diasporeErr("TypeError", `'${typeName(value)}' object cannot be interpreted as an integer`);
  if (n < -9223372036854775808n || n > 9223372036854775807n) {
    throw diasporeErr("OverflowError", "Python int too large to convert to C ssize_t");
  }
  return n;
}

function* decodeError(I: Interp, doc: PyString, message: string, pos: number): G<never> {
  const decoder = yield* I.importModule("json.decoder");
  const cls = yield* I.getAttr(decoder, "JSONDecodeError");
  throw new DiasporeError(yield* I.invoke(cls, [message, doc, BigInt(pos)], new Map()));
}

function stopScan(index: bigint): never {
  const error = makeExc("StopIteration", index);
  error.d.set("value", index);
  throw new DiasporeError(error);
}

class JSONScanner {
  readonly text: string[];
  index: number;
  readonly I: Interp;
  readonly doc: PyString;
  readonly strict: boolean;
  readonly hooks: Map<string, Value>;
  readonly memo = new DiasporeDict();
  constructor(I: Interp, doc: PyString, index: number, strict: boolean, hooks: Map<string, Value>) {
    this.I = I;
    this.doc = doc;
    this.strict = strict;
    this.hooks = hooks;
    this.text = chars(doc);
    this.index = index;
  }

  *fail(message: string, pos = this.index): G<never> {
    return yield* decodeError(this.I, this.doc, message, pos);
  }

  skipSpace(): void {
    while (this.text[this.index] === " " || this.text[this.index] === "\t" ||
      this.text[this.index] === "\r" || this.text[this.index] === "\n") this.index++;
  }

  *unicodeEscape(): G<number> {
    const start = this.index;
    if (start + 4 >= this.text.length) return yield* this.fail("Invalid \\uXXXX escape", start - 1);
    let value = 0;
    for (let i = start; i < start + 4; i++) {
      const ch = this.text[i];
      const c = ch.charCodeAt(0);
      const digit = c >= 48 && c <= 57 ? c - 48 : c >= 65 && c <= 70 ? c - 55 :
        c >= 97 && c <= 102 ? c - 87 : -1;
      if (digit < 0) return yield* this.fail("Invalid \\uXXXX escape", start - 1);
      value = value * 16 + digit;
    }
    this.index += 4;
    return value;
  }

  *string(): G<PyString> {
    const begin = this.index - 1;
    const out: string[] = [];
    while (this.index < this.text.length) {
      this.I.fuel();
      const ch = this.text[this.index++];
      if (ch === '"') return strFromChars(out);
      if (ch !== "\\") {
        if (this.strict && ch.charCodeAt(0) <= 0x1f) return yield* this.fail("Invalid control character at", this.index - 1);
        out.push(ch);
        continue;
      }
      if (this.index === this.text.length) return yield* this.fail("Unterminated string starting at", begin);
      const escaped = this.text[this.index++];
      if (escaped === "u") {
        let cp = yield* this.unicodeEscape();
        if (cp >= 0xd800 && cp <= 0xdbff && this.index + 6 < this.text.length &&
          this.text[this.index] === "\\" && this.text[this.index + 1] === "u") {
          this.index += 2;
          const low = yield* this.unicodeEscape();
          if (low >= 0xdc00 && low <= 0xdfff) cp = 0x10000 + ((cp - 0xd800) << 10) + low - 0xdc00;
          else this.index -= 6;
        }
        out.push(String.fromCodePoint(cp));
      } else {
        switch (escaped) {
          case '"': case "\\": case "/": out.push(escaped); break;
          case "b": out.push("\b"); break;
          case "f": out.push("\f"); break;
          case "n": out.push("\n"); break;
          case "r": out.push("\r"); break;
          case "t": out.push("\t"); break;
          default: return yield* this.fail("Invalid \\escape", this.index - 2);
        }
      }
    }
    return yield* this.fail("Unterminated string starting at", begin);
  }

  matches(word: string): boolean {
    for (let j = 0; j < word.length; j++) if (this.text[this.index + j] !== word[j]) return false;
    this.index += word.length;
    return true;
  }

  *value(): G<Value> {
    this.I.fuel();
    const ch = this.text[this.index];
    if (ch === '"') {
      this.index++;
      return yield* this.string();
    }
    if (ch === "[" || ch === "{") {
      if (this.I.depth >= this.I.maxDepth) {
        throw diasporeErr("RecursionError", `maximum recursion depth exceeded while decoding a JSON ${ch === "[" ? "array" : "object"} from a unicode string`);
      }
      this.I.depth++;
      try {
        return ch === "[" ? yield* this.array() : yield* this.object();
      } finally {
        this.I.depth--;
      }
    }
    if (ch === "n" && this.matches("null")) return NONE;
    if (ch === "t" && this.matches("true")) return true;
    if (ch === "f" && this.matches("false")) return false;
    for (const constant of ["NaN", "Infinity", "-Infinity"]) {
      if (ch === constant[0] && this.matches(constant)) {
        return yield* this.I.invoke(this.hooks.get("parse_constant")!, [constant], new Map());
      }
    }
    const start = this.index;
    if (this.text[this.index] === "-") this.index++;
    const digit = (c: string | undefined): boolean => c !== undefined && c >= "0" && c <= "9";
    if (this.text[this.index] === "0") this.index++;
    else if (digit(this.text[this.index]) && this.text[this.index] !== "0") {
      do { this.index++; } while (digit(this.text[this.index]));
    } else stopScan(BigInt(start));
    let float = false;
    if (this.text[this.index] === "." && digit(this.text[this.index + 1])) {
      float = true;
      this.index += 2;
      while (digit(this.text[this.index])) this.index++;
    }
    if (this.text[this.index] === "e" || this.text[this.index] === "E") {
      let end = this.index + 1;
      if (this.text[end] === "+" || this.text[end] === "-") end++;
      if (digit(this.text[end])) {
        float = true;
        do { end++; } while (digit(this.text[end]));
        this.index = end;
      }
    }
    const number = this.text.slice(start, this.index).join("");
    return yield* this.I.invoke(this.hooks.get(float ? "parse_float" : "parse_int")!, [number], new Map());
  }

  *array(): G<Value> {
    this.index++;
    const result = new DiasporeList();
    this.skipSpace();
    if (this.text[this.index] === "]") {
      this.index++;
      return result;
    }
    for (;;) {
      result.items.push(yield* this.value());
      this.skipSpace();
      if (this.text[this.index] === "]") {
        this.index++;
        return result;
      }
      if (this.text[this.index] !== ",") return yield* this.fail("Expecting ',' delimiter");
      this.index++;
      this.skipSpace();
    }
  }

  *object(): G<Value> {
    this.index++;
    const pairsHook = this.hooks.get("object_pairs_hook")!;
    const objectHook = this.hooks.get("object_hook")!;
    const result = pairsHook instanceof PyNoneT ? new DiasporeDict() : new DiasporeList();
    this.skipSpace();
    if (this.text[this.index] !== "}") {
      for (;;) {
        if (this.text[this.index] !== '"') return yield* this.fail("Expecting property name enclosed in double quotes");
        this.index++;
        let key = yield* this.string();
        const previous = this.memo.get(key);
        if (previous !== undefined) key = previous as PyString;
        else this.memo.set(key, key);
        this.skipSpace();
        if (this.text[this.index] !== ":") return yield* this.fail("Expecting ':' delimiter");
        this.index++;
        this.skipSpace();
        const value = yield* this.value();
        if (result instanceof DiasporeDict) result.set(key, value);
        else result.items.push(new DiasporeTuple([key, value]));
        this.skipSpace();
        if (this.text[this.index] === "}") break;
        if (this.text[this.index] !== ",") return yield* this.fail("Expecting ',' delimiter");
        this.index++;
        this.skipSpace();
      }
    }
    this.index++;
    if (!(pairsHook instanceof PyNoneT)) return yield* this.I.invoke(pairsHook, [result], new Map());
    if (!(objectHook instanceof PyNoneT)) return yield* this.I.invoke(objectHook, [result], new Map());
    return result;
  }
}

class JSONEncoder {
  readonly I: Interp;
  readonly markers: DiasporeDict | null;
  readonly defaultFn: Value;
  readonly encoder: Value;
  readonly keySeparator: PyString;
  readonly itemSeparator: PyString;
  readonly sortKeys: boolean;
  readonly skipKeys: boolean;
  readonly allowNaN: boolean;
  constructor(
    I: Interp, markers: DiasporeDict | null, defaultFn: Value,
    encoder: Value, keySeparator: PyString, itemSeparator: PyString,
    sortKeys: boolean, skipKeys: boolean, allowNaN: boolean,
  ) {
    this.I = I;
    this.markers = markers;
    this.defaultFn = defaultFn;
    this.encoder = encoder;
    this.keySeparator = keySeparator;
    this.itemSeparator = itemSeparator;
    this.sortKeys = sortKeys;
    this.skipKeys = skipKeys;
    this.allowNaN = allowNaN;
  }

  *string(value: Value): G<PyString> {
    const encoded = yield* this.I.invoke(this.encoder, [value], new Map());
    const raw = primitive(encoded);
    if (!isPyString(raw)) throw diasporeErr("TypeError", `encoder() must return a string, not ${typeName(encoded)}`);
    return raw;
  }

  *float(value: Value, raw: number): G<string> {
    if (Number.isFinite(raw)) return pyFloatStr(raw);
    if (!this.allowNaN) throw diasporeErr("ValueError", "Out of range float values are not JSON compliant: " + (yield* this.I.repr(value)));
    return Number.isNaN(raw) ? "NaN" : raw > 0 ? "Infinity" : "-Infinity";
  }

  *write(value: Value, out: PyString[]): G<void> {
    this.I.fuel();
    const raw = primitive(value);
    if (raw instanceof PyNoneT) { out.push("null"); return; }
    if (typeof raw === "boolean") { out.push(raw ? "true" : "false"); return; }
    if (typeof raw === "bigint") { out.push(raw.toString()); return; }
    if (typeof raw === "number") { out.push(yield* this.float(value, raw)); return; }
    if (isPyString(raw)) { out.push(yield* this.string(value)); return; }
    if (this.I.depth >= this.I.maxDepth) throw diasporeErr("RecursionError", "maximum recursion depth exceeded while encoding a JSON object");
    this.I.depth++;
    try {
      let sequence: Value[] | null = null;
      if (raw instanceof DiasporeList || raw instanceof DiasporeTuple) {
        sequence = value instanceof DiasporeInstance ? yield* iterToArray(this.I, value) : raw.items;
        if (sequence.length === 0) { out.push("[]"); return; }
      } else if (raw instanceof DiasporeDict && raw.size === 0) { out.push("{}"); return; }
      let ident: Value | null = null;
      if (this.markers !== null) {
        ident = yield* this.I.invoke(this.I.builtins.get("id")!, [value], new Map());
        if (this.markers.has(ident)) throw diasporeErr("ValueError", "Circular reference detected");
        this.markers.set(ident, value);
      }
      if (sequence !== null) {
        out.push("[");
        for (let i = 0; i < sequence.length; i++) {
          if (i) out.push(this.itemSeparator);
          yield* this.write(sequence[i], out);
        }
        out.push("]");
      } else if (raw instanceof DiasporeDict) {
        out.push("{");
        let first = true;
        const emit = function* (self: JSONEncoder, key: Value, entry: Value): G<void> {
          const k = primitive(key);
          let text: Value;
          if (isPyString(k)) text = key;
          else if (typeof k === "number") text = yield* self.float(key, k);
          else if (typeof k === "bigint") text = k.toString();
          else if (typeof k === "boolean") text = k ? "true" : "false";
          else if (k instanceof PyNoneT) text = "null";
          else if (self.skipKeys) return;
          else throw diasporeErr("TypeError", `keys must be str, int, float, bool or None, not ${typeName(key)}`);
          if (!first) out.push(self.itemSeparator);
          first = false;
          out.push(yield* self.string(text), self.keySeparator);
          yield* self.write(entry, out);
        };
        if (this.sortKeys || value instanceof DiasporeInstance) {
          const items = value instanceof DiasporeInstance
            ? yield* iterToArray(this.I, yield* this.I.invoke(yield* this.I.getAttr(value, "items"), [], new Map()))
            : raw.entries().map(([key, entry]) => new DiasporeTuple([key, entry]));
          if (this.sortKeys) yield* sortValues(this.I, items, null, false);
          for (const pair of items) {
            const p = primitive(pair);
            if (!(p instanceof DiasporeTuple) || p.items.length !== 2) throw diasporeErr("ValueError", "items must return 2-tuples");
            yield* emit(this, p.items[0], p.items[1]);
          }
        } else {
          for (const { key, val } of raw.map.values()) yield* emit(this, key, val);
        }
        out.push("}");
      } else {
        const replacement = yield* this.I.invoke(this.defaultFn, [value], new Map());
        yield* this.write(replacement, out);
      }
      // CPython retains markers when a callback or nested encoding raises.
      if (ident !== null && this.markers !== null && this.markers.delete(ident) === undefined) {
        throw new DiasporeError(makeExc("KeyError", ident));
      }
    } finally {
      this.I.depth--;
    }
  }
}

/** Construct CPython's private JSON accelerator; public json remains the bundled package. */
export function makeJSONModule(I: Interp): NativeModule {
  const ns = new Map<string, Value>();
  for (const [name, ascii] of [["encode_basestring", false], ["encode_basestring_ascii", true]] as const) {
    ns.set(name, bi(name, function* (_I, args, kw): G<Value> {
      noKw(name, kw);
      argCount(name, args, 1, 1);
      const s = jsonString(args[0]);
      I.fuelN(strLen(s));
      return encodeString(s, ascii);
    }));
  }
  ns.set("scanstring", bi("scanstring", function* (_I, args, kw): G<Value> {
    noKw("scanstring", kw);
    argCount("scanstring", args, 2, 3);
    const end = yield* jsonIndex(I, args[1]);
    const strict = args.length === 3 ? yield* I.truth(args[2]) : true;
    const doc = jsonString(args[0]);
    if (end < 0n || end > BigInt(strLen(doc))) throw diasporeErr("ValueError", "end is out of bounds");
    const parser = new JSONScanner(I, doc, Number(end), strict, new Map());
    const value = yield* parser.string();
    return new DiasporeTuple([value, BigInt(parser.index)]);
  }));
  ns.set("make_scanner", new BuiltinType("Scanner", function* (_I, args, kw): G<Value> {
    const [context] = bindJSONArgs("make_scanner", args, kw, ["context"]);
    const props = new Map<string, Value>();
    props.set("strict", yield* I.truth(yield* I.getAttr(context, "strict")));
    for (const name of ["object_hook", "object_pairs_hook", "parse_float", "parse_int", "parse_constant"]) {
      props.set(name, yield* I.getAttr(context, name));
    }
    const call = bi("scan_once", function* (_I, a, k): G<Value> {
      const [value, index] = bindJSONArgs("scan_once", a, k, ["string", "idx"]);
      const idx = yield* jsonIndex(I, index);
      const doc = jsonString(value);
      if (idx < 0n) throw diasporeErr("ValueError", "idx cannot be negative");
      if (idx >= BigInt(strLen(doc))) stopScan(idx);
      const parser = new JSONScanner(I, doc, Number(idx), props.get("strict") === true, props);
      const result = yield* parser.value();
      return new DiasporeTuple([result, BigInt(parser.index)]);
    });
    return new NativeObject("Scanner", new Map([["__call__", call]]), props);
  }));
  ns.set("make_encoder", new BuiltinType("Encoder", function* (_I, args, kw): G<Value> {
    const names = ["markers", "default", "encoder", "indent", "key_separator", "item_separator", "sort_keys", "skipkeys", "allow_nan"];
    const a = bindJSONArgs("make_encoder", args, kw, names);
    for (const i of [4, 5]) {
      if (!isPyString(primitive(a[i]))) throw diasporeErr("TypeError", `make_encoder() argument ${i + 1} must be str, not ${typeName(a[i])}`);
    }
    const sort = yield* I.truth(a[6]);
    const skip = yield* I.truth(a[7]);
    const nan = yield* I.truth(a[8]);
    const markers = primitive(a[0]);
    if (!(markers instanceof PyNoneT || markers instanceof DiasporeDict)) {
      throw diasporeErr("TypeError", `make_encoder() argument 1 must be dict or None, not ${typeName(a[0])}`);
    }
    const encoder = new JSONEncoder(I, markers instanceof DiasporeDict ? markers : null,
      a[1], a[2], primitive(a[4]) as PyString, primitive(a[5]) as PyString, sort, skip, nan);
    const props = new Map<string, Value>(names.slice(0, 8).map((name, i) => [name, i === 6 ? sort : i === 7 ? skip : a[i]]));
    const call = bi("_iterencode", function* (_I, values, keywords): G<Value> {
      const [obj, level] = bindJSONArgs("_iterencode", values, keywords, ["obj", "_current_indent_level"]);
      yield* jsonIndex(I, level);
      const chunks: PyString[] = [];
      yield* encoder.write(obj, chunks);
      return new DiasporeTuple([strJoin(chunks)]);
    });
    return new NativeObject("Encoder", new Map([["__call__", call]]), props);
  }));
  I.types.set("Scanner", ns.get("make_scanner") as BuiltinType);
  I.types.set("Encoder", ns.get("make_encoder") as BuiltinType);
  return new NativeModule("_json", ns);
}
