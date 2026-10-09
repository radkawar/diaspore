/* json.dumps / json.loads with CPython byte parity (spec 11.3). */

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
  if (typeof v === "string") {
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
      if (typeof key !== "string") {
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
export function jsonEscapeString(s: string): string {
  let b = '"';
  for (const ch of s) {
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
        } else if (r < 0x80) {
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
  s: string;
  i = 0;
  constructor(s: string) {
    this.s = s;
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
    if (this.s.startsWith(word, this.i)) {
      this.i += word.length;
      return true;
    }
    return false;
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
    const text = this.s.slice(start, this.i);
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

  string(): string {
    this.i++; // opening quote
    let b = "";
    for (;;) {
      if (this.i >= this.s.length) this.fail("Unterminated string starting at");
      const c = this.s[this.i];
      if (c === '"') {
        this.i++;
        return b;
      }
      if (c === "\\") {
        this.i++;
        if (this.i >= this.s.length) this.fail("Unterminated string starting at");
        const e = this.s[this.i];
        switch (e) {
          case '"':
            b += '"';
            break;
          case "\\":
            b += "\\";
            break;
          case "/":
            b += "/";
            break;
          case "b":
            b += "\b";
            break;
          case "f":
            b += "\f";
            break;
          case "n":
            b += "\n";
            break;
          case "r":
            b += "\r";
            break;
          case "t":
            b += "\t";
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
            b += String.fromCodePoint(r);
            break;
          }
          default:
            this.fail("Invalid \\escape");
        }
        this.i++;
        continue;
      }
      b += c;
      this.i++;
    }
  }

  /** Parses the 4 hex digits after \u; on entry this.i is at 'u'. */
  uEscape(): number {
    if (this.i + 4 >= this.s.length) this.fail("Invalid \\uXXXX escape");
    const hex = this.s.slice(this.i + 1, this.i + 5);
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
      out.setHK("s" + k, k, this.value());
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
export function jsonLoads(s: string): Value {
  const p = new JsonParser(s);
  p.skipWS();
  const v = p.value();
  p.skipWS();
  if (p.i !== p.s.length) p.fail("Extra data");
  return v;
}
