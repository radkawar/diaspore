/* The re module (spec 11.8): a validated portable subset of Python regular
 * expressions implemented over JS RegExp. The validator rejects everything
 * outside the subset; the translator bridges the remaining JS/Python gaps
 * ('.' vs '\r', bare inline flags, ']' as the first class member). */

import {
  type Value,
  type G,
  type KwArgs,
  NONE,
  DiasporeList,
  DiasporeTuple,
  NativeModule,
  NativeObject,
  Builtin,
  typeName,
  asIndex,
  hasAstral,
  diasporeErr,
} from "./values.ts";
import { needArgs } from "./helpers.ts";
import { bi } from "./builtins.ts";
import type { Interp } from "./interp.ts";

const RE_IGNORECASE = 2n;
const RE_MULTILINE = 8n;
const RE_DOTALL = 16n;

/** Mirrors diaspore-py's _re_validate byte for byte. */
function reValidate(pattern: string): void {
  let i = 0;
  const n = pattern.length;
  while (i < n) {
    const ch = pattern[i];
    if (ch === "\\") {
      const next = i + 1 < n ? pattern[i + 1] : "";
      if (next >= "1" && next <= "9") {
        throw diasporeErr("ValueError", "diaspore re: backreferences are not supported");
      }
      if (next === "A" || next === "Z") {
        throw diasporeErr("ValueError", "diaspore re: \\A/\\Z are not supported (use ^ and $)");
      }
      i += 2;
      continue;
    }
    if (pattern.startsWith("(?", i)) {
      let j = i + 2;
      let flags = 0;
      while (j < n && (pattern[j] === "i" || pattern[j] === "m" || pattern[j] === "s")) {
        flags++;
        j++;
      }
      const ok = (j < n && pattern[j] === ":") || (flags > 0 && j < n && pattern[j] === ")");
      if (!ok) {
        throw diasporeErr("ValueError", "diaspore re: only (?:...) groups and (?ims) flags are supported");
      }
      i = j + 1;
      continue;
    }
    if ((ch === "*" || ch === "+" || ch === "?") && i + 1 < n && pattern[i + 1] === "+") {
      throw diasporeErr("ValueError", "diaspore re: possessive quantifiers are not supported");
    }
    i++;
  }
}

/** Translates a validated diaspore pattern to JS RegExp source:
 * - strips bare inline-flag groups `(?ims)` (collected into global flags),
 * - rewrites `.` to [^\n] (or [\s\S] under DOTALL) for Python semantics,
 * - escapes a leading `]` inside a character class (Python treats it as a
 *   literal; JS treats `[]` as an empty class). */
function translate(pattern: string, dotAll: boolean): { src: string; extra: string } {
  let out = "";
  let extra = "";
  let i = 0;
  const n = pattern.length;
  while (i < n) {
    const ch = pattern[i];
    if (ch === "\\") {
      out += pattern.slice(i, i + 2);
      i += 2;
      continue;
    }
    if (ch === "[") {
      // character class: copy verbatim, escaping a leading ']'
      out += "[";
      i++;
      if (i < n && pattern[i] === "^") {
        out += "^";
        i++;
      }
      if (i < n && pattern[i] === "]") {
        out += "\\]";
        i++;
      }
      while (i < n && pattern[i] !== "]") {
        if (pattern[i] === "\\") {
          out += pattern.slice(i, i + 2);
          i += 2;
        } else {
          out += pattern[i];
          i++;
        }
      }
      if (i < n) {
        out += "]";
        i++;
      }
      continue;
    }
    if (ch === "(" && pattern.startsWith("(?", i)) {
      let j = i + 2;
      let flags = "";
      while (j < n && (pattern[j] === "i" || pattern[j] === "m" || pattern[j] === "s")) {
        flags += pattern[j];
        j++;
      }
      if (flags.length > 0 && j < n && pattern[j] === ")") {
        // bare inline flags: apply globally
        for (const f of flags) if (!extra.includes(f)) extra += f;
        i = j + 1;
        continue;
      }
      out += pattern.slice(i, j + 1);
      i = j + 1;
      continue;
    }
    if (ch === ".") {
      out += dotAll ? "[\\s\\S]" : "[^\\n]";
      i++;
      continue;
    }
    out += ch;
    i++;
  }
  return { src: out, extra };
}

interface CompiledRe {
  search: RegExp; // global, hasIndices
  match: RegExp; // sticky
  fullmatch: RegExp; // sticky, end-anchored
  nGroups: number;
}

const cache = new Map<string, CompiledRe>();

function getCompiled(pattern: Value, flags: bigint): CompiledRe {
  if (typeof pattern !== "string") {
    throw diasporeErr("TypeError", "re pattern must be a string");
  }
  const key = `${flags}:${pattern}`;
  const hit = cache.get(key);
  if (hit !== undefined) return hit;
  reValidate(pattern);
  let dotAll = (flags & RE_DOTALL) !== 0n;
  let jsFlags = "d";
  if ((flags & RE_IGNORECASE) !== 0n) jsFlags += "i";
  if ((flags & RE_MULTILINE) !== 0n) jsFlags += "m";
  // pre-scan inline flags so '.' translation sees DOTALL from (?s)
  const pre = translate(pattern, false);
  if (pre.extra.includes("s")) dotAll = true;
  const { src, extra } = translate(pattern, dotAll);
  for (const f of extra) {
    if (f === "i" && !jsFlags.includes("i")) jsFlags += "i";
    if (f === "m" && !jsFlags.includes("m")) jsFlags += "m";
  }
  const compile = (source: string, fl: string): RegExp => {
    try {
      return new RegExp(source, fl);
    } catch (e) {
      let msg = e instanceof Error ? e.message : String(e);
      const idx = msg.lastIndexOf(": ");
      if (idx >= 0) msg = msg.slice(idx + 2);
      throw diasporeErr("ValueError", `diaspore re: invalid pattern: ${msg}`);
    }
  };
  const c: CompiledRe = {
    search: compile(src, jsFlags + "g"),
    match: compile(`(?:${src})`, jsFlags + "y"),
    fullmatch: compile(`(?:${src})(?![\\s\\S])`, jsFlags + "y"),
    nGroups: 0,
  };
  // count capturing groups via a probe match against the empty-ish regex
  c.nGroups = countGroups(src);
  cache.set(key, c);
  return c;
}

function countGroups(src: string): number {
  let count = 0;
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === "\\") {
      i += 2;
      continue;
    }
    if (ch === "[") {
      i++;
      if (i < src.length && src[i] === "^") i++;
      if (i < src.length && src[i] === "]") i++;
      while (i < src.length && src[i] !== "]") {
        if (src[i] === "\\") i += 2;
        else i++;
      }
      i++;
      continue;
    }
    if (ch === "(" && !src.startsWith("(?", i)) count++;
    i++;
  }
  return count;
}

/** Loc vector like Go's FindStringSubmatchIndex: [s0,e0,s1,e1,...] with -1
 * for unmatched groups. Offsets are UTF-16 code units into the subject. */
type Loc = number[];

function locOf(m: RegExpExecArray): Loc {
  const indices = (m as RegExpExecArray & { indices?: ([number, number] | undefined)[] })
    .indices as ([number, number] | undefined)[];
  const out: number[] = [];
  for (let g = 0; g < m.length; g++) {
    const pair = indices[g];
    if (pair === undefined) {
      out.push(-1, -1);
    } else {
      out.push(pair[0], pair[1]);
    }
  }
  return out;
}

/** Advance lastIndex by one code point (for empty matches). */
function bumpLastIndex(re: RegExp, s: string): void {
  const i = re.lastIndex;
  const c = s.charCodeAt(i);
  if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
    const c2 = s.charCodeAt(i + 1);
    if (c2 >= 0xdc00 && c2 <= 0xdfff) {
      re.lastIndex = i + 2;
      return;
    }
  }
  re.lastIndex = i + 1;
}

/** All matches with CPython 3.7+ semantics (an empty match adjacent to a
 * non-empty match is kept; the scan advances one code point after an empty
 * match). The JS exec loop implements exactly this. */
function findAllPy(c: CompiledRe, s: string): Loc[] {
  const re = c.search;
  re.lastIndex = 0;
  const out: Loc[] = [];
  for (;;) {
    if (re.lastIndex > s.length) break;
    const m = re.exec(s);
    if (m === null) break;
    out.push(locOf(m));
    if (m[0].length === 0) {
      bumpLastIndex(re, s);
    }
  }
  return out;
}

/** UTF-16 offset -> code-point offset (CPython match positions). */
function cpIndex(s: string, off: number): bigint {
  if (!hasAstral(s)) return BigInt(off);
  let cp = 0;
  for (let i = 0; i < off; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < off) i++;
    cp++;
  }
  return BigInt(cp);
}

function wrapMatch(s: string, loc: Loc): NativeObject {
  const nGroups = loc.length / 2 - 1;
  const groupVal = (g: number): Value => {
    if (loc[2 * g] < 0) return NONE;
    return s.slice(loc[2 * g], loc[2 * g + 1]);
  };
  const groupArg = (name: string, args: Value[]): number => {
    let n = 0n;
    if (args.length > 1) {
      throw diasporeErr("TypeError", `${name} expected at most 1 arguments, got ${args.length}`);
    }
    if (args.length === 1) {
      const v = asIndex(args[0]);
      if (v === null) throw diasporeErr("IndexError", "no such group");
      n = v;
    }
    if (n < 0n || n > BigInt(nGroups)) throw diasporeErr("IndexError", "no such group");
    return Number(n);
  };
  const spanOf = (g: number): [Value, Value] => {
    if (loc[2 * g] < 0) return [-1n, -1n];
    return [cpIndex(s, loc[2 * g]), cpIndex(s, loc[2 * g + 1])];
  };
  const methods = new Map<string, Builtin>();
  methods.set(
    "group",
    bi("group", function* (_I, args): G<Value> {
      return groupVal(groupArg("group", args));
    }),
  );
  methods.set(
    "groups",
    bi("groups", function* (): G<Value> {
      const items: Value[] = [];
      for (let g = 1; g <= nGroups; g++) items.push(groupVal(g));
      return new DiasporeTuple(items);
    }),
  );
  methods.set(
    "start",
    bi("start", function* (_I, args): G<Value> {
      return spanOf(groupArg("start", args))[0];
    }),
  );
  methods.set(
    "end",
    bi("end", function* (_I, args): G<Value> {
      return spanOf(groupArg("end", args))[1];
    }),
  );
  methods.set(
    "span",
    bi("span", function* (_I, args): G<Value> {
      const [a, b] = spanOf(groupArg("span", args));
      return new DiasporeTuple([a, b]);
    }),
  );
  return new NativeObject("re.Match", methods);
}

// CPython's re.escape special-character set.
const ESCAPE_SET = new Set("()[]{}?*+-|^$\\.&~# \t\n\r\v\f");

function reEscape(s: string): string {
  let b = "";
  for (const r of s) {
    if (ESCAPE_SET.has(r)) b += "\\";
    b += r;
  }
  return b;
}

/** Applies a sub() replacement template to one match. */
function expandTemplate(b: string[], repl: string, s: string, loc: Loc): void {
  const nGroups = loc.length / 2 - 1;
  for (let i = 0; i < repl.length; i++) {
    const c = repl[i];
    if (c !== "\\") {
      b.push(c);
      continue;
    }
    i++;
    if (i >= repl.length) {
      throw diasporeErr("ValueError", "diaspore re: bad escape (end of pattern)");
    }
    const e = repl[i];
    if (e >= "1" && e <= "9") {
      const g = e.charCodeAt(0) - 48;
      if (g > nGroups) throw diasporeErr("ValueError", `invalid group reference ${g}`);
      if (loc[2 * g] >= 0) b.push(s.slice(loc[2 * g], loc[2 * g + 1]));
    } else if (e === "\\") {
      b.push("\\");
    } else if (e === "n") {
      b.push("\n");
    } else if (e === "t") {
      b.push("\t");
    } else if (e === "r") {
      b.push("\r");
    } else if (e === "f") {
      b.push("\f");
    } else if (e === "v") {
      b.push("\v");
    } else if (e === "a") {
      b.push("\x07");
    } else if (e === "b") {
      b.push("\b");
    } else if (e === "0") {
      b.push("\x00");
    } else {
      throw diasporeErr("ValueError", `diaspore re: bad escape \\${e}`);
    }
  }
}

// --------------------------------------------------------------------------

/** Construct the portable regular-expression module. */
export function makeReModule(_I: Interp): NativeModule {
  const intArg = (what: string, v: Value): bigint => {
    const n = asIndex(v);
    if (n === null) throw diasporeErr("TypeError", `${what} must be an integer`);
    return n;
  };

  // (pattern, string, flags=0) shared by search/match/fullmatch/findall
  const parse = (name: string, args: Value[], kw: KwArgs): [CompiledRe, string] => {
    needArgs(name, args, 2, 3);
    let flags = 0n;
    if (args.length === 3) flags = intArg("flags", args[2]);
    const kv = kw.get("flags");
    if (kv !== undefined) flags = intArg("flags", kv);
    const c = getCompiled(args[0], flags);
    if (typeof args[1] !== "string") {
      throw diasporeErr(
        "TypeError",
        `expected string or bytes-like object, got '${typeName(args[1])}'`,
      );
    }
    return [c, args[1]];
  };

  const matcher = (name: string, pick: (c: CompiledRe) => RegExp) =>
    bi(name, function* (_I2, args, kw): G<Value> {
      const [c, s] = parse(name, args, kw);
      const re = pick(c);
      re.lastIndex = 0;
      const m = re.exec(s);
      if (m === null) return NONE;
      return wrapMatch(s, locOf(m));
    });

  const findall = bi("findall", function* (_I2, args, kw): G<Value> {
    const [c, s] = parse("findall", args, kw);
    const nGroups = c.nGroups;
    const out = new DiasporeList();
    for (const loc of findAllPy(c, s)) {
      if (nGroups === 0) {
        out.items.push(s.slice(loc[0], loc[1]));
      } else if (nGroups === 1) {
        out.items.push(groupOrEmpty(s, loc, 1));
      } else {
        const row: Value[] = [];
        for (let g = 1; g <= nGroups; g++) row.push(groupOrEmpty(s, loc, g));
        out.items.push(new DiasporeTuple(row));
      }
    }
    return out;
  });

  const split = bi("split", function* (_I2, args, kw): G<Value> {
    needArgs("split", args, 2, 4);
    let maxsplit = 0n;
    let flags = 0n;
    if (args.length >= 3) maxsplit = intArg("maxsplit", args[2]);
    const mkv = kw.get("maxsplit");
    if (mkv !== undefined) maxsplit = intArg("maxsplit", mkv);
    if (args.length === 4) flags = intArg("flags", args[3]);
    const fkv = kw.get("flags");
    if (fkv !== undefined) flags = intArg("flags", fkv);
    const c = getCompiled(args[0], flags);
    if (typeof args[1] !== "string") {
      throw diasporeErr(
        "TypeError",
        `expected string or bytes-like object, got '${typeName(args[1])}'`,
      );
    }
    const s = args[1];
    const out = new DiasporeList();
    let last = 0;
    let count = 0n;
    for (const loc of findAllPy(c, s)) {
      if (maxsplit > 0n && count >= maxsplit) break;
      out.items.push(s.slice(last, loc[0]));
      for (let g = 1; g <= c.nGroups; g++) {
        if (loc[2 * g] < 0) out.items.push(NONE);
        else out.items.push(s.slice(loc[2 * g], loc[2 * g + 1]));
      }
      last = loc[1];
      count++;
    }
    out.items.push(s.slice(last));
    return out;
  });

  const sub = bi("sub", function* (_I2, args, kw): G<Value> {
    needArgs("sub", args, 3, 5);
    const repl = args[1];
    if (typeof repl !== "string") {
      throw diasporeErr("TypeError", "diaspore re: repl must be a string");
    }
    if (repl.includes("\\g")) {
      throw diasporeErr("ValueError", "diaspore re: \\g<...> replacements are not supported");
    }
    let count = 0n;
    if (args.length >= 4) {
      const n = asIndex(args[3]);
      if (n === null) throw diasporeErr("TypeError", "count must be an integer");
      count = n;
    }
    const ckv = kw.get("count");
    if (ckv !== undefined) {
      const n = asIndex(ckv);
      if (n === null) throw diasporeErr("TypeError", "count must be an integer");
      count = n;
    }
    let flags = 0n;
    if (args.length === 5) {
      const f = asIndex(args[4]);
      if (f === null) throw diasporeErr("TypeError", "flags must be an integer");
      flags = f;
    }
    const fkv = kw.get("flags");
    if (fkv !== undefined) {
      const f = asIndex(fkv);
      if (f === null) throw diasporeErr("TypeError", "flags must be an integer");
      flags = f;
    }
    const c = getCompiled(args[0], flags);
    if (typeof args[2] !== "string") {
      throw diasporeErr(
        "TypeError",
        `expected string or bytes-like object, got '${typeName(args[2])}'`,
      );
    }
    const s = args[2];
    const b: string[] = [];
    let last = 0;
    let done = 0n;
    for (const loc of findAllPy(c, s)) {
      if (count > 0n && done >= count) break;
      b.push(s.slice(last, loc[0]));
      expandTemplate(b, repl, s, loc);
      last = loc[1];
      done++;
    }
    b.push(s.slice(last));
    return b.join("");
  });

  const escape = bi("escape", function* (_I2, args): G<Value> {
    needArgs("escape", args, 1, 1);
    if (typeof args[0] !== "string") {
      throw diasporeErr("TypeError", `expected string, got '${typeName(args[0])}'`);
    }
    return reEscape(args[0]);
  });

  return new NativeModule(
    "re",
    new Map<string, Value>([
      ["IGNORECASE", RE_IGNORECASE],
      ["MULTILINE", RE_MULTILINE],
      ["DOTALL", RE_DOTALL],
      ["search", matcher("search", (c) => c.search)],
      ["match", matcher("match", (c) => c.match)],
      ["fullmatch", matcher("fullmatch", (c) => c.fullmatch)],
      ["findall", findall],
      ["split", split],
      ["sub", sub],
      ["escape", escape],
    ]),
  );
}

function groupOrEmpty(s: string, loc: Loc, g: number): Value {
  if (loc[2 * g] < 0) return "";
  return s.slice(loc[2 * g], loc[2 * g + 1]);
}
