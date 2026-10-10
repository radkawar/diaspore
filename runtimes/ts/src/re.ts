/* Native regular expressions implement Python 3.12 capture/backtracking and
 * Unicode 15.0 character semantics, with code-point or byte offsets. The
 * explicit engine avoids JS capture resets and empty-match scan differences.
 * LOCALE, TEMPLATE and DEBUG flags are unsupported. Conditional groups, named
 * Unicode escapes and variable or backreference-dependent lookbehind raise
 * re.error. Syntax and replacement errors retain their source coordinates. */
import {
  type Value, type G, type KwArgs, NONE, DiasporeList, DiasporeTuple,
  type PyString, isPyString, chars, strFromChars, strJoin,
  DiasporeDict, DiasporeIterBase, NativeModule, NativeObject, BuiltinType,
  Builtin, BoundMethod, DiasporeFunction, DiasporeClass, DiasporeInstance,
  asIndex, bytesToBinStr, diasporeErr,
} from "./values.ts";
import { acquireBuffer, indexInteger } from "./buffers.ts";
import { bi } from "./builtins.ts";
import type { Interp } from "./interp.ts";
import { compileRegex, executeRegex, type RegexCode, type RegexResult } from "./re_engine.ts";
import { REGEX_ERROR, regexError } from "./re_error.ts";

function integer(value: Value): bigint {
  const n = asIndex(value); if (n === null) throw diasporeErr("TypeError", "integer argument expected"); return n;
}
function bind(name: string, args: Value[], kw: KwArgs, names: string[], required: number, defaults: Value[]): Value[] {
  if (args.length > names.length) throw diasporeErr("TypeError", `${name}() takes at most ${names.length} arguments`);
  const result = args.slice();
  for (const [key, value] of kw) {
    const i = names.indexOf(key);
    if (i < 0) throw diasporeErr("TypeError", `${name}() got an unexpected keyword argument '${key}'`);
    if (i < args.length) throw diasporeErr("TypeError", `${name}() got multiple values for '${key}'`);
    result[i] = value;
  }
  for (let i = 0; i < names.length; i++) if (result[i] === undefined) {
    if (i < required) throw diasporeErr("TypeError", `${name}() missing required argument '${names[i]}'`);
    result[i] = defaults[i - required];
  }
  return result;
}
function text(value: Value): { raw: PyString; bytes: boolean } {
  if (isPyString(value)) return { raw: value, bytes: false };
  const lease = acquireBuffer(value);
  if (lease === null) throw diasporeErr("TypeError", "expected string or bytes-like object");
  try {
    return { raw: bytesToBinStr(lease.data), bytes: true };
  } finally {
    lease.release();
  }
}
function encoded(raw: PyString, bytes: boolean): Value {
  return bytes ? Uint8Array.from(raw, c => c.charCodeAt(0)) : raw;
}
interface Subject {
  original: Value; chars: string[]; bytes: boolean; pos: number; end: number;
  slice: (start: number, end?: number) => Value;
}
function subject(pattern: Pattern, value: Value, pos: Value = 0n, end: Value = 9223372036854775807n): Subject {
  const s = text(value);
  if (s.bytes !== pattern.bytes) throw diasporeErr("TypeError", "cannot mix string and bytes patterns");
  const points = chars(s.raw);
  const clamp = (v: Value) => {
    const n = integer(v);
    return Number(n < 0n ? 0n : n > BigInt(points.length) ? BigInt(points.length) : n);
  };
  return { original: value, chars: points, bytes: s.bytes, pos: clamp(pos), end: clamp(end), slice: (a, b = points.length) => encoded(strFromChars(points.slice(a, b)), s.bytes) };
}
function* groupNumber(I: Interp, code: RegexCode, value: Value): G<number> {
  const raw = value instanceof DiasporeInstance && value.prim !== null ? value.prim : value;
  let n: number | bigint | undefined;
  if (isPyString(raw)) n = typeof raw === "string" ? code.names.get(raw) : undefined;
  else if (asIndex(raw) !== null || value instanceof DiasporeInstance && value.cls.lookup("__index__") !== undefined) {
    n = yield* indexInteger(I, value);
  }
  if (n === undefined || n < 0 || n > code.groups) throw diasporeErr("IndexError", "no such group");
  return Number(n);
}
function captured(s: Subject, result: RegexResult, g: number, fallback: Value = NONE): Value {
  const start = result.caps[g * 2]; return start < 0 ? fallback : s.slice(start, result.caps[g * 2 + 1]);
}
function* matches(I: Interp, pattern: Pattern, s: Subject, mode: string): Generator<RegexResult> {
  let at = s.pos, mustConsume = false;
  while (at <= s.end) {
    I.fuel(); let found: RegexResult | undefined;
    for (const candidate of executeRegex(pattern.code, s.chars, at, s.end, () => I.fuel())) {
      if (mustConsume && candidate.pos === at || mode === "fullmatch" && candidate.pos !== s.end) continue;
      const caps = candidate.caps.slice(); caps[0] = at; caps[1] = candidate.pos;
      found = { ...candidate, caps }; break;
    }
    if (found) {
      yield found;
      if (mode === "match" || mode === "fullmatch" || mode === "search") return;
      mustConsume = found.pos === at; at = found.pos;
    } else {
      if (mode === "match" || mode === "fullmatch") return;
      at++; mustConsume = false;
    }
  }
}

type Template = (PyString | number)[];
function template(pattern: Pattern, source: PyString): Template {
  const raw = chars(source), parts: Template = [];
  let literal: string[] = [];
  const flush = () => { if (literal.length) { parts.push(strFromChars(literal)); literal = []; } };
  let at = 0;
  const error: (message: string, position?: number) => never = (message, position = at) => { throw regexError(message, encoded(source, pattern.bytes), position); };
  const escape: Record<string, string> = { a: "\x07", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t", v: "\v", "\\": "\\" };
  for (let i = 0; i < raw.length; i++) {
    if (raw[i] !== "\\") { literal.push(raw[i]); continue; }
    at = i;
    const c = raw[++i]; if (c === undefined) error("bad escape (end of pattern)");
    let ref: string | undefined, referenceStart = at + 1;
    if (c === "g") {
      if (raw[++i] !== "<") error("missing <");
      referenceStart = i + 1;
      const end = raw.indexOf(">", i + 1); if (end < 0) error("missing >, unterminated name", referenceStart);
      const name = strFromChars(raw.slice(i + 1, end));
      if (typeof name !== "string") error("bad character in group name", referenceStart);
      ref = name; i = end;
      if (!ref) error("missing group name", referenceStart);
    } else if (/^[0-9]$/.test(c)) {
      let digits = c;
      if (c === "0" || /^[0-7]{3}$/.test(raw.slice(i, i + 3).join(""))) {
        while (digits.length < 3 && /^[0-7]$/.test(raw[i + 1] ?? "")) digits += raw[++i];
        const oct = parseInt(digits, 8); if (oct > 255) error("octal escape outside range");
        literal.push(String.fromCharCode(oct)); continue;
      }
      if (/^[0-9]$/.test(raw[i + 1] ?? "")) digits += raw[++i];
      ref = digits;
    } else if (escape[c] !== undefined) literal.push(escape[c]);
    else if (/^[a-zA-Z]$/.test(c)) error(`bad escape \\${c}`);
    else literal.push("\\", c);
    if (ref !== undefined) {
      const id = /^[0-9]+$/.test(ref) ? Number(ref) : pattern.code.names.get(ref);
      if (id === undefined) throw diasporeErr("IndexError", `unknown group name '${ref}'`);
      if (id > pattern.code.groups) error(`invalid group reference ${id}`, referenceStart);
      flush(); parts.push(id);
    }
  }
  flush(); return parts;
}
function expand(parts: Template, s: Subject, result: RegexResult): Value {
  const out: PyString[] = [];
  for (const part of parts) {
    if (isPyString(part)) out.push(part);
    else if (result.caps[2 * part] >= 0) out.push(strFromChars(s.chars.slice(result.caps[2 * part], result.caps[2 * part + 1])));
  }
  return encoded(strJoin(out), s.bytes);
}
function wrapMatch(pattern: Pattern, s: Subject, result: RegexResult): NativeObject {
  const object = new NativeObject("re.Match", new Map());
  object.methods.set("group", bi("group", function* (I, args, kw): G<Value> {
    if (kw.size) throw diasporeErr("TypeError", "group() takes no keyword arguments");
    const values: Value[] = [];
    for (const arg of args.length ? args : [0n]) values.push(captured(s, result, yield* groupNumber(I, pattern.code, arg)));
    return values.length === 1 ? values[0] : new DiasporeTuple(values);
  }));
  object.methods.set("__getitem__", bi("__getitem__", function* (I, args, kw): G<Value> {
    if (kw.size) throw diasporeErr("TypeError", "__getitem__() takes no keyword arguments");
    const [g] = bind("__getitem__", args, kw, ["group"], 1, []);
    return captured(s, result, yield* groupNumber(I, pattern.code, g));
  }));
  for (const method of ["groups", "groupdict"]) object.methods.set(method, bi(method, function* (_I, args, kw): G<Value> {
    const [fallback] = bind(method, args, kw, ["default"], 0, [NONE]);
    if (method === "groups") return new DiasporeTuple(Array.from({ length: pattern.code.groups }, (_, i) => captured(s, result, i + 1, fallback)));
    const out = new DiasporeDict(); for (const [name, id] of pattern.code.names) out.set(name, captured(s, result, id, fallback)); return out;
  }));
  for (const method of ["start", "end", "span"]) object.methods.set(method, bi(method, function* (I, args, kw): G<Value> {
    const [g] = bind(method, args, kw, ["group"], 0, [0n]); const id = yield* groupNumber(I, pattern.code, g);
    const a = BigInt(result.caps[id * 2]), b = BigInt(result.caps[id * 2 + 1]);
    return method === "start" ? a : method === "end" ? b : new DiasporeTuple([a, b]);
  }));
  object.methods.set("expand", bi("expand", function* (_I, args, kw): G<Value> {
    const [value] = bind("expand", args, kw, ["template"], 1, []); const repl = text(value);
    if (repl.bytes !== s.bytes) throw diasporeErr("TypeError", "cannot mix string and bytes replacements");
    return expand(template(pattern, repl.raw), s, result);
  }));
  let lastgroup: Value = NONE;
  for (const [name, id] of pattern.code.names) if (id === result.last) lastgroup = name;
  object.props = new Map<string, Value>([
    ["re", pattern], ["string", s.original], ["pos", BigInt(s.pos)], ["endpos", BigInt(s.end)],
    ["lastindex", result.last === null ? NONE : BigInt(result.last)], ["lastgroup", lastgroup],
    ["regs", new DiasporeTuple(Array.from({ length: pattern.code.groups + 1 }, (_, i) => new DiasporeTuple([BigInt(result.caps[i * 2]), BigInt(result.caps[i * 2 + 1])])))]
  ]);
  return object;
}
class MatchIterator extends DiasporeIterBase {
  kindName = "callable_iterator";
  private pattern: Pattern;
  private s: Subject;
  private iterator: Generator<RegexResult>;
  constructor(pattern: Pattern, s: Subject, iterator: Generator<RegexResult>) {
    super(); this.pattern = pattern; this.s = s; this.iterator = iterator;
  }
  *next(): G<[Value, boolean]> { const item = this.iterator.next(); return item.done ? [NONE, false] : [wrapMatch(this.pattern, this.s, item.value), true]; }
}
class Pattern extends NativeObject {
  code: RegexCode; bytes: boolean;
  constructor(value: Value, flags: bigint) {
    super("re.Pattern", new Map());
    if (!isPyString(value) && !(value instanceof Uint8Array)) throw diasporeErr("TypeError", "first argument must be string or compiled pattern");
    const p = text(value); this.bytes = p.bytes;
    if (flags < 0n || flags > 511n) throw diasporeErr("ValueError", "unsupported regular expression flags");
    this.code = compileRegex(p.raw, Number(flags), p.bytes, value);
    const index = new DiasporeDict(); for (const [name, id] of this.code.names) index.set(name, BigInt(id));
    this.props = new Map<string, Value>([["pattern", value], ["flags", BigInt(this.code.flags)], ["groups", BigInt(this.code.groups)], ["groupindex", index]]);
    for (const method of ["match", "search", "fullmatch", "findall", "finditer"]) this.methods.set(method, bi(method, function* (I, args, kw): G<Value> {
      const [value, pos, end] = bind(method, args, kw, ["string", "pos", "endpos"], 1, [0n, 9223372036854775807n]);
      return yield* run(I, thisPattern, method, subject(thisPattern, value, pos, end));
    }));
    const thisPattern = this;
    this.methods.set("split", bi("split", function* (I, args, kw): G<Value> {
      const [value, limit] = bind("split", args, kw, ["string", "maxsplit"], 1, [0n]);
      return yield* run(I, thisPattern, "split", subject(thisPattern, value), limit);
    }));
    for (const method of ["sub", "subn"]) this.methods.set(method, bi(method, function* (I, args, kw): G<Value> {
      const [repl, value, limit] = bind(method, args, kw, ["repl", "string", "count"], 2, [0n]);
      return yield* run(I, thisPattern, method, subject(thisPattern, value), limit, repl);
    }));
  }
}
function* run(I: Interp, pattern: Pattern, method: string, s: Subject, limit: Value = 0n, repl: Value = NONE): G<Value> {
  const iterator = matches(I, pattern, s, method);
  if (method === "finditer") return new MatchIterator(pattern, s, iterator);
  if (method === "match" || method === "search" || method === "fullmatch") { const first = iterator.next(); return first.done ? NONE : wrapMatch(pattern, s, first.value); }
  const out: Value[] = []; let last = 0, done = 0n;
  const max = integer(limit), substitution = method === "sub" || method === "subn";
  let parts: Template | null = null;
  const callable = repl instanceof Builtin || repl instanceof BuiltinType || repl instanceof BoundMethod || repl instanceof DiasporeFunction || repl instanceof DiasporeClass || repl instanceof DiasporeInstance && repl.cls.lookup("__call__") !== undefined;
  if (substitution && !callable) {
    const r = text(repl); if (r.bytes !== s.bytes) throw diasporeErr("TypeError", "cannot mix string and bytes replacements");
    parts = template(pattern, r.raw);
  }
  if (max >= 0n) for (const result of iterator) {
    if (method === "findall") {
      const empty = encoded("", s.bytes);
      out.push(pattern.code.groups === 0 ? captured(s, result, 0) : pattern.code.groups === 1 ? captured(s, result, 1, empty) : new DiasporeTuple(Array.from({ length: pattern.code.groups }, (_, i) => captured(s, result, i + 1, empty))));
      continue;
    }
    out.push(s.slice(last, result.caps[0]));
    if (substitution) {
      const value = parts === null ? yield* I.invoke(repl, [wrapMatch(pattern, s, result)], new Map()) : expand(parts, s, result);
      if (value === NONE && parts === null) out.push(encoded("", s.bytes));
      else { const r = text(value); if (r.bytes !== s.bytes) throw diasporeErr("TypeError", "replacement has wrong type"); out.push(encoded(r.raw, r.bytes)); }
    } else for (let id = 1; id <= pattern.code.groups; id++) out.push(captured(s, result, id));
    last = result.pos; done++;
    if (max > 0n && done >= max) break;
  }
  if (method === "findall") return new DiasporeList(out);
  out.push(s.slice(last));
  if (!substitution) return new DiasporeList(out);
  const raw = strJoin(out.map(value => text(value).raw)); const value = encoded(raw, s.bytes);
  return method === "subn" ? new DiasporeTuple([value, done]) : value;
}

/** Construct the capability-free native Python regular-expression module. */
export function makeReModule(_I: Interp): NativeModule {
  const cache = new Map<string, Pattern>();
  const compile = (value: Value, flags: Value): Pattern => {
    const n = integer(flags);
    if (value instanceof Pattern) { if (n !== 0n) throw diasporeErr("ValueError", "cannot process flags argument with a compiled pattern"); return value; }
    if (!isPyString(value) && !(value instanceof Uint8Array)) throw diasporeErr("TypeError", "first argument must be string or compiled pattern");
    const p = text(value);
    const key = `${p.bytes ? "b" : typeof p.raw === "string" ? "s" : "p"}:${n}:${typeof p.raw === "string" ? p.raw : JSON.stringify(chars(p.raw))}`;
    const hit = cache.get(key); if (hit) return hit;
    const result = new Pattern(value, n);
    if (cache.size >= 512) cache.delete(cache.keys().next().value!);
    cache.set(key, result); return result;
  };
  const ns = new Map<string, Value>();
  const aliases: Record<string, number> = { NOFLAG: 0, TEMPLATE: 1, T: 1, IGNORECASE: 2, I: 2, LOCALE: 4, L: 4, MULTILINE: 8, M: 8, DOTALL: 16, S: 16, UNICODE: 32, U: 32, VERBOSE: 64, X: 64, DEBUG: 128, ASCII: 256, A: 256 };
  for (const [name, value] of Object.entries(aliases)) ns.set(name, BigInt(value));
  for (const name of ["Pattern", "Match"]) {
    const type = new BuiltinType(`re.${name}`, function* (): G<Value> { throw diasporeErr("TypeError", `cannot create 're.${name}' instances`); });
    ns.set(name, type);
    _I.types.set(`re.${name}`, type);
  }
  ns.set("error", REGEX_ERROR); ns.set("PatternError", REGEX_ERROR);
  ns.set("compile", bi("compile", function* (_I, args, kw): G<Value> {
    const [pattern, flags] = bind("compile", args, kw, ["pattern", "flags"], 1, [0n]); return compile(pattern, flags);
  }));
  ns.set("purge", bi("purge", function* (_I, args, kw): G<Value> { bind("purge", args, kw, [], 0, []); cache.clear(); return NONE; }));
  ns.set("escape", bi("escape", function* (_I, args, kw): G<Value> {
    const [value] = bind("escape", args, kw, ["pattern"], 1, []); const p = text(value);
    const special = /[()[\]{}?*+\-|^$\\.&~# \t\n\r\v\f]/g;
    const escaped = typeof p.raw === "string" ? p.raw.replace(special, "\\$&")
      : strJoin((function* () { for (const char of p.raw) yield char.replace(special, "\\$&"); })());
    return encoded(escaped, p.bytes);
  }));
  for (const method of ["match", "search", "fullmatch", "findall", "finditer", "split", "sub", "subn"]) ns.set(method, bi(method, function* (I, args, kw): G<Value> {
    const sub = method === "sub" || method === "subn", split = method === "split";
    const names = sub ? ["pattern", "repl", "string", "count", "flags"] : split ? ["pattern", "string", "maxsplit", "flags"] : ["pattern", "string", "flags"];
    const a = bind(method, args, kw, names, sub ? 3 : 2, sub || split ? [0n, 0n] : [0n]);
    const pattern = compile(a[0], a[a.length - 1]);
    return yield* run(I, pattern, method, subject(pattern, a[sub ? 2 : 1]), sub ? a[3] : split ? a[2] : 0n, sub ? a[1] : NONE);
  }));
  return new NativeModule("re", ns);
}
